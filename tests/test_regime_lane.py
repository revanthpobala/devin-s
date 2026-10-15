from src.tracking import regime_lane as rl

T = {"edges": [0.27, 0.44, 0.58, 0.75], "lanes": {"ZONE_RR": {"Q1 narrow": {"r_rel": 0.139, "n": 10}, "Q5 broad": {"r_rel": 0.104, "n": 5}}}}


def test_bucket_edges():
    assert rl.bucket(0.1, T["edges"]) == "Q1 narrow" and rl.bucket(0.9, T["edges"]) == "Q5 broad" and rl.bucket(0.5, T["edges"]) == "Q3"


def test_lane_context_maps_breadth_and_missing():
    r = rl.lane_context({"breadth": {"advancing": 10, "declining": 85, "flat": 5}}, T)
    assert r["quintile"] == "Q1 narrow" and r["advancing_pct"] == 10.0 and r["lanes"]["ZONE_RR"]["r_rel"] == 0.139
    assert rl.lane_context({}, T) == {"available": False}


def test_protection_only_on_event_with_hedge():
    snap = {"hedge": {"beta_dollars": 200000, "spy_notional_for_25pct_cut": 50000}}
    rel = [{"when": "Fri 8:30", "releases": [{"title": "CPI"}], "exposed": [{"underlying": "SPY"}]}]
    p = rl.index_protection(snap, rel, {}, 500.0)
    assert p["contracts"] == 1 and "CPI" in p["events"][0]
    assert rl.index_protection(snap, [{"when": "x", "releases": [], "exposed": []}], {}, 500.0) is None
    assert rl.index_protection({}, rel, {}, 500.0) is None
