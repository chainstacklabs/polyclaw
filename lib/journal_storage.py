"""Trade journal storage - append-only JSONL file."""

import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.position_storage import get_storage_dir

JOURNAL_FILE = get_storage_dir() / "journal.jsonl"

_journal_lock = threading.Lock()


@dataclass
class JournalEntry:
    """Single journal entry."""

    id: str
    timestamp: str
    type: str  # open, close, redeem, merge, deposit, swap, note
    market_id: Optional[str] = None
    position_id: Optional[str] = None
    side: Optional[str] = None
    amount_usd: Optional[float] = None
    price: Optional[float] = None
    tx_hash: Optional[str] = None
    pnl: Optional[float] = None
    notes: Optional[str] = None


class JournalStorage:
    """Append-only JSONL journal operations."""

    def __init__(self, path: Path = JOURNAL_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: JournalEntry) -> None:
        """Append a journal entry (thread-safe)."""
        with _journal_lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(asdict(entry)) + "\n")

    def load_all(self, limit: Optional[int] = None) -> list[JournalEntry]:
        """Load journal entries, most recent first."""
        if not self.path.exists():
            return []

        entries = []
        text = self.path.read_text().strip()
        if not text:
            return []

        for line in text.split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    entries.append(JournalEntry(**data))
                except (json.JSONDecodeError, TypeError):
                    continue

        entries.sort(key=lambda e: e.timestamp, reverse=True)

        if limit is not None:
            entries = entries[:limit]

        return entries

    @staticmethod
    def create_entry(
        entry_type: str,
        market_id: Optional[str] = None,
        position_id: Optional[str] = None,
        side: Optional[str] = None,
        amount_usd: Optional[float] = None,
        price: Optional[float] = None,
        tx_hash: Optional[str] = None,
        pnl: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> JournalEntry:
        """Create a new journal entry with auto-generated id and timestamp."""
        return JournalEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=entry_type,
            market_id=market_id,
            position_id=position_id,
            side=side,
            amount_usd=amount_usd,
            price=price,
            tx_hash=tx_hash,
            pnl=pnl,
            notes=notes,
        )
