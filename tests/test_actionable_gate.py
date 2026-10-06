"""
Table tests for src/logic/actionable_gate.py.

Every row in the table is a minimal data-window + plan pair.  The gate must fail
with the expected reason strings, and must pass only when every condition is
met exactly.
"""

from __future__ import annotations

import pytest

from src.logic.actionable_gate import is_actionable, STOP_ATR_MIN, gate_inputs_from_datawindow
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


class TestRawDataWindowMapping:
    def test_real_datawindow_row_passes(self):
        """A full raw Data Window payload with TradingView / Pine indicator names maps correctly and passes."""
        raw_dw = {
            "Signal Pack": 36.0,
            "Zone RR Flags Pack": 1.0,
            "Context Action Pack": 20.0,
            "Ext Z Self Relative": 0.5,
            "RSI2 ATR14": 2.5,
            "Long RR At Market": 2.5,
            "Long Stop Loss": 95.0,
            "Close": 100.0,
        }
        mapped = gate_inputs_from_datawindow(raw_dw)
        ok, fails = is_actionable(mapped, {"side": "long", "entry": 100.0})
        assert ok is True, f"Expected pass, got fails: {fails}"
        assert fails == []

    def test_each_missing_input_fails_closed_with_explicit_reason(self):
        """Every required gate input fails closed with an explicit reason string when missing."""
        test_cases = [
            ("long_in_zone", "long_in_zone missing"),
            ("long_rr_at_market", "long_rr_at_market missing"),
            ("long_stop_loss", "long_stop_loss missing for stop-width check"),
            ("atr14", "atr14 missing for stop-width check"),
            ("signal_pack", "signal_pack missing"),
            ("fade_long", "fade_long missing"),
            ("action_long", "action_long missing"),
            ("ext_z_self", "ext_z_self missing"),
        ]
        for field, expected_reason in test_cases:
            dw = _dw()
            dw[field] = None
            if field == "fade_long":
                # Ensure fade_long cannot be derived from signal_pack
                dw["signal_pack"] = None
            ok, fails = is_actionable(dw, {"side": "long", "entry": 100.0})
            assert ok is False, f"Field {field} was None but gate passed!"
            assert expected_reason in fails, f"Field {field}: expected {expected_reason!r} in {fails}"


class TestSchwStyleRowBlocked:
    def test_schw_no_entry_in_zone_blocked_in_evaluator(self):
        """SCHW-style setup with entry_type=NO_ENTRY arriving in zone must NEVER return ACTIONABLE_BUY."""
        from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival
        target = {
            "ticker": "SCHW",
            "side": "LONG",
            "entry_type": "NO_ENTRY",
            "entry_zone_low": 68.0,
            "entry_zone_high": 70.0,
            "tactical_stop": 65.0,
            "target_1": 80.0,
            "signal_pack": 4,  # Not PB funnel (missing bit 5)
            "action_long": 10,
            "atr_at_signal": 2.28,
            "fade": 0.0,
            "ext_z": 0.0,
        }
        res = evaluate_target_on_zone_arrival("SCHW", 69.0, target)
        assert res["is_actionable_now"] is False
        assert res["verdict"] != "ACTIONABLE_BUY"
        assert "ACTIONABLE_BUY" not in res.get("verdict_label", "")

    def test_schw_no_entry_rejected_by_geometry(self, tmp_path, monkeypatch):
        """upsert_watch_target for a NO_ENTRY setup is rejected by geometry gate and never persisted."""
        from pathlib import Path
        import sqlite3
        import src.tracking.watch_manager as wm

        test_db = Path(tmp_path / "test_watch.db")
        monkeypatch.setattr(wm, "DB_PATH", test_db)
        wm.init_watch_db()

        watch_payload = {
            "ticker": "SCHW",
            "date": "2026-10-05",
            "status": "IN_ZONE",
            "verdict": "STALK",
            "last_price": 69.0,
            "shares_plan": {
                "entry_type": "NO_ENTRY",
                "entry_zone_low": 68.0,
                "entry_zone_high": 70.0,
                "tactical_stop": 65.0,
                "target_1": 80.0,
            },
            "signal_pack": 4,
            "fade": 0.0,
            "action_long": 10,
            "ext_z": 0.0,
            "atr_at_signal": 2.28,
            "rr_at_market_at_signal": 2.75,
        }
        wm.upsert_watch_target(watch_payload)

        with sqlite3.connect(test_db) as conn:
            row = conn.execute("SELECT ticker FROM watch_targets WHERE ticker = 'SCHW'").fetchone()
            assert row is None, "NO_ENTRY setup should be rejected by geometry and not persisted"

    def test_schw_gate_failing_limit_persists_with_actionable_zero(self, tmp_path, monkeypatch):
        """A setup with entry_type=LIMIT that fails the actionable gate persists with actionable=0."""
        from pathlib import Path
        import sqlite3
        import src.tracking.watch_manager as wm

        test_db = Path(tmp_path / "test_watch.db")
        monkeypatch.setattr(wm, "DB_PATH", test_db)
        wm.init_watch_db()

        watch_payload = {
            "ticker": "SCHW",
            "date": "2026-10-05",
            "status": "IN_ZONE",
            "verdict": "STALK",
            "last_price": 69.0,
            "shares_plan": {
                "entry_type": "LIMIT",
                "entry_zone_low": 68.0,
                "entry_zone_high": 70.0,
                "tactical_stop": 65.0,
                "target_1": 80.0,
            },
            "signal_pack": 4,  # Missing PB funnel bit 5
            "fade": 0.0,
            "action_long": 10,
            "ext_z": 0.0,
            "atr_at_signal": 2.28,
            "rr_at_market_at_signal": 2.75,
        }
        wm.upsert_watch_target(watch_payload)

        with sqlite3.connect(test_db) as conn:
            row = conn.execute("SELECT ticker, actionable, status FROM watch_targets WHERE ticker = 'SCHW'").fetchone()
            assert row is not None
            assert row[1] == 0, f"Expected actionable=0, got {row[1]}"


class TestTickerValidation:
    def test_valid_tickers_pass(self):
        from src.ui.routes.research import validate_ticker
        for sym in ("AAPL", "BRK/B", "GOOGL", "SPY", "QQQ", "TSLA", "A"):
            assert validate_ticker(sym) == sym.upper()

    def test_invalid_tickers_rejected(self):
        from fastapi import HTTPException
        from src.ui.routes.research import validate_ticker
        for bad in ("../../etc/passwd", "AAPL;DROP", "VERYLONGTICKERNAME", "AAPL$", "<script>", ""):
            with pytest.raises(HTTPException) as exc:
                validate_ticker(bad)
            assert exc.value.status_code == 400


