"""
tests/test_breakout_geometry.py

C1 from plan/no-lost-movers.md — the META case.

The stored META plan (data/raw/2026-10-09/META/META_watch_levels.json) carried
entry_type LIMIT with breakout_level 747.60, breakout_stop 735.00, target_1
742.09 and target_2 747.60: both targets at or below the trigger. check_geometry
only ordered levels for entry_type == "BREAKOUT", so the plan was stored, then
evaluated against a trigger its own targets had already passed. Every plan that
carries a trigger must be geometry-checked now, and a broken trigger is demoted
(stripped) rather than silently kept — the limit-zone path survives.
"""

from __future__ import annotations

import pytest

from src.logic.level_validation import check_breakout_geometry, validate_levels

# The real META numbers, verbatim from the stored watch levels.
META = {
    "entry_type": "LIMIT",
    "entry_zone_low": 710.43,
    "entry_zone_high": 715.95,
    "breakout_level": 747.60,
    "breakout_stop": 735.00,
    "tactical_stop": 698.50,
    "target_1": 742.09,
    "target_2": 747.60,
}


def test_meta_trigger_fails_breakout_geometry():
    ok, reasons = check_breakout_geometry(
        breakout_level=META["breakout_level"],
        breakout_stop=META["breakout_stop"],
        target_1=META["target_1"],
        side="LONG",
    )
    assert ok is False
    # Both targets sit at/below the trigger: the ordering reason covers the case,
    # so the negative R:R at the trigger is not separately reported.
    assert any("below breakout trigger" in r for r in reasons)
    assert len(reasons) == 1


def test_breakout_geometry_accepts_valid_trigger():
    ok, reasons = check_breakout_geometry(
        breakout_level=100.0,
        breakout_stop=95.0,
        target_1=120.0,
        side="LONG",
    )
    assert ok is True
    assert reasons == []


def test_breakout_geometry_rejects_low_rr_at_trigger():
    """A trigger whose target IS above it still fails when the R:R at the trigger is thin."""
    ok, reasons = check_breakout_geometry(
        breakout_level=100.0,
        breakout_stop=99.50,   # 0.5 risk at the trigger
        target_1=100.60,       # 0.6 reward -> R:R 1.2 < 2.0 floor
        side="LONG",
    )
    assert ok is False
    assert any("R:R at trigger" in r for r in reasons)


def test_breakout_geometry_no_trigger_is_vacuous():
    ok, reasons = check_breakout_geometry(breakout_level=0.0, breakout_stop=95.0, target_1=120.0)
    assert ok is True
    assert reasons == []


def test_validate_levels_strips_meta_trigger_but_keeps_zone_path():
    """The demotion must not reject the limit-zone plan: trigger stripped, reason recorded."""
    plan = dict(META)
    plan["spot"] = 718.67
    plan["setup_lane"] = "JUDGE"
    dw = {"close": 718.67, "rsi2_atr14": 12.0}

    ok, reasons = validate_levels(plan, dw, "LONG")

    # The trigger is gone, and no failure reason claims the trigger is fine.
    assert plan["breakout_level"] == 0.0
    assert plan["breakout_stop"] == 0.0
    assert "below breakout trigger" in plan["breakout_demoted_reason"]
    assert not any("below breakout trigger" in r for r in reasons)
    # ... even though the plan still fails its other checks (the zone path's own R:R floor).
    assert ok is False
    assert any("planned r:r" in r.lower() or "at-market r:r" in r.lower() for r in reasons)
    # The zone itself is untouched — that is the path that survives.
    assert plan["entry_zone_low"] == 710.43
    assert plan["entry_zone_high"] == 715.95


def test_validate_levels_records_rr_at_trigger_for_a_valid_breakout():
    plan = {
        "entry_type": "BREAKOUT",
        "breakout_level": 747.60,
        "breakout_stop": 735.00,
        "tactical_stop": 735.00,
        "target_1": 775.00,
        "target_2": 800.00,
        "spot": 740.00,
        "setup_lane": "JUDGE",
    }
    dw = {"close": 740.00, "rsi2_atr14": 12.0}

    ok, reasons = validate_levels(plan, dw, "LONG")

    assert plan["breakout_level"] == 747.60  # valid trigger survives
    expected_rr = (775.00 - 747.60) / (747.60 - 735.00)
    assert plan["rr_at_trigger"] == pytest.approx(expected_rr, abs=1e-4)
    # Not a rejection reason: the geometry at the trigger is sound.
    assert not any("below breakout trigger" in r for r in reasons)


def test_validate_levels_pure_breakout_plan_fails_when_trigger_demoted():
    """A plan whose breakout IS the entry cannot survive the demotion — no entry is left."""
    plan = {
        "entry_type": "BREAKOUT",
        "breakout_level": 747.60,
        "breakout_stop": 735.00,
        "tactical_stop": 735.00,
        "target_1": 742.09,   # at/below trigger -> demote
        "target_2": 747.60,
        "spot": 740.00,
        "setup_lane": "JUDGE",
    }
    dw = {"close": 740.00, "rsi2_atr14": 12.0}

    ok, reasons = validate_levels(plan, dw, "LONG")

    assert ok is False
    assert any("breakout demoted, no entry left" in r for r in reasons)
    assert plan["breakout_level"] == 0.0
