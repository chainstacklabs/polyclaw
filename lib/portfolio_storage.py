"""Portfolio snapshot and rules storage."""

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from lib.position_storage import get_storage_dir

SNAPSHOTS_FILE = get_storage_dir() / "snapshots.jsonl"
RULES_FILE = get_storage_dir() / "portfolio_rules.json"

_snapshot_lock = threading.Lock()

DEFAULT_RULES = {
    "max_position_pct": 15,
    "max_portfolio_exposure_pct": 75,
    "min_cash_reserve_pct": 25,
    "max_positions": 8,
    "min_position_usd": 1.00,
    "min_volume_24h": 50000,
}


@dataclass
class PortfolioSnapshot:
    """Point-in-time portfolio snapshot."""

    timestamp: str
    total_value_usd: float
    cash_usd: float
    positions_usd: float
    position_count: int
    pol_balance: float
    cash_pct: float
    positions_pct: float


class PortfolioStorage:
    """Snapshot and rules I/O."""

    def __init__(self, snapshots_path: Path = SNAPSHOTS_FILE,
                 rules_path: Path = RULES_FILE):
        self.snapshots_path = snapshots_path
        self.rules_path = rules_path
        self.snapshots_path.parent.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        """Append a snapshot to JSONL (thread-safe)."""
        with _snapshot_lock:
            with open(self.snapshots_path, "a") as f:
                f.write(json.dumps(asdict(snapshot)) + "\n")

    def load_snapshots(self, limit: Optional[int] = None) -> list[PortfolioSnapshot]:
        """Load snapshots, oldest first."""
        if not self.snapshots_path.exists():
            return []

        entries = []
        text = self.snapshots_path.read_text().strip()
        if not text:
            return []

        for line in text.split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    entries.append(PortfolioSnapshot(**data))
                except (json.JSONDecodeError, TypeError):
                    continue

        if limit is not None:
            entries = entries[-limit:]

        return entries

    def load_rules(self) -> dict:
        """Load portfolio rules or return defaults."""
        if self.rules_path.exists():
            try:
                return json.loads(self.rules_path.read_text())
            except json.JSONDecodeError:
                pass
        return dict(DEFAULT_RULES)

    def save_rules(self, rules: dict) -> None:
        """Save portfolio rules."""
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.rules_path.write_text(json.dumps(rules, indent=2))
