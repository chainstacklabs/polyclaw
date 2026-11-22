"""Trading client: official ``polymarket-client`` SDK on a deposit wallet.

Polymarket requires new API traders to use a *deposit wallet* (signature
type 3): a personal smart wallet owned by your key. Orders from a bare key
(signature type 0) are rejected with "maker address not allowed, please use
the deposit wallet flow". Polymarket's relayer pays gas for the deposit
wallet, so the signing key needs no POL for trading.

Setup per user:
- ``POLYCLAW_PRIVATE_KEY``: the signing key (owner of the deposit wallet).
- ``POLYCLAW_RELAYER_API_KEY``: a Relayer API key minted for that same key at
  polymarket.com/settings?tab=api-keys. The relayer only sponsors the wallet
  the key was minted for.
- ``POLYCLAW_RELAYER_ADDRESS`` (optional): the address the relayer key belongs
  to; defaults to the signing key's address.

``SecureClient.create()`` deploys the deposit wallet on first use (free) and
returns a client acting for it. Its address is cached so read-only commands
(`wallet status`, `positions`) don't need to create a client.
"""

import json
import os
from typing import Optional

from eth_account import Account

from lib.position_storage import get_storage_dir

WALLET_CACHE = get_storage_dir() / "wallet.json"

# Guardrail: largest single trade the skill will execute without an explicit
# higher limit in the environment.
DEFAULT_MAX_TRADE_USD = 50.0
# Polymarket's minimum order value.
MIN_ORDER_USD = 1.0
# Fallback share minimum when Gamma omits `orderMinSize` (5 on every live
# market checked 2026-11-22).
DEFAULT_MIN_ORDER_SHARES = 5.0


class ConfigError(Exception):
    """Missing or invalid configuration; message tells the user what to set."""


def private_key() -> str:
    key = os.environ.get("POLYCLAW_PRIVATE_KEY", "").strip()
    if not key:
        raise ConfigError("Set POLYCLAW_PRIVATE_KEY (the wallet's signing key).")
    return key if key.startswith("0x") else "0x" + key


def signer_address() -> str:
    return Account.from_key(private_key()).address


def relayer_credentials() -> tuple[str, str]:
    """(relayer api key, relayer address) for the SDK's RelayerApiKey."""
    key = os.environ.get("POLYCLAW_RELAYER_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "Set POLYCLAW_RELAYER_API_KEY: mint a Relayer API key for your "
            "signing wallet at polymarket.com/settings?tab=api-keys."
        )
    address = os.environ.get("POLYCLAW_RELAYER_ADDRESS", "").strip() or signer_address()
    return key, address


def max_trade_usd() -> float:
    raw = os.environ.get("POLYCLAW_MAX_TRADE_USD", "").strip()
    if not raw:
        return DEFAULT_MAX_TRADE_USD
    try:
        value = float(raw)
    except ValueError as e:
        raise ConfigError(f"POLYCLAW_MAX_TRADE_USD must be a number, got {raw!r}") from e
    if value <= 0:
        raise ConfigError("POLYCLAW_MAX_TRADE_USD must be greater than 0")
    return value


def builder_code() -> Optional[str]:
    """Optional bytes32 builder code attached to orders (Polymarket builder program)."""
    return os.environ.get("POLY_BUILDER_CODE", "").strip() or None


def create_client():
    """Authenticated SecureClient acting for the signer's deposit wallet.

    Deploys the deposit wallet on first use (gasless). Caches its address.
    """
    from polymarket import SecureClient
    from polymarket.auth import RelayerApiKey

    key, address = relayer_credentials()
    client = SecureClient.create(
        private_key=private_key(),
        api_key=RelayerApiKey(key=key, address=address),
    )
    cache_deposit_wallet(signer_address(), str(client.wallet))
    return client


def cache_deposit_wallet(signer: str, deposit_wallet: str) -> None:
    WALLET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WALLET_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"signer": signer, "deposit_wallet": deposit_wallet}, indent=2))
    tmp.replace(WALLET_CACHE)


def cached_deposit_wallet(signer: Optional[str] = None) -> Optional[str]:
    """Deposit wallet address saved by the last client creation for this signer."""
    try:
        data = json.loads(WALLET_CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if signer and data.get("signer", "").lower() != signer.lower():
        return None
    return data.get("deposit_wallet")


def compute_sell_floor(price: float, slippage_pct: float = 10.0) -> float:
    """Worst acceptable sell price: `slippage_pct` below `price`, on the 0.01 grid.

    Multiples of 0.01 are valid on both 0.01- and 0.001-tick markets.
    """
    slippage_pct = max(1.0, min(50.0, slippage_pct))
    return round(max(price * (1 - slippage_pct / 100), 0.01), 2)
