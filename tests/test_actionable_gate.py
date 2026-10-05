"""
Table tests for src/logic/actionable_gate.py.

Every row in the table is a minimal data-window + plan pair.  The gate must fail
with the expected reason strings, and must pass only when every condition is
met exactly.
"""

from __future__ import annotations

import pytest

from src.logic.actionable_gate import is_actionable, STOP_ATR_MIN
from src.tracking import rr_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dw(**overrides):
    """Minimal data-window dict that clears the gate when every field is set."""
    base = {
        "long_in_zone": 1.0,
        "long_rr_at_market": 2.5,
        "long_stop_loss": 95.0,
        "atr14": 5.0,
        "price": 100.0,
        "signal_pack": 36.0,   # bit2 (fade off) + bit5 (PB)
        "fade_long": 0.0,      # bit2 set -> fade off
        "action_long": 1.0,    # not 17/18
        "ext_z_self": 0.0,
    }
    base.update(overrides)
    return base


def _plan(side="long", entry=None):
    p = {"side": side}
    if entry is not None:
        p["entry"] = entry
    return p


# ---------------------------------------------------------------------------
# Pass cases
# ---------------------------------------------------------------------------

class TestGatePasses:
    def test_all_gates_clear(self):
        dw = _dw()
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True
        assert fails == []

    def test_stop_width_exactly_at_floor(self):
        """(entry - stop) / ATR == 0.7 is the floor, not below it."""
        dw = _dw(long_stop_loss=96.5, atr14=5.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True

    def test_rr_at_min_floor(self):
        """RR == min_rr() is >= floor."""
        dw = _dw(long_rr_at_market=rr_config.min_rr())
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True

    def test_ext_z_below_cap(self):
        """Ext Z < 2.5 passes; exactly 2.4 is fine."""
        dw = _dw(ext_z_self=2.4)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------

class TestGateFails:
    def test_missing_in_zone(self):
        dw = _dw(long_in_zone=0.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert "not in the long zone" in fails

    def test_missing_rr(self):
        dw = _dw(long_rr_at_market=None)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert "long_rr_at_market missing" in fails

    def test_rr_below_floor(self):
        dw = _dw(long_rr_at_market=1.99)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("RR@mkt" in f for f in fails)

    def test_stop_width_below_floor(self):
        """0.4 ATR stop: (100 - 98) / 5 = 0.4 < 0.7."""
        dw = _dw(long_stop_loss=98.0, atr14=5.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("stop width" in f and "0.4" in f for f in fails), fails

    def test_missing_entry_stop_or_atr(self):
        for missing in ("stop", "atr"):
            dw = _dw()
            plan = _plan(entry=100.0)
            if missing == "stop":
                dw["long_stop_loss"] = None
            elif missing == "atr":
                dw["atr14"] = None
            ok, fails = is_actionable(dw, plan)
            assert ok is False
            assert any("missing" in f for f in fails), f"missing={missing}"

    def test_missing_entry_falls_back_to_price(self):
        """When the plan has no entry, the gate falls back to dw['price']."""
        dw = _dw()
        plan = {"side": "long"}   # no entry key, but side is correct
        ok, fails = is_actionable(dw, plan)
        assert ok is True, f"price fallback should clear: {fails}"

    def test_atr_zero(self):
        dw = _dw(atr14=0.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("ATR" in f for f in fails)

    def test_pb_bit_off(self):
        dw = _dw(signal_pack=4.0)   # bit2 only (fade off), no PB
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("not PB funnel" in f for f in fails), fails

    def test_fade_long_is_true(self):
        """fade_long=True means the fade gate is active."""
        dw = _dw(fade_long=True)   # explicit True, not derived from signal_pack
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("fade" in f.lower() for f in fails), fails

    def test_action_code_17_parabolic(self):
        dw = _dw(action_long=17.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("PARABOLIC" in f for f in fails), fails

    def test_action_code_18_toxic(self):
        dw = _dw(action_long=18.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("TOXIC" in f for f in fails), fails

    def test_ext_z_at_cap(self):
        """Ext Z >= 2.5 fails; exactly 2.5 is the hard cap."""
        dw = _dw(ext_z_self=2.5)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("ext_z_self" in f for f in fails), fails

    def test_short_plan_rejected(self):
        plan = _plan(side="short", entry=100.0)
        ok, fails = is_actionable(_dw(), plan)
        assert ok is False
        assert any("not a long plan" in f for f in fails), fails

    def test_missing_signal_pack(self):
        dw = _dw()
        del dw["signal_pack"]
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("signal_pack missing" in f for f in fails), fails

    def test_google_sept_30_stays_blocked(self):
        """GOOGL 2026-09-30: ext_z >= 2.5 blocks the row."""
        dw = _dw(
            price=170.0,
            long_stop_loss=165.0,
            atr14=5.0,
            long_rr_at_market=3.0,
            ext_z_self=2.6,
        )
        plan = _plan(entry=170.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("ext_z_self" in f for f in fails), fails


class TestUnroundedComparison:
    def test_rr_1_996_below_floor(self):
        """1.996 < 2.0 floor: unrounded comparison means 1.996 does not pass."""
        dw = _dw(long_rr_at_market=1.996)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("RR@mkt" in f and "1.996" in f for f in fails), fails

    def test_rr_2_000_above_floor(self):
        """2.0 >= 2.0 floor: passes the RR check."""
        dw = _dw(long_rr_at_market=2.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True


class TestStopWidthPrecision:
    def test_width_0_69_fails(self):
        """0.69 ATR < 0.7 floor: fails."""
        dw = _dw(long_stop_loss=96.55, atr14=5.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is False
        assert any("0.69" in f for f in fails), fails

    def test_width_0_70_passes(self):
        """0.70 ATR == floor: passes."""
        dw = _dw(long_stop_loss=96.5, atr14=5.0)
        plan = _plan(entry=100.0)
        ok, fails = is_actionable(dw, plan)
        assert ok is True
