"""
Regression tests for options-vertical geometry (Bug 3).

Trade 26670 in the live ledger: AMD BULL CALL SPREAD (480/510), entry 607.50, debit 12.70.
Both strikes sit >15% under spot and the short leg carried a 105 intrinsic, so the "30-wide"
spread was really at least 105 wide. It scored -1.99R / -$1270 because the ledger marked P&L off
the UNDERLYING's move and clamped, while the spread was in fact pinned at full width.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.logic.strike_validator import (
    MAX_STRIKE_SPOT_DRIFT_PCT,
    spread_intrinsic_value,
    validate_spread_geometry,
    vertical_mark_value,
)
from src.tracking.suggested_trades_auditor import _mark_vertical_pnl

# Verbatim from suggested_trades_audit id=26670
AMD = {
    "id": 26670,
    "ticker": "AMD",
    "date": "2026-09-25",
    "side": "LONG",
    "trade_type": "OPTIONS",
    "trade_structure": "BULL_CALL_SPREAD",
    "entry_price": 607.50,
    "entry_zone_low": 600.00,
    "entry_zone_high": 615.00,
    "tactical_stop": 579.00,
    "target_1": 649.81,
    "long_strike": 480.0,
    "short_strike": 510.0,
    "target_debit": 12.70,
    "max_profit": 1730.0,
    "max_loss": 1270.0,
    "last_price": 543.42,
}


# --- the exact row the spec names -----------------------------------------------------------

def test_amd_row_is_rejected():
    ok, defects = validate_spread_geometry(
        AMD["trade_structure"], AMD["entry_price"],
        AMD["long_strike"], AMD["short_strike"], AMD["target_debit"],
    )
    assert ok is False
    joined = " ".join(defects)
    assert "480.00 is -21.0% from spot" in joined, joined      # stale-price leg
    assert "510.00 is -16.0% from spot" in joined, joined
    # Spread intrinsic = (607.50-480) - (607.50-510) = the full 30.00 width, against a $12.70 debit.
    assert "below the $30.00 intrinsic of 480/510" in joined, joined


def test_amd_row_pnl_was_the_wrong_sign():
    """At 543.42 spot is above the 510 short strike, so the spread is at full width: +$1730."""
    spot = AMD["last_price"]
    assert spread_intrinsic_value(spot, AMD["long_strike"], AMD["short_strike"], is_call=True) == 30.0
    mark = vertical_mark_value(spot, AMD["long_strike"], AMD["short_strike"], AMD["target_debit"], True)
    assert mark == 3000.0                                     # full 30 width, per contract

    pnl = _mark_vertical_pnl(
        struct="BULL_CALL_SPREAD",
        mark_spot=spot,
        long_k=AMD["long_strike"],
        short_k=AMD["short_strike"],
        debit=AMD["target_debit"],
        exit_px=spot,
        fill_val=AMD["entry_zone_low"],
        side="LONG",
        max_loss=AMD["max_loss"],
        max_profit=AMD["max_profit"],
    )
    assert pnl == pytest.approx(1730.0)                       # max profit, not -$1270
    assert pnl == AMD["max_profit"]


# --- each independent gate fires on its own ------------------------------------------------

def _ok_args(**kw):
    # A coherent vertical: spot 100 sits inside 98/105, so the 2.00 intrinsic is under the debit
    # and the debit is well under the 7.00 width.
    base = dict(structure="BULL_CALL_SPREAD", spot_price=100.0,
                long_strike=98.0, short_strike=105.0, debit=3.0)
    base.update(kw)
    return validate_spread_geometry(**base)


def test_a_leg_far_from_spot_is_rejected():
    ok, defects = _ok_args(long_strike=70.0, short_strike=105.0, debit=3.0)
    assert ok is False
    assert any("different quotes" in d for d in defects)
    # Boundary: a leg exactly at the drift limit is allowed. Debit must clear the 15.0 intrinsic.
    edge = 100.0 * (1 - MAX_STRIKE_SPOT_DRIFT_PCT / 100.0)
    ok2, d2 = _ok_args(long_strike=edge, short_strike=105.0, debit=18.0)
    assert ok2 is True, d2
    just_past = 100.0 * (1 - MAX_STRIKE_SPOT_DRIFT_PCT / 100.0) - 0.01
    ok3, d3 = _ok_args(long_strike=just_past, short_strike=105.0, debit=18.0)
    assert ok3 is False
    assert any("different quotes" in d for d in d3)


def test_debit_below_intrinsic_is_rejected():
    # OTM spread: both legs worthless, so any debit works.
    assert _ok_args(debit=2.0)[0] is True
    # Spot above the short strike pins the spread at full width -> a debit below that is impossible.
    ok, defects = _ok_args(spot_price=110.0, debit=2.0)
    assert ok is False
    assert any("intrinsic" in d for d in defects)
    # Spot inside the range but the long leg already has intrinsic.
    ok2, d2 = _ok_args(spot_price=103.0, debit=2.0)
    assert ok2 is False
    assert any("intrinsic" in d for d in d2)


def test_debit_at_or_above_width_is_rejected():
    ok, defects = _ok_args(debit=10.0)
    assert ok is False
    assert any("max_profit is zero or negative" in d for d in defects)


def test_missing_debit_is_rejected():
    ok, defects = _ok_args(debit=0.0)
    assert ok is False
    assert any("R cannot be expressed" in d for d in defects)


def test_a_well_formed_vertical_passes():
    assert _ok_args()[0] is True
    # Bear put 105/98 at spot 100: both legs ITM, intrinsic 5.00, so the debit must sit in (5, 7).
    assert _ok_args(structure="BEAR_PUT_SPREAD", long_strike=105.0, short_strike=98.0,
                    debit=6.0)[0] is True


def test_zero_width_vertical_is_rejected():
    """Identical strikes cannot satisfy the strict ordering a debit vertical requires."""
    ok, defects = _ok_args(long_strike=95.0, short_strike=95.0)
    assert ok is False
    assert defects


def test_missing_strikes_are_rejected():
    assert validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 0, 105.0, 3.0)[0] is False
    assert validate_spread_geometry("BULL_CALL_SPREAD", 0, 95.0, 105.0, 3.0)[0] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_spot_is_rejected(bad):
    """NaN compares False against everything, so every check below silently passes it.

    Before this guard, `validate_spread_geometry("BULL_CALL_SPREAD", nan, 95, 105, 3)`
    returned (True, []) -- grading a corrupt quote as a well-formed structure.
    """
    ok, defects = validate_spread_geometry("BULL_CALL_SPREAD", bad, 95.0, 105.0, 3.0)
    assert ok is False, f"spot={bad} was accepted as a valid vertical"
    assert any("Non-finite" in d or "invalid spot" in d for d in defects), defects


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_strike_or_debit_is_rejected(bad):
    assert validate_spread_geometry("BULL_CALL_SPREAD", 100.0, bad, 105.0, 3.0)[0] is False
    assert validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 95.0, bad, 3.0)[0] is False
    assert validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 95.0, 105.0, bad)[0] is False


def test_finite_inputs_still_validate_normally():
    """Guard must not reject a legitimate structure."""
    assert validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 98.0, 105.0, 3.0)[0] is True


# --- direction: the two structures that were being priced with each other's math -------------

def test_put_spreads_are_priced_as_puts():
    """A debit put vertical is long the HIGHER strike. Pricing it with call math books max loss as
    a full-width gain."""
    # bear put 110/100, spot 120: long the 110 put is worthless -> max loss
    assert vertical_mark_value(120.0, 110.0, 100.0, 3.0, is_call=False) == 0.0
    # spot 90, below the short 100: both legs deep ITM -> full width
    assert vertical_mark_value(90.0, 110.0, 100.0, 3.0, is_call=False) == 1000.0


def test_call_spreads_are_priced_as_calls():
    assert vertical_mark_value(93.0, 95.0, 105.0, 3.0, is_call=True) == 0.0     # max loss
    assert vertical_mark_value(110.0, 95.0, 105.0, 3.0, is_call=True) == 1000.0  # max profit


def test_max_profit_direction_flips_with_the_structure():
    """Identical strikes and spot, opposite structures: the marks must differ."""
    assert vertical_mark_value(120.0, 110.0, 100.0, 3.0, is_call=True) != \
        vertical_mark_value(120.0, 110.0, 100.0, 3.0, is_call=False)


def test_inverted_strike_ordering_is_rejected_for_both_structures():
    """As given, these are credit spreads. The gate prices debits, so it must refuse them."""
    ok_c, d_c = validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 105.0, 95.0, 3.0)
    assert ok_c is False and any("ordering is inverted" in d for d in d_c)
    ok_p, d_p = validate_spread_geometry("BEAR_PUT_SPREAD", 100.0, 95.0, 105.0, 3.0)
    assert ok_p is False and any("ordering is inverted" in d for d in d_p)


def test_a_valid_put_vertical_is_accepted():
    # spot 92, long 95, short 90: width 5, intrinsic 3.00, so the debit must sit in (3, 5).
    assert validate_spread_geometry("BEAR_PUT_SPREAD", 92.0, 95.0, 90.0, 4.0)[0] is True


def test_multi_leg_structures_are_refused_rather_than_guessed():
    """A condor priced with single-spread math is nonsense, so say so rather than return a number."""
    for struct in ("IRON_CONDOR", "BUTTERFLY", "CALENDAR", "STRADDLE"):
        ok, defects = validate_spread_geometry(struct, 100.0, 95.0, 105.0, 1.0)
        assert ok is False, struct
        assert any("not a 2-leg debit vertical" in d for d in defects), struct


def test_unrecognised_structure_is_refused():
    ok, defects = validate_spread_geometry("SOMETHING_ELSE", 100.0, 95.0, 105.0, 3.0)
    assert ok is False
    assert any("Unrecognised structure" in d for d in defects)


def test_inverted_pair_intrinsic_is_not_silently_zero():
    """A naive long-minus-short returns 0 for an inverted pair, making every check inert.

    The long leg's own intrinsic is the meaningful number there.
    """
    assert spread_intrinsic_value(120.0, 105.0, 95.0, is_call=True) == 15.0
    assert spread_intrinsic_value(80.0, 95.0, 105.0, is_call=False) == 15.0


# --- marking -------------------------------------------------------------------------------

def test_spread_inside_its_range_marks_to_intrinsic_not_to_the_debit():
    """Spot 100 on a 95/105 call: the long leg is already 5.00 ITM, so the spread cannot be
    worth the 3.00 debit. Marking at the debit booked this as exactly break-even."""
    mark = vertical_mark_value(100.0, 95.0, 105.0, 3.0, is_call=True)
    assert mark == 500.0
    assert spread_intrinsic_value(100.0, 95.0, 105.0, is_call=True) == 5.0

    pnl = _mark_vertical_pnl("BULL_CALL_SPREAD", 100.0, 95.0, 105.0, 3.0, 100.0, 100.0,
                             "LONG", 300.0, 700.0)
    assert pnl == pytest.approx(200.0), "mark 500 less the 300 debit"


def test_stopped_out_otm_spread_books_the_loss_not_break_even():
    """The bug this replaced: an underlying stop lands between the strikes, so every stopped-out
    spread used to mark at the debit and report exactly $0.00."""
    pnl = _mark_vertical_pnl("BULL_CALL_SPREAD", 93.0, 95.0, 105.0, 3.0, 93.0, 100.0,
                             "LONG", 300.0, 700.0)
    assert pnl == pytest.approx(-300.0), "both legs worthless -> the full debit is gone"


def test_otm_spread_below_stop_marks_at_zero_and_never_below_max_loss():
    """Spot below the long strike: both legs are worthless, so the whole debit is gone."""
    pnl = _mark_vertical_pnl("BULL_CALL_SPREAD", 80.0, 95.0, 105.0, 3.0, 80.0, 100.0,
                             "LONG", 300.0, 700.0)
    assert pnl == pytest.approx(-300.0), "max loss = the debit"


def test_spread_past_the_short_strike_pins_at_full_width():
    """Both legs ITM: the position is worth its width, so the mark cannot be below max profit."""
    pnl = _mark_vertical_pnl("BULL_CALL_SPREAD", 130.0, 95.0, 105.0, 3.0, 130.0, 100.0,
                             "LONG", 300.0, 700.0)
    assert pnl == pytest.approx(700.0)


def test_mark_is_bounded_and_monotone_in_spot():
    """No time value is invented: the mark stays within [0, width] and never falls as spot rises."""
    marks = [
        vertical_mark_value(spot, 98.0, 105.0, 3.0, is_call=True)
        for spot in (80.0, 90.0, 98.0, 100.0, 105.0, 110.0, 130.0)
    ]
    assert all(0.0 <= m <= 700.0 for m in marks), marks
    assert marks == sorted(marks), f"mark fell as spot rose: {marks}"
    assert marks[0] == 0.0, "both legs worthless below the long strike"
    assert marks[-1] == 700.0, "pinned at full width above the short strike"


def test_pinned_mark_is_never_below_the_spreads_intrinsic():
    for spot in (105.0, 110.0, 130.0):
        mark = vertical_mark_value(spot, 98.0, 105.0, 3.0, is_call=True)
        floor = spread_intrinsic_value(spot, 98.0, 105.0, is_call=True) * 100
        assert mark >= floor


def test_non_vertical_rows_are_not_silently_zeroed():
    assert _mark_vertical_pnl("LONG_CALL", 100.0, 95.0, 0.0, 3.0, 100.0, 100.0,
                              "LONG", 300.0, 700.0) == 0.0, "no spread -> caller uses the old clamp"


# --- ledger quarantine ----------------------------------------------------------------------

@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    import src.tracking.watch_manager as wm

    db = tmp_path / "audit.db"
    monkeypatch.setattr(wm, "DB_PATH", db)
    wm.init_watch_db()
    yield db


def _seed(db, **over):
    row = {
        "ticker": "AMD", "date": "2026-09-25", "side": "LONG", "trade_type": "OPTIONS",
        "trade_structure": "BULL_CALL_SPREAD", "trade_label": "BULL CALL SPREAD (480/510)",
        "entry_type": "OPTIONS_ENTRY", "entry_price": 607.5,
        "entry_zone_low": 600.0, "entry_zone_high": 615.0, "tactical_stop": 579.0,
        "target_1": 649.81, "long_strike": 480.0, "short_strike": 510.0,
        "target_debit": 12.7, "max_profit": 1730.0, "max_loss": 1270.0,
        "last_price": 543.42, "distance_to_entry_pct": 12.74,
        "status": "IN_TRADE", "is_primary": 1, "created_at": "2026-09-30T09:59:09-06:00",
    }
    row.update(over)
    conn = sqlite3.connect(str(db))
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    cur = conn.execute(
        f"INSERT INTO suggested_trades_audit ({cols}) VALUES ({ph})", list(row.values())
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def _read(db, rid):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM suggested_trades_audit WHERE id = ?", (rid,)).fetchone())
    conn.close()
    return row


def test_quarantine_clears_the_amd_row(audit_db):
    from src.tracking.suggested_trades_auditor import revalidate_impossible_verticals

    rid = _seed(audit_db)
    assert revalidate_impossible_verticals() == 1
    row = _read(audit_db, rid)
    assert row["status"] == "INVALID_GEOMETRY"
    assert row["r_multiple"] is None
    assert row["dollar_pnl"] == 0.0


def test_quarantine_leaves_a_valid_vertical_alone(audit_db):
    from src.tracking.suggested_trades_auditor import revalidate_impossible_verticals

    rid = _seed(
        audit_db, ticker="XYZ", trade_structure="BULL_CALL_SPREAD",
        trade_label="BULL CALL SPREAD (98/105)", entry_price=100.0,
        entry_zone_low=99.0, entry_zone_high=101.0, tactical_stop=95.0, target_1=110.0,
        long_strike=98.0, short_strike=105.0, target_debit=3.0,
        max_profit=700.0, max_loss=300.0, last_price=100.0, distance_to_entry_pct=0.0,
    )
    assert revalidate_impossible_verticals() == 0
    assert _read(audit_db, rid)["status"] == "IN_TRADE"


def test_quarantine_is_idempotent(audit_db):
    from src.tracking.suggested_trades_auditor import revalidate_impossible_verticals

    _seed(audit_db)
    assert revalidate_impossible_verticals() == 1
    assert revalidate_impossible_verticals() == 0


def test_quarantine_leaves_equity_rows_alone(audit_db):
    from src.tracking.suggested_trades_auditor import revalidate_impossible_verticals

    rid = _seed(audit_db, trade_type="EQUITY", trade_structure="SHARES",
                trade_label="SHARES", long_strike=0.0, short_strike=0.0, target_debit=0.0)
    assert revalidate_impossible_verticals() == 0
    assert _read(audit_db, rid)["status"] == "IN_TRADE"