#!/usr/bin/env python3
"""PolyClaw daemon - continuous arbitrage monitoring and execution.

Runs an infinite scan loop: finds implication arbitrage opportunities,
optionally executes them automatically, and monitors existing positions.

Designed for headless Linux / LXC on Proxmox with systemd.

Configuration (via .env or environment variables):
    DAEMON_SCAN_INTERVAL=15      Minutes between scans (default: 15)
    DAEMON_AUTO_EXECUTE=false    Auto-execute opportunities (default: false = alert only)
    DAEMON_AMOUNT=50             USD per arb leg when auto-executing (default: 50)
    DAEMON_MIN_PROFIT=0.05       Minimum profit fraction to execute (default: 5%)
    DAEMON_DAILY_BUDGET=500      Max USD to spend per day (default: 500)
    DAEMON_SCAN_LIMIT=30         Markets per scan (default: 30)
    DAEMON_QUERY=                Optional keyword filter for markets

Notifications (pick one or both):
    DAEMON_NTFY_URL=https://ntfy.sh/my-topic     ntfy.sh push notifications
    DAEMON_TELEGRAM_TOKEN=bot:xxx                Telegram bot token
    DAEMON_TELEGRAM_CHAT_ID=123456789            Telegram chat/user ID
    DAEMON_WEBHOOK_URL=https://...               Generic JSON webhook

Usage:
    daemon run                   Run in foreground (recommended with systemd)
    daemon start                 Start as background process (PID-based)
    daemon stop                  Stop the running daemon
    daemon status                Show status, config, and stats
    daemon logs [-n N]           Tail the daemon log file
    daemon install               Install and enable systemd service
    daemon uninstall             Disable and remove systemd service
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, date, timezone
from pathlib import Path

# Add parent and scripts dirs to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import GammaClient
from lib.llm_client import LLMClient, DEFAULT_MODEL
from lib.wallet_manager import WalletManager
from lib.position_storage import PositionStorage
from lib.config import config as _cfg

from hedge import extract_implications_for_market, build_portfolios_from_covers
from arb import filter_arb_opportunities, save_scan_state


# =============================================================================
# PATHS
# =============================================================================

DATA_DIR = Path.home() / ".openclaw" / "polyclaw"
PID_FILE = DATA_DIR / "daemon.pid"
LOG_FILE = DATA_DIR / "daemon.log"
STATS_FILE = DATA_DIR / "daemon_stats.json"

SYSTEMD_SERVICE_NAME = "polyclaw"
SYSTEMD_UNIT_PATH = Path(f"/etc/systemd/system/{SYSTEMD_SERVICE_NAME}.service")
# Fallback for non-root: user systemd
SYSTEMD_USER_UNIT_PATH = (
    Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_SERVICE_NAME}.service"
)


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging(foreground: bool = False) -> logging.Logger:
    """Set up rotating file log + optional console output."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("polyclaw.daemon")
    logger.setLevel(logging.DEBUG)

    # Rotating file handler (10 MB, keep 5 files)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(file_handler)

    # Console output when running in foreground (systemd captures stdout → journal)
    if foreground:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(console)

    return logger


# =============================================================================
# CONFIG
# =============================================================================

def _kelly_amount(opp: dict, usdc_balance: float) -> float:
    """
    Calculate position size per leg using fractional Kelly criterion.

    For a near-arb position with coverage c and profit margin p:
        b      = net odds  = p / (1 - p)  [profit per dollar risked]
        f*     = full Kelly = coverage - (1 - coverage) / b
        f_used = f* × KELLY_FRACTION      [apply fractional Kelly]
        amount = usdc_balance × f_used

    Then capped at MAX_POSITION.
    """
    coverage = opp.get("coverage", 0.98)
    profit   = opp.get("profit", 0.0)
    cost     = opp.get("total_cost", 1.0)

    if profit <= 0 or cost <= 0:
        return _cfg.default_amount

    b = profit / cost          # net odds: profit per dollar of cost
    p = coverage               # probability of at least $1 payout
    q = 1.0 - p

    f_star = p - (q / b) if b > 0 else 0.0
    f_star = max(0.0, f_star)

    f_used = f_star * _cfg.kelly_fraction
    amount = usdc_balance * f_used
    amount = min(amount, _cfg.max_position)
    amount = max(amount, 1.0)   # minimum $1
    return round(amount, 2)


# Thin wrapper so the rest of the file uses a consistent interface
class DaemonConfig:
    """Facade over the central lib.config singleton for daemon-specific access."""

    @property
    def scan_interval(self):  return _cfg.scan_interval
    @property
    def auto_execute(self):   return _cfg.auto_execute
    @property
    def min_profit(self):     return _cfg.min_profit
    @property
    def daily_budget(self):   return _cfg.daily_budget
    @property
    def scan_limit(self):     return _cfg.scan_limit
    @property
    def query(self):          return _cfg.market_query
    @property
    def model(self):          return _cfg.llm_model
    @property
    def ntfy_url(self):       return _cfg.ntfy_url
    @property
    def telegram_token(self): return _cfg.telegram_token
    @property
    def telegram_chat(self):  return _cfg.telegram_chat_id
    @property
    def webhook_url(self):    return _cfg.webhook_url
    @property
    def paper_trading(self):  return _cfg.paper_trading
    @property
    def notifications_configured(self): return _cfg.notifications_configured

    def summary(self) -> str:
        return _cfg.summary()


# =============================================================================
# NOTIFICATIONS (headless / Linux)
# =============================================================================

import httpx as _httpx


def notify(title: str, message: str, config: "DaemonConfig") -> None:
    """Send notification via configured backends (best-effort, never raises)."""
    if not config.notifications_configured:
        return

    full = f"{title}: {message}"

    # ── ntfy.sh ──────────────────────────────────────────────────────────────
    if config.ntfy_url:
        try:
            _httpx.post(
                config.ntfy_url,
                content=full.encode(),
                headers={"Title": title, "Priority": "default"},
                timeout=5,
            )
        except Exception:
            pass

    # ── Telegram ─────────────────────────────────────────────────────────────
    if config.telegram_token and config.telegram_chat:
        try:
            _httpx.post(
                f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
                json={"chat_id": config.telegram_chat, "text": full, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass

    # ── Generic webhook ───────────────────────────────────────────────────────
    if config.webhook_url:
        try:
            _httpx.post(
                config.webhook_url,
                json={"title": title, "message": message, "text": full},
                timeout=5,
            )
        except Exception:
            pass


# =============================================================================
# STATS
# =============================================================================

class DaemonStats:
    """Persistent stats file for daemon activity."""

    def __init__(self):
        self.path = STATS_FILE
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return {
            "started_at": None,
            "scans_total": 0,
            "opportunities_found": 0,
            "trades_executed": 0,
            "daily_spent": 0.0,
            "daily_reset": str(date.today()),
            "last_scan": None,
            "last_opportunity": None,
        }

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))

    def _reset_daily_if_needed(self) -> None:
        today = str(date.today())
        if self._data.get("daily_reset") != today:
            self._data["daily_spent"] = 0.0
            self._data["daily_reset"] = today
            self._save()

    @property
    def daily_spent(self) -> float:
        self._reset_daily_if_needed()
        return self._data["daily_spent"]

    def record_start(self) -> None:
        self._data["started_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def record_scan(self, n_opportunities: int) -> None:
        self._reset_daily_if_needed()
        self._data["scans_total"] += 1
        self._data["opportunities_found"] += n_opportunities
        self._data["last_scan"] = datetime.now(timezone.utc).isoformat()
        if n_opportunities:
            self._data["last_opportunity"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def record_trade(self, amount_per_leg: float) -> None:
        self._reset_daily_if_needed()
        self._data["trades_executed"] += 1
        self._data["daily_spent"] += amount_per_leg * 2
        self._save()

    def display(self) -> str:
        self._reset_daily_if_needed()
        d = self._data
        lines = [
            f"  Started       : {d.get('started_at', 'N/A')}",
            f"  Scans total   : {d['scans_total']}",
            f"  Opportunities : {d['opportunities_found']} found total",
            f"  Trades        : {d['trades_executed']} executed",
            f"  Spent today   : ${d['daily_spent']:.2f}",
            f"  Last scan     : {d.get('last_scan', 'never')}",
            f"  Last arb found: {d.get('last_opportunity', 'never')}",
        ]
        return "\n".join(lines)


# =============================================================================
# DAEMON LOOP (platform-agnostic)
# =============================================================================

class PolyclawDaemon:
    """Main daemon: scan loop, auto-execute, position monitoring."""

    def __init__(self, config: DaemonConfig, logger: logging.Logger):
        self.config = config
        self.log = logger
        self.stats = DaemonStats()
        self._running = False

    def _get_executor(self):
        from trade import TradeExecutor
        return TradeExecutor

    async def scan_once(self) -> list[dict]:
        """Run one full arb scan. Returns found opportunities."""
        gamma = GammaClient()
        try:
            if self.config.query:
                markets = await gamma.search_markets(self.config.query, limit=self.config.scan_limit)
            else:
                markets = await gamma.get_trending_markets(limit=self.config.scan_limit)
        except Exception as e:
            self.log.error("Failed to fetch markets: %s", e)
            return []

        if len(markets) < 2:
            self.log.warning("Too few markets fetched (%d), skipping", len(markets))
            return []

        self.log.info("Scanning %d markets for implications...", len(markets))

        try:
            llm = LLMClient(model=self.config.model)
        except ValueError as e:
            self.log.error("LLM client error: %s", e)
            return []

        all_portfolios = []
        try:
            for target in markets:
                covers = await extract_implications_for_market(target, markets, llm)
                if covers:
                    portfolios = build_portfolios_from_covers(target, covers)
                    all_portfolios.extend(portfolios)
        except Exception as e:
            self.log.error("Error during implication extraction: %s", e)
        finally:
            await llm.close()

        opportunities = filter_arb_opportunities(all_portfolios, min_profit=self.config.min_profit)
        save_scan_state(opportunities)

        self.log.info(
            "Scan complete: %d portfolios, %d arb opportunities (>=%.0f%% profit)",
            len(all_portfolios), len(opportunities), self.config.min_profit * 100
        )
        return opportunities

    async def maybe_execute(self, opp: dict) -> bool:
        """Execute a single arb opportunity if budget allows."""
        wallet = WalletManager()

        # Determine position size
        if _cfg.use_kelly:
            try:
                balances = wallet.get_balances()
                amount = _kelly_amount(opp, balances.usdc_e)
                self.log.info(
                    "Kelly sizing: balance=$%.2f profit=%.1f%% coverage=%.1f%% -> $%.2f/leg",
                    balances.usdc_e, opp["profit_pct"], opp["coverage"] * 100, amount
                )
            except Exception as e:
                self.log.warning("Kelly sizing failed (%s), using default $%.0f", e, _cfg.default_amount)
                amount = _cfg.default_amount
        else:
            amount = _cfg.default_amount

        remaining = self.config.daily_budget - self.stats.daily_spent
        cost = amount * 2

        if cost > remaining:
            self.log.warning(
                "Daily budget exhausted (spent $%.2f / $%.2f), skipping",
                self.stats.daily_spent, self.config.daily_budget
            )
            return False

        if not wallet.is_unlocked and not _cfg.paper_trading:
            self.log.error("Wallet not configured, cannot execute")
            return False

        TradeExecutor = self._get_executor()
        executor = TradeExecutor(wallet)

        paper_tag = " [PAPER]" if _cfg.paper_trading else ""
        self.log.info(
            "Executing arb%s: %s %s / %s %s | profit=%.1f%% | $%.2f/leg",
            paper_tag,
            opp["target_position"], opp["target_question"][:40],
            opp["cover_position"], opp["cover_question"][:40],
            opp["profit_pct"], amount,
        )

        try:
            result1 = await executor.buy_position(
                opp["target_id"], opp["target_position"], amount
            )
            if not result1.success:
                self.log.error("Leg 1 failed: %s", result1.error)
                return False

            result2 = await executor.buy_position(
                opp["cover_id"], opp["cover_position"], amount
            )
            if not result2.success:
                self.log.error(
                    "Leg 2 FAILED: %s — LEG 1 IS UNHEDGED! Check positions.", result2.error
                )
                notify(
                    "PolyClaw CRITICAL",
                    f"Leg 2 failed! Unhedged position on {opp['target_question'][:50]}",
                    self.config,
                )
                return False

            self.stats.record_trade(amount)
            min_profit = amount * opp["profit"]
            self.log.info(
                "Arb executed! Min profit: +$%.2f (%.1f%%) | TX1: %s | TX2: %s",
                min_profit, opp["profit_pct"], result1.split_tx, result2.split_tx
            )
            notify(
                "PolyClaw: arb executed",
                f"+${min_profit:.2f} ({opp['profit_pct']:.1f}%) | {opp['target_question'][:50]}",
                self.config,
            )
            return True

        except Exception as e:
            self.log.error("Unexpected error during execution: %s", e, exc_info=True)
            return False

    async def check_positions(self) -> None:
        """Detect resolved markets and update position statuses."""
        storage = PositionStorage()
        open_positions = storage.get_open()
        if not open_positions:
            return

        gamma = GammaClient()
        resolved = 0
        for pos in open_positions:
            try:
                market = await gamma.get_market(pos["market_id"])
                if market.resolved:
                    outcome = market.outcome or "unknown"
                    won = (
                        (pos["position"] == "YES" and outcome.lower() in ("yes", "1", "true")) or
                        (pos["position"] == "NO"  and outcome.lower() in ("no",  "0", "false"))
                    )
                    self.log.info(
                        "Position resolved: %s %s -> outcome=%s (%s)",
                        pos["position"], pos["question"][:45], outcome,
                        "WON" if won else "LOST",
                    )
                    storage.update_status(pos["position_id"], "resolved")
                    notify(
                        f"PolyClaw: {'WON' if won else 'LOST'}",
                        f"{pos['position']} on {pos['question'][:60]}",
                        self.config,
                    )
                    resolved += 1
            except Exception as e:
                self.log.debug("Could not check position %s: %s", pos["position_id"][:8], e)

        if resolved:
            self.log.info("Marked %d position(s) as resolved", resolved)

    async def run(self) -> None:
        """Main infinite loop."""
        self._running = True
        self.stats.record_start()

        self.log.info("=" * 60)
        paper_tag = " [PAPER TRADING]" if _cfg.paper_trading else ""
        self.log.info("PolyClaw daemon started (PID %d)%s", os.getpid(), paper_tag)
        self.log.info("Mode: %s | interval: %dmin | budget: $%.0f/day",
                      "AUTO-EXECUTE" if self.config.auto_execute else "ALERT ONLY",
                      self.config.scan_interval, self.config.daily_budget)
        if _cfg.paper_trading:
            self.log.info("PAPER TRADING: no real blockchain transactions will be made")
        self.log.info("=" * 60)

        notify(
            "PolyClaw started",
            f"{'Auto-execute' if self.config.auto_execute else 'Alert only'} | "
            f"scan every {self.config.scan_interval}min",
            self.config,
        )

        while self._running:
            scan_start = time.monotonic()
            try:
                await self.check_positions()
                opportunities = await self.scan_once()
                self.stats.record_scan(len(opportunities))

                if opportunities:
                    best = opportunities[0]
                    self.log.info(
                        "Best opportunity: %.1f%% | %s %s / %s %s",
                        best["profit_pct"],
                        best["target_position"], best["target_question"][:35],
                        best["cover_position"], best["cover_question"][:35],
                    )
                    if self.config.auto_execute:
                        await self.maybe_execute(best)
                    else:
                        notify(
                            f"PolyClaw: {len(opportunities)} arb found",
                            f"Best: {best['profit_pct']:.1f}% | {best['target_question'][:50]}",
                            self.config,
                        )
                else:
                    self.log.info("No opportunities this scan")

            except Exception as e:
                self.log.error("Unhandled error in scan loop: %s", e, exc_info=True)
                notify("PolyClaw: error", str(e)[:120], self.config)

            # Sleep until next scan, waking every second to check _running
            elapsed = time.monotonic() - scan_start
            wait = max(0, self.config.scan_interval * 60 - elapsed)
            self.log.debug("Next scan in %.0fs", wait)
            deadline = time.monotonic() + wait
            while self._running and time.monotonic() < deadline:
                await asyncio.sleep(1)

        self.log.info("Daemon stopped.")

    def stop(self) -> None:
        self._running = False


# =============================================================================
# PROCESS MANAGEMENT
# =============================================================================

def write_pid() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _use_systemd() -> bool:
    """True if the systemd service is installed (system or user)."""
    return SYSTEMD_UNIT_PATH.exists() or SYSTEMD_USER_UNIT_PATH.exists()


def _systemctl(*args) -> subprocess.CompletedProcess:
    """Run systemctl, trying system then user scope."""
    if SYSTEMD_UNIT_PATH.exists():
        return subprocess.run(["systemctl", *args, SYSTEMD_SERVICE_NAME],
                              capture_output=True, text=True)
    return subprocess.run(["systemctl", "--user", *args, SYSTEMD_SERVICE_NAME],
                          capture_output=True, text=True)


def cmd_start(args) -> int:
    if _use_systemd():
        r = _systemctl("start")
        if r.returncode == 0:
            print(f"Started via systemd. Logs: journalctl -u {SYSTEMD_SERVICE_NAME} -f")
        else:
            print(f"systemctl start failed:\n{r.stderr}")
        return r.returncode

    # Fallback: background subprocess
    pid = read_pid()
    if pid and is_running(pid):
        print(f"Daemon already running (PID {pid})")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as logfile:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run"],
            stdout=logfile, stderr=logfile,
            start_new_session=True,
        )

    time.sleep(1.5)
    if is_running(proc.pid):
        PID_FILE.write_text(str(proc.pid))
        print(f"Daemon started (PID {proc.pid})")
        print(f"Logs: {LOG_FILE}")
        return 0

    print(f"Daemon failed to start. Check: {LOG_FILE}")
    return 1


def cmd_stop(args) -> int:
    if _use_systemd():
        r = _systemctl("stop")
        print("Stopped." if r.returncode == 0 else f"Failed:\n{r.stderr}")
        return r.returncode

    pid = read_pid()
    if not pid:
        print("Daemon is not running (no PID file)")
        return 1
    if not is_running(pid):
        print(f"Stale PID {pid}, cleaning up")
        PID_FILE.unlink(missing_ok=True)
        return 1

    os.kill(pid, signal.SIGTERM)
    for _ in range(10):
        time.sleep(0.5)
        if not is_running(pid):
            PID_FILE.unlink(missing_ok=True)
            print(f"Daemon stopped (PID {pid})")
            return 0

    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print("Force-killed daemon")
    return 0


def cmd_status(args) -> int:
    if _use_systemd():
        r = _systemctl("status")
        print(r.stdout or r.stderr)
    else:
        pid = read_pid()
        running = pid and is_running(pid)
        print(f"Daemon: {'RUNNING' if running else 'STOPPED'}" +
              (f" (PID {pid})" if running else ""))

    config = DaemonConfig()
    print("\nConfiguration:")
    print(config.summary())

    stats = DaemonStats()
    print("\nStatistics:")
    print(stats.display())

    print(f"\nLog file: {LOG_FILE}")
    return 0


def cmd_logs(args) -> int:
    lines = int(getattr(args, "lines", 50))

    if _use_systemd():
        scope = [] if SYSTEMD_UNIT_PATH.exists() else ["--user"]
        try:
            subprocess.run(
                ["journalctl", *scope, "-u", SYSTEMD_SERVICE_NAME,
                 "-n", str(lines), "--no-pager"]
            )
        except KeyboardInterrupt:
            pass
        return 0

    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return 1
    try:
        subprocess.run(["tail", f"-{lines}", str(LOG_FILE)])
    except KeyboardInterrupt:
        pass
    return 0


def cmd_run(args) -> int:
    """Run in foreground — this is what systemd ExecStart calls."""
    logger = setup_logging(foreground=True)
    config = DaemonConfig()
    daemon = PolyclawDaemon(config, logger)

    write_pid()

    def _on_signal(signum, frame):
        logger.info("Signal %d received, shutting down...", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        asyncio.run(daemon.run())
    finally:
        PID_FILE.unlink(missing_ok=True)

    return 0


# =============================================================================
# SYSTEMD INSTALL / UNINSTALL
# =============================================================================

def cmd_install(args) -> int:
    """Generate and install a systemd service unit."""
    python  = sys.executable
    script  = str(Path(__file__).resolve())
    env_file = str(Path(__file__).parent.parent / ".env")

    is_root = os.getuid() == 0
    unit_path = SYSTEMD_UNIT_PATH if is_root else SYSTEMD_USER_UNIT_PATH

    unit = f"""[Unit]
Description=PolyClaw 24/7 Arbitrage Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} {script} run
Restart=always
RestartSec=30
EnvironmentFile={env_file}

# Resource limits
MemoryMax=512M
CPUQuota=50%

# Logging goes to journald automatically
StandardOutput=journal
StandardError=journal
SyslogIdentifier=polyclaw

[Install]
WantedBy={'multi-user.target' if is_root else 'default.target'}
"""

    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit)
    print(f"Wrote unit file: {unit_path}")

    scope = [] if is_root else ["--user"]
    cmds = [
        (["systemctl", *scope, "daemon-reload"], "Reloading systemd..."),
        (["systemctl", *scope, "enable", SYSTEMD_SERVICE_NAME], "Enabling service..."),
        (["systemctl", *scope, "start",  SYSTEMD_SERVICE_NAME], "Starting service..."),
    ]
    for cmd, msg in cmds:
        print(msg)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"Error: {r.stderr.strip()}")
            return 1

    scope_flag = "" if is_root else " --user"
    print(f"\nService installed and started.")
    print(f"  Status : systemctl{scope_flag} status {SYSTEMD_SERVICE_NAME}")
    print(f"  Logs   : journalctl{scope_flag} -u {SYSTEMD_SERVICE_NAME} -f")
    print(f"  Stop   : polyclaw daemon stop")
    return 0


def cmd_uninstall(args) -> int:
    """Disable and remove the systemd service unit."""
    is_root = os.getuid() == 0
    unit_path = SYSTEMD_UNIT_PATH if is_root else SYSTEMD_USER_UNIT_PATH

    if not unit_path.exists():
        print("Service unit not found. Nothing to remove.")
        return 1

    scope = [] if is_root else ["--user"]
    subprocess.run(["systemctl", *scope, "stop",    SYSTEMD_SERVICE_NAME], capture_output=True)
    subprocess.run(["systemctl", *scope, "disable", SYSTEMD_SERVICE_NAME], capture_output=True)
    unit_path.unlink()
    subprocess.run(["systemctl", *scope, "daemon-reload"], capture_output=True)

    print(f"Service '{SYSTEMD_SERVICE_NAME}' disabled and removed.")
    return 0


# =============================================================================
# MAIN
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="PolyClaw 24/7 arbitrage daemon")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run",       help="Run in foreground (used by systemd)")
    subparsers.add_parser("start",     help="Start daemon in background")
    subparsers.add_parser("stop",      help="Stop the daemon")
    subparsers.add_parser("status",    help="Show status, config, and stats")
    subparsers.add_parser("install",   help="Install systemd service and enable")
    subparsers.add_parser("uninstall", help="Disable and remove systemd service")

    logs_p = subparsers.add_parser("logs", help="Show daemon logs")
    logs_p.add_argument("--lines", "-n", type=int, default=50,
                        help="Lines to show (default: 50)")

    args = parser.parse_args()

    dispatch = {
        "run":       cmd_run,
        "start":     cmd_start,
        "stop":      cmd_stop,
        "status":    cmd_status,
        "logs":      cmd_logs,
        "install":   cmd_install,
        "uninstall": cmd_uninstall,
    }

    if args.command in dispatch:
        return dispatch[args.command](args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
