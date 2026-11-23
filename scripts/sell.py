#!/usr/bin/env python3
"""Sell (exit) a position held in the deposit wallet.

  sell <position_id>              Preview a full exit (id prefix is enough)
  sell <position_id> --yes        Execute it
  sell <position_id> --shares 10  Sell only part of it

A full exit first merges any YES + NO pairs the position still holds (each
pair is worth exactly 1 pUSD, no slippage), then sells the rest of your side
on the order book with a FAK order and a price floor. Without ``--yes`` it
only prints a preview. Selling is allowed in close-only regions.

Exit codes: 0 = done (or preview), 1 = refused / failed before anything
changed, 3 = partly done (partial fill, or merge done but the sell failed).
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import SUPPORTED_VERSIONS, GammaClient, Market  # noqa: E402
from lib.position_storage import PositionStorage  # noqa: E402
from lib.trading import (  # noqa: E402
    DEFAULT_MIN_ORDER_SHARES,
    MIN_ORDER_USD,
    ConfigError,
    builder_code,
    compute_sell_floor,
    create_client,
)
from lib.wallet_manager import WalletManager  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_PARTIAL = 3

# Balances below this are treated as empty (rounding dust).
DUST_SHARES = 0.01
DEFAULT_SLIPPAGE_PCT = 5.0


class InsufficientLiquidity(Exception):
    """The order book can't absorb the sell at any price."""


# (token_id, shares) -> worst price level needed to fill that many shares.
PriceEstimator = Callable[[str, float], float]


@dataclass
class SellPlan:
    position_id: str
    market_id: str
    question: str
    condition_id: str
    protocol: str
    side: str
    token_id: str
    held_shares: float  # your side, in the deposit wallet
    held_other: float  # the other side, left over if its sell failed at buy time
    merge_shares: float  # YES + NO pairs merged back to pUSD first (1 pUSD each)
    sell_shares: float
    est_price: float  # worst book level needed to fill sell_shares now
    sell_floor: float  # lowest price the order accepts
    est_proceeds: float  # worst case: merge_shares + sell_shares * sell_floor
    full_exit: bool


@dataclass
class SellResult:
    plan: SellPlan
    merge_tx: Optional[str] = None
    sell_order_id: Optional[str] = None
    sold_shares: float = 0.0
    proceeds: float = 0.0  # pUSD received from the sell
    error: Optional[str] = None


def _floor_shares(value: float) -> float:
    """Round shares down to 2 decimals so we never try to sell more than held."""
    return int(value * 100) / 100


def plan_sell(
    position: dict,
    market: Market,
    held_shares: float,
    held_other: float,
    estimate: PriceEstimator,
    *,
    shares: Optional[float] = None,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> SellPlan:
    """Validate a sell and work out what it will do. Raises ValueError if refused."""
    pid = position["position_id"]
    if position.get("status") != "open":
        raise ValueError(f"Position {pid[:8]} is {position.get('status')}, not open")
    if position.get("holder", "eoa") != "deposit":
        raise ValueError(
            "This position was opened by polyclaw <= 0.1.0 and its tokens sit on the "
            "signing wallet. Use `polyclaw recover scan` for it."
        )
    if market.version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Market {market.id} is on unknown protocol {market.version!r}")
    if market.resolved or market.closed:
        raise ValueError(
            f"Market {market.id} is closed, so there's no order book. "
            "If it resolved in your favor, use `polyclaw redeem list`."
        )
    if not market.active or not market.accepting_orders:
        raise ValueError(f"Market {market.id} is not accepting orders right now")
    if held_shares < DUST_SHARES:
        raise ValueError(
            f"The deposit wallet holds no {position['position']} shares for this "
            "position (already sold or redeemed?)"
        )

    min_shares = market.order_min_size or DEFAULT_MIN_ORDER_SHARES
    full_exit = shares is None
    if full_exit:
        merge_shares = _floor_shares(min(held_shares, held_other))
        if merge_shares < DUST_SHARES:
            merge_shares = 0.0
        sell_shares = _floor_shares(held_shares - merge_shares)
        if sell_shares < min_shares:
            if merge_shares == 0:
                raise ValueError(
                    f"{held_shares:g} shares is below this market's {min_shares:g}-share "
                    "minimum order, so it can't be sold. Hold it and redeem after resolution."
                )
            sell_shares = 0.0  # merge only; the remainder is below the order minimum
    else:
        if shares <= 0:
            raise ValueError("--shares must be greater than 0")
        if shares > held_shares + 1e-9:
            raise ValueError(f"--shares {shares:g} is more than the {held_shares:g} held")
        if shares < min_shares:
            raise ValueError(
                f"--shares {shares:g} is below this market's {min_shares:g}-share minimum"
            )
        merge_shares = 0.0
        sell_shares = _floor_shares(shares)

    est_price = sell_floor = 0.0
    if sell_shares > 0:
        try:
            est_price = estimate(position["token_id"], sell_shares)
        except InsufficientLiquidity as e:
            raise ValueError(
                f"The order book can't absorb {sell_shares:g} shares right now ({e}). "
                "Try a smaller --shares."
            ) from e
        sell_floor = compute_sell_floor(est_price, slippage_pct)
        if sell_shares * sell_floor < MIN_ORDER_USD:
            raise ValueError(
                f"The sell would bring under ${MIN_ORDER_USD:g} (Polymarket minimum order value)"
            )

    return SellPlan(
        position_id=pid,
        market_id=market.id,
        question=market.question,
        condition_id=market.condition_id,
        protocol=market.version,
        side=position["position"],
        token_id=position["token_id"],
        held_shares=held_shares,
        held_other=held_other,
        merge_shares=merge_shares,
        sell_shares=sell_shares,
        est_price=est_price,
        sell_floor=sell_floor,
        est_proceeds=round(merge_shares + sell_shares * sell_floor, 4),
        full_exit=full_exit,
    )


def execute_sell(client, plan: SellPlan, storage: PositionStorage) -> SellResult:
    """Merge pairs (if any), sell on the book, record what happened."""
    result = SellResult(plan=plan)

    if plan.merge_shares > 0:
        handle = client.merge_positions(
            condition_id=plan.condition_id,
            amount=int(round(plan.merge_shares * 1e6)),  # base units
        )
        result.merge_tx = handle.wait().transaction_hash

    if plan.sell_shares > 0:
        try:
            resp = client.place_market_order(
                asset_id=plan.token_id,
                side="SELL",
                shares=plan.sell_shares,
                min_price=plan.sell_floor,
                order_type="FAK",
                builder_code=builder_code(),
            )
            if getattr(resp, "ok", False):
                result.sell_order_id = resp.order_id
                result.sold_shares = float(resp.making_amount)
                result.proceeds = float(resp.taking_amount)
                if result.sold_shares < plan.sell_shares - DUST_SHARES:
                    result.error = (
                        f"Partial fill: sold {result.sold_shares:g} of {plan.sell_shares:g}"
                    )
            else:
                result.error = getattr(resp, "message", None) or "Order rejected"
        except Exception as e:
            if result.merge_tx is None:
                raise  # nothing changed yet
            result.error = str(e)  # merge already happened; report, don't raise

    if result.merge_tx is None and result.sold_shares == 0:
        return result  # nothing happened (e.g. order rejected); nothing to record
    remaining = plan.held_shares - plan.merge_shares - result.sold_shares
    # Keep the position open while either side still has shares, so `redeem`
    # still sees what's left.
    other_left = plan.held_other - plan.merge_shares
    storage.record_exit(
        plan.position_id,
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "merged_shares": plan.merge_shares,
            "merge_tx": result.merge_tx,
            "sold_shares": result.sold_shares,
            "proceeds": result.proceeds,
            "order_id": result.sell_order_id,
        },
        close=plan.full_exit and remaining < DUST_SHARES and other_left < DUST_SHARES,
    )
    return result


def _find_position(storage: PositionStorage, prefix: str) -> dict:
    matches = [p for p in storage.load_all() if p["position_id"].startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Expected one position matching {prefix!r}, found {len(matches)}")
    return matches[0]


def _sdk_estimator() -> PriceEstimator:
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


def _print_plan(plan: SellPlan) -> None:
    other = "NO" if plan.side == "YES" else "YES"
    print(f"Market:   {plan.question}")
    extra = f" + {plan.held_other:g} {other}" if plan.held_other else ""
    print(f"Holding:  {plan.held_shares:g} {plan.side}{extra}")
    step = 1
    if plan.merge_shares:
        n = f"{plan.merge_shares:g}"
        print(f"Step {step}:   merge {n} {plan.side} + {n} {other} -> {n} pUSD")
        step += 1
    if plan.sell_shares:
        print(
            f"Step {step}:   sell {plan.sell_shares:g} {plan.side} at >= {plan.sell_floor:.2f} "
            f"(book now fills at ~{plan.est_price:.3f})"
        )
    print(f"Estimate: >= ${plan.est_proceeds:.2f} pUSD back")
    left = plan.held_shares - plan.merge_shares - plan.sell_shares
    if left >= DUST_SHARES:
        print(f"Keeps:    {left:g} {plan.side}")
    other_left = plan.held_other - plan.merge_shares
    if other_left >= DUST_SHARES:
        print(f"Keeps:    {other_left:g} {other} (redeem it if {other} wins)")


def cmd_sell(args) -> int:
    storage = PositionStorage()
    manager = WalletManager()
    try:
        position = _find_position(storage, args.position_id)
        if not manager.deposit_wallet:
            raise ValueError("No deposit wallet. Run `polyclaw wallet setup` first.")
        market = asyncio.run(GammaClient().get_market(position["market_id"]))
        protocol = position.get("protocol", "v1")
        held = manager.token_balance(manager.deposit_wallet, position["token_id"], protocol)
        other_id = position.get("unwanted_token_id")
        held_other = (
            manager.token_balance(manager.deposit_wallet, other_id, protocol) if other_id else 0.0
        )
        plan = plan_sell(
            position,
            market,
            held,
            held_other,
            _sdk_estimator(),
            shares=args.shares,
            slippage_pct=args.slippage,
        )
    except (ValueError, ConfigError) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:
        print(f"Error: could not prepare the sell: {e}", file=sys.stderr)
        return EXIT_REFUSED

    if not args.yes:
        if args.json:
            print(json.dumps({"executed": False, "plan": asdict(plan)}, indent=2))
        else:
            _print_plan(plan)
            print("\nPREVIEW ONLY - nothing was sold. Re-run with --yes to execute.")
        return EXIT_OK

    try:
        client = create_client()
        result = execute_sell(client, plan, storage)
    except ConfigError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Run the preview again to see what the deposit wallet holds now.", file=sys.stderr)
        return EXIT_REFUSED

    if result.error and result.merge_tx is None and result.sold_shares == 0:
        print(f"Refused by the exchange, nothing was sold: {result.error}", file=sys.stderr)
        return EXIT_REFUSED

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        _print_plan(plan)
        if result.merge_tx:
            print(f"\nMerge tx:   {result.merge_tx}")
        if plan.sell_shares:
            print(
                f"Sell order: {result.sell_order_id or '-'} sold {result.sold_shares:g} "
                f"for ${result.proceeds:.2f}"
            )
        if result.error:
            print(f"\nWARNING: {result.error}")
            print("Unsold shares stay in the deposit wallet; run `polyclaw sell` to retry.")
    return EXIT_PARTIAL if result.error else EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Sell (exit) a position")
    parser.add_argument("position_id", help="Position ID (prefix is enough)")
    parser.add_argument(
        "--shares", type=float, help="Sell only this many shares (default: full exit)"
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=DEFAULT_SLIPPAGE_PCT,
        help="Max %% below the current book price (1-50, default 5)",
    )
    parser.add_argument("--yes", action="store_true", help="Execute (default: preview only)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    return cmd_sell(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
