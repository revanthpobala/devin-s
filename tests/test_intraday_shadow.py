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
    assert "[UNPROVEN]" not in push_entry
    assert "AAPL CALLS · Entry 150.00 · Stop 148.50 (1.00%) · T1 153.00 · R:R 2.0" in push_entry
    assert "Wrong if: 5m close below $148.50" in push_entry
    assert "Track: n=0, no read yet" in push_entry
    assert "Unmeasured: stage=NULL · phase=NULL · bias=NULL" in push_entry
    assert "AI read: Clean bounce off VWAP" in push_entry

    push_exit = format_intraday_exit_push("AAPL", exit_r=1.52, session_r=2.10, reason="T2 target hit", day_record="(3W/1L)")
    assert push_exit == "[EXIT] AAPL · +1.52R · day +2.10R (3W/1L) · why: T2 target hit"


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


def test_entry_push_conflicts():
    from src.tracking.alert_evaluator import format_intraday_entry_push

    alert = {
        "symbol": "QQQ",
        "action": "BUY CALLS",
        "entry_px": 500.0,
        "entry_stop": 490.0,
        "entry_t1": 520.0,
        "grade": "A",
        "score": 80,
        "time_et": "12:00 PM ET",
        "market_price": 506.0,
        "wrong_if": "15m close < 490.00",
    }
    push_msg = format_intraday_entry_push(alert, llm_note="Watch for CPI data release")
    assert "!! CONFLICT:" in push_msg
    assert "chase >0.5R past entry" in push_msg
    assert "midday score<85" in push_msg
    assert "CPI in note" in push_msg


def test_llm_verdict_written_and_not_nulled(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    db_file = tmp_path / "test_verdict.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # 1. Upsert with VETO
    adb.upsert_intraday_signal({
        "trade_id": "T_VETO",
        "ticker": "AAPL",
        "date": "2026-09-23",
        "entry_ts": "10:00:00",
        "llm_verdict": "VETO:Trend extension into resistance",
        "taken": 0,
    })

    signals = adb.get_intraday_signals()
    assert len(signals) == 1
    assert signals[0]["llm_verdict"] == "VETO:Trend extension into resistance"

    # 2. Subsequent upsert with None for llm_verdict does not overwrite
    adb.upsert_intraday_signal({
        "trade_id": "T_VETO",
        "ticker": "AAPL",
        "date": "2026-09-23",
        "llm_verdict": None,
        "pine_exit_r": -0.5,
    })

    signals = adb.get_intraday_signals()
    assert signals[0]["llm_verdict"] == "VETO:Trend extension into resistance"
    assert signals[0]["pine_exit_r"] == -0.5

    # 3. Subsequent upsert with empty string does not overwrite
    adb.upsert_intraday_signal({
        "trade_id": "T_VETO",
        "ticker": "AAPL",
        "date": "2026-09-23",
        "llm_verdict": "",
    })
    signals = adb.get_intraday_signals()
    assert signals[0]["llm_verdict"] == "VETO:Trend extension into resistance"

    # 4. Upsert with TAKE and GATE
    adb.upsert_intraday_signal({
        "trade_id": "T_TAKE",
        "ticker": "MSFT",
        "date": "2026-09-23",
        "llm_verdict": "TAKE",
        "taken": 1,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T_GATE",
        "ticker": "NVDA",
        "date": "2026-09-23",
        "llm_verdict": "GATE:grade",
        "taken": 0,
    })
    sig_take = next(s for s in adb.get_intraday_signals() if s["trade_id"] == "T_TAKE")
    sig_gate = next(s for s in adb.get_intraday_signals() if s["trade_id"] == "T_GATE")
    assert sig_take["llm_verdict"] == "TAKE"
    assert sig_gate["llm_verdict"] == "GATE:grade"


def test_by_llm_present_with_correct_means(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    from src.tracking.intraday_stats import generate_postmortem_stats

    db_file = tmp_path / "test_by_llm.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()

    # Insert TAKE rows (+1.0, +2.0 -> mean 1.5)
    adb.upsert_intraday_signal({
        "trade_id": "T_T1", "ticker": "AAPL", "date": "2026-09-23",
        "llm_verdict": "TAKE", "exit_r": 1.0,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T_T2", "ticker": "MSFT", "date": "2026-09-23",
        "llm_verdict": "TAKE", "exit_r": 2.0,
    })

    # Insert VETO rows (-1.0, -0.5 -> mean -0.75)
    adb.upsert_intraday_signal({
        "trade_id": "T_V1", "ticker": "NVDA", "date": "2026-09-23",
        "llm_verdict": "VETO:Midday exhaustion", "exit_r": -1.0,
    })
    adb.upsert_intraday_signal({
        "trade_id": "T_V2", "ticker": "AMD", "date": "2026-09-23",
        "llm_verdict": "VETO:Chop", "exit_r": -0.5,
    })

    # Insert GATE row (-1.0)
    adb.upsert_intraday_signal({
        "trade_id": "T_G1", "ticker": "TSLA", "date": "2026-09-23",
        "llm_verdict": "GATE:grade", "exit_r": -1.0,
    })

    # Insert UNKNOWN row: no verdict, no veto_reason, not taken
    adb.upsert_intraday_signal({
        "trade_id": "T_U1", "ticker": "UNKN", "date": "2026-09-23",
        "exit_r": 0.3,
    })

    stats = generate_postmortem_stats(db_path=db_file)
    assert "by_llm" in stats
    by_llm = stats["by_llm"]
    assert "TAKE" in by_llm
    assert "VETO" in by_llm
    assert "GATE" in by_llm
    assert "UNKNOWN" in by_llm

    assert by_llm["TAKE"]["scored_n"] == 2
    assert by_llm["TAKE"]["mean_r"] == 1.5
    assert by_llm["VETO"]["scored_n"] == 2
    assert by_llm["VETO"]["mean_r"] == -0.75
    assert by_llm["GATE"]["scored_n"] == 1
    assert by_llm["GATE"]["mean_r"] == -1.0
    assert by_llm["UNKNOWN"]["scored_n"] == 1
    assert by_llm["UNKNOWN"]["mean_r"] == 0.3


def test_no_read_stub_prints_when_n_under_30(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    from src.tracking.intraday_stats import clear_bucket_stats_cache, lookup_bucket_stats

    db_file = tmp_path / "test_bucket.db"
    monkeypatch.setattr(adb, "DB_PATH", db_file)
    adb.init_db()
    clear_bucket_stats_cache()

    # 1. With 0 matching records, prints stub
    res0 = lookup_bucket_stats(grade="A", hour=10, score=90, min_n=30, db_path=db_file)
    assert res0["read"] is False
    assert "no read yet" in res0["text"]
    assert res0["n"] == 0

    # 2. Insert 5 records (5 < 30) -> prints stub n=5
    for i in range(5):
        adb.upsert_intraday_signal({
            "trade_id": f"T_BK_{i}",
            "ticker": "AAPL",
            "date": "2026-09-23",
            "entry_ts": "10:15:00",
            "hour": 10,
            "grade": "A",
            "score": 90,
            "exit_r": 0.5,
        })
    clear_bucket_stats_cache()
    res5 = lookup_bucket_stats(grade="A", hour=10, score=90, min_n=30, db_path=db_file)
    assert res5["read"] is False
    assert res5["n"] == 5
    assert res5["text"] == "n=5, no read yet"

    # 3. Insert 25 more records (total 30 >= 30) -> read is True
    for i in range(5, 30):
        adb.upsert_intraday_signal({
            "trade_id": f"T_BK_{i}",
            "ticker": "AAPL",
            "date": "2026-09-23",
            "entry_ts": "10:15:00",
            "hour": 10,
            "grade": "A",
            "score": 90,
            "exit_r": 0.5,
        })
    clear_bucket_stats_cache()
    res30 = lookup_bucket_stats(grade="A", hour=10, score=90, min_n=30, db_path=db_file)
    assert res30["read"] is True
    assert res30["n"] == 30
    assert "Grade A 9-10h score85+" in res30["text"]
    assert "+0.50R" in res30["text"]


def test_scoreboard_returns_both_sections(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm
    from src.ui.routes.trades import get_scoreboard

    db_intraday = tmp_path / "test_sb_intra.db"
    monkeypatch.setattr(adb, "DB_PATH", db_intraday)
    adb.init_db()

    db_swing = tmp_path / "test_sb_swing.db"
    monkeypatch.setattr(wm, "DB_PATH", db_swing)
    wm.init_watch_db()

    # Seed 1 intraday row
    adb.upsert_intraday_signal({
        "trade_id": "T_SB_1",
        "ticker": "AAPL",
        "date": "2026-09-23",
        "grade": "A",
        "hour": 10,
        "score": 90,
        "llm_verdict": "TAKE",
        "exit_r": 1.2,
    })

    sb = get_scoreboard(since=None)
    assert "intraday" in sb
    assert "swing" in sb

    intra = sb["intraday"]
    assert "by_grade" in intra
    assert "by_hour" in intra
    assert "by_score" in intra
    assert "by_llm" in intra
    assert "n_scored" in intra
    assert "go_no_go" in intra
    assert intra["n_scored"] == 1

    # Every group with n < 30 carries "read": False
    assert intra["by_grade"]["A"]["read"] is False
    assert intra["by_llm"]["TAKE"]["read"] is False
    assert isinstance(sb["swing"], list)

