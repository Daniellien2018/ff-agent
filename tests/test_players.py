from src.players import PlayerIndex, normalize


FAKE_BLOB = {
    "4046": {"full_name": "Patrick Mahomes", "position": "QB", "search_full_name": "patrickmahomes"},
    "6794": {"full_name": "Justin Jefferson", "position": "WR"},
    "8151": {"full_name": "Kenneth Walker III", "position": "RB"},
    "KC": {"full_name": "Kansas City Chiefs", "position": "DEF"},
}


class FakeClient:
    def get_players(self, sport="nfl", force_refresh=False):
        return FAKE_BLOB


def test_normalize_strips_suffix_and_punctuation():
    assert normalize("Kenneth Walker III") == "kenneth walker"
    assert normalize("A.J. Brown") == "aj brown"
    assert normalize("  Ja'Marr   Chase  ") == "jamarr chase"


def test_resolve_exact_match():
    idx = PlayerIndex(FakeClient())
    idx.load()
    result = idx.resolve("Patrick Mahomes")
    assert result.player_id == "4046"
    assert result.score == 100.0


def test_resolve_fuzzy_typo():
    idx = PlayerIndex(FakeClient())
    idx.load()
    result = idx.resolve("Pat Mahomes")
    assert result.player_id == "4046"


def test_resolve_missing_suffix():
    idx = PlayerIndex(FakeClient())
    idx.load()
    result = idx.resolve("Kenneth Walker")
    assert result.player_id == "8151"


def test_resolve_defense_by_nickname():
    idx = PlayerIndex(FakeClient())
    idx.load()
    result = idx.resolve("Chiefs")
    assert result.player_id == "KC"


def test_resolve_unrelated_name_returns_none_below_cutoff():
    idx = PlayerIndex(FakeClient())
    idx.load()
    result = idx.resolve("Xyzzy Quux Nonexistent", score_cutoff=90)
    assert result.player_id is None


def test_self_test_reports_accuracy():
    idx = PlayerIndex(FakeClient())
    idx.load()
    report = idx.self_test([("Pat Mahomes", "4046"), ("Kenneth Walker", "8151")])
    assert report["accuracy"] == 1.0
    assert report["n_correct"] == 2
