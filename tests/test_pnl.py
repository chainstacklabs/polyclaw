"""Position P&L from cost, cash back and current holdings; balance reads."""

from unittest.mock import MagicMock, create_autospec

from polymarket import SecureClient

from lib.pnl import position_pnl
from lib.position_storage import PositionEntry, PositionStorage
from lib.wallet_manager import WalletManager
from positions import BalanceReader
from redeem import Claim, _redeem, assess


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

    def test_payout_reads_resolution_on_chain(self):
        manager = self._manager()
        manager.web3.return_value = _w3(_ctf(1, [0, 1]))
        pos = _pos(condition_id="0x" + "ab" * 32)
        assert BalanceReader(manager).payout(pos) == (0.0, 1.0)

    def test_payout_none_without_rpc_or_on_errors(self):
        assert BalanceReader(self._manager(rpc="")).payout(_pos()) is None
        manager = self._manager()
        manager.web3.side_effect = RuntimeError("rpc down")
        assert BalanceReader(manager).payout(_pos(condition_id="0xab")) is None


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


# ── resolved markets: valued at payout (Polymarket auto-redeems) ─────────


def test_auto_redeemed_position_is_worth_its_payout():
    # Kept both sides (--skip-sell): 10 YES + 10 NO for $10. NO won and
    # Polymarket's auto-redeem emptied the wallet: P&L is 0, not -$10.
    pos = _pos(sold_shares=0.0, sell_proceeds=0.0)
    pnl = position_pnl(pos, 0.5, 0.5, balances=(0.0, 0.0), payout=(0.0, 1.0))
    assert (pnl.value, pnl.pnl) == (10.0, 0.0)
    assert pnl.holdings_source == "resolved"


def test_resolved_loser_ignores_the_last_price():
    pnl = position_pnl(_pos(), 0.4, 0.6, balances=(10.0, 0.0), payout=(0.0, 1.0))
    assert pnl.value == 0.0


def test_redeemed_by_polyclaw_is_not_counted_twice():
    pos = _pos(redeem_tx="0xr", redeem_payout=10.0, status="resolved")
    pnl = position_pnl(pos, 1.0, 0.0, balances=(0.0, 0.0), payout=(1.0, 0.0))
    assert (pnl.value, pnl.realized) == (0.0, 10.0)


def _ctf(denominator: int, numerators: list[int], balance=0) -> MagicMock:
    """`balance`: one amount for every token, or {token_id: amount}."""
    ctf = MagicMock()
    ctf.functions.payoutDenominator.return_value.call.return_value = denominator
    ctf.functions.payoutNumerators.side_effect = lambda cid, i: MagicMock(
        call=MagicMock(return_value=numerators[i])
    )
    held = balance if isinstance(balance, dict) else None
    ctf.functions.balanceOf.side_effect = lambda owner, tid: MagicMock(
        call=MagicMock(return_value=held.get(tid, 0) if held is not None else balance)
    )
    return ctf


def _w3(contract: MagicMock) -> MagicMock:
    w3 = MagicMock()
    w3.eth.contract.return_value = contract
    return w3


def test_assess_detects_auto_redeem():
    pos = _pos(sold_shares=0.0, sell_proceeds=0.0, condition_id="0x" + "ab" * 32)
    claim = assess(pos, _w3(_ctf(1, [0, 1])), "0x" + "11" * 20)
    assert claim.resolved and claim.auto
    assert claim.payout == 10.0


def test_assess_winner_paid_out_loser_left_behind():
    # Live 2026-11-23: kept 1 YES + 1 NO; NO won. Auto-redeem paid the NO and
    # left the worthless YES in the wallet. That's $1 paid, not a loss.
    pos = _pos(
        entry_amount=1.0, sold_shares=0.0, sell_proceeds=0.0, condition_id="0x" + "ab" * 32
    )
    claim = assess(pos, _w3(_ctf(1, [0, 1], balance={111: 1_000_000})), "0x" + "11" * 20)
    assert claim.resolved and claim.auto
    assert claim.payout == 1.0


def test_assess_held_tokens_are_not_auto():
    pos = _pos(condition_id="0x" + "ab" * 32)
    claim = assess(pos, _w3(_ctf(1, [1, 0], balance=10_000_000)), "0x" + "11" * 20)
    assert claim.resolved and not claim.auto
    assert claim.payout == 10.0


def test_auto_redeem_is_recorded_without_a_transaction(tmp_path):
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
    claim = Claim(storage.load_all()[0], resolved=True, payout=10.0, auto=True)
    _redeem(client, claim, storage)
    client.redeem_positions.assert_not_called()
    saved = storage.load_all()[0]
    assert saved["redeem_payout"] == 10.0
    assert saved["redeem_tx"] == "auto-redeem"
    assert saved["status"] == "resolved"


def test_two_positions_on_one_market_each_get_their_own_shares():
    # A and B each recorded 10 YES; the wallet holds 20; YES won.
    pos = _pos(condition_id="0x" + "ab" * 32)  # 10 YES recorded, NO sold
    claim = assess(pos, _w3(_ctf(1, [1, 0], balance={111: 20_000_000})), "0x" + "11" * 20)
    assert claim.payout == 10.0  # not the wallet's 20


def test_one_redeem_tx_per_market_per_run(tmp_path):
    storage = PositionStorage(tmp_path / "p.json")
    for pid in ("aaaa0000", "bbbb0000"):
        storage.add(
            PositionEntry(
                position_id=pid,
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
    done: dict[str, str] = {}
    for p in storage.load_all():
        _redeem(client, Claim(p, resolved=True, payout=10.0), storage, done)
    client.redeem_positions.assert_called_once()
    saved = storage.load_all()
    assert [p["redeem_payout"] for p in saved] == [10.0, 10.0]
    assert [p["redeem_tx"] for p in saved] == ["0xr", "0xr"]


def test_legacy_redeem_is_valued_at_the_payout():
    # Redeemed before payouts were recorded: the recorded shares are worth
    # the on-chain payout, not Gamma's last price.
    pos = _pos(redeem_tx="0xr", status="resolved")
    pnl = position_pnl(pos, 0.4, 0.6, balances=(0.0, 0.0), payout=(1.0, 0.0))
    assert pnl.value == 10.0
    assert pnl.holdings_source == "resolved"


def test_assess_reads_both_balances_at_one_block():
    # Auto-redeem landing between two `latest` reads would mix two states.
    ctf = _ctf(1, [0, 1])
    reads = []
    ctf.functions.balanceOf.side_effect = lambda owner, tid: MagicMock(
        call=lambda **kw: reads.append(kw) or 0
    )
    w3 = _w3(ctf)
    w3.eth.block_number = 123
    assess(_pos(condition_id="0x" + "ab" * 32), w3, "0x" + "11" * 20)
    assert len(reads) == 2
    assert all(kw == {"block_identifier": 123} for kw in reads)
