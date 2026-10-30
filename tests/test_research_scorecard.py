from src.logic.layered_entry import build_layers
from src.tracking import research_scorecard as rs


def b(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def test_market_limit_layer_on_a_runaway():
    """Spot 53.82 above zone 52.40-53.37: the limit never fills, market and layers catch the run to T1."""
    after = [b("2026-10-27", 54.0, 55.0, 53.6, 54.8), b("2026-10-28", 55.0, 60.0, 54.9, 59.5),
             b("2026-10-29", 59.6, 63.5, 59.0, 63.0)]
    stop, t1 = 51.04, 63.12
    assert rs.score_market(after, stop, t1) == (t1 - 54.0) / (54.0 - stop)
    assert rs.score_limit(after, 53.37, stop, t1) is None
    lay = build_layers(53.82, 52.40, 53.37, 51.04, 63.12, atr=2.5, risk_budget_usd=500)
    res = rs.score_layers(after, lay)
    assert res["fills"] == 1                     # starter only (cap 54.07 >= open 54.0)
    starter = lay["tranches"][0]
    full = sum(t["shares"] * (t["price"] - stop) for t in lay["tranches"])
    assert abs(res["r"] - starter["shares"] * (t1 - 54.0) / full) < 1e-9


def test_stop_first_on_a_shared_bar_and_limit_fill():
    after = [b("2026-10-27", 54.0, 54.2, 53.0, 53.2), b("2026-10-28", 53.1, 70.0, 50.0, 52.0)]
    assert rs.score_limit(after, 53.37, 51.04, 63.12) == -1.0   # filled at 53.37, then stop and T1 same bar
    assert rs.score_market(after, 51.04, 63.12) == -1.0


def test_unfilled_layers_cancel_when_plan_ends():
    lay = build_layers(107.54, 105.80, 106.99, 104.50, 110.20, atr=2.9, risk_budget_usd=500)  # limits only
    after = [b("2026-10-08", 108.0, 111.0, 107.5, 110.5)]           # T1 prints, nothing filled
    assert rs.score_layers(after, lay) == {"r": None, "fills": 0}


def test_score_row_buckets_and_spot_guard():
    bars = [b(f"2026-10-{d:02d}", 100, 101, 99, 100 + d * 0.0) for d in range(1, 15)]
    bars[-1] = b("2026-10-14", 110, 111, 109, 110.0)                  # +10% into the research day
    bars += [b(f"2026-10-{d:02d}", 110, 112, 109, 111.0) for d in range(15, 25)]
    spy = {x["date"]: 500.0 for x in bars}
    row = {"date": "2026-10-14", "spot_at_signal": 4725.0, "entry_low": 105, "entry_high": 107,
           "stop": 103, "target_1": 125, "atr_at_signal": 2.0}              # R:R at market (125-110)/7 = 2.14
    out = rs.score_row(row, bars, spy, sorted(spy))
    assert out["spot"] == 110.0                                       # bad stored spot replaced by the bar
    assert out["run_bucket"] == "RAN" and round(out["rel10_pre"], 3) == 0.1
    assert round(out["h5"], 4) == round(111 / 110 - 1, 4) and out["s5"] == 0.0
    assert out["limit_filled"] == 0 and out["layer_mode"] == "STARTER_PLUS_LIMITS"
