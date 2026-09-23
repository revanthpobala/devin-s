"""
Unit tests for Intraday Quality Gates, Shadow Ledger, and Push Formatting.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


def test_grade_hard_veto():
    from src.tracking.alert_evaluator import evaluate_risk_vetoes

    dt = datetime.now(ET).replace(hour=10, minute=0, second=0, microsecond=0)
    # Grade B setup must be vetoed
    res = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="B",
    )
    assert res is not None
    hdr, pb = res
    assert "GRADE B" in hdr
    assert "HARD VETO" in pb


def test_stage_veto_env_flag(monkeypatch):
    from src.tracking.alert_evaluator import evaluate_risk_vetoes

    dt = datetime.now(ET).replace(hour=10, minute=0, second=0, microsecond=0)

    # When INTRADAY_STAGE_VETO is 0 (default), counter-stage Grade A is allowed through
    monkeypatch.setenv("INTRADAY_STAGE_VETO", "0")
    res = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
        align="Weekly Stg4 Decline",
    )
    assert res is None

    # When INTRADAY_STAGE_VETO is 1, counter-stage Grade A is blocked
    monkeypatch.setenv("INTRADAY_STAGE_VETO", "1")
    res = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
        align="Weekly Stg4 Decline",
    )
    assert res is not None
    hdr, pb = res
    assert "COUNTER-STAGE: STAGE 4 DECLINE" in hdr


def test_push_formatters():
    from src.tracking.alert_evaluator import format_intraday_entry_push, format_intraday_exit_push

    alert = {
        "symbol": "AAPL",
        "action": "BUY CALLS",
        "entry_px": 150.0,
        "entry_stop": 148.5,
        "entry_t1": 153.0,
        "grade": "A",
        "score": 92,
        "time_et": "10:05 AM ET",
        "wrong_if": "5m close below $148.50",
    }
    push_entry = format_intraday_entry_push(alert, llm_note="Clean bounce off VWAP")
    assert "[UNPROVEN] AAPL LONG" in push_entry
    assert "Entry $150.00 · Stop $148.50 (1.00%) · T1 $153.00" in push_entry
    assert "Grade A · Score 92/100 · 10:05 AM ET" in push_entry
    assert "Wrong if: 5m close below $148.50" in push_entry
    assert "Note: Clean bounce off VWAP" in push_entry

    push_exit = format_intraday_exit_push("AAPL", exit_r=1.52, session_r=2.10, reason="T2 target hit")
    assert push_exit == "[EXIT] AAPL · Exit: +1.52R · Session: +2.10R (T2 target hit)"


def test_intraday_signals_ledger_lifecycle(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_shadow.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # 1. Upsert entry signal (taken=1)
    ok = adb.upsert_intraday_signal({
        "trade_id": "TRADE_001",
        "ticker": "NVDA",
        "date": "2026-09-23",
        "entry_ts": "10:15:00",
        "hour": 10,
        "grade": "A",
        "score": 94,
        "align": "Stg2",
        "side": "LONG",
        "entry_px": 120.0,
        "entry_stop": 118.0,
        "entry_t1": 124.0,
        "taken": 1,
    })
    assert ok is True

    # 2. Upsert vetoed signal (taken=0)
    ok_veto = adb.upsert_intraday_signal({
        "trade_id": "TRADE_002",
        "ticker": "AMD",
        "date": "2026-09-23",
        "entry_ts": "10:20:00",
        "hour": 10,
        "grade": "B",
        "score": 75,
        "align": "Stg4",
        "side": "LONG",
        "entry_px": 150.0,
        "entry_stop": 147.0,
        "entry_t1": 156.0,
        "veto_reason": "⛔ STAND ASIDE (GRADE B)",
        "taken": 0,
    })
    assert ok_veto is True

    # 3. Update exit
    ok_exit = adb.record_intraday_exit("TRADE_001", exit_r=1.85, exit_why="T2 Hit")
    assert ok_exit is True

    # 4. Query and verify
    signals = adb.get_intraday_signals()
    assert len(signals) == 2

    s1 = next(s for s in signals if s["trade_id"] == "TRADE_001")
    assert s1["taken"] == 1
    assert s1["exit_r"] == 1.85
    assert s1["exit_why"] == "T2 Hit"

    s2 = next(s for s in signals if s["trade_id"] == "TRADE_002")
    assert s2["taken"] == 0
    assert "GRADE B" in s2["veto_reason"]


def test_postmortem_stats_generation(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    from src.tracking.intraday_stats import generate_postmortem_stats, regenerate_postmortem_markdown

    db_file = tmp_path / "test_stats.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # Insert mock records
    adb.upsert_intraday_signal({
        "trade_id": "T_A1", "ticker": "AAPL", "date": "2026-09-23", "entry_ts": "09:35",
        "hour": 9, "grade": "A", "score": 95, "entry_px": 150.0, "exit_r": 1.5, "taken": 1,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T_A2", "ticker": "MSFT", "date": "2026-09-23", "entry_ts": "10:15",
        "hour": 10, "grade": "A", "score": 90, "entry_px": 400.0, "exit_r": -1.0, "taken": 1,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T_B1", "ticker": "GOOGL", "date": "2026-09-23", "entry_ts": "11:00",
        "hour": 11, "grade": "B", "score": 75, "entry_px": 170.0, "exit_r": -0.8, "taken": 0,
        "veto_reason": "⛔ STAND ASIDE (GRADE B)",
    })

    stats = generate_postmortem_stats(db_file)
    assert stats["total_count"] == 3
    assert "A" in stats["by_grade"]
    assert stats["by_grade"]["A"]["scored_n"] == 2
    assert stats["by_grade"]["A"]["mean_r"] == 0.25

    # Test markdown regeneration with db_path and output_path
    out_md = tmp_path / "postmortem_live.md"
    content = regenerate_postmortem_markdown(out_md, db_path=db_file)
    assert "## 1. Go / No-Go Decision Gate (n ≥ 200 Pairs)" in content
    assert "Grade A" in content
    assert "GRADE VETO (NOT GRADE A)" in content


def test_canonical_trade_id_helper():
    from src.tracking.alert_db import get_canonical_trade_id

    # Pine trade_id present in root or payload
    assert get_canonical_trade_id({"trade_id": "PINE_123"}) == "PINE_123"
    assert get_canonical_trade_id({"raw_payload": '{"trade_id": "PINE_456"}'}) == "PINE_456"

    # Fallback to symbol_date_ts
    tid = get_canonical_trade_id({
        "symbol": "aapl",
        "date": "2026-09-23",
        "timestamp": "10:30:00",
    })
    assert tid == "AAPL_2026-09-23_10:30:00"


def test_conflict_preserves_entry_and_taken(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_conflict.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # 1. First write ENTRY
    adb.upsert_intraday_signal({
        "trade_id": "TRADE_CONF",
        "ticker": "TSLA",
        "date": "2026-09-23",
        "entry_px": 250.0,
        "stop": 245.0,
        "target_1": 260.0,
        "grade": "A",
        "score": 90,
        "taken": 1,
    })

    # 2. Subsequent write with NULL / exit data should NOT clobber entry fields or reset taken
    adb.upsert_intraday_signal({
        "trade_id": "TRADE_CONF",
        "ticker": "TSLA",
        "date": "2026-09-23",
        "entry_px": None,
        "stop": None,
        "exit_r": 2.0,
        "exit_why": "Target hit",
        "taken": 0,
    })

    signals = adb.get_intraday_signals()
    s = signals[0]
    assert s["entry_px"] == 250.0
    assert s["stop"] == 245.0
    assert s["taken"] == 1
    assert s["exit_r"] == 2.0


def test_exit_without_trade_id_pairs_latest_open_entry(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_open_exit.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # Two entries for same symbol today: first already exited, second still open
    adb.upsert_intraday_signal({
        "trade_id": "TRADE_OLD",
        "ticker": "META",
        "date": "2026-09-23",
        "entry_px": 500.0,
        "exit_r": 0.5,
        "exit_why": "Scale 1",
    })
    adb.upsert_intraday_signal({
        "trade_id": "TRADE_NEW",
        "ticker": "META",
        "date": "2026-09-23",
        "entry_px": 510.0,
        "exit_r": None,
    })

    # Exit arriving with NO trade_id should pair with TRADE_NEW (the open row)
    ok = adb.record_intraday_exit(
        trade_id=None,
        exit_r=1.5,
        exit_why="Full exit",
        ticker="META",
        date="2026-09-23",
    )
    assert ok is True

    signals = adb.get_intraday_signals()
    old_row = next(r for r in signals if r["trade_id"] == "TRADE_OLD")
    new_row = next(r for r in signals if r["trade_id"] == "TRADE_NEW")
    assert old_row["exit_r"] == 0.5
    assert new_row["exit_r"] == 1.5
    assert new_row["exit_why"] == "Full exit"


def test_format_push_null_grade_score():
    from src.tracking.alert_evaluator import format_intraday_entry_push

    alert = {
        "symbol": "XYZ",
        "action": "BUY CALLS",
        "entry_px": 50.0,
        "stop": 48.0,
        "target_1": 55.0,
        # grade and score missing
    }
    push_msg = format_intraday_entry_push(alert)
    assert "Grade NULL" in push_msg
    assert "Score NULL" in push_msg


def test_two_exit_sources_pine_and_monitor(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_two_exits.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    adb.upsert_intraday_signal({
        "trade_id": "TRADE_DUAL",
        "ticker": "AAPL",
        "date": "2026-09-23",
        "entry_px": 150.0,
        "taken": 1,
    })

    # 1. Main.py writes Pine exit r
    ok1 = adb.record_intraday_exit(
        trade_id="TRADE_DUAL",
        pine_exit_r=1.25,
        exit_why="Pine exit condition",
    )
    assert ok1 is True

    # 2. Position monitor writes its own calculated exit_r
    ok2 = adb.record_intraday_exit(
        trade_id="TRADE_DUAL",
        exit_r=0.95,
        exit_why="Stop ratcheted BE+",
    )
    assert ok2 is True

    signals = adb.get_intraday_signals()
    s = signals[0]
    assert s["pine_exit_r"] == 1.25
    assert s["exit_r"] == 0.95
    assert s["exit_why"] == "Stop ratcheted BE+"


def test_closed_trade_not_overwritten_when_no_open_entry(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_no_overwrite.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # Entry already closed
    adb.upsert_intraday_signal({
        "trade_id": "TRADE_CLOSED",
        "ticker": "AMD",
        "date": "2026-09-23",
        "entry_px": 100.0,
        "exit_r": 2.0,
        "exit_why": "Target hit",
    })

    # Another exit arriving without trade_id should NOT overwrite TRADE_CLOSED
    ok = adb.record_intraday_exit(
        trade_id=None,
        exit_r=-1.0,
        exit_why="Phantom exit",
        ticker="AMD",
        date="2026-09-23",
    )
    assert ok is False

    signals = adb.get_intraday_signals()
    assert len(signals) == 1
    assert signals[0]["exit_r"] == 2.0
    assert signals[0]["exit_why"] == "Target hit"


def test_get_day_session_r_cumulative(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_session_r.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    adb.upsert_intraday_signal({
        "trade_id": "T1", "ticker": "AAPL", "date": "2026-09-23",
        "entry_px": 150.0, "exit_r": 1.25,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T2", "ticker": "MSFT", "date": "2026-09-23",
        "entry_px": 400.0, "exit_r": -0.50,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T3", "ticker": "NVDA", "date": "2026-09-23",
        "entry_px": 120.0, "exit_r": 1.75,
    })

    total_r = adb.get_day_session_r("2026-09-23")
    assert total_r == 2.50
