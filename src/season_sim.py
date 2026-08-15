"""Season simulator — the control system, design doc §4.1.

Monte Carlo's the remainder of the season on the actual (already-known)
schedule, using per-team scoring distributions shrunk toward the league mean
early on (§9: "season-sim overconfidence early... shrink hard toward the
league mean or the mode switcher will whipsaw"). Everything else in this repo
is eventually scored as Δ P(title) against this.

Split deliberately into a live-fetching half (`build_season_data`,
`estimate_team_strengths`) and a pure-function half (`simulate_season`) that
takes plain data in and plain numbers out — the pure half is what's actually
worth unit-testing without hitting the network, and what optimizer.py /
streaming.py / faab.py call repeatedly to score Δ P(title).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.sleeper_client import SleeperClient

REGULAR_SEASON_WEEKS = list(range(1, 15))  # 1-14
PLAYOFF_WEEKS = [15, 16, 17]


@dataclass
class SeasonData:
    teams: list[int]  # roster_ids
    schedule: dict[int, list[tuple[int, int]]]  # week -> [(roster_a, roster_b), ...], regular season only
    completed_scores: dict[int, list[float]]  # roster_id -> actual scores for weeks already played, in week order
    current_week: int  # first NOT-yet-played week


@dataclass
class TeamStrength:
    mean: float
    var: float


@dataclass
class SimResult:
    n_trials: int
    p_playoffs: dict[int, float]
    p_bye: dict[int, float]
    p_seed: dict[int, dict[int, float]]  # roster_id -> {seed: probability}
    p_title: dict[int, float]


# --------------------------------------------------------------------- live fetch


def build_season_data(client: SleeperClient, league_id: str, current_week: int) -> SeasonData:
    """`current_week`: first week with no results yet (1 if season hasn't started)."""
    rosters = client.get_rosters(league_id)
    teams = [r["roster_id"] for r in rosters]

    schedule: dict[int, list[tuple[int, int]]] = {}
    completed_scores: dict[int, list[float]] = {t: [] for t in teams}

    for week in REGULAR_SEASON_WEEKS:
        matchups = client.get_matchups(league_id, week)
        by_matchup_id: dict[int, list[dict]] = {}
        for m in matchups:
            by_matchup_id.setdefault(m["matchup_id"], []).append(m)

        pairs = []
        for entries in by_matchup_id.values():
            if len(entries) != 2:
                continue  # bye/odd grouping — shouldn't happen with 14 teams, skip defensively
            a, b = entries
            pairs.append((a["roster_id"], b["roster_id"]))
            if week < current_week:
                completed_scores[a["roster_id"]].append(a["points"])
                completed_scores[b["roster_id"]].append(b["points"])
        schedule[week] = pairs

    return SeasonData(teams=teams, schedule=schedule, completed_scores=completed_scores, current_week=current_week)


def estimate_prior_from_scores(all_scores: list[float]) -> TeamStrength:
    """League-wide prior mean/var, e.g. pooled from a previous season's actual scores."""
    arr = np.array(all_scores, dtype=float)
    return TeamStrength(mean=float(arr.mean()), var=float(arr.var(ddof=1)) if len(arr) > 1 else 400.0)


def estimate_team_strengths(
    season_data: SeasonData,
    prior: TeamStrength,
    shrinkage_weeks: float,
    efficiency_multipliers: dict[int, float] | None = None,
) -> dict[int, TeamStrength]:
    """Empirical-Bayes shrinkage toward `prior`, per-team, weighted by how many
    real weeks that team has actually played. `shrinkage_weeks` is the pseudo-
    count of prior observations — larger means slower to trust real results.
    """
    efficiency_multipliers = efficiency_multipliers or {}
    strengths = {}
    for team in season_data.teams:
        observed = season_data.completed_scores.get(team, [])
        n = len(observed)
        obs_mean = float(np.mean(observed)) if n else prior.mean
        shrunk_mean = (shrinkage_weeks * prior.mean + n * obs_mean) / (shrinkage_weeks + n)

        if n > 1:
            obs_var = float(np.var(observed, ddof=1))
            shrunk_var = (shrinkage_weeks * prior.var + n * obs_var) / (shrinkage_weeks + n)
        else:
            shrunk_var = prior.var

        eff = efficiency_multipliers.get(team, 1.0)
        strengths[team] = TeamStrength(mean=shrunk_mean * eff, var=shrunk_var)
    return strengths


# ------------------------------------------------------------------ pure simulation


def _draw_scores(strengths: dict[int, TeamStrength], teams: list[int], week: int, n_trials: int, rng: np.random.Generator,
                  weekly_mean_overrides: dict[tuple[int, int], float] | None) -> dict[int, np.ndarray]:
    weekly_mean_overrides = weekly_mean_overrides or {}
    out = {}
    for t in teams:
        s = strengths[t]
        mean = s.mean + weekly_mean_overrides.get((t, week), 0.0)
        sd = max(s.var, 1.0) ** 0.5
        draws = rng.normal(mean, sd, size=n_trials)
        out[t] = np.clip(draws, 0.0, None)  # fantasy scores don't go negative
    return out


def simulate_season(
    season_data: SeasonData,
    strengths: dict[int, TeamStrength],
    n_trials: int = 10_000,
    seed: int | None = None,
    weekly_mean_overrides: dict[tuple[int, int], float] | None = None,
) -> SimResult:
    """Δ P(title) for any decision = call this twice (baseline vs. with the
    change encoded as a `weekly_mean_overrides` entry) and diff `p_title`.
    """
    rng = np.random.default_rng(seed)
    teams = season_data.teams
    n_teams = len(teams)
    team_idx = {t: i for i, t in enumerate(teams)}

    wins = np.zeros((n_trials, n_teams))
    points_for = np.zeros((n_trials, n_teams))

    # Weeks already played: fixed results, not simulated.
    for week in REGULAR_SEASON_WEEKS:
        if week >= season_data.current_week:
            continue
        for a, b in season_data.schedule.get(week, []):
            a_scores = season_data.completed_scores[a]
            b_scores = season_data.completed_scores[b]
            wk_index = week - 1
            if wk_index >= len(a_scores) or wk_index >= len(b_scores):
                continue
            sa, sb = a_scores[wk_index], b_scores[wk_index]
            points_for[:, team_idx[a]] += sa
            points_for[:, team_idx[b]] += sb
            wins[:, team_idx[a]] += 1 if sa > sb else (0.5 if sa == sb else 0)
            wins[:, team_idx[b]] += 1 if sb > sa else (0.5 if sa == sb else 0)

    # Remaining regular-season weeks: simulated.
    for week in REGULAR_SEASON_WEEKS:
        if week < season_data.current_week:
            continue
        draws = _draw_scores(strengths, teams, week, n_trials, rng, weekly_mean_overrides)
        for a, b in season_data.schedule.get(week, []):
            sa, sb = draws[a], draws[b]
            points_for[:, team_idx[a]] += sa
            points_for[:, team_idx[b]] += sb
            a_win = (sa > sb).astype(float)
            tie = (sa == sb).astype(float) * 0.5
            wins[:, team_idx[a]] += a_win + tie
            wins[:, team_idx[b]] += (1 - a_win - tie) + 0  # complement handles sb>sa and ties already in a_win/tie

    # Seed regular season: rank by (wins desc, points_for desc) per trial.
    # Lexsort sorts ascending by last key primarily; negate for descending.
    playoff_flags = np.zeros((n_trials, n_teams), dtype=bool)
    bye_flags = np.zeros((n_trials, n_teams), dtype=bool)
    seed_of = np.zeros((n_trials, n_teams), dtype=int)  # 1-indexed seed, 0 = missed playoffs

    order = np.lexsort((-points_for, -wins), axis=1)  # shape (n_trials, n_teams), best team first per row
    for seed_rank in range(6):
        team_positions = order[:, seed_rank]
        trial_idx = np.arange(n_trials)
        playoff_flags[trial_idx, team_positions] = True
        seed_of[trial_idx, team_positions] = seed_rank + 1
        if seed_rank < 2:
            bye_flags[trial_idx, team_positions] = True

    # Playoffs: 6-team bracket, seeds 1-2 bye into round 2 (week 16).
    # Round 1 (week 15): 3v6, 4v5. Round 2 (week 16): 1 vs lower remaining seed,
    # 2 vs other remaining seed. Round 3 (week 17): final.
    champion = np.full(n_trials, -1, dtype=int)

    seed_to_team = np.zeros((n_trials, 7), dtype=int)  # index by seed 1..6
    for seed_rank in range(6):
        team_positions = order[:, seed_rank]
        seed_to_team[:, seed_rank + 1] = team_positions

    def draw_playoff_week(participants: dict[int, np.ndarray], week: int) -> dict[int, np.ndarray]:
        """participants: seed -> array of team indices (one per trial, -1 if not in this game)."""
        return _draw_scores(strengths, teams, week, n_trials, rng, weekly_mean_overrides)

    r1_scores = draw_playoff_week({}, PLAYOFF_WEEKS[0])  # team_idx -> scores, reused by masking per game
    team_score_r1 = np.stack([r1_scores[t] for t in teams], axis=1)  # (n_trials, n_teams)

    def score_of(team_positions: np.ndarray, score_matrix: np.ndarray) -> np.ndarray:
        return score_matrix[np.arange(n_trials), team_positions]

    seed3, seed6 = seed_to_team[:, 3], seed_to_team[:, 6]
    seed4, seed5 = seed_to_team[:, 4], seed_to_team[:, 5]
    win_3v6 = score_of(seed3, team_score_r1) > score_of(seed6, team_score_r1)
    r1_winner_36 = np.where(win_3v6, seed3, seed6)
    win_4v5 = score_of(seed4, team_score_r1) > score_of(seed5, team_score_r1)
    r1_winner_45 = np.where(win_4v5, seed4, seed5)

    r2_scores = draw_playoff_week({}, PLAYOFF_WEEKS[1])
    team_score_r2 = np.stack([r2_scores[t] for t in teams], axis=1)

    seed1, seed2 = seed_to_team[:, 1], seed_to_team[:, 2]
    # Seed 1 draws the lower remaining seed (worse of the two round-1 winners by
    # seed number); seed 2 gets the other. Round-1 winner "seed number" = whichever
    # original seed (3/4/5/6) they carried; lower seed number is the stronger team.
    r1_winner_36_seed = np.where(win_3v6, 3, 6)
    r1_winner_45_seed = np.where(win_4v5, 4, 5)
    # seed1 faces whichever round-1 winner has the HIGHER seed number (weaker team)
    winner_36_is_weaker = r1_winner_36_seed > r1_winner_45_seed
    seed1_opponent = np.where(winner_36_is_weaker, r1_winner_36, r1_winner_45)
    seed2_opponent = np.where(winner_36_is_weaker, r1_winner_45, r1_winner_36)

    win_semi_a = score_of(seed1, team_score_r2) > score_of(seed1_opponent, team_score_r2)
    semi_a_winner = np.where(win_semi_a, seed1, seed1_opponent)
    win_semi_b = score_of(seed2, team_score_r2) > score_of(seed2_opponent, team_score_r2)
    semi_b_winner = np.where(win_semi_b, seed2, seed2_opponent)

    r3_scores = draw_playoff_week({}, PLAYOFF_WEEKS[2])
    team_score_r3 = np.stack([r3_scores[t] for t in teams], axis=1)

    win_final = score_of(semi_a_winner, team_score_r3) > score_of(semi_b_winner, team_score_r3)
    champion = np.where(win_final, semi_a_winner, semi_b_winner)

    p_playoffs = {t: float(playoff_flags[:, team_idx[t]].mean()) for t in teams}
    p_bye = {t: float(bye_flags[:, team_idx[t]].mean()) for t in teams}
    p_title = {t: float((champion == team_idx[t]).mean()) for t in teams}
    p_seed: dict[int, dict[int, float]] = {t: {} for t in teams}
    for t in teams:
        for s in range(1, 7):
            p_seed[t][s] = float((seed_of[:, team_idx[t]] == s).mean())

    return SimResult(n_trials=n_trials, p_playoffs=p_playoffs, p_bye=p_bye, p_seed=p_seed, p_title=p_title)


def delta_p_title(baseline: SimResult, modified: SimResult, roster_id: int) -> float:
    return modified.p_title[roster_id] - baseline.p_title[roster_id]


def determine_current_week(nfl_state: dict) -> int:
    """First week with no fantasy results yet. Preseason/offseason -> week 1."""
    if nfl_state.get("season_type") == "regular":
        return int(nfl_state["week"])
    return 1


def determine_mode(p_playoffs: float, week: int) -> str:
    """§2.3 dynamic mode switching. Encodes the doc's table directly (rather
    than generically walking config.yaml's `strategy.modes` list) because the
    precedence between the last two rows -- Salvage is a stricter subset of
    All-In Variance -- doesn't reduce to a simple "first match wins" scan.
    """
    if p_playoffs > 0.85:
        return "Chase The Bye"
    if p_playoffs >= 0.30:
        return "Win Now"
    if week >= 12 and p_playoffs < 0.15:
        return "Salvage"
    if week >= 11:
        return "All-In Variance"
    return "Win Now"
