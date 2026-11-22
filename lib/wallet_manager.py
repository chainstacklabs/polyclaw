"""Wallet reads: signing key + deposit wallet balances and approval state.

Two addresses matter:
- signer (EOA from POLYCLAW_PRIVATE_KEY): owns the deposit wallet, signs.
  Holds only POL for `polyclaw recover`, plus anything older polyclaw
  versions left on it.
- deposit wallet: where pUSD buying power and outcome tokens live.

Writes (setup, approvals, trades) go through the SDK in ``lib.trading``.
"""

import os
from dataclasses import dataclass
from typing import Optional

from web3 import Web3

from lib.contracts import CONTRACTS, CTF_ABI, ERC20_ABI
from lib.trading import cached_deposit_wallet, private_key, signer_address


@dataclass
class WalletBalances:
    """Balances in human units."""

    signer_pol: float
    signer_pusd: float
    signer_usdc_e: float
    deposit_pusd: Optional[float]  # None until the deposit wallet is known


class WalletManager:
    """Reads for the configured signer and its deposit wallet."""

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url or os.environ.get("CHAINSTACK_NODE", "")
        try:
            private_key()
            self._address: Optional[str] = signer_address()
        except Exception:
            self._address = None

    @property
    def is_configured(self) -> bool:
        return self._address is not None

    @property
    def address(self) -> Optional[str]:
        """Signing wallet (EOA) address."""
        return self._address

    @property
    def deposit_wallet(self) -> Optional[str]:
        """Deposit wallet address, known after `polyclaw wallet setup` or a trade."""
        return cached_deposit_wallet(self._address) if self._address else None

    def web3(self) -> Web3:
        if not self.rpc_url:
            raise ValueError("CHAINSTACK_NODE environment variable not set")
        return Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))

    def _erc20_balance(self, w3: Web3, token: str, owner: str) -> float:
        contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        return contract.functions.balanceOf(Web3.to_checksum_address(owner)).call() / 1e6

    def get_balances(self) -> WalletBalances:
        if not self._address:
            raise ValueError("No wallet configured")
        w3 = self.web3()
        signer = Web3.to_checksum_address(self._address)
        deposit = self.deposit_wallet
        return WalletBalances(
            signer_pol=float(w3.from_wei(w3.eth.get_balance(signer), "ether")),
            signer_pusd=self._erc20_balance(w3, CONTRACTS["PUSD"], signer),
            signer_usdc_e=self._erc20_balance(w3, CONTRACTS["USDC_E"], signer),
            deposit_pusd=(
                self._erc20_balance(w3, CONTRACTS["PUSD"], deposit) if deposit else None
            ),
        )

    def token_balance(self, owner: str, token_id: str) -> float:
        """ERC-1155 outcome-token balance (shares) on the CTF."""
        w3 = self.web3()
        ctf = w3.eth.contract(address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI)
        return ctf.functions.balanceOf(Web3.to_checksum_address(owner), int(token_id)).call() / 1e6

    def check_approvals(self) -> Optional[bool]:
        """True if the deposit wallet has every approval the SDK trades with.

        Asks the SDK (indexed Data API state). None if unknown (no deposit
        wallet yet, or the lookup failed).
        """
        deposit = self.deposit_wallet
        if not deposit:
            return None
        from polymarket import PublicClient

        try:
            with PublicClient() as client:
                return client.get_trading_approvals_state(wallet=deposit).is_fully_approved
        except Exception:
            return None
