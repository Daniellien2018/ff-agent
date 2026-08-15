"""DEF + K streaming — design doc §4.2, "build first": highest Δ P(title) per hour.

DEF fantasy scoring is dominated by the points-allowed band, which is mostly
a function of the *opposing* offense's quality — priced accurately by Vegas
via implied team totals. K scoring correlates with your own team's implied
total (more scoring drives = more FG/XP chances) and, more weakly, with
playing in a dome (no wind/cold suppressing kicks).

Both rankers take a list of *candidate* team abbreviations (whatever's a free
agent this week — that's the caller's job to determine from rosters) and a
week's Vegas context. If Vegas is unavailable, they return an explicit
"no confident recommendation" result rather than silently guessing — per the
design doc's own instruction (§3) not to manufacture analysis you don't have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.projections.vegas_source import TeamGameContext, VegasUnavailable, get_week_context

# Retractable/fixed-roof stadiums where weather essentially never suppresses
# passing or kicking. Small, stable list -- not worth an API call.
DOME_TEAMS = {"ARI", "ATL", "DAL", "DET", "HOU", "IND", "LAC", "LAR", "LV", "MIN", "NO"}

# Points-allowed -> fantasy points, from config's def_points_allowed bands,
# expressed as (band-midpoint, score) breakpoints for linear interpolation.
# The design doc's bands are step functions; interpolating between midpoints
# gives a continuous estimate from a continuous input (Vegas implied total)
# instead of forcing a hard, arbitrary cutoff.
_PA_BREAKPOINTS = [0.0, 3.5, 10.0, 17.0, 24.0, 31.0, 38.0]
_PA_SCORES = [10.0, 7.0, 4.0, 1.0, 0.0, -1.0, -4.0]

# Rough constant estimate of sacks/INT/TD points per game, independent of
# matchup quality. This is a simplification -- real per-team pass-rush and
# takeaway rates would sharpen it. Flagged for backtest.py calibration.
DEF_BONUS_POINTS_ESTIMATE = 3.0

# K heuristic calibration -- baseline weekly kicker points at a league-average
# implied total (~21), points per unit of implied total above/below that, and
# a flat dome bonus. All placeholders pending real calibration against 2025
# kicker results in backtest.py.
K_BASELINE_POINTS = 7.0
K_BASELINE_IMPLIED_TOTAL = 21.0
K_POINTS_PER_IMPLIED_POINT = 0.3
K_DOME_BONUS = 1.0


@dataclass
class StreamRecommendation:
    available: bool
    reason: str = ""
    ranked: list[dict] = field(default_factory=list)


def expected_def_points(opponent_implied_total: float) -> float:
    pa = float(np.interp(opponent_implied_total, _PA_BREAKPOINTS, _PA_SCORES))
    return round(pa + DEF_BONUS_POINTS_ESTIMATE, 2)


def expected_k_points(own_implied_total: float, is_dome: bool) -> float:
    pts = K_BASELINE_POINTS + K_POINTS_PER_IMPLIED_POINT * (own_implied_total - K_BASELINE_IMPLIED_TOTAL)
    if is_dome:
        pts += K_DOME_BONUS
    return round(pts, 2)


def _rank(
    candidates: list[str],
    week_context: dict[str, TeamGameContext],
    score_fn,
    reason_fn,
) -> StreamRecommendation:
    ranked = []
    for team in candidates:
        ctx = week_context.get(team)
        if ctx is None:
            continue  # bye week or unmapped — no game, no recommendation for this team
        ranked.append(
            {
                "team": team,
                "opponent": ctx.opponent,
                "is_home": ctx.is_home,
                "implied_team_total": ctx.implied_team_total,
                "implied_opponent_total": ctx.implied_opponent_total,
                "expected_points": score_fn(ctx),
                "commence_time": ctx.commence_time,
                "reason": reason_fn(ctx),
            }
        )
    ranked.sort(key=lambda r: r["expected_points"], reverse=True)
    if not ranked:
        return StreamRecommendation(available=False, reason="none of the candidates have a game this week (byes?)")
    return StreamRecommendation(available=True, ranked=ranked)


def rank_defenses(candidates: list[str], cache_dir: str = "data/cache") -> StreamRecommendation:
    try:
        week_context = get_week_context(cache_dir)
    except VegasUnavailable as e:
        return StreamRecommendation(available=False, reason=f"Vegas odds unavailable: {e}")

    def score_fn(ctx: TeamGameContext) -> float:
        return expected_def_points(ctx.implied_opponent_total)

    def reason_fn(ctx: TeamGameContext) -> str:
        loc = "home" if ctx.is_home else "away"
        return (
            f"faces {ctx.opponent} ({loc}), opponent implied total {ctx.implied_opponent_total} "
            f"— {'lowest on the slate' if ctx.implied_opponent_total <= 17 else 'middling-to-tough matchup'}"
        )

    return _rank(candidates, week_context, score_fn, reason_fn)


def rank_kickers(candidates: list[str], cache_dir: str = "data/cache") -> StreamRecommendation:
    try:
        week_context = get_week_context(cache_dir)
    except VegasUnavailable as e:
        return StreamRecommendation(available=False, reason=f"Vegas odds unavailable: {e}")

    def score_fn(ctx: TeamGameContext) -> float:
        return expected_k_points(ctx.implied_team_total, ctx.team in DOME_TEAMS)

    def reason_fn(ctx: TeamGameContext) -> str:
        dome = ", dome" if ctx.team in DOME_TEAMS else ""
        return f"own implied total {ctx.implied_team_total}{dome}"

    return _rank(candidates, week_context, score_fn, reason_fn)
