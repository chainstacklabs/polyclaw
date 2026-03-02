"""Shared storage utilities."""

from pathlib import Path


def get_storage_dir() -> Path:
    """Get the storage directory for PolyClaw data."""
    storage_dir = Path.home() / ".openclaw" / "polyclaw"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir
