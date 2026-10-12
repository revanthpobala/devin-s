"""
tests/test_runaway_rule_labels.py

E1/E2 from plan/no-lost-movers.md: the replay corpus decides each rule's label and
whether it may say "actionable". A rule that fails (or has no corpus, or an unreadable
one) is watch-only: the alert carries the reason and no buy instruction. A rule that
passes gets the actionable wording. Fail-closed in every direction.
"""

from __future__ import annotations

import json

import pytest

from src.tracking import level_engine
import run_watch_alerts


@pytest.fixture(autouse=True)
def _clean_engine(monkeypatch):
    level_engine.reset_engine_state()
    monkeypatch.setattr(run_watch_alerts, "_rule_labels_cache", {"ts": 0.0, "data": None})
    yield
    level_engine.reset_engine_state()
    monkeypatch.setattr(run_watch_alerts, "_rule_labels_cache", {"ts": 0.0, "data": None})


PASSING_LABELS = {
    "rules": {
        "zone_top_market": {
            "verdict": "MEASURED",
            "actionable_wording_allowed": True,
            "resolved": 24,
            "win_rate": 0.58,
            "expectancy_r": 0.41,
            "note": "24 resolved fills of 31 replays; 58% wins; +0.41R.",
        },
        "high_20d_break": {
            "verdict": "UNMEASURED",
            "actionable_wording_allowed": False,
            "note": "3 resolved fills of 12 replays; expectation -0.40R.",
        },
    }
}


def _write_labels(tmp_path, monkeypatch, payload):
    labels_path = tmp_path / "rule_labels.json"
    if payload is not None:
        labels_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", labels_path)
    return labels_path


def test_cross_kind_maps_to_its_rule():
    """The rule that decides the wording is the rule the crossing belongs to."""
    assert run_watch_alerts.runaway_rule_for(["ZONE_TOP"]) == "zone_top_market"
    assert run_watch_alerts.runaway_rule_for(["BREAKOUT"]) == "zone_top_market"
    assert run_watch_alerts.runaway_rule_for(["HIGH_20D"]) == "high_20d_break"
    assert run_watch_alerts.runaway_rule_for(["T1"], reached_target=True) == "post_t1_reentry"


def test_missing_labels_are_watch_only(tmp_path, monkeypatch):
    _write_labels(tmp_path, monkeypatch, None)
    label = run_watch_alerts._runaway_rule_label("zone_top_market")
    assert label["label"] == "UNMEASURED"
    assert label["actionable_allowed"] is False
    assert label["source"] == "absent"


def test_failing_rule_is_watch_only(tmp_path, monkeypatch):
    _write_labels(tmp_path, monkeypatch, PASSING_LABELS)
    label = run_watch_alerts._runaway_rule_label("high_20d_break")
    assert label["label"] == "UNMEASURED"
    assert label["actionable_allowed"] is False


def test_passing_rule_may_say_actionable(tmp_path, monkeypatch):
    _write_labels(tmp_path, monkeypatch, PASSING_LABELS)
    label = run_watch_alerts._runaway_rule_label("zone_top_market")
    assert label["label"] == "MEASURED"
    assert label["actionable_allowed"] is True
    assert label["source"] == "replay_corpus"


def test_rescore_gate_pass_on_unmeasured_rule_is_watch_only(tmp_path, monkeypatch):
    """The headline case: the gate and R:R clear, the corpus did not measure the rule —
    so the alert says watch-only with the reason, never 'BUY 100 at market'."""
    from datetime import datetime

    # zone_top measured NEGATIVE (the real corpus result: -0.33R over 43 fills).
    negative = {"rules": {"zone_top_market": {
        "verdict": "UNMEASURED", "actionable_wording_allowed": False,
        "note": "43 resolved fills; 37% wins; expectancy -0.33R.",
    }}}
    _write_labels(tmp_path, monkeypatch, negative)

    row = {
        "ticker": "NEG1", "date": "2026-10-12", "side": "LONG",
        "entry_zone_low": 100.0, "entry_zone_high": 105.0,
        "breakout_level": 112.0, "breakout_stop": 108.0,
        "tactical_stop": 95.0, "target_1": 140.0, "target_2": 150.0,
        "invalidation_price": 95.0, "status": "STALKING",
        "signal_pack": 36, "fade": 0.0, "action_long": 20, "ext_z": 0.0, "atr_at_signal": 4.0,
        "raw_json": None,
    }
    res = run_watch_alerts._rescore_at_market("NEG1", 108.0, row, "LONG", datetime(2026, 10, 12, 12, 0), ["ZONE_TOP"])

    assert res["gates_ok"] is True           # the math clears: R:R 3.20@mkt, gates clear
    assert res["outcome"] == "RUNNING_NO_ROOM"
    assert res["lane"] == "UNMEASURED"
    assert "watch-only" in res["message"]
    assert "BUY 100" not in res["message"]
    assert "-0.33R" in res["message"]


def test_rescore_actionable_wording_needs_a_measured_rule(tmp_path, monkeypatch):
    from datetime import datetime

    _write_labels(tmp_path, monkeypatch, PASSING_LABELS)
    row = {
        "ticker": "POS1", "date": "2026-10-12", "side": "LONG",
        "entry_zone_low": 100.0, "entry_zone_high": 105.0,
        "breakout_level": 112.0, "breakout_stop": 108.0,
        "tactical_stop": 95.0, "target_1": 140.0, "target_2": 150.0,
        "invalidation_price": 95.0, "status": "STALKING",
        "signal_pack": 36, "fade": 0.0, "action_long": 20, "ext_z": 0.0, "atr_at_signal": 4.0,
        "raw_json": None,
    }
    res = run_watch_alerts._rescore_at_market("POS1", 108.0, row, "LONG", datetime(2026, 10, 12, 12, 0), ["ZONE_TOP"])

    assert res["outcome"] == "RUNNING_ACTIONABLE"
    assert "BUY 100 POS1 at market-$108.00" in res["message"]
    assert "lane MEASURED" in res["message"]

    # The same name crossing its 20-day high is a different rule with a failing corpus.
    res2 = run_watch_alerts._rescore_at_market("POS1", 108.0, row, "LONG", datetime(2026, 10, 12, 12, 0), ["HIGH_20D"])
    assert res2["outcome"] == "RUNNING_NO_ROOM"
    assert "watch-only" in res2["message"]


def test_gate_failure_is_still_no_room_even_on_a_measured_rule(tmp_path, monkeypatch):
    from datetime import datetime

    _write_labels(tmp_path, monkeypatch, PASSING_LABELS)
    row = {
        "ticker": "THIN", "date": "2026-10-12", "side": "LONG",
        "entry_zone_low": 100.0, "entry_zone_high": 105.0,
        "tactical_stop": 100.5,          # razor-thin stop: stop width ~ 0 ATR
        "target_1": 106.0, "target_2": 110.0,
        "invalidation_price": 100.5, "status": "STALKING",
        "signal_pack": 4,                # no PB bit
        "fade": 1.0, "action_long": 20, "ext_z": 0.0, "atr_at_signal": 4.0,
        "raw_json": None,
    }
    res = run_watch_alerts._rescore_at_market("THIN", 105.5, row, "LONG", datetime(2026, 10, 12, 12, 0), ["ZONE_TOP"])
    assert res["gates_ok"] is False
    assert res["outcome"] == "RUNNING_NO_ROOM"
    assert "ran, no room" in res["message"]
