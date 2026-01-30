#!/usr/bin/env python3
"""Position tracking and P&L."""

import sys
import json
import asyncio
import argparse
import uuid
from datetime import datetime
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.position_storage import PositionStorage, PositionEntry
from lib.gamma_client import GammaClient


def format_pnl(value: float) -> str:
    """Format P&L with color indicator."""
    if value > 0:
        return f"+${value:.2f}"
    elif value < 0:
        return f"-${abs(value):.2f}"
    else:
        return f"${value:.2f}"


async def calculate_position_pnl(position: dict, gamma: GammaClient) -> dict:
    """Calculate current P&L for a position."""
    try:
        market = await gamma.get_market(position["market_id"])
        current_price = market.yes_price if position["position"] == "YES" else market.no_price

        entry_price = position["entry_price"]
        entry_amount = position["entry_amount"]

        # Calculate token count (amount / entry_price approximation)
        # In reality: split gives you `amount` tokens of each side
        token_count = entry_amount  # Split gives 1 token per $1

        # Current value
        current_value = token_count * current_price

        # P&L
        # If CLOB sell succeeded, we recovered some cost
        if position.get("clob_filled"):
            # Approximate recovered value (unwanted side at ~opposite price)
            unwanted_price = 1 - entry_price
            recovered = token_count * unwanted_price * 0.9  # 10% slippage
            actual_cost = entry_amount - recovered
        else:
            actual_cost = entry_amount

        pnl = current_value - actual_cost
        pnl_pct = (pnl / actual_cost * 100) if actual_cost > 0 else 0
        effective_entry = actual_cost / token_count if token_count > 0 else entry_price

        return {
            "current_price": current_price,
            "entry_price": entry_price,
            "effective_entry": effective_entry,
            "current_value": round(current_value, 2),
            "cost_basis": round(actual_cost, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "market_active": market.active,
            "market_resolved": market.resolved,
            "market_outcome": market.outcome,
        }
    except Exception as e:
        return {
            "error": str(e),
            "current_price": 0,
            "entry_price": position["entry_price"],
            "effective_entry": position["entry_price"],
            "current_value": 0,
            "cost_basis": position["entry_amount"],
            "pnl": 0,
            "pnl_pct": 0,
        }


async def cmd_list(args):
    """List all positions with P&L."""
    storage = PositionStorage()
    gamma = GammaClient()

    if args.all:
        positions = storage.load_all()
    else:
        positions = storage.get_open()

    if not positions:
        print("No positions found.")
        return 0

    results = []
    total_pnl = 0
    total_value = 0
    total_cost = 0

    for pos in positions:
        pnl_info = await calculate_position_pnl(pos, gamma)

        result = {
            "position_id": pos["position_id"][:8],
            "market": pos["question"][:40] + "..." if len(pos["question"]) > 40 else pos["question"],
            "side": pos["position"],
            "entry": f"${pnl_info.get('effective_entry', pos['entry_price']):.2f}",
            "current": f"${pnl_info['current_price']:.2f}",
            "value": f"${pnl_info['current_value']:.2f}",
            "pnl": format_pnl(pnl_info["pnl"]),
            "pnl_pct": f"{pnl_info['pnl_pct']:+.1f}%",
            "status": pos["status"],
        }

        if pnl_info.get("market_resolved"):
            result["status"] = f"resolved: {pnl_info['market_outcome']}"

        results.append(result)

        total_pnl += pnl_info["pnl"]
        total_value += pnl_info["current_value"]
        total_cost += pnl_info["cost_basis"]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Table output
        print(f"{'ID':<10} {'Side':<4} {'Entry':>6} {'Now':>6} {'P&L':>10} {'Market'}")
        print("-" * 80)
        for r in results:
            print(f"{r['position_id']:<10} {r['side']:<4} {r['entry']:>6} {r['current']:>6} {r['pnl']:>10} {r['market'][:35]}")

        print("-" * 80)
        print(f"Total: {len(results)} positions | Value: ${total_value:.2f} | P&L: {format_pnl(total_pnl)}")

    return 0


async def cmd_show(args):
    """Show position details."""
    storage = PositionStorage()
    gamma = GammaClient()

    # Find position by ID prefix
    positions = storage.load_all()
    matches = [p for p in positions if p["position_id"].startswith(args.position_id)]

    if not matches:
        print(f"Position not found: {args.position_id}")
        return 1

    if len(matches) > 1:
        print(f"Multiple matches, be more specific:")
        for p in matches:
            print(f"  {p['position_id'][:12]} - {p['question'][:40]}")
        return 1

    pos = matches[0]
    pnl_info = await calculate_position_pnl(pos, gamma)

    result = {
        **pos,
        "pnl_info": pnl_info,
    }

    print(json.dumps(result, indent=2))
    return 0


def cmd_add(args):
    """Manually add a position (for testing or importing)."""
    storage = PositionStorage()

    entry = PositionEntry(
        position_id=str(uuid.uuid4()),
        market_id=args.market_id,
        question=args.question or "Manual entry",
        position=args.position.upper(),
        token_id=args.token_id or "",
        entry_time=datetime.utcnow().isoformat(),
        entry_amount=args.amount,
        entry_price=args.price,
        split_tx=args.tx or "manual",
        clob_order_id=None,
        clob_filled=False,
        status="open",
    )

    storage.add(entry)
    print(f"Position added: {entry.position_id[:12]}")
    return 0


def cmd_close(args):
    """Close a position (mark as closed)."""
    storage = PositionStorage()

    # Find by prefix
    positions = storage.load_all()
    matches = [p for p in positions if p["position_id"].startswith(args.position_id)]

    if not matches:
        print(f"Position not found: {args.position_id}")
        return 1

    if len(matches) > 1:
        print(f"Multiple matches, be more specific")
        return 1

    pos = matches[0]
    storage.update_status(pos["position_id"], "closed")
    print(f"Position closed: {pos['position_id'][:12]}")
    return 0


def cmd_delete(args):
    """Delete a position record."""
    storage = PositionStorage()

    # Find by prefix
    positions = storage.load_all()
    matches = [p for p in positions if p["position_id"].startswith(args.position_id)]

    if not matches:
        print(f"Position not found: {args.position_id}")
        return 1

    if len(matches) > 1:
        print(f"Multiple matches, be more specific")
        return 1

    pos = matches[0]

    if not args.force:
        confirm = input(f"Delete position {pos['position_id'][:12]}? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted")
            return 1

    storage.delete(pos["position_id"])
    print(f"Position deleted: {pos['position_id'][:12]}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Position tracking")
    parser.add_argument("--json", action="store_true", help="JSON output")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # List
    list_parser = subparsers.add_parser("list", help="List positions")
    list_parser.add_argument("--all", action="store_true", help="Include closed positions")

    # Show
    show_parser = subparsers.add_parser("show", help="Show position details")
    show_parser.add_argument("position_id", help="Position ID (prefix match)")

    # Add
    add_parser = subparsers.add_parser("add", help="Manually add position")
    add_parser.add_argument("market_id", help="Market ID")
    add_parser.add_argument("position", choices=["YES", "NO"], help="Position")
    add_parser.add_argument("amount", type=float, help="Amount in USD")
    add_parser.add_argument("price", type=float, help="Entry price")
    add_parser.add_argument("--question", help="Market question")
    add_parser.add_argument("--token-id", help="Token ID")
    add_parser.add_argument("--tx", help="Transaction hash")

    # Close
    close_parser = subparsers.add_parser("close", help="Close position")
    close_parser.add_argument("position_id", help="Position ID (prefix match)")

    # Delete
    delete_parser = subparsers.add_parser("delete", help="Delete position")
    delete_parser.add_argument("position_id", help="Position ID (prefix match)")
    delete_parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if args.command == "list":
        return asyncio.run(cmd_list(args))
    elif args.command == "show":
        return asyncio.run(cmd_show(args))
    elif args.command == "add":
        return cmd_add(args)
    elif args.command == "close":
        return cmd_close(args)
    elif args.command == "delete":
        return cmd_delete(args)
    else:
        # Default to list
        args.all = False
        return asyncio.run(cmd_list(args))


if __name__ == "__main__":
    sys.exit(main() or 0)
