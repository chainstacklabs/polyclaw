"""Position P&L from what was paid, what came back, and what's held now.

  cost      = pUSD split at buy time - what the other side sold for
  realized  = pUSD back from `sell` (sales + merges) and `redeem`
  value     = shares held now x current price, both sides; once the market
              has resolved on-chain, the recorded shares x their payout
              (Polymarket's auto-redeem may already have paid them out)
  P&L       = value + realized - cost

Holdings come from the chain when a balance reader is available and from
the recorded trades otherwise. Positions recorded before polyclaw 0.3.0
lack the buy's sale proceeds; their cost uses the preview estimate and is
flagged `cost_estimated`.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionPnL:
    shares: float  # your side, held now
    other_shares: float  # the other side, held now (leftover from the buy)
    price: float  # current price of your side
    other_price: float
    cost: float
    cost_estimated: bool
    realized: float
    value: float
    pnl: float
    pnl_pct: float
    holdings_source: str  # "chain" or "recorded"


def recorded_holdings(position: dict) -> tuple[float, float]:
    """(your side, other side) implied by the recorded buy and exits."""
    exits = position.get("exits") or []
    amount = float(position.get("entry_amount") or 0)
    merged = sum(float(e.get("merged_shares") or 0) for e in exits)
    sold = sum(float(e.get("sold_shares") or 0) for e in exits)
    shares = amount - merged - sold
    other = 0.0
    if position.get("unwanted_token_id"):
        other = amount - float(position.get("sold_shares") or 0) - merged
    if position.get("status") == "lost" or position.get("redeem_payout") is not None:
        shares = other = 0.0  # paid out (or worthless); the payout is in `realized`
    return max(shares, 0.0), max(other, 0.0)


def position_pnl(
    position: dict,
    yes_price: float,
    no_price: float,
    balances: Optional[tuple[float, float]] = None,
    payout: Optional[tuple[float, float]] = None,
) -> PositionPnL:
    """P&L for one stored position.

    `balances` = (your side, other side) read from the chain, or None to use
    the recorded trades. Prices are Gamma's outcome prices. `payout` =
    (your side, other side) pUSD per share once the market resolved on-chain
    (see lib/payout.py); it replaces both.
    """
    yes_side = position.get("position") == "YES"
    price, other_price = (yes_price, no_price) if yes_side else (no_price, yes_price)

    amount = float(position.get("entry_amount") or 0)
    proceeds = position.get("sell_proceeds")
    if proceeds is not None:
        cost, estimated = amount - float(proceeds), False
    else:
        cost, estimated = amount * float(position.get("entry_price") or 0), True

    exits = position.get("exits") or []
    realized = sum(
        float(e.get("proceeds") or 0) + float(e.get("merged_shares") or 0) for e in exits
    )
    realized += float(position.get("redeem_payout") or 0)

    # Redeemed before polyclaw 0.3.0 recorded payouts: the chain shows 0, so
    # value the recorded shares at the resolved prices instead.
    legacy_redeem = position.get("redeem_tx") and position.get("redeem_payout") is None
    if payout is not None:
        (shares, other), source = recorded_holdings(position), "resolved"
        price, other_price = payout
    elif balances is not None and not legacy_redeem:
        shares, other, source = balances[0], balances[1], "chain"
    else:
        (shares, other), source = recorded_holdings(position), "recorded"

    value = shares * price + other * other_price
    pnl = value + realized - cost
    return PositionPnL(
        shares=round(shares, 6),
        other_shares=round(other, 6),
        price=price,
        other_price=other_price,
        cost=round(cost, 4),
        cost_estimated=estimated,
        realized=round(realized, 4),
        value=round(value, 4),
        pnl=round(pnl, 4),
        pnl_pct=round(pnl / cost * 100, 2) if cost > 0 else 0.0,
        holdings_source=source,
    )
