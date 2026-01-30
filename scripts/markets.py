#!/usr/bin/env python3
"""Market browsing commands."""

import sys
import json
import asyncio
import argparse
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.gamma_client import GammaClient


def format_price(price: float) -> str:
    """Format price as cents."""
    return f"${price:.2f}"


def format_volume(volume: float) -> str:
    """Format volume in human-readable form."""
    if volume >= 1_000_000:
        return f"${volume / 1_000_000:.1f}M"
    elif volume >= 1_000:
        return f"${volume / 1_000:.1f}K"
    else:
        return f"${volume:.0f}"


def format_market_row(market, truncate: int = 0) -> dict:
    """Format market for display. Set truncate=0 for full question."""
    question = market.question
    if truncate > 0 and len(question) > truncate:
        question = question[:truncate] + "..."
    return {
        "id": market.id,
        "question": question,
        "yes": format_price(market.yes_price),
        "no": format_price(market.no_price),
        "volume_24h": format_volume(market.volume_24h),
        "volume_total": format_volume(market.volume),
    }


async def cmd_trending(args):
    """Show trending markets."""
    client = GammaClient()
    markets = await client.get_trending_markets(limit=args.limit)

    if args.json:
        # JSON output: full questions for agent consumption
        print(json.dumps([format_market_row(m) for m in markets], indent=2))
    else:
        # Terminal output: truncate unless --full
        trunc = 0 if args.full else 60
        print(f"{'ID':<12} {'YES':>6} {'NO':>6} {'24h Vol':>10} {'Question'}")
        print("-" * 80)
        for m in markets:
            question = m.question if args.full else (m.question[:60] + "..." if len(m.question) > 60 else m.question)
            print(f"{m.id[:12]:<12} {format_price(m.yes_price):>6} {format_price(m.no_price):>6} {format_volume(m.volume_24h):>10} {question}")


async def cmd_search(args):
    """Search markets by keyword."""
    client = GammaClient()
    markets = await client.search_markets(args.query, limit=args.limit)

    if not markets:
        print(f"No markets found for: {args.query}")
        return 1

    if args.json:
        # JSON output: full questions for agent consumption
        print(json.dumps([format_market_row(m) for m in markets], indent=2))
    else:
        # Terminal output: truncate unless --full
        print(f"{'ID':<12} {'YES':>6} {'NO':>6} {'24h Vol':>10} {'Question'}")
        print("-" * 80)
        for m in markets:
            question = m.question if args.full else (m.question[:60] + "..." if len(m.question) > 60 else m.question)
            print(f"{m.id[:12]:<12} {format_price(m.yes_price):>6} {format_price(m.no_price):>6} {format_volume(m.volume_24h):>10} {question}")


async def cmd_details(args):
    """Show market details."""
    client = GammaClient()

    try:
        if args.market_id.startswith("http"):
            # Extract slug from URL
            slug = args.market_id.rstrip("/").split("/")[-1]
            market = await client.get_market_by_slug(slug)
        elif args.market_id.isdigit():
            # Numeric IDs are Gamma market IDs
            market = await client.get_market(args.market_id)
        elif len(args.market_id) < 20:
            # Assume it's a slug
            market = await client.get_market_by_slug(args.market_id)
        else:
            # Assume it's an ID
            market = await client.get_market(args.market_id)
    except Exception as e:
        print(f"Error: {e}")
        return 1

    result = {
        "id": market.id,
        "question": market.question,
        "slug": market.slug,
        "condition_id": market.condition_id,
        "prices": {
            "yes": market.yes_price,
            "no": market.no_price,
        },
        "tokens": {
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id,
        },
        "volume": {
            "24h": market.volume_24h,
            "total": market.volume,
        },
        "liquidity": market.liquidity,
        "status": {
            "active": market.active,
            "closed": market.closed,
            "resolved": market.resolved,
            "outcome": market.outcome,
        },
        "end_date": market.end_date,
        "url": f"https://polymarket.com/event/{market.slug}",
    }

    print(json.dumps(result, indent=2))


async def cmd_events(args):
    """Show events/groups with markets."""
    client = GammaClient()
    events = await client.get_events(limit=args.limit)

    if args.json:
        # JSON output: full questions for agent consumption
        result = []
        for e in events:
            result.append({
                "id": e.id,
                "title": e.title,
                "slug": e.slug,
                "markets": [format_market_row(m) for m in e.markets[:5]],
            })
        print(json.dumps(result, indent=2))
    else:
        for e in events:
            print(f"\n{e.title}")
            print(f"  Slug: {e.slug}")
            print(f"  Markets: {len(e.markets)}")
            for m in e.markets[:3]:
                question = m.question if args.full else (m.question[:70] + "..." if len(m.question) > 70 else m.question)
                print(f"    - {question} (YES: {format_price(m.yes_price)})")


def main():
    parser = argparse.ArgumentParser(description="Market browsing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Trending
    trending_parser = subparsers.add_parser("trending", help="Show trending markets")
    trending_parser.add_argument("--limit", type=int, default=20, help="Number of markets")
    trending_parser.add_argument("--full", action="store_true", help="Show full question text")

    # Search
    search_parser = subparsers.add_parser("search", help="Search markets")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=20, help="Number of results")
    search_parser.add_argument("--full", action="store_true", help="Show full question text")

    # Details
    details_parser = subparsers.add_parser("details", help="Market details")
    details_parser.add_argument("market_id", help="Market ID, slug, or URL")

    # Events
    events_parser = subparsers.add_parser("events", help="Show events/groups")
    events_parser.add_argument("--limit", type=int, default=10, help="Number of events")
    events_parser.add_argument("--full", action="store_true", help="Show full question text")

    args = parser.parse_args()

    if args.command == "trending":
        return asyncio.run(cmd_trending(args))
    elif args.command == "search":
        return asyncio.run(cmd_search(args))
    elif args.command == "details":
        return asyncio.run(cmd_details(args))
    elif args.command == "events":
        return asyncio.run(cmd_events(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
