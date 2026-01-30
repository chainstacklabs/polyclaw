"""Position storage - JSON file with atomic writes."""

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

def get_storage_dir() -> Path:
    """Get the storage directory for PolyClaw data."""
    storage_dir = Path.home() / ".openclaw" / "polyclaw"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


POSITIONS_FILE = get_storage_dir() / "positions.json"

# Global lock for thread-safe file operations
_storage_lock = threading.Lock()


@dataclass
class PositionEntry:
    """Position entry stored in JSON file."""

    position_id: str

    # Market info
    market_id: str
    question: str
    position: str  # YES or NO
    token_id: str

    # Entry data
    entry_time: str  # ISO timestamp
    entry_amount: float  # USD spent on split
    entry_price: float  # Price at time of purchase

    # Transaction records
    split_tx: str
    clob_order_id: Optional[str] = None
    clob_filled: bool = False

    # Status
    status: str = "open"  # open, closed, resolved
    notes: Optional[str] = None


class PositionStorage:
    """Manage positions.json file with atomic writes."""

    def __init__(self, path: Path = POSITIONS_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[dict]:
        """Load all positions from JSON file."""
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return []

    def save_all(self, positions: list[dict]) -> None:
        """Atomic write all positions to file."""
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(positions, indent=2))
        temp.replace(self.path)

    def add(self, entry: PositionEntry) -> None:
        """Add new position entry (thread-safe)."""
        with _storage_lock:
            positions = self.load_all()
            positions.append(asdict(entry))
            self.save_all(positions)

    def get(self, position_id: str) -> Optional[dict]:
        """Get position by ID."""
        positions = self.load_all()
        for p in positions:
            if p.get("position_id") == position_id:
                return p
        return None

    def get_by_market(self, market_id: str) -> list[dict]:
        """Get all positions for a market."""
        positions = self.load_all()
        return [p for p in positions if p.get("market_id") == market_id]

    def get_open(self) -> list[dict]:
        """Get all open positions."""
        positions = self.load_all()
        return [p for p in positions if p.get("status") == "open"]

    def update_status(self, position_id: str, status: str) -> bool:
        """Update position status (thread-safe)."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p["status"] = status
                    self.save_all(positions)
                    return True
            return False

    def update_notes(self, position_id: str, notes: str) -> bool:
        """Update position notes (thread-safe)."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p["notes"] = notes
                    self.save_all(positions)
                    return True
            return False

    def delete(self, position_id: str) -> bool:
        """Delete position by ID (thread-safe)."""
        with _storage_lock:
            positions = self.load_all()
            filtered = [p for p in positions if p.get("position_id") != position_id]
            if len(filtered) < len(positions):
                self.save_all(filtered)
                return True
            return False

    def count(self) -> int:
        """Get total position count."""
        return len(self.load_all())
