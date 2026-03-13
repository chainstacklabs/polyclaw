"""Action logging for PolyClaw - records all operations to JSONL files.

Log files are stored at ~/.openclaw/polyclaw/logs/actions-YYYY-MM-DD.jsonl
Format: JSONL (one JSON object per line, easy to append and parse)
Rotation: Daily, with 30-day retention
"""

import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any


def get_storage_dir() -> Path:
    """Get the storage directory for PolyClaw data."""
    storage_dir = Path.home() / ".openclaw" / "polyclaw"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def get_logs_dir() -> Path:
    """Get the logs directory."""
    logs_dir = get_storage_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# Global lock for thread-safe log writes
_log_lock = threading.Lock()

# Log retention period in days
LOG_RETENTION_DAYS = 30


@dataclass
class ActionEntry:
    """A single action log entry."""

    timestamp: str        # ISO 8601
    action: str           # "markets.trending", "trade.buy", etc.
    params: dict          # Input parameters (sanitized)
    result: str           # "success" | "failure"
    details: dict         # Result details (tx_hash, order_id, error, etc.)
    duration_ms: int      # Execution time in milliseconds


def sanitize_value(value: Any, key: str = "") -> Any:
    """Sanitize a value for logging, masking sensitive data.

    Sensitive keys:
    - private_key, POLYCLAW_PRIVATE_KEY
    - password, secret
    - api_key, OPENROUTER_API_KEY
    - address, tx_hash, transaction, wallet identifiers
    """
    # Sensitive substrings to check in key names
    sensitive_substrings = [
        "private", "secret", "password", "api_key",
        "address", "tx_hash", "transaction", "tx",
        "clob_order_id", "wallet", "nonce",
    ]

    key_lower = key.lower()

    # Check if this key is sensitive
    if any(s in key_lower for s in sensitive_substrings):
        if isinstance(value, str) and len(value) > 10:
            # Show prefix and suffix
            if value.startswith("0x"):
                return f"{value[:8]}...{value[-6:]}"
            elif value.startswith("sk-"):
                return f"{value[:6]}...{value[-3:]}"
            else:
                return f"{value[:6]}...{value[-4:]}"

    return value


def sanitize_dict(d: dict) -> dict:
    """Recursively sanitize a dictionary for logging."""
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = sanitize_dict(value)
        elif isinstance(value, list):
            result[key] = [sanitize_dict(v) if isinstance(v, dict) else sanitize_value(v, key) for v in value]
        else:
            result[key] = sanitize_value(value, key)
    return result


def get_log_file_path(date: Optional[datetime] = None) -> Path:
    """Get the log file path for a specific date.

    Args:
        date: Date to get log file for. Defaults to today (UTC).

    Returns:
        Path to the log file.
    """
    if date is None:
        date = datetime.now(timezone.utc)

    filename = f"actions-{date.strftime('%Y-%m-%d')}.jsonl"
    return get_logs_dir() / filename


def cleanup_old_logs() -> None:
    """Delete log files older than LOG_RETENTION_DAYS."""
    logs_dir = get_logs_dir()
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)

    for log_file in logs_dir.glob("actions-*.jsonl"):
        try:
            # Extract date from filename
            date_str = log_file.stem.replace("actions-", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

            if file_date < cutoff:
                log_file.unlink()
        except (ValueError, OSError):
            # Ignore files with invalid names or deletion errors
            pass


def log_action(
    action: str,
    params: dict,
    result: str,
    details: dict,
    duration_ms: int,
) -> None:
    """Log an action to the daily log file.

    Thread-safe: uses a global lock for concurrent writes.

    Args:
        action: Action type (e.g., "markets.trending", "trade.buy")
        params: Input parameters (will be sanitized)
        result: "success" or "failure"
        details: Result details (tx_hash, order_id, error, etc.)
        duration_ms: Execution time in milliseconds
    """
    entry = ActionEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        params=sanitize_dict(params),
        result=result,
        details=sanitize_dict(details),
        duration_ms=duration_ms,
    )

    log_file = get_log_file_path()

    with _log_lock:
        # Append to log file
        with open(log_file, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    # Periodically clean up old logs
    # (Run cleanup ~1% of the time to avoid overhead)
    if random.random() < 0.01:
        cleanup_old_logs()


class ActionLogger:
    """Context manager for timing and logging actions.

    Usage:
        with ActionLogger("markets.trending", {"limit": 20}) as log:
            markets = await client.get_trending_markets(limit=20)
            log.set_details({"count": len(markets)})
            log.success()
    """

    def __init__(self, action: str, params: dict):
        """Initialize the action logger.

        Args:
            action: Action type (e.g., "markets.trending", "trade.buy").
            params: Input parameters to log.
        """
        self.action = action
        self.params = params
        self.details: dict = {}
        self.result: str = "success"
        self.start_time = time.perf_counter()

    def set_details(self, details: dict) -> None:
        """Set additional details for the log entry."""
        self.details.update(details)

    def success(self) -> None:
        """Mark the action as successful."""
        self.result = "success"

    def failure(self, error: str) -> None:
        """Mark the action as failed with an error message."""
        self.result = "failure"
        self.details["error"] = error

    def cancelled(self, reason: str = "") -> None:
        """Mark the action as cancelled by user.

        Args:
            reason: Optional reason for cancellation.
        """
        self.result = "cancelled"
        if reason:
            self.details["reason"] = reason

    def __enter__(self) -> "ActionLogger":
        """Enter the context manager, returning self for use in the with block."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager, logging the action with duration and result.

        Args:
            exc_type: Exception type if an exception was raised, else None.
            exc_val: Exception value if an exception was raised, else None.
            exc_tb: Exception traceback if an exception was raised, else None.
        """
        duration_ms = int((time.perf_counter() - self.start_time) * 1000)

        if exc_type is not None:
            self.result = "failure"
            self.details["error"] = str(exc_val)

        try:
            log_action(
                action=self.action,
                params=self.params,
                result=self.result,
                details=self.details,
                duration_ms=duration_ms,
            )
        except Exception as e:
            # Logging should never break command execution
            # Fallback to stderr if logging fails
            import sys
            print(f"Logging failed: {e}", file=sys.stderr)


def get_actions_by_date(date_str: str) -> list[dict]:
    """Get all actions for a specific date.

    Args:
        date_str: Date in YYYY-MM-DD format.

    Returns:
        List of action entries.
    """
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return []

    log_file = get_log_file_path(date)

    if not log_file.exists():
        return []

    actions = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    actions.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    return actions


def get_recent_actions(limit: int = 20, max_lookback_days: int = 7) -> list[dict]:
    """Get the most recent actions across multiple days.

    Iterates backwards day-by-day until limit is reached or max lookback is hit.

    Args:
        limit: Maximum number of actions to return.
        max_lookback_days: Maximum number of days to look back (default: 7).

    Returns:
        List of action entries, most recent first.
    """
    actions = []
    current_date = datetime.now(timezone.utc)

    for _ in range(max_lookback_days):
        date_str = current_date.strftime("%Y-%m-%d")
        day_actions = get_actions_by_date(date_str)

        # Prepend actions from this day (they're already in chronological order)
        actions = day_actions + actions

        if len(actions) >= limit:
            break

        # Move to previous day
        current_date = current_date - timedelta(days=1)

    # Return last N actions, reversed (most recent first)
    return actions[-limit:][::-1]


def get_actions_by_type(action_prefix: str, limit: int = 50, max_lookback_days: int = 7) -> list[dict]:
    """Get actions filtered by type (action prefix).

    Args:
        action_prefix: Filter actions starting with this prefix (e.g., "trade")
        limit: Maximum number of actions to return.
        max_lookback_days: Maximum number of days to look back (default: 7).

    Returns:
        List of matching action entries.
    """
    actions = get_recent_actions(limit=limit * 2, max_lookback_days=max_lookback_days)

    filtered = [a for a in actions if a.get("action", "").startswith(action_prefix)]
    return filtered[:limit]  # Already most recent first from get_recent_actions