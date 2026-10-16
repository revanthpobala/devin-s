"""Phase 0 gate changes: Pine-stop rule, zone-top R:R for waiting limits, earnings as flag vs reject."""

from unittest.mock import patch

import pytest

from src.logic import level_validation as lv
from src.logic.level_validation import validate_levels


def _plan(**kw):
    p = {
        "entry_type": "LIMIT", "entry_zone_low": 98.0, "entry_zone_high": 100.0,
        "tactical_stop": 95.0, "target_1": 112.0, "target_2": 118.0,
        "setup_lane": "JUDGE", "source": "judge", "kind": "NEW",
    }
    p.update(kw)
    return p


def _dw(**kw):
    d = {"Close": 100.0, "RSI2 ATR14": 2.0, "Long Stop Loss": 95.5}
    d.update(kw)
    return d


def _run(plan, dw, earn=None):
    with patch.object(lv, "earnings_days_for", return_value=earn):
        return validate_levels(plan, dw, "LONG", ticker="TEST", date_str="2026-10-15")


def _stop_msgs(reasons):
    return [r for r in reasons if "max allowed stop" in r]


def test_stop_at_or_below_pine_stop_passes_even_when_inside_one_atr():
    # fill 98, ATR 2: the old rule needed stop <= 96. Pine stop is 95.5, stop 95 is below it.
    ok, reasons = _run(_plan(), _dw())
    assert not _stop_msgs(reasons), reasons


def test_stop_tighter_than_pine_stop_fails():
    ok, reasons = _run(_plan(tactical_stop=96.5), _dw())
    assert _stop_msgs(reasons)


def test_no_pine_stop_uses_point_seven_atr():
    dw = _dw()
    dw.pop("Long Stop Loss")
    ok, reasons = _run(_plan(tactical_stop=96.7), dw)  # fill 98 - 0.7*2 = 96.6
    assert _stop_msgs(reasons)
    ok, reasons = _run(_plan(tactical_stop=96.5), dw)
    assert not _stop_msgs(reasons)


def test_waiting_limit_uses_zone_top_rr_not_at_market():
    # spot 103 above the zone; at market (112-103)/(103-95)=1.1, at zone top (112-100)/(100-95)=2.4
    plan = _plan()
    ok, reasons = _run(plan, _dw(Close=103.0))
    assert plan["deferred"] is True
    assert plan["rr_at_zone"] == pytest.approx(2.4)
    assert not any("R:R" in r and "below" in r and "floor" in r and "planned" not in r for r in reasons), reasons


def test_chase_beyond_half_the_move_is_not_deferred():
    plan = _plan()
    _run(plan, _dw(Close=107.0))  # 7 > 0.5 * 12
    assert plan["deferred"] is False
    assert plan["rr_at_zone"] is None


def test_market_entry_keeps_at_market_rr():
    plan = _plan(entry_type="MARKET")
    ok, reasons = _run(plan, _dw(Close=103.0))
    assert plan["deferred"] is False
    assert any("at-market R:R" in r for r in reasons)


def test_earnings_is_not_a_blackout_when_stance_declared():
    plan = _plan(entry_zone_high=104.0, earnings_stance="hold through")
    ok, reasons = _run(plan, _dw(Close=103.0), earn=10)
    assert plan["earnings_stance"] == "HOLD_THROUGH"
    assert not any("earnings" in r for r in reasons)


def test_judge_plan_inside_earnings_window_must_declare_stance():
    plan = _plan(entry_zone_high=104.0)
    ok, reasons = _run(plan, _dw(Close=103.0), earn=10)
    assert any("earnings_stance missing" in r for r in reasons)


def test_no_stance_needed_outside_window():
    ok, reasons = _run(_plan(), _dw(), earn=40)
    assert not any("earnings" in r for r in reasons)


def test_earnings_days_ride_on_a_waiting_plan():
    plan = _plan(earnings_stance="EXIT_BEFORE")
    ok, reasons = _run(plan, _dw(Close=103.0), earn=10)
    assert plan["deferred"] is True
    assert plan["earnings_days"] == 10
    assert not any("earnings" in r for r in reasons)


def test_replay_rejected_plans_snapshot():
    """Replays the rejected_plans copy when present; every plan must run through the validator without error."""
    import json
    import sqlite3
    from pathlib import Path

    db = Path("/tmp/rw/research_watch.db")
    if not db.exists():
        pytest.skip("rejected_plans snapshot not available")
    rows = sqlite3.connect(db).execute(
        "select ticker, date, plan_json from rejected_plans where date >= '2026-10-05'"
    ).fetchall()
    seen, passed = set(), 0
    for t, d, pj in rows:
        if (t, d) in seen or not pj:
            continue
        seen.add((t, d))
        plan = json.loads(pj)
        plan.pop("rr_at_market", None)
        dw = {"Close": plan.get("spot") or 0.0}
        with patch.object(lv, "earnings_days_for", return_value=None):
            ok, _ = validate_levels(plan, dw, "LONG", ticker=t, date_str=d)
        passed += bool(ok)
    assert len(seen) > 0
