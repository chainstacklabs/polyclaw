"""Trading approvals go out in relayer-sized batches; failures are reported."""

from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

from polymarket import SecureClient

import wallet
from lib.trading import set_trading_approvals


def _client(n_erc20: int, n_erc1155: int) -> MagicMock:
    # Autospec against the real SDK: renamed/removed methods fail the tests.
    client = create_autospec(SecureClient, instance=True)
    client.wallet = "0xDeposit"
    missing = client.get_trading_approvals_state.return_value.missing
    missing.erc20 = tuple(
        SimpleNamespace(token_address=f"0x{i:040x}", spender=f"0x{i + 1:040x}", amount=1)
        for i in range(n_erc20)
    )
    missing.erc1155 = tuple(
        SimpleNamespace(token_address=f"0x{i:040x}", operator=f"0x{i + 1:040x}")
        for i in range(n_erc1155)
    )
    return client


def test_approvals_sent_in_batches_of_four():
    """The relayer rejects all 17 approvals in one batch (Cloudflare 400)."""
    client = _client(9, 8)

    assert set_trading_approvals(client) == 17

    sizes = [len(c.kwargs["calls"]) for c in client.execute_transaction.call_args_list]
    assert sizes == [4, 4, 4, 4, 1]
    assert client.execute_transaction.return_value.wait.call_count == 5
    client.setup_trading_approvals.assert_not_called()


def test_no_transaction_when_fully_approved():
    client = _client(0, 0)

    assert set_trading_approvals(client) == 0
    client.execute_transaction.assert_not_called()


def test_wallet_setup_reports_failed_approvals(capsys):
    client = _client(4, 0)
    client.execute_transaction.side_effect = RuntimeError("blocked by Cloudflare")

    with patch.object(wallet, "create_client", return_value=client):
        assert wallet.cmd_setup(SimpleNamespace()) == 1

    assert "Cloudflare" in capsys.readouterr().err


def test_wallet_setup_succeeds_when_approved(capsys):
    with patch.object(wallet, "create_client", return_value=_client(0, 0)):
        assert wallet.cmd_setup(SimpleNamespace()) == 0

    assert "already in place" in capsys.readouterr().out
