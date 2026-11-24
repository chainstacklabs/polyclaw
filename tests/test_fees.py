"""Sale proceeds are what reaches the wallet: taking_amount minus the taker fee.

Live 2026-11-23: 5 NO at 0.47 on a sports market (rate 0.05) paid $2.2877,
not the $2.35 the order response reported; 5 YES at 0.49 on a crypto market
(rate 0.01) paid $2.4375, not $2.45. fee = shares * rate * p * (1 - p).
"""

import pytest

from lib.gamma_client import GammaClient
from lib.trading import net_sale_proceeds, taker_fee

SPORTS = {"exponent": 1, "rate": 0.05}
CRYPTO = {"exponent": 1, "rate": 0.01}


def test_taker_fee_matches_live_trades():
    assert taker_fee(5, 0.47, SPORTS) == pytest.approx(2.35 - 2.28773, abs=1e-4)
    assert taker_fee(5, 0.49, CRYPTO) == pytest.approx(2.45 - 2.43751, abs=1e-4)


def test_no_schedule_means_no_fee():
    assert taker_fee(5, 0.47, None) == 0.0


def test_net_sale_proceeds():
    assert net_sale_proceeds(5, 2.35, SPORTS) == pytest.approx(2.28773, abs=1e-4)
    assert net_sale_proceeds(0, 0, SPORTS) == 0.0


def test_market_carries_fee_schedule_only_when_fees_are_on():
    base = {"id": "1", "question": "q", "conditionId": "0xc", "clobTokenIds": '["1","2"]',
            "outcomePrices": '["0.5","0.5"]', "version": "v1"}
    on = GammaClient()._parse_market({**base, "feesEnabled": True, "feeSchedule": SPORTS})
    off = GammaClient()._parse_market({**base, "feesEnabled": False, "feeSchedule": SPORTS})
    assert on.fee_schedule == SPORTS
    assert off.fee_schedule is None
