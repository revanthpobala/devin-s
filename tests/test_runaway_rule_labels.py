"""
tests/test_runaway_rule_labels.py

E1/E2 from plan/no-lost-movers.md: the replay corpus decides the runaway lane's
label and wording. No rule_labels.json -> UNMEASURED, fail-closed. A rule that
passes the honest replay gets to say actionable; anything else keeps the
watch-only wording and the forward ledger keeps counting.
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


def test_missing_labels_are_unmeasured(tmp_path, monkeypatch):
    labels_path = tmp_path / "data" / "research" / "rule_labels.json"
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", labels_path)

    label = run_watch_alerts._runaway_rule_label()
    assert label["label"] == "UNMEASURED"
    assert label["actionable_allowed"] is False


def test_failing_corpus_keeps_unmeasured(tmp_path, monkeypatch):
    labels_path = tmp_path / "data" / "research" / "rule_labels.json"
    labels_path.parent.mkdir(parents=True)
    labels_path.write_text(json.dumps({
        "rules": {
            "zone_top_market": {
                "verdict": "UNMEASURED",
                "actionable_wording_allowed": False,
                "note": "3 fills of 12 replays, expectation -0.40R",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", labels_path)

    label = run_watch_alerts._runaway_rule_label()
    assert label["label"] == "UNMEASURED"
    assert label["actionable_allowed"] is False


def test_passing_corpus_flips_the_tag(tmp_path, monkeypatch):
    labels_path = tmp_path / "data" / "research" / "rule_labels.json"
    labels_path.parent.mkdir(parents=True)
    labels_path.write_text(json.dumps({
        "rules": {
            "breakout_trigger": {
                "verdict": "MEASURED",
                "actionable_wording_allowed": True,
                "fills": 24,
                "win_rate": 0.58,
                "expectancy_r": 0.41,
                "note": "24 resolved fills of 31 replays; 58% wins; +0.41R expectancy.",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", labels_path)

    label = run_watch_alerts._runaway_rule_label()
    assert label["label"] == "MEASURED"
    assert label["actionable_allowed"] is True
    assert label["rule"] == "breakout_trigger"


def test_rescore_message_carries_the_corpus_lane(tmp_path, monkeypatch):
    """The alert tag is the corpus's decision, not a hardcoded word."""
    from datetime import datetime

    row = {
        "ticker": "TAGX",
        "date": "2026-10-12",
        "side": "LONG",
        "entry_zone_low": 100.0,
        "entry_zone_high": 105.0,
        "breakout_level": 112.0,
        "breakout_stop": 108.0,
        "tactical_stop": 98.0,
        "target_1": 140.0,
        "target_2": 150.0,
        "invalidation_price": 98.0,
        "status": "STALKING",
        "signal_pack": 36,
        "fade": 0.0,
        "action_long": 20,
        "ext_z": 0.0,
        "atr_at_signal": 4.0,
        "raw_json": None,
    }

    # Fail-closed: no labels file -> the actionable alert is tagged UNMEASURED.
    # 108 > zone top (105), below T1 (140): R:R (140-108)/(108-98) = 3.20 clears the floor.
    labels_path = tmp_path / "rule_labels.json"
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", labels_path)
    res = run_watch_alerts._rescore_at_market("TAGX", 108.0, row, "LONG", datetime(2026, 10, 12, 12, 0), ["ZONE_TOP"])
    assert res["outcome"] == "RUNNING_ACTIONABLE"
    assert "lane UNMEASURED" in res["message"]

    # Corpus passes -> the same alert is tagged MEASURED.
    labels_path.write_text(json.dumps({
        "rules": {"zone_top_market": {"verdict": "MEASURED", "actionable_wording_allowed": True}},
    }), encoding="utf-8")
    monkeypatch.setattr(run_watch_alerts, "_rule_labels_cache", {"ts": 0.0, "data": None})
    res2 = run_watch_alerts._rescore_at_market("TAGX", 108.0, row, "LONG", datetime(2026, 10, 12, 12, 0), ["ZONE_TOP"])
    assert res2["outcome"] == "RUNNING_ACTIONABLE"
    assert "lane MEASURED" in res2["message"]
