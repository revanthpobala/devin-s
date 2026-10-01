"""
evaluate_batch_pending must not grade history against the live tape.

It fetches LIVE price and LIVE news. With no date term in its pending-filter it graded an entire
archived backlog, writing llm_decision onto rows deliberately archived with a NULL verdict -- a
2026-09-11 setup judged against the current tape and current headlines, stored as contemporaneous.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    import src.tracking.alert_db as adb
    import src.tracking.alert_evaluator as ae

    monkeypatch.setattr(adb, "DB_PATH", tmp_path / "alerts.db")
    adb.init_db()

    today = adb.get_eastern_date_str()
    rows = [("TODAY-ROW", today), ("OLD-ROW", "2026-07-08"), ("OTHER-OLD", "2026-08-31")]
    with adb._get_connection() as c:
        for mid, d in rows:
            c.execute(
                "INSERT INTO alerts (message_id, email_id, date, timestamp, symbol, action, "
                "strategy, raw_payload, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, mid, d, f"{d} 14:30:00", mid, "NEUTRAL", "Daily", "{}",
                 "INGESTED", f"{d}T14:30:00"),
            )
        c.commit()

    seen: list = []
    monkeypatch.setattr(ae, "evaluate_alert_payload",
                        lambda a, use_tools=False: seen.append(a["message_id"]) or {})
    return ae, seen, today


def test_default_scope_is_today_only(seeded):
    ae, seen, today = seeded
    out = ae.evaluate_batch_pending(limit=50)
    assert seen == ["TODAY-ROW"], "history must not be graded against the live tape"
    assert out["scope"] == today
    assert out["count"] == 1


def test_an_explicit_date_is_an_opt_in(seeded):
    ae, seen, _ = seeded
    seen.clear()
    ae.evaluate_batch_pending(limit=50, date_str="2026-07-08")
    assert seen == ["OLD-ROW"]


def test_force_all_keeps_its_recovery_meaning(seeded):
    """force_all is an operator recovery sweep: every date, verdict filter ignored.

    It must NOT be quietly repurposed into "all dates, pending only" -- that would break the tool
    someone reaches for when decisions are corrupted. Safety comes from the ARCHIVED exclusion.
    """
    ae, seen, _ = seeded
    import src.tracking.alert_db as adb

    with adb._get_connection() as c:
        c.execute("UPDATE alerts SET llm_decision='TAKE' WHERE message_id='TODAY-ROW'")
        c.commit()
    seen.clear()
    out = ae.evaluate_batch_pending(limit=50, force_all=True)
    assert sorted(seen) == ["OLD-ROW", "OTHER-OLD", "TODAY-ROW"], \
        "force_all must reach rows that already have a verdict"
    assert "ALL dates" in out["scope"], "a full-history run must announce itself"


def test_archived_rows_are_never_selected_even_by_force_all(seeded):
    """Defence in depth: ARCHIVED means deliberately not graded."""
    import src.tracking.alert_db as adb

    ae, seen, _ = seeded
    with adb._get_connection() as c:
        c.execute("UPDATE alerts SET routing_stage='ARCHIVED' WHERE message_id='OTHER-OLD'")
        c.commit()
    seen.clear()
    ae.evaluate_batch_pending(limit=50, force_all=True)
    assert "OTHER-OLD" not in seen, "an ARCHIVED row must not be graded even under force_all"


def test_query_carries_a_date_term(seeded):
    """Structural check: the pending filter must not be the whole WHERE clause."""
    src = inspect.getsource(seeded[0].evaluate_batch_pending)
    assert "WHERE date = ?" in src, "the default scope must be a single day"
    assert "target = date_str or get_eastern_date_str()" in src
    assert "routing_stage" in src, "ARCHIVED rows must be excluded structurally"
    assert "scope" in src, "the caller must be able to see what was evaluated"

    # force_all keeps an unbounded pending filter, so the ARCHIVED exclusion is what stops it
    # from re-fabricating the backlog. That clause must sit OUTSIDE the if/else.
    marker = "query += \" AND COALESCE(routing_stage, 'RECORDED') != 'ARCHIVED'\""
    assert marker in src, "the ARCHIVED guard must apply to force_all as well"
    assert src.count(marker) == 1, "and must not be conditional"
    assert "WHERE 1=1" in src, "force_all must keep its recovery semantics"


def test_archived_rows_are_never_selected_even_by_force_all(seeded):
    """Defence in depth: ARCHIVED means deliberately not graded."""
    import src.tracking.alert_db as adb

    ae, seen, _ = seeded
    with adb._get_connection() as c:
        c.execute("UPDATE alerts SET routing_stage='ARCHIVED' WHERE message_id='OTHER-OLD'")
        c.commit()
    seen.clear()
    ae.evaluate_batch_pending(limit=50, force_all=True)
    assert "OTHER-OLD" not in seen, "an ARCHIVED row must not be graded even under force_all"