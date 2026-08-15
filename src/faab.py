"""FAAB bid sizing with roster-spot pricing — design doc §2.2/§4.6.

$1000 budget, 5 bench slots, 14 teams: money is nearly free, spots are not.
Bid size should track how much a pickup is actually worth (Δ P(title)), not
some fraction of "market price" -- there is no efficient market here, FAAB
gets underbid league-wide in shallow leagues because managers anchor on
dollar amounts instead of value.

This is a v1 heuristic, not a market-clearing model: it maps the same
critical/moderate/marginal leverage tiers used everywhere else in the repo
(§4.6) onto bid-as-percent-of-remaining-budget. Tune the percentages against
data/recon/waiver_timing.json's actual historical bids once backtest.py exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.explain import classify_leverage

# % of remaining FAAB budget to bid, by leverage tier. Streamers (DEF/K, or any
# add explicitly not meant to stick past a week or two) bid less aggressively
# than a critical add expected to start the rest of the season, even at the
# same Δ P(title), because their edge decays next week when the matchup changes.
BID_PCT_BY_TIER = {
    "critical": 0.04,
    "moderate": 0.015,
    "marginal": 0.010,
}
STREAMER_BID_PCT = 0.010  # flat, low -- see design doc §5 example: $12 on $980 remaining
WALK_AWAY_MULTIPLIER = 2.0


@dataclass
class FaabBid:
    bid: int
    walk_away_above: int
    pct_of_remaining: float
    tier: str


def suggest_faab_bid(
    delta_p_title: float,
    remaining_budget: float,
    leverage_cfg: dict,
    *,
    is_streamer: bool = False,
    min_bid: int = 0,
) -> FaabBid:
    tier = classify_leverage(delta_p_title, leverage_cfg)
    pct = STREAMER_BID_PCT if is_streamer else BID_PCT_BY_TIER[tier]
    bid = max(min_bid, round(remaining_budget * pct))
    walk_away = round(bid * WALK_AWAY_MULTIPLIER)
    return FaabBid(bid=bid, walk_away_above=walk_away, pct_of_remaining=pct, tier=tier)


def bench_spot_opportunity_cost(worst_bench_avg_pts: float | None, best_available_avg_pts: float | None) -> float | None:
    """How many points/week you're leaving on the table by holding the worst
    bench player instead of the best available free agent at a startable
    position. None if either side is unknown (small sample, bye week, etc.) --
    don't fabricate a number from missing data.
    """
    if worst_bench_avg_pts is None or best_available_avg_pts is None:
        return None
    return round(best_available_avg_pts - worst_bench_avg_pts, 2)
