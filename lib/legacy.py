"""Find and clean up outcome tokens left on the bare signing wallet (EOA).

polyclaw <= 0.1.0 traded from the signing key directly and, after the
2026-04-28 V2 cutover, split with **pUSD** on the CTF. Live markets' tokens
are USDC.e-backed, so those splits minted token IDs that match no order book
(the follow-up sell then failed). The pUSD is not lost: merging the YES+NO
pair back on the CTF returns it, and after resolution `redeemPositions`
pays out the winning side.

Positions from before the cutover are USDC.e-backed and normal.

Everything here is plain CTF calls from the signing wallet (it pays POL gas);
the deposit wallet is not involved.
"""

from dataclasses import dataclass, field
from typing import Optional

from web3 import Web3

from lib.contracts import CONTRACTS, CTF_ABI, POLYGON_CHAIN_ID

ZERO32 = bytes(32)
# Binary markets: index set 1 = outcome 0 (YES), 2 = outcome 1 (NO).
INDEX_SETS = (1, 2)
COLLATERALS = {"pUSD": CONTRACTS["PUSD"], "USDC.e": CONTRACTS["USDC_E"]}


def _cid_bytes(condition_id: str) -> bytes:
    return bytes.fromhex(condition_id.removeprefix("0x"))


@dataclass
class Holding:
    """YES/NO balances of one (condition, collateral) pair on the signing wallet."""

    condition_id: str
    collateral_name: str
    collateral: str
    yes_id: int
    no_id: int
    yes: int  # base units (6 decimals)
    no: int
    resolved: bool
    payouts: list[int] = field(default_factory=list)  # numerators, len 2
    denominator: int = 0

    @property
    def mergeable(self) -> int:
        return min(self.yes, self.no)

    @property
    def redeem_payout(self) -> int:
        """Collateral (base units) redeemPositions would pay out now."""
        if not self.resolved or not self.denominator:
            return 0
        return (self.yes * self.payouts[0] + self.no * self.payouts[1]) // self.denominator


@dataclass
class Action:
    kind: str  # "redeem" | "merge" | "none"
    holding: Holding
    amount: int = 0  # merge amount, or expected redeem payout (base units)
    note: Optional[str] = None


def plan_action(h: Holding) -> Action:
    """Decide what to do with one holding."""
    if h.yes == 0 and h.no == 0:
        return Action("none", h)
    if h.resolved:
        payout = h.redeem_payout
        if payout == 0:
            return Action("none", h, note="resolved; these tokens pay nothing")
        return Action("redeem", h, amount=payout)
    if h.mergeable > 0:
        leftover = max(h.yes, h.no) - h.mergeable
        note = None
        if leftover:
            side = "YES" if h.yes > h.no else "NO"
            note = (
                f"{leftover / 1e6:g} {side} stay after the merge; "
                "they can be redeemed once the market resolves"
            )
        return Action("merge", h, amount=h.mergeable, note=note)
    side = "YES" if h.yes else "NO"
    return Action(
        "none", h, note=f"only {side} held; redeemable after the market resolves"
    )


class LegacyScanner:
    """Read balances/resolution and send merge/redeem txs from the signing wallet."""

    def __init__(self, w3: Web3, owner: str):
        self.w3 = w3
        self.owner = Web3.to_checksum_address(owner)
        self.ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_ABI
        )

    def position_id(self, collateral: str, condition_id: str, index_set: int) -> int:
        collection = self.ctf.functions.getCollectionId(
            ZERO32, _cid_bytes(condition_id), index_set
        ).call()
        return self.ctf.functions.getPositionId(
            Web3.to_checksum_address(collateral), collection
        ).call()

    def holdings(self, condition_id: str) -> list[Holding]:
        cid = _cid_bytes(condition_id)
        denom = self.ctf.functions.payoutDenominator(cid).call()
        payouts = (
            [self.ctf.functions.payoutNumerators(cid, i).call() for i in range(2)]
            if denom
            else []
        )
        out = []
        for name, token in COLLATERALS.items():
            yes_id, no_id = (self.position_id(token, condition_id, i) for i in INDEX_SETS)
            out.append(
                Holding(
                    condition_id=condition_id,
                    collateral_name=name,
                    collateral=token,
                    yes_id=yes_id,
                    no_id=no_id,
                    yes=self.ctf.functions.balanceOf(self.owner, yes_id).call(),
                    no=self.ctf.functions.balanceOf(self.owner, no_id).call(),
                    resolved=denom > 0,
                    payouts=payouts,
                    denominator=denom,
                )
            )
        return out

    def execute(self, action: Action, private_key: str) -> str:
        h = action.holding
        args = (Web3.to_checksum_address(h.collateral), ZERO32, _cid_bytes(h.condition_id))
        if action.kind == "merge":
            fn = self.ctf.functions.mergePositions(*args, list(INDEX_SETS), action.amount)
        elif action.kind == "redeem":
            fn = self.ctf.functions.redeemPositions(*args, list(INDEX_SETS))
        else:
            raise ValueError(f"Nothing to execute for {action.kind!r}")
        tx = fn.build_transaction(
            {
                "from": self.owner,
                "nonce": self.w3.eth.get_transaction_count(self.owner),
                "chainId": POLYGON_CHAIN_ID,
            }
        )
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return tx_hash.hex()
