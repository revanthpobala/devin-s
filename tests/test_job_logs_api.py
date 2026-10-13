"""
Unit and integration tests for research job logs API endpoints.
Tests /api/jobs/{job_id}/logs, /api/jobs/{job_id}/log, /api/jobs/{job_id}/raw,
/api/jobs/{job_id}, and /api/logs/raw/{job_id}.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from src import config
from src.ui.app import create_app
from src.ui.state import LOGS_DIR, get_db


@pytest.fixture(scope="module")
def client():
    import src.ui.services.daemon_manager as dm

    orig_start = dm.start_all_daemons
    orig_stop = dm.stop_all_daemons
    dm.start_all_daemons = lambda: None
    dm.stop_all_daemons = lambda: None

    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        dm.start_all_daemons = orig_start
        dm.stop_all_daemons = orig_stop


@pytest.fixture
def sample_job_and_log():
    job_id = "test_job_api_logs_xyz123"
    ticker = "TESTTICKER"
    log_file = LOGS_DIR / f"{job_id}.log"
    lines = [f"[12:00:0{i}] Processing step {i} for {ticker}..." for i in range(10)]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO active_research_jobs
            (job_id, ticker, mode, stage, status, started_at, log_file, target_date, stage_detail, tier)
            VALUES (?, ?, 'full', 'DONE', 'COMPLETED', ?, ?, '2026-10-08', 'Completed research', 'FULL')
            """,
            (job_id, ticker, datetime.now(timezone.utc).isoformat(), str(log_file)),
        )
        conn.commit()

    try:
        yield job_id, ticker, log_file, lines
    finally:
        if log_file.exists():
            try:
                log_file.unlink()
            except Exception:
                pass
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM active_research_jobs WHERE job_id = ?", (job_id,))
            conn.commit()
def test_jobs_collection_includes_log_urls(client, sample_job_and_log):
    job_id, ticker, _, _ = sample_job_and_log

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()

    assert "jobs" in data
    matching = [j for j in data["jobs"] if j.get("job_id") == job_id]
    assert len(matching) == 1
    job_item = matching[0]
    assert job_item["log_url"] == f"/api/jobs/{job_id}/logs"
    assert job_item["raw_log_url"] == f"/api/jobs/{job_id}/logs?raw=true"


@pytest.fixture
def two_clock_jobs():
    """One job stamped UTC (the codebase's write convention) and one stamped local.

    After ~5pm local the two stamps fall on different dates, which is exactly the window
    where /api/jobs used to drop a job: it filtered today's panel on the LOCAL date while
    matching against a UTC-stored timestamp. Distinct tickers because /api/jobs dedupes
    each ticker to one row (RUNNING > QUEUED > latest finished).
    """
    utc_id, utc_ticker = "test_job_utc_clock_1", "TESTTICKERA"
    local_id, local_ticker = "test_job_local_clock_1", "TESTTICKERB"
    past_id, past_ticker = "test_job_past_dated_1", "TESTTICKERC"
    seeds = (
        (utc_id, utc_ticker, datetime.now(timezone.utc).isoformat(), "2026-10-08"),
        (local_id, local_ticker, datetime.now().isoformat(), "2026-10-08"),
        (past_id, past_ticker, "2020-01-02T15:00:00+00:00", "2020-01-02"),
    )
    with get_db() as conn:
        c = conn.cursor()
        for jid, tick, ts, tdate in seeds:
            c.execute(
                """
                INSERT OR REPLACE INTO active_research_jobs
                (job_id, ticker, mode, stage, status, started_at, log_file, target_date, stage_detail, tier)
                VALUES (?, ?, 'full', 'DONE', 'COMPLETED', ?, ?, ?, 'Completed research', 'FULL')
                """,
                (jid, tick, ts, "", tdate),
            )
        conn.commit()
    try:
        yield utc_id, local_id, past_id
    finally:
        with get_db() as conn:
            conn.cursor().execute(
                "DELETE FROM active_research_jobs WHERE job_id IN (?, ?, ?)", (utc_id, local_id, past_id)
            )
            conn.commit()


def test_jobs_collection_lists_both_clocks_and_keeps_past_filters_exact(client, two_clock_jobs):
    """A UTC-stamped and a locally-stamped job both list under today regardless of the
    local hour, while a genuinely past-dated job stays out of today's panel."""
    utc_id, local_id, past_id = two_clock_jobs

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    ids = [j["job_id"] for j in resp.json()["jobs"]]
    assert utc_id in ids, "the UTC-stamped job must list under today (evening-clock regression)"
    assert local_id in ids, "the locally-stamped job must list under today"
    assert past_id not in ids, "a past-dated job must not leak into today's panel"

    # The date picker offers today under both clocks as well.
    dates = resp.json().get("available_dates") or []
    assert datetime.now().strftime("%Y-%m-%d") in dates

    # A past-date filter stays exact: today's jobs do not leak into the past view.
    resp_past = client.get("/api/jobs?date=2020-01-02")
    past_ids = [j["job_id"] for j in resp_past.json()["jobs"]]
    assert past_id in past_ids
    assert utc_id not in past_ids
    assert local_id not in past_ids


def test_get_single_job_metadata(client, sample_job_and_log):
    job_id, ticker, log_file, _ = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["job_id"] == job_id
    assert data["ticker"] == ticker
    assert data["has_log"] is True
    assert data["log_file"] == str(log_file)
    assert data["log_size_bytes"] > 0
    assert data["log_url"] == f"/api/jobs/{job_id}/logs"


def test_get_job_logs_json_default(client, sample_job_and_log):
    job_id, ticker, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["job_id"] == job_id
    assert data["ticker"] == ticker
    assert data["total_lines"] == len(expected_lines)
    assert data["lines"] == expected_lines
    assert data["logs"] == expected_lines
    assert f"/api/jobs/{job_id}/logs?raw=true" in data["raw_url"]


def test_get_job_logs_alias_singular(client, sample_job_and_log):
    job_id, ticker, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["total_lines"] == len(expected_lines)


def test_get_job_logs_lines_tail(client, sample_job_and_log):
    job_id, _, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}/logs?lines=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["returned_lines"] == 3
    assert data["lines"] == expected_lines[-3:]

    resp_tail = client.get(f"/api/jobs/{job_id}/logs?tail=2")
    assert resp_tail.status_code == 200
    data_tail = resp_tail.json()
    assert data_tail["returned_lines"] == 2
    assert data_tail["lines"] == expected_lines[-2:]


def test_get_job_logs_raw_query_param(client, sample_job_and_log):
    job_id, _, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}/logs?raw=true")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert resp.text.splitlines() == expected_lines


def test_get_job_raw_endpoint(client, sample_job_and_log):
    job_id, _, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{job_id}/raw")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert resp.text.splitlines() == expected_lines


def test_status_raw_log_endpoint_delegation(client, sample_job_and_log):
    job_id, _, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/logs/raw/{job_id}")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert resp.text.splitlines() == expected_lines


def test_get_job_logs_by_ticker_lookup(client, sample_job_and_log):
    job_id, ticker, _, expected_lines = sample_job_and_log
    resp = client.get(f"/api/jobs/{ticker}/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["lines"] == expected_lines


def test_get_job_logs_queued_state(client):
    queued_jid = "test_job_queued_only_999"
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO active_research_jobs
            (job_id, ticker, mode, stage, status, started_at, target_date, tier)
            VALUES (?, 'QUEUEDTICKER', 'deep_only', 'QUEUED', 'QUEUED', ?, '2026-10-08', 'MID')
            """,
            (queued_jid, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    try:
        resp = client.get(f"/api/jobs/{queued_jid}/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_status"] == "QUEUED"
        assert data["lines"] == []
        assert "is currently QUEUED" in data["message"]

        resp_raw = client.get(f"/api/jobs/{queued_jid}/logs?raw=true")
        assert resp_raw.status_code == 200
        assert "is currently QUEUED" in resp_raw.text
    finally:
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM active_research_jobs WHERE job_id = ?", (queued_jid,))
            conn.commit()


def test_get_job_logs_not_found(client):
    resp = client.get("/api/jobs/nonexistent_phantom_job_12345/logs")
    assert resp.status_code == 404
