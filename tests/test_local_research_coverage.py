"""
"Every alert must have a local research" as an enforced invariant, not an accident.

It WAS true, but only because nothing had failed: the enrichment worker caught an exception,
logged it, moved on, and left the row with a NULL llm_decision permanently. Nothing retried and
nothing reported it. These tests cover the retry and the OPS check that close that hole.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def alerts_db(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    adb.init_db()
    return adb


def _seed(adb, symbol, ts, decision=None, stage="RECORDED"):
    with adb._get_connection() as c:
        c.execute(
            "INSERT INTO alerts (message_id, email_id, date, timestamp, symbol, action, strategy, "
            "raw_payload, llm_decision, status, routing_stage, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"m-{symbol}", f"e-{symbol}", ts[:10], ts, symbol, "LONG", "Intraday",
             '{"ticker":"%s"}' % symbol, decision, "INGESTED", stage, ts),
        )
        c.commit()


# --- the OPS check -------------------------------------------------------------------------------

def test_full_coverage_is_healthy(alerts_db, monkeypatch):
    from src.tracking.ops_alerts import check_local_research_coverage

    _seed(alerts_db, "AAA", "2026-10-01 09:31:00", decision="TAKE")
    _seed(alerts_db, "BBB", "2026-10-01 09:32:00", decision="CUT")
    monkeypatch.setattr(alerts_db, "get_eastern_date_str", lambda *_a, **_k: "2026-10-01")
    monkeypatch.setattr(alerts_db, "get_eastern_now", lambda *_a, **_k: datetime(2026, 10, 1, 12, 0))
    broken, detail = check_local_research_coverage()
    assert broken is False
    assert "all 2 forward alert(s)" in detail


def test_a_missed_alert_is_reported(alerts_db, monkeypatch):
    from src.tracking.ops_alerts import check_local_research_coverage

    _seed(alerts_db, "AAA", "2026-10-01 09:31:00", decision="TAKE")
    _seed(alerts_db, "GAP", "2026-10-01 09:32:00", decision=None)
    monkeypatch.setattr(alerts_db, "get_eastern_date_str", lambda *_a, **_k: "2026-10-01")
    monkeypatch.setattr(alerts_db, "get_eastern_now", lambda *_a, **_k: datetime(2026, 10, 1, 12, 0))
    broken, detail = check_local_research_coverage()
    assert broken is True
    assert "1 of 2" in detail and "NO local research" in detail


def test_a_just_arrived_alert_is_not_a_failure(alerts_db, monkeypatch):
    """Thirty seconds old and not yet graded is the queue working, not a gap."""
    from src.tracking.ops_alerts import check_local_research_coverage

    _seed(alerts_db, "NEW", "2026-10-01 11:59:40", decision=None)
    monkeypatch.setattr(alerts_db, "get_eastern_date_str", lambda *_a, **_k: "2026-10-01")
    monkeypatch.setattr(alerts_db, "get_eastern_now", lambda *_a, **_k: datetime(2026, 10, 1, 12, 0))
    assert check_local_research_coverage(grace_minutes=20)[0] is False


def test_archived_history_is_not_a_coverage_failure(alerts_db, monkeypatch):
    """Backlog left alone is the design; it must not light up the OPS channel every cycle."""
    from src.tracking.ops_alerts import check_local_research_coverage

    _seed(alerts_db, "OLD", "2026-10-01 09:00:00", decision=None, stage="ARCHIVED")
    _seed(alerts_db, "NEW", "2026-10-01 10:00:00", decision="TAKE")
    monkeypatch.setattr(alerts_db, "get_eastern_date_str", lambda *_a, **_k: "2026-10-01")
    monkeypatch.setattr(alerts_db, "get_eastern_now", lambda *_a, **_k: datetime(2026, 10, 1, 12, 0))
    assert check_local_research_coverage()[0] is False


def test_the_check_runs_as_part_of_ops(alerts_db, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts, "check_schwab_token", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_llm_server", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_daily_artifacts_fresh", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_ingester_running", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_core_symbol_coverage", lambda _m=None: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_local_research_coverage", lambda: (True, "gap!"))
    report = ops_alerts.run_ops_checks(notify=False)
    assert report["local_research_coverage"]["broken"] is True
    assert report["ok"] is False


# --- the worker retry ------------------------------------------------------------------------------

def test_the_enrichment_queue_cannot_drop(monkeypatch):
    """An alert that is put on the queue must be processed. A bounded queue would block or drop."""
    import inspect

    import main

    assert inspect.getsource(main).count("queue.Queue()") >= 1
    # No maxsize anywhere: a bounded queue is exactly how a burst becomes a silent gap.
    src = inspect.getsource(main)
    assert "maxsize=" not in src, "the enrichment queue is bounded; a burst would be dropped"


def test_worker_retries_until_the_alert_has_a_verdict(alerts_db, monkeypatch):
    """The core guarantee: a transient failure does not leave the alert un-researched."""
    import main

    _seed(alerts_db, "AAA", "2026-10-01 09:31:00", decision=None)

    calls = {"n": 0}

    def flaky(_alert):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("LLM timeout")
        with alerts_db._get_connection() as c:
            c.execute("UPDATE alerts SET llm_decision='TAKE' WHERE symbol='AAA'")
            c.commit()

    monkeypatch.setattr(main, "process_alert_enrichment", flaky)
    monkeypatch.setattr(main, "_ENRICH_RETRY_BACKOFF_SECONDS", 0.0)

    stop = threading.Event()
    started = main.start_enrichment_workers(num_workers=1, stop_event=stop)
    main._enrichment_queue.put({"symbol": "AAA", "timestamp": "2026-10-01 09:31:00"})
    for _ in range(80):
        if calls["n"] >= 3:
            break
        threading.Event().wait(0.05)
    stop.set()
    for t in started:
        t.join(timeout=2)

    assert calls["n"] == 3, "should have retried twice before succeeding"
    with alerts_db._get_connection() as c:
        got = c.execute("SELECT llm_decision FROM alerts WHERE symbol='AAA'").fetchone()[0]
    assert got == "TAKE"


def test_worker_gives_up_loudly_after_the_retry_budget(alerts_db, monkeypatch):
    """Exhausting the retries must be visible, not just logged and forgotten."""
    import main

    _seed(alerts_db, "BAD", "2026-10-01 09:31:00", decision=None)

    calls = {"n": 0}

    def always_fails(_alert):
        calls["n"] += 1
        raise RuntimeError("LLM down")

    monkeypatch.setattr(main, "process_alert_enrichment", always_fails)
    monkeypatch.setattr(main, "_ENRICH_RETRY_BACKOFF_SECONDS", 0.0)

    stop = threading.Event()
    started = main.start_enrichment_workers(num_workers=1, stop_event=stop)
    main._enrichment_queue.put({"symbol": "BAD", "timestamp": "2026-10-01 09:31:00"})
    for _ in range(80):
        if calls["n"] >= main._ENRICH_RETRY_ATTEMPTS:
            break
        threading.Event().wait(0.05)
    stop.set()
    for t in started:
        t.join(timeout=2)

    assert calls["n"] == main._ENRICH_RETRY_ATTEMPTS, "must use the whole retry budget"
    # Still ungraded -- which is exactly what the OPS coverage check now reports.
    with alerts_db._get_connection() as c:
        got = c.execute("SELECT llm_decision FROM alerts WHERE symbol='BAD'").fetchone()[0]
    assert got is None