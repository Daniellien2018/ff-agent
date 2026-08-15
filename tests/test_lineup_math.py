from src.lineup_math import LineupPlayer, optimal_lineup

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
FLEX = {"FLEX": ["RB", "WR", "TE"]}


def test_full_roster_trivial_assignment():
    players = [
        LineupPlayer("qb1", "QB", 20),
        LineupPlayer("rb1", "RB", 18), LineupPlayer("rb2", "RB", 10),
        LineupPlayer("wr1", "WR", 12), LineupPlayer("wr2", "WR", 8),
        LineupPlayer("te1", "TE", 9),
        LineupPlayer("k1", "K", 7),
        LineupPlayer("def1", "DEF", 5),
    ]
    total, assignment = optimal_lineup(players, SLOTS, FLEX)
    assert total == sum(p.points for p in players)
    assert len(assignment) == len(players)


def test_flex_prefers_best_leftover_regardless_of_position():
    """A 3rd elite TE should out-flex a mediocre RB3, even though the RB pool
    has 'more RBs' -- this is the case a naive per-position-first greedy gets
    wrong if it doesn't pool the leftovers across positions for FLEX.
    """
    players = [
        LineupPlayer("te1", "TE", 25), LineupPlayer("te2", "TE", 22), LineupPlayer("te3", "TE", 20),
        LineupPlayer("rb1", "RB", 10), LineupPlayer("rb2", "RB", 8), LineupPlayer("rb3", "RB", 6), LineupPlayer("rb4", "RB", 4),
        LineupPlayer("wr1", "WR", 9), LineupPlayer("wr2", "WR", 7), LineupPlayer("wr3", "WR", 5),
    ]
    slots = ["RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]
    total, assignment = optimal_lineup(players, slots, FLEX)
    # Hand-computed optimal: RB1,RB2 / WR1,WR2 / TE1 / FLEX=TE2,TE3 = 101
    assert total == 101.0
    assert assignment["FLEX_0"] in ("te2", "te3")
    assert assignment["FLEX_1"] in ("te2", "te3")


def test_insufficient_players_leaves_slot_unfilled():
    players = [LineupPlayer("qb1", "QB", 20), LineupPlayer("rb1", "RB", 10)]
    total, assignment = optimal_lineup(players, SLOTS, FLEX)
    assert total == 30.0
    assert "DEF_0" not in assignment
    assert "K_0" not in assignment
