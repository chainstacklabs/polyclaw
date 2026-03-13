#!/usr/bin/env python3
"""Action log viewer commands."""

import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.action_logger import (
    get_recent_actions,
    get_actions_by_type,
    get_actions_by_date,
    get_logs_dir,
)


def format_duration(ms: int) -> str:
    """Format duration in human-readable form."""
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60000:
        return f"{ms / 1000:.1f}s"
    else:
        return f"{ms / 60000:.1f}m"


def format_action_row(entry: dict, truncate: int = 40) -> str:
    """Format an action entry as a table row."""
    timestamp = entry.get("timestamp", "")[11:19]  # Extract HH:MM:SS
    action = entry.get("action", "")
    result = entry.get("result", "")
    duration = format_duration(entry.get("duration_ms", 0))

    # Color indicators for result
    result_icon = "✓" if result == "success" else "✗"

    # Truncate action if needed
    if len(action) > truncate:
        action = action[:truncate - 3] + "..."

    return f"{timestamp} {result_icon} {action:<30} {duration:>8}"


def cmd_list(args):
    """List recent actions."""
    if args.type:
        actions = get_actions_by_type(args.type, limit=args.limit)
    elif args.date:
        actions = get_actions_by_date(args.date)
        actions = actions[-args.limit:][::-1]
    else:
        actions = get_recent_actions(limit=args.limit)

    if not actions:
        print("No actions found.")
        if args.date:
            print(f"No logs for {args.date}")
        return 0

    if args.json:
        print(json.dumps(actions, indent=2))
        return 0

    # Table output
    print(f"{'Time':<10} {'R':<1} {'Action':<30} {'Duration':>8}")
    print("-" * 60)

    for entry in actions:
        print(format_action_row(entry))

    print(f"\nShowing {len(actions)} actions")
    return 0


def cmd_show(args):
    """Show details of a specific action by index."""
    actions = get_recent_actions(limit=100)

    if not actions:
        print("No actions found.")
        return 1

    if args.index >= len(actions):
        print(f"Index {args.index} out of range (0-{len(actions) - 1})")
        return 1

    entry = actions[args.index]

    print(json.dumps(entry, indent=2))
    return 0


def cmd_files(args):
    """List available log files."""
    logs_dir = get_logs_dir()
    log_files = sorted(logs_dir.glob("actions-*.jsonl"), reverse=True)

    if not log_files:
        if getattr(args, 'json', False):
            print(json.dumps([]))
        else:
            print("No log files found.")
        return 0

    # Build file data
    file_data = []
    for f in log_files:
        with open(f) as fp:
            count = sum(1 for _ in fp)
        file_data.append({"name": f.stem, "count": count})

    if getattr(args, 'json', False):
        print(json.dumps(file_data, indent=2))
        return 0

    print("Available log files:")
    for fd in file_data:
        print(f"  {fd['name']} ({fd['count']} actions)")

    return 0


def main():
    """Main entry point for the actions CLI command."""
    parser = argparse.ArgumentParser(description="View action logs")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # List (default)
    list_parser = subparsers.add_parser("list", help="List recent actions")
    list_parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    list_parser.add_argument("--type", "-t", help="Filter by action type (e.g., 'trade', 'markets')")
    list_parser.add_argument("--date", "-d", help="Specific date (YYYY-MM-DD)")
    list_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of actions (default: 20)")

    # Show
    show_parser = subparsers.add_parser("show", help="Show action details")
    show_parser.add_argument("index", type=int, nargs="?", default=0, help="Action index (0 = most recent)")
    show_parser.add_argument("--json", "-j", action="store_true", help="JSON output")

    # Files
    files_parser = subparsers.add_parser("files", help="List available log files")
    files_parser.add_argument("--json", "-j", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "show":
        return cmd_show(args)
    elif args.command == "files":
        return cmd_files(args)
    else:
        # Default to list
        args.json = False
        args.type = None
        args.date = None
        args.limit = 20
        return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main() or 0)