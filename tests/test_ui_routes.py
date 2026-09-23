"""
Unit and integration tests for decomposed FastAPI Cockpit UI routes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.ui.app import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    # Use TestClient with context manager to avoid triggering full background daemon threads during unit test
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_api_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "market_phase" in data
    assert "active_processes" in data
    assert "gpu_stats" in data


def test_schwab_status(client):
    response = client.get("/api/schwab/status")
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data and "configured" in data


def test_portfolio_summary(client):
    response = client.get("/api/schwab/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    assert "accounts" in data


def test_screener_status(client):
    response = client.get("/api/screener/continuous-status")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data


def test_watch_targets(client):
    response = client.get("/api/watch-targets")
    assert response.status_code == 200
    data = response.json()
    assert "targets" in data


def test_alerts_history(client):
    response = client.get("/api/alerts/history?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert "alerts" in data


def test_jobs_queue(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    assert "max_concurrent" in data


def test_reports_list(client):
    response = client.get("/api/reports")
    assert response.status_code == 200
    data = response.json()
    assert "dates" in data


def test_tickers_list(client):
    response = client.get("/api/tickers")
    assert response.status_code == 200
    data = response.json()
    assert "tickers" in data
    assert len(data["tickers"]) > 0


def test_company_names(client):
    response = client.get("/api/company-names")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)


def test_logs_buffer(client):
    response = client.get("/api/logs")
    assert response.status_code == 200
    data = response.json()
    assert "logs" in data


def test_sync_ticker_tastytrade_alerts_not_indexed(client, monkeypatch):
    from run_watch_alerts import SyncResult
    import run_watch_alerts

    monkeypatch.setattr(
        run_watch_alerts,
        "sync_reports_to_watchlist",
        lambda **kwargs: SyncResult(0),
    )

    response = client.post("/api/tastytrade-alerts/sync-ticker", json={"ticker": "NONEXISTENT", "date": "2026-09-23"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["indexed_count"] == 0
    assert "No research reports or watch levels found" in data["message"]


def test_sync_ticker_tastytrade_alerts_gate_rejected(client, monkeypatch):
    from run_watch_alerts import SyncResult
    import run_watch_alerts

    monkeypatch.setattr(
        run_watch_alerts,
        "sync_reports_to_watchlist",
        lambda **kwargs: SyncResult(
            1,
            indexed=["BADTICKER"],
            rejected=[{"ticker": "BADTICKER", "reasons": ["stop $110.00 >= entry_low $100.00"]}],
        ),
    )

    response = client.post("/api/tastytrade-alerts/sync-ticker", json={"ticker": "BADTICKER", "date": "2026-09-23"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["indexed_count"] == 1
    assert "rejected by validation gate" in data["message"]


def test_sync_ticker_tastytrade_alerts_success(client, monkeypatch):
    from run_watch_alerts import SyncResult
    import run_watch_alerts

    monkeypatch.setattr(
        run_watch_alerts,
        "sync_reports_to_watchlist",
        lambda **kwargs: SyncResult(1, indexed=["AAPL"], tt_alerts_count=3, tt_synced=["AAPL"]),
    )

    response = client.post("/api/tastytrade-alerts/sync-ticker", json={"ticker": "AAPL", "date": "2026-09-23"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["indexed_count"] == 1
    assert data["tt_alerts_count"] == 3
    assert "Successfully synced 3 Tastytrade cloud quote alert(s)" in data["message"]

