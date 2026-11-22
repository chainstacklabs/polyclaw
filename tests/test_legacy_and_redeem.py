"""Recovery planning for signing-wallet tokens, and redeem payout math."""

from lib.legacy import Holding, plan_action
from redeem import expected_payout


def _h(yes=0, no=0, resolved=False, payouts=(0, 0), denom=0) -> Holding:
    return Holding(
        condition_id="0xc",
        collateral_name="pUSD",
        collateral="0xpusd",
        yes_id=1,
        no_id=2,
        yes=yes,
        no=no,
        resolved=resolved,
        payouts=list(payouts),
        denominator=denom,
    )


def test_unresolved_pair_is_merged():
    # The pUSD-split bug leaves equal YES + NO: merge all of it back
    a = plan_action(_h(yes=20_000_000, no=20_000_000))
    assert a.kind == "merge"
    assert a.amount == 20_000_000
    assert a.note is None


def test_unbalanced_pair_merges_min_and_notes_leftover():
    a = plan_action(_h(yes=20_000_000, no=5_000_000))
    assert a.kind == "merge"
    assert a.amount == 5_000_000
    assert "15 YES stay" in a.note


def test_resolved_winning_tokens_are_redeemed():
    a = plan_action(_h(yes=20_000_000, no=20_000_000, resolved=True, payouts=(1, 0), denom=1))
    assert a.kind == "redeem"
    assert a.amount == 20_000_000


def test_resolved_losing_only_does_nothing():
    a = plan_action(_h(no=7_000_000, resolved=True, payouts=(1, 0), denom=1))
    assert a.kind == "none"
    assert "pay nothing" in a.note


def test_single_side_unresolved_waits():
    a = plan_action(_h(yes=3_000_000))
    assert a.kind == "none"


def test_redeem_payout_counts_both_sides():
    # Held 10 YES (wanted) + 4 NO left after a partial sell; YES won
    assert expected_payout(10, 4, "YES", [1, 0], 1) == 10
    # Same holdings, NO won: the leftover NO still pays
    assert expected_payout(10, 4, "YES", [0, 1], 1) == 4
    # Unresolved
    assert expected_payout(10, 4, "YES", [], 0) == 0
