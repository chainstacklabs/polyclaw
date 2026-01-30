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


@dataclass
class MarketGroup:
    """Polymarket event/group containing multiple markets."""

    id: str
    title: str
    slug: str
    description: str
    markets: list[Market]


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
        """Search markets by keyword.

        Note: Gamma API doesn't support server-side text search,
        so we fetch a larger batch and filter client-side.
        """
        # Fetch more markets to search through
        fetch_limit = max(500, limit * 10)

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                f"{GAMMA_API_BASE}/markets",
                params={
                    "closed": "false",
                    "limit": fetch_limit,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()

            # Client-side filter by query in question or slug
            query_lower = query.lower()
            matches = []
            for m in resp.json():
                question = m.get("question", "").lower()
                slug = m.get("slug", "").lower()
                if query_lower in question or query_lower in slug:
                    matches.append(self._parse_market(m))
                    if len(matches) >= limit:
                        break

            return matches

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

    async def get_prices(self, token_ids: list[str]) -> dict[str, float]:
        """Get current prices for token IDs."""
        if not token_ids:
            return {}

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.get(
                "https://clob.polymarket.com/prices",
                params={"token_ids": ",".join(token_ids)},
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_market(self, data: dict) -> Market:
        """Parse market JSON into Market dataclass."""
        clob_tokens = json.loads(data.get("clobTokenIds", "[]"))
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
