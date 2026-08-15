"""Phase 4 (partial) — RB/WR/TE start-sit, design doc §4.5.

The doc's full spec (ILP top-50 candidate lineups, each re-scored via 10k
correlated sims against the opponent) is real work for a real variance model
(§4.4's bootstrap residuals + Gaussian copula) that isn't built yet. This is
the mean-projection slice of that: find the actual EV-optimal lineup from
real per-player projections (src/projections/sleeper_source.py) using the
same exact FLEX-aware solver already validated in lineup_math.py, diff it
against what's currently SET in Sleeper, and score each resulting swap's
Δ P(title) the same way DEF/K streaming already is (src/decisions.py).

What this deliberately does NOT do yet: model correlation (stacking, RB
committee anti-correlation) or use anything but a point estimate per player
-- so it can't yet tell you "start the boom/bust guy because you're an
underdog" (§2.7). It correctly identifies "this lineup leaves points on the
bench," which is most of the practical value in most weeks (§3: "5 bench
spots means most weeks are forced" -- when they're NOT forced, this is the
part that catches it).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.lineup_math import LineupPlayer, optimal_lineup


@dataclass
class LineupSwap:
    start_id: str
    bench_id: str | None  # None if this fills a currently-empty slot, not a real swap
    start_name: str
    bench_name: str | None
    start_pos: str | None
    bench_pos: str | None
    start_pts: float
    bench_pts: float
    delta_pts: float
    start_status: str | None
    bench_status: str | None


def compute_lineup_swaps(
    roster: dict,
    pts_index: dict[str, float],
    player_index,
    slot_positions: list[str],
    flex_eligible: dict[str, list[str]],
) -> list[LineupSwap]:
    """Compares the mean-optimal lineup (from real projections) against
    whatever's currently SET as starters. Returns swaps ranked by point
    impact, most valuable first. Empty (0 or missing points) entries are
    fine -- a bye-week or off-slate player just won't get selected by the
    optimizer over anyone with a real projection.
    """
    relevant_positions = set(slot_positions) | {p for group in flex_eligible.values() for p in group}
    relevant_positions.discard("FLEX")  # "FLEX" itself is a slot name, not a player position

    lineup_players = []
    for pid in roster.get("players") or []:
        info = player_index.get(pid)
        if not info:
            continue
        position = info.get("position")
        if position not in relevant_positions:
            continue
        lineup_players.append(LineupPlayer(player_id=pid, position=position, points=pts_index.get(pid, 0.0)))

    if not lineup_players:
        return []

    _, assignment = optimal_lineup(lineup_players, slot_positions, flex_eligible)
    optimal_ids = set(assignment.values())
    current_ids = {pid for pid in (roster.get("starters") or []) if pid != "0"}

    pts_by_id = {p.player_id: p.points for p in lineup_players}
    to_start = sorted(optimal_ids - current_ids, key=lambda pid: -pts_by_id.get(pid, 0.0))
    to_bench = sorted(current_ids - optimal_ids, key=lambda pid: pts_by_id.get(pid, 0.0))

    swaps = []
    for i, start_id in enumerate(to_start):
        bench_id = to_bench[i] if i < len(to_bench) else None
        start_info = player_index.get(start_id) or {}
        bench_info = (player_index.get(bench_id) or {}) if bench_id else {}
        start_pts = pts_by_id.get(start_id, 0.0)
        bench_pts = pts_by_id.get(bench_id, 0.0) if bench_id else 0.0
        swaps.append(
            LineupSwap(
                start_id=start_id,
                bench_id=bench_id,
                start_name=start_info.get("full_name") or start_id,
                bench_name=(bench_info.get("full_name") or bench_id) if bench_id else None,
                start_pos=start_info.get("position"),
                bench_pos=bench_info.get("position") if bench_id else None,
                start_pts=start_pts,
                bench_pts=bench_pts,
                delta_pts=round(start_pts - bench_pts, 2),
                start_status=start_info.get("injury_status"),
                bench_status=bench_info.get("injury_status") if bench_id else None,
            )
        )

    swaps.sort(key=lambda s: s.delta_pts, reverse=True)
    return swaps
