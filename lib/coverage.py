"""Coverage calculation for hedge portfolios.

Calculate coverage metrics and tier classification for covering portfolios.

Coverage formula:
    Coverage = P(target wins) + P(target loses) x P(cover fires | target loses)

Example:
    Buy: "Region NOT captured" @ $0.80 (target)
    Buy: "City captured"       @ $0.15 (cover)
    Total cost: $0.95

    Coverage = 0.80 + 0.20 x 0.98 = 99.6%
    Expected profit = $0.996 - $0.95 = +$0.046

Tier classification:
    - TIER 1 (HIGH):     >=95% coverage - near-arbitrage
    - TIER 2 (GOOD):     90-95% - strong hedges
    - TIER 3 (MODERATE): 85-90% - decent but noticeable risk
    - TIER 4 (LOW):      <85% - speculative
"""

# =============================================================================
# CONFIGURATION
# =============================================================================

# Minimum coverage to include (filters out Tier 4 / Low quality)
MIN_COVERAGE = 0.85

# Probability for necessary relationships
NECESSARY_PROBABILITY = 0.98

# Coverage tier thresholds (coverage_threshold, tier_number, label, description)
TIER_THRESHOLDS = [
    (0.95, 1, "HIGH", "near-arbitrage"),
    (0.90, 2, "GOOD", "strong hedge"),
    (0.85, 3, "MODERATE", "decent hedge"),
    (0.00, 4, "LOW", "speculative"),
]


# =============================================================================
# METRICS CALCULATION
# =============================================================================


def calculate_coverage_metrics(
    target_price: float,
    cover_probability: float,
    total_cost: float,
) -> dict:
    """
    Calculate coverage and expected value for a portfolio.

    Args:
        target_price: Price of target position (= P(target pays out))
        cover_probability: P(cover fires | target doesn't pay out)
        total_cost: Total cost of both positions

    Returns:
        Dict with coverage, loss_probability, expected_profit
    """
    p_target = target_price
    p_not_target = 1 - target_price

    # Coverage = P(get paid) = P(target wins) + P(target loses) x P(cover fires)
    coverage = p_target + p_not_target * cover_probability

    # Loss probability = P(both fail)
    loss_probability = p_not_target * (1 - cover_probability)

    # Expected payout is just coverage (each payout is $1)
    expected_profit = coverage - total_cost

    return {
        "coverage": round(coverage, 4),
        "loss_probability": round(loss_probability, 4),
        "expected_profit": round(expected_profit, 4),
    }


def classify_tier(coverage: float) -> tuple[int, str]:
    """
    Classify portfolio into tier based on coverage.

    Returns:
        Tuple of (tier_number, tier_label)
    """
    for threshold, tier, label, _ in TIER_THRESHOLDS:
        if coverage >= threshold:
            return tier, label
    return 4, "LOW"


def get_tier_description(tier: int) -> str:
    """Get description for a tier number."""
    for _, t, label, desc in TIER_THRESHOLDS:
        if t == tier:
            return desc
    return "speculative"


# =============================================================================
# PORTFOLIO BUILDING
# =============================================================================


def build_portfolio(
    target_market: dict,
    cover_market: dict,
    target_position: str,
    cover_position: str,
    cover_probability: float,
    relationship: str,
) -> dict | None:
    """
    Build a single portfolio from target and cover markets.

    Args:
        target_market: Target market dict with prices
        cover_market: Cover market dict with prices
        target_position: "YES" or "NO" for target
        cover_position: "YES" or "NO" for cover
        cover_probability: P(cover fires | target doesn't)
        relationship: Explanation of the logical relationship

    Returns:
        Portfolio dict or None if invalid
    """
    # Get prices based on positions
    if target_position == "YES":
        target_price = target_market.get("yes_price", 0)
    else:
        target_price = target_market.get("no_price", 0)

    if cover_position == "YES":
        cover_price = cover_market.get("yes_price", 0)
    else:
        cover_price = cover_market.get("no_price", 0)

    total_cost = target_price + cover_price

    # Skip invalid costs
    if total_cost <= 0 or total_cost > 2.0:
        return None

    # Calculate metrics
    metrics = calculate_coverage_metrics(target_price, cover_probability, total_cost)

    # Skip low coverage portfolios
    if metrics["coverage"] < MIN_COVERAGE:
        return None

    # Classify tier
    tier, tier_label = classify_tier(metrics["coverage"])

    return {
        # Target info
        "target_id": target_market.get("id", ""),
        "target_question": target_market.get("question", ""),
        "target_slug": target_market.get("slug", ""),
        "target_position": target_position,
        "target_price": round(target_price, 4),
        # Cover info
        "cover_id": cover_market.get("id", ""),
        "cover_question": cover_market.get("question", ""),
        "cover_slug": cover_market.get("slug", ""),
        "cover_position": cover_position,
        "cover_price": round(cover_price, 4),
        "cover_probability": cover_probability,
        # Relationship
        "relationship": relationship,
        # Metrics
        "total_cost": round(total_cost, 4),
        "profit": round(1.0 - total_cost, 4),
        "profit_pct": round((1.0 - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0,
        **metrics,
        # Tier
        "tier": tier,
        "tier_label": tier_label,
    }


def filter_portfolios_by_tier(
    portfolios: list[dict],
    max_tier: int = 2,
) -> list[dict]:
    """
    Filter portfolios by maximum tier.

    Args:
        portfolios: List of portfolios
        max_tier: Maximum tier to include (1 = best only)

    Returns:
        Filtered list
    """
    return [p for p in portfolios if p["tier"] <= max_tier]


def filter_portfolios_by_coverage(
    portfolios: list[dict],
    min_coverage: float = MIN_COVERAGE,
) -> list[dict]:
    """
    Filter portfolios by minimum coverage.

    Args:
        portfolios: List of portfolios
        min_coverage: Minimum coverage threshold

    Returns:
        Filtered list
    """
    return [p for p in portfolios if p["coverage"] >= min_coverage]


def sort_portfolios(portfolios: list[dict]) -> list[dict]:
    """Sort portfolios by tier (ascending) then coverage (descending)."""
    return sorted(portfolios, key=lambda p: (p["tier"], -p["coverage"]))
