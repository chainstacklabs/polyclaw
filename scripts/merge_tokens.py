#!/usr/bin/env python3
"""Merge YES + NO conditional tokens back into USDC.e.

Usage:
    polyclaw merge <condition_id> [amount]

Merges overlapping YES + NO token balances for a given condition ID back into
USDC.e via CTF mergePositions. Routes to NegRiskAdapter for neg-risk markets.
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

import httpx
from web3 import Web3

from lib.wallet_manager import WalletManager
from lib.contracts import CONTRACTS, CTF_ABI, NEG_RISK_ADAPTER_ABI, POLYGON_CHAIN_ID


def get_token_id(condition_id: str, index: int) -> int:
    """Calculate conditional token ID from condition_id and outcome index."""
    condition_bytes = bytes.fromhex(
        condition_id[2:] if condition_id.startswith("0x") else condition_id
    )
    collection_id = Web3.solidity_keccak(
        ["bytes32", "bytes32", "uint256"],
        [bytes(32), condition_bytes, 1 << index],
    )
    return int.from_bytes(collection_id, "big")


async def detect_neg_risk(condition_id: str) -> bool:
    """Try to detect if a market is neg-risk via Gamma API."""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                "https://gamma-api.polymarket.com/markets",
                params={"condition_id": condition_id},
            )
            resp.raise_for_status()
            markets = resp.json()
            if markets:
                return markets[0].get("negRisk", False)
    except Exception:
        pass
    return False


def cmd_merge(args):
    """Execute merge command."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    condition_id = args.condition_id

    # Validate condition_id is valid hex
    hex_str = condition_id[2:] if condition_id.startswith("0x") else condition_id
    try:
        bytes.fromhex(hex_str)
    except ValueError:
        print(json.dumps({"error": "Condition ID not found"}))
        return 1
    if len(hex_str) != 64:
        print(json.dumps({"error": "Condition ID not found"}))
        return 1

    w3 = Web3(Web3.HTTPProvider(wallet.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))
    address = Web3.to_checksum_address(wallet.address)
    account = w3.eth.account.from_key(wallet.get_unlocked_key())

    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACTS["CTF"]),
        abi=CTF_ABI,
    )

    # Get YES and NO token balances
    yes_id = get_token_id(condition_id, 0)  # index 0 = YES (indexSet=1)
    no_id = get_token_id(condition_id, 1)   # index 1 = NO (indexSet=2)

    yes_bal = ctf.functions.balanceOf(address, yes_id).call()
    no_bal = ctf.functions.balanceOf(address, no_id).call()

    print(f"YES tokens: {yes_bal / 1e6:.6f}")
    print(f"NO tokens:  {no_bal / 1e6:.6f}")

    if args.amount is not None:
        if args.amount <= 0:
            print(json.dumps({"error": "Amount must be greater than 0"}))
            return 1
        amount_wei = int(round(args.amount * 1e6))
        if amount_wei > min(yes_bal, no_bal):
            print(json.dumps({
                "error": f"Requested ${args.amount} but max mergeable is ${min(yes_bal, no_bal) / 1e6:.6f}",
            }))
            return 1
    else:
        amount_wei = min(yes_bal, no_bal)

    if amount_wei == 0:
        print(json.dumps({"error": "No tokens to merge"}))
        return 1

    # Detect neg-risk
    neg_risk = asyncio.run(detect_neg_risk(condition_id))
    print(f"Neg-risk: {neg_risk}")

    condition_bytes = bytes.fromhex(
        condition_id[2:] if condition_id.startswith("0x") else condition_id
    )

    # Route to correct contract
    if neg_risk:
        adapter = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["NEG_RISK_ADAPTER"]),
            abi=NEG_RISK_ADAPTER_ABI,
        )
        tx = adapter.functions.mergePositions(
            Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            bytes(32),
            condition_bytes,
            [1, 2],
            amount_wei,
        ).build_transaction({
            "from": address,
            "nonce": w3.eth.get_transaction_count(address, "pending"),
            "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "chainId": POLYGON_CHAIN_ID,
        })
    else:
        tx = ctf.functions.mergePositions(
            Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            bytes(32),
            condition_bytes,
            [1, 2],
            amount_wei,
        ).build_transaction({
            "from": address,
            "nonce": w3.eth.get_transaction_count(address, "pending"),
            "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "chainId": POLYGON_CHAIN_ID,
        })

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"TX sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        print(json.dumps({"error": f"Merge TX failed: {tx_hash.hex()}"}))
        return 1

    gas_cost = receipt["gasUsed"] * receipt.get("effectiveGasPrice", w3.eth.gas_price)
    gas_cost_pol = float(w3.from_wei(gas_cost, "ether"))

    # Check remaining balances
    remaining_yes = ctf.functions.balanceOf(address, yes_id).call() / 1e6
    remaining_no = ctf.functions.balanceOf(address, no_id).call() / 1e6

    result = {
        "condition_id": condition_id,
        "amount_merged": amount_wei / 1e6,
        "tx_hash": tx_hash.hex(),
        "usdc_e_received": amount_wei / 1e6,
        "gas_cost_pol": gas_cost_pol,
        "neg_risk": neg_risk,
        "remaining_yes": remaining_yes,
        "remaining_no": remaining_no,
    }
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Merge YES+NO tokens back to USDC.e")
    parser.add_argument("condition_id", help="CTF condition ID (hex)")
    parser.add_argument("amount", type=float, nargs="?", default=None,
                        help="USD amount to merge (default: all overlapping)")

    args = parser.parse_args()
    return cmd_merge(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
