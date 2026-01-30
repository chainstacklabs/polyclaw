"""Wallet management - env var based."""

import os
from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3

from lib.contracts import CONTRACTS, ERC20_ABI, CTF_ABI, POLYGON_CHAIN_ID


@dataclass
class WalletBalances:
    """Wallet balances."""
    pol: float
    usdc_e: float


class WalletManager:
    """Manages wallet from POLYCLAW_PRIVATE_KEY env var."""

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url or os.environ.get("CHAINSTACK_NODE", "")
        self._private_key: Optional[str] = None
        self._address: Optional[str] = None
        self._load_from_env()

    def _load_from_env(self) -> None:
        """Load private key from POLYCLAW_PRIVATE_KEY env var."""
        private_key = os.environ.get("POLYCLAW_PRIVATE_KEY")
        if private_key:
            if not private_key.startswith("0x"):
                private_key = "0x" + private_key
            account = Account.from_key(private_key)
            self._private_key = private_key
            self._address = account.address

    @property
    def is_unlocked(self) -> bool:
        """Check if wallet is available."""
        return self._private_key is not None

    @property
    def address(self) -> Optional[str]:
        """Get wallet address."""
        return self._address

    def _get_web3(self) -> Web3:
        """Get Web3 instance."""
        if not self.rpc_url:
            raise ValueError("CHAINSTACK_NODE environment variable not set")
        return Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60, "proxies": {}}))

    def get_unlocked_key(self) -> str:
        """Get the private key for signing."""
        if not self._private_key:
            raise ValueError("No wallet configured. Set POLYCLAW_PRIVATE_KEY env var.")
        return self._private_key

    def lock(self) -> None:
        """Clear private key from memory (no-op for env var mode)."""
        pass  # Key stays in env var anyway

    def get_balances(self) -> WalletBalances:
        """Get POL and USDC.e balances."""
        if not self._address:
            raise ValueError("No wallet configured")

        w3 = self._get_web3()
        checksum = Web3.to_checksum_address(self._address)

        pol = float(w3.from_wei(w3.eth.get_balance(checksum), "ether"))

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        usdc_balance = usdc.functions.balanceOf(checksum).call() / 1e6

        return WalletBalances(pol=pol, usdc_e=usdc_balance)

    def check_approvals(self) -> bool:
        """Check if all Polymarket approvals are set."""
        if not self._address:
            return False

        w3 = self._get_web3()
        checksum = Web3.to_checksum_address(self._address)

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        # Check USDC approvals
        for contract in ["CTF", "CTF_EXCHANGE", "NEG_RISK_CTF_EXCHANGE"]:
            allowance = usdc.functions.allowance(checksum, CONTRACTS[contract]).call()
            if allowance == 0:
                return False

        # Check CTF approvals
        for contract in ["CTF_EXCHANGE", "NEG_RISK_CTF_EXCHANGE", "NEG_RISK_ADAPTER"]:
            approved = ctf.functions.isApprovedForAll(
                checksum, CONTRACTS[contract]
            ).call()
            if not approved:
                return False

        return True

    def set_approvals(self) -> list[str]:
        """Set all Polymarket contract approvals. Returns tx hashes."""
        if not self._private_key:
            raise ValueError("No wallet configured")

        w3 = self._get_web3()
        address = Web3.to_checksum_address(self._address)
        account = w3.eth.account.from_key(self._private_key)

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        MAX_UINT256 = 2**256 - 1
        tx_hashes = []

        approvals = [
            (usdc, "approve", CONTRACTS["CTF"], MAX_UINT256),
            (usdc, "approve", CONTRACTS["CTF_EXCHANGE"], MAX_UINT256),
            (usdc, "approve", CONTRACTS["NEG_RISK_CTF_EXCHANGE"], MAX_UINT256),
            (ctf, "setApprovalForAll", CONTRACTS["CTF_EXCHANGE"], True),
            (ctf, "setApprovalForAll", CONTRACTS["NEG_RISK_CTF_EXCHANGE"], True),
            (ctf, "setApprovalForAll", CONTRACTS["NEG_RISK_ADAPTER"], True),
        ]

        for contract, method, spender, value in approvals:
            fn = getattr(contract.functions, method)
            tx = fn(Web3.to_checksum_address(spender), value).build_transaction(
                {
                    "from": address,
                    "nonce": w3.eth.get_transaction_count(address),
                    "gas": 100000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": POLYGON_CHAIN_ID,
                }
            )

            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] != 1:
                raise ValueError(f"Approval failed: {tx_hash.hex()}")

            tx_hashes.append(tx_hash.hex())

        return tx_hashes
