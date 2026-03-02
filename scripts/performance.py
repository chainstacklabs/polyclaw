#!/usr/bin/env python3
"""Performance analytics — win rate, P&L, trade breakdown, charts.

Usage:
    polyclaw performance summary       # Overall performance metrics
    polyclaw performance trades        # Per-trade breakdown
    polyclaw performance chart         # ASCII chart of portfolio value
"""

import sys
import json
import asyncio
import argparse
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient
from lib.subgraph_client import SubgraphClient, SubgraphError
from lib.market_cache import MarketCache
from lib.portfolio_storage import PortfolioStorage


async def cmd_summary(args):
    """Overall performance summary from PnL subgraph."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    try:
        client = SubgraphClient(wallet.address)
        positions = await client.get_positions()
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to query performance data: {e}"}))
        return 1

    # Classify positions
    open_positions = [p for p in positions if p.amount > 0]
    closed_positions = [p for p in positions if p.amount == 0]

    total_trades = len(open_positions) + len(closed_positions)
    open_trades = len(open_positions)
    closed_trades = len(closed_positions)

    wins = [p for p in closed_positions if p.realized_pnl > 0]
    losses = [p for p in closed_positions if p.realized_pnl < 0]
    breakeven = [p for p in closed_positions if p.realized_pnl == 0]

    total_pnl = sum(p.realized_pnl for p in closed_positions)
    decisive_trades = len(wins) + len(losses)
    win_rate = len(wins) / decisive_trades if decisive_trades > 0 else 0

    gross_profit = sum(p.realized_pnl for p in wins) if wins else 0
    gross_loss = abs(sum(p.realized_pnl for p in losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # Average return %
    returns = []
    for p in closed_positions:
        if p.total_bought > 0:
            returns.append(p.realized_pnl / p.total_bought * 100)
    avg_return_pct = sum(returns) / len(returns) if returns else 0

    best_pnl = max((p.realized_pnl for p in closed_positions), default=0)
    worst_pnl = min((p.realized_pnl for p in closed_positions), default=0)

    result = {
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "avg_return_pct": round(avg_return_pct, 1),
        "best_trade_pnl": round(best_pnl, 2),
        "worst_trade_pnl": round(worst_pnl, 2),
    }
    print(json.dumps(result, indent=2))
    return 0


async def cmd_trades(args):
    """Trade-by-trade breakdown from on-chain events.

    Note: Same output as `portfolio history`. Keep in sync with
    portfolio.py::cmd_history if modifying the event format.
    """
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    try:
        client = SubgraphClient(wallet.address)
        events = await client.get_all_events()
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to query trade events: {e}"}))
        return 1

    if args.limit is not None:
        if args.limit < 0:
            print(json.dumps({"error": "--limit must be >= 0"}))
            return 1
        events = events[:args.limit]

    # Pre-populate cache from position token_ids (reliable Gamma lookup),
    # then resolve events by condition_id from cache.
    gamma = GammaClient()
    cache = MarketCache()
    try:
        positions = await client.get_positions()
        token_ids = list({p.token_id for p in positions})
        await cache.populate_from_token_ids(token_ids, gamma)
    except SubgraphError:
        pass  # Best-effort; resolve_batch will try conditionId fallback

    condition_ids = [cid for cid in {e.condition_id for e in events} if cid]
    metadata = await cache.resolve_batch(condition_ids, gamma)

    result = []
    for e in events:
        entry = metadata.get(e.condition_id)
        result.append({
            "id": e.id,
            "timestamp": e.timestamp,
            "type": e.event_type,
            "market_id": entry.market_id if entry else "",
            "question": entry.question if entry else e.condition_id,
            "amount_usd": round(e.amount_usdc, 2),
            "tx_hash": e.tx_hash,
            "condition_id": e.condition_id,
        })

    print(json.dumps(result, indent=2))
    return 0


def cmd_chart(args):
    """ASCII chart of portfolio value over time from snapshots."""
    storage = PortfolioStorage()
    snapshots = storage.load_snapshots()

    if len(snapshots) < 2:
        print(json.dumps({"error": "Need at least 2 snapshots. Run 'polyclaw portfolio snapshot' periodically."}))
        return 1

    values = [s.total_value_usd for s in snapshots]
    dates = [s.timestamp[:10] for s in snapshots]
    min_val = min(values) * 0.95
    max_val = max(values) * 1.05
    height = 15
    width = min(len(values), 60)

    # Resample if too many points
    if len(values) > width:
        step = len(values) / width
        sampled_values = [values[int(i * step)] for i in range(width)]
        sampled_dates = [dates[int(i * step)] for i in range(width)]
    else:
        sampled_values = values
        sampled_dates = dates

    val_range = max_val - min_val
    if val_range == 0:
        val_range = 1

    print("Portfolio Value Chart")
    print(f"  ${max_val:.2f} |")

    for row in range(height - 1, -1, -1):
        threshold = min_val + (row / height) * val_range
        line = ""
        for v in sampled_values:
            if v >= threshold:
                line += "#"
            else:
                line += " "

        if row == height // 2:
            label = f"${min_val + (row / height) * val_range:.2f}"
            print(f"  {label:>8} |{line}")
        elif row == 0:
            print(f"  ${min_val:.2f} |{line}")
        else:
            print(f"           |{line}")

    print(f"           +{'-' * len(sampled_values)}")
    pad = max(0, len(sampled_values) - len(sampled_dates[0]) - len(sampled_dates[-1]))
    print(f"            {sampled_dates[0]}{' ' * pad}{sampled_dates[-1]}")
    if values[0] > 0:
        change_pct = (values[-1] / values[0] - 1) * 100
        print(f"\n  {len(snapshots)} snapshots | ${values[0]:.2f} -> ${values[-1]:.2f} ({change_pct:+.1f}%)")
    else:
        print(f"\n  {len(snapshots)} snapshots | ${values[0]:.2f} -> ${values[-1]:.2f}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Performance analytics")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("summary", help="Overall performance summary")

    trades_parser = subparsers.add_parser("trades", help="Trade-by-trade breakdown")
    trades_parser.add_argument("--limit", type=int, default=None,
                               help="Max trades to show")

    subparsers.add_parser("chart", help="ASCII portfolio value chart")

    args = parser.parse_args()

    sync_commands = {
        "chart": cmd_chart,
    }
    async_commands = {
        "summary": cmd_summary,
        "trades": cmd_trades,
    }

    if args.command in sync_commands:
        return sync_commands[args.command](args)
    elif args.command in async_commands:
        return asyncio.run(async_commands[args.command](args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
