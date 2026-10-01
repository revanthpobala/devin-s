"""
INVARIANT: an alert that is not from the current Eastern date is never graded automatically.

Three separate paths can call evaluate_alert_payload. Each was found with the same defect, and the
third is an always-on background daemon, so it was silently re-grading the archived backlog against
the live tape:

  1. alert_evaluator.evaluate_batch_pending  -- pending filter had no date term
  2. alert_evaluator.evaluate_alert_payload  -- reachable directly from a UI route (manual)
  3. auto_triage_daemon._process_batch       -- ORDER BY today-first but never FILTERED to today

The rule under test: forward-looking alerts are graded; historical ones are left alone. `raw_payload`
is preserved on the archived rows so they can still be judged later, deliberately.
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()

    today = adb.get_eastern_date_str()
    rows = [
        ("TODAY-PENDING", today, None, "RECORDED"),
        ("OLD-PENDING", "2026-07-08", None, "RECORDED"),
        ("OLD-ARCHIVED", "2026-07-08", None, "ARCHIVED"),
    ]
    with adb._get_connection() as c:
        for mid, d, dec, stage in rows:
            c.execute(
                "INSERT INTO alerts (message_id, email_id, date, timestamp, symbol, action, "
                "strategy, raw_payload, llm_decision, status, routing_stage, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, mid, d, f"{d} 14:30:00", mid, "NEUTRAL", "Daily", "{}",
                 dec, "INGESTED", stage, f"{d}T14:30:00"),
            )
        c.commit()
    return today


# --- path 1: the batch evaluator --------------------------------------------------------------

def test_batch_evaluator_touches_today_only(seeded, monkeypatch):
    import src.tracking.alert_evaluator as ae

    seen = []
    monkeypatch.setattr(ae, "evaluate_alert_payload",
                        lambda a, use_tools=False: seen.append(a["message_id"]) or {})
    ae.evaluate_batch_pending(limit=50)
    assert seen == ["TODAY-PENDING"]


def test_batch_evaluator_leaves_archived_alone_even_on_force_all(seeded, monkeypatch):
    import src.tracking.alert_evaluator as ae

    seen = []
    monkeypatch.setattr(ae, "evaluate_alert_payload",
                        lambda a, use_tools=False: seen.append(a["message_id"]) or {})
    ae.evaluate_batch_pending(limit=50, force_all=True)
    assert "OLD-ARCHIVED" not in seen


# --- path 3: the always-on daemon ---------------------------------------------------------------

def _daemon(monkeypatch):
    """Build a daemon instance with its network path stubbed.

    The daemon holds `evaluate_alert_payload` and `DB_PATH` as module-level references, so patching
    the source module is not enough -- and leaving the real one in place makes the test call
    yfinance and Alpaca for a fake ticker.
    """
    import threading

    import src.tracking.alert_evaluator as ae
    import src.tracking.auto_triage_daemon as atd

    seen = []
    monkeypatch.setattr(atd, "evaluate_alert_payload",
                        lambda a, use_tools=False: seen.append(a["message_id"]) or {})

    d = atd.AutoTriageDaemon.__new__(atd.AutoTriageDaemon)
    d.batch_size = 50
    d.poll_interval = 999
    d.total_triaged = 0
    d.is_processing = False
    d._stop_event = threading.Event()
    return d, seen, ae


def test_auto_triage_daemon_touches_today_only(seeded, monkeypatch):
    """This is the one that ran on its own, repeatedly, with no operator action."""
    daemon, seen, _ = _daemon(monkeypatch)
    daemon._process_batch()
    assert seen == ["TODAY-PENDING"], f"daemon graded history: {seen}"


def test_daemon_query_filters_on_date_not_just_orders_by_it():
    """The defect was ORDER BY today-first without a WHERE date term."""
    import src.tracking.auto_triage_daemon as atd

    src = inspect.getsource(atd.AutoTriageDaemon._process_batch)
    assert "WHERE date = ?" in src
    assert "ORDER BY CASE WHEN date" not in src, (
        "ordering by recency is not a filter; the daemon walked the whole table"
    )
    assert "ARCHIVED" in src


def test_daemon_uses_the_eastern_boundary_not_utc():
    """alerts.date is Eastern; strftime('now') is UTC, so they disagree near midnight ET."""
    import src.tracking.auto_triage_daemon as atd

    src = inspect.getsource(atd.AutoTriageDaemon._process_batch)
    assert "get_eastern_date_str" in src
    assert "strftime('%Y-%m-%d', 'now')" not in src


def test_daemon_resolves_its_db_path_at_call_time():
    """`from ... import DB_PATH` pinned the original file for the life of the module."""
    import src.tracking.auto_triage_daemon as atd

    src = inspect.getsource(atd)
    assert "from src.tracking.alert_db import DB_PATH, update_research_status" not in src, (
        "DB_PATH is captured by value again; the daemon can read a different alerts table "
        "than the rest of the pipeline writes to"
    )
    assert "_alerts_db()" in src


# --- the invariant, stated once -------------------------------------------------------------------

def test_no_automatic_path_can_select_a_historical_alert(seeded, monkeypatch):
    """Every path that runs WITHOUT an operator asking, run together, must touch only today.

    force_all is excluded deliberately: it is an explicit recovery flag with no callers, invoked
    by hand. "We do not regrade old ones" is about what happens on its own.
    """
    import src.tracking.alert_evaluator as ae

    seen = []
    monkeypatch.setattr(ae, "evaluate_alert_payload",
                        lambda a, use_tools=False: seen.append(a["message_id"]) or {})

    ae.evaluate_batch_pending(limit=50)          # the normal sweep
    ae.evaluate_batch_pending(limit=50, date_str=None)

    daemon, daemon_seen, _ = _daemon(monkeypatch)
    daemon._process_batch()

    assert set(seen) == {"TODAY-PENDING"}, f"history was graded: {sorted(set(seen))}"
    assert daemon_seen == ["TODAY-PENDING"], f"daemon graded history: {daemon_seen}"


def test_force_all_is_not_wired_into_anything_automatic():
    """It stays available for deliberate recovery, but nothing reaches it on its own."""
    import src.tracking.alert_evaluator as ae

    assert "force_all" in inspect.signature(ae.evaluate_batch_pending).parameters

    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    callers = []
    for p in list(root.glob("src/**/*.py")) + [root / "main.py", root / "run_ui.py"]:
        if p.name == "alert_evaluator.py" or "__pycache__" in str(p):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "force_all" in text:
            callers.append(p.name)
    assert not callers, f"force_all is now reachable from {callers}"


def test_archived_rows_keep_their_payload_for_a_deliberate_later_review(seeded):
    """Left alone is not erased: raw_payload survives so history stays judgeable on purpose."""
    import src.tracking.alert_db as adb

    with adb._get_connection() as c:
        c.row_factory = sqlite3.Row
        r = dict(c.execute(
            "SELECT raw_payload, grade, llm_decision, routing_stage "
            "FROM alerts WHERE message_id='OLD-ARCHIVED'").fetchone())
    assert r["raw_payload"] == "{}"
    assert r["grade"] is None and r["llm_decision"] is None
    assert r["routing_stage"] == "ARCHIVED"


def test_ingest_guard_prevents_history_reaching_the_queue_at_all(seeded, monkeypatch):
    """The live path never even enqueues a historical alert, so there is nothing to regrade."""
    import main

    routed, enqueued = [], []
    monkeypatch.setattr(main, "_enrichment_queue",
                        type("Q", (), {"put": staticmethod(enqueued.append)})())
    monkeypatch.setattr(
        main, "_position_manager",
        type("PM", (), {"route_alert": staticmethod(lambda a: routed.append(a)),
                        "handle_exit_alert": staticmethod(lambda a: {"action": "NONE"})})(),
    )

    main.ingest_alert_fast({
        "symbol": "OLDCO", "strategy": "Intraday", "action": "LONG", "side": "LONG",
        "alert_price": 10.0, "timestamp": "2026-07-08 14:30:00",
        "email_id": "e-old", "message_id": "m-old", "body": "{}",
    })
    assert enqueued == [], "a historical alert must never be queued for grading"
    assert routed == [], "nor open a position"