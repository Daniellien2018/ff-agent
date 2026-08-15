import pytest

from src.streaming import DOME_TEAMS, StreamRecommendation, expected_def_points, expected_k_points, rank_defenses
from src.projections.vegas_source import TeamGameContext, VegasUnavailable


def test_expected_def_points_monotonic_in_opponent_total():
    # Weaker opposing offense (lower implied total) should score the defense more.
    assert expected_def_points(3) > expected_def_points(17) > expected_def_points(31)


def test_expected_def_points_matches_band_edges_roughly():
    shutout_ish = expected_def_points(0)
    blowout_allowed = expected_def_points(38)
    assert shutout_ish > 10  # 10 (band) + bonus
    assert blowout_allowed < 0  # -4 band swamps the small bonus


def test_expected_k_points_dome_bonus_and_scaling():
    assert expected_k_points(28, False) > expected_k_points(14, False)
    assert expected_k_points(21, True) > expected_k_points(21, False)
    assert "MIN" in DOME_TEAMS and "CHI" not in DOME_TEAMS


def test_rank_defenses_degrades_gracefully_without_odds_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    rec = rank_defenses(["KC", "BUF"], cache_dir=str(tmp_path))
    assert isinstance(rec, StreamRecommendation)
    assert rec.available is False
    assert "ODDS_API_KEY" in rec.reason


def test_rank_defenses_ranks_lowest_opponent_total_first(monkeypatch, tmp_path):
    fake_context = {
        "KC": TeamGameContext("KC", "DEN", True, 27.0, 14.0, 41.0, "2026-09-14T17:00:00Z"),
        "BUF": TeamGameContext("BUF", "NYJ", False, 24.0, 24.0, 48.0, "2026-09-14T17:00:00Z"),
    }
    monkeypatch.setattr("src.streaming.get_week_context", lambda cache_dir=None: fake_context)
    rec = rank_defenses(["KC", "BUF"], cache_dir=str(tmp_path))
    assert rec.available is True
    # KC's opponent (DEN) implied total 14.0 is lower than BUF's opponent (NYJ) 24.0,
    # so KC's DEF should score better and rank first.
    assert rec.ranked[0]["team"] == "KC"
