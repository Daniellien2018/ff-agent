from src.season_sim import (
    SeasonData,
    TeamStrength,
    delta_p_title,
    determine_current_week,
    determine_mode,
    estimate_team_strengths,
    simulate_season,
)


def _symmetric_season_data():
    teams = list(range(1, 15))
    schedule = {wk: [(teams[i], teams[i + 1]) for i in range(0, 14, 2)] for wk in range(1, 15)}
    return SeasonData(teams=teams, schedule=schedule, completed_scores={t: [] for t in teams}, current_week=1)


def test_symmetric_preseason_gives_baseline_odds():
    """Design doc §2.1: average team is ~1/14 title odds, 6/14 make playoffs, 2/14 get byes."""
    sd = _symmetric_season_data()
    prior = TeamStrength(mean=110, var=400)
    strengths = estimate_team_strengths(sd, prior, shrinkage_weeks=4)
    result = simulate_season(sd, strengths, n_trials=20_000, seed=42)

    assert abs(sum(result.p_title.values()) - 1.0) < 1e-9
    assert abs(sum(result.p_playoffs.values()) - 6.0) < 1e-9
    assert abs(sum(result.p_bye.values()) - 2.0) < 1e-9
    for t in sd.teams:
        assert 0.05 < result.p_title[t] < 0.10  # ~7.1% baseline, wide tolerance for MC noise


def test_stronger_team_has_higher_title_odds():
    sd = _symmetric_season_data()
    prior = TeamStrength(mean=110, var=400)
    strengths = estimate_team_strengths(sd, prior, shrinkage_weeks=4)
    strengths[1] = TeamStrength(mean=130, var=400)  # team 1 much stronger

    result = simulate_season(sd, strengths, n_trials=20_000, seed=1)
    assert result.p_title[1] > result.p_title[2]
    assert result.p_playoffs[1] > 0.9


def test_weekly_override_moves_delta_in_expected_direction():
    sd = _symmetric_season_data()
    prior = TeamStrength(mean=110, var=400)
    strengths = estimate_team_strengths(sd, prior, shrinkage_weeks=4)

    baseline = simulate_season(sd, strengths, n_trials=20_000, seed=7)
    boosted = simulate_season(sd, strengths, n_trials=20_000, seed=7, weekly_mean_overrides={(1, wk): 3.0 for wk in range(1, 15)})
    assert delta_p_title(baseline, boosted, 1) > 0


def test_shrinkage_pulls_toward_prior_when_few_weeks_played():
    sd = SeasonData(teams=[1, 2], schedule={1: [(1, 2)]}, completed_scores={1: [200.0], 2: [20.0]}, current_week=2)
    prior = TeamStrength(mean=110, var=400)
    strengths = estimate_team_strengths(sd, prior, shrinkage_weeks=100)  # huge pseudo-count -> barely moves
    assert abs(strengths[1].mean - prior.mean) < 5
    assert abs(strengths[2].mean - prior.mean) < 5

    strengths_low_shrink = estimate_team_strengths(sd, prior, shrinkage_weeks=0.01)
    assert strengths_low_shrink[1].mean > 150  # nearly all-observed with a tiny pseudo-count


def test_determine_current_week():
    assert determine_current_week({"season_type": "pre", "week": 3}) == 1
    assert determine_current_week({"season_type": "regular", "week": 5}) == 5


def test_determine_mode_matches_doc_table():
    assert determine_mode(0.90, 5) == "Chase The Bye"
    assert determine_mode(0.50, 5) == "Win Now"
    assert determine_mode(0.20, 11) == "All-In Variance"
    assert determine_mode(0.10, 12) == "Salvage"
    assert determine_mode(0.20, 3) == "Win Now"  # early season, no rule covers it -> default
