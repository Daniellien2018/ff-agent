"""Roster-spot discipline — design doc §2.2: bench spots are the scarce
resource, not FAAB. Every function here exists to catch a wasted spot:
an empty IR slot, a rostered backup QB/K/DEF, or an upcoming bye-week crunch.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.players import PlayerIndex

# Statuses Sleeper reports that make a player IR-eligible in this league
# (settings confirm reserve_allow_doubtful/out/dnr/na/sus/cov are all on).
IR_ELIGIBLE_STATUSES = {"Doubtful", "Out", "IR", "PUP", "NA", "DNR", "Sus", "COV"}


@dataclass
class IRCandidate:
    player_id: str
    name: str
    status: str


def flag_ir_candidates(roster: dict, player_index: PlayerIndex) -> list[IRCandidate]:
    """Rostered (non-reserve) players who qualify for the IR slot right now."""
    reserve = set(roster.get("reserve") or [])
    candidates = []
    for pid in roster.get("players") or []:
        if pid in reserve:
            continue
        info = player_index.get(pid)
        if not info:
            continue
        status = info.get("injury_status")
        if status in IR_ELIGIBLE_STATUSES:
            name = info.get("full_name") or pid
            candidates.append(IRCandidate(player_id=pid, name=name, status=status))
    return candidates


def ir_slot_usage(roster: dict, ir_slots: int) -> dict:
    used = len(roster.get("reserve") or [])
    return {"used": used, "total": ir_slots, "empty_slots": max(0, ir_slots - used)}


@dataclass
class BackupViolation:
    position: str
    keep: str  # player_id to keep (best/first rostered)
    cut_candidates: list[str]  # player_ids that violate "never carry a backup"


def flag_backup_violations(roster: dict, player_index: PlayerIndex, positions: tuple[str, ...] = ("QB", "K", "DEF")) -> list[BackupViolation]:
    """§2.2: never carry a backup QB, K, or DEF. Flags any position where more
    than one is rostered. Doesn't guess who's "better" — that needs actual
    projections — just flags the position and lists all held players so a
    human picks which to keep.
    """
    by_position: dict[str, list[str]] = {p: [] for p in positions}
    for pid in roster.get("players") or []:
        info = player_index.get(pid)
        if not info:
            continue
        pos = info.get("position")
        if pos in by_position:
            by_position[pos].append(pid)

    violations = []
    for pos, pids in by_position.items():
        if len(pids) > 1:
            violations.append(BackupViolation(position=pos, keep=pids[0], cut_candidates=pids[1:]))
    return violations


def bye_week_crunch(
    roster_players: list[dict],  # [{"player_id", "position", "team"}, ...]
    bye_weeks: dict[str, int],  # team abbr -> bye week number
    slot_positions: list[str],
    flex_eligible: dict[str, list[str]],
    lookahead_weeks: list[int],
) -> dict[int, list[str]]:
    """Which upcoming weeks leave a starting slot impossible to fill because
    of overlapping byes -- pure slot-count feasibility, independent of who's
    actually best. Returns week -> list of shortfall descriptions.

    Requires `bye_weeks` to be populated (2026 schedule) -- returns {} with no
    crash if it's empty, since we don't fabricate an NFL schedule we don't have.
    """
    if not bye_weeks:
        return {}

    exclusive_required = Counter(p for p in slot_positions if p not in flex_eligible)
    total_flex_slots = sum(1 for p in slot_positions if p in flex_eligible)
    flex_positions: set[str] = set()
    for positions in flex_eligible.values():
        flex_positions |= set(positions)

    crunches: dict[int, list[str]] = {}
    for week in lookahead_weeks:
        available = [p for p in roster_players if bye_weeks.get(p["team"]) != week]
        by_pos = Counter(p["position"] for p in available)
        remaining = dict(by_pos)
        issues = []

        for pos, required in exclusive_required.items():
            have = remaining.get(pos, 0)
            if have < required:
                issues.append(f"{pos}: need {required}, have {have}")
            remaining[pos] = max(0, have - required)

        flex_pool = sum(remaining.get(pos, 0) for pos in flex_positions)
        if flex_pool < total_flex_slots:
            issues.append(f"FLEX: need {total_flex_slots}, have {flex_pool}")

        if issues:
            byes_this_week = [p["player_id"] for p in roster_players if bye_weeks.get(p["team"]) == week]
            crunches[week] = issues + [f"players on bye: {byes_this_week}"]

    return crunches


def rank_bench_by_recent_value(
    client,
    league_id: str,
    roster: dict,
    player_index: PlayerIndex,
    current_week: int,
    lookback_weeks: int = 3,
) -> list[dict]:
    """Bench (non-starter) players ranked weakest-first by recent average
    points -- the practical "which body is safest to cut" list feeding faab.py.
    """
    starters = set(roster.get("starters") or [])
    bench_ids = [pid for pid in (roster.get("players") or []) if pid not in starters and pid != "0"]

    weeks = [w for w in range(max(1, current_week - lookback_weeks), current_week)]
    totals: dict[str, list[float]] = {pid: [] for pid in bench_ids}
    for week in weeks:
        try:
            matchups = client.get_matchups(league_id, week)
        except Exception:
            continue
        for m in matchups:
            if m.get("roster_id") != roster.get("roster_id"):
                continue
            for pid, pts in (m.get("players_points") or {}).items():
                if pid in totals:
                    totals[pid].append(pts or 0.0)

    ranked = []
    for pid in bench_ids:
        info = player_index.get(pid) or {}
        pts = totals.get(pid, [])
        avg = sum(pts) / len(pts) if pts else None
        ranked.append(
            {
                "player_id": pid,
                "name": info.get("full_name") or pid,
                "position": info.get("position"),
                "avg_pts_last_n": avg,
                "n_weeks": len(pts),
            }
        )
    ranked.sort(key=lambda r: (r["avg_pts_last_n"] if r["avg_pts_last_n"] is not None else -1))
    return ranked
