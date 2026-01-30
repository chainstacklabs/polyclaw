#!/usr/bin/env python3
"""Hedge discovery commands - find covering portfolios via LLM implications.

Usage:
    hedge scan                    # Scan trending markets for hedges
    hedge scan --query "election" # Scan markets matching query
    hedge analyze <id1> <id2>     # Analyze specific market pair
"""

import sys
import json
import re
import asyncio
import argparse
from pathlib import Path

# Add parent to path for lib imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from skill root directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from lib.gamma_client import GammaClient, Market
from lib.llm_client import LLMClient, DEFAULT_MODEL
from lib.coverage import (
    NECESSARY_PROBABILITY,
    build_portfolio,
    filter_portfolios_by_tier,
    filter_portfolios_by_coverage,
    sort_portfolios,
)


# =============================================================================
# IMPLICATION PROMPT
# =============================================================================

IMPLICATION_PROMPT = """Find ONLY logically necessary relationships between prediction market events.

## TARGET EVENT:
"{target_question}"

## AVAILABLE EVENTS:
{market_list_text}

## WHAT IS "NECESSARY"?

A **NECESSARY** implication (A -> B) means: "If A is true, B MUST be true BY DEFINITION OR PHYSICAL LAW."

There must be ZERO possible scenarios where A=YES and B=NO. Not "unlikely" - IMPOSSIBLE.

## VALID NECESSARY RELATIONSHIPS (include these):
- "election held" -> "election called" (DEFINITION: can't hold without calling)
- "city captured" -> "military operation in city" (PHYSICAL: can't capture without entering)
- "person dies" -> "person was alive" (LOGICAL: death requires prior life)
- "child born" -> "pregnancy occurred" (BIOLOGICAL: birth requires pregnancy)

## NOT NECESSARY - DO NOT INCLUDE:
- "war started" -> "peace talks failed" (WRONG: war can start without talks)
- "election called" -> "election held" (WRONG: can be cancelled)
- "military clash" -> "nuclear weapon used" (WRONG: clash doesn't require nukes)
- "ceasefire broken" -> "war escalates" (WRONG: could de-escalate)
- "sanctions imposed" -> "conflict worsens" (WRONG: correlation, not causation)
- "candidate wins primary" -> "candidate wins general" (WRONG: can lose general)

## YOUR TASK

Find relationships where events GUARANTEE each other:

### 1. implied_by (OTHER -> TARGET): What GUARANTEES the target?
- "If OTHER=YES, then TARGET=YES is 100% CERTAIN"
- Must be definitionally or physically impossible for OTHER=YES and TARGET=NO

### 2. implies (TARGET -> OTHER): What does the target GUARANTEE?
- "If TARGET=YES, then OTHER=YES is 100% CERTAIN"
- BE VERY CAREFUL: This direction is often confused with correlation!

## STRICT COUNTEREXAMPLE TEST (REQUIRED)

For EACH relationship, you MUST:
1. Try to construct a scenario that violates the implication
2. If you can imagine ANY such scenario (even unlikely), DO NOT INCLUDE IT
3. Only include if the scenario is LOGICALLY IMPOSSIBLE

## OUTPUT FORMAT (JSON only):
```json
{{
  "implied_by": [
    {{
      "market_id": "exact id from list",
      "market_question": "exact question from list",
      "explanation": "why other=YES makes target=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]"
    }}
  ],
  "implies": [
    {{
      "market_id": "exact id from list",
      "market_question": "exact question from list",
      "explanation": "why target=YES makes other=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]"
    }}
  ]
}}
```

## CRITICAL RULES:
1. QUALITY OVER QUANTITY - empty lists are fine, false positives are NOT
2. "Likely" or "usually" means DO NOT INCLUDE
3. Correlations are NOT implications - "A often leads to B" is NOT "A guarantees B"
4. Political/social predictions are almost NEVER necessary (humans are unpredictable)
5. When in doubt, LEAVE IT OUT
"""


# =============================================================================
# JSON EXTRACTION
# =============================================================================


def extract_json_from_response(text: str) -> dict | None:
    """
    Extract and parse JSON from LLM response.

    Handles common patterns:
    - JSON wrapped in markdown code blocks
    - Raw JSON objects
    - JSON embedded in text
    """
    text = text.strip()

    # Remove markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1]
    if "```" in text:
        text = text.split("```")[0]
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# =============================================================================
# IMPLICATION EXTRACTION
# =============================================================================


def match_market_to_list(
    market_id: str,
    market_question: str,
    markets_by_id: dict[str, Market],
    markets_by_question: dict[str, Market],
) -> Market | None:
    """Match LLM output to actual market."""
    # Direct ID match
    if market_id in markets_by_id:
        return markets_by_id[market_id]

    # Question match (case insensitive)
    question_lower = market_question.lower().strip()
    if question_lower in markets_by_question:
        return markets_by_question[question_lower]

    # Fuzzy match - substring
    for q, market in markets_by_question.items():
        if question_lower in q or q in question_lower:
            return market

    return None


def derive_covers_from_implications(
    llm_result: dict,
    target_market: Market,
    other_markets: list[Market],
) -> list[dict]:
    """
    Derive cover relationships from LLM implications.

    For target event T:
    - "implied_by" (other -> target): contrapositive gives YES cover (buy NO on other)
    - "implies" (target -> other): direct gives NO cover (buy YES on other)
    """
    # Build lookup tables
    markets_by_id = {m.id: m for m in other_markets}
    markets_by_question = {m.question.lower().strip(): m for m in other_markets}

    covers = []

    # Process "implied_by": other -> target (contrapositive gives YES cover)
    # If other=YES implies target=YES, then target=NO implies other=NO
    # So if we buy target=YES, we're covered by other=NO
    for item in llm_result.get("implied_by", []):
        other_id = item.get("market_id", "")
        other_question = item.get("market_question", "")

        matched = match_market_to_list(
            other_id, other_question, markets_by_id, markets_by_question
        )
        if not matched or matched.id == target_market.id:
            continue

        covers.append({
            "target_position": "YES",
            "cover_market": matched,
            "cover_position": "NO",
            "relationship": f"necessary (contrapositive): {item.get('explanation', '')}",
            "probability": NECESSARY_PROBABILITY,
        })

    # Process "implies": target -> other (direct gives NO cover)
    # If target=YES implies other=YES, then buying target=NO is covered by other=YES
    for item in llm_result.get("implies", []):
        other_id = item.get("market_id", "")
        other_question = item.get("market_question", "")

        matched = match_market_to_list(
            other_id, other_question, markets_by_id, markets_by_question
        )
        if not matched or matched.id == target_market.id:
            continue

        covers.append({
            "target_position": "NO",
            "cover_market": matched,
            "cover_position": "YES",
            "relationship": f"necessary (direct): {item.get('explanation', '')}",
            "probability": NECESSARY_PROBABILITY,
        })

    return covers


async def extract_implications_for_market(
    target_market: Market,
    other_markets: list[Market],
    llm: LLMClient,
) -> list[dict]:
    """Extract implications for a single target market."""
    # Build market list for prompt
    market_list_text = "\n".join(
        f"- ID: {m.id}, Question: {m.question}" for m in other_markets if m.id != target_market.id
    )

    prompt = IMPLICATION_PROMPT.format(
        target_question=target_market.question,
        market_list_text=market_list_text,
    )

    try:
        response = await llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        llm_result = extract_json_from_response(response)
        if not llm_result:
            return []

        return derive_covers_from_implications(
            llm_result, target_market, other_markets
        )

    except Exception as e:
        print(f"Error extracting implications: {e}", file=sys.stderr)
        return []


# =============================================================================
# PORTFOLIO BUILDING
# =============================================================================


def market_to_dict(market: Market) -> dict:
    """Convert Market dataclass to dict for coverage functions."""
    return {
        "id": market.id,
        "question": market.question,
        "slug": market.slug,
        "yes_price": market.yes_price,
        "no_price": market.no_price,
    }


def build_portfolios_from_covers(
    target_market: Market,
    covers: list[dict],
) -> list[dict]:
    """Build portfolio dicts from cover relationships."""
    portfolios = []
    target_dict = market_to_dict(target_market)

    for cover in covers:
        cover_market = cover["cover_market"]
        cover_dict = market_to_dict(cover_market)

        portfolio = build_portfolio(
            target_market=target_dict,
            cover_market=cover_dict,
            target_position=cover["target_position"],
            cover_position=cover["cover_position"],
            cover_probability=cover["probability"],
            relationship=cover["relationship"],
        )

        if portfolio:
            portfolios.append(portfolio)

    return portfolios


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================


def format_portfolio_row(p: dict) -> str:
    """Format a portfolio as a table row."""
    target_q = p["target_question"][:35] + "..." if len(p["target_question"]) > 35 else p["target_question"]
    cover_q = p["cover_question"][:35] + "..." if len(p["cover_question"]) > 35 else p["cover_question"]

    return (
        f"T{p['tier']} {p['coverage']*100:5.1f}% "
        f"${p['total_cost']:.2f} "
        f"{p['target_position']:>3}@{p['target_price']:.2f} {target_q:<38} | "
        f"{p['cover_position']:>3}@{p['cover_price']:.2f} {cover_q}"
    )


def print_portfolios_table(portfolios: list[dict]) -> None:
    """Print portfolios as formatted table."""
    if not portfolios:
        print("No covering portfolios found.")
        return

    print(f"{'Tier':>4} {'Cov':>6} {'Cost':>6} {'Target':^45} | {'Cover'}")
    print("-" * 120)

    for p in portfolios:
        print(format_portfolio_row(p))


def print_portfolios_json(portfolios: list[dict]) -> None:
    """Print portfolios as JSON."""
    print(json.dumps(portfolios, indent=2))


# =============================================================================
# COMMANDS
# =============================================================================


async def cmd_scan(args):
    """Scan markets for hedging opportunities."""
    gamma = GammaClient()

    # Fetch markets
    print(f"Fetching markets...", file=sys.stderr)
    if args.query:
        markets = await gamma.search_markets(args.query, limit=args.limit)
        print(f"Found {len(markets)} markets matching '{args.query}'", file=sys.stderr)
    else:
        markets = await gamma.get_trending_markets(limit=args.limit)
        print(f"Got {len(markets)} trending markets", file=sys.stderr)

    if len(markets) < 2:
        print("Need at least 2 markets to find hedges")
        return 1

    # Initialize LLM client
    try:
        llm = LLMClient(model=args.model)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    all_portfolios = []

    # Extract implications for each market
    print(f"Analyzing {len(markets)} markets for hedging relationships...", file=sys.stderr)

    try:
        for i, target in enumerate(markets):
            if not args.json:
                print(f"[{i+1}/{len(markets)}] {target.question[:60]}...", file=sys.stderr)

            covers = await extract_implications_for_market(target, markets, llm)

            if covers:
                portfolios = build_portfolios_from_covers(target, covers)
                all_portfolios.extend(portfolios)

                if not args.json and portfolios:
                    print(f"  Found {len(portfolios)} potential hedges", file=sys.stderr)

    finally:
        await llm.close()

    # Filter and sort
    if args.min_coverage:
        all_portfolios = filter_portfolios_by_coverage(all_portfolios, args.min_coverage)

    if args.tier:
        all_portfolios = filter_portfolios_by_tier(all_portfolios, args.tier)

    all_portfolios = sort_portfolios(all_portfolios)

    # Output
    print(f"\n=== Found {len(all_portfolios)} covering portfolios ===\n", file=sys.stderr)

    if args.json:
        print_portfolios_json(all_portfolios)
    else:
        print_portfolios_table(all_portfolios)

    return 0


async def cmd_analyze(args):
    """Analyze a specific market pair for hedging relationship."""
    gamma = GammaClient()

    # Fetch both markets
    try:
        print(f"Fetching markets...", file=sys.stderr)
        market1 = await gamma.get_market(args.market_id_1)
        market2 = await gamma.get_market(args.market_id_2)
    except Exception as e:
        print(f"Error fetching markets: {e}")
        return 1

    print(f"Market 1: {market1.question}", file=sys.stderr)
    print(f"Market 2: {market2.question}", file=sys.stderr)

    # Initialize LLM client
    try:
        llm = LLMClient(model=args.model)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    all_portfolios = []

    try:
        # Check both directions
        print(f"\nAnalyzing implications...", file=sys.stderr)

        # Market 1 as target
        covers1 = await extract_implications_for_market(market1, [market2], llm)
        if covers1:
            portfolios1 = build_portfolios_from_covers(market1, covers1)
            all_portfolios.extend(portfolios1)

        # Market 2 as target
        covers2 = await extract_implications_for_market(market2, [market1], llm)
        if covers2:
            portfolios2 = build_portfolios_from_covers(market2, covers2)
            all_portfolios.extend(portfolios2)

    finally:
        await llm.close()

    # Filter and sort
    if args.min_coverage:
        all_portfolios = filter_portfolios_by_coverage(all_portfolios, args.min_coverage)

    all_portfolios = sort_portfolios(all_portfolios)

    # Output
    if not all_portfolios:
        print("\nNo hedging relationship found between these markets.")
        print("This could mean:")
        print("  - No logical implication exists (most common)")
        print("  - Relationship is correlation, not causation")
        print("  - Coverage is below minimum threshold")
        return 0

    print(f"\n=== Found {len(all_portfolios)} covering portfolio(s) ===\n", file=sys.stderr)

    if args.json:
        print_portfolios_json(all_portfolios)
    else:
        print_portfolios_table(all_portfolios)
        print("\nRelationships:")
        for p in all_portfolios:
            print(f"  - {p['relationship']}")

    return 0


# =============================================================================
# MAIN
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Hedge discovery - find covering portfolios")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan markets for hedges")
    scan_parser.add_argument("--query", "-q", help="Search query to filter markets")
    scan_parser.add_argument("--limit", type=int, default=20, help="Number of markets to scan (default: 20)")
    scan_parser.add_argument("--min-coverage", type=float, default=0.85, help="Minimum coverage threshold (default: 0.85)")
    scan_parser.add_argument("--tier", type=int, default=2, help="Maximum tier to include (1=best, default: 2)")

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze specific market pair")
    analyze_parser.add_argument("market_id_1", help="First market ID")
    analyze_parser.add_argument("market_id_2", help="Second market ID")
    analyze_parser.add_argument("--min-coverage", type=float, default=0.85, help="Minimum coverage threshold")

    args = parser.parse_args()

    if args.command == "scan":
        return asyncio.run(cmd_scan(args))
    elif args.command == "analyze":
        return asyncio.run(cmd_analyze(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
