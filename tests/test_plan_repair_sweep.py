"""
tests/test_plan_repair_sweep.py

The one-time sweep and read repair for plans already stored with a dropped or
broken breakout (rig item 2d), plus the write-time rules around it:

  1. A stored plan whose trigger was zeroed (only the demotion reason carries the
     value) is rebuilt and re-armed by the sweep — the GOOGL plan
     (trigger $356.83, T1 $356.73) arms without re-running research.
  2. A stored plan whose trigger is present but whose T1/T2 sit at or below it is
     rebuilt above the trigger, tagged targets_rebuilt.
  3. The same repair runs on plan read: the armed set carries the BREAKOUT.
  4. A trigger that cannot be recovered from a real number is skipped, never invented.
  5. A STALK verdict on a plan that arms no level is rejected at write time.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.tracking import level_engine, watch_manager
from src.tracking.plan_repair import (
    reconcile_row_with_plan,
    repair_shares_plan,
    repair_watch_targets,
    sweep_stored_plans,
)
from src.tracking import plan_repair as plan_repair_mod
from src.tracking.level_engine import build_armed_levels, has_armed_level

GOOGL_STORED = {
    "ticker": "GOOGL",
    "verdict": "STALK",
    "conviction": 7,
    "side": "LONG",
    "shares_plan": {
        "entry_type": "LIMIT",
        "entry_zone_low": 349.0,
        "entry_zone_high": 351.0,
        "breakout_level": 0,
        "breakout_stop": 0,
        "tactical_stop": 344.0,
        "target_1": 356.73,
        "target_2": 370.0,
        "breakout_demoted_reason": (
            "target_1 $356.7300 is below breakout trigger $356.8300 — "
            "targets must sit above the trigger"
        ),
    },
    "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 344.0},
    "setup_lane": "COIL",
    "date": "2026-10-12",
    "level_gate_rejected": True,
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(watch_manager, "DB_PATH", tmp_path / "watch.db")
    watch_manager.init_watch_db()
    level_engine.reset_engine_state()
    # No market data reachable: bars fetch returns None so the repair falls back
    # to the plan's own risk basis, never the network.
    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
        yield
    level_engine.reset_engine_state()


def _plan_path(base, ticker="GOOGL", date="2026-10-12"):
    return base / "reports" / date / f"{ticker}_watch_levels.json"


def _write_plan(base, ticker="GOOGL", date="2026-10-12", doc=None):
    p = _plan_path(base, ticker, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc if doc is not None else GOOGL_STORED, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1+2. The sweep rebuilds and re-arms
# ---------------------------------------------------------------------------

def test_sweep_restores_dropped_trigger_and_rebuilds_targets(tmp_path):
    base = tmp_path / "base"
    _write_plan(base)

    with patch("src.config.BASE_DIR", base):
        report = sweep_stored_plans(write=True)

    doc = json.loads(_plan_path(base).read_text(encoding="utf-8"))
    sp = doc["shares_plan"]

    # The trigger came back from the recorded reason — never zeroed.
    assert sp["breakout_level"] == 356.83
    assert sp["breakout_target_1"] > 356.83
    assert sp["breakout_target_2"] > sp["breakout_target_1"]
    assert sp["targets_rebuilt"] is True
    assert "targets rebuilt above it" in sp["breakout_demoted_reason"]
    # The re-arm is real: the plan's own levels include the BREAKOUT at 356.83.
    armed = build_armed_levels(sp, side="LONG")
    by_kind = {lv.kind: lv for lv in armed}
    assert "BREAKOUT" in by_kind
    assert by_kind["BREAKOUT"].price == 356.83
    assert any(r["ticker"] == "GOOGL" for r in report["repaired"])


def test_sweep_dry_run_does_not_write(tmp_path):
    base = tmp_path / "base"
    p = _write_plan(base)
    before = p.read_text(encoding="utf-8")

    with patch("src.config.BASE_DIR", base):
        report = sweep_stored_plans(write=False)

    assert p.read_text(encoding="utf-8") == before
    assert report["repaired"], "the dry run still reports what would change"


def test_sweep_skips_a_trigger_that_cannot_be_recovered(tmp_path):
    """A reason with no recorded trigger value leaves the plan alone — absent means absent."""
    doc = json.loads(json.dumps(GOOGL_STORED))
    doc["shares_plan"]["breakout_demoted_reason"] = "R:R at trigger 0.31 below 2.00 floor"
    base = tmp_path / "base"
    p = _write_plan(base, doc=doc)
    before = p.read_text(encoding="utf-8")

    with patch("src.config.BASE_DIR", base):
        report = sweep_stored_plans(write=True)

    assert p.read_text(encoding="utf-8") == before
    assert report["skipped"] and report["skipped"][0]["ticker"] == "GOOGL"
    assert not report["repaired"]


# ---------------------------------------------------------------------------
# 3. Repair on read: the armed set carries the restored trigger
# ---------------------------------------------------------------------------

def test_plan_read_repair_re_arms_the_trigger():
    """The stored (unrepaired) plan still arms its BREAKOUT on read: the trigger
    value is recovered from the recorded demotion reason at arm time."""
    sp = json.loads(json.dumps(GOOGL_STORED))["shares_plan"]
    armed = build_armed_levels(sp, side="LONG")
    by_kind = {lv.kind: lv for lv in armed}
    assert by_kind["BREAKOUT"].price == 356.83
    # A plan with no level at all arms nothing.
    assert has_armed_level({"entry_type": "LIMIT"}) is False


# ---------------------------------------------------------------------------
# 4. The DB rows: a stored trigger whose targets sit at/below it
# ---------------------------------------------------------------------------

def test_watch_targets_row_with_broken_trigger_is_repaired():
    """GOOGL's stored row shape: trigger 364.17 present, T1 360.00 below it."""
    payload = {
        "ticker": "GOOGL",
        "date": "2026-09-30",
        "verdict": "STALK",
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            "entry_zone_low": 344.58,
            "entry_zone_high": 346.78,
            "breakout_level": 364.17,
            "breakout_stop": 354.0,
            "tactical_stop": 343.5,
            "target_1": 360.0,
            "target_2": 377.16,
        },
        "status": "STALKING",
    }
    watch_manager.upsert_watch_target(payload)

    changes = repair_watch_targets(write=True)
    assert changes["repaired"], changes
    entry = changes["repaired"][0]
    assert entry["breakout_level"] == 364.17
    assert entry["target_1"] > 364.17
    assert entry["target_2"] > entry["target_1"]

    rows = watch_manager.get_all_watch_targets()
    row = next(r for r in rows if r["ticker"] == "GOOGL")
    assert row["breakout_level"] == 364.17
    assert row["target_1"] == 360.0
    assert row["breakout_target_1"] > 364.17
    assert row["breakout_target_2"] > row["breakout_target_1"]


def test_repair_shares_plan_is_a_noop_on_a_sound_plan():
    sp = {
        "breakout_level": 100.0,
        "breakout_stop": 95.0,
        "tactical_stop": 95.0,
        "target_1": 120.0,
        "target_2": 130.0,
    }
    assert repair_shares_plan(dict(sp)) is None


# ---------------------------------------------------------------------------
# 5. STALK with no armed level is rejected at write time
# ---------------------------------------------------------------------------

def test_reconciliation_makes_the_plan_trigger_win_over_a_mis_scraped_row():
    """The GOOGL question, pinned: the plan's trigger is the level that alerts.

    The stored row carried 355.00 (the options put wall, scraped into the row); the
    plan of record — the arbitration directive — is 356.83. Reconciliation follows
    the plan, and reports the disagreement instead of leaving two different armed
    breakouts for one name.
    """
    payload = {
        "ticker": "GOOGL",
        "date": "2026-10-12",
        "verdict": "STALK",
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            "entry_zone_low": 349.0,
            "entry_zone_high": 351.0,
            "breakout_level": 355.0,          # the put wall, not the trigger
            "breakout_stop": 350.0,
            "tactical_stop": 344.0,
            "target_1": 360.0,
            "target_2": 377.16,
        },
        "status": "STALKING",
    }
    watch_manager.upsert_watch_target(payload)

    plan = {
        "entry_type": "LIMIT",
        "entry_zone_low": 349.0,
        "entry_zone_high": 351.0,
        "breakout_level": 356.83,             # the arbitration's binding directive
        "breakout_stop": 353.22,
        "tactical_stop": 344.0,
        "target_1": 357.3803,
        "target_2": 363.971,
    }
    rows = watch_manager.get_all_watch_targets()
    row = next(r for r in rows if r["ticker"] == "GOOGL")

    with patch.object(plan_repair_mod, "_stored_plan_for", return_value=plan):
        recon = reconcile_row_with_plan(row)

    assert recon is not None, "a disagreeing row must be reported"
    assert recon["row_breakout_level"] == 355.0
    assert recon["plan_breakout_level"] == 356.83

    # Dry run first: the row is untouched until the sweep is asked to write.
    # The plan of record is the test's own: reports/ is git-ignored, so reading
    # the live tree here would make the report depend on this machine's state.
    with patch.object(plan_repair_mod, "_stored_plan_for", return_value=plan):
        changes = plan_repair_mod.repair_watch_targets(write=False, reconcile_rows=True)
    assert any(c.get("ticker") == "GOOGL" and c.get("kind") == "reconciled" for c in changes["repaired"])
    unchanged = next(r for r in watch_manager.get_all_watch_targets() if r["ticker"] == "GOOGL")
    assert unchanged["breakout_level"] == 355.0, "a dry run never writes"

    with patch.object(plan_repair_mod, "_stored_plan_for", return_value=plan):
        changes = plan_repair_mod.repair_watch_targets(write=True, reconcile_rows=True)

    after = next(r for r in watch_manager.get_all_watch_targets() if r["ticker"] == "GOOGL")
    assert after["breakout_level"] == 356.83
    assert after["target_1"] == pytest.approx(357.3803)
    assert after["target_2"] == pytest.approx(363.971)
    assert changes["repaired"], changes

    # And the reconciled row arms the plan's trigger — not the put wall.
    from run_watch_alerts import _plan_view_from_row
    from src.tracking import level_engine

    armed = level_engine.build_armed_levels(_plan_view_from_row(after), side="LONG")
    by_kind = {lv.kind: lv for lv in armed}
    assert by_kind["BREAKOUT"].price == 356.83


def test_reconciliation_leaves_a_manual_row_alone_in_a_dry_run_and_agrees_when_equal():
    """A row that already holds the plan's trigger reconciles to nothing."""
    payload = {
        "ticker": "MSFT", "date": "2026-10-12", "verdict": "STALK", "side": "LONG",
        "shares_plan": {
            "entry_type": "BREAKOUT", "entry_zone_low": 500.0, "entry_zone_high": 510.0,
            "breakout_level": 525.0, "breakout_stop": 505.0, "tactical_stop": 505.0,
            "target_1": 545.0, "target_2": 570.0,
        },
        "status": "STALKING",
    }
    watch_manager.upsert_watch_target(payload)
    row = next(r for r in watch_manager.get_all_watch_targets() if r["ticker"] == "MSFT")

    with patch.object(plan_repair_mod, "_stored_plan_for", return_value=dict(payload["shares_plan"])):
        assert reconcile_row_with_plan(row) is None, "an agreeing row reconciles to nothing"
        # A plan with no trigger at all is never reconciled against (nothing real).
        with patch.object(plan_repair_mod, "_stored_plan_for", return_value={"entry_type": "LIMIT"}):
            assert reconcile_row_with_plan(row) is None
        # No stored plan of record (missing file) is the same fail-closed answer:
        # the row is left alone rather than reconciled against an invented level.
        with patch.object(plan_repair_mod, "_stored_plan_for", return_value=None):
            assert reconcile_row_with_plan(row) is None


def test_sync_rejects_a_stalk_with_no_armed_level():
    """A plan that arms nothing has no cross to alert on: upsert is skipped."""
    from run_watch_alerts import sync_reports_to_watchlist

    fake_data = {
        "ticker": "NOLVL",
        "date": "2026-09-23",
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            # No zone, no trigger, no stop, no targets: nothing arms.
        },
    }
    mock_dw = {"Close": 100.0, "ATR": 3.0, "Action Code": 20, "setup_lane": "RR_SETUP"}
    with patch("run_watch_alerts.extract_watch_levels_from_report", return_value=fake_data), \
         patch("run_watch_alerts.upsert_watch_target") as mock_upsert, \
         patch("src.logic.level_validation.validate_levels", return_value=(True, [])), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=json.dumps(mock_dw)):
        count = sync_reports_to_watchlist(target_date="2026-09-23", target_ticker="NOLVL")

    assert count == 0
    mock_upsert.assert_not_called()
