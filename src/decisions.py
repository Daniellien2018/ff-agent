"""Shared decision-building: DEF/K streaming scored as Δ P(title). Used by
both weekly_lineup.py (Sunday) and recap.py (pre-waiver) so the two emails
can't disagree about what the best available stream is.
"""

from __future__ import annotations

from src.explain import Decision
from src.faab import suggest_faab_bid
from src.league_data import current_position_team, free_agent_kicker_teams, free_agent_teams
from src.season_sim import simulate_season
from src.streaming import expected_def_points, expected_k_points, rank_defenses, rank_kickers


def build_streaming_decisions(
    cfg: dict,
    rosters: list[dict],
    players_blob: dict,
    roster_id: int,
    season_data,
    strengths: dict,
    baseline_p_title: float,
    current_week: int,
) -> tuple[list[Decision], list[str]]:
    my_roster = next(r for r in rosters if r["roster_id"] == roster_id)
    changes: list[Decision] = []
    watch_items: list[str] = []
    n_trials = cfg["simulation"]["n_trials"]
    cache_dir = cfg["cache"]["dir"]
    budget = cfg["league_mechanics"]["waivers"]["budget"]
    leverage_cfg = cfg["leverage_thresholds"]

    my_def_id = current_position_team(my_roster, players_blob, "DEF")
    def_candidates = list(free_agent_teams(rosters) | ({my_def_id} if my_def_id else set()))
    def_rec = rank_defenses(def_candidates, cache_dir=cache_dir)

    if def_rec.available and def_rec.ranked:
        best = def_rec.ranked[0]
        if best["team"] != my_def_id:
            current_pts = (
                expected_def_points(
                    next((r["implied_opponent_total"] for r in def_rec.ranked if r["team"] == my_def_id), best["implied_opponent_total"])
                )
                if my_def_id
                else 0.0
            )
            delta_pts = best["expected_points"] - current_pts
            modified = simulate_season(
                season_data, strengths, n_trials=n_trials, weekly_mean_overrides={(roster_id, current_week): delta_pts}
            )
            dpt = modified.p_title[roster_id] - baseline_p_title
            bid = suggest_faab_bid(dpt, budget, leverage_cfg, is_streamer=True)
            changes.append(
                Decision(
                    kind="stream_def",
                    action=f"STREAM DEF: {best['team']} over {my_def_id or 'empty slot'}",
                    delta_p_title=dpt,
                    rationale=best["reason"],
                    extra={"faab_bid": bid.bid, "walk_away_above": bid.walk_away_above},
                )
            )
    elif not def_rec.available:
        watch_items.append(f"DEF streaming unavailable: {def_rec.reason}")

    my_k_team = current_position_team(my_roster, players_blob, "K")
    k_candidates = list(free_agent_kicker_teams(rosters, players_blob) | ({my_k_team} if my_k_team else set()))
    k_rec = rank_kickers(k_candidates, cache_dir=cache_dir)

    if k_rec.available and k_rec.ranked:
        best = k_rec.ranked[0]
        if best["team"] != my_k_team:
            current_pts = (
                expected_k_points(
                    next((r["implied_team_total"] for r in k_rec.ranked if r["team"] == my_k_team), best["implied_team_total"]),
                    False,
                )
                if my_k_team
                else 0.0
            )
            delta_pts = best["expected_points"] - current_pts
            modified = simulate_season(
                season_data, strengths, n_trials=n_trials, weekly_mean_overrides={(roster_id, current_week): delta_pts}
            )
            dpt = modified.p_title[roster_id] - baseline_p_title
            bid = suggest_faab_bid(dpt, budget, leverage_cfg, is_streamer=True)
            changes.append(
                Decision(
                    kind="stream_k",
                    action=f"STREAM K: {best['team']} kicker over {my_k_team or 'empty slot'}",
                    delta_p_title=dpt,
                    rationale=best["reason"],
                    extra={"faab_bid": bid.bid, "walk_away_above": bid.walk_away_above},
                )
            )
    elif not k_rec.available:
        watch_items.append(f"K streaming unavailable: {k_rec.reason}")

    return changes, watch_items
