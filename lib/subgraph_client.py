"""Polymarket Goldsky subgraph client for activity and PnL queries."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


ACTIVITY_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn"
)
PNL_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn"
)


class SubgraphError(Exception):
    """Raised when subgraph query fails."""

    pass


@dataclass
class TradeEvent:
    """On-chain trade event from the activity subgraph."""

    id: str
    event_type: str
    timestamp: str
    wallet: str
    condition_id: str
    amount_usdc: float
    tx_hash: str
    index_sets: list[int] = field(default_factory=list)
    question: str = ""
    market_id: str = ""


@dataclass
class UserPosition:
    """User position from the PnL subgraph."""

    id: str
    token_id: str
    amount: float
    avg_price: float
    realized_pnl: float
    total_bought: float
    condition_id: str = ""
    question: str = ""
    side: str = ""


class SubgraphClient:
    """HTTP client for Polymarket Goldsky subgraphs."""

    def __init__(self, wallet_address: str, timeout: float = 30.0):
        self.wallet_address = wallet_address.lower()
        self.timeout = timeout
        self._page_delay = 0.5  # seconds between paginated requests

    async def _query(
        self, url: str, query: str, variables: dict, retries: int = 2
    ) -> dict:
        """Execute a GraphQL query against a subgraph endpoint.

        Args:
            url: Subgraph endpoint URL.
            query: GraphQL query string.
            variables: Query variables dict.
            retries: Number of retries on transient failures (timeouts).

        Returns:
            The "data" dict from the response.

        Raises:
            SubgraphError: On network failure, non-200 status, or GraphQL errors.
        """
        last_error: Exception | None = None

        for attempt in range(1 + retries):
            if attempt > 0:
                await asyncio.sleep(1 * attempt)

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    resp = await http.post(
                        url,
                        json={"query": query, "variables": variables},
                    )
            except httpx.HTTPError as exc:
                last_error = SubgraphError(f"Subgraph request failed: {exc}")
                continue

            if resp.status_code != 200:
                raise SubgraphError(
                    f"Subgraph returned status {resp.status_code}: {resp.text}"
                )

            try:
                body = resp.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise SubgraphError(
                    f"Invalid JSON response: {resp.text[:200]}"
                ) from exc

            if "errors" in body:
                errors = body["errors"]
                msg = (
                    errors[0].get("message", str(errors))
                    if errors
                    else str(errors)
                )
                # Retry on timeout errors
                if "timeout" in msg.lower() and attempt < retries:
                    last_error = SubgraphError(f"Subgraph query error: {msg}")
                    continue
                raise SubgraphError(f"Subgraph query error: {msg}")

            return body["data"]

        raise last_error or SubgraphError("Subgraph query failed after retries")

    # ------------------------------------------------------------------
    # Activity subgraph methods
    # ------------------------------------------------------------------

    async def get_splits(self, limit: int = 1000) -> list[TradeEvent]:
        """Fetch split events for the wallet from the activity subgraph."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        query = """
        query GetSplits($wallet: String!, $limit: Int!, $cursor: String!) {
            splits(
                where: { stakeholder: $wallet, id_gt: $cursor }
                first: $limit
                orderBy: id
                orderDirection: asc
            ) {
                id
                timestamp
                stakeholder
                condition
                amount
            }
        }
        """
        events: list[TradeEvent] = []
        cursor = ""

        while True:
            data = await self._query(
                ACTIVITY_SUBGRAPH_URL,
                query,
                {"wallet": self.wallet_address, "limit": limit, "cursor": cursor},
            )
            splits = data.get("splits", [])
            for s in splits:
                events.append(
                    TradeEvent(
                        id=s["id"],
                        event_type="split",
                        timestamp=_unix_to_iso(s["timestamp"]),
                        wallet=s["stakeholder"].lower(),
                        condition_id=s["condition"],
                        amount_usdc=int(s["amount"]) / 1e6,
                        tx_hash=s["id"].split("_")[0],
                    )
                )
            if len(splits) < limit:
                break
            cursor = splits[-1]["id"]
            await asyncio.sleep(self._page_delay)

        return events

    async def get_merges(self, limit: int = 1000) -> list[TradeEvent]:
        """Fetch merge events for the wallet from the activity subgraph."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        query = """
        query GetMerges($wallet: String!, $limit: Int!, $cursor: String!) {
            merges(
                where: { stakeholder: $wallet, id_gt: $cursor }
                first: $limit
                orderBy: id
                orderDirection: asc
            ) {
                id
                timestamp
                stakeholder
                condition
                amount
            }
        }
        """
        events: list[TradeEvent] = []
        cursor = ""

        while True:
            data = await self._query(
                ACTIVITY_SUBGRAPH_URL,
                query,
                {"wallet": self.wallet_address, "limit": limit, "cursor": cursor},
            )
            merges = data.get("merges", [])
            for m in merges:
                events.append(
                    TradeEvent(
                        id=m["id"],
                        event_type="merge",
                        timestamp=_unix_to_iso(m["timestamp"]),
                        wallet=m["stakeholder"].lower(),
                        condition_id=m["condition"],
                        amount_usdc=int(m["amount"]) / 1e6,
                        tx_hash=m["id"].split("_")[0],
                    )
                )
            if len(merges) < limit:
                break
            cursor = merges[-1]["id"]
            await asyncio.sleep(self._page_delay)

        return events

    async def get_redemptions(self, limit: int = 1000) -> list[TradeEvent]:
        """Fetch redemption events for the wallet from the activity subgraph."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        query = """
        query GetRedemptions($wallet: String!, $limit: Int!, $cursor: String!) {
            redemptions(
                where: { redeemer: $wallet, id_gt: $cursor }
                first: $limit
                orderBy: id
                orderDirection: asc
            ) {
                id
                timestamp
                redeemer
                condition
                payout
                indexSets
            }
        }
        """
        events: list[TradeEvent] = []
        cursor = ""

        while True:
            data = await self._query(
                ACTIVITY_SUBGRAPH_URL,
                query,
                {"wallet": self.wallet_address, "limit": limit, "cursor": cursor},
            )
            redemptions = data.get("redemptions", [])
            for r in redemptions:
                index_sets_raw = r.get("indexSets", [])
                index_sets = [int(x) for x in index_sets_raw]
                events.append(
                    TradeEvent(
                        id=r["id"],
                        event_type="redemption",
                        timestamp=_unix_to_iso(r["timestamp"]),
                        wallet=r["redeemer"].lower(),
                        condition_id=r["condition"],
                        amount_usdc=int(r["payout"]) / 1e6,
                        tx_hash=r["id"].split("_")[0],
                        index_sets=index_sets,
                    )
                )
            if len(redemptions) < limit:
                break
            cursor = redemptions[-1]["id"]
            await asyncio.sleep(self._page_delay)

        return events

    async def get_all_events(self, limit: int = 1000) -> list[TradeEvent]:
        """Fetch all activity events concurrently and return deduplicated, sorted."""
        splits, merges, redemptions = await asyncio.gather(
            self.get_splits(limit=limit),
            self.get_merges(limit=limit),
            self.get_redemptions(limit=limit),
        )

        combined = splits + merges + redemptions

        # Deduplicate by id
        seen: set[str] = set()
        unique: list[TradeEvent] = []
        for event in combined:
            if event.id not in seen:
                seen.add(event.id)
                unique.append(event)

        # Sort by timestamp descending (most recent first)
        unique.sort(key=lambda e: e.timestamp, reverse=True)
        return unique

    # ------------------------------------------------------------------
    # PnL subgraph methods
    # ------------------------------------------------------------------

    async def get_positions(self, limit: int = 1000) -> list[UserPosition]:
        """Fetch all positions for the wallet from the PnL subgraph."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        query = """
        query GetPositions($wallet: String!, $limit: Int!, $cursor: String!) {
            userPositions(
                where: { user: $wallet, id_gt: $cursor }
                first: $limit
                orderBy: id
                orderDirection: asc
            ) {
                id
                tokenId
                amount
                avgPrice
                realizedPnl
                totalBought
            }
        }
        """
        positions: list[UserPosition] = []
        cursor = ""

        while True:
            data = await self._query(
                PNL_SUBGRAPH_URL,
                query,
                {"wallet": self.wallet_address, "limit": limit, "cursor": cursor},
            )
            items = data.get("userPositions", [])
            for p in items:
                positions.append(
                    UserPosition(
                        id=p["id"],
                        token_id=p["tokenId"],
                        amount=int(p["amount"]) / 1e6,
                        avg_price=int(p["avgPrice"]) / 1e6,
                        realized_pnl=int(p["realizedPnl"]) / 1e6,
                        total_bought=int(p["totalBought"]) / 1e6,
                    )
                )
            if len(items) < limit:
                break
            cursor = items[-1]["id"]
            await asyncio.sleep(self._page_delay)

        return positions

    async def get_open_positions(self, limit: int = 1000) -> list[UserPosition]:
        """Fetch only open positions (amount > 0) from the PnL subgraph.

        Uses client-side filtering because the subgraph's amount_gt filter
        causes query timeouts on Goldsky's infrastructure.
        """
        all_positions = await self.get_positions(limit=limit)
        return [p for p in all_positions if p.amount > 0]


def _unix_to_iso(unix_ts: str) -> str:
    """Convert a unix timestamp string to ISO 8601 format."""
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()
