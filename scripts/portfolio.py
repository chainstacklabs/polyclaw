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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient
from lib.portfolio_storage import PortfolioStorage, PortfolioSnapshot
from lib.subgraph_client import SubgraphClient, SubgraphError
from lib.market_cache import MarketCache


async def get_positions_value(wallet_address: str) -> tuple[float, int]:
    """Calculate total open positions market value. Returns (value_usd, count).

    Raises SubgraphError if the subgraph is unreachable.
    """
    client = SubgraphClient(wallet_address)
    positions = await client.get_open_positions()

    if not positions:
        return 0.0, 0

    gamma = GammaClient()
    total = 0.0

    # Batch price lookup via CLOB API
    token_ids = [pos.token_id for pos in positions]
    try:
        prices = await gamma.get_prices(token_ids)
    except Exception as e:
        print(f"Warning: Failed to fetch midpoint prices; using avg_price fallback: {e}", file=sys.stderr)
        prices = {}

    for pos in positions:
        current_price = float(prices.get(pos.token_id, pos.avg_price))
        total += pos.amount * current_price

    return total, len(positions)


async def cmd_status(args):
    """Portfolio status overview."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    balances = wallet.get_balances()
    portfolio_storage = PortfolioStorage()
    rules = portfolio_storage.load_rules()

    try:
        positions_usd, position_count = await get_positions_value(wallet.address)
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to fetch open positions: {e}"}))
        return 1

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


async def cmd_history(args):
    """Show trade history from on-chain events."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    try:
        client = SubgraphClient(wallet.address)
        events = await client.get_all_events()
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to query trade history: {e}"}))
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


async def cmd_snapshot(args):
    """Save current portfolio snapshot."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    balances = wallet.get_balances()

    try:
        positions_usd, position_count = await get_positions_value(wallet.address)
    except SubgraphError as e:
        print(json.dumps({"error": f"Failed to fetch open positions: {e}"}))
        return 1

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

    sync_commands = {
        "rules": cmd_rules,
    }
    async_commands = {
        "status": cmd_status,
        "history": cmd_history,
        "snapshot": cmd_snapshot,
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
