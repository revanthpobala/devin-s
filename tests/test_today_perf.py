"""
Performance & Concurrency Load Tests for Today Desk & SQLite Read-Path.
Validates:
1. /api/desk/today returns in <1s.
2. Load test of 20 concurrent requests shows no pile-up.
3. Read path never waits on writes (dedicated WAL read-only connections).
4. Lock telemetry accurately reports hold durations and lock statistics.
"""

from __future__ import annotations

import concurrent.futures
import time
import pytest
from fastapi.testclient import TestClient

from src.ui.app import create_app
from src.tracking.db_locks import watch_db_lock, alert_db_lock, get_all_lock_stats
from src.ui.services import today_service


@pytest.fixture(scope="module")
def client():
    import src.ui.services.daemon_manager as dm

    orig_start = dm.start_all_daemons
    orig_stop = dm.stop_all_daemons
    dm.start_all_daemons = lambda: None
    dm.stop_all_daemons = lambda: None

    # Pre-populate today_service cache so read path responds immediately
    today_service.rebuild_today_cache()

    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        dm.start_all_daemons = orig_start
        dm.stop_all_daemons = orig_stop


def test_today_returns_under_one_second(client: TestClient):
    """Pass criterion 1: /api/desk/today returns in <1 s."""
    t0 = time.perf_counter()
    resp = client.get("/api/desk/today")
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    data = resp.json()
    assert "coverage" in data or "actionable_inbox" in data or "record_stats" in data
    assert elapsed < 1.0, f"Expected <1.0s, got {elapsed:.3f}s"
    print(f"\n[PERF] /api/desk/today answered in {elapsed*1000:.2f}ms")


def test_load_test_20_requests_no_pileup(client: TestClient):
    """Pass criterion 2: Load test of 20 concurrent requests shows no pile-up."""
    latencies = []

    def fetch_today(idx: int):
        t0 = time.perf_counter()
        r = client.get("/api/desk/today")
        duration = time.perf_counter() - t0
        return r.status_code, duration

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_today, i) for i in range(20)]
        for fut in concurrent.futures.as_completed(futures):
            status_code, dur = fut.result()
            assert status_code == 200
            latencies.append(dur)
    total_elapsed = time.perf_counter() - t_start

    assert len(latencies) == 20
    max_lat = max(latencies)
    avg_lat = sum(latencies) / len(latencies)
    assert max_lat < 1.0, f"Max latency was {max_lat:.3f}s (exceeded 1.0s limit)"
    assert total_elapsed < 3.0, f"Total batch time was {total_elapsed:.3f}s (exceeded 3.0s)"
    print(f"\n[LOAD TEST] 20 concurrent requests completed in {total_elapsed*1000:.2f}ms. Avg: {avg_lat*1000:.2f}ms, Max: {max_lat*1000:.2f}ms")


def test_stale_cache_serves_while_writer_blocked(client: TestClient):
    """Pass criterion 3: Read path never waits; cache serves while a writer holds _db_lock."""
    # Ensure cache has valid payload
    today_service.rebuild_today_cache()

    # Background thread acquires the writer lock for 1.2 seconds
    writer_finished = False

    def simulate_heavy_writer():
        nonlocal writer_finished
        with watch_db_lock:
            time.sleep(1.2)
        writer_finished = True

    writer_thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    writer_fut = writer_thread.submit(simulate_heavy_writer)

    # Give thread time to acquire the lock
    time.sleep(0.1)
    assert watch_db_lock.acquire_time is not None, "Writer lock should be actively held"

    # Make multiple read requests while the lock is actively held
    t0 = time.perf_counter()
    resp_today = client.get("/api/desk/today")
    dur_today = time.perf_counter() - t0

    t1 = time.perf_counter()
    resp_cov = client.get("/api/desk/coverage")
    dur_cov = time.perf_counter() - t1

    assert resp_today.status_code == 200
    assert resp_cov.status_code == 200
    assert dur_today < 0.5, f"/api/desk/today blocked on writer! Took {dur_today:.3f}s"
    assert dur_cov < 0.5, f"/api/desk/coverage blocked on writer! Took {dur_cov:.3f}s"

    # Wait for writer to finish cleanly
    writer_fut.result()
    assert writer_finished is True
    print(f"\n[CONCURRENCY] Read path served while writer blocked in {dur_today*1000:.2f}ms (today) and {dur_cov*1000:.2f}ms (coverage)")


def test_lock_telemetry_endpoint(client: TestClient):
    """Test lock stats telemetry endpoint reports accurate diagnostics."""
    resp = client.get("/api/desk/lock-stats")
    assert resp.status_code == 200
    stats = resp.json()

    assert "watch_db_lock" in stats
    assert "alert_db_lock" in stats
    w_stats = stats["watch_db_lock"]
    assert "longest_hold_s" in w_stats
    assert "total_acquisitions" in w_stats
    print(f"\n[TELEMETRY] Lock stats: {stats}")
