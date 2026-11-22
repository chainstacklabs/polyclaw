#!/usr/bin/env python3
"""Buy a position: split pUSD into YES + NO, then sell the side you don't want.

Runs on the deposit wallet through the official SDK (gasless). Without
``--yes`` it only prints a preview and spends nothing.

Exit codes: 0 = done (or preview), 1 = refused / failed before spending,
3 = split done but the unwanted side was not (fully) sold.
"""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import GammaClient, Market  # noqa: E402
from lib.position_storage import PositionEntry, PositionStorage  # noqa: E402
from lib.trading import (  # noqa: E402
    DEFAULT_MIN_ORDER_SHARES,
    MIN_ORDER_USD,
    ConfigError,
    builder_code,
    compute_sell_floor,
    create_client,
    max_trade_usd,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_PARTIAL = 3


@dataclass
class BuyPlan:
    market_id: str
    question: str
    condition_id: str
    side: str
    amount: float  # pUSD to split = shares of each side minted
    wanted_token_id: str
    unwanted_token_id: str
    wanted_price: float
    unwanted_price: float
    sell_unwanted: bool
    sell_floor: float  # worst price accepted for the unwanted side
    est_recovered: float  # pUSD back if the unwanted side sells at the floor
    est_cost: float  # amount - est_recovered
    est_entry_price: float  # est_cost per wanted share


@dataclass
class BuyResult:
    executed: bool
    plan: BuyPlan
    split_tx: Optional[str] = None
    sell_order_id: Optional[str] = None
    sold_shares: float = 0.0
    sell_error: Optional[str] = None
    position_id: Optional[str] = None


def plan_buy(
    market: Market,
    side: str,
    amount: float,
    *,
    max_usd: float,
    skip_sell: bool = False,
    slippage_pct: float = 10.0,
) -> BuyPlan:
    """Validate a buy and work out what it will do. Raises ValueError if refused."""
    side = side.upper()
    if side not in ("YES", "NO"):
        raise ValueError("Side must be YES or NO")
    if amount <= 0:
        raise ValueError("Amount must be greater than 0")
    if amount > max_usd:
        raise ValueError(
            f"Amount ${amount:.2f} is above the ${max_usd:.2f} limit "
            "(POLYCLAW_MAX_TRADE_USD)"
        )
    if market.version != "v1":
        raise ValueError(
            f"Market {market.id} is on protocol {market.version!r}; polyclaw only "
            "trades CTF (v1) markets for now"
        )
    if market.closed or not market.active or not market.accepting_orders:
        raise ValueError(f"Market {market.id} is not open for trading")
    if not market.condition_id or not market.yes_token_id or not market.no_token_id:
        raise ValueError(f"Market {market.id} is missing token or condition data")

    wanted, unwanted = (
        (market.yes_token_id, market.no_token_id)
        if side == "YES"
        else (market.no_token_id, market.yes_token_id)
    )
    wanted_price, unwanted_price = (
        (market.yes_price, market.no_price)
        if side == "YES"
        else (market.no_price, market.yes_price)
    )

    sell_floor = compute_sell_floor(unwanted_price, slippage_pct)
    if not skip_sell:
        min_shares = market.order_min_size or DEFAULT_MIN_ORDER_SHARES
        if amount < min_shares:
            raise ValueError(
                f"${amount:.2f} mints {amount:g} shares of each side, below this "
                f"market's {min_shares:g}-share minimum order. Use at least "
                f"${min_shares:g}, or --skip-sell to keep both sides."
            )
        if amount * sell_floor < MIN_ORDER_USD:
            raise ValueError(
                f"The unwanted side would sell for under ${MIN_ORDER_USD:g} "
                "(Polymarket minimum). Use a larger amount or --skip-sell."
            )

    recovered = 0.0 if skip_sell else round(amount * sell_floor, 4)
    cost = round(amount - recovered, 4)
    return BuyPlan(
        market_id=market.id,
        question=market.question,
        condition_id=market.condition_id,
        side=side,
        amount=amount,
        wanted_token_id=wanted,
        unwanted_token_id=unwanted,
        wanted_price=wanted_price,
        unwanted_price=unwanted_price,
        sell_unwanted=not skip_sell,
        sell_floor=sell_floor,
        est_recovered=recovered,
        est_cost=cost,
        est_entry_price=round(cost / amount, 4),
    )


def execute_buy(client, plan: BuyPlan, storage: PositionStorage) -> BuyResult:
    """Split on the deposit wallet, sell the unwanted side, record the position."""
    result = BuyResult(executed=True, plan=plan)

    handle = client.split_position(
        condition_id=plan.condition_id,
        amount=int(round(plan.amount * 1e6)),  # pUSD base units
    )
    result.split_tx = handle.wait().transaction_hash

    if plan.sell_unwanted:
        try:
            resp = client.place_market_order(
                asset_id=plan.unwanted_token_id,
                side="SELL",
                shares=plan.amount,
                min_price=plan.sell_floor,
                order_type="FAK",
                builder_code=builder_code(),
            )
            if getattr(resp, "ok", False):
                result.sell_order_id = resp.order_id
                result.sold_shares = float(resp.making_amount)
                if result.sold_shares < plan.amount:
                    result.sell_error = (
                        f"Partial fill: sold {result.sold_shares:g} of {plan.amount:g}"
                    )
            else:
                result.sell_error = getattr(resp, "message", None) or "Order rejected"
        except Exception as e:  # split already happened; report, don't raise
            result.sell_error = str(e)

    entry = PositionEntry(
        position_id=str(uuid.uuid4()),
        market_id=plan.market_id,
        question=plan.question,
        position=plan.side,
        token_id=plan.wanted_token_id,
        entry_time=datetime.now(timezone.utc).isoformat(),
        entry_amount=plan.amount,
        entry_price=plan.est_entry_price,
        split_tx=result.split_tx,
        clob_order_id=result.sell_order_id,
        clob_filled=result.sold_shares > 0,
        condition_id=plan.condition_id,
        holder="deposit",
        unwanted_token_id=plan.unwanted_token_id,
        sold_shares=result.sold_shares,
    )
    storage.add(entry)
    result.position_id = entry.position_id
    return result


def _print_plan(plan: BuyPlan) -> None:
    other = "NO" if plan.side == "YES" else "YES"
    print(f"Market:   {plan.question}")
    print(f"Buy:      {plan.side} with ${plan.amount:.2f} pUSD (deposit wallet)")
    print(f"Step 1:   split ${plan.amount:.2f} -> {plan.amount:g} YES + {plan.amount:g} NO")
    if plan.sell_unwanted:
        print(
            f"Step 2:   sell {plan.amount:g} {other} at >= {plan.sell_floor:.2f} "
            f"(now ~{plan.unwanted_price:.2f})"
        )
        print(
            f"Estimate: cost <= ${plan.est_cost:.2f}, "
            f"entry <= {plan.est_entry_price:.4f} per {plan.side} share"
        )
    else:
        print(f"Step 2:   skipped (--skip-sell): you keep {plan.amount:g} {other}")


async def cmd_buy(args) -> int:
    try:
        market = await GammaClient().get_market(args.market_id)
        plan = plan_buy(
            market,
            args.position,
            args.amount,
            max_usd=max_trade_usd(),
            skip_sell=args.skip_sell,
            slippage_pct=args.slippage,
        )
    except (ValueError, ConfigError) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:
        print(f"Error: could not load market {args.market_id}: {e}", file=sys.stderr)
        return EXIT_REFUSED

    if not args.yes:
        if args.json:
            print(json.dumps({"executed": False, "plan": asdict(plan)}, indent=2))
        else:
            _print_plan(plan)
            print("\nPREVIEW ONLY - nothing was spent. Re-run with --yes to execute.")
        return EXIT_OK

    try:
        client = create_client()
    except ConfigError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as e:
        print(f"Error: could not connect the deposit wallet: {e}", file=sys.stderr)
        return EXIT_REFUSED

    try:
        result = execute_buy(client, plan, PositionStorage())
    except Exception as e:
        print(f"Split failed, nothing was bought: {e}", file=sys.stderr)
        return EXIT_REFUSED

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        _print_plan(plan)
        print(f"\nSplit tx:    {result.split_tx}")
        if plan.sell_unwanted:
            print(f"Sell order:  {result.sell_order_id or '-'} sold {result.sold_shares:g}")
        print(f"Position ID: {result.position_id}")
        if result.sell_error:
            other = "NO" if plan.side == "YES" else "YES"
            left = plan.amount - result.sold_shares
            print(f"\nWARNING: {result.sell_error}")
            print(f"You still hold {left:g} {other} in the deposit wallet (hedged, not lost).")
    return EXIT_PARTIAL if result.sell_error else EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Buy a Polymarket position")
    sub = parser.add_subparsers(dest="command")
    buy = sub.add_parser("buy", help="Buy YES or NO on a market")
    buy.add_argument("market_id", help="Gamma market ID")
    buy.add_argument("position", type=str.upper, choices=["YES", "NO"])
    buy.add_argument("amount", type=float, help="pUSD to spend on the split")
    buy.add_argument("--yes", action="store_true", help="Execute (default: preview only)")
    buy.add_argument("--skip-sell", action="store_true", help="Keep both sides")
    buy.add_argument(
        "--slippage",
        type=float,
        default=10.0,
        help="Max %% below the current price for the unwanted-side sell (1-50, default 10)",
    )
    buy.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    if args.command == "buy":
        return asyncio.run(cmd_buy(args))
    parser.print_help()
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
