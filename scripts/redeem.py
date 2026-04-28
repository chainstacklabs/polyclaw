#!/usr/bin/env python3
"""Claim collateral from resolved Polymarket positions.

After a market resolves, holders of winning outcome tokens must call
``redeemPositions`` on the CTF contract to swap those tokens back into
collateral (pUSD post-2026-04-28). polymarket.com does this automatically
for orders placed via its UI, but positions opened via direct contract
calls (``polyclaw buy``) don't appear there — so the holder has to claim
the collateral themselves. This is what this script does.

Usage:
    polyclaw redeem list                # show resolved/redeemable positions
    polyclaw redeem <position_id>       # claim one position
    polyclaw redeem all                 # claim every redeemable position

Refs #4, #2.
"""

import sys
import json
import asyncio
import argparse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from web3 import Web3

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient
from lib.position_storage import PositionStorage
from lib.contracts import CONTRACTS, CTF_ABI, POLYGON_CHAIN_ID


# Polymarket binary market convention: outcome index 0 = YES, 1 = NO.
# splitPosition partitions [1, 2] map indexSet bit-0 → outcome 0 (YES),
# bit-1 → outcome 1 (NO). To redeem a single side we pass that same
# bitmask back as a single-element indexSets array.
SIDE_TO_OUTCOME_INDEX = {"YES": 0, "NO": 1}
SIDE_TO_INDEX_SET = {"YES": 1, "NO": 2}


def _condition_bytes(condition_id: str) -> bytes:
    return bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)


def _ctf(w3: Web3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["CTF"]),
        abi=CTF_ABI,
    )


async def _ensure_condition_id(position: dict, gamma: GammaClient, storage: PositionStorage) -> Optional[str]:
    """Return position's condition_id, backfilling from Gamma when missing."""
    cid = position.get("condition_id")
    if cid:
        return cid
    try:
        market = await gamma.get_market(position["market_id"])
        cid = market.condition_id
    except Exception as e:
        print(f"  [{position['position_id'][:8]}] failed to fetch market: {e}")
        return None
    if cid:
        storage.set_condition_id(position["position_id"], cid)
    return cid or None


def _resolution_state(w3: Web3, condition_id: str) -> tuple[bool, list[int], int]:
    """Return (resolved, numerators, denominator) for a condition."""
    ctf = _ctf(w3)
    cb = _condition_bytes(condition_id)
    denom = ctf.functions.payoutDenominator(cb).call()
    if denom == 0:
        return False, [], 0
    slot_count = ctf.functions.getOutcomeSlotCount(cb).call()
    nums = [ctf.functions.payoutNumerators(cb, i).call() for i in range(slot_count)]
    return True, nums, denom


async def cmd_list(args):
    """Show positions that are resolved and not yet redeemed."""
    storage = PositionStorage()
    gamma = GammaClient()

    if not (rpc := WalletManager().rpc_url):
        print("Error: CHAINSTACK_NODE not set")
        return 1
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60, "proxies": {}}))

    positions = [p for p in storage.load_all() if p.get("status") != "resolved" and not p.get("redeem_tx")]
    if not positions:
        print("No open positions to check.")
        return 0

    redeemable = []
    for pos in positions:
        cid = await _ensure_condition_id(pos, gamma, storage)
        if not cid:
            continue
        try:
            resolved, nums, denom = _resolution_state(w3, cid)
        except Exception as e:
            print(f"  [{pos['position_id'][:8]}] CTF query failed: {e}")
            continue
        if not resolved:
            continue
        side = pos["position"]
        won = nums[SIDE_TO_OUTCOME_INDEX[side]] > 0
        redeemable.append({
            "position_id": pos["position_id"],
            "market": pos["question"][:50],
            "side": side,
            "won": won,
            "payout_share": f"{nums[SIDE_TO_OUTCOME_INDEX[side]]}/{denom}",
            "estimated_payout": round(pos["entry_amount"] * nums[SIDE_TO_OUTCOME_INDEX[side]] / denom, 2) if won else 0.0,
        })

    if args.json:
        print(json.dumps(redeemable, indent=2))
        return 0

    if not redeemable:
        print("No resolved positions awaiting redemption.")
        return 0

    print(f"{'ID':<10} {'Side':<4} {'Won':<5} {'Share':>10} {'Est. payout':>12}  Market")
    print("-" * 90)
    for r in redeemable:
        won_str = "yes" if r["won"] else "no"
        print(f"{r['position_id'][:8]:<10} {r['side']:<4} {won_str:<5} {r['payout_share']:>10} ${r['estimated_payout']:>10.2f}  {r['market']}")
    print(f"\n{len(redeemable)} redeemable. Run `polyclaw redeem <id>` or `polyclaw redeem all`.")
    return 0


def _redeem_one(w3: Web3, wallet: WalletManager, condition_id: str, side: str) -> str:
    """Send the redeemPositions tx for a single position. Returns tx hash."""
    ctf = _ctf(w3)
    address = Web3.to_checksum_address(wallet.address)
    account = w3.eth.account.from_key(wallet.get_unlocked_key())
    tx = ctf.functions.redeemPositions(
        Web3.to_checksum_address(CONTRACTS["PUSD"]),
        bytes(32),  # parentCollectionId
        _condition_bytes(condition_id),
        [SIDE_TO_INDEX_SET[side]],
    ).build_transaction({
        "from": address,
        "nonce": w3.eth.get_transaction_count(address),
        "gas": 250_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": POLYGON_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  Redeem TX submitted: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise ValueError(f"Redeem reverted: {tx_hash.hex()}")
    print(f"  Redeem confirmed in block {receipt['blockNumber']}")
    return tx_hash.hex()


async def _redeem_position(pos: dict, w3: Web3, wallet: WalletManager, gamma: GammaClient, storage: PositionStorage) -> bool:
    """Redeem one position. Returns True on success."""
    pid = pos["position_id"][:8]
    if pos.get("redeem_tx"):
        print(f"[{pid}] already redeemed: {pos['redeem_tx']}")
        return False
    cid = await _ensure_condition_id(pos, gamma, storage)
    if not cid:
        print(f"[{pid}] no condition_id available")
        return False
    try:
        resolved, nums, denom = _resolution_state(w3, cid)
    except Exception as e:
        print(f"[{pid}] CTF query failed: {e}")
        return False
    if not resolved:
        print(f"[{pid}] market not resolved yet")
        return False
    side = pos["position"]
    won = nums[SIDE_TO_OUTCOME_INDEX[side]] > 0
    if not won:
        print(f"[{pid}] losing side ({side}) — skip; payout vector {nums}/{denom}")
        return False
    print(f"[{pid}] redeeming {side} on '{pos['question'][:60]}'")
    try:
        tx = _redeem_one(w3, wallet, cid, side)
    except Exception as e:
        print(f"[{pid}] redeem failed: {e}")
        return False
    storage.mark_redeemed(pos["position_id"], tx)
    print(f"[{pid}] done.")
    return True


async def cmd_claim(args):
    """Redeem a single position by id prefix."""
    storage = PositionStorage()
    gamma = GammaClient()
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print("Error: POLYCLAW_PRIVATE_KEY not set")
        return 1
    if not wallet.rpc_url:
        print("Error: CHAINSTACK_NODE not set")
        return 1
    w3 = Web3(Web3.HTTPProvider(wallet.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))

    positions = storage.load_all()
    matches = [p for p in positions if p["position_id"].startswith(args.position_id)]
    if not matches:
        print(f"Position not found: {args.position_id}")
        return 1
    if len(matches) > 1:
        print("Multiple matches, be more specific:")
        for p in matches:
            print(f"  {p['position_id'][:12]} — {p['question'][:40]}")
        return 1

    ok = await _redeem_position(matches[0], w3, wallet, gamma, storage)
    return 0 if ok else 1


async def cmd_all(args):
    """Sweep every redeemable position."""
    storage = PositionStorage()
    gamma = GammaClient()
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print("Error: POLYCLAW_PRIVATE_KEY not set")
        return 1
    if not wallet.rpc_url:
        print("Error: CHAINSTACK_NODE not set")
        return 1
    w3 = Web3(Web3.HTTPProvider(wallet.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))

    candidates = [
        p for p in storage.load_all()
        if p.get("status") != "resolved" and not p.get("redeem_tx")
    ]
    if not candidates:
        print("Nothing to redeem.")
        return 0

    redeemed = 0
    for pos in candidates:
        if await _redeem_position(pos, w3, wallet, gamma, storage):
            redeemed += 1
    print(f"\nRedeemed {redeemed} of {len(candidates)} candidates.")
    return 0


def main():
    # Convenience: `polyclaw redeem <id>` (bare positional) → claim that id
    argv = sys.argv[1:]
    if argv and argv[0] not in ("list", "all", "claim", "-h", "--help", "--json"):
        argv = ["claim", *argv]

    parser = argparse.ArgumentParser(description="Redeem resolved Polymarket positions")
    parser.add_argument("--json", action="store_true", help="JSON output (list only)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="Show resolved/redeemable positions")
    sub.add_parser("all", help="Redeem every redeemable position")
    claim = sub.add_parser("claim", help="Redeem one position by id prefix")
    claim.add_argument("position_id")

    args = parser.parse_args(argv)

    if args.command == "list":
        return asyncio.run(cmd_list(args))
    if args.command == "all":
        return asyncio.run(cmd_all(args))
    if args.command == "claim":
        return asyncio.run(cmd_claim(args))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
