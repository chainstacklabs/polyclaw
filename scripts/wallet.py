#!/usr/bin/env python3
"""Wallet commands: status, setup (deposit wallet + approvals), deposit.

  status            Addresses, balances, approval state (read-only)
  setup             Deploy the deposit wallet and set trading approvals (gasless)
  approve           Alias for setup
  deposit <amount>  Move pUSD from the signing wallet into the deposit wallet
                    (one ERC-20 transfer from the signing wallet; needs a little POL)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from web3 import Web3  # noqa: E402

from lib.contracts import CONTRACTS, ERC20_ABI, POLYGON_CHAIN_ID  # noqa: E402
from lib.trading import ConfigError, create_client, private_key  # noqa: E402
from lib.wallet_manager import WalletManager  # noqa: E402


def cmd_status(args) -> int:
    manager = WalletManager()
    if not manager.is_configured:
        print("No wallet configured. Set POLYCLAW_PRIVATE_KEY.", file=sys.stderr)
        return 1

    result = {
        "signer": manager.address,
        "deposit_wallet": manager.deposit_wallet,
        "approvals_set": None,
        "balances": None,
    }
    try:
        b = manager.get_balances()
        result["balances"] = {
            "deposit_wallet_pUSD": None if b.deposit_pusd is None else round(b.deposit_pusd, 6),
            "signer_POL": round(b.signer_pol, 6),
            "signer_pUSD": round(b.signer_pusd, 6),
            "signer_USDC.e": round(b.signer_usdc_e, 6),
        }
        result["approvals_set"] = manager.check_approvals()
    except Exception as e:
        result["balances"] = f"Unable to fetch: {e}"

    print(json.dumps(result, indent=2))
    if args.json:
        return 0

    if not manager.deposit_wallet:
        print("\nNo deposit wallet yet. Run `polyclaw wallet setup` (free, gasless).")
    elif result["approvals_set"] is False:
        print("\nApprovals missing. Run `polyclaw wallet setup`.")
    bal = result["balances"] if isinstance(result["balances"], dict) else {}
    if bal.get("signer_pUSD", 0) > 0:
        print(
            "\npUSD on the signing wallet is not buying power. Move it with "
            "`polyclaw wallet deposit <amount>`."
        )
    return 0


def cmd_setup(args) -> int:
    try:
        client = create_client()
    except ConfigError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: could not set up the deposit wallet: {e}", file=sys.stderr)
        return 1

    print(f"Deposit wallet: {client.wallet}")
    try:
        client.setup_trading_approvals()
        print("Trading approvals set (or already in place).")
    except Exception as e:
        # The relayer sometimes rejects the approval batch; the SDK also
        # re-checks allowances on split/merge/orders.
        print(f"Warning: approval setup failed: {e}", file=sys.stderr)
    print("Fund it by sending pUSD to the deposit wallet address above.")
    return 0


def cmd_deposit(args) -> int:
    manager = WalletManager()
    if not manager.is_configured:
        print("No wallet configured. Set POLYCLAW_PRIVATE_KEY.", file=sys.stderr)
        return 1
    if args.amount <= 0:
        print("Refused: amount must be greater than 0", file=sys.stderr)
        return 1
    deposit = manager.deposit_wallet
    if not deposit:
        print("Refused: no deposit wallet yet. Run `polyclaw wallet setup` first.", file=sys.stderr)
        return 1

    try:
        w3 = manager.web3()
        b = manager.get_balances()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if b.signer_pusd < args.amount:
        print(
            f"Refused: signing wallet holds {b.signer_pusd:.6f} pUSD, "
            f"less than {args.amount}",
            file=sys.stderr,
        )
        return 1

    print(f"Transfer {args.amount} pUSD: {manager.address} -> {deposit} (deposit wallet)")
    if not args.yes:
        print("PREVIEW ONLY - re-run with --yes to send. Needs a little POL for gas.")
        return 0

    try:
        signer = Web3.to_checksum_address(manager.address)
        pusd = w3.eth.contract(address=Web3.to_checksum_address(CONTRACTS["PUSD"]), abi=ERC20_ABI)
        tx = pusd.functions.transfer(
            Web3.to_checksum_address(deposit), int(round(args.amount * 1e6))
        ).build_transaction(
            {
                "from": signer,
                "nonce": w3.eth.get_transaction_count(signer),
                "chainId": POLYGON_CHAIN_ID,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, private_key())
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    except Exception as e:
        print(f"Error: transfer failed: {e}", file=sys.stderr)
        return 1
    if receipt["status"] != 1:
        print(f"Error: transfer reverted: {tx_hash.hex()}", file=sys.stderr)
        return 1
    print(f"Done: {tx_hash.hex()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Wallet management")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status", help="Show addresses, balances, approvals")
    status.add_argument("--json", action="store_true", help="JSON only, no hints")
    sub.add_parser("setup", help="Deploy deposit wallet + set approvals (gasless)")
    sub.add_parser("approve", help="Alias for setup")
    dep = sub.add_parser("deposit", help="Move pUSD from signing wallet to deposit wallet")
    dep.add_argument("amount", type=float, help="pUSD amount")
    dep.add_argument("--yes", action="store_true", help="Send (default: preview only)")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status(args)
    if args.command in ("setup", "approve"):
        return cmd_setup(args)
    if args.command == "deposit":
        return cmd_deposit(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
