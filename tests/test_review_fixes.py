"""
Regression tests for the bugs an independent adversarial review found in this session's work.

Each test names the failure it pins. Every one of these was live and reachable before it was
fixed; the descriptions say what broke, not what the fix looks like.
"""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3

import pytest

from src.logic.strike_validator import (
    UNSUPPORTED_STRUCTURE_TERMS,
    spread_intrinsic_value,
    validate_spread_geometry,
    vertical_mark_value,
)
from src.tracking.entry_risk_alerts import Onset


# =====================================================================================
# 1. SchwabScanRequest lost side/date/headless -> short scans silently ran long
# =====================================================================================

def test_schwab_scan_request_still_declares_its_fields():
    """`_pb_lane_prior` was inserted INSIDE the class body, so pydantic saw a 4-field model.

    Pydantic ignores unknown extras, so `side: "short"` was accepted and dropped: every POST
    short scan ran long and said nothing.
    """
    from src.ui.routes.screener import SchwabScanRequest

    fields = set(SchwabScanRequest.model_fields)
    for required in ("side", "date", "headless", "top", "auto_max"):
        assert required in fields, f"{required} was dropped from SchwabScanRequest"
    assert SchwabScanRequest(side="short").side == "short"
    assert SchwabScanRequest().side == "long"


def test_only_one_scan_request_class_is_defined():
    """A redefinition would leave the partial model shadowing the real one depending on import order."""
    from src.ui.routes import screener

    tree = ast.parse(inspect.getsource(screener))
    names = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert names.count("SchwabScanRequest") == 1, names


def test_short_scan_reaches_the_scan_path():
    from src.ui.routes import screener

    src = inspect.getsource(screener.get_schwab_screener_candidates)
    assert 'req_side == "short"' in src, "the route no longer branches on side"


# =====================================================================================
# 2/3. vertical_mark_value ignored is_call, and the middle branch marked at the debit
# =====================================================================================

def test_max_profit_direction_follows_the_structure_not_the_strike_order():
    """A debit put vertical is long the HIGHER strike. Pricing it by numeric ordering books a
    max-loss put spread at the full width."""
    assert vertical_mark_value(120.0, 110.0, 100.0, 3.0, is_call=False) == 0.0
    assert vertical_mark_value(90.0, 110.0, 100.0, 3.0, is_call=False) == 1000.0
    assert vertical_mark_value(93.0, 95.0, 105.0, 3.0, is_call=True) == 0.0
    assert vertical_mark_value(110.0, 95.0, 105.0, 3.0, is_call=True) == 1000.0


def test_a_marked_at_the_debit_can_hide_a_real_intrinsic():
    """The middle branch returned the debit, so spot 100 on 95/105 (5.00 intrinsic) booked $0 P&L."""
    assert spread_intrinsic_value(100.0, 95.0, 105.0, is_call=True) == 5.0
    assert vertical_mark_value(100.0, 95.0, 105.0, 3.0, is_call=True) == 500.0


def test_the_debit_is_not_a_floor_on_the_mark():
    """Flooring at the debit books every stopped-out spread as break-even. An underlying stop
    lands between the strikes, so that was every stopped-out spread."""
    assert vertical_mark_value(93.0, 95.0, 105.0, 3.0, is_call=True) == 0.0
    assert vertical_mark_value(97.0, 98.0, 103.0, 3.0, is_call=True) < 300.0


def test_the_mark_is_monotone_and_bounded_for_both_structures():
    for is_call, lk, sk in ((True, 98.0, 105.0), (False, 105.0, 98.0)):
        spots = [70.0, 90.0, 98.0, 100.0, 103.0, 110.0, 130.0]
        marks = [vertical_mark_value(s, lk, sk, 3.0, is_call) for s in spots]
        width = abs(lk - sk) * 100
        assert all(0.0 <= m <= width for m in marks), marks
        if is_call:
            assert marks == sorted(marks), marks
        else:
            assert marks == sorted(marks, reverse=True), marks


def test_max_loss_and_max_profit_bracket_the_position():
    """Full debit lost at one end, full width at the other -- for calls AND puts."""
    for is_call, lk, sk in ((True, 95.0, 105.0), (False, 105.0, 95.0)):
        width = abs(lk - sk) * 100
        if is_call:
            assert vertical_mark_value(90.0, lk, sk, 3.0, is_call) == 0.0
            assert vertical_mark_value(110.0, lk, sk, 3.0, is_call) == width
        else:
            assert vertical_mark_value(110.0, lk, sk, 3.0, is_call) == 0.0
            assert vertical_mark_value(90.0, lk, sk, 3.0, is_call) == width


# =====================================================================================
# 4. No strike-ordering check; anything-not-"CALL" priced as a put
# =====================================================================================

def test_inverted_ordering_is_rejected():
    ok_c, d_c = validate_spread_geometry("BULL_CALL_SPREAD", 100.0, 105.0, 95.0, 3.0)
    assert ok_c is False and any("ordering is inverted" in d for d in d_c)
    ok_p, d_p = validate_spread_geometry("BEAR_PUT_SPREAD", 100.0, 95.0, 105.0, 3.0)
    assert ok_p is False and any("ordering is inverted" in d for d in d_p)


def test_a_correct_put_vertical_still_passes():
    assert validate_spread_geometry("BEAR_PUT_SPREAD", 92.0, 95.0, 90.0, 4.0)[0] is True


@pytest.mark.parametrize("struct", ["IRON_CONDOR", "BUTTERFLY", "CALENDAR", "STRADDLE", "STRANGLE"])
def test_multi_leg_structures_are_refused_not_guessed(struct):
    ok, defects = validate_spread_geometry(struct, 100.0, 95.0, 105.0, 1.0)
    assert ok is False
    assert any("not a 2-leg debit vertical" in d for d in defects)


def test_unrecognised_structure_is_refused():
    ok, defects = validate_spread_geometry("MYSTERY_STRUCTURE", 100.0, 95.0, 105.0, 3.0)
    assert ok is False
    assert any("Unrecognised structure" in d for d in defects)


def test_unsupported_terms_are_declared():
    assert "CONDOR" in UNSUPPORTED_STRUCTURE_TERMS
    assert "BUTTERFLY" in UNSUPPORTED_STRUCTURE_TERMS


def test_inverted_pair_intrinsic_is_not_silently_zero():
    assert spread_intrinsic_value(120.0, 105.0, 95.0, is_call=True) == 15.0
    assert spread_intrinsic_value(80.0, 95.0, 105.0, is_call=False) == 15.0


# =====================================================================================
# 5. stop_width_atr rounded to 2dp BEFORE the <0.7 test
# =====================================================================================

def _onset_atr_width(width: float) -> Onset:
    return Onset(
        ticker="SWKS", date="d", close=100.0, stop=100.0 - width, target=130.0, atr=1.0,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=True,
    )


def test_a_stop_just_under_the_floor_is_still_flagged():
    """0.6965 ATR rounds to 0.70 for display and was then tested as 0.70 < 0.7 -> False.

    That let through the exact artifact-R:R stop this flag exists to suppress.
    """
    o = _onset_atr_width(0.6965)
    assert o.stop_width_atr == 0.7, "display rounding is still fine"
    assert o.stop_tight is True, "but the flag must use the raw width"


@pytest.mark.parametrize("width,expected", [
    (0.5, True), (0.6965, True), (0.6999, True),
    (0.7, False), (0.7001, False), (1.0, False), (2.0, False),
])
def test_flag_boundary_uses_the_exact_width(width, expected):
    assert _onset_atr_width(width).stop_tight is expected


def test_stop_tight_is_false_when_width_is_unmeasurable():
    o = Onset(ticker="X", date="d", close=100.0, stop=95.0, target=115.0, atr=None,
              long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
              packs_present=True)
    assert o.stop_tight is False


# =====================================================================================
# 6/7. run_alerts: --dry-run sent real pushes; the loop re-paged after close
# =====================================================================================

def _run_alerts_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "run_alerts.py").read_text(encoding="utf-8")


def test_dry_run_never_calls_a_firing_function():
    """fire_entry/fire_risk dispatch for real. A dry run that called them paged the phone."""
    src = _run_alerts_source()
    dry = src.split("if args.dry_run:")[1].split("else:")[0]
    for firing in ("fire_entry(", "fire_risk(", "fire_income(", "run_digest(date=date)"):
        assert firing not in dry, f"--dry-run still calls {firing}"
    assert "notify_enabled=False" in dry


def test_dry_run_still_reports_what_would_fire():
    src = _run_alerts_source()
    dry = src.split("if args.dry_run:")[1].split("else:")[0]
    assert "entry_gate()" in dry, "dry run must compute the gate without dispatching"


def test_the_loop_emits_the_digest_once_per_session():
    """run_digest fires INCOME with force=True, so re-running it per tick re-paged all evening."""
    src = _run_alerts_source()
    loop = src.split("if args.loop:")[1]
    assert "last_digest_date" in loop
    assert "today != last_digest_date" in loop, "the loop must not re-run the digest every tick"


def test_the_real_path_does_not_overwrite_entry_pushed():
    """out.update(run_digest(...)) replaced entry_pushed with the digest's dedupe-suppressed
    result, so every real run reported [] even when pushes had fired."""
    src = _run_alerts_source()
    real = src.split("else:", 2)[-1].split("out[\"ops\"]")[0]
    assert "out.update(" not in real, "the merge must not clobber entry_pushed with []"
    assert '"entry_pushed": entry_out' in real


# =====================================================================================
# 8. Screener Stop-ATR column read a key the manifest never writes
# =====================================================================================

def test_screener_accepts_the_manifests_atrs_up_field():
    """schwab_pre_move_scan writes `atrs_up`; the route read `stop_width_atr`, so the Stop ATR
    column the table advertises rendered '-' for every candidate."""
    from src.screener import schwab_pre_move_scan
    from src.ui.routes import screener

    scan_src = inspect.getsource(schwab_pre_move_scan)
    assert '"atrs_up"' in scan_src, "precondition: the manifest writes atrs_up"

    route_src = inspect.getsource(screener.get_schwab_screener_candidates)
    assert "atrs_up" in route_src, "the route must read the manifest's field name"


def test_screener_passes_atrs_up_through_to_stop_width_atr(tmp_path):
    from src.ui.routes import screener as sr

    raw = tmp_path / "data" / "raw" / "2026-09-30"
    raw.mkdir(parents=True)
    (raw / "schwab_survivors.json").write_text(json.dumps([
        {"symbol": "GLW", "price": 100.0, "support_level": 95.0, "pb_funnel": True,
         "proxy_rr": 3.4, "atrs_up": 0.2},
    ]), encoding="utf-8")

    from unittest.mock import MagicMock, patch
    with patch.object(sr.config, "BASE_DIR", tmp_path), \
         patch("src.screener.schwab_pre_move_scan.get_schwab_client", return_value=MagicMock()), \
         patch("src.screener.schwab_pre_move_scan.check_market_tide",
               return_value={"is_bullish": True, "available": True, "trend_str": "BULLISH"}):
        out = sr.get_schwab_screener_candidates(date="2026-09-30", side="long")

    assert out["candidates"][0]["stop_width_atr"] == 0.2


def test_unmeasured_pb_gets_no_borrowed_long_side_prior():
    """_pb_lane_prior passed pb=None into lane_prior, which fell through to the long-side
    number -- a prior the row has no claim to."""
    from src.ui.routes.screener import _pb_lane_prior

    assert _pb_lane_prior(3.0, None) is None
    assert _pb_lane_prior(3.0, True) is not None
    assert _pb_lane_prior(3.0, False) is not None


# =====================================================================================
# 9/10. Auditor: silent delete instead of quarantine; re-priced at today's spot
# =====================================================================================

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
    cur = conn.execute(
        f"INSERT INTO suggested_trades_audit ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        list(row.values()),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def _status(db, rid):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM suggested_trades_audit WHERE id = ?", (rid,)).fetchone())
    conn.close()
    return row


def test_quarantine_prices_at_the_entry_not_todays_spot(audit_db):
    """Re-pricing entry-time strikes against a later print invents geometry failures on rows that
    were fine when opened -- and quarantining nulls r_multiple with no way back."""
    from src.tracking.suggested_trades_auditor import revalidate_impossible_verticals

    # entry 607.5 with 480/510 is genuinely impossible (intrinsic 30.00 vs a 12.70 debit)
    rid_bad = _seed(audit_db, ticker="BAD", r_multiple=1.5)
    assert revalidate_impossible_verticals() == 1
    assert _status(audit_db, rid_bad)["status"] == "INVALID_GEOMETRY"

    # A sound vertical that has since drifted must NOT be quarantined by its current price.
    rid_ok = _seed(
        audit_db, ticker="OK", trade_structure="BULL_CALL_SPREAD",
        trade_label="BULL CALL SPREAD (98/105)", entry_price=100.0,
        entry_zone_low=99.0, entry_zone_high=101.0, tactical_stop=95.0, target_1=110.0,
        long_strike=98.0, short_strike=105.0, target_debit=3.0,
        max_profit=700.0, max_loss=300.0, last_price=140.0,   # drifted way up since entry
        r_multiple=2.0, status="IN_TRADE",
    )
    revalidate_impossible_verticals()
    assert _status(audit_db, rid_ok)["status"] == "IN_TRADE", \
        "a later print must not retroactively invalidate a sound entry-time structure"


def test_the_creation_gate_records_defective_verticals_instead_of_dropping_them(audit_db):
    """`and not opt_defects` made the insert unreachable for exactly the rejected rows, so a bad
    structure vanished rather than being recorded as INVALID_GEOMETRY."""
    from src.tracking import suggested_trades_auditor as aud

    src = inspect.getsource(aud.sync_suggested_trades_from_watch_targets)
    insert_guard = [ln for ln in src.splitlines() if "is_income or has_actionable_options" in ln]
    assert insert_guard, "insert guard not found"
    assert "not opt_defects" not in insert_guard[0], (
        "defective verticals must still be inserted so they land in the ledger as quarantined"
    )
    assert "rejected_geometry" in src, "the quarantines must be counted and logged"
    assert "QUARANTINED" in src