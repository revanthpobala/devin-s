"""
tests/test_desk_cache_service.py

Item 4: the desk stays fast and a repeat of the measured 120s regression is
diagnosable.

  1. A cached payload is served without running the builder again (the GET path
     never touches the DB when the background worker has the payload).
  2. A cold GET builds exactly once even when many requests arrive together and the
     background cycle is building the same payload — the in-flight lock, not a queue
     of duplicate builds behind each other's DB lock.
  3. Briefing and feedback are lazy: the 15s cycle skips them while their TTL holds.
  4. force_refresh wakes the worker with every payload due.
  5. Cache stats expose age / build time / lock state so a slow repeat is diagnosable.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.ui.services import today_service as ts


@pytest.fixture(autouse=True)
def _fresh_cache():
    with ts._CACHE_LOCK:
        for key in [k for k in ts._CACHE if not k.endswith(("_ts", "_build_s"))]:
            ts._CACHE[key] = None
        for key in [k for k in ts._CACHE if k.endswith(("_ts", "_build_s"))]:
            ts._CACHE[key] = 0.0
    ts._TRIGGER_EVENT.clear()
    ts._FORCE_NEXT_CYCLE = False
    yield
    ts._TRIGGER_EVENT.clear()
    ts._FORCE_NEXT_CYCLE = False


def _cache_val(name):
    with ts._CACHE_LOCK:
        return ts._CACHE.get(name), ts._CACHE.get(f"{name}_ts", 0.0)


def test_cached_payload_is_served_without_rebuilding():
    calls = {"n": 0}

    def _builder():
        calls["n"] += 1
        return {"payload": "today", "value": 1}

    first = ts.serve_payload("today", _builder)
    assert first["from_cache"] is False
    assert "value" in first and "payload" in first
    assert calls["n"] == 1

    second = ts.serve_payload("today", _builder)
    assert second["from_cache"] is True
    assert second["stale_s"] >= 0
    assert calls["n"] == 1, "a cached payload is never rebuilt by the GET path"


def test_cold_burst_builds_once(monkeypatch):
    """20 concurrent cold GETs + a background build = ONE build, no pile-up."""
    monkeypatch.setattr(ts, "_COLD_GET_WAIT_S", 5.0)
    # Event-driven hand-off, never sleep: the builder blocks until the burst is in
    # flight, and the burst waits until the builder is provably in flight.
    build_entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def _slow_builder():
        with calls_lock:
            calls["n"] += 1
        build_entered.set()
        assert release.wait(5.0)
        return {"value": 42}

    results = []
    errors = []

    def _getter():
        try:
            results.append(ts.serve_payload("coverage", _slow_builder))
        except Exception as e:  # pragma: no cover - defensive
            errors.append(e)

    # The background cycle starts the build first: the cold GETs must wait on it.
    worker = threading.Thread(target=ts._run_build, args=("coverage", _slow_builder), daemon=True)
    worker.start()
    assert build_entered.wait(5.0), "the background build never entered the builder"

    threads = [threading.Thread(target=_getter, daemon=True) for _ in range(20)]
    for t in threads:
        t.start()
    # The burst threads are now running and blocked on the in-flight build's lock;
    # releasing the builder lets exactly one payload land for all of them.
    release.set()
    for t in threads:
        t.join(timeout=10.0)
    worker.join(timeout=10.0)

    assert not errors
    assert len(results) == 20
    assert calls["n"] == 1, f"expected exactly one build, got {calls['n']}"
    for r in results:
        assert r.get("value") == 42
        assert r.get("from_cache") is True


def test_briefing_and_feedback_are_lazy(monkeypatch):
    """The 15s cycle rebuilds today, but skips briefing/feedback while their TTL holds."""
    # A cached payload only stays cached while the data it was built from is
    # unchanged: seed the current fingerprint so the TTL (not the fingerprint) is
    # what decides this test.
    fp = ts._data_fingerprint()
    ts._CACHE["morning_briefing"] = {"regime": "NEUTRAL"}
    ts._CACHE["morning_briefing_ts"] = time.time()
    ts._CACHE["morning_briefing_fp"] = fp
    ts._CACHE["feedback_loop"] = {"status": "ok"}
    ts._CACHE["feedback_loop_ts"] = time.time()
    ts._CACHE["feedback_loop_fp"] = fp
    ts._CACHE["today"] = None  # today is due; the lazy pair is not

    built = []

    def _rec(name, value):
        def _b():
            built.append(name)
            return value
        return _b

    monkeypatch.setattr(ts, "_build_today_payload_sync", _rec("today", {"value": 1}))
    monkeypatch.setattr(ts, "_build_coverage_payload_sync", _rec("coverage", {"value": 2}))
    monkeypatch.setattr(ts, "_build_missed_moves_sync", _rec("missed_moves", {"value": 3}))
    monkeypatch.setattr(ts, "_build_morning_briefing_sync", _rec("morning_briefing", {"regime": "RISK_ON"}))
    monkeypatch.setattr(ts, "_build_feedback_loop_sync", _rec("feedback_loop", {"status": "ok"}))

    ts._background_cycle()
    assert "today" in built
    assert "morning_briefing" not in built
    assert "feedback_loop" not in built

    # Expire the TTLs: now they are due again.
    ts._CACHE["morning_briefing_ts"] = time.time() - (ts._PAYLOAD_TTL_S["morning_briefing"] + 5)
    ts._CACHE["feedback_loop_ts"] = time.time() - (ts._PAYLOAD_TTL_S["feedback_loop"] + 5)
    ts._background_cycle()
    assert "morning_briefing" in built
    assert "feedback_loop" in built


def test_force_refresh_wakes_the_worker_with_every_payload():
    ts._CACHE["morning_briefing"] = {"regime": "NEUTRAL"}
    ts._CACHE["morning_briefing_ts"] = time.time()  # well inside its TTL

    ts.trigger_today_refresh()
    assert ts._FORCE_NEXT_CYCLE is True
    assert ts._TRIGGER_EVENT.is_set()


def test_cache_stats_are_diagnosable():
    ts._CACHE["today"] = {"value": 1}
    ts._CACHE["today_ts"] = time.time() - 30.0
    ts._CACHE["today_build_s"] = 7.5

    stats = ts.get_cache_stats()
    today_stats = stats["payloads"]["today"]
    assert today_stats["cached"] is True
    assert today_stats["stale_s"] >= 29.0
    assert today_stats["build_s"] == 7.5
    assert today_stats["ttl_s"] == ts._PAYLOAD_TTL_S["today"]
    assert today_stats["locked"] in (True, False)
    assert stats["payloads"]["morning_briefing"]["ttl_s"] == 300.0
    assert stats["payloads"]["feedback_loop"]["ttl_s"] == 120.0


def test_a_rewritten_db_invalidates_the_cached_payload():
    """A payload built from DB A is never served for DB B: the cache is fingerprinted
    on the session date plus each DB's mtime/size, so a swapped or rewritten DB
    rebuilds instead of serving the other database's numbers."""
    calls = {"n": 0}

    def _builder():
        calls["n"] += 1
        return {"value": calls["n"]}

    first = ts.serve_payload("coverage", _builder)
    assert first["from_cache"] is False and calls["n"] == 1

    # Same fingerprint: the cached payload is served.
    second = ts.serve_payload("coverage", _builder)
    assert second["from_cache"] is True and calls["n"] == 1

    # The data underneath changed (mtime/size): rebuild, never serve the old payload.
    with patch.object(ts, "_data_fingerprint", return_value=("2026-10-14", ("db", 2, 999))):
        third = ts.serve_payload("coverage", _builder)
    assert third["from_cache"] is False
    assert calls["n"] == 2
    assert third["value"] == 2


def test_dated_briefing_bypasses_cache(monkeypatch):
    """A dated briefing is a different day's file, not a refresh: never cached."""
    monkeypatch.setattr(ts, "_build_morning_briefing_sync", lambda: {"regime": "RISK_ON"})
    out = ts.get_morning_briefing_payload(date="2026-10-13")
    assert out["regime"] == "RISK_ON"
    assert out["from_cache"] is False
    with ts._CACHE_LOCK:
        assert ts._CACHE.get("morning_briefing") is None, "the dated path must not poison the cache"


def test_warm_start_serves_saved_payload_until_first_build(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_WARM_PATH", tmp_path / "warm.json")
    monkeypatch.setattr(ts, "_WARM_LAST_WRITE", 0.0)
    ts.serve_payload("today", lambda: {"value": 1})
    assert (tmp_path / "warm.json").exists(), "a good build is saved"

    with ts._CACHE_LOCK:
        for key in [k for k in ts._CACHE if k.startswith("today")]:
            ts._CACHE[key] = None
    ts._load_warm_start()

    calls = {"n": 0}

    def _builder():
        calls["n"] += 1
        return {"value": 2}

    warm = ts.serve_payload("today", _builder)
    assert warm["warm_start"] is True and warm["value"] == 1
    assert calls["n"] == 0, "the saved payload is served without building"

    fresh = ts.serve_payload("today", _builder, force_refresh=True)
    assert fresh["value"] == 2 and "warm_start" not in fresh
    assert ts.serve_payload("today", _builder).get("warm_start") is None


def test_warm_start_ignores_stale_or_other_session(tmp_path, monkeypatch):
    path = tmp_path / "warm.json"
    monkeypatch.setattr(ts, "_WARM_PATH", path)
    import json

    path.write_text(json.dumps({"ts": time.time() - ts._WARM_MAX_AGE_S - 10, "key": ts._warm_key(), "payload": {"value": 1}}))
    ts._load_warm_start()
    assert _cache_val("today")[0] is None

    path.write_text(json.dumps({"ts": time.time(), "key": ["1999-01-01", []], "payload": {"value": 1}}))
    ts._load_warm_start()
    assert _cache_val("today")[0] is None


def test_edge_scanner_regime_never_blocks_the_request(monkeypatch):
    from src.ui.routes import edge_scanner as es

    started = threading.Event()
    release = threading.Event()

    def _slow_fetch():
        started.set()
        release.wait(5.0)
        es._REGIME_STATE["val"] = "RISK_ON"
        es._REGIME_STATE["ts"] = time.time()
        es._REGIME_STATE["busy"] = False

    monkeypatch.setattr(es, "_fetch_regime", _slow_fetch)
    monkeypatch.setitem(es._REGIME_STATE, "ts", 0.0)
    monkeypatch.setitem(es._REGIME_STATE, "busy", False)
    monkeypatch.setitem(es._REGIME_STATE, "val", None)

    t0 = time.perf_counter()
    assert es._cached_regime() is None, "the first call returns the last known value, not the fetch"
    assert time.perf_counter() - t0 < 0.5
    assert started.wait(2.0)
    release.set()


def test_event_radar_looks_up_causes_together(monkeypatch):
    from src.ui.services import action_engine as ae

    monkeypatch.setattr(ae, "_holdings_by_underlying", lambda: {})

    import threading
    barrier = threading.Barrier(4, timeout=5.0)

    def _slow_cause(sym):
        barrier.wait()  # only passes if all 4 lookups run at the same time
        return f"cause-{sym}"

    monkeypatch.setattr(ae, "_mover_cause", _slow_cause)
    movers = [{"ticker": t, "drastic": True, "chg_pct": 5} for t in ("AAA", "BBB", "CCC", "DDD")]
    radar = ae.get_event_radar(movers, [])
    assert [m["cause"] for m in radar] == ["cause-AAA", "cause-BBB", "cause-CCC", "cause-DDD"]
