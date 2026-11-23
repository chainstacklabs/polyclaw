"""Polymarket Gamma API client for market browsing."""

import json
from dataclasses import dataclass
from typing import Optional

import httpx


GAMMA_API_BASE = "https://gamma-api.polymarket.com"


@dataclass
class Market:
    """Polymarket market data."""

    id: str
    question: str
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: Optional[str]
    yes_price: float
    no_price: float
    volume: float
    volume_24h: float
    liquidity: float
    end_date: str
    active: bool
    closed: bool
    resolved: bool
    outcome: Optional[str]
    # Protocol: "v1" = CTF market (clobTokenIds) — the only kind polyclaw
    # trades. "v2" = Polymarket Protocol V2 (positionIds, ExchangeV3).
    version: Optional[str] = None
    # Minimum order size in shares (Gamma `orderMinSize`).
    order_min_size: Optional[float] = None
    accepting_orders: bool = True


@dataclass
class MarketGroup:
    """Polymarket event/group containing multiple markets."""

    id: str
    title: str
    slug: str
    description: str
    markets: list[Market]


SUPPORTED_VERSIONS = ("v1", "v2")


def outcome_token_ids(data: dict) -> list[str]:
    """Trading IDs of a market's outcomes [YES, NO], chosen by its protocol.

    Per Polymarket's V2 migration guide, pick the field by `version` even when
    both are present: "v1" -> `clobTokenIds` (JSON-encoded string, CTF token
    IDs), "v2" -> `positionIds` (list, PositionManager IDs). Unknown or
    missing versions give no IDs, so the market can't be traded by mistake.
    """
    version = data.get("version")
    if version == "v2":
        raw = data.get("positionIds") or []
    elif version == "v1":
        raw = data.get("clobTokenIds") or "[]"
    else:
        return []
    ids = json.loads(raw) if isinstance(raw, str) else raw
    return [str(i) for i in ids if str(i).isdigit()]


class GammaClient:
    """HTTP client for Polymarket Gamma API."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def get_trending_markets(self, limit: int = 20) -> list[Market]:
        """Get trending markets by volume."""
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                f"{GAMMA_API_BASE}/markets",
                params={
                    "closed": "false",
                    "limit": limit,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            return [self._parse_market(m) for m in resp.json()]

    async def search_markets(self, query: str, limit: int = 20) -> list[Market]:
        """Search open markets by keyword (server-side, Gamma /public-search).

        Returns markets from matching active events, highest 24h volume first.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                f"{GAMMA_API_BASE}/public-search",
                params={
                    "q": query,
                    "events_status": "active",
                    "keep_closed_markets": 0,
                    "limit_per_type": max(limit, 20),
                },
            )
            resp.raise_for_status()
            events = resp.json().get("events") or []

        markets = [
            m
            for event in events
            for m in event.get("markets") or []
            if m.get("active", True) and not m.get("closed", False)
        ]
        markets.sort(key=lambda m: float(m.get("volume24hr") or 0), reverse=True)
        return [self._parse_market(m) for m in markets[:limit]]

    async def get_market(self, market_id: str) -> Market:
        """Get market by ID."""
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(f"{GAMMA_API_BASE}/markets/{market_id}")
            resp.raise_for_status()
            return self._parse_market(resp.json())

    async def get_market_by_slug(self, slug: str) -> Market:
        """Get market by slug."""
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                f"{GAMMA_API_BASE}/markets",
                params={"slug": slug},
            )
            resp.raise_for_status()
            markets = resp.json()
            if not markets:
                raise ValueError(f"Market not found: {slug}")
            return self._parse_market(markets[0])

    async def get_events(self, limit: int = 20) -> list[MarketGroup]:
        """Get events/groups with their markets."""
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                f"{GAMMA_API_BASE}/events",
                params={
                    "closed": "false",
                    "limit": limit,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            return [self._parse_event(e) for e in resp.json()]

    def _parse_market(self, data: dict) -> Market:
        """Parse market JSON into Market dataclass."""
        clob_tokens = outcome_token_ids(data)
        prices = json.loads(data.get("outcomePrices", "[0.5, 0.5]"))

        return Market(
            id=data.get("id", ""),
            question=data.get("question", ""),
            slug=data.get("slug", ""),
            condition_id=data.get("conditionId", ""),
            yes_token_id=clob_tokens[0] if clob_tokens else "",
            no_token_id=clob_tokens[1] if len(clob_tokens) > 1 else None,
            yes_price=float(prices[0]) if prices else 0.5,
            no_price=float(prices[1]) if len(prices) > 1 else 0.5,
            volume=float(data.get("volume", 0) or 0),
            volume_24h=float(data.get("volume24hr", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            end_date=data.get("endDate", ""),
            active=data.get("active", True),
            closed=data.get("closed", False),
            resolved=data.get("resolved", False),
            outcome=data.get("outcome"),
            version=data.get("version"),
            order_min_size=(
                float(data["orderMinSize"]) if data.get("orderMinSize") else None
            ),
            accepting_orders=bool(data.get("acceptingOrders", True)),
        )

    def _parse_event(self, data: dict) -> MarketGroup:
        """Parse event JSON into MarketGroup dataclass."""
        markets_data = data.get("markets", [])
        return MarketGroup(
            id=data.get("id", ""),
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            markets=[self._parse_market(m) for m in markets_data],
        )
