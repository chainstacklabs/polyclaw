#!/usr/bin/env python3
"""Wallet management commands."""

import sys
import json
import argparse
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.wallet_manager import WalletManager


def cmd_status(args):
    """Show wallet status."""
    manager = WalletManager()

    if not manager.address:
        print("No wallet configured.")
        print("Set POLYCLAW_PRIVATE_KEY environment variable.")
        return 1

    result = {
        "address": manager.address,
        "unlocked": manager.is_unlocked,
    }

    try:
        result["approvals_set"] = manager.check_approvals()
        balances = manager.get_balances()
        result["balances"] = {
            "POL": f"{balances.pol:.6f}",
            "USDC.e": f"{balances.usdc_e:.6f}",
        }
    except Exception as e:
        result["approvals_set"] = "unknown"
        result["balances"] = f"Unable to fetch: {e}"

    print(json.dumps(result, indent=2))
    return 0


def cmd_approve(args):
    """Set Polymarket contract approvals."""
    manager = WalletManager()

    if not manager.address:
        print("Error: No wallet configured")
        print("Set POLYCLAW_PRIVATE_KEY environment variable.")
        return 1

    print("Setting contract approvals...")
    print("This will submit 6 transactions to Polygon.")

    try:
        tx_hashes = manager.set_approvals()
        print("Approvals set successfully!")
        for i, tx in enumerate(tx_hashes, 1):
            print(f"  {i}. {tx}")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Wallet management")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Status
    subparsers.add_parser("status", help="Show wallet status")

    # Approve
    subparsers.add_parser("approve", help="Set Polymarket approvals")

    args = parser.parse_args()

    if args.command == "status":
        return cmd_status(args)
    elif args.command == "approve":
        return cmd_approve(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
