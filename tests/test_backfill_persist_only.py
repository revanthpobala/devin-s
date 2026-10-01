"""
Tests for the persist-only backfill.

The dangerous failure mode is silent and irreversible: if the backfill claims an alert dated today,
the live poller will skip it (email-id dedupe) and that alert never runs the 0DTE gem. So the
date guard gets more test weight than the happy path.
"""

from __future__ import annotations

import pytest

from src.data.backfill_alerts import (
    ARCHIVED,
    _alert_date,
    run_backfill,
    select_backfill_batch,
    strip_verdict_fields,
)

TODAY = "2026-10-01"
CUTOFF = "2026-10-01"


def _a(day, symbol="X", **kw):
    a = {"symbol": symbol, "strategy": "Intraday", "action": "LONG",
         "timestamp": f"{day} 14:30:00", "alert_price": 100.0,
         "email_id": f"e-{symbol}-{day}", "message_id": f"m-{symbol}-{day}", "body": "{}"}
    a.update(kw)
    return a


# --- the date guard ---------------------------------------------------------------------------

def test_alerts_before_the_cutoff_are_persisted():
    keep, skip, bad = select_backfill_batch([_a("2026-09-30"), _a("2026-07-08")], CUTOFF, TODAY)
    assert len(keep) == 2
    assert not skip and not bad


def test_todays_alerts_are_never_claimed():
    """The irreversible mistake: claiming today's alerts skips their 0DTE gem run."""
    keep, left, bad = select_backfill_batch([_a(TODAY), _a(TODAY, symbol="Y")], CUTOFF, TODAY)
    assert keep == []
    assert len(left) == 2


def test_the_cutoff_boundary_is_exclusive_but_never_silent():
    """An alert on the cutoff date is not persisted -- and must still be reported, not dropped."""
    keep, left_to_live, bad = select_backfill_batch([_a("2026-09-30")], "2026-09-30", TODAY)
    assert keep == []
    assert len(left_to_live) == 1, "it belongs to the live path, so say so rather than lose it"
    assert bad == []


def test_every_input_lands_in_exactly_one_bucket():
    batch = [_a("2026-07-08", "OLD"), _a("2026-09-30", "EDGE"),
             _a(TODAY, "LIVE"), {"symbol": "NODATE"}]
    keep, left, bad = select_backfill_batch(batch, "2026-09-30", TODAY)
    total = len(keep) + len(left) + len(bad)
    assert total == len(batch), "an alert vanished between the buckets"
    assert [a["symbol"] for a in keep] == ["OLD"]


def test_undated_alerts_are_set_aside_not_written():
    a = _a("2026-09-30")
    a.pop("timestamp")
    keep, skip, bad = select_backfill_batch([a], CUTOFF, TODAY)
    assert keep == [] and skip == []
    assert len(bad) == 1, "an alert with no date must not be written with a guessed date"


def test_iso_dates_compare_as_strings_correctly():
    """Sep < Oct lexicographically. Getting this wrong silently drops or steals whole months."""
    keep, _, _ = select_backfill_batch([_a("2026-09-11"), _a("2026-10-01")], TODAY, TODAY)
    assert [a["timestamp"][:10] for a in keep] == ["2026-09-11"]


# --- no fabricated verdicts --------------------------------------------------------------------

def test_llm_verdict_fields_are_stripped():
    a = _a("2026-09-30", grade="A", score=95.0, llm_decision="TAKE",
           llm_playbook="buy calls", align="up", wrong_if="lose 20", act_now="yes", verdict="ENTER")
    clean = strip_verdict_fields(a)
    for field in ("grade", "score", "llm_decision", "llm_playbook",
                  "align", "wrong_if", "act_now", "verdict"):
        assert field not in clean, f"{field} survived; the row would claim a grade it never earned"
    assert clean["symbol"] == "X"
    assert clean["body"] == "{}", "raw_payload must survive so the row can be judged later"


def test_contemporaneous_fields_are_preserved():
    a = _a("2026-09-30", alert_price=123.4, market_price=123.0, setup="OVERSOLD")
    clean = strip_verdict_fields(a)
    assert clean["alert_price"] == 123.4
    assert clean["market_price"] == 123.0
    assert clean["setup"] == "OVERSOLD"


# --- the runner ------------------------------------------------------------------------------

class _FakeGmail:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def fetch_new_alerts(self, limit=500):
        self.calls.append(limit)
        return self.batches.pop(0) if self.batches else []


def test_runner_persists_history_and_stops(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm
    from src.tracking.suggestions_ledger import ensure_suggestions_schema

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    wm_path = tmp_path / "watch.db"
    monkeypatch.setattr(wm, "DB_PATH", wm_path)
    adb.init_db()
    wm.init_watch_db()

    batch = [_a("2026-09-30", "OLD"), _a(TODAY, "LIVE")]
    gmail = _FakeGmail([batch])
    report = run_backfill(gmail_client=gmail, before=TODAY)

    assert report["persisted"] == 1
    assert report["skipped_today"] == 1, "today's alert must be left to the live poller"

    with adb._get_connection() as conn:
        rows = conn.execute("SELECT symbol, date, grade, llm_decision, routing_stage, status "
                            "FROM alerts ORDER BY symbol").fetchall()
    got = {r["symbol"]: dict(r) for r in rows}
    assert set(got) == {"OLD"}, "only the historical alert was written"

    old = got["OLD"]
    assert old["grade"] is None and old["llm_decision"] is None
    assert old["routing_stage"] == ARCHIVED
    assert old["status"] == "INGESTED", "INGESTED, not PROCESSED: no gem ran"
    assert old["date"] == "2026-09-30"


def test_runner_is_idempotent_across_reruns(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()

    batch = [_a("2026-09-30", "OLD")]
    first = run_backfill(gmail_client=_FakeGmail([batch]), before=TODAY)
    second = run_backfill(gmail_client=_FakeGmail([batch]), before=TODAY)

    assert first["persisted"] == 1
    assert second["persisted"] == 0
    assert second["duplicates"] == 1

    with adb._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert n == 1, "re-running must not duplicate"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()

    report = run_backfill(gmail_client=_FakeGmail([[_a("2026-09-30"), _a("2026-07-08")]]),
                          dry_run=True, before=TODAY)
    assert report["persisted"] == 2
    with adb._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_max_cycles_stops_early_and_resumes(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()

    gmail = _FakeGmail([[_a("2026-09-30", "A")], [_a("2026-09-29", "B")], []])
    report = run_backfill(gmail_client=gmail, max_cycles=1, before=TODAY)
    assert report["persisted"] == 1
    assert len(gmail.calls) == 1, "must stop after one cycle"

    with adb._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_alert_date_tolerates_a_missing_or_short_timestamp():
    assert _alert_date({}) == ""
    assert _alert_date({"timestamp": "2026-09-30"}) == "2026-09-30"
    assert _alert_date({"timestamp": "bad"}) == ""


def test_only_rows_this_run_inserted_are_stamped(tmp_path, monkeypatch):
    """A blanket UPDATE races a live ingester and produces ARCHIVED rows carrying a verdict.

    Observed on 7 rows: the backfill stamped them, then the ingester's enrichment worker finished
    and wrote llm_decision -- a 2026-09-11 alert graded against today's tape.
    """
    import src.tracking.alert_db as adb
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "watch.db")
    adb.init_db()
    wm.init_watch_db()

    # A row the LIVE ingester already owns, still RECORDED and INGESTED.
    with adb._get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (message_id, email_id, date, timestamp, symbol, action, strategy, "
            "raw_payload, status, routing_stage, created_at) "
            "VALUES ('LIVE-OWNED', '99999', '2026-09-11', '2026-09-11 16:00:00', 'LIVE', "
            "'NEUTRAL', 'Daily', '{}', 'INGESTED', 'RECORDED', '2026-09-11T16:00:00')"
        )
        conn.commit()

    run_backfill(gmail_client=_FakeGmail([[_a("2026-09-30", "MINE")]]), before=TODAY)

    mine_mid = _a("2026-09-30", "MINE")["message_id"]
    with adb._get_connection() as conn:
        rows = {r["message_id"]: dict(r) for r in conn.execute(
            "SELECT message_id, routing_stage FROM alerts")}
    assert rows[mine_mid]["routing_stage"] == ARCHIVED, "a row we inserted must be stamped"
    assert rows["LIVE-OWNED"]["routing_stage"] == "RECORDED", \
        "the backfill must not stamp a row the live ingester owns"