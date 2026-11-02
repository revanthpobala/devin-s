from src.logic import actionable_gate as ag
from src.logic.level_validation import _pine_drift
from src.tracking import trade_rules as tr


def test_one_r_target_band_and_rounding(monkeypatch):
    monkeypatch.setattr(tr, "min_rr", lambda: 1.0)
    t = tr.one_r_target(0.7, 100.003, 97.0, 101.0)
    assert t == 103.01 and (t - 100.003) / (100.003 - 97.0) >= 1.0
    assert tr.one_r_target(0.49, 100, 97, 101) is None
    assert tr.one_r_target(1.0, 100, 97, 101) is None
    assert tr.one_r_target(0.7, 100, 101, 101) is None
    assert tr.one_r_target(0.7, 100, 97, 104) is None
    assert tr.one_r_target(0.7, 4725.0, 33.0, 36.0) is None
    monkeypatch.setattr(tr, "min_rr", lambda: 1.5)
    assert tr.one_r_target(0.7, 100, 97, 101) is None


def test_gate_reads_one_r_plan_as_rr_one(monkeypatch):
    monkeypatch.setattr(ag.rr_config, "min_rr", lambda: 1.0)
    dw = {"long_in_zone": 1.0, "long_rr_at_market": 0.7, "signal_pack": 36.0, "fade_long": 0.0, "action_long": 10.0,
          "ext_z": 0.5, "atr14": 2.0, "price": 100.0, "long_stop_loss": 97.0}
    plan = {"side": "LONG", "entry": 100.0, "stop": 97.0, "atr": 2.0}
    ok, why = ag.is_actionable(dw, plan)
    assert any("RR@mkt 0.7" in w for w in why)
    ok, why = ag.is_actionable(dw, {**plan, "one_r_target": True})
    assert not any("RR@mkt" in w for w in why)


def test_pine_drift_skips_raised_target():
    dw = {"atr14": 2.0, "Long Target": 101.0, "Long Stop Loss": 97.0}
    plan = {"tactical_stop": 97.0, "target_1": 120.0}
    assert [d["field"] for d in _pine_drift(plan, dw, "LONG")] == ["target_1"]
    assert _pine_drift({**plan, "one_r_target": True}, dw, "LONG") == []
