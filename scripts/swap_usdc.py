#!/usr/bin/env python3
"""Swap between native USDC and USDC.e on Polygon via QuickSwap V2.

Usage:
    polyclaw swap balances                    # Show USDC and USDC.e balances
    polyclaw swap to-bridged                  # Swap all USDC -> USDC.e
    polyclaw swap to-bridged --amount 5       # Swap $5 worth
    polyclaw swap to-native                   # Swap all USDC.e -> USDC
    polyclaw swap to-native --dry-run         # Preview without executing
"""

import sys
import json
import time
import argparse
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from web3 import Web3

from lib.wallet_manager import WalletManager
from lib.contracts import CONTRACTS, ERC20_ABI, QUICKSWAP_V2_ROUTER_ABI, POLYGON_CHAIN_ID

DECIMALS = 6


def ensure_approval(w3, account, token_addr: str, spender: str, amount_wei: int) -> str | None:
    """Approve spender if needed. Returns tx hash or None."""
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
    address = Web3.to_checksum_address(account.address)
    spender_cs = Web3.to_checksum_address(spender)

    if token.functions.allowance(address, spender_cs).call() >= amount_wei:
        return None

    tx = token.functions.approve(spender_cs, amount_wei).build_transaction({
        "from": address,
        "nonce": w3.eth.get_transaction_count(address),
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": POLYGON_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise ValueError(f"Approval failed: {tx_hash.hex()}")
    return tx_hash.hex()


def cmd_balances(args):
    """Show USDC and USDC.e balances."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    balances = wallet.get_balances()
    result = {
        "address": wallet.address,
        "POL": f"{balances.pol:.6f}",
        "USDC": f"{balances.usdc:.6f}",
        "USDC.e": f"{balances.usdc_e:.6f}",
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_swap(args, token_in: str, token_out: str, direction: str):
    """Execute swap via QuickSwap V2."""
    wallet = WalletManager()
    if not wallet.is_unlocked:
        print(json.dumps({"error": "No wallet configured. Set POLYCLAW_PRIVATE_KEY."}))
        return 1

    w3 = Web3(Web3.HTTPProvider(wallet.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))
    address = Web3.to_checksum_address(wallet.address)
    account = w3.eth.account.from_key(wallet.get_unlocked_key())

    # Get input token balance
    token = w3.eth.contract(address=Web3.to_checksum_address(token_in), abi=ERC20_ABI)
    available_wei = token.functions.balanceOf(address).call()
    available = available_wei / 10**DECIMALS

    if available < 0.01:
        label = "USDC" if token_in == CONTRACTS["USDC"] else "USDC.e"
        print(json.dumps({"error": f"No {label} to swap (balance: {available:.6f})"}))
        return 1

    if args.amount is not None:
        amount = args.amount
        if amount <= 0:
            print(json.dumps({"error": "Amount must be greater than 0"}))
            return 1
        if amount > available:
            print(json.dumps({"error": f"Requested {amount:.2f} but only have {available:.2f}"}))
            return 1
    else:
        amount = available

    amount_wei = int(round(amount * 10**DECIMALS))
    slippage_pct = 2.0

    path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
    router_addr = Web3.to_checksum_address(CONTRACTS["QUICKSWAP_V2_ROUTER"])
    router = w3.eth.contract(address=router_addr, abi=QUICKSWAP_V2_ROUTER_ABI)

    # Quote
    amounts_out = router.functions.getAmountsOut(amount_wei, path).call()
    quoted_out_wei = amounts_out[1]
    min_out_wei = int(quoted_out_wei * (1 - slippage_pct / 100))
    expected_out = quoted_out_wei / 10**DECIMALS
    fee_pct = round((1 - expected_out / amount) * 100, 2)

    if args.dry_run:
        result = {
            "direction": direction,
            "amount_in": amount,
            "expected_out": expected_out,
            "fee_pct": fee_pct,
            "dry_run": True,
        }
        print(json.dumps(result, indent=2))
        return 0

    # Approve router
    if ensure_approval(w3, account, token_in, CONTRACTS["QUICKSWAP_V2_ROUTER"], amount_wei):
        time.sleep(2)

    # Swap
    deadline = int(time.time()) + 300
    swap_tx = router.functions.swapExactTokensForTokens(
        amount_wei, min_out_wei, path, address, deadline,
    ).build_transaction({
        "from": address,
        "nonce": w3.eth.get_transaction_count(address),
        "gas": 300_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": POLYGON_CHAIN_ID,
    })
    signed = account.sign_transaction(swap_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        print(json.dumps({"error": f"Swap failed: {tx_hash.hex()}"}))
        return 1

    gas_cost = receipt["gasUsed"] * receipt.get("effectiveGasPrice", w3.eth.gas_price)
    gas_cost_pol = float(w3.from_wei(gas_cost, "ether"))

    result = {
        "direction": direction,
        "amount_in": amount,
        "amount_out": expected_out,
        "fee_pct": fee_pct,
        "tx_hash": tx_hash.hex(),
        "gas_cost_pol": gas_cost_pol,
    }
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Swap USDC <-> USDC.e via QuickSwap V2")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("balances", help="Show wallet balances")

    p1 = subparsers.add_parser("to-bridged", help="Swap USDC -> USDC.e")
    p1.add_argument("--amount", type=float, help="Amount in USD (default: all)")
    p1.add_argument("--dry-run", action="store_true", help="Preview only")

    p2 = subparsers.add_parser("to-native", help="Swap USDC.e -> USDC")
    p2.add_argument("--amount", type=float, help="Amount in USD (default: all)")
    p2.add_argument("--dry-run", action="store_true", help="Preview only")

    args = parser.parse_args()

    if args.command == "balances":
        return cmd_balances(args)
    elif args.command == "to-bridged":
        return cmd_swap(args, CONTRACTS["USDC"], CONTRACTS["USDC_E"], "to-bridged")
    elif args.command == "to-native":
        return cmd_swap(args, CONTRACTS["USDC_E"], CONTRACTS["USDC"], "to-native")
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
