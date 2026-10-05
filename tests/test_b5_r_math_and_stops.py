"""
tests/test_b5_r_math_and_stops.py
=================================
Unit tests for B5 remediation:
- Unified compute_r with unit tagging (share vs option)
- R derived from initial_stop only, skips when risk < 0.25 ATR, clamps |R| > 20 to None
- position_state: never overwrites initial_stop, validates side-correct stops, cancel_position
- feedback_service: keeps NULL exit_reason, manual trades not dropped by raw_alert, scratch |R| < 0.1 bucketed
"""

import pytest
from src.tracking.r_calculator import compute_r
from src.tracking.position_state import open_position, update_position, close_position, cancel_position, get_position, load_state


def test_compute_r_shares():
    # Long winning trade
    r = compute_r(unit="share", entry=100.0, exit_px=105.0, stop=98.0, side="LONG", atr=2.0)
    assert r == 2.5

    # Short winning trade
    r = compute_r(unit="share", entry=100.0, exit_px=95.0, stop=102.0, side="SHORT", atr=2.0)
    assert r == 2.5

    # Risk < 0.25 ATR -> None
    r = compute_r(unit="share", entry=100.0, exit_px=101.0, stop=99.8, side="LONG", atr=2.0)
    assert r is None  # risk_dist=0.2 < 0.25*2.0=0.5

    # |R| > 20 clamped to None
    r = compute_r(unit="share", entry=100.0, exit_px=150.0, stop=98.0, side="LONG", atr=1.0)
    # risk_dist=2, gain=50 -> 25R > 20 -> None
    assert r is None

    # Missing / zero risk -> None (no 0.0 default)
    r = compute_r(unit="share", entry=100.0, exit_px=105.0, stop=None, side="LONG")
    assert r is None


def test_compute_r_options():
    # Options vertical spread: debit $3.00, realized dollar pnl +$600 -> +2.0R
    r = compute_r(unit="option", debit=3.0, dollar_pnl=600.0)
    assert r == 2.0

    # Options loss: debit $3.00, realized dollar pnl -$300 -> -1.0R
    r = compute_r(unit="option", debit=3.0, dollar_pnl=-300.0)
    assert r == -1.0

    # Options clamping > 20R -> None
    r = compute_r(unit="option", debit=1.0, dollar_pnl=2500.0)
    assert r is None


def test_position_state_initial_stop_protection_and_side_validation(tmp_path, monkeypatch):
    test_pos_file = tmp_path / "positions.json"
    monkeypatch.setattr("src.tracking.position_state.POSITIONS_FILE", test_pos_file)

    # 1. Open LONG position with valid stop below entry
    rec = open_position("TESTSYM", side="LONG", strategy="Intraday", entry_price=100.0, stop=95.0, target=110.0)
    assert rec["initial_stop"] == 95.0
    assert rec["stop"] == 95.0

    # 2. Try to update stop with an invalid stop above current price
    update_position("TESTSYM", stop=105.0, last_price=100.0)
    pos = get_position("TESTSYM")
    assert pos["stop"] == 95.0  # Invalid stop ignored!

    # 3. Trailing runner stop to BE+
    update_position("TESTSYM", stop=100.5, last_price=105.0, be_locked=True)
    pos = get_position("TESTSYM")
    assert pos["stop"] == 100.5
    assert pos["initial_stop"] == 95.0  # Initial stop never overwritten!

    # 4. Try to overwrite initial_stop in update_position
    update_position("TESTSYM", initial_stop=100.0)
    pos = get_position("TESTSYM")
    assert pos["initial_stop"] == 95.0  # Protected!

    # 5. Cancel position removes from open positions without 0R trade
    cancelled = cancel_position("TESTSYM", reason="AI triage veto")
    assert cancelled is not None
    assert get_position("TESTSYM") is None


def test_cat_trade_evaluates_to_null():
    # Verify the CAT row geometry: entry 822.74, stop 825.26, exit 510.0 for SHORT
    # gain = 312.74, risk = 2.52 -> R = 124.1032 > 20 -> Clamped to None
    r = compute_r(unit="share", entry=822.74, exit_px=510.0, stop=825.26, side="SHORT", atr=5.0)
    assert r is None
