"""Buy rules (preview/guardrails) and the split + sell flow on the deposit wallet."""

from unittest.mock import MagicMock, create_autospec

import pytest
from polymarket import SecureClient

from lib.gamma_client import Market
from lib.position_storage import PositionStorage
from trade import execute_buy, plan_buy


def _market(**overrides) -> Market:
    base = dict(
        id="559651",
        question="Xi Jinping out before 2027?",
        slug="xi",
        condition_id="0xcond",
        yes_token_id="111",
        no_token_id="222",
        yes_price=0.3,
        no_price=0.7,
        volume=0.0,
        volume_24h=0.0,
        liquidity=0.0,
        end_date="",
        active=True,
        closed=False,
        resolved=False,
        outcome=None,
        version="v1",
        order_min_size=5.0,
        accepting_orders=True,
    )
    base.update(overrides)
    return Market(**base)


class TestPlan:
    def test_yes_plan_sells_no_side_with_floor(self):
        plan = plan_buy(_market(), "yes", 10, max_usd=50)
        assert plan.side == "YES"
        assert plan.wanted_token_id == "111"
        assert plan.unwanted_token_id == "222"
        assert plan.sell_floor == 0.63  # 10% under 0.70
        assert plan.est_recovered == 6.3
        assert plan.est_cost == 3.7

    def test_refuses_above_cap(self):
        with pytest.raises(ValueError, match="above the \\$50.00 limit"):
            plan_buy(_market(), "YES", 60, max_usd=50)

    def test_accepts_protocol_v2_market(self):
        plan = plan_buy(_market(version="v2"), "YES", 10, max_usd=50)
        assert plan.protocol == "v2"

    def test_refuses_unknown_protocol(self):
        with pytest.raises(ValueError, match="unknown protocol"):
            plan_buy(_market(version=None), "YES", 10, max_usd=50)

    def test_refuses_closed_market(self):
        with pytest.raises(ValueError, match="not open"):
            plan_buy(_market(accepting_orders=False), "YES", 10, max_usd=50)

    def test_refuses_below_share_minimum(self):
        with pytest.raises(ValueError, match="5-share minimum"):
            plan_buy(_market(), "YES", 3, max_usd=50)

    def test_refuses_sell_under_one_dollar(self):
        # 6 shares of NO at a 0.09 floor = $0.54
        with pytest.raises(ValueError, match="under \\$1"):
            plan_buy(_market(yes_price=0.9, no_price=0.1), "YES", 6, max_usd=50)

    def test_skip_sell_allows_small_amounts(self):
        plan = plan_buy(_market(), "YES", 3, max_usd=50, skip_sell=True)
        assert not plan.sell_unwanted
        assert plan.est_cost == 3

    def test_refuses_bad_side_and_amount(self):
        with pytest.raises(ValueError):
            plan_buy(_market(), "MAYBE", 10, max_usd=50)
        with pytest.raises(ValueError):
            plan_buy(_market(), "YES", 0, max_usd=50)


def _client(sold: float = 10.0, ok: bool = True) -> MagicMock:
    client = create_autospec(SecureClient, instance=True)
    client.split_position.return_value.wait.return_value.transaction_hash = "0xsplit"
    resp = MagicMock(
        ok=ok, order_id="0xorder", making_amount=sold, taking_amount=sold * 0.65, message="no fill"
    )
    client.place_market_order.return_value = resp
    return client


class TestExecute:
    def test_splits_then_sells_and_records(self, tmp_path):
        storage = PositionStorage(tmp_path / "positions.json")
        client = _client(sold=10.0)
        result = execute_buy(client, plan_buy(_market(), "YES", 10, max_usd=50), storage)

        split = client.split_position.call_args.kwargs
        assert split == {"condition_id": "0xcond", "amount": 10_000_000}
        sell = client.place_market_order.call_args.kwargs
        assert sell["asset_id"] == "222"
        assert sell["side"] == "SELL"
        assert sell["shares"] == 10
        assert sell["min_price"] == 0.63
        assert sell["order_type"] == "FAK"

        assert result.split_tx == "0xsplit"
        assert result.sold_shares == 10.0
        assert result.sell_error is None
        saved = storage.load_all()[0]
        assert saved["holder"] == "deposit"
        assert saved["token_id"] == "111"
        assert saved["unwanted_token_id"] == "222"
        assert saved["sell_proceeds"] == 6.5

    def test_partial_fill_is_reported(self, tmp_path):
        client = _client(sold=4.0)
        result = execute_buy(
            client, plan_buy(_market(), "YES", 10, max_usd=50), PositionStorage(tmp_path / "p.json")
        )
        assert "Partial fill" in result.sell_error

    def test_rejected_sell_is_reported_and_position_kept(self, tmp_path):
        storage = PositionStorage(tmp_path / "p.json")
        result = execute_buy(
            _client(ok=False), plan_buy(_market(), "NO", 10, max_usd=50), storage
        )
        assert result.sell_error == "no fill"
        assert storage.load_all()[0]["clob_filled"] is False

    def test_skip_sell_places_no_order(self, tmp_path):
        client = _client()
        execute_buy(
            client,
            plan_buy(_market(), "YES", 3, max_usd=50, skip_sell=True),
            PositionStorage(tmp_path / "p.json"),
        )
        client.place_market_order.assert_not_called()
