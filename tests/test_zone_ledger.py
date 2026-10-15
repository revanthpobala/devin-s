import json

from src.tracking import zone_ledger as zl


def _plan(root, day, tk, lo, hi, stop, t1):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tk}_watch_levels.json").write_text(json.dumps({
        "ticker": tk, "side": "LONG",
        "shares_plan": {"entry_zone_low": lo, "entry_zone_high": hi, "tactical_stop": stop, "target_1": t1}}))


def _amzn(tmp_path):
    _plan(tmp_path, "2026-09-29", "AMZN", 245.0, 248.53, 240.0, 270.0)
    _plan(tmp_path, "2026-10-08", "AMZN", 250.0, 253.0, 243.5, 270.0)
    return zl.load_plans("2026-10-13", tmp_path)["AMZN"]


def test_older_zone_stays_tracked_beside_newer_plan(tmp_path):
    zones = zl.merge_zones(_amzn(tmp_path))
    assert [(z["low"], z["high"]) for z in zones] == [(245.0, 248.53), (250.0, 253.0)]


def test_touch_counts_distinct_days_and_rebound_fires_once(tmp_path):
    zones = zl.merge_zones(_amzn(tmp_path))
    st = {}
    for day, px in (("2026-10-09", 247.0), ("2026-10-09", 246.5), ("2026-10-10", 248.0), ("2026-10-12", 247.5)):
        ev = zl.evaluate("AMZN", px, day, zones, st)
        touch = [e for e in ev if e["type"] == "ZONE_TOUCH"][0]
    assert "Touch #3" in touch["message"] and "$245.00-$248.53" in touch["message"]
    assert "Newer plan zone $250.00-$253.00" in touch["message"]
    ev = zl.evaluate("AMZN", 260.0, "2026-10-13", zones, st)
    assert [e["type"] for e in ev] == ["ZONE_REBOUND"]
    assert zl.evaluate("AMZN", 260.5, "2026-10-13", zones, st) == []


def test_approach_and_no_touch_below_stop(tmp_path):
    zones = zl.merge_zones(_amzn(tmp_path))
    ev = zl.evaluate("AMZN", 250.5, "2026-10-13", zones[:1], {})
    assert [e["type"] for e in ev] == ["ZONE_APPROACH"] and "If it fills at the zone top $248.53" in ev[0]["message"]
    assert zl.evaluate("AMZN", 239.0, "2026-10-13", zones[:1], {}) == []


def test_rr_text_uses_at_market_price(tmp_path):
    zones = zl.merge_zones(_amzn(tmp_path))
    ev = zl.evaluate("AMZN", 247.0, "2026-10-13", zones[:1], {})
    assert "R:R at market 3.29" in ev[0]["message"] and "meets" in ev[0]["message"]


def test_run_cycle_emits_and_persists(tmp_path, monkeypatch):
    _amzn(tmp_path)
    monkeypatch.setattr(zl, "state_path", lambda: tmp_path / "zone_state.json")
    got = []
    n = zl.run_cycle({}, "2026-10-13", lambda *a: got.append(a) or True,
                     lambda syms: {"AMZN": 247.0}, tmp_path)
    assert n == 1 and got[0][1] == "ZONE_TOUCH"
    assert (tmp_path / "zone_state.json").exists()


def test_low_rr_and_tight_stop_touches_are_not_warnings(tmp_path):
    _plan(tmp_path, "2026-10-12", "TSLA", 370.4, 373.24, 368.0, 400.0)
    _plan(tmp_path, "2026-10-12", "DUOL", 139.0, 141.4, 132.5, 143.0)
    z = zl.merge_zones(zl.load_plans("2026-10-13", tmp_path)["TSLA"])
    ev = zl.evaluate("TSLA", 368.09, "2026-10-13", z, {})
    assert ev[0]["type"] == "ZONE_TOUCH_LOW_RR" and "No room" in ev[0]["message"]
    z = zl.merge_zones(zl.load_plans("2026-10-13", tmp_path)["DUOL"])
    ev = zl.evaluate("DUOL", 140.05, "2026-10-13", z, {})
    assert ev[0]["type"] == "ZONE_TOUCH_LOW_RR" and "below the floor" in ev[0]["message"]
    assert zl.evaluate("DUOL", 142.0, "2026-10-13", z, {}) == []
