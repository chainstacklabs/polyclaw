"""Central configuration for PolyClaw.

All settings are loaded from environment variables (or .env file).
Use the module-level singleton:

    from lib.config import config
    if config.paper_trading:
        ...

Call reload() to re-read env vars (useful in tests).
"""

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes", "on")


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        logger.warning("Invalid float for %s, using default %.4f", key, default)
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        logger.warning("Invalid int for %s, using default %d", key, default)
        return default


@dataclass
class Config:
    # =========================================================================
    # MODE
    # =========================================================================

    paper_trading: bool
    """Simulate all trades. No blockchain calls, no CLOB orders.
    Positions are stored with paper=True so they can be tracked separately.
    Use this to validate the algorithm before risking real capital.
    Env: PAPER_TRADING (default: false)"""

    # =========================================================================
    # WALLET / BLOCKCHAIN
    # =========================================================================

    private_key: str
    """EVM private key (with or without 0x prefix).
    Env: POLYCLAW_PRIVATE_KEY (required for live trading)"""

    rpc_url: str
    """Polygon RPC endpoint. Get a free node at chainstack.com.
    Env: CHAINSTACK_NODE (required for live trading)"""

    # =========================================================================
    # LLM
    # =========================================================================

    llm_api_key: str
    """API key for the LLM provider.
    For OpenRouter: https://openrouter.ai/keys
    For local Ollama: set to 'ollama'
    Env: OPENROUTER_API_KEY (required for hedge/arb)"""

    llm_base_url: str
    """Base URL for the LLM API (OpenAI-compatible).
    Default: https://openrouter.ai/api/v1
    For local Ollama: http://localhost:11434/v1
    Env: OPENROUTER_BASE_URL"""

    llm_model: str
    """LLM model identifier.
    Good free options via OpenRouter:
      nvidia/nemotron-nano-9b-v2:free  (default, fast + accurate JSON)
      deepseek/deepseek-r1:free        (strong reasoning, slower)
    For local Ollama: qwen3:14b, llama3.3:70b
    Env: OPENROUTER_MODEL"""

    llm_timeout: float
    """LLM request timeout in seconds. Env: LLM_TIMEOUT (default: 60)"""

    llm_max_retries: int
    """LLM retry attempts on rate limit / network error.
    Env: LLM_MAX_RETRIES (default: 3)"""

    # =========================================================================
    # GAMMA API
    # =========================================================================

    gamma_timeout: float
    """Gamma API request timeout in seconds.
    Env: GAMMA_TIMEOUT (default: 30)"""

    gamma_max_retries: int
    """Gamma API retry attempts on timeout / 5xx errors.
    Env: GAMMA_MAX_RETRIES (default: 3)"""

    # =========================================================================
    # CLOB
    # =========================================================================

    clob_max_retries: int
    """CLOB order retry attempts (Cloudflare rotation).
    Env: CLOB_MAX_RETRIES (default: 5)"""

    default_slippage: float
    """Default max slippage for CLOB sells (fraction, not percent).
    0.10 = accept up to 10% below market price.
    Env: DEFAULT_SLIPPAGE (default: 0.10)"""

    # =========================================================================
    # POSITION SIZING
    # =========================================================================

    use_kelly: bool
    """Use Kelly criterion for automatic position sizing.
    When true, DEFAULT_AMOUNT is ignored and size is calculated
    from wallet balance, opportunity profit, and KELLY_FRACTION.
    Env: USE_KELLY_SIZING (default: false)"""

    kelly_fraction: float
    """Fraction of full Kelly to apply (0.0–1.0).
    1.0 = full Kelly (aggressive, max growth)
    0.5 = half Kelly  (balanced)
    0.25 = quarter Kelly (conservative, recommended for bots)
    Env: KELLY_FRACTION (default: 0.25)"""

    default_amount: float
    """Fixed USD amount per leg when USE_KELLY_SIZING=false.
    Total capital per arb = default_amount × 2.
    Env: DEFAULT_AMOUNT (default: 50)"""

    max_position: float
    """Hard cap on USD per leg, regardless of Kelly or other sizing.
    Env: MAX_POSITION (default: 500)"""

    # =========================================================================
    # DAEMON
    # =========================================================================

    scan_interval: int
    """Minutes between arb scans. Env: DAEMON_SCAN_INTERVAL (default: 15)"""

    auto_execute: bool
    """Automatically execute found arb opportunities.
    false = alert only (recommended while testing).
    true  = execute best opportunity each scan.
    Env: DAEMON_AUTO_EXECUTE (default: false)"""

    min_profit: float
    """Minimum profit fraction to consider an opportunity.
    0.05 = require at least 5% guaranteed profit.
    Lower = more opportunities but higher noise.
    Env: DAEMON_MIN_PROFIT (default: 0.05)"""

    daily_budget: float
    """Maximum USD to spend across all trades in a calendar day.
    Resets at midnight UTC. Env: DAEMON_DAILY_BUDGET (default: 500)"""

    scan_limit: int
    """Number of markets fetched per scan.
    More markets → more opportunities found, but slower and more LLM calls.
    Env: DAEMON_SCAN_LIMIT (default: 30)"""

    market_query: str
    """Optional keyword to filter markets (e.g. 'election', 'bitcoin').
    Empty = scan trending markets by volume.
    Env: DAEMON_QUERY (default: empty)"""

    # =========================================================================
    # NOTIFICATIONS
    # =========================================================================

    ntfy_url: str
    """ntfy.sh push notification URL.
    Example: https://ntfy.sh/my-secret-topic
    Install ntfy app on phone, subscribe to the same topic.
    Env: DAEMON_NTFY_URL"""

    telegram_token: str
    """Telegram bot token (from @BotFather).
    Env: DAEMON_TELEGRAM_TOKEN"""

    telegram_chat_id: str
    """Telegram chat or user ID to send notifications to.
    Get it by messaging @userinfobot.
    Env: DAEMON_TELEGRAM_CHAT_ID"""

    webhook_url: str
    """Generic JSON webhook URL (Slack, Discord, custom endpoint).
    Payload: {"title": "...", "message": "...", "text": "..."}
    Env: DAEMON_WEBHOOK_URL"""

    # =========================================================================
    # DERIVED PROPERTIES
    # =========================================================================

    @property
    def notifications_configured(self) -> bool:
        return bool(
            self.ntfy_url
            or (self.telegram_token and self.telegram_chat_id)
            or self.webhook_url
        )

    @property
    def trading_enabled(self) -> bool:
        """True if wallet is configured for live trading."""
        return bool(self.private_key and self.rpc_url)

    def validate(self) -> list[str]:
        """Return list of warnings about missing/invalid settings."""
        warnings = []

        if self.paper_trading:
            warnings.append("PAPER_TRADING=true — no real trades will be executed")

        if not self.paper_trading and not self.private_key:
            warnings.append("POLYCLAW_PRIVATE_KEY not set — trading disabled")

        if not self.paper_trading and not self.rpc_url:
            warnings.append("CHAINSTACK_NODE not set — trading disabled")

        if not self.llm_api_key:
            warnings.append("OPENROUTER_API_KEY not set — hedge/arb scanning disabled")

        if not self.notifications_configured:
            warnings.append("No notification backend configured (DAEMON_NTFY_URL etc.)")

        if self.auto_execute and self.paper_trading:
            warnings.append("AUTO_EXECUTE=true with PAPER_TRADING=true — safe, simulating")

        if self.auto_execute and not self.paper_trading and not self.trading_enabled:
            warnings.append("AUTO_EXECUTE=true but wallet not configured — execution will fail")

        if self.kelly_fraction <= 0 or self.kelly_fraction > 1:
            warnings.append(f"KELLY_FRACTION={self.kelly_fraction} is outside (0, 1]")

        if self.min_profit < 0.01:
            warnings.append(f"DAEMON_MIN_PROFIT={self.min_profit} is very low — expect false positives")

        return warnings

    def summary(self) -> str:
        mode = "[PAPER] " if self.paper_trading else ""
        sizing = (
            f"Kelly x{self.kelly_fraction} (max ${self.max_position})"
            if self.use_kelly
            else f"${self.default_amount}/leg (max ${self.max_position})"
        )
        notif = []
        if self.ntfy_url:          notif.append("ntfy")
        if self.telegram_token:    notif.append("Telegram")
        if self.webhook_url:       notif.append("webhook")

        lines = [
            f"  Mode          : {mode}{'AUTO-EXECUTE' if self.auto_execute else 'ALERT ONLY'}",
            f"  Scan interval : every {self.scan_interval} min",
            f"  Min profit    : {self.min_profit*100:.0f}%",
            f"  Position size : {sizing}",
            f"  Daily budget  : ${self.daily_budget:.0f}",
            f"  Markets/scan  : {self.scan_limit}",
            f"  Market filter : '{self.market_query}' (all if empty)",
            f"  LLM model     : {self.llm_model}",
            f"  Notifications : {', '.join(notif) or 'none'}",
        ]
        return "\n".join(lines)


def load() -> Config:
    """Load config from current environment variables."""
    return Config(
        # Mode
        paper_trading    = _bool("PAPER_TRADING"),

        # Wallet
        private_key      = os.getenv("POLYCLAW_PRIVATE_KEY", ""),
        rpc_url          = os.getenv("CHAINSTACK_NODE", ""),

        # LLM
        llm_api_key      = os.getenv("OPENROUTER_API_KEY", ""),
        llm_base_url     = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        llm_model        = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-nano-9b-v2:free"),
        llm_timeout      = _float("LLM_TIMEOUT", 60.0),
        llm_max_retries  = _int("LLM_MAX_RETRIES", 3),

        # Gamma
        gamma_timeout    = _float("GAMMA_TIMEOUT", 30.0),
        gamma_max_retries = _int("GAMMA_MAX_RETRIES", 3),

        # CLOB
        clob_max_retries = _int("CLOB_MAX_RETRIES", 5),
        default_slippage = _float("DEFAULT_SLIPPAGE", 0.10),

        # Position sizing
        use_kelly        = _bool("USE_KELLY_SIZING"),
        kelly_fraction   = _float("KELLY_FRACTION", 0.25),
        default_amount   = _float("DEFAULT_AMOUNT", 50.0),
        max_position     = _float("MAX_POSITION", 500.0),

        # Daemon
        scan_interval    = _int("DAEMON_SCAN_INTERVAL", 15),
        auto_execute     = _bool("DAEMON_AUTO_EXECUTE"),
        min_profit       = _float("DAEMON_MIN_PROFIT", 0.05),
        daily_budget     = _float("DAEMON_DAILY_BUDGET", 500.0),
        scan_limit       = _int("DAEMON_SCAN_LIMIT", 30),
        market_query     = os.getenv("DAEMON_QUERY", ""),

        # Notifications
        ntfy_url         = os.getenv("DAEMON_NTFY_URL", ""),
        telegram_token   = os.getenv("DAEMON_TELEGRAM_TOKEN", ""),
        telegram_chat_id = os.getenv("DAEMON_TELEGRAM_CHAT_ID", ""),
        webhook_url      = os.getenv("DAEMON_WEBHOOK_URL", ""),
    )


def reload() -> "Config":
    """Re-read env vars and replace the module singleton. Useful in tests."""
    global config
    config = load()
    return config


# Module-level singleton — loaded once on first import
config = load()
