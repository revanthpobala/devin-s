import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from src.tracking.alert_db import get_eastern_date_str
from src.tracking.auto_triage_daemon import (
    AutoTriageDaemon,
    get_auto_triage_status,
    get_pending_alerts_count,
    is_llm_server_online,
    start_auto_triage_daemon,
)


@pytest.fixture
def temp_alerts_db(tmp_path):
    db_file = tmp_path / "test_trading_alerts.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE alerts (
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
            routing_stage TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # Dated TODAY. The daemon only grades the current session: an unbounded pending query
    # walked the whole table and re-graded history against the live tape.
    today = get_eastern_date_str()
    # Insert 2 pending alerts and 1 evaluated alert
    cur.execute(
        """
        INSERT INTO alerts (message_id, date, timestamp, symbol, action, strategy, raw_payload, created_at, llm_decision)
        VALUES
        ('m1', ?, ?, 'AAPL', 'CALLS', 'Intraday', '{}', ?, NULL),
        ('m2', ?, ?, 'MSFT', 'PUTS', 'Intraday', '{}', ?, ''),
        ('m3', ?, ?, 'NVDA', 'CALLS', 'Intraday', '{}', ?, '🟢 GO (ENTER CALLS)')
        """,
        (today, f"{today} 10:00:00", today,
         today, f"{today} 10:05:00", today,
         today, f"{today} 10:10:00", today),
    )
    conn.commit()
    conn.close()
    return db_file


def test_get_pending_alerts_count(temp_alerts_db):
    with patch("src.tracking.alert_db.DB_PATH", temp_alerts_db):
        today = get_eastern_date_str()
        assert get_pending_alerts_count() == 2
        assert get_pending_alerts_count(date_str=today) == 2
        assert get_pending_alerts_count(date_str="2026-09-10") == 0


def test_auto_triage_daemon_processes_batch(temp_alerts_db):
    with patch("src.tracking.alert_db.DB_PATH", temp_alerts_db), \
         patch("src.tracking.auto_triage_daemon.is_llm_server_online", return_value=True), \
         patch("src.tracking.auto_triage_daemon.evaluate_alert_payload", return_value={"llm_decision": "🟢 GO"}) as mock_eval:
        
        daemon = AutoTriageDaemon(poll_interval=1, batch_size=5)
        # Manually invoke _process_batch to test exact logic
        daemon._process_batch()

        assert daemon.total_triaged == 2
        assert mock_eval.call_count == 2
        assert daemon.last_run_time is not None

        status = daemon.get_status()
        assert status["total_triaged"] == 2


def test_auto_triage_daemon_lifecycle(temp_alerts_db):
    with patch("src.tracking.alert_db.DB_PATH", temp_alerts_db), \
         patch("src.tracking.auto_triage_daemon.is_llm_server_online", return_value=False):
        
        daemon = AutoTriageDaemon(poll_interval=1, batch_size=2)
        daemon.start()
        assert daemon.is_alive()
        daemon.stop()
        daemon.join(timeout=2.0)
        assert not daemon.is_alive()
