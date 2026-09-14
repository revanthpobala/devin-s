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
