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
import math
import os
from typing import Callable, Optional

from eth_account import Account

from lib.position_storage import get_storage_dir

WALLET_CACHE = get_storage_dir() / "wallet.json"

# Guardrail: largest single trade the skill will execute without an explicit
# higher limit in the environment.
DEFAULT_MAX_TRADE_USD = 50.0
# Fallback share minimum when Gamma omits `orderMinSize` (5 on every live
# market checked 2026-11-22).
DEFAULT_MIN_ORDER_SHARES = 5.0
# Approvals per relayed transaction. The relayer rejected all 17 in one batch
# (Cloudflare 400) and accepted batches of 4 (2026-11-23, SDK 0.20.0).
APPROVAL_BATCH_SIZE = 4


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


def set_trading_approvals(client) -> int:
    """Grant the deposit wallet's missing trading approvals in small batches.

    ``setup_trading_approvals()`` relays every missing approval (17 on a new
    wallet) as one batch, which the relayer rejects (Cloudflare 400). Raises
    on the first failed batch; a re-run skips approvals already granted.
    Returns the number of approvals sent.
    """
    from polymarket.calls import erc20_approval_call, erc1155_set_approval_for_all_call

    missing = client.get_trading_approvals_state().missing
    calls = [
        erc20_approval_call(token_address=a.token_address, spender=a.spender, amount=a.amount)
        for a in missing.erc20
    ] + [
        erc1155_set_approval_for_all_call(
            token_address=a.token_address, operator=a.operator, approved=True
        )
        for a in missing.erc1155
    ]
    batches = [
        calls[i : i + APPROVAL_BATCH_SIZE] for i in range(0, len(calls), APPROVAL_BATCH_SIZE)
    ]
    for n, batch in enumerate(batches, 1):
        client.execute_transaction(
            calls=batch, metadata=f"Trading setup approvals {n}/{len(batches)}"
        ).wait()
    return len(calls)


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


class InsufficientLiquidity(Exception):
    """The order book can't absorb the sell at any price."""


# (token_id, shares) -> worst price level needed to fill that many shares.
PriceEstimator = Callable[[str, float], float]


def sdk_estimator() -> PriceEstimator:
    """Order-book estimate of the worst price a SELL of `shares` fills at."""
    from polymarket import PublicClient
    from polymarket.errors import InsufficientLiquidityError

    def estimate(token_id: str, shares: float) -> float:
        try:
            with PublicClient() as client:
                return float(
                    client.estimate_market_price(
                        asset_id=token_id, side="SELL", shares=shares, order_type="FOK"
                    )
                )
        except InsufficientLiquidityError as e:
            raise InsufficientLiquidity(str(e)) from e

    return estimate


def taker_fee(shares: float, price: float, schedule: Optional[dict]) -> float:
    """Taker fee in pUSD on a fill of `shares` at `price` (Gamma `feeSchedule`).

    fee = shares * rate * price^exponent * (1 - price)^exponent; matched what
    reached the wallet on live sells (2026-11-23) to 1e-5.
    """
    if not schedule or shares <= 0 or not 0 < price < 1:
        return 0.0
    rate = float(schedule.get("rate", 0.0))
    exponent = int(schedule.get("exponent", 1))
    return shares * rate * price**exponent * (1 - price) ** exponent


def net_sale_proceeds(shares: float, gross: float, schedule: Optional[dict]) -> float:
    """pUSD a SELL fill pays into the wallet. The order response's
    taking_amount is before the taker fee."""
    if shares <= 0:
        return 0.0
    return round(gross - taker_fee(shares, gross / shares, schedule), 6)


def compute_sell_floor(price: float, slippage_pct: float = 10.0) -> float:
    """Worst acceptable sell price: `slippage_pct` below `price`, rounded down
    so it never sits above the book price it came from.

    On the 0.01 grid, valid on both 0.01- and 0.001-tick markets. Bids under
    a cent exist only on 0.001-tick books, so those use the 0.001 grid.
    """
    slippage_pct = max(1.0, min(50.0, slippage_pct))
    raw = price * (1 - slippage_pct / 100)
    tick = 0.01 if price >= 0.01 else 0.001
    # round() first so float noise (0.63 / 0.01 = 62.999999) doesn't drop a tick
    ticks = math.floor(round(raw / tick, 6))
    return round(max(ticks, 1) * tick, 3)
