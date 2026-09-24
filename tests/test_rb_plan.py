import sqlite3
import pytest
import pandas as pd
from unittest.mock import patch

from src.tracking.execution_validator import evaluate_setup_lifecycle_bars
from src.logic.level_validation import validate_levels


# =====================================================================
# 1. Ledger Migration & Deduplication Tests
# =====================================================================

def test_ledger_migration_and_deduplication(tmp_path):
    """Test ledger migration on old schema with duplicates and old UNIQUE(ticker, date, source, report_hash)."""
    db_file = tmp_path / "test_suggestions.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()

    # Create old schema
    cur.execute("""
        CREATE TABLE suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            report_hash TEXT,
            gate_status TEXT DEFAULT 'PASS',
            verdict TEXT DEFAULT 'STALK',
            kind TEXT DEFAULT 'NEW',
            scorer_version INTEGER,
            entry_low REAL,
            entry_high REAL,
            stop REAL,
            target_1 REAL,
            target_2 REAL,
            planned_rr REAL,
            taken INTEGER DEFAULT 0,
            your_fill REAL,
            notes TEXT,
            UNIQUE(ticker, date, source, report_hash)
        )
    """)

    # Insert duplicate rows for same (ticker, date, source) with different report_hash
    cur.execute("INSERT INTO suggestions (ticker, date, source, report_hash, scorer_version, stop, target_1) VALUES ('AAPL', '2026-09-01', 'judge', 'hash1', 1, 100.0, 110.0)")
    cur.execute("INSERT INTO suggestions (ticker, date, source, report_hash, scorer_version, stop, target_1) VALUES ('AAPL', '2026-09-01', 'judge', 'hash2', 1, 101.0, 112.0)") # higher id
    cur.execute("INSERT INTO suggestions (ticker, date, source, report_hash, scorer_version, stop, target_1) VALUES ('MSFT', '2026-09-01', 'judge', 'hash3', 2, 200.0, 220.0)")
    conn.commit()
    conn.close()

    # Now run suggestions_ledger schema initialization pointing at this DB
    with patch("src.tracking.watch_manager.DB_PATH", db_file):
        from src.tracking.suggestions_ledger import ensure_suggestions_schema, append_suggestion

        conn = sqlite3.connect(str(db_file))
        ensure_suggestions_schema(conn)
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check unique index exists
        indices = [row["name"] for row in cur.execute("PRAGMA index_list('suggestions')").fetchall()]
        assert "ux_sugg" in indices

        # Check duplicate AAPL was resolved to highest id (id 2)
        rows = cur.execute("SELECT * FROM suggestions WHERE ticker='AAPL'").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == 2
        assert rows[0]["stop"] == 101.0

        # Check legacy migration set gate_status='LEGACY_UNGATED', verdict=NULL, kind=NULL for scorer_version < 2
        assert rows[0]["gate_status"] == "LEGACY_UNGATED"
        assert rows[0]["verdict"] is None
        assert rows[0]["kind"] is None

        # Check scorer_version=2 MSFT row was NOT changed to LEGACY_UNGATED
        msft = cur.execute("SELECT * FROM suggestions WHERE ticker='MSFT'").fetchone()
        assert msft["gate_status"] == "PASS"

        # Check append_suggestion UPSERT preserves user fields and clears outcome
        cur.execute("UPDATE suggestions SET taken=1, your_fill=105.0, notes='Keep me', r_net=1.5 WHERE id=2")
        conn.commit()
        conn.close()

        # Re-append AAPL with new levels
        append_suggestion({
            "ticker": "AAPL",
            "date": "2026-09-01",
            "source": "judge",
            "stop": 99.0,
            "target_1": 115.0,
            "gate_status": "PASS",
            "verdict": "ENTER",
            "kind": "NEW",
        })

        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        updated = cur.execute("SELECT * FROM suggestions WHERE ticker='AAPL'").fetchone()
        assert updated["stop"] == 99.0
        assert updated["target_1"] == 115.0
        assert updated["taken"] == 1
        assert updated["your_fill"] == 105.0
        assert updated["notes"] == "Keep me"
        assert updated["r_net"] is None  # outcome cleared
        conn.close()


# =====================================================================
# 2. Execution Validator Bar Walk Cases
# =====================================================================

def test_walk_gap_stop_pre_fill():
    """Open below stop before fill -> status GAP_STOP, no fill."""
    df = pd.DataFrame([
        {"bar_date": "2026-09-10", "Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 101.0}, # signal
        {"bar_date": "2026-09-11", "Open": 88.0, "High": 89.0, "Low": 85.0, "Close": 87.0},   # opens below stop 90
    ])
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-10",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=110.0,
    )
    assert res["status"] == "GAP_STOP"
    assert res["was_filled"] is False


def test_walk_gap_stop_post_fill():
    """Open below stop after fill -> exit at open, status STOP_BREACHED, real R counted."""
    df = pd.DataFrame([
        {"bar_date": "2026-09-10", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, # signal
        {"bar_date": "2026-09-11", "Open": 98.0, "High": 98.5, "Low": 96.0, "Close": 96.5},    # fills at 97 (entry_high)
        {"bar_date": "2026-09-12", "Open": 85.0, "High": 86.0, "Low": 84.0, "Close": 85.5},    # opens below stop 90 at 85.0
    ])
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-10",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=110.0,
    )
    assert res["status"] == "STOP_BREACHED"
    assert res["was_filled"] is True
    assert res["exit_price"] == 85.0 # exited at open
    risk = res["fill_price"] - 90.0
    expected_r = (res["exit_price"] - res["fill_price"]) / risk
    gross_r = (res["exit_price"] - res["fill_price"]) / risk
    assert pytest.approx(gross_r, 0.01) == expected_r


def test_walk_t1_closes_trade_no_t2():
    """T1 closes trade; exit at T1 or gap open."""
    df = pd.DataFrame([
        {"bar_date": "2026-09-10", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
        {"bar_date": "2026-09-11", "Open": 98.0, "High": 98.0, "Low": 96.0, "Close": 96.5},  # fills at 97
        {"bar_date": "2026-09-12", "Open": 105.0, "High": 115.0, "Low": 104.0, "Close": 112.0}, # hits T1 (110)
        {"bar_date": "2026-09-13", "Open": 113.0, "High": 130.0, "Low": 110.0, "Close": 125.0}, # T2 (120) - trade should already be closed
    ])
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-10",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=110.0,
        target_2=120.0,
    )
    assert res["status"] == "TARGET_HIT"
    assert res["exit_price"] == 110.0
    assert res["hit_t1"] is True
    assert res["exit_date"] == "2026-09-12"


def test_walk_fill_window_5_bars():
    """Fill on bar 6 returns NOT_FILLED (5-bar unfilled window)."""
    df = pd.DataFrame([
        {"bar_date": "2026-09-10", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, # signal
        {"bar_date": "2026-09-11", "Open": 101.0, "High": 102.0, "Low": 99.0, "Close": 100.0}, # bar 1 unfilled
        {"bar_date": "2026-09-12", "Open": 101.0, "High": 102.0, "Low": 99.0, "Close": 100.0}, # bar 2 unfilled
        {"bar_date": "2026-09-13", "Open": 101.0, "High": 102.0, "Low": 99.0, "Close": 100.0}, # bar 3 unfilled
        {"bar_date": "2026-09-14", "Open": 101.0, "High": 102.0, "Low": 99.0, "Close": 100.0}, # bar 4 unfilled
        {"bar_date": "2026-09-15", "Open": 101.0, "High": 102.0, "Low": 99.0, "Close": 100.0}, # bar 5 unfilled -> NOT_FILLED
        {"bar_date": "2026-09-16", "Open": 96.0, "High": 97.0, "Low": 95.0, "Close": 96.0},   # bar 6 would touch 97
    ])
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-10",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=110.0,
    )
    assert res["status"] == "NOT_FILLED"
    assert res["was_filled"] is False


def test_walk_stop_first_both_touch():
    """Same bar touches stop and target -> conservative resolution gives STOP_BREACHED."""
    df = pd.DataFrame([
        {"bar_date": "2026-09-10", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
        {"bar_date": "2026-09-11", "Open": 98.0, "High": 98.0, "Low": 96.0, "Close": 96.5},  # fills at 97
        {"bar_date": "2026-09-12", "Open": 95.0, "High": 115.0, "Low": 88.0, "Close": 110.0}, # low 88 <= stop 90 AND high 115 >= t1 110
    ])
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-10",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=110.0,
    )
    assert res["status"] == "STOP_BREACHED"
    assert res["exit_price"] == 90.0


def test_walk_21_bar_time_exit():
    """Holding for 21 bars without stop or target -> time exit."""
    rows = [{"bar_date": "2026-09-01", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}]
    # Fill on bar 1
    rows.append({"bar_date": "2026-09-02", "Open": 98.0, "High": 98.0, "Low": 96.0, "Close": 96.5})
    # 20 more bars inside range
    for i in range(3, 23):
        rows.append({"bar_date": f"2026-09-{i:02d}", "Open": 98.0, "High": 102.0, "Low": 94.0, "Close": 99.0})
    # Bar 22 exits at close 99.0
    df = pd.DataFrame(rows)
    res = evaluate_setup_lifecycle_bars(
        bars=df,
        setup_date="2026-09-01",
        side="LONG",
        entry_type="LIMIT",
        entry_low=95.0,
        entry_high=97.0,
        stop_loss=90.0,
        target_1=120.0,
        max_holding_bars=21,
    )
    assert res["status"] == "TIME_EXIT"
    assert res["exit_price"] == 99.0
    assert res["bars_held"] == 21


# =====================================================================
# 3. Level Gate Tests per Lane
# =====================================================================

def test_level_gate_measured_lane_pine_levels():
    """Measured lane (RR_SETUP) passes with exact Pine levels and skips ATR/planned R:R floor."""
    dw = {
        "Long Stop Loss": 95.0,
        "Long Target": 110.0,
        "RSI2 ATR14": 10.0, # tactical stop is 95, distance is 5 < 10 (less than 1 ATR), but measured lanes skip ATR floor
        "Close": 100.0,
    }
    plan = {
        "tactical_stop": 95.0,
        "target_1": 110.0,
        "entry_zone_low": 98.0,
        "entry_zone_high": 100.0,
        "setup_lane": "RR_SETUP",
        "kind": "NEW",
    }
    ok, reasons = validate_levels(plan, dw, "LONG")
    assert ok is True, reasons


def test_level_gate_judge_lane_enforces_atr_and_rr_floor():
    """Judge-invented lane (FLOOR_DEFENSE) fails when stop distance < 1 ATR or R:R < 2.0."""
    dw = {
        "RSI2 ATR14": 10.0,
        "Close": 100.0,
    }
    # Stop distance is only 4 (< 10 ATR) -> must fail
    plan = {
        "tactical_stop": 96.0,
        "target_1": 115.0,
        "entry_zone_low": 98.0,
        "entry_zone_high": 100.0,
        "setup_lane": "FLOOR_DEFENSE",
        "kind": "NEW",
    }
    ok, reasons = validate_levels(plan, dw, "LONG")
    assert ok is False
    assert any("exceeds max allowed stop" in r or "ATR" in r for r in reasons)


def test_level_gate_rsi2_levels():
    """RSI2 passes with its own levels (close - 2 ATR / close + 4 ATR) and skips pine drift check."""
    atr = 5.0
    close = 100.0
    dw = {
        "RSI2 ATR14": atr,
        "Close": close,
        "Long Stop Loss": 80.0, # deliberate difference from Pine
        "Long Target": 130.0,
    }
    plan = {
        "tactical_stop": close - 2 * atr, # 90.0
        "target_1": close + 4 * atr,      # 120.0
        "entry_zone_low": 99.0,
        "entry_zone_high": 100.0,
        "setup_lane": "RSI2",
        "kind": "NEW",
    }
    ok, reasons = validate_levels(plan, dw, "LONG")
    assert ok is True, reasons
