"""
Tests for the notification channel and the OPS tier.

The channel is new infrastructure that previously did not exist (nothing in src/tracking/ could
reach a phone), so these cover the three properties the tier rules depend on: dedupe actually
dedupes, quiet hours actually silence, and OPS is exempt from both.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

import src.tracking.notify as notify_mod
from src.tracking.notify import (
    DIGEST_TIERS,
    PUSH_TIERS,
    is_digest_time,
    is_quiet_hours,
    notify,
    notify_ops,
    purge_dedupe,
)


MT_SUNDAY_3AM = datetime(2026, 10, 4, 3, 0)      # Sunday
MT_MONDAY_0930 = datetime(2026, 9, 28, 9, 30)    # Monday, mid-session
MT_MONDAY_0645 = datetime(2026, 9, 28, 6, 45)    # Monday, before the window
MT_MONDAY_1700 = datetime(2026, 9, 28, 17, 0)    # Monday, after the close
MT_MONDAY_1630 = datetime(2026, 9, 28, 16, 30)   # Monday, exactly at the boundary
MT_MONDAY_1629 = datetime(2026, 9, 28, 16, 29)   # Monday, last push minute


@pytest.fixture
def clean_channel(tmp_path, monkeypatch):
    """Dedicated dedupe ledger + no real transport + a clock parked inside the push window."""
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "dedupe.db")
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    # Park inside the window so tests exercise dedupe rather than the clock.
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: MT_MONDAY_0930)
    yield
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(
        notify_mod, "_dispatch",
        lambda tier, title, body: (sent.append((tier, title, body)) or ["capture"]),
    )
    return sent


# --- tier registry ----------------------------------------------------------------------------

def test_the_four_push_tiers_and_digest_are_the_only_valid_ones():
    assert set(PUSH_TIERS) == {"ENTRY", "RISK", "INCOME", "OPS"}
    assert set(DIGEST_TIERS) == {"DIGEST"}


def test_unknown_tier_is_refused(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    assert notify("MOONSHOT", "t", "b") is False
    assert sent == []


# --- quiet hours ------------------------------------------------------------------------------

@pytest.mark.parametrize("now,expected", [
    (MT_MONDAY_0645, True),      # before 07:00
    (MT_MONDAY_0930, False),     # in session
    (MT_MONDAY_1629, False),     # last minute of the push window
    (MT_MONDAY_1630, True),      # the boundary belongs to the digest, not to pushes
    (MT_MONDAY_1700, True),      # after the close
    (MT_SUNDAY_3AM, True),       # weekend
])
def test_quiet_hours_window(now, expected):
    assert is_quiet_hours(now) is expected


def test_the_push_window_and_the_digest_never_both_fire():
    """16:30 used to be quiet=False AND digest=True, so one sweep sent a push and the summary."""
    from src.tracking.notify import PUSH_END_HOUR, PUSH_END_MINUTE

    boundary = datetime(2026, 9, 28, PUSH_END_HOUR, PUSH_END_MINUTE)
    assert is_quiet_hours(boundary) is True
    assert is_digest_time(boundary) is True
    assert not (is_quiet_hours(boundary) is False and is_digest_time(boundary) is True)


def test_push_is_silenced_outside_the_window(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: MT_MONDAY_1700)
    assert notify("ENTRY", "GLW", "body") is False
    assert sent == [], "a 17:00 entry push is a phone call nobody wants"


def test_push_fires_inside_the_window(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    assert notify("ENTRY", "GLW", "body") is True
    assert len(sent) == 1
    assert sent[0][0] == "ENTRY"


def test_ops_ignores_quiet_hours(clean_channel, monkeypatch):
    """A dead machine at 3 AM must still be actionable in the morning."""
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: MT_SUNDAY_3AM)
    assert notify_ops("LLM down", "10 minutes unreachable") is True
    assert len(sent) == 1
    assert sent[0][0] == "OPS"


def test_digest_is_deferred_until_after_the_close(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: MT_MONDAY_0930)
    assert notify("DIGEST", "close summary", "body") is False
    assert sent == []
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: MT_MONDAY_1700)
    assert notify("DIGEST", "close summary", "body") is True
    assert len(sent) == 1


def test_is_digest_time_matches_the_close(clean_channel):
    assert is_digest_time(MT_MONDAY_0930) is False
    assert is_digest_time(MT_MONDAY_1700) is True
    assert is_digest_time(MT_MONDAY_1630) is True


# --- dedupe -----------------------------------------------------------------------------------

def test_same_dedupe_key_sends_once(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    key = "ENTRY:GLW:2026-09-28"
    assert notify("ENTRY", "GLW in zone", "body", dedupe_key=key) is True
    assert notify("ENTRY", "GLW in zone", "body", dedupe_key=key) is False
    assert len(sent) == 1, "the second call must not re-page"


def test_dedupe_defaults_to_tier_title_and_date(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    notify("ENTRY", "GLW in zone", "body")
    notify("ENTRY", "GLW in zone", "body")
    assert len(sent) == 1


def test_different_tier_same_ticker_is_not_a_dupe(clean_channel, monkeypatch):
    """ENTRY and RISK on the same name are different facts and must both get through."""
    sent = _capture(monkeypatch)
    notify("ENTRY", "GLW", "body", dedupe_key="ENTRY:GLW:2026-09-28")
    notify("RISK", "GLW", "body", dedupe_key="RISK:GLW:2026-09-28")
    assert len(sent) == 2


def test_a_new_day_is_a_new_alert(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    notify("ENTRY", "GLW", "body", dedupe_key="ENTRY:GLW:2026-09-28")
    notify("ENTRY", "GLW", "body", dedupe_key="ENTRY:GLW:2026-09-29")
    assert len(sent) == 2


def test_force_bypasses_dedupe(clean_channel, monkeypatch):
    sent = _capture(monkeypatch)
    key = "ENTRY:GLW:2026-09-28"
    notify("ENTRY", "GLW", "body", dedupe_key=key)
    notify("ENTRY", "GLW", "body", dedupe_key=key, force=True)
    assert len(sent) == 2


def test_history_is_recorded_and_readable(clean_channel):
    notify("OPS", "schwab expiring", "detail", dedupe_key="OPS:SCHWAB:2026-09-28", force=True)
    hist = notify_mod.sent_history()
    assert len(hist) == 1
    assert hist[0]["tier"] == "OPS"
    assert hist[0]["dedupe_key"] == "OPS:SCHWAB:2026-09-28"


def test_purge_drops_old_rows(clean_channel, monkeypatch):
    notify("OPS", "a", "b", dedupe_key="OPS:A", force=True)
    notify("OPS", "b", "b", dedupe_key="OPS:B", force=True)
    # Backdate past the cutoff rather than relying on wall-clock luck.
    conn = notify_mod._get_dedupe_db()
    conn.execute("UPDATE sent SET sent_at_ts = 0")
    conn.commit()
    assert purge_dedupe(older_than_days=30) == 2
    assert notify_mod.sent_history() == []
    # A fresh row survives the same purge.
    notify("OPS", "c", "c", dedupe_key="OPS:C", force=True)
    assert purge_dedupe(older_than_days=30) == 0
    assert len(notify_mod.sent_history()) == 1


def test_notify_survives_an_unavailable_ledger(clean_channel, monkeypatch):
    """No dedupe ledger must degrade to 'may repeat', never to 'crash the sweep'."""
    monkeypatch.setattr(notify_mod, "_get_dedupe_db", lambda: None)
    sent = _capture(monkeypatch)
    assert notify("ENTRY", "GLW", "body", dedupe_key="ENTRY:GLW:X") is True
    assert len(sent) == 1


# --- channels ---------------------------------------------------------------------------------

def test_ntfy_receives_the_tier_in_its_title(clean_channel, monkeypatch):
    posted = {}

    class _Resp:
        status_code = 200

    def _post(url, data=None, headers=None, timeout=None):
        posted.update(url=url, data=data, headers=headers or {})
        return _Resp()

    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: True)
    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setenv("NTFY_TOPIC", "revdesk")
    notify("ENTRY", "GLW in zone", "RR@mkt 3.1", dedupe_key="ENTRY:GLW:2026-09-28", force=True)
    assert posted["url"].endswith("/revdesk")
    assert "[ENTRY] GLW in zone" in posted["headers"]["Title"]
    assert b"ENTRY" in posted["data"]


def test_no_channel_configured_reports_failure(clean_channel):
    """True only means a channel accepted it. With none configured nothing went out, so False."""
    assert notify("OPS", "t", "b", dedupe_key="OPS:X") is False


def test_an_undelivered_alert_does_not_burn_its_dedupe_key(clean_channel, monkeypatch):
    """A broken channel at 09:00 must not silence that alert for the rest of the session."""
    assert notify("OPS", "later", "b", dedupe_key="OPS:LATER") is False
    assert notify_mod.sent_history() == [], "nothing was delivered, so nothing is recorded"

    # The channel comes back. The alert must still be deliverable.
    sent = _capture(monkeypatch)
    assert notify("OPS", "later", "b", dedupe_key="OPS:LATER") is True
    assert len(sent) == 1
    assert len(notify_mod.sent_history()) == 1


# --- OPS checks ---------------------------------------------------------------------------------

def test_schwab_token_warns_inside_24h(monkeypatch):
    import src.clients.schwab_client as sc
    from src.tracking import ops_alerts

    monkeypatch.setattr(sc, "get_schwab_token_status",
                        lambda: {"valid": True, "hours_remaining": 17.3})
    broken, detail = ops_alerts.check_schwab_token()
    assert broken is True
    assert "17.3h" in detail
    assert "setup_schwab.py" in detail


def test_schwab_token_quiet_when_healthy(monkeypatch):
    import src.clients.schwab_client as sc
    from src.tracking import ops_alerts

    monkeypatch.setattr(sc, "get_schwab_token_status",
                        lambda: {"valid": True, "hours_remaining": 96.0})
    assert ops_alerts.check_schwab_token()[0] is False


def test_schwab_token_reports_expiry_as_broken(monkeypatch):
    import src.clients.schwab_client as sc
    from src.tracking import ops_alerts

    monkeypatch.setattr(sc, "get_schwab_token_status",
                        lambda: {"valid": False, "hours_remaining": -3.0})
    broken, detail = ops_alerts.check_schwab_token()
    assert broken is True
    assert "EXPIRED" in detail


def test_llm_grace_period_absorbs_one_blip(tmp_path, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    monkeypatch.setattr("requests.get", _boom("connection refused"))
    assert ops_alerts.check_llm_server()[0] is False, "one failed probe is not an incident"


def test_llm_down_past_the_grace_period_is_broken(tmp_path, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    monkeypatch.setattr("requests.get", _boom("connection refused"))
    first, _ = ops_alerts.check_llm_server()
    state = tmp_path / "data" / "llm_health_state.json"
    state.write_text(state.read_text().replace('"first_failed_ts":', '"first_failed_ts":'), encoding="utf-8")

    # Age the recorded first-failure past the grace window.
    import json
    data = json.loads(state.read_text(encoding="utf-8"))
    data["first_failed_ts"] -= (ops_alerts.LLM_DOWN_GRACE_MINUTES + 1) * 60
    state.write_text(json.dumps(data), encoding="utf-8")

    broken, detail = ops_alerts.check_llm_server()
    assert broken is True
    assert "unreachable" in detail
    assert "triage are down" in detail


def test_llm_recovery_clears_state(tmp_path, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    monkeypatch.setattr("requests.get", _boom("down"))
    ops_alerts.check_llm_server()
    assert (tmp_path / "data" / "llm_health_state.json").exists()

    monkeypatch.setattr("requests.get", _ok(200))
    assert ops_alerts.check_llm_server()[0] is False
    assert not (tmp_path / "data" / "llm_health_state.json").exists()


def _boom(msg):
    def _get(*a, **k):
        raise ConnectionError(msg)
    return _get


def _ok(code):
    class _R:
        status_code = code
    def _get(*a, **k):
        return _R()
    return _get


def test_stale_artifacts_are_called_out(tmp_path, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)

    # Only a stale date present: the newest session artifact is 4 sessions old.
    stale_day = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    (raw / stale_day).mkdir()
    broken, detail = ops_alerts.check_daily_artifacts_fresh()
    assert broken is True
    assert "sessions old" in detail

    # Today's artifact present -> current.
    (raw / datetime.now().strftime("%Y-%m-%d")).mkdir()
    assert ops_alerts.check_daily_artifacts_fresh()[0] is False


def test_missing_data_raw_is_an_ops_failure(tmp_path, monkeypatch):
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    assert ops_alerts.check_daily_artifacts_fresh()[0] is True


def test_future_dated_fixture_does_not_mask_staleness(tmp_path, monkeypatch):
    """data/raw carries a 2029-01-01 fixture; it must not read as the newest session."""
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "2029-01-01").mkdir()
    (raw / "test_options").mkdir()
    stale_day = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    (raw / stale_day).mkdir()

    broken, detail = ops_alerts.check_daily_artifacts_fresh()
    assert broken is True
    assert "2029" not in detail
    assert "sessions old" in detail


def test_non_date_directories_are_ignored(tmp_path, monkeypatch):
    """data/raw/test_options must not read as the newest session."""
    from src.tracking import ops_alerts

    monkeypatch.setattr(ops_alerts.config, "BASE_DIR", tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "test_options").mkdir()
    (raw / "2029-01-01").mkdir()
    # Only fixtures are present -> nothing to be fresh against.
    assert ops_alerts.check_daily_artifacts_fresh()[0] is True


def _seed_coverage(suggested=(), triaged=()):
    """Seed the isolated DBs the way the pipeline would for a given day."""
    import sqlite3

    import src.tracking.alert_db as alert_db
    import src.tracking.watch_manager as wm
    from datetime import datetime, timedelta

    today = datetime.now().strftime("%Y-%m-%d")
    older = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")

    with wm._get_connection() as conn:
        for sym in suggested:
            conn.execute(
                "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane) "
                "VALUES (?, ?, 'test', 'PASS', 'RR_SETUP')",
                (sym, today if sym not in ("STALE",) else older),
            )
        conn.commit()
    with alert_db._get_connection() as conn:
        for sym in triaged:
            conn.execute(
                "INSERT INTO alerts (message_id, date, timestamp, symbol, action, strategy, "
                "llm_decision, raw_payload, created_at) "
                "VALUES (?, ?, ?, ?, 'LONG', 'swing', 'PASS', '{}', ?)",
                (f"cov-{sym}", today, f"{today} 10:00:00", sym, f"{today}T10:00:00"),
            )
        conn.commit()


def test_core_symbol_queued_but_never_triaged_is_reported():
    """The real defect: a core name in the working window with no triage decision at all."""
    from src.tracking.ops_alerts import check_core_symbol_coverage

    _seed_coverage(suggested=["SPY", "QQQ"], triaged=["QQQ"])
    broken, detail = check_core_symbol_coverage([])
    assert broken is True
    assert "SPY" in detail
    assert "QQQ" not in detail, "QQQ was triaged"
    assert "coverage has a hole" in detail


def test_triaged_core_symbol_is_never_a_gap():
    """Regression for the false positive that fired on all six names on first run.

    The check used to read the desk's `missing_symbols`, which compares a 21-day suggestion
    window against TODAY's alerts only. Every core name absent from today's inbox therefore read
    as untriaged, even with 6-54 triaged alerts and a live watch_target.
    """
    from src.tracking.ops_alerts import check_core_symbol_coverage

    _seed_coverage(
        suggested=["SPY", "QQQ", "AMZN", "WMT", "HOOD", "CRM"],
        triaged=["SPY", "QQQ", "AMZN", "WMT", "HOOD", "CRM"],
    )
    broken, detail = check_core_symbol_coverage([])
    assert broken is False, detail
    assert "all 6 core symbols have a triage decision" in detail


def test_a_core_symbol_triaged_on_an_older_day_still_counts_as_covered():
    """Being absent from TODAY's inbox is not a coverage hole."""
    from src.tracking.ops_alerts import check_core_symbol_coverage

    _seed_coverage(suggested=["SPY"], triaged=[])
    assert check_core_symbol_coverage([])[0] is True, "queued and never triaged -> a real gap"

    import src.tracking.alert_db as alert_db
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    with alert_db._get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (message_id, date, timestamp, symbol, action, strategy, "
            "llm_decision, raw_payload, created_at) "
            "VALUES ('cov-old', ?, ?, 'SPY', 'LONG', 'swing', 'PASS', '{}', ?)",
            (old, f"{old} 10:00:00", f"{old}T10:00:00"),
        )
        conn.commit()
    broken, detail = check_core_symbol_coverage([])
    assert broken is False, detail


def test_a_stale_suggestion_outside_the_window_is_not_a_gap():
    """Nothing in play means nothing to cover."""
    from src.tracking.ops_alerts import CORE_COVERAGE_WINDOW_DAYS, check_core_symbol_coverage

    _seed_coverage(suggested=["STALE"], triaged=[])
    assert check_core_symbol_coverage([])[0] is False
    assert CORE_COVERAGE_WINDOW_DAYS == 21


def test_only_non_core_missing_is_healthy():
    from src.tracking.ops_alerts import check_core_symbol_coverage

    assert check_core_symbol_coverage(["GLW", "SWKS", "QRVO"])[0] is False


def test_test_tickers_are_not_coverage_gaps():
    """GOODTICKER in missing_symbols is fixture noise, not an alert-coverage hole."""
    from src.tracking.ops_alerts import check_core_symbol_coverage

    broken, detail = check_core_symbol_coverage(["GOODTICKER"])
    assert broken is False
    assert "1 test row(s) ignored" in detail


# =====================================================================================
# The desk and the OPS tier must agree on what a coverage gap is
# =====================================================================================

def test_desk_reports_the_same_core_gaps_as_the_ops_check():
    """One definition. When the desk derived this from `missing_symbols` it claimed six gaps on
    live data where all six core names had 6-54 triaged alerts and live watch_targets."""
    from src.tracking.ops_alerts import core_coverage_gaps
    from src.ui.routes import desk

    gaps, covered = core_coverage_gaps()
    payload = desk.get_today()
    assert payload["missing_symbols_core_gaps"] == gaps
    assert payload["core_symbols_covered"] == covered
    assert payload["coverage"]["missing_symbols_core_gaps"] == len(gaps)
    assert payload["core_coverage_error"] is None


def test_a_failing_coverage_probe_is_reported_not_rendered_as_healthy(monkeypatch):
    """Swallowing a probe failure would turn a broken check into a board that looks fine."""
    from src.ui.routes import desk

    def _boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(desk, "core_coverage_gaps", _boom)
    payload = desk.get_today()
    assert payload["missing_symbols_core_gaps"] == []
    assert payload["core_coverage_error"], "a failed probe must be visible to the caller"
    assert "locked" in payload["core_coverage_error"]


def test_a_core_name_absent_from_todays_inbox_is_still_covered():
    """The specific false positive: not in today's alerts is not the same as never triaged."""
    import sqlite3
    from datetime import datetime, timedelta

    import src.tracking.alert_db as alert_db
    import src.tracking.watch_manager as wm
    from src.tracking.ops_alerts import core_coverage_gaps

    today = datetime.now().strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")

    with wm._get_connection() as conn:
        conn.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane) "
            "VALUES ('SPY', ?, 'test', 'PASS', 'RR_SETUP')", (today,))
        conn.commit()
    with alert_db._get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (message_id, date, timestamp, symbol, action, strategy, "
            "llm_decision, raw_payload, created_at) "
            "VALUES ('old-cov', ?, ?, 'SPY', 'LONG', 'swing', 'PASS', '{}', ?)",
            (old, f"{old} 10:00:00", f"{old}T10:00:00"))
        conn.commit()

    gaps, _ = core_coverage_gaps()
    assert "SPY" not in gaps, "triaged four days ago is still triaged"


# =====================================================================================
# Purge covers every table a fixture ticker can land in
# =====================================================================================

def test_purge_covers_every_table_a_fixture_ticker_lands_in():
    """GOODTICKER was in `suggestions` AND twice in `rejected_plans`; the original purge only
    touched two tables, so it would have left the rows that put it in the desk's list."""
    import inspect

    from src.tracking.ops_alerts import purge_test_rows

    src = inspect.getsource(purge_test_rows)
    for table in ("suggestions", "watch_targets", "rejected_plans"):
        assert table in src, f"purge_test_rows does not cover {table}"


def test_purge_dry_run_deletes_nothing():
    from src.tracking.ops_alerts import purge_test_rows

    import src.tracking.watch_manager as wm

    with wm._get_connection() as conn:
        conn.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane) "
            "VALUES ('GOODTICKER', date('now'), 'test', 'PASS', 'RR_SETUP')")
        conn.commit()

    out = purge_test_rows(dry_run=True)
    assert out["dry_run"] is True
    assert out["total_deleted"] == 0
    assert "GOODTICKER" in out["found"]["suggestions"]

    with wm._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE UPPER(ticker)='GOODTICKER'").fetchone()[0]
    assert n == 1, "a dry run must not delete"


def test_purge_removes_fixture_rows_when_confirmed():
    from src.tracking.ops_alerts import purge_test_rows
    import src.tracking.watch_manager as wm

    with wm._get_connection() as conn:
        conn.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane) "
            "VALUES ('GOODTICKER', date('now'), 'test', 'PASS', 'RR_SETUP')")
        conn.execute(
            "INSERT INTO rejected_plans (ticker, date, side, reasons) "
            "VALUES ('GOODTICKER', date('now'), 'LONG', 'fixture')")
        conn.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane) "
            "VALUES ('KEEPME', date('now'), 'test', 'PASS', 'RR_SETUP')")
        conn.commit()

    out = purge_test_rows(dry_run=False)
    assert out["total_deleted"] >= 2
    with wm._get_connection() as conn:
        for tbl in ("suggestions", "rejected_plans"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE UPPER(ticker)='GOODTICKER'").fetchone()[0]
            assert n == 0, f"{tbl} still holds GOODTICKER"
        kept = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE UPPER(ticker)='KEEPME'").fetchone()[0]
    assert kept == 1, "the purge must not touch real tickers"


def test_runner_pushes_one_alert_per_failure_set(clean_channel, monkeypatch):
    from src.tracking import ops_alerts

    sent = _capture(monkeypatch)
    monkeypatch.setattr(ops_alerts, "check_schwab_token", lambda: (True, "expires in 17.3h"))
    monkeypatch.setattr(ops_alerts, "check_llm_server", lambda: (False, "healthy"))
    monkeypatch.setattr(ops_alerts, "check_daily_artifacts_fresh", lambda: (False, "fresh"))
    monkeypatch.setattr(ops_alerts, "check_ingester_running", lambda: (False, "running"))
    monkeypatch.setattr(ops_alerts, "check_core_symbol_coverage", lambda _m=None: (False, "ok"))

    report = ops_alerts.run_ops_checks(notify=True)
    assert report["ok"] is False
    assert report["broken_count"] == 1
    assert len(sent) == 1
    assert sent[0][0] == "OPS"
    assert "schwab_token" in sent[0][2]


def test_runner_reports_clean_when_everything_passes(clean_channel, monkeypatch):
    from src.tracking import ops_alerts

    sent = _capture(monkeypatch)
    monkeypatch.setattr(ops_alerts, "check_schwab_token", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_llm_server", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_daily_artifacts_fresh", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_ingester_running", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_core_symbol_coverage", lambda _m=None: (False, "ok"))

    report = ops_alerts.run_ops_checks(notify=True)
    assert report["ok"] is True
    assert sent == []


def test_a_raising_check_is_reported_not_swallowed(clean_channel, monkeypatch):
    from src.tracking import ops_alerts

    def _boom_check():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(ops_alerts, "check_schwab_token", _boom_check)
    monkeypatch.setattr(ops_alerts, "check_llm_server", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_daily_artifacts_fresh", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_ingester_running", lambda: (False, "ok"))
    monkeypatch.setattr(ops_alerts, "check_core_symbol_coverage", lambda _m=None: (False, "ok"))

    report = ops_alerts.run_ops_checks(notify=False)
    assert report["schwab_token"]["broken"] is True
    assert "probe exploded" in report["schwab_token"]["detail"]