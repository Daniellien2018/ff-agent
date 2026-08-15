from src.explain import Decision, classify_leverage, format_decision_plaintext, format_delta, rank_decisions

LEVERAGE_CFG = {"critical": 0.010, "moderate": 0.002, "marginal": 0.0}


def test_classify_leverage_boundaries():
    assert classify_leverage(0.011, LEVERAGE_CFG) == "critical"
    assert classify_leverage(0.010, LEVERAGE_CFG) == "moderate"  # not strictly greater than critical
    assert classify_leverage(0.005, LEVERAGE_CFG) == "moderate"
    assert classify_leverage(0.002, LEVERAGE_CFG) == "marginal"
    assert classify_leverage(0.0001, LEVERAGE_CFG) == "marginal"
    assert classify_leverage(-0.015, LEVERAGE_CFG) == "critical"  # magnitude, sign-agnostic


def test_format_delta_sign():
    assert format_delta(0.009) == "+0.9% P(title)"
    assert format_delta(-0.009) == "-0.9% P(title)"


def test_rank_decisions_critical_first_then_by_magnitude():
    decisions = [
        Decision("start_sit", "marginal call", 0.001, "..."),
        Decision("start_sit", "critical call", 0.015, "..."),
        Decision("start_sit", "moderate call", 0.005, "..."),
        Decision("start_sit", "bigger critical call", -0.02, "..."),  # negative but larger magnitude
    ]
    ranked = rank_decisions(decisions, LEVERAGE_CFG)
    assert [d.action for d in ranked] == ["bigger critical call", "critical call", "moderate call", "marginal call"]


def test_format_decision_plaintext_includes_faab_line():
    d = Decision("stream_def", "STREAM DEF: X over Y", 0.005, "reason here", extra={"faab_bid": 12, "walk_away_above": 25})
    text = format_decision_plaintext(d, 1)
    assert "FAAB bid $12" in text
    assert "Walk away above $25" in text
    assert "reason here" in text
