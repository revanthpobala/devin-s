import json

from src.logic.deep_research.deterministic import _withholdable, numbers, parse_json


def _obj(**kw):
    d = {"side": "LONG", "verdict": "CASH_SKIP", "veto": "NONE",
         "entry": {"kind": "NONE", "low": None, "high": None, "trigger": None},
         "stop": None, "target_1": None, "target_2": None, "trim": "NONE"}
    d.update(kw)
    return d


def test_parse_truncated_json_repaired():
    """IREN case: finish_reason=length cut the object mid-array inside `concurrence`."""
    d = _obj(verdict="ARMED", stop="PINE_STOP", target_1="PINE_TARGET",
             entry={"kind": "ZONE", "low": "PINE_ZONE_LOW", "high": "PINE_ZONE_HIGH", "trigger": None},
             concurrence=["A and B agree on the base", "both see the wall"])
    text = json.dumps(d)[:-8]  # cut inside the concurrence array
    out = parse_json(text)
    assert out is not None
    assert out["verdict"] == "ARMED"
    assert out["stop"] == "PINE_STOP"


def test_parse_fenced_json():
    d = _obj()
    out = parse_json("Here is the plan:\n```json\n" + json.dumps(d, indent=2) + "\n```")
    assert out is not None and out["verdict"] == "CASH_SKIP"


def test_parse_empty_returns_none():
    assert parse_json("") is None
    assert parse_json(None) is None
    assert parse_json("no json here") is None


def test_repaired_missing_required_keys_rejected():
    """Truncation ate target_1: the salvaged dict is missing a required key -> reject."""
    text = '{"side": "LONG", "verdict": "ARMED", "entry": {"kind": "NONE"}, "stop": "PINE_STOP", "conc'
    assert parse_json(text) is None


def test_trailing_prose_after_json():
    d = _obj()
    out = parse_json(json.dumps(d) + "\n\nHope this helps. Let me know.")
    assert out is not None and out["verdict"] == "CASH_SKIP"


def test_scenario_confirm_thresholds_withholdable():
    """COHR case: 'RVOL above 1.0' inside confirm is a condition, not a claim."""
    assert _withholdable("scenarios[0].confirm[1]")
    assert _withholdable("scenarios[3].confirm[0]")
    assert not _withholdable("scenarios[0].thesis")


def test_sec_accession_numbers_masked():
    """IREN rerun case: an SEC accession id in veto_fact must not be traced as a figure."""
    assert numbers("filed under accession 0001878848") == []
    assert numbers("accession 0001878848-25-000123") == []
    assert numbers("revenue grew 12.5% to 4.2B") == ["12.5", "4.2"]


def test_stress_day_measured_lanes_first(monkeypatch):
    """2026-11-05 dump: 14 OVERSOLD names outranked 2 code-20 passes on raw rr_at_market.
    On a stress tape the measured lanes lead the queue."""
    from src.logic import data_window_filter as dwf

    monkeypatch.setattr(dwf, "index_stressed", lambda: True)
    cohr = {"ticker": "COHR", "triage": "PASS", "action": "REVERSAL BUY", "mode": "REVERSION_LONG",
            "in_zone": False, "rr_at_market": 4.45}
    lrcx = {"ticker": "LRCX", "triage": "PASS", "action": "WAIT", "mode": "NONE",
            "setup_lane": "OVERSOLD", "in_zone": False, "rr_at_market": 5.19}
    ranked = dwf.rank_pass_tickers([lrcx, cohr])
    assert ranked[0]["ticker"] == "COHR"

    monkeypatch.setattr(dwf, "index_stressed", lambda: False)
    ranked = dwf.rank_pass_tickers([lrcx, cohr])
    assert ranked[0]["ticker"] == "LRCX"  # normal order: rr_at_market outranks the lane tag
