#!/usr/bin/env python3
"""Trade execution - split + CLOB sell."""

import sys
import json
import time
import uuid
import asyncio
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from web3 import Web3

from lib.wallet_manager import WalletManager
from lib.gamma_client import GammaClient, Market
from lib.clob_client import ClobClientWrapper
from lib.contracts import CONTRACTS, CTF_ABI, POLYGON_CHAIN_ID
from lib.position_storage import PositionStorage, PositionEntry


@dataclass
class TradeResult:
    """Result of a trade execution."""

    success: bool
    market_id: str
    position: str
    amount: float
    split_tx: Optional[str]
    clob_order_id: Optional[str]
    clob_filled: bool
    error: Optional[str] = None
    question: str = ""
    wanted_token_id: str = ""
    entry_price: float = 0.0


class TradeExecutor:
    """Executes on-chain trades via split + CLOB sell."""

    def __init__(self, wallet: WalletManager):
        self.wallet = wallet
        self._gamma = GammaClient()

    def _get_web3(self) -> Web3:
        """Get Web3 instance."""
        return Web3(
            Web3.HTTPProvider(
                self.wallet.rpc_url,
                request_kwargs={"timeout": 60, "proxies": {}}  # Bypass HTTPS_PROXY
            )
        )

    def _split_position(
        self,
        condition_id: str,
        amount_usd: float,
    ) -> str:
        """Split USDC into YES + NO tokens. Returns tx hash."""
        w3 = self._get_web3()
        address = Web3.to_checksum_address(self.wallet.address)
        account = w3.eth.account.from_key(self.wallet.get_unlocked_key())

        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        amount_wei = int(amount_usd * 1e6)
        condition_bytes = bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        )

        tx = ctf.functions.splitPosition(
            Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            bytes(32),  # parentCollectionId
            condition_bytes,
            [1, 2],  # partition for YES, NO
            amount_wei,
        ).build_transaction(
            {
                "from": address,
                "nonce": w3.eth.get_transaction_count(address),
                "gas": 300000,
                "gasPrice": w3.eth.gas_price,
                "chainId": POLYGON_CHAIN_ID,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"Split TX submitted: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise ValueError(f"Split failed: {tx_hash.hex()}")

        print(f"Split confirmed in block {receipt['blockNumber']}")
        return tx_hash.hex()

    async def buy_position(
        self,
        market_id: str,
        position: str,  # "YES" or "NO"
        amount: float,
        skip_clob_sell: bool = False,
    ) -> TradeResult:
        """Buy a position on a market."""
        position = position.upper()
        if position not in ["YES", "NO"]:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error="Position must be YES or NO",
            )

        # Check wallet
        if not self.wallet.is_unlocked:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error="Wallet not unlocked",
            )

        # Check balance
        balances = self.wallet.get_balances()
        if balances.usdc_e < amount:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error=f"Insufficient USDC.e: have {balances.usdc_e:.2f}, need {amount:.2f}",
            )

        # Get market info
        try:
            market = await self._gamma.get_market(market_id)
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error=f"Failed to fetch market: {e}",
            )

        # Determine tokens and prices
        wanted_token = market.yes_token_id if position == "YES" else market.no_token_id
        unwanted_token = market.no_token_id if position == "YES" else market.yes_token_id
        wanted_price = market.yes_price if position == "YES" else market.no_price
        unwanted_price = market.no_price if position == "YES" else market.yes_price

        print(f"Market: {market.question}")
        print(f"Buying: {position} @ {wanted_price:.2f}")
        print(f"Will sell: {'NO' if position == 'YES' else 'YES'} @ ~{unwanted_price:.2f}")

        # Execute split
        try:
            split_tx = self._split_position(market.condition_id, amount)
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error=f"Split failed: {e}",
            )

        time.sleep(2)  # Wait for chain confirmation

        # Sell unwanted side via CLOB
        clob_order_id = None
        clob_filled = False
        clob_error = None

        if not skip_clob_sell and unwanted_token:
            print("Selling unwanted tokens via CLOB...")
            try:
                clob = ClobClientWrapper(
                    self.wallet.get_unlocked_key(),
                    self.wallet.address,
                )
                clob_order_id, clob_filled, clob_error = clob.sell_fok(
                    unwanted_token,
                    amount,  # Same number of tokens as USDC spent
                    unwanted_price,
                )
                if clob_filled:
                    print(f"CLOB sell filled: {clob_order_id}")
                else:
                    print(f"CLOB sell failed: {clob_error}")
            except Exception as e:
                clob_error = str(e)
                print(f"CLOB error: {clob_error}")

        return TradeResult(
            success=True,  # Split succeeded
            market_id=market_id,
            position=position,
            amount=amount,
            split_tx=split_tx,
            clob_order_id=clob_order_id,
            clob_filled=clob_filled,
            error=clob_error,
            question=market.question,
            wanted_token_id=wanted_token,
            entry_price=wanted_price,
        )


async def cmd_buy(args):
    """Execute buy command."""
    wallet = WalletManager()

    if not wallet.is_unlocked:
        print("Error: No wallet configured")
        print("Set POLYCLAW_PRIVATE_KEY environment variable.")
        return 1

    try:
        executor = TradeExecutor(wallet)
        result = await executor.buy_position(
            args.market_id,
            args.position,
            args.amount,
            skip_clob_sell=args.skip_sell,
        )

        print("\n" + "=" * 50)
        if result.success:
            print("Trade executed successfully!")
            print(f"  Market: {result.question[:50]}...")
            print(f"  Position: {result.position}")
            print(f"  Amount: ${result.amount:.2f}")
            print(f"  Split TX: {result.split_tx}")
            if result.clob_filled:
                print(f"  CLOB Order: {result.clob_order_id} (FILLED)")
            elif result.clob_order_id:
                print(f"  CLOB Order: {result.clob_order_id} (pending)")
            elif args.skip_sell:
                print(f"  CLOB: Skipped (--skip-sell)")
                print(f"  Note: You have both YES and NO tokens")
            else:
                print(f"  CLOB: Failed - {result.error}")
                unwanted = "NO" if result.position == "YES" else "YES"
                print(f"  Note: You have {result.amount:.0f} {unwanted} tokens to sell manually")

            # Record position
            storage = PositionStorage()
            position_entry = PositionEntry(
                position_id=str(uuid.uuid4()),
                market_id=result.market_id,
                question=result.question,
                position=result.position,
                token_id=result.wanted_token_id,
                entry_time=datetime.now(timezone.utc).isoformat(),
                entry_amount=result.amount,
                entry_price=result.entry_price,
                split_tx=result.split_tx,
                clob_order_id=result.clob_order_id,
                clob_filled=result.clob_filled,
            )
            storage.add(position_entry)
            print(f"  Position ID: {position_entry.position_id[:12]}...")
        else:
            print(f"Trade failed: {result.error}")
            return 1

        # Output JSON if requested
        if args.json:
            print("\nJSON Result:")
            print(json.dumps(asdict(result), indent=2))

        return 0

    finally:
        wallet.lock()


def main():
    parser = argparse.ArgumentParser(description="Trade execution")
    parser.add_argument("--json", action="store_true", help="JSON output")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Buy
    buy_parser = subparsers.add_parser("buy", help="Buy a position")
    buy_parser.add_argument("market_id", help="Market ID")
    buy_parser.add_argument("position", choices=["YES", "NO", "yes", "no"], help="YES or NO")
    buy_parser.add_argument("amount", type=float, help="Amount in USD")
    buy_parser.add_argument(
        "--skip-sell", action="store_true",
        help="Skip selling unwanted side (keep both YES and NO)"
    )

    args = parser.parse_args()

    if args.command == "buy":
        return asyncio.run(cmd_buy(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
