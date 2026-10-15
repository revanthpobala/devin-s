from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src import config
from src.logic.actionable_gate import dw_long_levels, dw_rr_at_market, level_mismatch
from src.logic.data_window_filter import datawindow_gate
from src.tracking.alert_db import get_eastern_date_str
from src.tracking.auto_triage_daemon import AutoTriageDaemon
from src.ui.services import opportunity_service as opp


def _dw(entry, stop, target, close, **extra):
    base = {
        "Long Entry": entry,
        "Long Stop Loss": stop,
        "Long Target": target,
        "close": close,
        "Zone RR Flags Pack": 1,
        "Signal Pack": 36,
        "Action Long Code": 1,
        "Ext Z Self Relative": 0.0,
        "atr14": 5.0,
    }
    base.update(extra)
    return base


def test_goog_levels_disagree_and_rr_derived_from_dw():
    lv = dw_long_levels(_dw(343.54, 332.32, 350.09, 343.54))
    assert dw_rr_at_market(lv) == pytest.approx(0.5837, abs=1e-3)
    reason = level_mismatch(lv, {"entry": 343.54, "stop": 326.66, "target": 381.81}, 343.54)
    assert "target 381.81 vs DW 350.09" in reason


def test_level_mismatch_none_when_close_and_missing_when_dw_incomplete():
    lv = dw_long_levels(_dw(100.0, 95.0, 115.0, 100.0))
    assert level_mismatch(lv, {"entry": 100.5, "stop": 95.5, "target": 114.0}, 100.0) is None
    assert level_mismatch(dw_long_levels({"close": 100}), {"stop": 95.0}, 100.0) == "Data Window stop/target missing"


def test_datawindow_gate_uses_dw_levels():
    with patch("src.data.datawindow_loader.load_datawindow", return_value=_dw(100.0, 95.0, 115.0, 100.0)):
        ok, fails = datawindow_gate("AAA", "2026-10-14")
    assert ok and fails == []
    with patch("src.data.datawindow_loader.load_datawindow", return_value=_dw(343.54, 332.32, 350.09, 343.54)):
        ok, fails = datawindow_gate("GOOG", "2026-10-14")
    assert not ok and any("RR@mkt" in f for f in fails)
    with patch("src.data.datawindow_loader.load_datawindow", return_value=None):
        assert datawindow_gate("NONE") == (False, ["no Data Window"])


@pytest.fixture
def coil(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    (tmp_path / "reports").mkdir()
    raw = tmp_path / "data" / "raw" / today
    raw.mkdir(parents=True)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    def run(candidate, dw, spot):
        (raw / "survivors.json").write_text(json.dumps([candidate]), encoding="utf-8")
        with patch.object(opp, "get_current_prices_batch", return_value={candidate["Ticker"]: spot}), \
             patch("src.data.datawindow_loader.load_datawindow", return_value=dw):
            rows = opp._collect_opportunities_uncached(lookback_days=1)
        return rows[0]

    return run


def _candidate(sym, sup, stop, t1, t2, atr, price):
    return {
        "Ticker": sym, "price": price, "support_level": sup, "stop_level": stop,
        "target_level": t1, "ceiling_level": t2, "atr": atr, "weinstein_stage": 2,
        "pb_funnel": 1,
    }


def test_goog_style_coil_capped_at_watch(coil):
    row = coil(
        _candidate("GOOG", 343.54, 326.66, 381.81, 400.0, 6.0, 343.54),
        _dw(343.54, 332.32, 350.09, 343.54),
        343.54,
    )
    assert row["opportunity_state"] == "WATCH"
    assert row["is_actionable"] is False
    assert any("disagree" in r for r in row["gate_reasons"])
    assert row["live_rr"] == pytest.approx(0.58, abs=0.01)


def test_coil_with_dw_levels_missing_capped_at_watch(coil):
    dw = _dw(100.0, 95.0, 115.0, 100.0)
    del dw["Long Target"]
    row = coil(_candidate("AAA", 100.0, 95.0, 115.0, 120.0, 5.0, 100.0), dw, 100.0)
    assert row["opportunity_state"] == "WATCH"
    assert any("missing" in r for r in row["gate_reasons"])


def test_coil_agreeing_with_dw_can_take_now(coil):
    row = coil(
        _candidate("AAA", 100.0, 95.0, 115.0, 120.0, 5.0, 100.0),
        _dw(100.0, 95.0, 115.0, 100.0),
        100.0,
    )
    assert row["opportunity_state"] == "TAKE_NOW"
    assert row["is_actionable"] is True


@pytest.fixture
def alerts_db(tmp_path):
    db = tmp_path / "alerts.db"
    today = get_eastern_date_str()
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """CREATE TABLE alerts (
            message_id TEXT PRIMARY KEY, date TEXT, timestamp TEXT, symbol TEXT, action TEXT,
            strategy TEXT, raw_payload TEXT, status TEXT, llm_decision TEXT, routing_stage TEXT,
            created_at TEXT)"""
        )
        for mid, sym, strat in (("e1", "GOOG", "Swing"), ("synth-AAPL-x", "AAPL", "Swing"), ("i1", "MSFT", "Intraday")):
            conn.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, today, f"{today} 10:00:00", sym, "LONG", strat, "{}", "PENDING", None, None, today),
            )
    return db


def _decision(db, mid):
    with sqlite3.connect(str(db)) as conn:
        return conn.execute("SELECT llm_decision FROM alerts WHERE message_id = ?", (mid,)).fetchone()[0]


def test_daemon_skips_llm_when_gate_fails(alerts_db):
    with patch("src.tracking.alert_db.DB_PATH", alerts_db), \
         patch("src.tracking.auto_triage_daemon._data_window_gate", return_value=(False, ["not PB funnel"])), \
         patch("src.tracking.auto_triage_daemon.evaluate_alert_payload", return_value={"llm_decision": "WATCH"}) as ev:
        d = AutoTriageDaemon(batch_size=5)
        d._process_batch()
    called = {c.args[0]["message_id"] for c in ev.call_args_list}
    assert called == {"synth-AAPL-x", "i1"}
    assert _decision(alerts_db, "e1") == "SKIPPED (no LLM): Data Window gate: not PB funnel"
    assert d.total_skipped == 1


def test_daemon_runs_llm_when_gate_passes(alerts_db):
    with patch("src.tracking.alert_db.DB_PATH", alerts_db), \
         patch("src.tracking.auto_triage_daemon._data_window_gate", return_value=(True, [])), \
         patch("src.tracking.auto_triage_daemon.evaluate_alert_payload", return_value={"llm_decision": "WATCH"}) as ev:
        d = AutoTriageDaemon(batch_size=5)
        d._process_batch()
    assert ev.call_count == 3
    assert d.total_skipped == 0
