from src.optimizer import compute_lineup_swaps

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
FLEX = {"FLEX": ["RB", "WR", "TE"]}


class FakePlayerIndex:
    def __init__(self, blob):
        self.blob = blob

    def get(self, pid):
        return self.blob.get(pid)


def test_identifies_a_clean_bench_upgrade():
    blob = {
        "qb1": {"full_name": "QB One", "position": "QB"},
        "rb1": {"full_name": "RB One", "position": "RB"},
        "rb2": {"full_name": "RB Two (weak)", "position": "RB"},
        "rb3": {"full_name": "RB Three (bench, hot)", "position": "RB"},
        "wr1": {"full_name": "WR One", "position": "WR"},
        "wr2": {"full_name": "WR Two", "position": "WR"},
        "te1": {"full_name": "TE One", "position": "TE"},
        "k1": {"full_name": "K One", "position": "K"},
        "def1": {"full_name": "DEF One", "position": "DEF"},
    }
    roster = {
        "players": list(blob.keys()),
        "starters": ["qb1", "rb1", "rb2", "wr1", "wr2", "te1", "k1", "def1"],
    }
    pts_index = {
        "qb1": 18.0, "rb1": 15.0, "rb2": 4.0, "rb3": 12.0,  # rb3 clearly better than started rb2
        "wr1": 10.0, "wr2": 9.0, "te1": 8.0, "k1": 7.0, "def1": 6.0,
    }
    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DEF"]  # no FLEX in this fixture, keep it simple
    swaps = compute_lineup_swaps(roster, pts_index, FakePlayerIndex(blob), slots, {})
    assert len(swaps) == 1
    assert swaps[0].start_id == "rb3"
    assert swaps[0].bench_id == "rb2"
    assert swaps[0].delta_pts == 8.0


def test_no_swaps_when_current_lineup_is_already_optimal():
    blob = {
        "qb1": {"full_name": "QB", "position": "QB"},
        "rb1": {"full_name": "RB", "position": "RB"},
        "k1": {"full_name": "K", "position": "K"},
    }
    roster = {"players": list(blob.keys()), "starters": ["qb1", "rb1", "k1"]}
    pts_index = {"qb1": 18.0, "rb1": 15.0, "k1": 7.0}
    slots = ["QB", "RB", "K"]
    swaps = compute_lineup_swaps(roster, pts_index, FakePlayerIndex(blob), slots, {})
    assert swaps == []


def test_flex_swap_finds_best_leftover_regardless_of_position():
    blob = {
        "rb1": {"full_name": "RB1", "position": "RB"},
        "rb2": {"full_name": "RB2", "position": "RB"},
        "wr1": {"full_name": "WR1", "position": "WR"},
        "wr2": {"full_name": "WR2 (started, weak)", "position": "WR"},
        "te1": {"full_name": "TE1 (bench, elite)", "position": "TE"},
    }
    roster = {
        "players": list(blob.keys()),
        "starters": ["rb1", "rb2", "wr1", "wr2"],  # wr2 in the FLEX slot
    }
    pts_index = {"rb1": 10.0, "rb2": 8.0, "wr1": 9.0, "wr2": 3.0, "te1": 20.0}
    slots = ["RB", "RB", "WR", "FLEX"]
    swaps = compute_lineup_swaps(roster, pts_index, FakePlayerIndex(blob), slots, FLEX)
    assert len(swaps) == 1
    assert swaps[0].start_id == "te1"
    assert swaps[0].bench_id == "wr2"


def test_bye_player_never_recommended_over_any_real_projection():
    blob = {
        "rb1": {"full_name": "RB1 (on bye)", "position": "RB"},
        "rb2": {"full_name": "RB2 (bench, playing)", "position": "RB"},
    }
    roster = {"players": list(blob.keys()), "starters": ["rb1"]}
    pts_index = {"rb2": 6.0}  # rb1 has no entry at all -- on bye, contributes 0
    slots = ["RB"]
    swaps = compute_lineup_swaps(roster, pts_index, FakePlayerIndex(blob), slots, {})
    assert len(swaps) == 1
    assert swaps[0].start_id == "rb2"
    assert swaps[0].bench_id == "rb1"
