"""
tests/test_audit_geometry.py
============================
Shared geometry gate, watch_manager guard, and auditor behavior tests
for the auditor geometry fix plan.
"""

from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.logic.level_validation import check_geometry
from src.tracking.suggested_trades_auditor import evaluate_all_suggested_trades, get_audit_summary
from src.tracking.watch_manager import upsert_watch_target, init_watch_db, _get_connection, _db_lock


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test_audit_geom.db"
    monkeypatch.setenv("RESEARCH_WATCH_DB", str(db))
    from src import config
    from src.tracking import watch_manager, suggested_trades_auditor
    watch_manager.DB_PATH = db
    init_watch_db()
    suggested_trades_auditor._AUDIT_CACHE.clear()
    yield


def _target_payload(ticker: str, **overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ticker": ticker,
        "date": "2026-09-22",
        "side": "LONG",
        "entry_type": "LIMIT",
        "entry_zone_low": 245.0,
        "entry_zone_high": 248.0,
        "tactical_stop": 240.0,
        "target_1": 260.87,
        "options_plan": {"actionable": False, "structure": "NONE"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. Shared geometry gate
# ---------------------------------------------------------------------------

def test_check_geometry_rejects_invalid_underlying():
    reasons = check_geometry(
        side="LONG",
        entry_type="LIMIT",
        entry_low=246.0,
        entry_high=250.5,
        breakout_level=0.0,
        stop=248.5,
        t1=260.87,
    )
    assert any("stop" in r.lower() for r in reasons), f"expected stop>=entry_low failure, got {reasons}"


def test_check_geometry_rejects_breakout_target_at_or_below_breakout():
    reasons = check_geometry(
        side="LONG",
        entry_type="BREAKOUT",
        entry_low=0.0,
        entry_high=0.0,
        breakout_level=245.0,
        stop=240.0,
        t1=245.0,
    )
    assert any("entry_high" in r.lower() for r in reasons), f"expected target<=breakout failure, got {reasons}"


def test_check_geometry_accepts_valid():
    reasons = check_geometry(
        side="LONG",
        entry_type="LIMIT",
        entry_low=167.66,
        entry_high=169.84,
        breakout_level=0.0,
        stop=163.41,
        t1=176.10,
    )
    assert reasons == [], f"expected clean geometry, got {reasons}"


# ---------------------------------------------------------------------------
# 2. Watch manager guard
# ---------------------------------------------------------------------------

def test_upsert_watch_target_refuses_amzn_payload():
    payload = _target_payload("AMZN", entry_zone_low=246.0, tactical_stop=248.5)
    upsert_watch_target(payload)
    with _db_lock, _get_connection() as conn:
        row = conn.execute("SELECT * FROM watch_targets WHERE ticker = 'AMZN'").fetchone()
    assert row is None, "AMZN bad geometry should be rejected"


# ---------------------------------------------------------------------------
# 3. Auditor behavior
# ---------------------------------------------------------------------------

def _insert_suggestion(ticker: str, side: str, trade_type: str, struct: str, status: str, spot: float = 0.0, fill: float = 0.0, exit_px: float = 0.0, max_profit: float = 0.0, max_loss: float = 0.0, is_primary: int = 1, r_multiple: float = None):
    trade_label = f"{struct} (test)"
    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO suggested_trades_audit (ticker, date, side, trade_type, trade_structure, trade_label, status, last_price, entry_price, entry_zone_low, entry_zone_high, tactical_stop, target_1, target_2, max_profit, max_loss, is_primary, r_multiple, evaluated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, "2026-09-22", side, trade_type, struct, trade_label, status, spot, fill, 245.0, 250.0, 240.0, 260.0, 270.0, max_profit, max_loss, is_primary, r_multiple, "2026-09-22T00:00:00", "2026-09-22T00:00:00"),
        )
        conn.commit()


def test_options_target_hit_scores_underlying_r():
    _insert_suggestion("GLW", "LONG", "OPTIONS", "BULL_CALL_SPREAD", "STALKING", spot=150.0, fill=146.5, max_profit=500.0, max_loss=200.0, is_primary=1, r_multiple=None)
    _insert_suggestion("GLW", "LONG", "EQUITY", "SHARES", "STALKING", spot=150.0, fill=146.5, is_primary=0, r_multiple=None)
    with _db_lock, _get_connection() as conn:
        conn.execute("UPDATE suggested_trades_audit SET tactical_stop = 143.5, target_1 = 150.0 WHERE ticker = 'GLW' AND trade_type = 'OPTIONS'")
        conn.commit()
    with patch("src.tracking.execution_validator.evaluate_setup_lifecycle", return_value={
        "status": "TARGET_HIT", "was_filled": True, "fill_price": 146.5, "exit_price": 150.0
    }):
        evaluate_all_suggested_trades(refresh_quotes=False, window=10)
    res = get_audit_summary(tab="ALL", force_sync=False)
    glw_rows = [t for t in res["trades"] if t["ticker"] == "GLW" and t["trade_type"] == "OPTIONS"]
    assert glw_rows, f"expected GLW OPTIONS rows, got {res['trades']}"
    assert any(t["r_multiple"] is not None and abs(t["r_multiple"] - 1.17) < 0.01 for t in glw_rows), f"expected underlying R=1.17, got {glw_rows}"


def test_spot_zero_yields_no_quote():
    _insert_suggestion("PANW", "LONG", "EQUITY", "SHARES", "STALKING", spot=0.0, fill=250.0)
    evaluate_all_suggested_trades(refresh_quotes=False, window=10)
    res = get_audit_summary(tab="ALL", force_sync=False)
    panw_rows = [t for t in res["trades"] if t["ticker"] == "PANW" and t["trade_type"] == "EQUITY"]
    assert any(t["status"] == "NO_QUOTE" for t in panw_rows), f"expected NO_QUOTE for PANW, got {panw_rows}"


def test_covered_call_excluded_from_summary():
    _insert_suggestion("GOOGL", "LONG", "INCOME", "COVERED_CALL", "INCOME", spot=175.0, max_profit=50.0, max_loss=0.0, is_primary=1)
    evaluate_all_suggested_trades(refresh_quotes=False, window=10)
    res = get_audit_summary(tab="ALL", force_sync=False)
    assert res["summary"]["invalid_count"] == 0, "COVERED_CALL is INCOME, not invalid"
    assert res["summary"]["won_count"] == 0, "COVERED_CALL should not be R-scored"
    assert res["tab_counts"]["options"] == 0, "COVERED_CALL is INCOME, not OPTIONS"
    assert res["tab_counts"]["income"] == 1, f"expected 1 INCOME, got {res['tab_counts']}"
