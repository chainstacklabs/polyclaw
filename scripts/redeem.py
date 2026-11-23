#!/usr/bin/env python3
"""Claim pUSD from resolved positions held in the deposit wallet.

  redeem list             Resolved positions and their expected payout (read-only)
  redeem <position_id>    Redeem one position (id prefix is enough)
  redeem all              Redeem every resolved position with a payout

Redeeming goes through the SDK (gasless, both YES and NO in one go). A
position is marked redeemed only when it had something to pay out; positions
that resolved against you are marked "lost". Polymarket also auto-redeems
resolved positions in deposit wallets: those are recorded with their payout
and need no transaction. Positions opened by polyclaw
<= 0.1.0 live on the signing wallet: use `polyclaw recover` for those.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from web3 import Web3  # noqa: E402

from lib.contracts import CONTRACTS, CTF_ABI, POSITION_MANAGER_ABI  # noqa: E402
from lib.payout import SIDE_INDEX, payout_rates  # noqa: E402
from lib.pnl import recorded_holdings  # noqa: E402
from lib.position_storage import PositionStorage  # noqa: E402
from lib.trading import ConfigError, create_client  # noqa: E402
from lib.wallet_manager import WalletManager  # noqa: E402

# Balances below this are rounding dust.
DUST_SHARES = 0.01
# redeem_tx recorded when Polymarket's auto-redeem paid the position out.
AUTO_REDEEM = "auto-redeem"


@dataclass
class Claim:
    position: dict
    resolved: bool
    payout: float  # expected pUSD
    note: Optional[str] = None
    # Polymarket's auto-redeem already paid it into the deposit wallet.
    auto: bool = False


def expected_payout(
    wanted: float, unwanted: float, side: str, numerators: list[int], denominator: int
) -> float:
    """pUSD paid for the held YES/NO balances (shares) under the resolution."""
    if not denominator:
        return 0.0
    i = SIDE_INDEX[side]
    return (wanted * numerators[i] + unwanted * numerators[1 - i]) / denominator


def assess(position: dict, w3: Web3, deposit: str) -> Claim:
    """Resolution state and payout for one deposit-wallet position.

    Tokens still in the wallet are ours to redeem. If the market resolved and
    the wallet holds none, Polymarket's auto-redeem paid them out: the payout
    is the recorded shares at the resolution (no transaction needed).
    """
    rates = payout_rates(w3, position)
    if rates is None:
        return Claim(position, resolved=False, payout=0.0)
    ledger = "POSITION_MANAGER" if position.get("protocol") == "v2" else "CTF"
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS[ledger]),
        abi=POSITION_MANAGER_ABI if ledger == "POSITION_MANAGER" else CTF_ABI,
    )
    owner = Web3.to_checksum_address(deposit)

    def bal(token_id: Optional[str]) -> float:
        if not token_id:
            return 0.0
        return contract.functions.balanceOf(owner, int(token_id)).call() / 1e6

    held = bal(position.get("token_id")), bal(position.get("unwanted_token_id"))
    if sum(held) > DUST_SHARES:
        return Claim(position, resolved=True, payout=held[0] * rates[0] + held[1] * rates[1])
    shares, other = recorded_holdings(position)
    payout = shares * rates[0] + other * rates[1]
    return Claim(position, resolved=True, payout=payout, auto=payout > 0)


def _open_deposit_positions(storage: PositionStorage) -> tuple[list[dict], int]:
    rows = [
        p
        for p in storage.load_all()
        if p.get("status") == "open" and not p.get("redeem_tx") and p.get("condition_id")
    ]
    legacy = sum(1 for p in rows if p.get("holder", "eoa") != "deposit")
    return [p for p in rows if p.get("holder") == "deposit"], legacy


def _claims(positions: list[dict], manager: WalletManager) -> list[Claim]:
    w3 = manager.web3()
    out = []
    for p in positions:
        try:
            out.append(assess(p, w3, manager.deposit_wallet))
        except Exception as e:
            print(f"[{p['position_id'][:8]}] read failed: {e}", file=sys.stderr)
    return out


def _redeem(client, claim: Claim, storage: PositionStorage) -> bool:
    pid = claim.position["position_id"]
    if claim.auto:
        storage.mark_redeemed(pid, AUTO_REDEEM, payout=round(claim.payout, 6))
        print(f"[{pid[:8]}] Polymarket already paid ~{claim.payout:.2f} pUSD (auto-redeem)")
        return True
    if claim.payout <= 0:
        storage.update_status(pid, "lost")
        print(f"[{pid[:8]}] resolved with no payout - marked lost")
        return False
    tx = client.redeem_positions(condition_id=claim.position["condition_id"]).wait()
    storage.mark_redeemed(pid, tx.transaction_hash, payout=round(claim.payout, 6))
    print(f"[{pid[:8]}] redeemed ~{claim.payout:.2f} pUSD: {tx.transaction_hash}")
    return True


async def cmd(args) -> int:
    manager = WalletManager()
    storage = PositionStorage()
    if not manager.is_configured or not manager.deposit_wallet:
        print("No deposit wallet. Run `polyclaw wallet setup` first.", file=sys.stderr)
        return 1

    positions, legacy = _open_deposit_positions(storage)
    if args.command == "claim":
        positions = [p for p in positions if p["position_id"].startswith(args.position_id)]
        if len(positions) != 1:
            print(
                f"Expected one open deposit-wallet position matching "
                f"{args.position_id!r}, found {len(positions)}",
                file=sys.stderr,
            )
            return 1

    try:
        claims = [c for c in _claims(positions, manager) if c.resolved]
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.command == "list":
        rows = [
            {
                "position_id": c.position["position_id"],
                "market": c.position["question"][:60],
                "side": c.position["position"],
                "expected_payout": round(c.payout, 4),
                "auto_redeemed": c.auto,
            }
            for c in claims
        ]
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("No resolved deposit-wallet positions.")
        else:
            for r in rows:
                done = "  (already paid by auto-redeem)" if r["auto_redeemed"] else ""
                print(
                    f"{r['position_id'][:8]}  {r['side']:<3}  ~{r['expected_payout']:.2f} pUSD  "
                    f"{r['market']}{done}"
                )
        if legacy and not args.json:
            print(f"\n{legacy} older position(s) live on the signing wallet: run `polyclaw recover scan`.")
        return 0

    if not claims:
        print("Nothing resolved to redeem.")
        return 0
    try:
        client = create_client()
    except ConfigError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1

    failed = 0
    for c in claims:
        try:
            _redeem(client, c, storage)
        except Exception as e:
            failed += 1
            print(f"[{c.position['position_id'][:8]}] redeem failed: {e}", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] not in ("list", "all", "claim", "-h", "--help"):
        argv = ["claim", *argv]
    parser = argparse.ArgumentParser(description="Redeem resolved positions")
    sub = parser.add_subparsers(dest="command")
    lst = sub.add_parser("list", help="Resolved positions and expected payout")
    lst.add_argument("--json", action="store_true")
    sub.add_parser("all", help="Redeem every resolved position")
    claim = sub.add_parser("claim", help="Redeem one position")
    claim.add_argument("position_id")
    args = parser.parse_args(argv)
    if args.command in ("list", "all", "claim"):
        return asyncio.run(cmd(args))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
