#!/usr/bin/env python3
"""Performance analytics — win rate, P&L, trade breakdown, charts.

Usage:
    polyclaw performance summary       # Overall performance metrics
    polyclaw performance trades        # Per-trade breakdown
    polyclaw performance chart         # ASCII chart of portfolio value
"""

import sys
import json
import argparse
from dataclasses import asdict
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.journal_storage import JournalStorage
from lib.portfolio_storage import PortfolioStorage


def cmd_summary(args):
    """Overall performance summary."""
    journal = JournalStorage()
    entries = journal.load_all()

    # Reverse to chronological for processing
    entries.reverse()

    closes = [e for e in entries if e.type in ("close", "redeem")]
    opens = [e for e in entries if e.type == "open"]

    total_trades = len(opens)
    closed_trades = len(closes)

    opened_ids = {e.position_id for e in opens if e.position_id}
    closed_ids = {e.position_id for e in closes if e.position_id}
    open_trades = len(opened_ids - closed_ids) if opened_ids else max(0, total_trades - closed_trades)

    wins = [e for e in closes if (e.pnl or 0) > 0]
    losses = [e for e in closes if (e.pnl or 0) < 0]
    breakeven = [e for e in closes if (e.pnl or 0) == 0]

    total_pnl = sum(e.pnl or 0 for e in closes)
    win_rate = len(wins) / closed_trades if closed_trades > 0 else 0

    gross_profit = sum(e.pnl for e in wins) if wins else 0
    gross_loss = abs(sum(e.pnl for e in losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    # Average return %
    returns = []
    for e in closes:
        if e.amount_usd and e.amount_usd > 0:
            returns.append((e.pnl or 0) / e.amount_usd * 100)
    avg_return_pct = sum(returns) / len(returns) if returns else 0

    best_pnl = max((e.pnl or 0 for e in closes), default=0)
    worst_pnl = min((e.pnl or 0 for e in closes), default=0)

    result = {
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_return_pct": round(avg_return_pct, 1),
        "best_trade_pnl": round(best_pnl, 2),
        "worst_trade_pnl": round(worst_pnl, 2),
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_trades(args):
    """Trade-by-trade breakdown."""
    journal = JournalStorage()
    entries = journal.load_all()
    trades = [e for e in entries if e.type in ("open", "close", "redeem", "merge")]
    if args.limit is not None:
        trades = trades[:args.limit]
    result = [asdict(e) for e in trades]
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

    commands = {
        "summary": cmd_summary,
        "trades": cmd_trades,
        "chart": cmd_chart,
    }

    if args.command in commands:
        return commands[args.command](args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
