"""Protocol V2 markets: token IDs by version, payouts from the PositionManager.

Gamma shapes below are copied from live markets (2026-11-22): market 7256561
(v2, unresolved) and a v1 market.
"""

from unittest.mock import MagicMock

from lib.gamma_client import GammaClient, outcome_token_ids
from redeem import assess_v2

V2 = {
    "id": "7256561",
    "version": "v2",
    "conditionId": "0x01067c7254ed6f3a64d491e4e31a8d9f5e000000000000000000000000000000",
    "positionIds": ["4637728298967661612806540046937", "4637728298967661612806540046938"],
    # Gamma also sends clobTokenIds on v2 markets; it must not be used
    "clobTokenIds": '["1", "2"]',
    "outcomePrices": '["0.5", "0.5"]',
    "orderMinSize": 5,
    "acceptingOrders": True,
}
V1 = {
    "id": "559651",
    "version": "v1",
    "conditionId": "0xabc",
    "clobTokenIds": '["111", "222"]',
    "outcomePrices": '["0.3", "0.7"]',
}


def test_v2_uses_position_ids_even_when_clob_ids_present():
    assert outcome_token_ids(V2) == V2["positionIds"]


def test_v1_uses_clob_token_ids():
    assert outcome_token_ids(V1) == ["111", "222"]


def test_unknown_or_missing_version_gives_no_ids():
    assert outcome_token_ids({**V1, "version": "v3"}) == []
    assert outcome_token_ids({k: v for k, v in V1.items() if k != "version"}) == []


def test_parsed_market_keeps_version_and_ids():
    m = GammaClient()._parse_market(V2)
    assert m.version == "v2"
    assert (m.yes_token_id, m.no_token_id) == tuple(V2["positionIds"])


def _w3(balances: dict[int, int], payouts: dict[int, int] | None):
    """Fake web3 whose PositionManager returns given balances / payouts.

    payouts=None means unresolved: getPayout reverts.
    """
    pm = MagicMock()
    pm.functions.balanceOf.side_effect = lambda owner, pid: MagicMock(
        call=MagicMock(return_value=balances.get(pid, 0))
    )

    def get_payout(pid, amount):
        if payouts is None:
            return MagicMock(call=MagicMock(side_effect=RuntimeError("unresolved")))
        return MagicMock(call=MagicMock(return_value=payouts.get(pid, 0) * amount // 10**6))

    pm.functions.getPayout.side_effect = get_payout
    w3 = MagicMock()
    w3.eth.contract.return_value = pm
    return w3


POSITION = {
    "position_id": "p1",
    "protocol": "v2",
    "position": "YES",
    "token_id": "1001",
    "unwanted_token_id": "1002",
}
DEPOSIT = "0x21c79811788D075ED5C701A0a4875D73c5c92c4f"


def test_v2_unresolved_is_not_redeemable():
    claim = assess_v2(POSITION, _w3({1001: 10_000_000}, payouts=None), DEPOSIT)
    assert claim.resolved is False


def test_v2_winning_payout_counts_held_shares():
    # Held 10 YES + 4 NO (partial sell); YES won: 1 pUSD per YES share
    w3 = _w3({1001: 10_000_000, 1002: 4_000_000}, payouts={1001: 10**6, 1002: 0})
    claim = assess_v2(POSITION, w3, DEPOSIT)
    assert claim.resolved is True
    assert claim.payout == 10.0


def test_v2_lost_and_nothing_held_is_resolved_zero():
    w3 = _w3({}, payouts={1001: 0, 1002: 10**6})
    claim = assess_v2(POSITION, w3, DEPOSIT)
    assert claim.resolved is True
    assert claim.payout == 0.0
