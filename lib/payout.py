"""Payout of a resolved market, read on-chain.

Gamma's `closed` / outcome prices lag on-chain resolution, and Polymarket's
auto-redeem operator pays resolved positions out of deposit wallets within
about a minute (seen 2026-11-23), so resolution is read from the contracts:
the CTF for v1 markets, the PositionManager for v2 (getPayout reverts until
the market resolves).
"""

from typing import Optional

from web3 import Web3
from web3.exceptions import ContractLogicError

from lib.contracts import CONTRACTS, CTF_ABI, POSITION_MANAGER_ABI

# Polymarket binary markets: outcome index 0 = YES, 1 = NO.
SIDE_INDEX = {"YES": 0, "NO": 1}
ONE_SHARE = 1_000_000  # 6 decimals


def payout_rates(w3: Web3, position: dict) -> Optional[tuple[float, float]]:
    """(your side, other side) pUSD per share once resolved; None before."""
    if position.get("protocol") == "v2":
        pm = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["POSITION_MANAGER"]),
            abi=POSITION_MANAGER_ABI,
        )

        def rate(token_id: str) -> Optional[float]:
            try:
                return pm.functions.getPayout(int(token_id), ONE_SHARE).call() / ONE_SHARE
            except ContractLogicError:
                return None

        mine = rate(position["token_id"])
        if mine is None:
            return None
        other_id = position.get("unwanted_token_id")
        other = rate(other_id) if other_id else 1.0 - mine
        return None if other is None else (mine, other)

    ctf = w3.eth.contract(address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI)
    cid = bytes.fromhex(position["condition_id"].removeprefix("0x"))
    denominator = ctf.functions.payoutDenominator(cid).call()
    if not denominator:
        return None
    numerators = [ctf.functions.payoutNumerators(cid, i).call() for i in range(2)]
    i = SIDE_INDEX[position["position"]]
    return numerators[i] / denominator, numerators[1 - i] / denominator
