"""Shared optimal-lineup math — reused by league_intel.py's efficiency baseline
(§2.6) and by optimizer.py (Phase 4) so both agree on what "optimal" means.

The only real complexity in this league's roster is FLEX eligibility (RB/WR/TE
sharing 2 slots). This is solved exactly with a two-phase greedy, not an ILP:

  1. Fill each exclusive slot (QB, RB, WR, TE, K, DEF) with the top-N players
     of that exact position, N = how many such slots exist.
  2. Fill FLEX slots with the top-N leftover players from any FLEX-eligible
     position, by points.

This is optimal, not just a heuristic: exclusive slots form a partition
matroid (fixed capacity per position) and FLEX is a uniform matroid over the
remaining FLEX-eligible players; a weighted basis of a matroid is found
exactly by greedy. (This holds for one FLEX "family" sharing one eligibility
set, which is this league's actual structure — both FLEX slots accept the
same RB/WR/TE set. It would need real matroid-union augmentation, not this
greedy, if the league had two *differently*-restricted flex categories, e.g.
FLEX + SUPER_FLEX at once. Not this league's shape, so not built.)

Originally attempted via PuLP/CBC, but the bundled CBC binary is x86_64-only
and doesn't run on Apple Silicon in this environment — hence the from-scratch
greedy instead of an ILP dependency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LineupPlayer:
    player_id: str
    position: str
    points: float


def optimal_lineup(
    players: list[LineupPlayer],
    slot_positions: list[str],
    flex_eligible: dict[str, list[str]],
) -> tuple[float, dict[str, str]]:
    """Maximize total points assigning players to slots respecting eligibility.

    `slot_positions`: e.g. ["QB","RB","RB","WR","WR","TE","FLEX","FLEX","K","DEF"]
    `flex_eligible`: e.g. {"FLEX": ["RB","WR","TE"]}

    Returns (total_points, {slot_label: player_id}), slot_label disambiguating
    duplicates as "RB_0", "RB_1", etc. Slots left empty (not enough eligible
    players) simply don't appear in the assignment.
    """
    exclusive_positions = [p for p in slot_positions if p not in flex_eligible]
    flex_slot_names = [p for p in slot_positions if p in flex_eligible]

    assignment: dict[str, str] = {}
    used_ids: set[str] = set()
    total = 0.0

    # Phase 1: exclusive slots, grouped by position, best-first.
    from collections import Counter

    required_by_position = Counter(exclusive_positions)
    slot_counters: dict[str, int] = {}

    for position, count in required_by_position.items():
        candidates = sorted(
            (p for p in players if p.position == position and p.player_id not in used_ids),
            key=lambda p: p.points,
            reverse=True,
        )
        for p in candidates[:count]:
            idx = slot_counters.get(position, 0)
            assignment[f"{position}_{idx}"] = p.player_id
            slot_counters[position] = idx + 1
            used_ids.add(p.player_id)
            total += p.points

    # Phase 2: FLEX slots pooled across all flex slot names that share the
    # same eligibility list (this league: just "FLEX" x2).
    for flex_name in set(flex_slot_names):
        n_slots = flex_slot_names.count(flex_name)
        eligible_positions = set(flex_eligible[flex_name])
        leftover = sorted(
            (p for p in players if p.position in eligible_positions and p.player_id not in used_ids),
            key=lambda p: p.points,
            reverse=True,
        )
        for p in leftover[:n_slots]:
            idx = slot_counters.get(flex_name, 0)
            assignment[f"{flex_name}_{idx}"] = p.player_id
            slot_counters[flex_name] = idx + 1
            used_ids.add(p.player_id)
            total += p.points

    return round(total, 2), assignment
