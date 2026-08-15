"""Manager intel from the 2025 season — design doc §2.6.

Same 14 managers, same scoring, one season of real behavior to mine:
  - lineup efficiency: actual points scored vs. the optimal lineup they could
    have started, per week (uses lineup_math's exact FLEX-aware solver)
  - transaction activity: who's still engaged, who's checked out
  - panic behavior: adds dropped again within a couple weeks — a proxy for
    managers who bail after one bad week, i.e. free acquisitions later
  - trade history: who trades at all

This is what lets season_sim.py treat a disengaged manager's realized
distribution as worse than their roster implies (§4.1 step 2), and gives
faab.py/trades.py a ranked list of soft targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.lineup_math import LineupPlayer, optimal_lineup
from src.players import PlayerIndex
from src.sleeper_client import SleeperClient

REGULAR_SEASON_WEEKS = list(range(1, 15))

# Adds dropped again within this many weeks count as "panic" for the purposes
# of the impulsiveness score. Short enough to catch one-bad-week overreactions,
# long enough not to flag ordinary roster churn.
PANIC_WINDOW_WEEKS = 2


@dataclass
class ManagerReport:
    roster_id: int
    display_name: str
    lineup_efficiency: float | None  # actual/optimal, averaged across weeks; None if no data
    n_weeks_scored: int
    n_transactions: int
    last_transaction_week: int | None
    panic_score: float | None  # fraction of adds dropped again within PANIC_WINDOW_WEEKS; None if no adds
    n_trades: int


def compute_lineup_efficiency(
    client: SleeperClient,
    league_id: str,
    player_index: PlayerIndex,
    slot_positions: list[str],
    flex_eligible: dict[str, list[str]],
    weeks: list[int] = REGULAR_SEASON_WEEKS,
) -> dict[int, dict]:
    """roster_id -> {"actual_total", "optimal_total", "efficiency", "n_weeks"}."""
    totals: dict[int, dict] = {}

    for week in weeks:
        try:
            matchups = client.get_matchups(league_id, week)
        except Exception:
            continue
        if not matchups or all(m.get("points", 0) == 0 for m in matchups):
            continue  # week not played yet

        for m in matchups:
            roster_id = m["roster_id"]
            actual = m.get("points", 0.0)
            players_points = m.get("players_points") or {}
            if not players_points:
                continue

            lineup_players = []
            for pid, pts in players_points.items():
                info = player_index.get(pid)
                position = (info or {}).get("position") or ("DEF" if pid.isalpha() else None)
                if position is None:
                    continue
                lineup_players.append(LineupPlayer(player_id=pid, position=position, points=pts or 0.0))

            optimal_total, _ = optimal_lineup(lineup_players, slot_positions, flex_eligible)

            bucket = totals.setdefault(roster_id, {"actual_total": 0.0, "optimal_total": 0.0, "n_weeks": 0})
            bucket["actual_total"] += actual
            bucket["optimal_total"] += optimal_total
            bucket["n_weeks"] += 1

    for roster_id, bucket in totals.items():
        bucket["efficiency"] = (
            bucket["actual_total"] / bucket["optimal_total"] if bucket["optimal_total"] > 0 else None
        )

    return totals


def compute_transaction_activity(client: SleeperClient, league_id: str, weeks: list[int] = REGULAR_SEASON_WEEKS) -> dict[int, dict]:
    """roster_id -> {"n_transactions", "last_transaction_week", "n_trades", "adds": [(player_id, week)], "drops": [(player_id, week)]}"""
    activity: dict[int, dict] = {}

    def bucket(roster_id: int) -> dict:
        return activity.setdefault(
            roster_id, {"n_transactions": 0, "last_transaction_week": None, "n_trades": 0, "adds": [], "drops": []}
        )

    for week in weeks:
        try:
            txns = client.get_transactions(league_id, week)
        except Exception:
            continue
        for t in txns or []:
            if t.get("status") != "complete":
                continue
            roster_ids = t.get("roster_ids") or []
            is_trade = t.get("type") == "trade"

            for roster_id in roster_ids:
                b = bucket(roster_id)
                b["n_transactions"] += 1
                b["last_transaction_week"] = week
                if is_trade:
                    b["n_trades"] += 1

            adds = t.get("adds") or {}
            drops = t.get("drops") or {}
            for player_id, roster_id in adds.items():
                bucket(roster_id)["adds"].append((player_id, week))
            for player_id, roster_id in drops.items():
                bucket(roster_id)["drops"].append((player_id, week))

    return activity


def compute_panic_score(activity: dict[int, dict]) -> dict[int, float | None]:
    """Fraction of a manager's adds that they dropped again within PANIC_WINDOW_WEEKS."""
    scores = {}
    for roster_id, b in activity.items():
        adds = b["adds"]
        if not adds:
            scores[roster_id] = None
            continue
        drop_weeks_by_player: dict[str, list[int]] = {}
        for pid, wk in b["drops"]:
            drop_weeks_by_player.setdefault(pid, []).append(wk)

        n_panicked = 0
        for pid, add_week in adds:
            drop_weeks = drop_weeks_by_player.get(pid, [])
            if any(add_week <= dw <= add_week + PANIC_WINDOW_WEEKS for dw in drop_weeks):
                n_panicked += 1
        scores[roster_id] = n_panicked / len(adds)
    return scores


def build_manager_report(
    client: SleeperClient,
    league_id: str,
    player_index: PlayerIndex,
    slot_positions: list[str],
    flex_eligible: dict[str, list[str]],
) -> list[ManagerReport]:
    rosters = client.get_rosters(league_id)
    users = client.get_users(league_id)
    name_by_user_id = {u["user_id"]: (u.get("display_name") or u["user_id"]) for u in users}
    name_by_roster_id = {r["roster_id"]: name_by_user_id.get(r.get("owner_id"), "?") for r in rosters}

    efficiency = compute_lineup_efficiency(client, league_id, player_index, slot_positions, flex_eligible)
    activity = compute_transaction_activity(client, league_id)
    panic = compute_panic_score(activity)

    reports = []
    for r in rosters:
        rid = r["roster_id"]
        eff = efficiency.get(rid, {})
        act = activity.get(rid, {"n_transactions": 0, "last_transaction_week": None, "n_trades": 0})
        reports.append(
            ManagerReport(
                roster_id=rid,
                display_name=name_by_roster_id.get(rid, "?"),
                lineup_efficiency=eff.get("efficiency"),
                n_weeks_scored=eff.get("n_weeks", 0),
                n_transactions=act["n_transactions"],
                last_transaction_week=act["last_transaction_week"],
                panic_score=panic.get(rid),
                n_trades=act["n_trades"],
            )
        )
    return sorted(reports, key=lambda m: (m.lineup_efficiency if m.lineup_efficiency is not None else 1.0))
