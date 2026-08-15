"""Small live-data helpers shared across the weekly scripts: free-agent team
lookups for streaming, and the previous-season prior for the season simulator.
Split out of scripts/weekly_lineup.py so recap.py and inactives_check.py don't
duplicate them.
"""

from __future__ import annotations

from src.players import TEAM_ALIASES
from src.projections.sleeper_source import ProjectionsUnavailable, build_pts_ppr_index, fetch_projections, roster_projected_total
from src.season_sim import TeamStrength, estimate_prior_from_scores
from src.sleeper_client import SleeperClient


def fetch_prior_from_previous_season(client: SleeperClient, previous_league_id: str, weeks: list[int]) -> TeamStrength:
    all_scores = []
    for week in weeks:
        try:
            matchups = client.get_matchups(previous_league_id, week)
        except Exception:
            continue
        for m in matchups:
            pts = m.get("points")
            if pts:
                all_scores.append(pts)
    if not all_scores:
        return TeamStrength(mean=110.0, var=400.0)
    return estimate_prior_from_scores(all_scores)


def free_agent_teams(rosters: list[dict]) -> set[str]:
    """Team abbreviations (i.e. defenses) not currently rostered by anyone."""
    rostered_ids = set()
    for r in rosters:
        rostered_ids |= set(r.get("players") or [])
    return set(TEAM_ALIASES.keys()) - rostered_ids


def free_agent_kicker_teams(rosters: list[dict], players_blob: dict) -> set[str]:
    """Teams whose current kicker isn't rostered by anyone."""
    rostered_ids = set()
    for r in rosters:
        rostered_ids |= set(r.get("players") or [])
    free_teams = set()
    for pid, info in players_blob.items():
        if info and info.get("position") == "K" and pid not in rostered_ids and info.get("team"):
            free_teams.add(info["team"])
    return free_teams


def current_position_team(roster: dict, players_blob: dict, position: str) -> str | None:
    """The team abbreviation behind this roster's currently-rostered DEF/K
    (DEF player_id already *is* the team abbreviation)."""
    for pid in roster.get("players") or []:
        info = players_blob.get(pid)
        if info and info.get("position") == position:
            return pid if position == "DEF" else info.get("team")
    return None


def name_by_roster_id(rosters: list[dict], users: list[dict]) -> dict[int, str]:
    name_by_user = {u["user_id"]: (u.get("display_name") or u["user_id"]) for u in users}
    return {r["roster_id"]: name_by_user.get(r.get("owner_id"), "?") for r in rosters}


def compute_current_week_overrides(
    cfg: dict,
    rosters: list[dict],
    strengths: dict,
    my_roster_id: int,
    opponent_roster_id: int | None,
    current_week: int,
) -> tuple[dict[tuple[int, int], float], float | None, float | None]:
    """Real per-player projections (§4.3, Sleeper/RotoWire feed) for THIS week,
    expressed as season_sim weekly_mean_overrides on top of the team-level
    historical model -- which has zero signal to distinguish teams before any
    games are played, and is otherwise all the "Projected" number is built
    from. Returns (overrides, my_real_projection, opponent_real_projection);
    overrides is {} and both projections are None if the feed's unavailable,
    so callers fall back to the plain historical mean rather than crash.
    """
    try:
        projections = fetch_projections(cfg["league"]["season"], current_week, cache_dir=cfg["cache"]["dir"])
    except ProjectionsUnavailable:
        return {}, None, None

    pts_index = build_pts_ppr_index(projections)
    my_roster = next(r for r in rosters if r["roster_id"] == my_roster_id)
    my_proj = roster_projected_total(my_roster.get("starters") or [], pts_index)
    overrides = {(my_roster_id, current_week): my_proj - strengths[my_roster_id].mean}

    opp_proj = None
    if opponent_roster_id is not None:
        opp_roster = next(r for r in rosters if r["roster_id"] == opponent_roster_id)
        opp_proj = roster_projected_total(opp_roster.get("starters") or [], pts_index)
        overrides[(opponent_roster_id, current_week)] = opp_proj - strengths[opponent_roster_id].mean

    return overrides, my_proj, opp_proj
