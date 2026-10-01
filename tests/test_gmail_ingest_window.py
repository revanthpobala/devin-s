"""
The 60-message recency ceiling is gone.

It was a permanent, silent data-loss path: with a busy inbox and zero UNSEEN messages, anything
older than the newest 60 sender messages could not be reached by the poller at all. 2026-09-10
produced 72 intraday alerts on its own, so one day-long outage would have pushed 12 of them into
that blind spot permanently -- no error, no retry, no way back.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from src.clients.gmail_client import GmailClient


def _client(mock_mail):
    c = GmailClient.__new__(GmailClient)
    c.mail = mock_mail
    c.sender = "noreply@tradingview.com"
    return c


def _mail(all_ids, unseen_ids=()):
    """Fake IMAP session. search() answers both queries the client issues."""
    mail = MagicMock()

    def search(_charset, query):
        if query.startswith("(UNSEEN"):
            return "OK", [b" ".join(str(i).encode() for i in unseen_ids)]
        return "OK", [b" ".join(str(i).encode() for i in all_ids)]

    mail.search.side_effect = search
    mail.select.return_value = ("OK", [b"1"])
    return mail


# --- the ceiling itself ---------------------------------------------------------------------

def test_source_has_no_recency_ceiling():
    src = inspect.getsource(GmailClient.fetch_new_alerts)
    assert "recent_limit" not in src, "the recency ceiling is back"
    assert "all_ids[-" not in src, "the search result is being truncated again"
    assert "min(limit, 60)" not in src


def test_every_sender_message_is_a_candidate_not_just_the_newest():
    """The whole regression: ids 1..5000 must all reach the pre-filter."""
    all_ids = list(range(1, 5001))
    client = _client(_mail(all_ids))

    seen_ids = []

    def _processed(email_ids):
        seen_ids.extend(email_ids)
        return set()          # nothing ingested yet

    with patch("src.tracking.alert_db.get_processed_email_ids", _processed), \
         patch.object(GmailClient, "process_email", return_value=None):
        client.fetch_new_alerts(limit=10000)

    assert len(seen_ids) == 5000, "only part of the mailbox was offered to the pre-filter"
    assert "1" in seen_ids and "5000" in seen_ids


def test_a_message_beyond_the_old_60_is_still_reachable():
    """The exact case that was lost: a backlog deeper than the old window."""
    all_ids = list(range(1, 501))              # 500 messages, 0 UNSEEN
    client = _client(_mail(all_ids))
    fetched = []

    def _processed(email_ids):
        return {str(i) for i in range(440, 501)}   # everything except the oldest 60

    with patch("src.tracking.alert_db.get_processed_email_ids", _processed), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: fetched.append(
                          eid.decode() if isinstance(eid, bytes) else str(eid)) or None):
        client.fetch_new_alerts(limit=10000)

    got = {int(x) for x in fetched}
    assert 1 in got, "the oldest message was outside the old 60-window and was dropped"
    assert 60 in got
    assert not (got & set(range(440, 501))), "an already-ingested message must not be re-fetched"
    assert len(got) == 439, "ids 1..439 unprocessed, 440..500 already ingested"


def test_unseen_is_a_subset_and_does_not_double_order():
    """UNSEEN ⊆ FROM sender, so the dedupe collapses it. Ordering stays newest-first."""
    all_ids = list(range(1, 201))
    unseen = [1, 2, 3]
    client = _client(_mail(all_ids, unseen))
    order = []

    with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: order.append(
                          int(eid.decode() if isinstance(eid, bytes) else eid)) or None):
        client.fetch_new_alerts(limit=10000)

    assert order[:3] == [200, 199, 198], "newest first, so live alerts land at zero latency"
    assert sorted(order) == list(range(1, 201)), "every message exactly once, no duplicates"


# --- pre-filter still does the work ---------------------------------------------------------

def test_already_ingested_mail_is_never_downloaded():
    all_ids = list(range(1, 301))
    client = _client(_mail(all_ids))
    fetched = []
    with patch("src.tracking.alert_db.get_processed_email_ids",
               lambda ids: {i for i in ids if int(i) <= 290}), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: fetched.append(eid) or None):
        client.fetch_new_alerts(limit=10000)

    assert len(fetched) == 10, "only the 10 genuinely-new messages should be downloaded"
    assert all(int(e) > 290 for e in fetched)


# --- the per-cycle budget ---------------------------------------------------------------------

def test_budget_caps_bodies_but_not_detection():
    """A backlog is drained across cycles rather than dropped, and newest goes first."""
    all_ids = list(range(1, 101))
    client = _client(_mail(all_ids))
    fetched = []
    with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: fetched.append(eid) or None):
        client.fetch_new_alerts(limit=25)

    assert len(fetched) == 25
    assert [int(e) for e in fetched[:3]] == [100, 99, 98], "newest first"


def test_budget_is_not_applied_when_under_the_limit():
    all_ids = list(range(1, 41))
    client = _client(_mail(all_ids))
    fetched = []
    with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: fetched.append(eid) or None):
        client.fetch_new_alerts(limit=500)
    assert len(fetched) == 40


def test_backlog_is_logged_not_silently_starved(caplog):
    import logging

    all_ids = list(range(1, 301))
    client = _client(_mail(all_ids))
    with caplog.at_level(logging.WARNING):
        with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()), \
             patch.object(GmailClient, "process_email", return_value=None):
            client.fetch_new_alerts(limit=100)
    assert any("deferring" in r.message for r in caplog.records), \
        "a deferred backlog must be visible; silence is how the old ceiling hid"


# --- regressions ---------------------------------------------------------------------------

def test_empty_mailbox_is_handled():
    client = _client(_mail([]))
    with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()):
        assert client.fetch_new_alerts() == []


def test_malformed_alerts_are_skipped_without_stopping_the_sweep():
    all_ids = list(range(1, 6))
    client = _client(_mail(all_ids))
    seen = []

    def _parse(eid):
        seen.append(eid)
        return {"symbol": "X"} if int(eid) != 3 else None

    with patch("src.tracking.alert_db.get_processed_email_ids", lambda ids: set()), \
         patch.object(GmailClient, "process_email",
                      lambda _self, eid: _parse(eid)):
        out = client.fetch_new_alerts(limit=100)

    assert len(seen) == 5, "one unparseable message must not abort the rest"
    assert len(out) == 4


def test_connect_failure_returns_empty_instead_of_raising():
    c = GmailClient.__new__(GmailClient)
    c.mail = None
    c.sender = "x"
    with patch.object(GmailClient, "connect", return_value=False):
        assert c.fetch_new_alerts() == []