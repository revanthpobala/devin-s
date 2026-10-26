from src.tracking import counterfactual as cf


def _b(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


ROW = {"entry_high": 100.0, "stop": 96.0, "target_1": 106.0, "atr_at_signal": 2.0}


def test_zone_limit_fills_when_price_returns_and_hits_target():
    bars = [_b(103, 104, 102, 103), _b(102, 103, 99.5, 102), _b(102, 107, 101, 106)] + [_b(106, 106.5, 105, 106)] * 30
    out = cf.policy_replay(ROW, bars)
    assert out["r_zone"] == 1.5 and out["r_p25"] == (106 - 100.5) / 4.5 and out["mature"] == 1
    assert out["r_open"] == (106 - 103) / 7


def test_raised_limit_fills_where_zone_limit_does_not():
    bars = [_b(103, 104, 100.4, 103), _b(103, 107, 101, 106)]
    out = cf.policy_replay(ROW, bars)
    assert out["r_zone"] is None and out["r_p10"] is None and out["r_p25"] == (106 - 100.5) / 4.5
    assert out["mature"] == 0


def test_stop_first_and_gap_below_stop_skip():
    out = cf.policy_replay(ROW, [_b(95, 99, 94, 98)] + [_b(98, 99, 97, 98)] * 30)
    assert out["r_open"] is None and out["r_zone"] is None
    out = cf.policy_replay(ROW, [_b(101, 107, 95, 100)])
    assert out["r_zone"] == -1.0 and out["r_open"] == -1.0


def test_component_scorecard_groups_and_rates():
    from src.tracking.trust_report import component_scorecard

    rows = [
        {"source": "rule", "setup_lane": "A", "gate_status": "PASS", "verdict": "STALK", "scored_at": "t", "fill_price": 10, "r_net": 1.0},
        {"source": "rule", "setup_lane": "A", "gate_status": "PASS", "verdict": "STALK", "scored_at": "t", "fill_price": 10, "r_net": -1.0},
        {"source": "rule", "setup_lane": "A", "gate_status": "PASS", "verdict": "STALK", "scored_at": "t", "exit_reason": "MISSED_RUNAWAY"},
        {"source": "rule", "setup_lane": "A", "gate_status": "PASS", "verdict": "STALK"},
        {"source": "judge", "setup_lane": "B", "gate_status": "REJECTED_BY_GATE", "verdict": "ENTER", "kind": "UPDATE"},
    ]
    out = component_scorecard(rows)
    assert len(out) == 1 and out[0]["n"] == 4 and out[0]["scored"] == 3
    assert out[0]["fill_rate_pct"] == 66.7 and out[0]["miss_rate_pct"] == 33.3 and out[0]["realized"]["sum_r"] == 0.0
