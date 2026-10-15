from src.ui.routes.trades import _action_for


def act(**kw):
    base = dict(side="LONG", status="STALKING", last=100.0, low=95.0, high=97.0, stop=90.0, t1=120.0, taken=False,
                filled=False, hit_target=False, hit_stop=False, gate_ok=False, gate_fails=[])
    base.update(kw)
    return _action_for(**base)


def test_in_zone_gate_pass_is_act():
    a = act(last=96.0, gate_ok=True)
    assert a["label"] == "ACT" and a["rr_live"] == 4.0


def test_in_zone_gate_fail_is_skip_with_real_reason():
    a = act(last=96.0, gate_fails=["signal_pack missing", "RR@mkt 1.3 < 2.0 floor"])
    assert a["label"] == "SKIP" and "RR@mkt 1.3" in a["why"]


def test_waiting_above_zone_uses_zone_top_rr():
    a = act(last=99.0)
    assert a["label"] == "WAIT" and "$97.00" in a["why"]
    assert act(last=99.0, t1=100.0)["label"] == "SKIP"


def test_missed_and_dead_are_not_called_stopped():
    assert act(status="MISSED_RUNAWAY")["label"] == "MISSED"
    assert act(status="INVALIDATED")["group"] == "DEAD"


def test_taken_trade_is_hold_then_exit_or_profit():
    assert act(taken=True)["label"] == "HOLD"
    assert act(taken=True, hit_target=True, last=121.0)["label"] == "TAKE PROFIT"
    assert act(taken=True, hit_stop=True, last=89.0)["label"] == "EXIT"
    assert act(filled=True)["label"] == "TRACKING"


def test_short_is_levels_only():
    assert act(side="SHORT")["label"] == "NO EDGE"


def test_missed_reason_separates_touched_from_ran_away():
    from src.ui.routes.trades import _missed_reason

    plan = {"ticker": "AMZN", "date": "2026-09-29", "entry_zone_low": 245.0, "entry_zone_high": 248.5}
    z = {"AMZN": {"touches": [{"d": "2026-10-02", "p": 246.0}]}}
    assert _missed_reason(plan, z).startswith("touched the zone on 2026-10-02")
    assert _missed_reason(plan, {}).startswith("no touch of the zone recorded")
    assert _missed_reason({**plan, "entry_zone_low": 0}, z) is None


def test_in_zone_good_rr_with_only_missing_flags_is_check():
    a = act(last=96.0, gate_fails=["signal_pack missing", "atr14 missing for stop-width check"])
    assert a["label"] == "CHECK" and "Data Window" in a["why"]


def test_stop_width_reason_is_labelled_unmeasured():
    a = act(last=96.0, gate_fails=["stop width 0.328 ATR < 0.7 floor"])
    assert a["label"] == "SKIP" and "UNMEASURED" in a["why"]
