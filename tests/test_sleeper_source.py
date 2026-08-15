from src.projections.sleeper_source import build_pts_ppr_index, roster_projected_total

FAKE_PROJECTIONS = [
    {"player_id": "1001", "stats": {"pts_ppr": 19.5}},
    {"player_id": "1002", "stats": {"pts_ppr": 8.2}},
    {"player_id": "1003", "stats": {"adp_dd_ppr": 999.0}},  # no pts_ppr -- irrelevant deep player
    {"player_id": "1004", "stats": {}},
]


def test_build_pts_ppr_index_skips_entries_without_pts_ppr():
    idx = build_pts_ppr_index(FAKE_PROJECTIONS)
    assert idx == {"1001": 19.5, "1002": 8.2}


def test_roster_projected_total_sums_starters_only():
    idx = build_pts_ppr_index(FAKE_PROJECTIONS)
    total = roster_projected_total(["1001", "1002", "1003"], idx)
    assert total == 27.7  # 1003 missing from index contributes 0, not an error


def test_roster_projected_total_ignores_empty_slot_marker():
    idx = build_pts_ppr_index(FAKE_PROJECTIONS)
    total = roster_projected_total(["1001", "0"], idx)
    assert total == 19.5
