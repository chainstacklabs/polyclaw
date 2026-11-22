#!/usr/bin/env python3
"""Recover tokens older polyclaw versions left on the signing wallet.

  recover scan                     Read-only: list leftover tokens and what can be done
  recover run [--yes]              Merge unresolved YES+NO pairs back to collateral and
                                   redeem resolved ones (preview unless --yes)

Checks the condition IDs of positions recorded with holder "eoa" (everything
recorded by polyclaw <= 0.1.0). Add more with --condition <id> or --market <id>.
Transactions are sent from the signing wallet, which needs a little POL.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import GammaClient  # noqa: E402
from lib.legacy import LegacyScanner, plan_action  # noqa: E402
from lib.position_storage import PositionStorage  # noqa: E402
from lib.trading import private_key  # noqa: E402
from lib.wallet_manager import WalletManager  # noqa: E402


async def _condition_ids(args) -> list[str]:
    ids: list[str] = []
    for p in PositionStorage().load_all():
        if p.get("holder", "eoa") == "eoa" and p.get("condition_id"):
            ids.append(p["condition_id"])
    ids.extend(args.condition or [])
    gamma = GammaClient()
    for market_id in args.market or []:
        ids.append((await gamma.get_market(market_id)).condition_id)
    seen: set[str] = set()
    return [c for c in ids if c and not (c.lower() in seen or seen.add(c.lower()))]


def _describe(action) -> dict:
    h = action.holding
    return {
        "condition_id": h.condition_id,
        "collateral": h.collateral_name,
        "yes": h.yes / 1e6,
        "no": h.no / 1e6,
        "resolved": h.resolved,
        "action": action.kind,
        "amount": action.amount / 1e6,
        "note": action.note,
    }


async def cmd(args) -> int:
    manager = WalletManager()
    if not manager.is_configured:
        print("No wallet configured. Set POLYCLAW_PRIVATE_KEY.", file=sys.stderr)
        return 1
    try:
        condition_ids = await _condition_ids(args)
        scanner = LegacyScanner(manager.web3(), manager.address)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not condition_ids:
        print("No legacy positions recorded. Use --condition or --market to check one.")
        return 0

    actions = []
    for cid in condition_ids:
        try:
            holdings = scanner.holdings(cid)
        except Exception as e:
            print(f"[{cid[:10]}] read failed: {e}", file=sys.stderr)
            continue
        actions.extend(a for a in map(plan_action, holdings) if a.holding.yes or a.holding.no)

    if args.json:
        print(json.dumps([_describe(a) for a in actions], indent=2))
    elif not actions:
        print(f"Checked {len(condition_ids)} market(s): no tokens left on {manager.address}.")
    else:
        for a in actions:
            d = _describe(a)
            print(
                f"[{d['condition_id'][:10]}] {d['collateral']:<6} YES={d['yes']:g} "
                f"NO={d['no']:g} -> {d['action']}"
                + (f" ({d['amount']:g})" if a.kind != "none" else "")
                + (f" - {d['note']}" if d["note"] else "")
            )

    todo = [a for a in actions if a.kind != "none"]
    if args.command == "scan" or not todo:
        if todo and not args.json:
            print(f"\n{len(todo)} action(s) available. Run `polyclaw recover run --yes`.")
        return 0
    if not args.yes:
        print("\nPREVIEW ONLY - re-run with --yes to send. Needs a little POL for gas.")
        return 0

    failed = 0
    for a in todo:
        try:
            tx = scanner.execute(a, private_key())
            print(f"[{a.holding.condition_id[:10]}] {a.kind} done: {tx}")
        except Exception as e:
            failed += 1
            print(f"[{a.holding.condition_id[:10]}] {a.kind} failed: {e}", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover legacy signing-wallet tokens")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("scan", "List leftover tokens (read-only)"),
        ("run", "Merge / redeem them (preview unless --yes)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--condition", action="append", help="Extra condition ID to check")
        p.add_argument("--market", action="append", help="Extra Gamma market ID to check")
        p.add_argument("--json", action="store_true", help="JSON output")
        if name == "run":
            p.add_argument("--yes", action="store_true", help="Send transactions")
    args = parser.parse_args()
    if args.command in ("scan", "run"):
        return asyncio.run(cmd(args))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
