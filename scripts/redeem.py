#!/usr/bin/env python3
"""Auto-redeem resolved positions for USDC.e.

Usage:
    polyclaw redeem            # Redeem all resolved winning positions
    polyclaw redeem --dry-run  # Preview without executing
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

from web3 import Web3
import httpx

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient
from lib.contracts import CONTRACTS, CTF_ABI, NEG_RISK_ADAPTER_ABI, POLYGON_CHAIN_ID
from lib.subgraph_client import SubgraphClient, SubgraphError
from lib.market_cache import MarketCache, MarketCacheEntry


async def cmd_redeem(args):
    """Find resolved markets and redeem winning positions."""
    gamma = GammaClient()
    cache = MarketCache()

    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    subgraph = SubgraphClient(wallet.address)
    try:
        open_positions = await subgraph.get_open_positions()
    except SubgraphError as e:
        print(json.dumps({"error": f"Subgraph unreachable: {e}"}))
        return 1

    if not open_positions:
        print(json.dumps({"redeemed": [], "resolved_lost": [], "unchanged": 0}))
        return 0

    w3 = Web3(Web3.HTTPProvider(wallet.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))
    address = Web3.to_checksum_address(wallet.address)
    account = w3.eth.account.from_key(wallet.get_unlocked_key())
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI)

    nonce = w3.eth.get_transaction_count(address, "pending")
    redeemed = []
    resolved_lost = []
    unchanged = 0

    # Resolve each position's market via Gamma API (token_id -> market)
    # and group by market_id
    by_market: dict[str, list[tuple]] = {}  # market_id -> [(pos, market)]
    for pos in open_positions:
        try:
            market = await gamma.get_market_by_token(pos.token_id)
        except (httpx.HTTPError, ValueError) as e:
            print(f"Could not resolve market for token {pos.token_id[:12]}: {e}", file=sys.stderr)
            unchanged += 1
            continue

        # Cache the result in MarketCache (keyed by condition_id)
        if cache.get(market.condition_id) is None:
            cache.put(market.condition_id, MarketCacheEntry(
                condition_id=market.condition_id,
                market_id=market.id,
                question=market.question,
                slug=market.slug,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id or "",
                cached_at=datetime.now(timezone.utc).isoformat(),
            ))

        by_market.setdefault(market.id, []).append((pos, market))

    for market_id, entries in by_market.items():
        # All entries share the same market object
        market = entries[0][1]

        if not market.resolved:
            unchanged += len(entries)
            continue

        for pos, market in entries:
            if pos.token_id == market.yes_token_id:
                side = "YES"
            elif market.no_token_id and pos.token_id == market.no_token_id:
                side = "NO"
            else:
                print(
                    f"Token {pos.token_id[:12]} does not match YES/NO tokens for market {market_id}",
                    file=sys.stderr,
                )
                unchanged += 1
                continue
            outcome = (market.outcome or "").upper()
            won = side == outcome
            try:
                token_id = int(pos.token_id)
            except ValueError:
                print(f"Invalid token ID: {pos.token_id[:12]}", file=sys.stderr)
                unchanged += 1
                continue
            balance = ctf.functions.balanceOf(address, token_id).call()

            if not won:
                resolved_lost.append({
                    "token_id": pos.token_id,
                    "market_id": market_id,
                    "question": market.question,
                    "side": side,
                    "outcome": "lost",
                })
                continue

            # Won — redeem
            if args.dry_run:
                redeemed.append({
                    "token_id": pos.token_id,
                    "market_id": market_id,
                    "question": market.question,
                    "side": side,
                    "outcome": "won",
                    "amount_redeemed": balance / 1e6,
                    "tx_hash": "dry-run",
                })
                continue

            condition_bytes = bytes.fromhex(
                market.condition_id[2:] if market.condition_id.startswith("0x") else market.condition_id
            )

            neg_risk = market.neg_risk

            if neg_risk:
                # amounts array: [outcome_0_amount, outcome_1_amount]
                # YES = outcome 0, NO = outcome 1
                if side == "YES":
                    amounts = [balance, 0]
                else:
                    amounts = [0, balance]

                adapter = w3.eth.contract(
                    address=Web3.to_checksum_address(CONTRACTS["NEG_RISK_ADAPTER"]),
                    abi=NEG_RISK_ADAPTER_ABI,
                )
                tx = adapter.functions.redeemPositions(
                    condition_bytes,
                    amounts,
                ).build_transaction({
                    "from": address,
                    "nonce": nonce,
                    "gas": 300000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": POLYGON_CHAIN_ID,
                })
            else:
                tx = ctf.functions.redeemPositions(
                    Web3.to_checksum_address(CONTRACTS["USDC_E"]),
                    bytes(32),
                    condition_bytes,
                    [1, 2],
                ).build_transaction({
                    "from": address,
                    "nonce": nonce,
                    "gas": 300000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": POLYGON_CHAIN_ID,
                })

            nonce += 1
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                redeemed.append({
                    "token_id": pos.token_id,
                    "market_id": market_id,
                    "question": market.question,
                    "side": side,
                    "outcome": "won",
                    "amount_redeemed": balance / 1e6,
                    "tx_hash": tx_hash.hex(),
                })
            else:
                print(f"Redeem TX failed: {tx_hash.hex()}", file=sys.stderr)
                unchanged += 1

    result = {
        "redeemed": redeemed,
        "resolved_lost": resolved_lost,
        "unchanged": unchanged,
    }
    if args.dry_run:
        result["dry_run"] = True

    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Auto-redeem resolved positions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report redeemable positions without executing")

    args = parser.parse_args()
    return asyncio.run(cmd_redeem(args))


if __name__ == "__main__":
    sys.exit(main() or 0)
