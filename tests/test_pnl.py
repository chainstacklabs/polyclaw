"""Position P&L from cost, cash back and current holdings; balance reads."""

from unittest.mock import MagicMock, create_autospec

from polymarket import SecureClient

from lib.pnl import position_pnl
from lib.position_storage import PositionEntry, PositionStorage
from lib.wallet_manager import WalletManager
from positions import BalanceReader
from redeem import Claim, _redeem


def _pos(**overrides) -> dict:
    base = dict(
        position_id="abcd1234",
        position="YES",
        token_id="111",
        unwanted_token_id="222",
        entry_amount=10.0,
        entry_price=0.37,  # preview estimate of cost per share
        sold_shares=10.0,
        sell_proceeds=6.5,
        status="open",
        holder="deposit",
        exits=[],
    )
    base.update(overrides)
    return base


def test_open_position_from_chain_balances():
    # Paid 10, got 6.5 back for NO: cost 3.5. Holds 10 YES at 0.40 = 4.00.
    pnl = position_pnl(_pos(), yes_price=0.4, no_price=0.6, balances=(10.0, 0.0))
    assert (pnl.cost, pnl.value, pnl.realized, pnl.pnl) == (3.5, 4.0, 0.0, 0.5)
    assert pnl.pnl_pct == round(0.5 / 3.5 * 100, 2)
    assert not pnl.cost_estimated
    assert pnl.holdings_source == "chain"


def test_no_side_uses_no_price():
    pnl = position_pnl(_pos(position="NO"), yes_price=0.4, no_price=0.6, balances=(10.0, 0.0))
    assert pnl.price == 0.6
    assert pnl.value == 6.0


def test_cost_falls_back_to_preview_estimate():
    pnl = position_pnl(_pos(sell_proceeds=None), 0.4, 0.6, balances=(10.0, 0.0))
    assert pnl.cost == 3.7
    assert pnl.cost_estimated


def test_leftover_other_side_counts_at_its_price():
    # Buy's sell filled 4 of 10: 6 NO still held
    pos = _pos(sold_shares=4.0, sell_proceeds=2.6)
    pnl = position_pnl(pos, 0.4, 0.6, balances=(10.0, 6.0))
    assert pnl.cost == 7.4
    assert pnl.value == round(10 * 0.4 + 6 * 0.6, 4)


def test_sold_position_realizes_sales_and_merges():
    exits = [{"merged_shares": 6.0, "sold_shares": 4.0, "proceeds": 1.8}]
    pos = _pos(sold_shares=4.0, sell_proceeds=2.6, exits=exits, status="closed")
    pnl = position_pnl(pos, 0.4, 0.6, balances=(0.0, 0.0))
    assert pnl.realized == 7.8  # 6 pairs merged at 1 pUSD + 1.80 from the sale
    assert pnl.pnl == round(7.8 - 7.4, 4)


def test_recorded_holdings_without_chain():
    exits = [{"merged_shares": 6.0, "sold_shares": 0.0, "proceeds": 0.0}]
    pos = _pos(sold_shares=4.0, sell_proceeds=2.6, exits=exits)
    pnl = position_pnl(pos, 0.4, 0.6)
    assert (pnl.shares, pnl.other_shares) == (4.0, 0.0)
    assert pnl.holdings_source == "recorded"


def test_redeemed_position_uses_recorded_payout():
    pos = _pos(redeem_tx="0xr", redeem_payout=10.0, status="resolved")
    pnl = position_pnl(pos, 1.0, 0.0, balances=(0.0, 0.0))
    assert (pnl.value, pnl.realized, pnl.pnl) == (0.0, 10.0, 6.5)


def test_redeemed_before_payouts_were_recorded_values_shares_at_resolution():
    # Chain shows 0 after the redeem; the recorded 10 YES at the resolved
    # price 1.0 stand in for the unrecorded payout.
    pos = _pos(redeem_tx="0xr", status="resolved")
    pnl = position_pnl(pos, 1.0, 0.0, balances=(0.0, 0.0))
    assert pnl.holdings_source == "recorded"
    assert pnl.value == 10.0
    assert pnl.pnl == 6.5


def test_lost_position_is_worth_nothing():
    pnl = position_pnl(_pos(status="lost"), 0.0, 1.0)
    assert (pnl.value, pnl.pnl) == (0.0, -3.5)


class TestBalanceReader:
    def _manager(self, rpc="https://rpc", configured=True):
        manager = create_autospec(WalletManager, instance=True)
        manager.is_configured = configured
        manager.rpc_url = rpc
        manager.deposit_wallet = "0xdeposit"
        manager.address = "0xsigner"
        held = {"111": 10.0, "222": 2.0}
        manager.token_balance.side_effect = lambda owner, token, protocol: held[token]
        return manager

    def test_reads_both_sides_from_the_deposit_wallet(self):
        manager = self._manager()
        assert BalanceReader(manager)(_pos(protocol="v2")) == (10.0, 2.0)
        assert manager.token_balance.call_args.args == ("0xdeposit", "222", "v2")

    def test_legacy_positions_read_the_signing_wallet(self):
        manager = self._manager()
        BalanceReader(manager)(_pos(holder="eoa"))
        assert manager.token_balance.call_args.args[0] == "0xsigner"

    def test_no_rpc_means_recorded_holdings(self):
        assert BalanceReader(self._manager(rpc=""))(_pos()) is None

    def test_read_errors_fall_back(self):
        manager = self._manager()
        manager.token_balance.side_effect = RuntimeError("rpc down")
        assert BalanceReader(manager)(_pos()) is None


def test_redeem_records_the_payout(tmp_path):
    storage = PositionStorage(tmp_path / "p.json")
    storage.add(
        PositionEntry(
            position_id="abcd1234",
            market_id="1",
            question="q",
            position="YES",
            token_id="111",
            entry_time="t",
            entry_amount=10,
            entry_price=0.37,
            split_tx="0xs",
            condition_id="0xcond",
            holder="deposit",
        )
    )
    client = create_autospec(SecureClient, instance=True)
    client.redeem_positions.return_value.wait.return_value = MagicMock(transaction_hash="0xr")
    _redeem(client, Claim(storage.load_all()[0], resolved=True, payout=10.0), storage)
    saved = storage.load_all()[0]
    assert saved["redeem_tx"] == "0xr"
    assert saved["redeem_payout"] == 10.0
    assert saved["status"] == "resolved"
