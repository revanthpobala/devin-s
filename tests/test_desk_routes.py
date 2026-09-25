import pytest
import sqlite3
from fastapi.testclient import TestClient
import json

from src.ui.app import create_app
from src.tracking.watch_manager import _get_connection, _db_lock, init_watch_db
from src.tracking import suggestion_scorer
from src.tracking.alert_db import set_db_path as set_alert_db_path

@pytest.fixture
def client(tmp_path, monkeypatch):
    watch_db = tmp_path / "test_research_watch.db"
    alert_db = tmp_path / "test_trading_alerts.db"
    monkeypatch.setenv("RESEARCH_WATCH_DB", str(watch_db))
    monkeypatch.setenv("ALERT_DB_PATH", str(alert_db))
    from src.tracking import watch_manager
    watch_manager.DB_PATH = watch_db
    set_alert_db_path(str(alert_db))

    init_watch_db()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS suggestions (
                    id INTEGER PRIMARY KEY,
                    ticker TEXT,
                    date TEXT,
                    source TEXT,
                    setup_lane TEXT,
                    gate_status TEXT,
                    entry_low REAL,
                    entry_high REAL,
                    stop REAL,
                    target_1 REAL,
                    lane_prior_win REAL,
                    lane_prior_ev REAL,
                    rr_at_market_at_signal REAL,
                    fill_date TEXT,
                    fill_price REAL,
                    exit_date TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    bars_held INTEGER,
                    r_net REAL,
                    mae_r REAL,
                    notes TEXT,
                    scorer_version INTEGER DEFAULT 2,
                    kind TEXT DEFAULT 'NEW',
                    verdict TEXT DEFAULT 'ENTER',
                    entry_type TEXT DEFAULT 'LIMIT',
                    breakout_level REAL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rejected_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    date TEXT,
                    side TEXT,
                    plan_json TEXT,
                    reasons TEXT,
                    logged_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watch_targets (
                    ticker TEXT PRIMARY KEY,
                    date TEXT,
                    status TEXT,
                    verdict TEXT,
                    distance_to_entry_pct REAL,
                    updated_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_research_jobs (
                    job_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    pid INTEGER,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    log_file TEXT,
                    error_message TEXT,
                    target_date TEXT,
                    stage_detail TEXT
                )
            """)

            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1) VALUES (?, ?, 'test', 'PASS', 'RR_SETUP', 100, 110, 95, 130)", ('MSFT', today))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1) VALUES (?, ?, 'test', 'PASS', 'CODE20', 200, 210, 190, 240)", ('AAPL', today))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'CODE20', 900, 910, 880, 950, '2026-09-25', '2026-09-26', 1.5)", ('TSLA', '2026-09-24'))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'RR_SETUP', 600, 610, 580, 640, '2026-09-25', '2026-09-26', -1.0)", ('NVDA', '2026-09-24'))

            # Insert 55 closed suggestions to verify SQL pagination returns a full page of 50
            for i in range(55):
                cursor.execute(
                    "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'RR_SETUP', 100, 105, 95, 120, '2026-08-01', '2026-08-05', 1.0)",
                    (f"CL{i:02d}", "2026-08-01")
                )

            cursor.execute("INSERT INTO rejected_plans (ticker, date, side, reasons) VALUES ('REJ', '2026-09-24', 'LONG', 'Low R:R')", ())

            cursor.execute("INSERT INTO watch_targets (ticker, date, status, verdict, distance_to_entry_pct, updated_at) VALUES ('MSFT', ?, 'IN_ZONE', 'ENTER', 0.5, ?)", (today, today))
            cursor.execute("INSERT INTO watch_targets (ticker, date, status, verdict, distance_to_entry_pct, updated_at) VALUES ('AAPL', ?, 'STALKING', 'STALK', 3.0, ?)", (today, today))

            cursor.execute("INSERT INTO active_research_jobs (job_id, ticker, mode, stage, status, started_at, target_date) VALUES ('job-1', 'AAPL', 'full', 'DONE', 'COMPLETED', ?, ?)", (datetime.now().isoformat(), today))

            conn.commit()

    with sqlite3.connect(str(alert_db), timeout=30.0) as acon:
        ac = acon.cursor()
        ac.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                message_id TEXT PRIMARY KEY,
                email_id TEXT,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                strategy TEXT NOT NULL,
                alert_price REAL,
                market_price REAL,
                event TEXT,
                setup TEXT,
                verdict TEXT,
                act_now TEXT,
                plan TEXT,
                score TEXT,
                grade TEXT,
                align TEXT,
                wrong_if TEXT,
                raw_payload TEXT NOT NULL,
                status TEXT DEFAULT 'INGESTED',
                llm_decision TEXT,
                llm_playbook TEXT,
                created_at TEXT NOT NULL
            );
        """)
        ac.execute("""
            CREATE TABLE IF NOT EXISTS research_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                setup TEXT,
                status TEXT DEFAULT 'QUEUED',
                reason TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(date, symbol)
            );
        """)
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'AAPL', 'screener', 'Breakout', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'MSFT', 'screener', 'Pullback', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'GOOG', 'screener', 'Breakout', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))

        try:
            ac.execute("INSERT OR IGNORE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload) VALUES (?, ?, ?, 'MSFT', 'LONG', 'test', 'PASS', 'Test playbook', 'LOCAL_TRIAGE', ?, 'test raw payload')", (f"msg-msft-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            ac.execute("INSERT OR IGNORE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload) VALUES (?, ?, ?, 'AAPL', 'LONG', 'test', 'WATCH', 'Test playbook', 'LOCAL_TRIAGE', ?, 'test raw payload')", (f"msg-aapl-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            acon.commit()
        except Exception as e:
            print(f"Alert DB insert error: {e}")
            raise

    app = create_app()
    return TestClient(app)

def test_get_today(client):
    response = client.get("/api/desk/today")
    print('RESPONSE:', response.json())
    assert response.status_code == 200
    data = response.json()
    assert "actionable" in data
    assert "stalking" in data
    assert "found" in data
    assert "coverage" in data
    assert "needs_you" in data
    assert "watch" in data
    tickers_found = []
    for f in data["found"]:
        tickers_found.extend(f.get("tickers", []))
    assert "MSFT" in tickers_found or "AAPL" in tickers_found
    assert any(r.get("ticker") == "MSFT" for r in data.get("needs_you", []))
    assert any(r.get("ticker") == "AAPL" for r in data.get("watch", []))

def test_get_today_dedup(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    data = response.json()
    act_tickers = [r.get("ticker") for r in data.get("actionable", [])]
    stalk_tickers = [r.get("ticker") for r in data.get("stalking", [])]
    all_tickers = act_tickers + stalk_tickers
    assert len(all_tickers) == len(set(all_tickers))

def test_get_today_live_rr_null_when_below_stop(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    data = response.json()
    for a in data.get("actionable", []):
        if a.get("ticker") == "MSFT" and a.get("live_rr_flag") == "BELOW_STOP":
            assert a.get("live_rr") is None

def test_get_journal_include_rejected(client):
    response = client.get("/api/desk/journal?include_rejected=1")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "summary" in data
    assert any(r.get("row_type") == "rejected" for r in data["items"])

def test_get_journal_status_filter_paged(client):
    response = client.get("/api/desk/journal?status=CLOSED&page=1")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    # 57 total closed items inserted; page 1 must return a full page of 50
    assert len(data["items"]) == 50
    for r in data["items"]:
        assert r.get("derived_status") == "CLOSED"
        assert r.get("r_net") is not None

    # Page 2 returns the remaining 7 items
    response_p2 = client.get("/api/desk/journal?status=CLOSED&page=2")
    assert response_p2.status_code == 200
    assert len(response_p2.json()["items"]) == 7

def test_get_journal_paging_returns_full_page(client):
    response = client.get("/api/desk/journal?page=1")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "per_status" in data.get("summary", {})
    assert data["summary"]["per_status"]["CLOSED"] == 57

def test_get_record_scoped_keys(client):
    response = client.get("/api/desk/record")
    assert response.status_code == 200
    data = response.json()
    assert "total_scored" in data
    assert "sum_r" in data
    assert "mean_r" in data
    assert "median_r" in data
    assert "equity_curve" in data
    assert "by_lane" in data
    assert "first_score_eta" in data
    assert "scope" in data

def test_get_record_gated_zero_keys(client):
    from unittest.mock import patch
    with patch("src.ui.routes.desk._get_connection") as mock_conn:
        mock_cursor = mock_conn.return_value.__enter__.return_value.cursor.return_value
        mock_cursor.execute.return_value.fetchall.return_value = []
        mock_cursor.execute.return_value.fetchone.return_value = None
        response = client.get("/api/desk/record?from=2026-09-01&scope=gated")
    assert response.status_code == 200
    data = response.json()
    assert data["total_scored"] == 0
    assert data["sum_r"] == 0.0
    assert data["mean_r"] == 0.0
    assert data["median_r"] == 0.0

def test_get_record_all_legacy(client):
    response = client.get("/api/desk/record?scope=all")
    assert response.status_code == 200
    data = response.json()
    assert "equity_curve" in data
    assert "by_lane" in data
    assert "scope" in data
    assert data["scope"] == "all"
    assert data["total_scored"] == 57
    assert data["sum_r"] > 0
    assert "RR_SETUP" in data["by_lane"]
    assert "prior_win" in data["by_lane"]["RR_SETUP"]

def test_get_coverage(client):
    response = client.get("/api/desk/coverage")
    assert response.status_code == 200
    data = response.json()
    assert "alerts" in data
    assert "local_done" in data
    assert "local_pass" in data
    assert "deep_done" in data
    assert "deep_missing" in data
    assert isinstance(data["deep_missing"], list)
    assert "local_missing" in data
    assert isinstance(data["local_missing"], list)

def test_update_journal_notes(client):
    response = client.patch("/api/desk/journal/1", json={"notes": "new notes"})
    assert response.status_code == 200
    assert response.json().get("success") is True
