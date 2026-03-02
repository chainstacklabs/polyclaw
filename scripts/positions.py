#!/usr/bin/env python3
"""Position tracking and P&L from on-chain data."""

import sys
import json
import asyncio
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.subgraph_client import SubgraphClient, SubgraphError, UserPosition
from lib.market_cache import MarketCache, MarketCacheEntry
from lib.gamma_client import GammaClient
from lib.wallet_manager import WalletManager


def format_pnl(value: float) -> str:
    """Format P&L with sign indicator."""
    if value > 0:
        return f"+${value:.2f}"
    elif value < 0:
        return f"-${abs(value):.2f}"
    else:
        return f"${value:.2f}"


def format_pnl_pct(value: float) -> str:
    """Format P&L percentage with sign indicator."""
    if value > 0:
        return f"+{value:.1f}%"
    elif value < 0:
        return f"{value:.1f}%"
    else:
        return f"{value:.1f}%"


async def enrich_positions(
    positions: list[UserPosition],
    gamma: GammaClient,
    cache: MarketCache,
) -> None:
    """Enrich positions in-place with market metadata (condition_id, question, side).

    For each position, resolves the token_id to its parent market via
    GammaClient, then determines whether the token is the YES or NO side.
    Results are cached in MarketCache for subsequent lookups.
    """
    # Collect unique token_ids
    token_ids_to_resolve: list[str] = []
    token_to_indices: dict[str, list[int]] = {}

    for idx, pos in enumerate(positions):
        token_to_indices.setdefault(pos.token_id, []).append(idx)
        if pos.token_id not in token_ids_to_resolve:
            token_ids_to_resolve.append(pos.token_id)

    resolved: dict[str, dict] = {}  # token_id -> market info

    for token_id in token_ids_to_resolve:
        try:
            market = await gamma.get_market_by_token(token_id)
        except Exception as e:
            print(f"Warning: Failed to resolve market for token {token_id[:12]}: {e}", file=sys.stderr)
            continue
        resolved[token_id] = {
            "condition_id": market.condition_id,
            "question": market.question,
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id or "",
        }
        # Cache result
        if market.condition_id and not cache.get(market.condition_id):
            cache.put(market.condition_id, MarketCacheEntry(
                condition_id=market.condition_id,
                market_id=market.id,
                question=market.question,
                slug=market.slug,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id or "",
                cached_at=datetime.now(timezone.utc).isoformat(),
            ))

    # Apply resolved metadata to positions
    for token_id, indices in token_to_indices.items():
        info = resolved.get(token_id)
        for idx in indices:
            pos = positions[idx]
            if info:
                pos.condition_id = info["condition_id"]
                pos.question = info["question"]
                if token_id == info["yes_token_id"]:
                    pos.side = "YES"
                elif token_id == info["no_token_id"]:
                    pos.side = "NO"
                else:
                    pos.side = "?"
            else:
                pos.question = pos.token_id
                pos.side = "?"


async def cmd_list(args):
    """List positions from on-chain data with P&L."""
    wallet = WalletManager()
    if not wallet.is_unlocked or not wallet.address:
        print(json.dumps({"error": "Wallet not configured. Set POLYCLAW_PRIVATE_KEY env var."}))
        return 1

    try:
        subgraph = SubgraphClient(wallet.address)
        if args.all:
            all_positions = await subgraph.get_positions()
            # Open positions: amount > 0
            open_positions = [p for p in all_positions if p.amount > 0]
            # Closed positions: all with amount == 0
            closed_positions = [p for p in all_positions if p.amount == 0]
            positions = open_positions + closed_positions
        else:
            positions = await subgraph.get_open_positions()
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to query positions: {e}"}))
        return 1

    if not positions:
        print(json.dumps({"positions": [], "message": "No positions found."}))
        return 0

    gamma = GammaClient()
    cache = MarketCache()

    # Enrich positions with market metadata (in-place)
    await enrich_positions(positions, gamma, cache)

    # Get current prices for open positions (amount > 0)
    open_token_ids = list({p.token_id for p in positions if p.amount > 0})
    prices: dict[str, float] = {}
    if open_token_ids:
        try:
            prices = await gamma.get_prices(open_token_ids)
        except Exception as e:
            print(f"Warning: Price fetch failed, using avg_price fallback: {e}", file=sys.stderr)

    results = []
    total_pnl = 0.0
    total_value = 0.0

    for pos in positions:
        is_open = pos.amount > 0
        current_price = float(prices.get(pos.token_id, pos.avg_price)) if is_open else 0.0
        current_value = current_price * pos.amount if is_open else 0.0
        cost_basis = pos.avg_price * pos.amount if is_open else 0.0
        unrealized_pnl = (current_value - cost_basis) if is_open else 0.0
        display_pnl = unrealized_pnl if is_open else pos.realized_pnl
        pnl_pct = (unrealized_pnl / cost_basis * 100) if is_open and cost_basis > 0 else 0.0

        result = {
            "token_id": pos.token_id,
            "market": pos.question,
            "side": pos.side,
            "entry": f"${pos.avg_price:.2f}",
            "current": f"${current_price:.2f}" if is_open else "-",
            "value": f"${current_value:.2f}" if is_open else "-",
            "pnl": format_pnl(display_pnl),
            "pnl_pct": format_pnl_pct(pnl_pct) if is_open else "-",
        }

        results.append(result)
        total_pnl += display_pnl
        total_value += current_value

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Table output
        print(f"{'ID':<10} {'Side':<4} {'Entry':>7} {'Current':>8} {'Value':>9} {'P&L':>10} {'P&L%':>8}  {'Market'}")
        print("-" * 100)
        for r in results:
            short_id = r["token_id"][:8]
            market_short = r["market"][:35] if len(r["market"]) > 35 else r["market"]
            print(
                f"{short_id:<10} {r['side']:<4} {r['entry']:>7} {r['current']:>8} "
                f"{r['value']:>9} {r['pnl']:>10} {r['pnl_pct']:>8}  {market_short}"
            )

        print("-" * 100)
        print(f"Total: {len(results)} positions | Value: ${total_value:.2f} | P&L: {format_pnl(total_pnl)}")

    return 0


async def cmd_show(args):
    """Show detailed position info by token_id prefix."""
    wallet = WalletManager()
    if not wallet.is_unlocked or not wallet.address:
        print(json.dumps({"error": "Wallet not configured. Set POLYCLAW_PRIVATE_KEY env var."}))
        return 1

    try:
        subgraph = SubgraphClient(wallet.address)
        all_positions = await subgraph.get_positions()
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to query positions: {e}"}))
        return 1

    # Match by token_id prefix
    prefix = args.token_id
    matches = [p for p in all_positions if p.token_id.startswith(prefix)]

    if not matches:
        print(json.dumps({"error": f"Position not found: {prefix}"}))
        return 1

    if len(matches) > 1:
        print(json.dumps({
            "error": "Multiple matches, be more specific",
            "matches": [{"token_id": p.token_id[:12], "amount": p.amount} for p in matches],
        }))
        return 1

    pos = matches[0]
    gamma = GammaClient()
    cache = MarketCache()

    # Enrich with market metadata (in-place)
    await enrich_positions([pos], gamma, cache)

    # Get current price if position is open
    is_open = pos.amount > 0
    current_price = 0.0
    if is_open:
        try:
            prices = await gamma.get_prices([pos.token_id])
            current_price = float(prices.get(pos.token_id, pos.avg_price))
        except Exception as e:
            print(f"Warning: Price fetch failed for token {pos.token_id[:12]}: {e}", file=sys.stderr)

    current_value = current_price * pos.amount if is_open else 0.0
    cost_basis = pos.avg_price * pos.amount if is_open else 0.0
    unrealized_pnl = (current_value - cost_basis) if is_open else 0.0

    result = {
        "token_id": pos.token_id,
        "condition_id": pos.condition_id,
        "market": pos.question,
        "side": pos.side,
        "amount": pos.amount,
        "avg_price": pos.avg_price,
        "entry": f"${pos.avg_price:.2f}",
        "current": f"${current_price:.2f}" if is_open else None,
        "value": f"${current_value:.2f}" if is_open else None,
        "pnl": format_pnl(unrealized_pnl) if is_open else format_pnl(pos.realized_pnl),
        "pnl_pct": format_pnl_pct(
            unrealized_pnl / cost_basis * 100 if cost_basis > 0 else 0.0
        ) if is_open else None,
        "realized_pnl": pos.realized_pnl,
        "total_bought": pos.total_bought,
        "status": "open" if is_open else "closed",
    }

    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Position tracking (on-chain)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # List
    list_parser = subparsers.add_parser("list", help="List positions")
    list_parser.add_argument("--all", action="store_true", help="Include closed positions")

    # Show
    show_parser = subparsers.add_parser("show", help="Show position details")
    show_parser.add_argument("token_id", help="Token ID (prefix match)")

    parser.set_defaults(command="list", all=False)
    args = parser.parse_args()

    if args.command == "list":
        return asyncio.run(cmd_list(args))
    elif args.command == "show":
        return asyncio.run(cmd_show(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
