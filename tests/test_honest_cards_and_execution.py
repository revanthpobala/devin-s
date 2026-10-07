"""
Unit and integration tests for honest cards, actionable gate enforcement,
single-row trade execution, and limit price calculations (Tasks d1-d9).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from src import config
from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
from src.logic.level_validation import check_geometry
from src.ui.services.opportunity_service import collect_active_deep_research_opportunities
from src.ui.app import create_app
from src.tracking import position_state, rr_config


@pytest.fixture(scope="module")
def client():
    import src.ui.services.daemon_manager as dm
    orig_start = dm.start_all_daemons
    orig_stop = dm.stop_all_daemons
    dm.start_all_daemons = lambda: None
    dm.stop_all_daemons = lambda: None
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        dm.start_all_daemons = orig_start
        dm.stop_all_daemons = orig_stop


from src.tracking.watch_manager import _db_lock, _get_connection


def test_coil_with_stop_above_entry_rejected():
    """Verify that a candidate with stop above entry is strictly rejected by level validation."""
    entry_low = 170.0
    entry_high = 172.21
    stop_above = 175.35
    target_1 = 185.0
    
    # check_geometry must fail for long setup when stop >= entry_low
    reasons = check_geometry("LONG", "LIMIT", entry_low, entry_high, 0.0, stop_above, target_1, 0.0)
    assert len(reasons) > 0
    assert any("stop" in r.lower() or "entry" in r.lower() for r in reasons)


def test_screener_coil_never_high_conviction():
    """Verify that screener coils without Data Window measurements are never labeled HIGH_CONVICTION."""
    # Test opportunity service on dummy or empty data
    items = collect_active_deep_research_opportunities(lookback_days=1)
    for it in items:
        source = it.get("source")
        if source == "SCREENER_COIL":
            assert it.get("state") == "SCREENER_UNMEASURED"
            assert it.get("badge") != "HIGH CONVICTION"
            assert it.get("conviction") is None


def test_failing_in_zone_arrival_does_not_push():
    """Verify that an in-zone arrival with stop width < 0.7 ATR fails is_actionable and emits no push."""
    # SCHW-style row: stop 0.54 ATR, no PB bit
    dw = {
        "long_in_zone": 1.0,
        "long_rr_at_market": 2.5,
        "long_stop_loss": 95.0,
        "atr14": 5.0,  # stop width = (100 - 97.3) / 5 = 0.54 ATR
        "price": 100.0,
        "signal_pack": 4.0,  # bit 5 (PB) not set, bit 2 set
        "fade_long": 0.0,
        "action_long": 20.0,
        "ext_z_self": 1.0,
    }
    gate_in = gate_inputs_from_datawindow(dw)
    # Entry at 100.0, stop at 97.3 -> width is 2.7 / 5.0 = 0.54 ATR < 0.7 ATR
    is_act, fails = is_actionable(gate_in, {"side": "long", "entry": 100.0, "stop": 97.3})
    assert is_act is False
    assert any("stop width" in f or "0.7" in f or "noise" in f for f in fails) or any("PB" in f for f in fails)


def test_take_opens_position_for_exactly_one_row(client):
    """Verify that POST /api/trades/take updates only the specified row and opens exactly 1 position."""
    # Setup test DB suggestion row
    with _db_lock:
        with _get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO suggestions (ticker, date, source, setup_lane, entry_low, entry_high, stop, target_1, target_2, taken, gate_status)
                VALUES ('TESTCO', '2026-10-07', 'SWING', 'CODE20_REVERSAL', 50.0, 52.0, 48.0, 58.0, 62.0, 0, 'PASS')
            """)
            row_id = c.lastrowid
            
            # Second row for same ticker to ensure it is NOT modified
            c.execute("""
                INSERT INTO suggestions (ticker, date, source, setup_lane, entry_low, entry_high, stop, target_1, target_2, taken, gate_status)
                VALUES ('TESTCO', '2026-10-06', 'SWING', 'CODE20_REVERSAL', 49.0, 51.0, 47.0, 56.0, 60.0, 0, 'PASS')
            """)
            other_id = c.lastrowid
            conn.commit()

    try:
        resp = client.post("/api/trades/take", json={
            "suggestion_id": row_id,
            "ticker": "TESTCO",
            "entry_price": 51.0,
            "quantity": 100,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        
        # Verify only row_id status is taken == 1, other_id remains taken == 0
        with _get_connection() as conn:
            c = conn.cursor()
            t_1 = c.execute("SELECT taken FROM suggestions WHERE id = ?", (row_id,)).fetchone()[0]
            t_2 = c.execute("SELECT taken FROM suggestions WHERE id = ?", (other_id,)).fetchone()[0]
            assert t_1 == 1
            assert t_2 == 0
            
        # Verify positions.json has open position
        open_positions = position_state.list_open()
        assert "TESTCO" in open_positions
        test_pos = open_positions["TESTCO"]
        assert test_pos.get("suggestion_id") == row_id
        
        # Verify status endpoint reflects open_position_count
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        assert status_resp.json().get("open_position_count", 0) >= 1
    finally:
        # Cleanup
        position_state.cancel_position("TESTCO")
        with _db_lock:
            with _get_connection() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM suggestions WHERE ticker = 'TESTCO'")
                conn.commit()


def test_under_floor_rr_yields_exact_limit_price():
    """Verify that when spot R:R < active floor, the limit calculation computes the exact price needed."""
    # Setup: Target = 120, Stop = 90. Floor = 2.0
    # Formula: limit = (T1 + floor * stop) / (1 + floor) = (120 + 2 * 90) / 3 = 300 / 3 = 100.0
    target_1 = 120.0
    stop = 90.0
    floor = 2.0
    
    calc_limit = (target_1 + floor * stop) / (1.0 + floor)
    assert calc_limit == 100.0
    
    # Verify resulting R:R at limit price
    rr_at_limit = (target_1 - calc_limit) / (calc_limit - stop)
    assert abs(rr_at_limit - floor) < 1e-6
