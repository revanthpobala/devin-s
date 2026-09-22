"""
Unit tests for the recalibrated risk gates in alert_evaluator.py.

Verifies that the old blanket vetoes (Grade-B <80, mid-morning <90, exposure cap 2,
DAY PAUSE always-on) have been replaced by env-tunable defaults:
  GRADE_B_VETO_THRESHOLD=65  MID_MORNING_MIN_SCORE=85  MAX_CONCURRENT_SAME_SIDE=3  DAY_PAUSE_ENABLED=off
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pytest

from zoneinfo import ZoneInfo

from src.tracking import alert_db
from src.tracking.alert_evaluator import evaluate_risk_vetoes

ET = ZoneInfo("America/New_York")


# All tests start from the recalibrated defaults.
@pytest.fixture(autouse=True)
def default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADE_B_VETO_THRESHOLD", "65")
    monkeypatch.setenv("MID_MORNING_MIN_SCORE", "85")
    monkeypatch.setenv("MAX_CONCURRENT_SAME_SIDE", "3")
    monkeypatch.setenv("DAY_PAUSE_ENABLED", "false")
    monkeypatch.setenv("INTRADAY_EXCLUDED_TICKERS", "")


def et_ts(hour: int, minute: int = 0) -> datetime:
    """Build a same-day ET datetime at the given hour:minute (today in ET)."""
    return datetime.now(ET).replace(hour=hour, minute=minute, second=0, microsecond=0)


def call(symbol: str, score: int, hour: int, minute: int = 0, grade: str = "A",
         align: str = "", side: str = "LONG", rvol: Optional[float] = None) -> Any:
    return evaluate_risk_vetoes(
        symbol=symbol,
        action="BUY CALLS" if side == "LONG" else "SELL PUTS",
        score=score,
        current_time_et=f"{hour:02d}:{minute:02d}",
        eastern_dt=et_ts(hour, minute),
        grade=grade,
        align=align,
        rvol=rvol,
    )


# ---------------------------------------------------------------------------
# Gate 1: Grade-A quality gate (default: hard veto below 65, Grade-B 65-79 passes)
# ---------------------------------------------------------------------------

def test_grade_b_75_no_longer_vetoed():
    result = call("AAPL", 75, 9, 45, grade="B")
    assert result is None, "Score 75 (Grade B) must pass the quality gate (new default 65)."


def test_score_64_still_vetoed():
    result = call("AAPL", 64, 9, 45, grade="C")
    assert result is not None
    assert "STAND ASIDE" in result[0]
    assert "QUALITY VETO" in result[1]


def test_grade_b_threshold_env_respects_env():
    # Raise the threshold back to 80 -> score 75 is vetoed again.
    os.environ["GRADE_B_VETO_THRESHOLD"] = "80"
    try:
        result = call("AAPL", 75, 9, 45, grade="B")
        assert result is not None
    finally:
        os.environ.pop("GRADE_B_VETO_THRESHOLD", None)


def test_grade_b_threshold_env_looser():
    # Lower the threshold to 50 -> score 60 (Grade C) now passes.
    os.environ["GRADE_B_VETO_THRESHOLD"] = "50"
    try:
        result = call("AAPL", 60, 9, 45, grade="C")
        assert result is None
    finally:
        os.environ.pop("GRADE_B_VETO_THRESHOLD", None)


# ---------------------------------------------------------------------------
# Gate 3: Mid-morning exhaustion (default: require score >= 85, not >= 90)
# ---------------------------------------------------------------------------

def test_mid_morning_85_now_passes():
    result = call("AAPL", 85, 10, 45, grade="A")
    assert result is None, "Score 85 must pass mid-morning with new default 85."


def test_mid_morning_84_still_vetoed():
    result = call("AAPL", 84, 10, 45, grade="A")
    assert result is not None
    assert "EXHAUSTION" in result[0]


def test_mid_morning_90_env_recovers_old_behavior():
    os.environ["MID_MORNING_MIN_SCORE"] = "90"
    try:
        result = call("AAPL", 89, 10, 45, grade="A")
        assert result is not None
    finally:
        os.environ.pop("MID_MORNING_MIN_SCORE", None)


def test_mid_morning_outside_window_ignores_low_score():
    # 09:45 is before 10:30 -> no mid-morning veto even at low score (>=65).
    result = call("AAPL", 70, 9, 45, grade="B")
    assert result is None


# ---------------------------------------------------------------------------
# Gate 4: Max concurrent same-direction exposure (default: cap 3, not 2)
# ---------------------------------------------------------------------------

def test_exposure_cap_default_allows_two_and_blocks_three(monkeypatch):
    # Two same-side open -> still under cap of 3 -> passes.
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {"AAPL": _pos("AAPL", "LONG"),
                                 "MSFT": _pos("MSFT", "LONG")})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is None, "Two same-direction opens must pass (cap 3)."

    # Three same-side open -> at cap -> vetoed.
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {"AAPL": _pos("AAPL", "LONG"),
                                 "MSFT": _pos("MSFT", "LONG"),
                                 "NVDA": _pos("NVDA", "LONG")})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is not None
    assert "MAX EXPOSURE" in result[0]


def test_exposure_env_cap_two(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_SAME_SIDE", "2")
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {"AAPL": _pos("AAPL", "LONG"),
                                 "MSFT": _pos("MSFT", "LONG")})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is not None, "Cap 2 must block at 2 same-direction opens."


def test_exposure_env_cap_four(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_SAME_SIDE", "4")
    # Gate vetoes when len(same_side) >= cap, so cap 4 passes with 3 same-side opens.
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {s: _pos(s, "LONG") for s in ["AAPL", "MSFT", "NVDA"]})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is None, "Cap 4 must allow a 4th same-direction entry with 3 open."


def test_exposure_counts_only_same_side(monkeypatch):
    # Opposite-side (SHORT) opens do not count against a LONG entry.
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {"AAPL": _pos("AAPL", "SHORT"),
                                 "MSFT": _pos("MSFT", "SHORT"),
                                 "NVDA": _pos("NVDA", "SHORT")})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is None, "SHORT opens must not block a LONG entry."


def test_exposure_ignores_other_day_and_non_intraday(monkeypatch):
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {"AAPL": _pos("AAPL", "LONG", opened_at="2026-09-20"),
                                 "MSFT": _pos("MSFT", "LONG", strategy="Swing")})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is None, "Other-day and non-Intraday (Swing) positions must not count."


# ---------------------------------------------------------------------------
# Gate 5: Lunch chop (1:15 PM - 2:45 PM ET; require score >= 88; RVOL > 1.8x bypass)
# ---------------------------------------------------------------------------

def test_lunch_chop_84_vetoed():
    result = call("AAPL", 84, 13, 30, grade="A")
    assert result is not None
    assert "LUNCH CHOP" in result[0]


def test_lunch_chop_88_passes():
    result = call("AAPL", 88, 13, 30, grade="A")
    assert result is None


def test_lunch_chop_87_vetoed():
    result = call("AAPL", 87, 13, 30, grade="A")
    assert result is not None
    assert "LUNCH CHOP" in result[0]


def test_lunch_chop_rvol_bypass():
    # RVOL > 1.8x bypasses the lunch chop gate even with score < 88.
    result = call("AAPL", 80, 13, 30, grade="A", rvol=2.0)
    assert result is None, "RVOL > 1.8x must bypass the lunch chop gate."


def test_lunch_chop_rvol_below_threshold_still_vetoed():
    # RVOL <= 1.8x does NOT bypass the gate.
    result = call("AAPL", 80, 13, 30, grade="A", rvol=1.5)
    assert result is not None
    assert "LUNCH CHOP" in result[0]


def test_lunch_chop_rvol_none_no_bypass():
    # RVOL=None (not provided) means no bypass — gate fires normally.
    result = call("AAPL", 80, 13, 30, grade="A", rvol=None)
    assert result is not None
    assert "LUNCH CHOP" in result[0]


def test_lunch_chop_outside_window_no_veto():
    # 12:15 PM ET is OUTSIDE the new 1:15-2:45 PM ET window — no veto regardless of score.
    result = call("AAPL", 70, 12, 15, grade="A")
    assert result is None, "Outside the lunch chop window, low score must not veto."


# ---------------------------------------------------------------------------
# Gate 6: DAY PAUSE (default OFF; opt-in via DAY_PAUSE_ENABLED)
# ---------------------------------------------------------------------------

def _make_stop_db(tmp_path: Path, n_stops: int, stop_minutes_ago: list[int]) -> Path:
    """Create a temp DB with `n_stops` recent intraday EXIT events that are losses.

    The DAY PAUSE gate reads trade_events.details.net_pnl (authoritative loss signal),
    so the fixture mirrors that schema rather than the alerts table."""
    db_path = tmp_path / "veto.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trade_events (
            id INTEGER PRIMARY KEY,
            trade_id TEXT,
            event_type TEXT,
            symbol TEXT,
            strategy_id TEXT,
            timestamp TEXT,
            details TEXT
        )
    """)
    base = datetime.now(ET).replace(second=0, microsecond=0)
    for i, mins in enumerate(stop_minutes_ago):
        # aware ISO-8601 ET timestamp (matches how position_state writes trade_events)
        ts = (base - timedelta(minutes=mins)).isoformat()
        det = json.dumps({"exit_reason": "Confirmed stop breach.", "net_pnl": -50.0})
        conn.execute(
            "INSERT INTO trade_events (trade_id, event_type, symbol, strategy_id, timestamp, details) VALUES (?,?,?,?,?,?)",
            (f"stop-{i}", "EXIT", "AAPL", "Intraday", ts, det),
        )
    conn.commit()
    conn.close()
    return db_path


def test_day_pause_off_by_default_never_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = _make_stop_db(tmp_path, 2, [40, 20])
    monkeypatch.setattr(alert_db, "DB_PATH", str(db_path))
    dt = datetime.now(ET).replace(second=0, microsecond=0)
    result = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
    )
    assert result is None, "DAY PAUSE must be OFF by default (gem spec)."


def test_day_pause_on_fires_after_two_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # DAY PAUSE compares eastern_dt against the exit timestamps; use "now" so
    # the 20/40-min-old stops fall inside the 45-min cooldown window.
    db_path = _make_stop_db(tmp_path, 2, [40, 20])
    monkeypatch.setattr(alert_db, "DB_PATH", str(db_path))
    monkeypatch.setenv("DAY_PAUSE_ENABLED", "true")
    dt = datetime.now(ET).replace(second=0, microsecond=0)
    result = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
    )
    assert result is not None
    assert "DAY PAUSE" in result[0]


def test_day_pause_on_stale_stops_not_fired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Stops are 2 hours old -> outside the 45-min window -> no pause.
    db_path = _make_stop_db(tmp_path, 2, [120, 90])
    monkeypatch.setattr(alert_db, "DB_PATH", str(db_path))
    monkeypatch.setenv("DAY_PAUSE_ENABLED", "true")
    dt = datetime.now(ET).replace(second=0, microsecond=0)
    result = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
    )
    assert result is None


def test_day_pause_win_breaks_streak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A winning exit (net_pnl >= 0) between two losses breaks the consecutive streak.
    db_path = tmp_path / "veto.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trade_events (
            id INTEGER PRIMARY KEY, trade_id TEXT, event_type TEXT, symbol TEXT,
            strategy_id TEXT, timestamp TEXT, details TEXT
        )
    """)
    base = datetime.now(ET).replace(second=0, microsecond=0)
    # newest first: a WIN 10min ago, then two losses 30/40min ago -> streak broken by the win.
    rows = [
        ("win-0", (base - timedelta(minutes=10)).isoformat(), json.dumps({"exit_reason": "target hit", "net_pnl": 200.0})),
        ("loss-1", (base - timedelta(minutes=30)).isoformat(), json.dumps({"exit_reason": "stop", "net_pnl": -50.0})),
        ("loss-2", (base - timedelta(minutes=40)).isoformat(), json.dumps({"exit_reason": "stop", "net_pnl": -50.0})),
    ]
    for tid, ts, det in rows:
        conn.execute(
            "INSERT INTO trade_events (trade_id, event_type, symbol, strategy_id, timestamp, details) VALUES (?,?,?,?,?,?)",
            (tid, "EXIT", "AAPL", "Intraday", ts, det),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(alert_db, "DB_PATH", str(db_path))
    monkeypatch.setenv("DAY_PAUSE_ENABLED", "true")
    dt = datetime.now(ET).replace(second=0, microsecond=0)
    result = evaluate_risk_vetoes(
        symbol="AAPL", action="BUY CALLS", score=90,
        current_time_et=dt.strftime("%I:%M %p"), eastern_dt=dt, grade="A",
    )
    assert result is None, "A winning exit must break the consecutive-loss streak."


# ---------------------------------------------------------------------------
# Counter-stage and excluded gates (regression: unchanged behavior)
# ---------------------------------------------------------------------------

def test_counter_stage_stg4_call_vetoed():
    result = call("AAPL", 90, 9, 45, align="stg4 decline")
    assert result is not None
    assert "COUNTER-STAGE" in result[0]


def test_counter_stage_stg2_put_vetoed():
    result = call("AAPL", 90, 9, 45, side="SHORT", align="stg2 advance")
    assert result is not None
    assert "COUNTER-STAGE" in result[0]


def test_excluded_ticker_vetoed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTRADAY_EXCLUDED_TICKERS", "AAPL")
    result = call("AAPL", 90, 9, 45, grade="A")
    assert result is not None
    assert "EXCLUDED" in result[0]


def _pos(symbol: str, side: str, opened_at: str | None = None, strategy: str = "Intraday") -> dict:
    # NOTE: strategy is stored as "Intraday" (capital I) by open_position. The exposure
    # gate must match it case-insensitively — a regression to exact-lowercase matching
    # silently disables the cap (see test_exposure_gate_matches_real_intraday_strategy).
    # Default opened_at to today: the gate only counts positions opened today in ET,
    # so a stale hardcoded date makes same_side empty and the cap never fires.
    if opened_at is None:
        opened_at = datetime.now(ET).strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "opened_at": opened_at,
    }


def test_exposure_gate_matches_real_intraday_strategy(monkeypatch):
    """Regression: the exposure gate must count positions written with strategy='Intraday'
    (capital I), which is what open_position actually stores. If it only matched lowercase
    'intraday', same_side would always be empty and the cap would never fire."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {s: _pos(s, "LONG", opened_at=today) for s in ["AAPL", "MSFT", "NVDA"]})
    result = call("GOOGL", 90, 9, 45, side="LONG")
    assert result is not None, "3 real Intraday-strategy LONG opens must trip the cap-3 exposure gate."
    assert "MAX EXPOSURE" in result[0]


# ---------------------------------------------------------------------------
# Action -> side mapping (regression: substring matching misread exit strings)
# ---------------------------------------------------------------------------

def test_action_side_mapping():
    from src.tracking.alert_evaluator import _action_side
    assert _action_side("ENTER_CALLS") == "LONG"
    assert _action_side("ENTER_PUTS") == "SHORT"
    assert _action_side("BUY CALLS") == "LONG"
    assert _action_side("SELL PUTS") == "SHORT"
    # Exit/neutral strings must NOT be treated as directional entries.
    assert _action_side("EXIT") is None
    assert _action_side("EXIT_CALLS") is None
    assert _action_side("CLOSE_PUTS") is None
    assert _action_side("NEUTRAL") is None
    assert _action_side("") is None


def test_exit_action_does_not_fire_side_gates(monkeypatch):
    """An EXIT_CALLS action must not be misread as a LONG entry that trips the exposure gate.
    With 3 open LONG positions, a fresh ENTER_CALLS is vetoed, but an EXIT_CALLS passes the
    side-dependent gates (side=None) and is handled by exit routing instead."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    monkeypatch.setattr("src.tracking.position_state.list_open",
                        lambda: {s: _pos(s, "LONG", opened_at=today) for s in ["AAPL", "MSFT", "NVDA"]})
    # Fresh entry -> exposure veto.
    assert call("GOOGL", 90, 9, 45, side="LONG") is not None
    # Exit string -> no side, so the exposure/counter-stage gates are skipped.
    result = evaluate_risk_vetoes(
        symbol="GOOGL", action="EXIT_CALLS", score=90,
        current_time_et="09:45", eastern_dt=et_ts(9, 45), grade="A", align="",
    )
    assert result is None, "EXIT_CALLS must not be treated as a directional entry."


# ---------------------------------------------------------------------------
# Safe env int-parse (regression: a bad .env value must not crash the veto path)
# ---------------------------------------------------------------------------

def test_env_int_falls_back_on_bad_value(monkeypatch):
    from src.tracking.alert_evaluator import _env_int, evaluate_risk_vetoes as ev
    assert _env_int("GRADE_B_VETO_THRESHOLD", 65) == 65  # valid (set by default_env fixture)
    monkeypatch.setenv("GRADE_B_VETO_THRESHOLD", "not-a-number")
    assert _env_int("GRADE_B_VETO_THRESHOLD", 65) == 65, "Bad env value must fall back to default."


def test_bad_grade_b_env_does_not_crash_vetoes(monkeypatch):
    """A malformed GRADE_B_VETO_THRESHOLD in .env must degrade to the default (65), not raise."""
    monkeypatch.setenv("GRADE_B_VETO_THRESHOLD", "garbage")
    # score 70 is above the default-65 threshold -> must pass, not crash.
    result = call("AAPL", 70, 9, 45, grade="B")
    assert result is None
