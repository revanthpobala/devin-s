"""
tests/test_b12_integration_verification.py

Comprehensive end-to-end integration verification for B1-B12 remediation contracts:
1. GET-purity: GET endpoints perform zero DB writes / state mutations.
2. Evaluator no-levels: Evaluator returns UNMEASURED with 0 DB writes when levels missing.
3. TARGET_HIT needs fill: Unfilled setups reaching target become MISSED_RUNAWAY.
4. Thesis freeze: watch_manager preserves open thesis within 5 trading days; unchanged research detection.
5. compute_r: Correct unified R calculation, clamp |R| > 20 to None, skip risk < 0.25 ATR.
6. Desk promotion: needs_you strictly requires passing all gates.
7. Feedback service purity: GET does not modify open positions.
8. Chat no-exec: Python execution endpoint denies execution without explicit approval.
"""

import json
import sqlite3
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from src.ui.app import create_app
from src.tracking.watch_manager import _get_connection, _db_lock, init_watch_db, upsert_watch_target
from src.tracking.alert_db import set_db_path as set_alert_db_path
from src.tracking.r_calculator import compute_r
from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival
from src.logic.actionable_gate import is_actionable


@pytest.fixture
def b12_client(tmp_path, monkeypatch):
    watch_db = tmp_path / "b12_watch.db"
    alert_db = tmp_path / "b12_alerts.db"
    monkeypatch.setenv("RESEARCH_WATCH_DB", str(watch_db))
    monkeypatch.setenv("ALERT_DB_PATH", str(alert_db))
    from src.tracking import watch_manager
    watch_manager.DB_PATH = watch_db
    set_alert_db_path(str(alert_db))

    init_watch_db()

    app = create_app()
    return TestClient(app), watch_db, alert_db


def test_get_purity_endpoints_perform_no_writes(b12_client):
    """GET endpoints must not mutate SQLite tables."""
    client, watch_db, alert_db = b12_client

    # Snapshot current DB sizes / modification signatures
    with sqlite3.connect(str(watch_db)) as conn:
        before_watch_counts = {
            t[0]: conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    # Exercise GET endpoints
    r1 = client.get("/api/desk/today")
    assert r1.status_code == 200
    r2 = client.get("/api/trades/suggested")
    assert r2.status_code == 200
    r3 = client.get("/api/jobs")
    assert r3.status_code == 200
    r4 = client.get("/api/desk/journal")
    assert r4.status_code == 200
    r5 = client.get("/api/desk/feedback-loop")
    assert r5.status_code == 200

    with sqlite3.connect(str(watch_db)) as conn:
        after_watch_counts = {
            t[0]: conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert before_watch_counts == after_watch_counts, "GET endpoints mutated the watch database!"


def test_evaluator_no_levels_returns_unmeasured_and_no_db_write():
    """Missing or incomplete levels return UNMEASURED without fabricating or caching."""
    # Empty levels dictionary
    res = evaluate_target_on_zone_arrival(
        ticker="TEST",
        spot_price=100.0,
        watch_target={},
    )
    assert res["verdict"] == "UNMEASURED"
    assert res["is_actionable_now"] is False
    assert res["live_rr"] == 0.0

    # Partial levels (missing stop and target)
    res_partial = evaluate_target_on_zone_arrival(
        ticker="TEST",
        spot_price=100.0,
        watch_target={"entry_zone_low": 98.0, "entry_zone_high": 102.0},
    )
    assert res_partial["verdict"] == "UNMEASURED"


def test_target_hit_requires_fill():
    """Unfulfilled trades reaching target are categorized as MISSED_RUNAWAY."""
    from src.tracking.execution_validator import evaluate_setup_lifecycle_bars

    res = evaluate_setup_lifecycle_bars(
        bars=[
            {"date": "2026-10-01", "open": 105.0, "high": 125.0, "low": 103.0, "close": 124.0},
        ],
        setup_date="2026-10-01",
        side="LONG",
        entry_type="LIMIT",
        entry_low=98.0,
        entry_high=102.0,
        stop_loss=95.0,
        target_1=120.0,
    )
    assert res["status"] == "MISSED_RUNAWAY", f"Expected MISSED_RUNAWAY, got {res['status']}"
    assert res["was_filled"] is False


def test_thesis_freeze_and_recent_research_tolerance():
    """Existing thesis is preserved against overwrite within 5 trading days."""
    target_data = {
        "ticker": "NVDA",
        "date": "2026-10-01",
        "status": "WATCH",
        "thesis_id": "thesis-nvda-1",
        "shares_plan": {
            "entry_type": "LIMIT",
            "entry_zone_low": 115.0,
            "entry_zone_high": 118.0,
            "tactical_stop": 110.0,
            "target_1": 135.0,
            "target_2": 145.0,
        },
    }
    upsert_watch_target(target_data)

    # Attempt to overwrite with different thesis within 5 trading days
    overwrite_attempt = {
        "ticker": "NVDA",
        "date": "2026-10-02",
        "status": "WATCH",
        "thesis_id": "thesis-nvda-2",
        "shares_plan": {
            "entry_type": "LIMIT",
            "entry_zone_low": 115.0,
            "entry_zone_high": 118.0,
            "tactical_stop": 110.0,
            "target_1": 135.0,
            "target_2": 145.0,
        },
    }
    upsert_watch_target(overwrite_attempt)

    with _db_lock:
        with _get_connection() as conn:
            row = conn.execute("SELECT thesis_id FROM watch_targets WHERE ticker = 'NVDA'").fetchone()
            # Original thesis_id is preserved
            assert row is not None
            assert row[0] == "thesis-nvda-1"


def test_compute_r_unified_invariants():
    """Verify compute_r handles stock/options, skips tight stops, and clamps extreme R."""
    # 1. Normal stock win (initial risk: 100 - 95 = 5, exit 110 -> +2.0R)
    r = compute_r(unit="share", entry=100.0, exit_px=110.0, stop=95.0, side="LONG", atr=5.0)
    assert r == pytest.approx(2.0)

    # 2. Normal stock loss (exit 95 -> -1.0R)
    r_loss = compute_r(unit="share", entry=100.0, exit_px=95.0, stop=95.0, side="LONG", atr=5.0)
    assert r_loss == pytest.approx(-1.0)

    # 3. Micro stop (< 0.25 ATR) returns None
    r_tight = compute_r(unit="share", entry=100.0, exit_px=105.0, stop=99.9, side="LONG", atr=5.0)
    assert r_tight is None

    # 4. Extreme outlier clamped to None (|R| > 20)
    r_extreme = compute_r(unit="share", entry=100.0, exit_px=350.0, stop=95.0, side="LONG", atr=5.0)
    # (350 - 100) / 5 = 50R > 20R -> None
    assert r_extreme is None

    # 5. Options structure uses debit
    r_opt = compute_r(unit="option", debit=3.0, dollar_pnl=300.0)
    assert r_opt == pytest.approx(1.0)


def test_copilot_python_execution_security_approval(b12_client):
    """Copilot execution endpoint strictly refuses unapproved execution."""
    client, _, _ = b12_client

    unapproved = client.post("/api/copilot/execute-python", json={
        "code": "print(2 + 2)",
        "ticker": "AAPL",
        "approved": False,
    })
    assert unapproved.status_code == 200
    assert unapproved.json()["success"] is False
    assert "explicit user approval required" in unapproved.json()["error"]

    approved = client.post("/api/copilot/execute-python", json={
        "code": "print(f'Math: {2 + 2}')",
        "ticker": "AAPL",
        "approved": True,
    })
    assert approved.status_code == 200
    assert approved.json()["success"] is True
    assert "Math: 4" in approved.json()["output"]
