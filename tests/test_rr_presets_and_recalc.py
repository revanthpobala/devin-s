"""
Does changing the R:R require a recalculation, and do the presets behave?

The honest answer this file pins down: the desk and the alert gates are recomputed on read, so no
backfill is needed. Two things were NOT recomputed on read and had to be fixed:
  * level_validation carried its own hardcoded 2.0 at-market floor;
  * setup_lane is PERSISTED at triage time by the Pine's thresholds, so the displayed lane badge
    used to disagree with the bucket the row landed in after a UI change.
"""

from __future__ import annotations

import pytest

import src.tracking.rr_config as rc
from src.ui.routes import desk as desk_mod


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RR_CONFIG_PATH", str(tmp_path / "rr.json"))
    monkeypatch.setattr(rc, "_cache", {})
    monkeypatch.setattr(rc, "_cache_mtime", None)
    yield


# =====================================================================================
# Presets
# =====================================================================================

def test_presets_are_in_reward_to_risk_notation():
    """1:X is how it gets said out loud, so that is what the button says."""
    assert [p["label"] for p in rc.as_ui_payload()["presets"]] == ["1:1", "1:2", "1:3", "1:5"]


def test_the_default_preset_is_the_measured_one():
    """1:2 == rr_market_min 2.0, the floor the corpus supports. Shipping any other default
    would be shipping an unmeasured claim."""
    assert rc.PRESETS["1_2"]["rr_market_min"] == 2.0
    assert rc.active_preset() == "1_2", "a clean install must land on the measured preset"


def test_every_preset_is_internally_valid():
    for name, p in rc.PRESETS.items():
        cfg = rc.apply_preset(name)
        assert cfg["rr_market_min"] == p["rr_market_min"]
        assert cfg["rr_hi_rr"] == p["rr_hi_rr"]
        assert cfg["rr_hi_rr"] >= cfg["rr_market_min"], f"{name} would make HI-RR unreachable"
        lo, hi = rc.SPEC["rr_market_min"][1], rc.SPEC["rr_market_min"][2]
        assert lo <= p["rr_market_min"] <= hi, f"{name} is outside the settable bounds"


def test_presets_step_monotonically():
    """1:1 < 1:2 < 1:3 < 1:5 must actually be stricter in that order."""
    mins = [p["rr_market_min"] for p in rc.PRESETS.values()]
    assert mins == sorted(mins)
    assert len(set(mins)) == len(mins), "two presets with the same floor is a UI trap"


def test_preset_blurbs_carry_the_measured_tradeoff():
    """Raising the floor buys expectancy at the cost of hit rate. The button has to say so."""
    blurbs = {p["name"]: p["blurb"] for p in rc.as_ui_payload()["presets"]}
    assert "34%" in blurbs["1_2"] and "26%" in blurbs["1_3"]


def test_active_preset_is_none_for_custom_settings():
    rc.apply_preset("1_3")
    assert rc.active_preset() == "1_3"
    rc.set_rr_config(rr_market_min=3.4, rr_hi_rr=6.0)
    assert rc.active_preset() is None, "a near-miss is not a preset"


def test_exactly_one_preset_shows_active():
    payload = rc.as_ui_payload()
    assert sum(1 for p in payload["presets"] if p["active"]) <= 1
    for name in rc.PRESETS:
        rc.apply_preset(name)
        active = [p["name"] for p in rc.as_ui_payload()["presets"] if p["active"]]
        assert active == [name]


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="unknown preset"):
        rc.apply_preset("1:99")


def test_preset_endpoint_applies_and_reports():
    out = desk_mod.apply_rr_preset_endpoint("1_3")
    assert out["updated"] == {"rr_market_min": 3.0, "rr_hi_rr": 6.0}
    assert out["active_preset"] == "1_3"
    assert rc.min_rr() == 3.0


def test_preset_endpoint_rejects_unknown_with_400():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        desk_mod.apply_rr_preset_endpoint("nope")
    assert exc.value.status_code == 400


# =====================================================================================
# "Does it need recalculation?"
# =====================================================================================

def test_the_desk_route_has_no_response_cache():
    """get_today() must not memoise, or a dial change would not be visible until restart."""
    import inspect
    src = inspect.getsource(desk_mod.get_today)
    for token in ("_CACHE", "_cache", "lru_cache"):
        assert token not in src, f"get_today() now references {token}; a response cache would " \
                                 f"make an R:R change invisible until the process restarts"


@pytest.fixture
def seeded_desk():
    """A desk with controlled rows, so bucketing assertions do not depend on production data.

    These four tests originally called get_today() and asserted against whatever happened to be in
    data/research_watch.db. That is exactly the landmine the suite-hygiene checks exist to kill: the
    same assertions pass or fail depending on the day's scrape, and read nothing at all the morning
    after a date rollover.
    """
    import sqlite3
    from datetime import datetime

    import src.tracking.watch_manager as wm

    conn = sqlite3.connect(str(wm.DB_PATH))
    # Seed TODAY. A hardcoded date silently produced zero rows after a date rollover, which is how
    # these tests came to depend on production data in the first place.
    today = datetime.now().strftime("%Y-%m-%d")

    # The desk computes RR from last_price and the persisted stop/target, so the stops are derived
    # from the R:R each row is meant to have rather than hard-coded:
    #     rr = (130 - 100) / (100 - stop)
    SPOT, TARGET = 100.0, 130.0

    def stop_for(rr):
        return round(SPOT - (TARGET - SPOT) / rr, 4)

    rows = [
        # (ticker, lane, intended_rr, stop, atr)
        ("AAA", "RR_SETUP", 6.0, stop_for(6.0), 5.0),     # clears every preset floor
        ("BBB", "RR_SETUP", 1.6, stop_for(1.6), 5.0),     # between the 1:1 and 1:2 floors
        ("CCC", "RR_SETUP", 0.8, stop_for(0.8), 5.0),     # below the 1:1 floor
        ("DDD", "RR_SETUP", None, 100.0, 5.0),            # stop at spot -> no ratio at all
        ("EEE", "CODE20", 8.0, stop_for(8.0), 5.0),       # non-RR lane, keeps its identity
    ]
    for sym, lane, _rr, stop, atr in rows:
        conn.execute(
            """INSERT INTO suggestions (ticker, date, source, setup_lane, gate_status, verdict,
                   entry_low, entry_high, stop, target_1, atr_at_signal, spot_at_signal, created_at)
               VALUES (?, ?, 'swing_research', ?, 'PASS', 'ENTER', 99, 101, ?, ?, ?, 100.0, ?)""",
            (sym, today, lane, stop, TARGET, atr, f"{today}T10:00:00-06:00"),
        )
        # The desk resolves last_price / status through watch_targets; without this row the
        # suggestion never reaches a bucket.
        conn.execute(
            """INSERT OR REPLACE INTO watch_targets (ticker, date, status, verdict,
                   distance_to_entry_pct, last_price, updated_at)
               VALUES (?, ?, 'STALKING', 'STALK', 1.0, 100.0, ?)""",
            (sym, today, f"{today}T10:00:00-06:00"),
        )
    conn.commit()
    conn.close()

    # A desk row is a suggestion AND a triage alert: the bucket comes from the alert's
    # llm_decision, and a suggestion with no alert is reported as a coverage gap instead.
    import src.tracking.alert_db as alert_db

    with alert_db._get_connection() as ac:
        for sym, _lane, _rr, _stop, _atr in rows:
            ac.execute(
                """INSERT INTO alerts (message_id, date, timestamp, symbol, action, strategy,
                       llm_decision, score, setup, raw_payload, created_at)
                   VALUES (?, ?, ?, ?, 'LONG', 'swing', 'PASS', 90, 'RR_AT_MARKET', '{}', ?)""",
                (f"seed-{sym}", today, f"{today} 10:00:00", sym, f"{today}T10:00:00"),
            )
        ac.commit()

    return today


def _desk_rows(d, ticker):
    return [r for bucket in ("actionable", "needs_you", "stalking", "unmeasured")
            for r in d[bucket] if r.get("ticker") == ticker]


def test_desk_rebuckets_without_any_recalculation(seeded_desk):
    """The live check: move the floor, and the buckets change on the very next request."""
    seen = {}
    for rr in (2.0, 1.2, 1.0, 3.0):
        desk_mod.set_rr_config_endpoint(
            desk_mod.RRConfigUpdate(rr_market_min=rr, rr_hi_rr=rr + 3)
        )
        d = desk_mod.get_today()
        assert d["rr_gates"]["values"]["rr_market_min"] == rr
        seen[rr] = {r["ticker"] for r in d["needs_you"]}

    # Seeded R:R @ market: AAA 6.0, EEE 8.0, BBB 1.6, CCC 0.8, DDD none.
    assert {"AAA", "EEE"} <= seen[2.0], "6.0 and 8.0 clear a 2.0 floor"
    assert "BBB" not in seen[2.0] and "BBB" in seen[1.2], "1.6 is floor-dependent"
    assert "BBB" in seen[1.0]
    assert "CCC" not in seen[1.0], "0.8 is under the lowest preset floor of 1.0"
    assert "DDD" not in seen[1.0], "a row with no ratio is never actionable"
    assert seen[3.0] < seen[1.0], "raising the floor must shrink the work queue"
    desk_mod.reset_rr_config_endpoint()


def test_below_bar_rows_are_counted_separately(seeded_desk):
    """Raising the floor holds back measured setups. Calling that "unmeasured" would be a lie."""
    desk_mod.apply_rr_preset_endpoint("1_5")
    d = desk_mod.get_today()
    assert "below_bar_count" in d
    reasons = {r.get("unmeasured_reason") for r in d["unmeasured"]}
    assert reasons <= {"below_bar", "no_rr", "no_lane", None}

    tagged = [r for r in d["unmeasured"] if r.get("unmeasured_reason") == "below_bar"]
    assert tagged, "a measured row under a 5.0 floor must be tagged below_bar"
    assert len(tagged) == d["below_bar_count"]
    for r in tagged:
        assert r["rr_at_market"] is not None, "below_bar implies a measured number"
        assert r["rr_at_market"] < rc.min_rr()
    desk_mod.reset_rr_config_endpoint()


def test_a_measured_row_below_the_bar_is_marked_below_bar(seeded_desk):
    """The distinction only matters at a raised threshold, so prove it there."""
    for preset in ("1_2", "1_3", "1_5"):
        desk_mod.apply_rr_preset_endpoint(preset)
        d = desk_mod.get_today()
        for r in d["unmeasured"]:
            if r.get("unmeasured_reason") == "below_bar":
                assert r["rr_at_market"] is not None
                assert r["rr_at_market"] < rc.min_rr()
    desk_mod.reset_rr_config_endpoint()


def test_a_row_with_no_ratio_is_never_treated_as_below_bar(seeded_desk):
    """"Never measured" and "under your bar" are different facts and must not be conflated."""
    for preset in ("1_1", "1_2", "1_3", "1_5"):
        desk_mod.apply_rr_preset_endpoint(preset)
        for r in _desk_rows(desk_mod.get_today(), "DDD"):
            assert r.get("unmeasured_reason") != "below_bar"
            assert r["rr_at_market"] is None
    desk_mod.reset_rr_config_endpoint()


def test_code20_keeps_its_lane_and_its_ratio_at_every_floor(seeded_desk):
    """A non-RR lane's tier is a property of the setup, not of the dial."""
    for preset in ("1_1", "1_2", "1_3", "1_5"):
        desk_mod.apply_rr_preset_endpoint(preset)
        rows = _desk_rows(desk_mod.get_today(), "EEE")
        assert len(rows) == 1
        assert rows[0]["lane"] == "CODE20"
        assert rows[0]["rr_at_market"] == pytest.approx(8.0)
    desk_mod.reset_rr_config_endpoint()


def test_rr_at_market_is_recomputed_per_request_not_stored():
    """The ratio itself is derived from live price + persisted stop/target, never cached."""
    import inspect
    src = inspect.getsource(desk_mod.get_today)
    assert 'rr_at_market = round((target - last_px)' in src
    assert '"rr_at_market_source"' in src, "each row must say whether its R:R is signal or live"


def test_entry_gate_reads_the_dial_at_sweep_time():
    """Onsets are rebuilt inside the sweep, so no persisted gate state exists to invalidate."""
    import inspect
    from src.tracking import entry_risk_alerts as era

    assert "rr_config.min_rr()" in inspect.getsource(era.Onset.rr_ok.fget)
    assert "rr_config.hi_rr()" in inspect.getsource(era.Onset.__post_init__)


# =====================================================================================
# The two things that DID need fixing
# =====================================================================================

def test_level_validation_floor_follows_the_dial_not_a_literal():
    """It used to hardcode 2.0, so the desk could say 1:1 while the gate still cut at 2.0."""
    import inspect
    src = inspect.getsource(desk_mod)
    import src.logic.level_validation as lv
    lv_src = inspect.getsource(lv)

    assert "rr_at_market < 2.0" not in lv_src, "level_validation still hardcodes the old floor"
    assert "at_market_floor()" in lv_src


def test_level_validation_rejects_at_the_configured_floor():
    """An unmeasured lane with at-market R:R 1.5: rejected at the 2.0 default, accepted at 1.5."""
    import src.logic.level_validation as lv

    def reasons_at(floor):
        rc.set_rr_config(rr_market_min=floor, rr_hi_rr=floor + 3)
        plan = {
            "entry_low": 99.0, "entry_high": 101.0, "stop": 90.0,
            "target_1": 115.0, "target_2": 130.0, "entry_type": "MARKET",
            "setup_lane": "WATCH_SHADOW",
        }
        dw = {"close": 100.0, "stop_loss": 90.0, "target_1": 115.0, "atr14": 5.0}
        _ok, reasons = lv.validate_levels(plan=plan, dw=dw, side="LONG")
        return [r for r in reasons if "at-market R:R" in r]

    # at-market R:R = (115-100)/(100-90) = 1.5
    strict = reasons_at(2.0)
    assert len(strict) == 1 and "below 2.00 floor" in strict[0], strict

    permissive = reasons_at(1.5)
    assert permissive == [], "at exactly the floor the row must not be cut"


def test_level_validation_message_names_the_live_floor_not_a_literal():
    import inspect

    import src.logic.level_validation as lv

    lv_src = inspect.getsource(lv)
    assert "rr_at_market < 2.0" not in lv_src, "level_validation still hardcodes the old floor"
    assert "at_market_floor()" in lv_src


def test_displayed_lane_follows_the_dial():
    """setup_lane is persisted by the Pine at triage time; the badge must re-tier from live R:R."""
    rc.set_rr_config(rr_market_min=2.0, rr_hi_rr=5.0)
    assert desk_mod._live_lane_tier("RR_SETUP", 3.0) == "RR_SETUP"
    assert desk_mod._live_lane_tier("RR_SETUP", 6.0) == "RR_SETUP_STRONG"
    assert desk_mod._live_lane_tier("RR_SETUP", 1.5) is None, "below the floor: no lane"

    rc.set_rr_config(rr_market_min=1.0, rr_hi_rr=2.0)
    assert desk_mod._live_lane_tier("RR_SETUP", 1.5) == "RR_SETUP", "1:1 admits what 1:2 rejected"


def test_non_rr_lanes_keep_their_identity():
    """CODE20's tier is a property of the setup, not of the dial."""
    for lane in ("CODE20", "OVERSOLD", "RSI2", "WATCH_SHADOW"):
        assert desk_mod._live_lane_tier(lane, 99.0) == lane
        assert desk_mod._live_lane_tier(lane, None) == lane


def test_lane_without_a_measurable_rr_stays_as_persisted():
    assert desk_mod._live_lane_tier("RR_SETUP", None) == "RR_SETUP"


def test_prior_is_resolved_from_the_live_lane_not_the_persisted_one():
    """A row re-tiered up must show the STRONG lane's prior, not RR_SETUP's."""
    assert desk_mod._lane_priors("RR_SETUP", True) != desk_mod._lane_priors("RR_SETUP_STRONG", True)


# =====================================================================================
# Below-the-bar rows must not masquerade as unmeasured
# =====================================================================================

def test_below_bar_rows_are_counted_separately():
    """Raising the floor holds back measured setups. Calling that "unmeasured" would be a lie."""
    rc.apply_preset("1_2")
    d = desk_mod.get_today()
    assert "below_bar_count" in d
    reasons = {r.get("unmeasured_reason") for r in d["unmeasured"]}
    assert reasons <= {"below_bar", "no_rr", "no_lane", None}
    if d["below_bar_count"]:
        # Everything counted as below_bar must actually carry that reason, and vice versa.
        assert sum(1 for r in d["unmeasured"] if r.get("unmeasured_reason") == "below_bar") \
            == d["below_bar_count"]


def test_a_measured_row_below_the_bar_is_marked_below_bar():
    """The distinction only matters at a raised threshold, so prove it there."""
    for preset in ("1_2", "1_3", "1_5"):
        desk_mod.apply_rr_preset_endpoint(preset)
        d = desk_mod.get_today()
        for r in d["unmeasured"]:
            if r.get("unmeasured_reason") == "below_bar":
                assert r["rr_at_market"] is not None, "below_bar implies a measured number"
                assert r["rr_at_market"] < rc.min_rr()
    desk_mod.reset_rr_config_endpoint()


def test_default_preset_does_not_manufacture_below_bar_rows():
    rc.apply_preset("1_2")
    d = desk_mod.get_today()
    for r in d["unmeasured"]:
        if r.get("unmeasured_reason") == "below_bar":
            assert r["measured"] is True