#!/usr/bin/env python3
"""Portfolio management — status, rules, history, snapshots.

Usage:
    polyclaw portfolio status       # Portfolio overview with allocation check
    polyclaw portfolio rules        # Show portfolio rules
    polyclaw portfolio history      # Trade journal entries
    polyclaw portfolio snapshot     # Save current portfolio snapshot
"""

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

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient
from lib.position_storage import PositionStorage
from lib.journal_storage import JournalStorage
from lib.portfolio_storage import PortfolioStorage, PortfolioSnapshot


async def get_positions_value(positions: list[dict]) -> tuple[float, int]:
    """Calculate total open positions market value. Returns (value_usd, count)."""
    gamma = GammaClient()
    total = 0.0
    count = 0

    for pos in positions:
        if pos.get("status") != "open":
            continue
        try:
            market = await gamma.get_market(pos["market_id"])
            current_price = market.yes_price if pos["position"] == "YES" else market.no_price
            value = pos["entry_amount"] * current_price
            total += value
            count += 1
        except Exception:
            # Fallback to entry amount
            total += pos.get("entry_amount", 0)
            count += 1

    return total, count


def cmd_status(args):
    """Portfolio status overview."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    balances = wallet.get_balances()
    storage = PositionStorage()
    positions = storage.load_all()
    portfolio_storage = PortfolioStorage()
    rules = portfolio_storage.load_rules()

    positions_usd, position_count = asyncio.run(get_positions_value(positions))
    cash = balances.usdc + balances.usdc_e
    total = cash + positions_usd
    cash_pct = (cash / total * 100) if total > 0 else 100
    positions_pct = 100 - cash_pct

    # Check rules compliance
    min_cash_reserve = rules.get("min_cash_reserve_pct", 25)
    max_exposure = rules.get("max_portfolio_exposure_pct", 75)
    max_positions = rules.get("max_positions", 8)

    violations = []
    if cash_pct < min_cash_reserve:
        violations.append(f"Cash {cash_pct:.0f}% < minimum {min_cash_reserve}%")
    if positions_pct > max_exposure:
        violations.append(f"Exposure {positions_pct:.0f}% > maximum {max_exposure}%")
    if position_count > max_positions:
        violations.append(f"Positions {position_count} > maximum {max_positions}")

    result = {
        "total_value_usd": round(total, 2),
        "cash": {
            "usdc": round(balances.usdc, 2),
            "usdc_e": round(balances.usdc_e, 2),
            "total": round(cash, 2),
            "pct": round(cash_pct, 1),
        },
        "positions": {
            "count": position_count,
            "value_usd": round(positions_usd, 2),
            "pct": round(positions_pct, 1),
        },
        "pol_balance": round(balances.pol, 6),
        "rules_compliant": len(violations) == 0,
        "rule_violations": violations,
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_rules(args):
    """Show portfolio rules."""
    storage = PortfolioStorage()
    rules = storage.load_rules()
    print(json.dumps(rules, indent=2))
    return 0


def cmd_history(args):
    """Show trade journal history."""
    journal = JournalStorage()
    entries = journal.load_all(limit=args.limit)

    from dataclasses import asdict
    result = [asdict(e) for e in entries]
    print(json.dumps(result, indent=2))
    return 0


def cmd_snapshot(args):
    """Save current portfolio snapshot."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    balances = wallet.get_balances()
    pos_storage = PositionStorage()
    positions = pos_storage.load_all()

    positions_usd, position_count = asyncio.run(get_positions_value(positions))
    cash = balances.usdc + balances.usdc_e
    total = cash + positions_usd
    cash_pct = (cash / total * 100) if total > 0 else 100
    positions_pct = 100 - cash_pct

    snapshot = PortfolioSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_value_usd=round(total, 4),
        cash_usd=round(cash, 4),
        positions_usd=round(positions_usd, 4),
        position_count=position_count,
        pol_balance=round(balances.pol, 6),
        cash_pct=round(cash_pct, 2),
        positions_pct=round(positions_pct, 2),
    )

    portfolio_storage = PortfolioStorage()
    portfolio_storage.save_snapshot(snapshot)

    from dataclasses import asdict
    print(json.dumps(asdict(snapshot), indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Portfolio management")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("status", help="Portfolio overview")
    subparsers.add_parser("rules", help="Show portfolio rules")

    history_parser = subparsers.add_parser("history", help="Trade journal")
    history_parser.add_argument("--limit", type=int, default=None,
                                help="Max entries to show")

    subparsers.add_parser("snapshot", help="Save portfolio snapshot")

    args = parser.parse_args()

    commands = {
        "status": cmd_status,
        "rules": cmd_rules,
        "history": cmd_history,
        "snapshot": cmd_snapshot,
    }

    if args.command in commands:
        return commands[args.command](args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
