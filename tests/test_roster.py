from src.roster import bye_week_crunch, flag_backup_violations, flag_ir_candidates, ir_slot_usage

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
FLEX = {"FLEX": ["RB", "WR", "TE"]}


class FakePlayerIndex:
    def __init__(self, blob):
        self.blob = blob

    def get(self, pid):
        return self.blob.get(pid)


def test_flag_ir_candidates_excludes_already_reserved():
    blob = {
        "p1": {"full_name": "Hurt Guy", "injury_status": "Out"},
        "p2": {"full_name": "Fine Guy", "injury_status": None},
        "p3": {"full_name": "Already On IR", "injury_status": "IR"},
    }
    roster = {"players": ["p1", "p2", "p3"], "reserve": ["p3"]}
    candidates = flag_ir_candidates(roster, FakePlayerIndex(blob))
    assert [c.player_id for c in candidates] == ["p1"]


def test_ir_slot_usage():
    roster = {"reserve": ["p1"]}
    assert ir_slot_usage(roster, ir_slots=1) == {"used": 1, "total": 1, "empty_slots": 0}
    assert ir_slot_usage({"reserve": []}, ir_slots=1) == {"used": 0, "total": 1, "empty_slots": 1}


def test_flag_backup_violations_detects_extra_qb():
    blob = {
        "qb1": {"position": "QB"},
        "qb2": {"position": "QB"},
        "rb1": {"position": "RB"},
    }
    roster = {"players": ["qb1", "qb2", "rb1"]}
    violations = flag_backup_violations(roster, FakePlayerIndex(blob))
    assert len(violations) == 1
    assert violations[0].position == "QB"
    assert violations[0].cut_candidates == ["qb2"]


def test_flag_backup_violations_clean_roster_has_none():
    blob = {"qb1": {"position": "QB"}, "k1": {"position": "K"}, "def1": {"position": "DEF"}}
    roster = {"players": ["qb1", "k1", "def1"]}
    assert flag_backup_violations(roster, FakePlayerIndex(blob)) == []


def test_bye_week_crunch_flags_missing_position():
    roster_players = [
        {"player_id": "rb1", "position": "RB", "team": "KC"},
        {"player_id": "rb2", "position": "RB", "team": "SF"},
        {"player_id": "wr1", "position": "WR", "team": "SF"},
        {"player_id": "wr2", "position": "WR", "team": "DAL"},
        {"player_id": "te1", "position": "TE", "team": "SF"},
    ]
    bye_weeks = {"SF": 9}  # rb2, wr1, te1 all out week 9
    crunches = bye_week_crunch(roster_players, bye_weeks, SLOTS, FLEX, lookahead_weeks=[9])
    assert 9 in crunches
    assert any("TE" in issue for issue in crunches[9])


def test_bye_week_crunch_empty_without_bye_data():
    assert bye_week_crunch([], {}, SLOTS, FLEX, lookahead_weeks=[9]) == {}


def test_bye_week_crunch_no_issue_when_no_overlap():
    roster_players = [
        {"player_id": "qb1", "position": "QB", "team": "KC"},
        {"player_id": "rb1", "position": "RB", "team": "KC"},
        {"player_id": "rb2", "position": "RB", "team": "SF"},
        {"player_id": "rb3", "position": "RB", "team": "DEN"},  # bench depth, covers FLEX after SF bye
        {"player_id": "rb4", "position": "RB", "team": "NYJ"},  # extra bench depth
        {"player_id": "wr1", "position": "WR", "team": "DEN"},
        {"player_id": "wr2", "position": "WR", "team": "DAL"},
        {"player_id": "wr3", "position": "WR", "team": "MIA"},  # bench depth, covers FLEX
        {"player_id": "te1", "position": "TE", "team": "MIA"},
        {"player_id": "k1", "position": "K", "team": "KC"},
        {"player_id": "def1", "position": "DEF", "team": "KC"},
    ]
    bye_weeks = {"SF": 9}
    crunches = bye_week_crunch(roster_players, bye_weeks, SLOTS, FLEX, lookahead_weeks=[9])
    assert crunches == {}
