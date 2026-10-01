"""
Historical alerts must be archived, never gem-enriched.

process_alert_enrichment pulls LIVE price (step 1) and LIVE news (step 2). Running the 0DTE gem over
an alert from July would grade a three-month-old setup against today's tape and write the verdict
as if contemporaneous -- and those rows feed intraday_signals, which the intraday scoreboard reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _patch_env(tmp_path, monkeypatch, today="2026-10-01"):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()
    monkeypatch.setattr(adb, "get_eastern_date_str", lambda *_a, **_k: today)


def _alert(day, symbol="ZZZ"):
    return {
        "symbol": symbol, "strategy": "Intraday", "action": "LONG", "side": "LONG",
        "alert_price": 100.0, "timestamp": f"{day} 14:30:00",
        "email_id": f"e-{symbol}", "message_id": f"m-{symbol}-{day}", "body": "{}",
    }


def test_historical_alert_is_archived_not_enriched(tmp_path, monkeypatch):
    import main
    import src.tracking.alert_db as adb

    _patch_env(tmp_path, monkeypatch)
    enqueued = []
    monkeypatch.setattr(main, "_enrichment_queue", type("Q", (), {"put": staticmethod(enqueued.append)}))

    assert main.ingest_alert_fast(_alert("2026-07-08", "OLD")) is True
    assert enqueued == [], "a July alert must not reach the enrichment worker / 0DTE gem"


def test_historical_alert_never_opens_a_position(tmp_path, monkeypatch):
    """PositionManager routing happens BEFORE the archive guard, so it must be gated too."""
    import main

    _patch_env(tmp_path, monkeypatch)
    routed = []
    monkeypatch.setattr(
        main, "_position_manager",
        type("PM", (), {"route_alert": staticmethod(lambda a: routed.append(a)),
                        "handle_exit_alert": staticmethod(lambda a: {"action": "NONE"})})(),
    )
    monkeypatch.setattr(main, "_enrichment_queue", type("Q", (), {"put": staticmethod(lambda a: None)}))

    main.ingest_alert_fast(_alert("2026-07-08", "OLD"))
    assert routed == [], "a July alert must not open a position today"


def test_todays_alert_is_enriched_normally(tmp_path, monkeypatch):
    """The archive guard must not touch the live path."""
    import main

    _patch_env(tmp_path, monkeypatch)
    enqueued = []
    monkeypatch.setattr(main, "_enrichment_queue", type("Q", (), {"put": staticmethod(enqueued.append)}))
    monkeypatch.setattr(
        main, "_position_manager",
        type("PM", (), {"route_alert": staticmethod(lambda a: None),
                        "handle_exit_alert": staticmethod(lambda a: {"action": "NONE"})})(),
    )

    assert main.ingest_alert_fast(_alert("2026-10-01", "LIVE")) is True
    assert len(enqueued) == 1, "today's alert must still run the 0DTE gem"


def test_yesterdays_alert_is_also_archived(tmp_path, monkeypatch):
    """Only TODAY is live. "Yesterday's alert at today's price" is the same fabrication in a smaller
    dose -- the position is gone and the session has rolled, so it must not be graded."""
    import main

    today = "2026-10-01"
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    _patch_env(tmp_path, monkeypatch, today=today)
    enqueued = []
    routed = []
    monkeypatch.setattr(main, "_enrichment_queue", type("Q", (), {"put": staticmethod(enqueued.append)}))
    monkeypatch.setattr(
        main, "_position_manager",
        type("PM", (), {"route_alert": staticmethod(lambda a: routed.append(a)),
                        "handle_exit_alert": staticmethod(lambda a: {"action": "NONE"})})(),
    )

    main.ingest_alert_fast(_alert(yesterday, "YDY"))
    assert enqueued == [], "a previous session must not be graded against today's tape"
    assert routed == []


def test_archive_marks_the_row_routing_stage_archived(tmp_path, monkeypatch):
    import main
    import src.tracking.alert_db as adb

    _patch_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_enrichment_queue", type("Q", (), {"put": staticmethod(lambda a: None)}))
    monkeypatch.setattr(
        main, "_position_manager",
        type("PM", (), {"route_alert": staticmethod(lambda a: None),
                        "handle_exit_alert": staticmethod(lambda a: {"action": "NONE"})})(),
    )

    main.ingest_alert_fast(_alert("2026-07-08", "OLD"))
    with adb._get_connection() as conn:
        rows = conn.execute("SELECT symbol, routing_stage, grade, llm_decision FROM alerts").fetchall()
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["routing_stage"] == "ARCHIVED"
    assert r["grade"] is None and r["llm_decision"] is None