#!/usr/bin/env python3
"""Implication arbitrage scanner and executor.

Finds market pairs where a logical implication (A→B) is mispriced:
P(A) > P(B) means the portfolio [YES(B) + NO(A)] costs less than $1
but guarantees at least $1 payout — risk-free profit.

Math:
    Portfolio cost = P(B) + (1 - P(A)) = 1 - (P(A) - P(B))
    Minimum payout = $1 in all scenarios
    Guaranteed profit per dollar of tokens = P(A) - P(B)

Usage:
    arb scan                        # Scan trending markets
    arb scan --query "election"     # Scan matching query
    arb scan --limit 50             # Scan more markets
    arb execute 1 --amount 50       # Execute opportunity #1 with $50 per leg
    arb execute 1 --amount 50 --yes # Skip confirmation
"""

import asyncio
import json
import logging
import sys
import argparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add parent and scripts dirs to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import GammaClient
from lib.llm_client import LLMClient, DEFAULT_MODEL
from lib.wallet_manager import WalletManager
from lib.position_storage import PositionStorage, PositionEntry

# Import hedge logic
from hedge import extract_implications_for_market, build_portfolios_from_covers

# Lazy import to avoid circular dependencies
def get_trade_executor(wallet):
    from trade import TradeExecutor
    return TradeExecutor(wallet)

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# STATE FILE - persists last scan for execute-by-number
# =============================================================================

ARB_STATE_FILE = Path.home() / ".openclaw" / "polyclaw" / "arb_scan.json"


def save_scan_state(opportunities: list[dict]) -> None:
    """Save scan results so arb execute can reference by number."""
    ARB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ARB_STATE_FILE.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "opportunities": opportunities,
    }, indent=2))


def load_scan_state() -> list[dict]:
    """Load last scan results."""
    if not ARB_STATE_FILE.exists():
        return []
    try:
        data = json.loads(ARB_STATE_FILE.read_text())
        return data.get("opportunities", [])
    except (json.JSONDecodeError, KeyError):
        return []


# =============================================================================
# ARBITRAGE FILTERING
# =============================================================================

def filter_arb_opportunities(
    portfolios: list[dict],
    min_profit: float = 0.03,
) -> list[dict]:
    """
    Filter hedge portfolios to true arbitrage opportunities.

    A portfolio is arbitrage when total_cost < 1.0, meaning you pay less
    than $1 for a position that guarantees at least $1 payout.

    Args:
        portfolios: Output of build_portfolios_from_covers
        min_profit: Minimum profit fraction to include (default 3%)

    Returns:
        Filtered and sorted list of arbitrage opportunities
    """
    arb = [p for p in portfolios if p.get("profit", 0) > min_profit]
    return sorted(arb, key=lambda p: -p.get("profit_pct", 0))


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================

def format_arb_table(opportunities: list[dict]) -> None:
    """Print arbitrage opportunities as formatted table."""
    if not opportunities:
        print("No arbitrage opportunities found.")
        return

    print(f"\n{'#':<4} {'Profit%':>8} {'Cost':>6} {'Min.Win':>8}  "
          f"{'Target (buy)':^50}  {'Cover (buy)'}")
    print("─" * 140)

    for i, p in enumerate(opportunities, 1):
        tq = p["target_question"]
        cq = p["cover_question"]
        tq = tq[:47] + "..." if len(tq) > 47 else tq
        cq = cq[:47] + "..." if len(cq) > 47 else cq

        # Minimum win per $100 of tokens held
        profit_100 = p["profit"] * 100

        print(
            f"{i:<4} {p['profit_pct']:>7.1f}% "
            f"${p['total_cost']:.2f} "
            f"  +${profit_100:.2f}  "
            f"{p['target_position']:>3}@{p['target_price']:.2f} {tq:<47}  "
            f"{p['cover_position']:>3}@{p['cover_price']:.2f} {cq}"
        )


def format_arb_detail(p: dict, amount: float) -> None:
    """Print detailed view of a single arbitrage opportunity."""
    total_capital = amount * 2
    net_cost = amount * p["total_cost"]
    recovered = total_capital - net_cost
    min_profit = amount * p["profit"]
    roi = p["profit_pct"]

    print(f"\n{'─'*60}")
    print(f"  Target : {p['target_position']} on \"{p['target_question']}\"")
    print(f"           Market ID: {p['target_id']}")
    print(f"           Price: ${p['target_price']:.2f}")
    print()
    print(f"  Cover  : {p['cover_position']} on \"{p['cover_question']}\"")
    print(f"           Market ID: {p['cover_id']}")
    print(f"           Price: ${p['cover_price']:.2f}")
    print()
    print(f"  Logic  : {p['relationship'][:80]}")
    print(f"{'─'*60}")
    print(f"  Capital deployed   : ${total_capital:.2f} (${amount:.2f} × 2 legs)")
    print(f"  Recovered from CLOB: ~${recovered:.2f} (selling unwanted sides)")
    print(f"  Net cost           : ~${net_cost:.2f}")
    print(f"  Guaranteed min win : +${min_profit:.2f} ({roi:.1f}% ROI)")
    print(f"  Coverage           : {p['coverage']*100:.1f}%")
    print(f"{'─'*60}")


# =============================================================================
# COMMANDS
# =============================================================================

async def cmd_scan(args):
    """Scan markets for arbitrage opportunities."""
    gamma = GammaClient()

    print(f"Fetching markets...", file=sys.stderr)
    if args.query:
        markets = await gamma.search_markets(args.query, limit=args.limit)
        print(f"Found {len(markets)} markets for '{args.query}'", file=sys.stderr)
    else:
        markets = await gamma.get_trending_markets(limit=args.limit)
        print(f"Got {len(markets)} trending markets", file=sys.stderr)

    if len(markets) < 2:
        print("Need at least 2 markets to scan.")
        return 1

    try:
        llm = LLMClient(model=args.model)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    all_portfolios = []

    print(f"Analyzing {len(markets)} markets for logical implications...", file=sys.stderr)
    try:
        for i, target in enumerate(markets):
            if not args.json:
                print(f"  [{i+1}/{len(markets)}] {target.question[:70]}", file=sys.stderr)

            covers = await extract_implications_for_market(target, markets, llm)
            if covers:
                portfolios = build_portfolios_from_covers(target, covers)
                all_portfolios.extend(portfolios)
    finally:
        await llm.close()

    # Filter to actual arbitrage
    opportunities = filter_arb_opportunities(all_portfolios, min_profit=args.min_profit)

    # Save for execute-by-number
    save_scan_state(opportunities)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Scanned {len(markets)} markets, "
          f"found {len(all_portfolios)} hedges, "
          f"{len(opportunities)} arbitrage opportunities.\n")

    if args.json:
        print(json.dumps(opportunities, indent=2))
    else:
        format_arb_table(opportunities)
        if opportunities:
            print(f"\nTo execute: polyclaw arb execute <#> --amount <USD>")
            print(f"Example  : polyclaw arb execute 1 --amount 50")

    return 0


async def cmd_execute(args):
    """Execute both legs of an arbitrage opportunity."""
    # Resolve opportunity — either by number from last scan or explicit IDs
    if args.number is not None:
        opportunities = load_scan_state()
        if not opportunities:
            print("No saved scan results. Run 'polyclaw arb scan' first.")
            return 1
        if args.number < 1 or args.number > len(opportunities):
            print(f"Invalid opportunity number {args.number}. "
                  f"Scan returned {len(opportunities)} opportunities.")
            return 1
        opp = opportunities[args.number - 1]
    else:
        # Must have explicit market IDs
        if not all([args.target_id, args.target_pos, args.cover_id, args.cover_pos]):
            print("Must provide either --number or "
                  "--target-id, --target-pos, --cover-id, --cover-pos")
            return 1
        opp = {
            "target_id": args.target_id,
            "target_position": args.target_pos.upper(),
            "target_question": args.target_id,
            "target_price": 0,
            "cover_id": args.cover_id,
            "cover_position": args.cover_pos.upper(),
            "cover_question": args.cover_id,
            "cover_price": 0,
            "total_cost": 0,
            "profit": 0,
            "profit_pct": 0,
            "coverage": 0,
            "relationship": "manual",
        }

    wallet = WalletManager()
    if not wallet.is_unlocked:
        print("Error: No wallet configured. Set POLYCLAW_PRIVATE_KEY.")
        return 1

    # Show opportunity detail + confirm
    format_arb_detail(opp, args.amount)

    if not args.yes:
        confirm = input("\nExecute this arbitrage? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted.")
            return 1

    executor = get_trade_executor(wallet)

    # ── Leg 1: target position ──────────────────────────────────────────────
    print(f"\n[1/2] Buying {opp['target_position']} on target market...")
    result1 = await executor.buy_position(
        opp["target_id"],
        opp["target_position"],
        args.amount,
        slippage=args.max_slippage,
    )

    if not result1.success:
        print(f"\nLeg 1 FAILED: {result1.error}")
        print("Aborting — leg 2 not executed. No capital at risk.")
        return 1

    print(f"  ✓ Leg 1 done | Split TX: {result1.split_tx}")
    if result1.clob_filled:
        print(f"  ✓ Unwanted side sold on CLOB: {result1.clob_order_id}")
    elif result1.error:
        print(f"  ! Unwanted side kept (CLOB failed): {result1.error}")

    # ── Leg 2: cover position ───────────────────────────────────────────────
    print(f"\n[2/2] Buying {opp['cover_position']} on cover market...")
    result2 = await executor.buy_position(
        opp["cover_id"],
        opp["cover_position"],
        args.amount,
        slippage=args.max_slippage,
    )

    if not result2.success:
        print(f"\nLeg 2 FAILED: {result2.error}")
        print("WARNING: Leg 1 was executed but leg 2 failed.")
        print(f"  You hold an unhedged {opp['target_position']} position on the target market.")
        print(f"  Check 'polyclaw positions' and hedge or close manually.")
        return 1

    print(f"  ✓ Leg 2 done | Split TX: {result2.split_tx}")
    if result2.clob_filled:
        print(f"  ✓ Unwanted side sold on CLOB: {result2.clob_order_id}")
    elif result2.error:
        print(f"  ! Unwanted side kept (CLOB failed): {result2.error}")

    # ── Summary ─────────────────────────────────────────────────────────────
    total_capital = args.amount * 2
    net_cost = args.amount * opp["total_cost"]
    min_profit = args.amount * opp["profit"]

    print(f"\n{'='*60}")
    print(f"Arbitrage executed successfully!")
    print(f"  Capital deployed    : ${total_capital:.2f}")
    print(f"  Net cost (est.)     : ${net_cost:.2f}")
    print(f"  Guaranteed min gain : +${min_profit:.2f} ({opp['profit_pct']:.1f}%)")
    print(f"\n  Positions recorded in 'polyclaw positions'")

    if args.json:
        print(json.dumps({
            "leg1": {
                "split_tx": result1.split_tx,
                "clob_order_id": result1.clob_order_id,
                "clob_filled": result1.clob_filled,
            },
            "leg2": {
                "split_tx": result2.split_tx,
                "clob_order_id": result2.clob_order_id,
                "clob_filled": result2.clob_filled,
            },
            "total_capital": total_capital,
            "net_cost": net_cost,
            "min_profit": min_profit,
        }, indent=2))

    return 0


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Implication arbitrage scanner")
    parser.add_argument("--json", action="store_true", help="JSON output")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # ── scan ────────────────────────────────────────────────────────────────
    scan_parser = subparsers.add_parser("scan", help="Scan markets for arbitrage")
    scan_parser.add_argument("--query", "-q", help="Filter markets by keyword")
    scan_parser.add_argument(
        "--limit", type=int, default=30,
        help="Number of markets to scan (default: 30)"
    )
    scan_parser.add_argument(
        "--min-profit", type=float, default=0.03, metavar="FRAC",
        help="Minimum profit fraction to show (default: 0.03 = 3%%)"
    )
    scan_parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"LLM model (default: {DEFAULT_MODEL})"
    )

    # ── execute ─────────────────────────────────────────────────────────────
    exec_parser = subparsers.add_parser("execute", help="Execute an arbitrage opportunity")
    exec_parser.add_argument(
        "number", type=int, nargs="?",
        help="Opportunity number from last 'arb scan'"
    )
    exec_parser.add_argument(
        "--amount", type=float, required=True,
        help="USD amount per leg (total capital = amount × 2)"
    )
    exec_parser.add_argument(
        "--max-slippage", type=float, default=0.10, metavar="FRAC",
        help="Max CLOB slippage when selling unwanted side (default: 0.10)"
    )
    exec_parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt"
    )
    # Alternative: explicit market IDs (bypass scan state)
    exec_parser.add_argument("--target-id", help="Target market ID")
    exec_parser.add_argument(
        "--target-pos", choices=["YES", "NO", "yes", "no"],
        help="Target position"
    )
    exec_parser.add_argument("--cover-id", help="Cover market ID")
    exec_parser.add_argument(
        "--cover-pos", choices=["YES", "NO", "yes", "no"],
        help="Cover position"
    )

    args = parser.parse_args()

    if args.command == "scan":
        return asyncio.run(cmd_scan(args))
    elif args.command == "execute":
        return asyncio.run(cmd_execute(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
