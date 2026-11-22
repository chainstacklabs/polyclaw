"""Config parsing and guardrail helpers."""

import pytest

from lib import trading
from lib.trading import ConfigError, compute_sell_floor


def test_sell_floor_rounds_to_cent_and_clamps():
    assert compute_sell_floor(0.70, 10) == 0.63
    assert compute_sell_floor(0.005, 10) == 0.01
    assert compute_sell_floor(0.5, 90) == 0.25  # slippage capped at 50%


def test_max_trade_default_and_override(monkeypatch):
    monkeypatch.delenv("POLYCLAW_MAX_TRADE_USD", raising=False)
    assert trading.max_trade_usd() == 50.0
    monkeypatch.setenv("POLYCLAW_MAX_TRADE_USD", "200")
    assert trading.max_trade_usd() == 200.0
    monkeypatch.setenv("POLYCLAW_MAX_TRADE_USD", "lots")
    with pytest.raises(ConfigError):
        trading.max_trade_usd()


def test_relayer_key_required(monkeypatch):
    monkeypatch.setenv("POLYCLAW_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.delenv("POLYCLAW_RELAYER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="Relayer API key"):
        trading.relayer_credentials()


def test_relayer_address_defaults_to_signer(monkeypatch):
    monkeypatch.setenv("POLYCLAW_PRIVATE_KEY", "11" * 32)  # no 0x is fine
    monkeypatch.setenv("POLYCLAW_RELAYER_API_KEY", "rk")
    monkeypatch.delenv("POLYCLAW_RELAYER_ADDRESS", raising=False)
    key, address = trading.relayer_credentials()
    assert key == "rk"
    assert address == trading.signer_address()


def test_deposit_wallet_cache_is_per_signer():
    trading.cache_deposit_wallet("0xAbc", "0xDeposit")
    assert trading.cached_deposit_wallet("0xabc") == "0xDeposit"
    assert trading.cached_deposit_wallet("0xother") is None
