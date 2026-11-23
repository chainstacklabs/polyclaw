#!/usr/bin/env python3
"""Position tracking and P&L.

P&L = value of what the wallet holds now + pUSD already back - what the
position cost. See lib/pnl.py. Holdings are read from the chain when
CHAINSTACK_NODE and the wallet are configured, else from recorded trades.
"""

import sys
import json
import asyncio
import argparse
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.position_storage import PositionStorage, PositionEntry
from lib.gamma_client import GammaClient
from lib.pnl import position_pnl
from lib.wallet_manager import WalletManager


def format_pnl(value: float) -> str:
    """Format P&L with a sign."""
    if value > 0:
        return f"+${value:.2f}"
    elif value < 0:
        return f"-${abs(value):.2f}"
    else:
        return f"${value:.2f}"


class BalanceReader:
    """On-chain (your side, other side) balances, or None when they can't be read."""

    def __init__(self, manager: Optional[WalletManager] = None):
        self.manager = manager or WalletManager()
        self.available = bool(self.manager.is_configured and self.manager.rpc_url)

    def owner(self, position: dict) -> Optional[str]:
        if position.get("holder") == "deposit":
            return self.manager.deposit_wallet
        return self.manager.address  # positions from polyclaw <= 0.1.0

    def __call__(self, position: dict) -> Optional[tuple[float, float]]:
        owner = self.owner(position) if self.available else None
        if not owner or not position.get("token_id"):
            return None
        protocol = position.get("protocol", "v1")
        try:
            shares = self.manager.token_balance(owner, position["token_id"], protocol)
            other_id = position.get("unwanted_token_id")
            other = self.manager.token_balance(owner, other_id, protocol) if other_id else 0.0
        except Exception:
            return None
        return shares, other


async def calculate_position_pnl(position: dict, gamma: GammaClient, balances) -> dict:
    """P&L for one position; `balances` reads holdings (see BalanceReader)."""
    try:
        market = await gamma.get_market(position["market_id"])
    except Exception as e:
        return {"error": f"market lookup failed: {e}"}
    pnl = position_pnl(position, market.yes_price, market.no_price, balances(position))
    return {
        **asdict(pnl),
        "market_active": market.active,
        "market_resolved": market.resolved,
        "market_outcome": market.outcome,
    }


async def cmd_list(args):
    """List positions with P&L."""
    storage = PositionStorage()
    gamma = GammaClient()
    balances = BalanceReader()

    positions = storage.load_all() if args.all else storage.get_open()
    if not positions:
        print("No positions found.")
        return 0

    rows = []
    totals = {"cost": 0.0, "value": 0.0, "realized": 0.0, "pnl": 0.0}
    estimated = redeemable = failed = 0
    for pos in positions:
        info = await calculate_position_pnl(pos, gamma, balances)
        row = {
            "position_id": pos["position_id"],
            "market": pos["question"],
            "side": pos["position"],
            "status": pos.get("status"),
            **info,
        }
        rows.append(row)
        if "error" in info:
            failed += 1
            continue
        for key in totals:
            totals[key] += info[key]
        estimated += info["cost_estimated"]
        if info["market_resolved"] and pos.get("status") == "open":
            redeemable += 1

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(
        f"{'ID':<9} {'Side':<4} {'Shares':>7} {'Now':>5} {'Cost':>8} {'Value':>8} "
        f"{'Back':>8} {'P&L':>9}  Market"
    )
    print("-" * 96)
    for r in rows:
        if "error" in r:
            print(f"{r['position_id'][:8]:<9} {r['side']:<4} {'?':>7}  {r['error'][:60]}")
            continue
        cost = ("~" if r["cost_estimated"] else "") + f"${r['cost']:.2f}"
        value, back = f"${r['value']:.2f}", f"${r['realized']:.2f}"
        market = r["market"][:32]
        if r["market_resolved"]:
            market = f"[resolved {r['market_outcome']}] " + market
        elif r["status"] != "open":
            market = f"[{r['status']}] " + market
        print(
            f"{r['position_id'][:8]:<9} {r['side']:<4} {r['shares']:>7.2f} {r['price']:>5.2f} "
            f"{cost:>8} {value:>8} {back:>8} {format_pnl(r['pnl']):>9}  "
            f"{market}"
        )
    print("-" * 96)
    print(
        f"{len(rows)} position(s) | cost ${totals['cost']:.2f} | value ${totals['value']:.2f} | "
        f"back ${totals['realized']:.2f} | P&L {format_pnl(totals['pnl'])}"
    )
    if not balances.available:
        print("Shares are from recorded trades: set CHAINSTACK_NODE to read the wallet.")
    if estimated:
        print("~ cost estimated from the buy preview (positions from before polyclaw 0.3.0).")
    if failed:
        print(f"{failed} position(s) skipped: market lookup failed.")
    if redeemable:
        print(f"{redeemable} resolved position(s) to claim: run `polyclaw redeem list`.")
    return 0


async def cmd_show(args):
    """Show position details."""
    storage = PositionStorage()
    matches = [p for p in storage.load_all() if p["position_id"].startswith(args.position_id)]

    if not matches:
        print(f"Position not found: {args.position_id}")
        return 1

    if len(matches) > 1:
        print("Multiple matches, be more specific:")
        for p in matches:
            print(f"  {p['position_id'][:12]} - {p['question'][:40]}")
        return 1

    pos = matches[0]
    pnl_info = await calculate_position_pnl(pos, GammaClient(), BalanceReader())
    print(json.dumps({**pos, "pnl_info": pnl_info}, indent=2))
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
        print("Multiple matches, be more specific")
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
        print("Multiple matches, be more specific")
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
