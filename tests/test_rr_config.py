"""
Tests for the UI-tunable R:R gates (src/tracking/rr_config.py) and their wiring.

The point of the control is that it moves the bar. The point of these tests is that it moves ONLY
the bar: the measured guards -- the stop-ATR noise floor, the PB requirement on the ENTRY push,
the R>20 unit-error ceiling -- have to survive any value the user dials in.
"""

from __future__ import annotations

import inspect
import json

import pytest

import src.tracking.rr_config as rc
from src.tracking.entry_risk_alerts import Onset
from src.ui.routes import desk as desk_mod


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own config file, so nothing leaks between them or into data/."""
    path = tmp_path / "rr.json"
    monkeypatch.setenv("RR_CONFIG_PATH", str(path))
    monkeypatch.setattr(rc, "_cache", {})
    monkeypatch.setattr(rc, "_cache_mtime", None)
    yield path


def _onset(ticker="T", rr=3.0, stop=96.5, close=100.0, atr=5.0):
    """An onset whose at-market R:R is exactly `rr`: target = close + rr*(close-stop)."""
    target = close + rr * (close - stop)
    return Onset(
        ticker=ticker, date="2026-09-30", close=close, stop=stop, target=target, atr=atr,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=True,
    )


# --- defaults ------------------------------------------------------------------------------------

def test_shipped_default_is_the_measured_floor():
    assert rc.get_rr_config() == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}
    assert rc.min_rr() == 2.0
    assert rc.hi_rr() == 3.0


def test_nothing_is_written_on_read(isolated_config):
    """Reading must not create a file; defaults must not become an override."""
    rc.get_rr_config()
    rc.min_rr()
    assert not isolated_config.exists()


# --- persistence -----------------------------------------------------------------------------------

def test_set_persists_and_survives_a_cold_read(isolated_config):
    rc.set_rr_config(rr_market_min=3.5, rr_hi_rr=4.0)
    stored = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert stored["rr_market_min"] == 3.5
    assert stored["rr_hi_rr"] == 4.0
    rc._cache.clear()
    rc._cache_mtime = None
    assert rc.min_rr() == 3.5
    assert rc.hi_rr() == 4.0


def test_partial_update_leaves_other_keys_alone():
    rc.set_rr_config(rr_hi_rr=8.0)
    rc.set_rr_config(rr_market_min=3.0)
    cfg = rc.get_rr_config()
    assert cfg == {"rr_market_min": 3.0, "rr_hi_rr": 8.0}


def test_reset_restores_defaults(isolated_config):
    rc.set_rr_config(rr_market_min=9.0, rr_hi_rr=12.0)
    assert rc.reset_rr_config() == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}
    assert not isolated_config.exists()


def test_corrupt_file_falls_back_to_defaults_instead_of_raising(isolated_config):
    isolated_config.write_text("{not json", encoding="utf-8")
    assert rc.get_rr_config() == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}


def test_external_edit_is_picked_up(isolated_config):
    """The alert sweep is a separate process; an out-of-band write must be seen."""
    rc.set_rr_config(rr_market_min=2.5)
    assert rc.min_rr() == 2.5
    import os, time
    isolated_config.write_text(json.dumps({"rr_market_min": 2.5, "rr_hi_rr": 3.0}), encoding="utf-8")
    os.utime(isolated_config, (time.time() + 10, time.time() + 10))   # ensure mtime moves
    assert rc.min_rr() == 2.5


def test_unwritable_path_raises_rather_than_corrupting_the_defaults(isolated_config, tmp_path, monkeypatch):
    """A write that cannot land must fail loudly, not leave the caller thinking it persisted."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("RR_CONFIG_PATH", str(blocker / "nested" / "rr.json"))
    with pytest.raises(OSError):
        rc.set_rr_config(rr_market_min=3.0)
    assert rc.get_rr_config()["rr_market_min"] == 2.0


# --- validation -------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [0.5, 10.5, -1, "abc", float("nan"), float("inf")])
def test_out_of_range_and_junk_values_are_rejected(bad):
    with pytest.raises(ValueError):
        rc.set_rr_config(rr_market_min=bad)
    assert rc.get_rr_config()["rr_market_min"] == 2.0, "a rejected value must not persist"


def test_boolean_is_accepted_as_one_because_json_owns_that_bug():
    """bool is a subclass of int; float(True) == 1.0 lands inside the bounds. Documented, not accidental."""
    assert rc.set_rr_config(rr_market_min=True)["rr_market_min"] == 1.0


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError, match="unknown R:R setting"):
        rc.set_rr_config(rr_stop_atr=0.3)


def test_hi_rr_cannot_sit_below_the_actionable_floor():
    """Otherwise nothing could ever be tagged HI-RR and the badge would be dead UI."""
    rc.set_rr_config(rr_market_min=6.0, rr_hi_rr=8.0)      # floor and tag move together
    assert rc.min_rr() == 6.0 and rc.hi_rr() == 8.0
    with pytest.raises(ValueError, match="cannot be below"):
        rc.set_rr_config(rr_hi_rr=5.0)
    # Raising the floor past the current tag is rejected too, even without mentioning hi_rr.
    with pytest.raises(ValueError, match="cannot be below"):
        rc.set_rr_config(rr_market_min=9.0)


def test_hi_rr_equal_to_the_floor_is_allowed():
    cfg = rc.set_rr_config(rr_market_min=4.0, rr_hi_rr=4.0)
    assert cfg["rr_hi_rr"] == 4.0


def test_none_means_leave_alone_not_reset():
    """One rule, not two: the API drops None keys before calling us, and _validate does the same."""
    rc.set_rr_config(rr_market_min=3.5, rr_hi_rr=4.0)
    assert rc._validate(rc.get_rr_config(), {"rr_market_min": None}) == {}
    assert rc.min_rr() == 3.5, "None must not silently revert to the default"


def test_unknown_key_is_rejected_even_when_its_value_is_none():
    """A typo'd field name used to be a silent no-op."""
    with pytest.raises(ValueError, match="unknown R:R setting"):
        rc.set_rr_config(rr_market_mn=None)


def test_values_are_rounded_to_two_decimals():
    assert rc.set_rr_config(rr_market_min=2.3456)["rr_market_min"] == 2.35


# --- the gate actually moves ----------------------------------------------------------------------

def test_lowering_the_floor_admits_a_row_that_was_below_it():
    o = _onset(rr=1.8)
    assert o.rr_at_market == pytest.approx(1.8)
    assert o.rr_ok is False, "1.8 sits under the 2.0 default"
    assert o.lane is None

    rc.set_rr_config(rr_market_min=1.5)
    o2 = _onset(rr=1.8)
    assert o2.rr_ok is True, "the same row is admitted once the floor moves"
    assert o2.lane == "rr_at_market_lane"


def test_raising_the_floor_excludes_a_row_that_was_above_it():
    o = _onset(rr=3.0)
    assert o.rr_ok is True
    rc.set_rr_config(rr_market_min=4.0, rr_hi_rr=5.0)
    o2 = _onset(rr=3.0)
    assert o2.rr_ok is False
    assert o2.lane is None


def test_lane_tier_follows_the_hi_rr_control():
    o = _onset(rr=6.0)
    assert o.lane == "rr_at_market_lane_strong"
    rc.set_rr_config(rr_market_min=2.0, rr_hi_rr=8.0)
    o2 = _onset(rr=6.0)
    assert o2.lane == "rr_at_market_lane", "6.0 is under the raised HI-RR threshold"


def test_entry_gate_failure_message_reflects_the_live_floor():
    rc.set_rr_config(rr_market_min=5.0, rr_hi_rr=6.0)
    _qualifies, fails = _onset(rr=3.0).entry_gate()
    assert any("RR@mkt" in f and "5.0" in f for f in fails), fails


def test_desk_uses_the_live_floor(monkeypatch):
    """The desk's needs-you gate and the ENTRY gate are the same number."""
    rc.set_rr_config(rr_market_min=3.0)
    assert rc.min_rr() == 3.0
    # The floor is read per request; there is no longer a second copy to drift.
    assert not hasattr(desk_mod, "RR_MKT_ACTIONABLE"), \
        "the desk must not keep its own mirror of a tunable threshold"
    assert "rr_config.min_rr()" in inspect.getsource(desk_mod.get_today)


def test_stop_atr_floor_has_exactly_one_definition():
    """It was 0.7 in three places; a measured constant with several copies is a drift risk."""
    from src.tracking import entry_risk_alerts

    assert rc.STOP_ATR_MIN == 0.7
    assert entry_risk_alerts.STOP_ATR_MIN == rc.STOP_ATR_MIN
    assert desk_mod.STOP_ATR_NOISE_FLOOR == rc.STOP_ATR_MIN


def test_the_triage_step_reads_the_same_thresholds():
    """data_window_filter had its own literals (2.0 / 3.0) and tiered the persisted lane at 3.0
    while every other surface showed 5.0."""
    from src.logic import data_window_filter as dwf

    src = inspect.getsource(dwf)
    assert "rr_pass_floor()" in src and "rr_strong_floor()" in src
    assert "rr_mkt >= RR_MKT_PASS" not in src, \
        "the decision must use the live accessor, not the import-time mirror"
    assert "rr_mkt >= RR_MKT_STRONG" not in src


# --- the measured guards do not move ---------------------------------------------------------------

def test_stop_atr_floor_is_not_tunable():
    from src.tracking.entry_risk_alerts import STOP_ATR_MIN

    assert STOP_ATR_MIN == 0.7
    with pytest.raises(ValueError):
        rc.set_rr_config(stop_atr_floor=0.2)
    # A 0.2-ATR stop stays flagged no matter where the R:R floor sits.
    rc.set_rr_config(rr_market_min=1.0)
    tight = _onset(rr=50.0, stop=99.9, atr=1.0)
    assert tight.stop_tight is True
    assert tight.stop_width_atr == pytest.approx(0.1)


def test_pb_is_still_required_for_the_entry_push_at_any_floor():
    """R:R tier is not a substitute for the PB funnel, at any setting."""
    rc.set_rr_config(rr_market_min=1.0)          # as permissive as the bounds allow
    o = _onset(rr=40.0)
    o.pb_funnel = False
    o.fade_long = False
    qualifies, fails = o.entry_gate()
    assert qualifies is False
    assert any("not PB funnel" in f for f in fails)


def test_ui_payload_declares_what_is_not_configurable():
    payload = rc.as_ui_payload()
    assert payload["defaults"] == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}
    assert payload["bounds"]["rr_market_min"] == {"min": 1.0, "max": 10.0}
    assert payload["overridden"] == {"rr_market_min": False, "rr_hi_rr": False}
    assert "stop_atr_floor" in payload["not_configurable"]
    assert "pb_required_for_entry_push" in payload["not_configurable"]


def test_overridden_flags_track_the_config():
    rc.set_rr_config(rr_market_min=2.5)
    ov = rc.as_ui_payload()["overridden"]
    assert ov["rr_market_min"] is True
    assert ov["rr_hi_rr"] is False


# --- API surface -----------------------------------------------------------------------------------

def test_get_endpoint_returns_the_ui_payload():
    out = desk_mod.get_rr_config_endpoint()
    assert out["values"] == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}
    assert out["labels"]["rr_market_min"]
    assert out["hints"]["rr_market_min"]


def test_post_endpoint_persists():
    out = desk_mod.set_rr_config_endpoint(desk_mod.RRConfigUpdate(rr_market_min=3.5, rr_hi_rr=4.0))
    assert out["updated"]["rr_market_min"] == 3.5
    assert rc.min_rr() == 3.5


def test_post_endpoint_ignores_omitted_fields():
    desk_mod.set_rr_config_endpoint(desk_mod.RRConfigUpdate(rr_hi_rr=9.0))
    desk_mod.set_rr_config_endpoint(desk_mod.RRConfigUpdate(rr_market_min=3.0))
    assert rc.get_rr_config() == {"rr_market_min": 3.0, "rr_hi_rr": 9.0}


def test_post_endpoint_rejects_bad_values_with_400():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        desk_mod.set_rr_config_endpoint(desk_mod.RRConfigUpdate(rr_market_min=99.0))
    assert exc.value.status_code == 400
    assert "between" in exc.value.detail


def test_reset_endpoint_restores_defaults():
    desk_mod.set_rr_config_endpoint(desk_mod.RRConfigUpdate(rr_market_min=4.0, rr_hi_rr=9.0))
    assert rc.get_rr_config() == {"rr_market_min": 4.0, "rr_hi_rr": 9.0}
    out = desk_mod.reset_rr_config_endpoint()
    assert out["updated"] == {"rr_market_min": 2.0, "rr_hi_rr": 3.0}
    assert rc.min_rr() == 2.0