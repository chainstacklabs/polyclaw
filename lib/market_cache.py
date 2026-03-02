"""Cache for conditionId-to-market metadata resolution."""

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from lib.storage import get_storage_dir

if TYPE_CHECKING:
    from lib.gamma_client import GammaClient


@dataclass
class MarketCacheEntry:
    """Cached market metadata keyed by conditionId."""

    condition_id: str  # Key
    market_id: str  # Gamma API id
    question: str  # Human-readable question
    slug: str  # URL slug
    yes_token_id: str  # YES outcome token ID
    no_token_id: str  # NO outcome token ID
    cached_at: str  # ISO 8601 timestamp


class MarketCacheError(Exception):
    """Raised when market metadata cannot be resolved."""

    pass


class MarketCache:
    """Disk-backed cache mapping conditionId to market metadata."""

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = cache_path or get_storage_dir() / "market_cache.json"
        self._cache: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        """Read JSON file, return dict of conditionId -> entry dict."""
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        """Write the internal cache dict to disk as JSON."""
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2), encoding="utf-8"
        )

    def get(self, condition_id: str) -> Optional[MarketCacheEntry]:
        """Look up a cached entry by conditionId."""
        entry = self._cache.get(condition_id)
        if entry is None:
            return None
        return MarketCacheEntry(**entry)

    def put(self, condition_id: str, entry: MarketCacheEntry) -> None:
        """Store entry and persist to disk immediately."""
        self._cache[condition_id] = asdict(entry)
        self._save()

    async def resolve(
        self, condition_id: str, gamma: "GammaClient"
    ) -> MarketCacheEntry:
        """Resolve a conditionId to market metadata, using cache when available."""
        cached = self.get(condition_id)
        if cached is not None:
            return cached

        try:
            market = await gamma.get_market_by_condition(condition_id)
        except Exception as e:
            raise MarketCacheError(
                f"Failed to resolve market for conditionId {condition_id}: {e}"
            ) from e

        entry = MarketCacheEntry(
            condition_id=condition_id,
            market_id=market.id,
            question=market.question,
            slug=market.slug,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id or "",
            cached_at=datetime.now(timezone.utc).isoformat(),
        )
        self.put(condition_id, entry)
        return entry

    async def resolve_batch(
        self, condition_ids: list[str], gamma: "GammaClient"
    ) -> dict[str, MarketCacheEntry]:
        """Resolve multiple conditionIds, skipping individual failures."""
        results: dict[str, MarketCacheEntry] = {}
        for condition_id in condition_ids:
            try:
                results[condition_id] = await self.resolve(condition_id, gamma)
            except MarketCacheError:
                continue
        return results
