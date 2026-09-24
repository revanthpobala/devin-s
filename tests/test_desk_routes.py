import pytest
from fastapi.testclient import TestClient
import json

from src.ui.app import create_app
from src.tracking.watch_manager import _get_connection, _db_lock, init_watch_db
from src.tracking import suggestion_scorer

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_research_watch.db"
    monkeypatch.setenv("RESEARCH_WATCH_DB", str(db_path))
    from src.tracking import watch_manager
    watch_manager.DB_PATH = db_path
    
    init_watch_db()
    
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            # Setup some basic data for today
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            
            # suggestions table
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
                    verdict TEXT DEFAULT 'ENTER'
                )
            """)
            # Actionable (IN_ZONE)
            cursor.execute("INSERT INTO suggestions (ticker, date, gate_status, setup_lane) VALUES ('MSFT', ?, 'PASS', 'RR_SETUP')", (today,))
            
            # Record
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, scorer_version, kind, verdict, setup_lane, exit_date, r_net) VALUES ('TSLA', '2026-09-24', 'judge', 'PASS', 2, 'NEW', 'ENTER', 'CODE20', '2026-09-25', 1.5)")
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, scorer_version, kind, verdict, setup_lane, exit_date, r_net) VALUES ('NVDA', '2026-09-24', 'judge', 'PASS', 2, 'NEW', 'ENTER', 'RR_SETUP', '2026-09-25', -1.0)")
            
            # watch_targets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watch_targets (
                    ticker TEXT PRIMARY KEY,
                    status TEXT,
                    distance_to_entry_pct REAL,
                    last_price REAL
                )
            """)
            cursor.execute("INSERT INTO watch_targets (ticker, date, status, verdict, distance_to_entry_pct, updated_at) VALUES ('MSFT', ?, 'IN_ZONE', 'ENTER', 0.5, ?)", (today, today))
            
            conn.commit()
    
    app = create_app()
    return TestClient(app)

def test_get_today(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    data = response.json()
    assert "found" in data
    assert "actionable" in data
    assert "stalking" in data
    
    # Check AAPL in found
    assert any(r["ticker"] == "AAPL" for r in data["found"])
    
    # Check MSFT in actionable
    assert any(r["ticker"] == "MSFT" for r in data["actionable"])

def test_get_journal(client):
    response = client.get("/api/desk/journal")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) > 0
    assert any(r["ticker"] == "TSLA" for r in data["items"])

def test_get_record(client):
    response = client.get("/api/desk/record?from=2026-09-23")
    assert response.status_code == 200
    data = response.json()
    
    assert "win_pct" in data
    assert "sum_r" in data
    assert "equity_curve" in data
    assert "lanes" in data
    
    assert data["sum_r"] == 0.5  # 1.5 - 1.0
    assert data["won_count"] == 1
    assert data["total_scored"] == 2
    assert "CODE20" in data["lanes"]
    assert "RR_SETUP" in data["lanes"]
    assert len(data["equity_curve"]) > 0
