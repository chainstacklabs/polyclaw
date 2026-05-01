#!/usr/bin/env python3
"""PolyClaw CLI - Polymarket trading skill for OpenClaw.

Usage:
    polyclaw markets trending
    polyclaw markets search "election"
    polyclaw market <id>
    polyclaw wallet status
    polyclaw wallet approve
    polyclaw buy <market_id> YES 50
    polyclaw sell <position_id>
    polyclaw positions
    polyclaw hedge scan
    polyclaw hedge scan --query "election"
    polyclaw hedge analyze <id1> <id2>
    polyclaw arb scan
    polyclaw arb execute 1 --amount 50
"""

import sys
import subprocess
from pathlib import Path

# Load .env file from skill root directory (for OpenClaw env var injection)
from dotenv import load_dotenv
SKILL_DIR = Path(__file__).parent.parent
load_dotenv(SKILL_DIR / ".env")

# Script directory
SCRIPT_DIR = Path(__file__).parent


def run_script(script_name: str, args: list[str]) -> int:
    """Run a script with arguments."""
    script_path = SCRIPT_DIR / f"{script_name}.py"
    if not script_path.exists():
        print(f"Error: Script not found: {script_path}")
        return 1

    cmd = [sys.executable, str(script_path)] + args
    result = subprocess.run(cmd)
    return result.returncode


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    command = sys.argv[1]
    args = sys.argv[2:]

    # Route commands to appropriate scripts
    if command == "markets":
        return run_script("markets", args)

    elif command == "market":
        # Shortcut: polyclaw market <id> -> polyclaw markets details <id>
        if not args:
            print("Usage: polyclaw market <market_id>")
            return 1
        return run_script("markets", ["details"] + args)

    elif command == "wallet":
        return run_script("wallet", args)

    elif command == "buy":
        # Shortcut: polyclaw buy <id> YES 50 -> trade buy <id> YES 50
        return run_script("trade", ["buy"] + args)

    elif command == "sell":
        # Shortcut: polyclaw sell <position_id> -> trade sell <position_id>
        return run_script("trade", ["sell"] + args)

    elif command == "positions":
        return run_script("positions", args)

    elif command == "position":
        # Shortcut: polyclaw position <id> -> positions show <id>
        if args:
            return run_script("positions", ["show"] + args)
        else:
            return run_script("positions", ["list"])

    elif command == "hedge":
        return run_script("hedge", args)

    elif command == "arb":
        return run_script("arb", args)

    elif command == "daemon":
        return run_script("daemon", args)

    elif command == "help" or command == "--help" or command == "-h":
        print(__doc__)
        print("Commands:")
        print("  markets trending                Show trending markets by volume")
        print("  markets search <query>          Search markets by keyword")
        print("  markets events                  Show events with multiple markets")
        print("  market <id>                     Show market details")
        print("")
        print("  wallet status                   Show wallet status and balances")
        print("  wallet approve                  Set Polymarket contract approvals (one-time)")
        print("")
        print("  buy <market_id> YES <amt>       Buy YES position for $amt")
        print("  buy <market_id> NO <amt>        Buy NO position for $amt")
        print("    --skip-sell                   Keep both YES and NO tokens")
        print("    --max-slippage 0.05           Max slippage for CLOB sell (default: 0.10)")
        print("")
        print("  sell <position_id>              Sell (close) an open position via CLOB")
        print("    --max-slippage 0.05           Max slippage (default: 0.10)")
        print("    --yes                         Skip confirmation prompt")
        print("")
        print("  positions                       List open positions with P&L")
        print("  positions --all                 List all positions")
        print("  positions export                Export positions to CSV (stdout)")
        print("  positions export -o file.csv    Export positions to file")
        print("  position <id>                   Show position details")
        print("")
        print("  hedge scan                      Scan trending markets for hedges")
        print("  hedge scan --query <q>          Scan markets matching query")
        print("  hedge analyze <id1> <id2>       Analyze pair for hedging relationship")
        print("")
        print("  arb scan                        Scan for implication arbitrage opportunities")
        print("  arb scan --query <q>            Scan markets matching query")
        print("  arb scan --limit 50             Scan more markets (default: 30)")
        print("  arb scan --min-profit 0.05      Minimum profit threshold (default: 3%)")
        print("  arb execute <N> --amount <USD>  Execute opportunity #N ($USD per leg)")
        print("  arb execute <N> --amount 50 --yes  Skip confirmation")
        print("")
        print("  daemon start                    Start 24/7 background daemon")
        print("  daemon stop                     Stop the daemon")
        print("  daemon status                   Show status, config, and stats")
        print("  daemon logs [-n 100]            Tail daemon log")
        print("  daemon run                      Run in foreground (debug mode)")
        print("  daemon install                  Install macOS LaunchAgent (auto-start on login)")
        print("  daemon uninstall                Remove macOS LaunchAgent")
        print("")
        print("Daemon env vars (in .env):")
        print("  DAEMON_SCAN_INTERVAL=15         Minutes between scans")
        print("  DAEMON_AUTO_EXECUTE=false        Auto-execute found opportunities")
        print("  DAEMON_AMOUNT=50                USD per arb leg")
        print("  DAEMON_MIN_PROFIT=0.05          Min profit to execute (5%)")
        print("  DAEMON_DAILY_BUDGET=500         Max USD per day")
        print("  DAEMON_NOTIFY=true              macOS notifications")
        print("")
        print("Environment Variables:")
        print("  CHAINSTACK_NODE                 Polygon RPC URL (required for trading)")
        print("  POLYCLAW_PRIVATE_KEY            EVM private key (required for trading)")
        print("  OPENROUTER_API_KEY              OpenRouter API key (required for hedge)")
        print("  OPENROUTER_BASE_URL             LLM API base URL (default: OpenRouter)")
        print("                                  Set to http://localhost:11434/v1 for Ollama")
        print("  OPENROUTER_MODEL                LLM model override")
        print("")
        print("Examples:")
        print("  polyclaw markets trending")
        print("  polyclaw markets search 'trump'")
        print("  polyclaw market will-trump-win-2028")
        print("  polyclaw wallet status")
        print("  polyclaw buy abc123 YES 50")
        print("  polyclaw buy abc123 YES 50 --max-slippage 0.05")
        print("  polyclaw sell abc123")
        print("  polyclaw positions")
        print("  polyclaw positions export -o positions.csv")
        print("  polyclaw hedge scan")
        print("  polyclaw hedge scan --query 'election'")
        return 0

    elif command == "version" or command == "--version" or command == "-v":
        print("PolyClaw v0.1.0")
        return 0

    else:
        print(f"Unknown command: {command}")
        print("Run 'polyclaw help' for usage")
        return 1


if __name__ == "__main__":
    sys.exit(main())
