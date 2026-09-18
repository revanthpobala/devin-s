import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest

from src.tracking import alert_db


@pytest.fixture
def temp_db(tmp_path):
    test_db_path = tmp_path / "test_trading_alerts.db"
    with patch.object(alert_db, "DB_PATH", test_db_path):
        alert_db.init_alert_db()
        yield test_db_path


def test_alert_db_initialization_and_wal(temp_db):
    assert temp_db.exists()
    conn = sqlite3.connect(str(temp_db))
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"

    # Check tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cur.fetchall()}
    assert "alerts" in tables
    assert "research_queue" in tables
    assert "positions" in tables
    conn.close()


def test_record_alert_and_idempotency(temp_db):
    with patch.object(alert_db, "DB_PATH", temp_db):
        alert = {
            "message_id": "test-msg-12345@tradingview.com",
            "email_id": "1001",
            "timestamp": "2026-09-09 16:01:28",
            "symbol": "CAT",
            "action": "ENTER_PUTS",
            "strategy": "Intraday",
            "alert_price": 822.74,
            "event": "ENTRY",
            "body": '{"event":"ENTRY", "action":"ENTER_PUTS", "ticker":"CAT"}',
        }

        # 1. First insert should succeed with INGESTED status
        assert not alert_db.is_alert_processed(message_id="test-msg-12345@tradingview.com", require_completed=False)
        inserted = alert_db.record_alert(alert)
        assert inserted is True
        # Ingested, but not yet marked PROCESSED:
        assert alert_db.is_alert_processed(message_id="test-msg-12345@tradingview.com", require_completed=False)
        assert not alert_db.is_alert_processed(message_id="test-msg-12345@tradingview.com", require_completed=True)

        # Mark processed:
        alert_db.update_alert_llm(message_id="test-msg-12345@tradingview.com", llm_decision="TAKE", llm_playbook="Buy", status="PROCESSED")
        assert alert_db.is_alert_processed(message_id="test-msg-12345@tradingview.com", require_completed=True)

        # Also check by email_id and natural key:
        assert alert_db.is_alert_processed(email_id="1001")
        assert alert_db.is_alert_processed(symbol="CAT", action="ENTER_PUTS", timestamp="2026-09-09 16:01:28")

        # 2. Second insert with same message_id should be idempotent
        inserted_again = alert_db.record_alert(alert)
        assert inserted_again is False

        # 3. Verify record in database
        alerts = alert_db.get_alerts_for_date("2026-09-09")
        assert len(alerts) == 1
        assert alerts[0]["symbol"] == "CAT"
        assert alerts[0]["action"] == "ENTER_PUTS"
        assert alerts[0]["alert_price"] == 822.74
        assert alerts[0]["status"] == "PROCESSED"


def test_update_alert_llm(temp_db):
    with patch.object(alert_db, "DB_PATH", temp_db):
        alert = {
            "message_id": "llm-test-msg@tradingview.com",
            "timestamp": "2026-09-09 10:00:00",
            "symbol": "AMD",
            "action": "ENTER_CALLS",
            "strategy": "Intraday",
            "alert_price": 160.0,
        }
        alert_db.record_alert(alert)

        alert_db.update_alert_llm(
            message_id="llm-test-msg@tradingview.com",
            llm_decision="🟢 TAKE TRADE (8/10)",
            llm_playbook="Buy $160C 2026-09-18. Target $165. Stop $158.",
        )

        alerts = alert_db.get_alerts_for_date("2026-09-09")
        assert len(alerts) == 1
        assert alerts[0]["status"] == "PROCESSED"
        assert "TAKE TRADE" in alerts[0]["llm_decision"]
        assert "Target $165" in alerts[0]["llm_playbook"]


def test_research_queue_and_survivors_sync(temp_db, tmp_path):
    with patch.object(alert_db, "DB_PATH", temp_db):
        fake_base = tmp_path / "app_base"
        fake_base.mkdir()
        with patch.object(alert_db.config, "BASE_DIR", fake_base):
            date_str = "2026-09-09"

            # 1. Queue a screener candidate
            queued = alert_db.queue_for_research(
                symbol="MSFT",
                date_str=date_str,
                setup="Put-Sell Timing",
                source="screener_alert",
                reason="Screener trigger",
            )
            assert queued is True

            # 2. Queueing same ticker on same date should deduplicate
            queued_again = alert_db.queue_for_research(
                symbol="MSFT",
                date_str=date_str,
                setup="Put-Sell Timing",
            )
            assert queued_again is False

            # 3. Check SQLite research_queue
            items = alert_db.get_research_queue(date_str)
            assert len(items) == 1
            assert items[0]["symbol"] == "MSFT"
            assert items[0]["setup"] == "Put-Sell Timing"
            assert items[0]["status"] == "QUEUED"

            # 4. Check survivors.json file sync
            survivors_file = fake_base / "data" / "raw" / date_str / "survivors.json"
            assert survivors_file.exists()
            survivors_data = json.loads(survivors_file.read_text(encoding="utf-8"))
            assert len(survivors_data) == 1
            assert survivors_data[0]["Ticker"] == "MSFT"
            assert survivors_data[0]["screener_setup"] == "Put-Sell Timing"


def test_sync_position(temp_db):
    with patch.object(alert_db, "DB_PATH", temp_db):
        pos_data = {
            "side": "SHORT",
            "strategy": "Intraday",
            "entry_price": 822.74,
            "stop": 825.26,
            "target": 817.73,
            "alert_price": 822.74,
            "opened_at": "2026-09-09T16:01:36",
        }
        alert_db.sync_position("CAT", pos_data)

        # Query SQLite positions table
        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE symbol = 'CAT'").fetchone()
        conn.close()

        assert row is not None
        assert row["symbol"] == "CAT"
        assert row["side"] == "SHORT"
        assert row["entry_price"] == 822.74
        assert row["stop"] == 825.26
        assert row["status"] == "OPEN"

        # Now test close
        pos_data["closed_at"] = "2026-09-09T16:05:00"
        pos_data["exit_price"] = 827.15
        pos_data["exit_reason"] = "catastrophe stop"
        alert_db.sync_position("CAT", pos_data)

        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        row2 = conn.execute("SELECT * FROM positions WHERE symbol = 'CAT'").fetchone()
        conn.close()

        assert row2["status"] == "CLOSED"
        assert row2["exit_price"] == 827.15
        assert row2["exit_reason"] == "catastrophe stop"


def test_eastern_date_anchor():
    # Explicit ISO timestamp
    assert alert_db.get_eastern_date_str("2026-09-09 16:01:28") == "2026-09-09"
    assert alert_db.get_eastern_date_str("2026-09-10T09:30:00-04:00") == "2026-09-10"
    # UTC aware timestamp conversion: 2026-09-16T01:30:00+00:00 is 2026-09-15 21:30:00 EDT
    assert alert_db.get_eastern_date_str("2026-09-16T01:30:00Z") == "2026-09-15"
    # Fallback to current ET date
    now_et = alert_db.get_eastern_now().strftime("%Y-%m-%d")
    assert alert_db.get_eastern_date_str(None) == now_et


def test_schema_v2_trade_events_and_outbox(temp_db):
    with patch.object(alert_db, "DB_PATH", temp_db):
        # 1. Verify schema_version in schema_meta
        conn = sqlite3.connect(str(temp_db))
        ver = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
        conn.close()
        assert ver == "2"

        # 2. Record trade event
        row_id = alert_db.record_trade_event({
            "trade_id": "TRADE-CAT-001",
            "setup_id": "SETUP-CAT-20260915",
            "strategy_id": "rsi2_pullback",
            "strategy_version": "v2.0",
            "mode": "MODEL",
            "event_type": "ENTRY_FILL",
            "symbol": "CAT",
            "price": 822.50,
            "quantity": 100.0,
            "instrument_type": "EQUITY",
            "stop_level": 810.0,
            "target_1": 840.0,
            "details": {"source": "next_open"},
        })
        assert row_id > 0

        # Retrieve events
        events = alert_db.get_trade_events("TRADE-CAT-001")
        assert len(events) == 1
        assert events[0]["symbol"] == "CAT"
        assert events[0]["strategy_id"] == "rsi2_pullback"
        assert events[0]["event_type"] == "ENTRY_FILL"

        # 3. Test routing stage updates and get_unrouted_alerts
        alert = {
            "message_id": "outbox-test-1",
            "email_id": "2001",
            "timestamp": "2026-09-15 09:30:00",
            "symbol": "AAPL",
            "action": "CALLS",
            "strategy": "Intraday",
            "alert_price": 225.0,
        }
        alert_db.record_alert(alert)

        # Should be unrouted
        unrouted = alert_db.get_unrouted_alerts()
        assert any(a["message_id"] == "outbox-test-1" for a in unrouted)

        # Update stage to ROUTED
        alert_db.update_routing_stage("outbox-test-1", "ROUTED")
        unrouted_after = alert_db.get_unrouted_alerts()
        assert not any(a["message_id"] == "outbox-test-1" for a in unrouted_after)

