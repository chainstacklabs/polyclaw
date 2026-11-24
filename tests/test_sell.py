"""Sell rules (preview/guardrails) and the merge + sell flow on the deposit wallet."""

from unittest.mock import MagicMock, create_autospec

import pytest
from polymarket import SecureClient

from lib.contracts import CONTRACTS
from lib.gamma_client import Market
from lib.position_storage import PositionEntry, PositionStorage
from lib.wallet_manager import WalletManager
from sell import InsufficientLiquidity, execute_sell, plan_sell


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


def _position(**overrides) -> dict:
    base = dict(
        position_id="abcd1234-0000",
        market_id="559651",
        question="Xi Jinping out before 2027?",
        position="YES",
        token_id="111",
        unwanted_token_id="222",
        status="open",
        holder="deposit",
        protocol="v1",
    )
    base.update(overrides)
    return base


def _estimate(price: float = 0.5):
    calls = []

    def estimate(token_id, shares):
        calls.append((token_id, shares))
        return price

    estimate.calls = calls
    return estimate


class TestPlan:
    def test_full_exit_sells_everything(self):
        est = _estimate(0.5)
        plan = plan_sell(_position(), _market(), 10, 0, est)
        assert (plan.merge_shares, plan.sell_shares) == (0, 10)
        assert est.calls == [("111", 10)]
        assert plan.sell_floor == 0.47  # 5% under 0.50, on the 0.01 grid
        assert plan.est_proceeds == 4.7
        assert plan.full_exit

    def test_full_exit_merges_leftover_pairs_first(self):
        plan = plan_sell(_position(), _market(), 10, 3, _estimate(0.5))
        assert (plan.merge_shares, plan.sell_shares) == (3, 7)
        assert plan.est_proceeds == round(3 + 7 * 0.47, 4)

    def test_merge_only_when_remainder_is_below_minimum(self):
        est = _estimate()
        plan = plan_sell(_position(), _market(), 6, 3, est)
        assert (plan.merge_shares, plan.sell_shares) == (3, 0)
        assert est.calls == []  # no book lookup without a sell

    def test_sell_amount_rounds_down_to_cents(self):
        plan = plan_sell(_position(), _market(), 10.456789, 0, _estimate())
        assert plan.sell_shares == 10.45

    def test_partial_sell_skips_merge(self):
        plan = plan_sell(_position(), _market(), 10, 3, _estimate(), shares=6)
        assert (plan.merge_shares, plan.sell_shares) == (0, 6)
        assert not plan.full_exit

    def test_slippage_widens_the_floor(self):
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.5), slippage_pct=20)
        assert plan.sell_floor == 0.4

    @pytest.mark.parametrize(
        ("position", "market", "match"),
        [
            (_position(status="closed"), _market(), "not open"),
            (_position(holder="eoa"), _market(), "recover scan"),
            (_position(), _market(closed=True), "redeem list"),
            (_position(), _market(resolved=True), "redeem list"),
            (_position(), _market(accepting_orders=False), "not accepting orders"),
            (_position(), _market(version=None), "unknown protocol"),
        ],
    )
    def test_refuses_when_position_or_market_cant_sell(self, position, market, match):
        with pytest.raises(ValueError, match=match):
            plan_sell(position, market, 10, 0, _estimate())

    def test_refuses_empty_balance(self):
        with pytest.raises(ValueError, match="holds no YES shares"):
            plan_sell(_position(), _market(), 0, 0, _estimate())

    def test_refuses_balance_below_minimum(self):
        with pytest.raises(ValueError, match="5-share minimum"):
            plan_sell(_position(), _market(), 3, 0, _estimate())

    def test_refuses_bad_share_amounts(self):
        with pytest.raises(ValueError, match="more than the 10 held"):
            plan_sell(_position(), _market(), 10, 0, _estimate(), shares=11)
        with pytest.raises(ValueError, match="5-share minimum"):
            plan_sell(_position(), _market(), 10, 0, _estimate(), shares=2)
        with pytest.raises(ValueError, match="greater than 0"):
            plan_sell(_position(), _market(), 10, 0, _estimate(), shares=0)

    def test_refuses_when_book_is_too_thin(self):
        def no_liquidity(token_id, shares):
            raise InsufficientLiquidity("only 4 shares bid")

        with pytest.raises(ValueError, match="can't absorb 10 shares"):
            plan_sell(_position(), _market(), 10, 0, no_liquidity)

    def test_allows_sell_under_one_dollar(self):
        # 10 shares at ~0.05 = $0.50: Polymarket's minimum is in shares, and a
        # $0.98 sale filled live (2026-11-23), so no dollar floor on sells.
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.05))
        assert plan.sell_shares == 10


def _client(sold: float = 7.0, proceeds: float = 3.5, ok: bool = True) -> MagicMock:
    client = create_autospec(SecureClient, instance=True)
    client.merge_positions.return_value.wait.return_value.transaction_hash = "0xmerge"
    client.place_market_order.return_value = MagicMock(
        ok=ok, order_id="0xorder", making_amount=sold, taking_amount=proceeds, message="no fill"
    )
    return client


def _storage(tmp_path) -> PositionStorage:
    storage = PositionStorage(tmp_path / "positions.json")
    storage.add(
        PositionEntry(
            position_id="abcd1234-0000",
            market_id="559651",
            question="q",
            position="YES",
            token_id="111",
            entry_time="2026-11-20T00:00:00+00:00",
            entry_amount=10,
            entry_price=0.3,
            split_tx="0xsplit",
            condition_id="0xcond",
            holder="deposit",
            unwanted_token_id="222",
        )
    )
    return storage


class TestExecute:
    def test_proceeds_are_net_of_taker_fee(self, tmp_path):
        market = _market(fee_schedule={"exponent": 1, "rate": 0.05})
        plan = plan_sell(_position(), market, 7, 0, _estimate(0.5))
        result = execute_sell(_client(sold=7.0, proceeds=3.5), plan, _storage(tmp_path))
        assert result.proceeds == pytest.approx(3.5 - 7 * 0.05 * 0.5 * 0.5)

    def test_merges_then_sells_and_closes(self, tmp_path):
        storage = _storage(tmp_path)
        client = _client(sold=7.0, proceeds=3.5)
        plan = plan_sell(_position(), _market(), 10, 3, _estimate(0.5))
        result = execute_sell(client, plan, storage)

        assert client.merge_positions.call_args.kwargs == {
            "condition_id": "0xcond",
            "amount": 3_000_000,
        }
        sell = client.place_market_order.call_args.kwargs
        assert sell["asset_id"] == "111"
        assert sell["side"] == "SELL"
        assert sell["shares"] == 7
        assert sell["min_price"] == 0.47
        assert sell["order_type"] == "FAK"

        assert result.error is None
        assert (result.merge_tx, result.sold_shares, result.proceeds) == ("0xmerge", 7, 3.5)
        saved = storage.load_all()[0]
        assert saved["status"] == "closed"
        assert saved["exits"][0]["merged_shares"] == 3
        assert saved["exits"][0]["proceeds"] == 3.5

    def test_leftover_other_side_keeps_position_open(self, tmp_path):
        # 10 YES + 12 NO: merge 10 pairs, 2 NO stay in the wallet
        storage = _storage(tmp_path)
        plan = plan_sell(_position(), _market(), 10, 12, _estimate(0.5))
        assert (plan.merge_shares, plan.sell_shares) == (10, 0)
        execute_sell(_client(), plan, storage)
        assert storage.load_all()[0]["status"] == "open"

    def test_partial_fill_keeps_position_open(self, tmp_path):
        storage = _storage(tmp_path)
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.5))
        result = execute_sell(_client(sold=4.0, proceeds=2.0), plan, storage)
        assert "Partial fill" in result.error
        saved = storage.load_all()[0]
        assert saved["status"] == "open"
        assert saved["exits"][0]["sold_shares"] == 4.0

    def test_partial_sell_keeps_position_open(self, tmp_path):
        storage = _storage(tmp_path)
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.5), shares=5)
        result = execute_sell(_client(sold=5.0, proceeds=2.5), plan, storage)
        assert result.error is None
        assert storage.load_all()[0]["status"] == "open"

    def test_rejected_order_records_nothing(self, tmp_path):
        storage = _storage(tmp_path)
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.5))
        result = execute_sell(_client(ok=False), plan, storage)
        assert result.error == "no fill"
        saved = storage.load_all()[0]
        assert saved["status"] == "open"
        assert saved["exits"] == []

    def test_sell_error_after_merge_is_reported_not_raised(self, tmp_path):
        storage = _storage(tmp_path)
        client = _client()
        client.place_market_order.side_effect = RuntimeError("timeout")
        plan = plan_sell(_position(), _market(), 10, 3, _estimate(0.5))
        result = execute_sell(client, plan, storage)
        assert result.error == "timeout"
        assert storage.load_all()[0]["exits"][0]["merge_tx"] == "0xmerge"

    def test_sell_error_without_merge_raises(self, tmp_path):
        client = _client()
        client.place_market_order.side_effect = RuntimeError("timeout")
        plan = plan_sell(_position(), _market(), 10, 0, _estimate(0.5))
        with pytest.raises(RuntimeError):
            execute_sell(client, plan, _storage(tmp_path))


class TestBalances:
    @pytest.mark.parametrize(
        ("protocol", "ledger"), [("v1", CONTRACTS["CTF"]), ("v2", CONTRACTS["POSITION_MANAGER"])]
    )
    def test_reads_the_ledger_for_the_protocol(self, monkeypatch, protocol, ledger):
        w3 = MagicMock()
        w3.eth.contract.return_value.functions.balanceOf.return_value.call.return_value = 7_500_000
        monkeypatch.setattr(WalletManager, "web3", lambda self: w3)
        owner = "0x" + "11" * 20
        balance = WalletManager().token_balance(owner, "111", protocol)
        assert balance == 7.5
        assert w3.eth.contract.call_args.kwargs["address"].lower() == ledger.lower()
