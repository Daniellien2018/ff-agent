from src.faab import bench_spot_opportunity_cost, suggest_faab_bid

LEVERAGE_CFG = {"critical": 0.010, "moderate": 0.002, "marginal": 0.0}


def test_suggest_faab_bid_scales_with_tier():
    critical = suggest_faab_bid(0.015, 1000, LEVERAGE_CFG)
    moderate = suggest_faab_bid(0.005, 1000, LEVERAGE_CFG)
    marginal = suggest_faab_bid(0.0005, 1000, LEVERAGE_CFG)
    assert critical.bid > moderate.bid > marginal.bid
    assert critical.walk_away_above > critical.bid


def test_suggest_faab_bid_streamer_flag_overrides_tier_pct():
    # A "critical" Δ P(title) streamer (rare, but the flag should still apply
    # the flat streamer rate rather than the tier rate).
    streamer = suggest_faab_bid(0.015, 1000, LEVERAGE_CFG, is_streamer=True)
    non_streamer = suggest_faab_bid(0.015, 1000, LEVERAGE_CFG, is_streamer=False)
    assert streamer.pct_of_remaining < non_streamer.pct_of_remaining


def test_suggest_faab_bid_matches_design_doc_example_scale():
    # §5 sample: "$12 (1.2% of remaining $980)" for a DEF stream.
    bid = suggest_faab_bid(0.005, 980, LEVERAGE_CFG, is_streamer=True)
    assert bid.pct_of_remaining == 0.010
    assert bid.bid == round(980 * 0.010)


def test_bench_spot_opportunity_cost():
    assert bench_spot_opportunity_cost(4.0, 9.5) == 5.5
    assert bench_spot_opportunity_cost(None, 9.5) is None
    assert bench_spot_opportunity_cost(4.0, None) is None
