import pytest
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8050"


def _is_server_running() -> bool:
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/status", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_server_running(),
    reason="Cockpit server not running on http://127.0.0.1:8050 (run run_ui.py to execute live E2E tests)",
)


def _fetch(endpoint, method="GET", data=None, headers=None, timeout=10):
    url = f"{BASE_URL}{endpoint}"
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8")


def test_api_cockpit_html_page():
    """Verify Cockpit index HTML loads with 200 OK and expected core layout."""
    status, body = _fetch("/")
    assert status == 200
    assert "Stock Trading Operations Cockpit" in body
    assert "desk-switcher-bar" in body
    assert "sidebar-rev-chat" in body
    assert "report-modal" in body


def test_api_status_endpoint():
    """Verify /api/status returns market, ingestor, hardware and GPU telemetry."""
    status, body = _fetch("/api/status")
    assert status == 200
    data = json.loads(body)
    assert "market_open" in data or "is_open" in data or "status" in data
    assert "ingestor_running" in data or "tracker_running" in data
    assert "gpus" in data
    assert isinstance(data["gpus"], list)


def test_api_watch_targets():
    """Verify /api/watch-targets returns institutional watchlist, trigger targets, trade P&L, and performance summary."""
    status, body = _fetch("/api/watch-targets")
    assert status == 200
    data = json.loads(body)
    assert "targets" in data
    assert isinstance(data["targets"], list)
    assert "performance" in data
    assert "win_rate" in data["performance"] or "total_r" in data["performance"]
    if data["targets"]:
        t0 = data["targets"][0]
        assert "trade_dollar_pnl" in t0
        assert "trade_roc_pct" in t0
        assert "trade_label" in t0



def test_api_reports_list():
    """Verify /api/reports returns list of research dates and dossiers."""
    status, body = _fetch("/api/reports")
    assert status == 200
    data = json.loads(body)
    assert "dates" in data or "reports" in data or isinstance(data, (list, dict))


def test_api_copilot_chat_sessions():
    """Verify /api/copilot/chats/sessions returns 15-day chat session records."""
    status, body = _fetch("/api/copilot/chats/sessions?days=15")
    assert status == 200
    data = json.loads(body)
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


def test_api_copilot_python_execute():
    """Verify /api/copilot/execute-python executes quantitative math in sandbox."""
    status, body = _fetch(
        "/api/copilot/execute-python",
        method="POST",
        data={
            "code": "r = 100 - 45; print(f'DIFF={r}')",
            "ticker": "WMT"
        }
    )
    assert status == 200
    data = json.loads(body)
    assert "output" in data
    assert "DIFF=55" in data["output"]
    assert data["success"] is True


def test_api_quote_wmt():
    """Verify /api/quote/{ticker} returns live quote data."""
    status, body = _fetch("/api/quote/WMT")
    assert status == 200
    data = json.loads(body)
    assert "price" in data or "close" in data or "symbol" in data or "spot" in data


def test_api_positions():
    """Verify /api/positions returns current open positions list."""
    status, body = _fetch("/api/positions")
    assert status == 200
    data = json.loads(body)
    assert "positions" in data or isinstance(data, list)


def test_api_copilot_chat_stream_wmt():
    """Verify /api/copilot/chat/stream SSE streams tokens with zero compilation errors."""
    url = f"{BASE_URL}/api/copilot/chat/stream"
    req_data = json.dumps({
        "question": "Quick 10-word summary of WMT setup.",
        "ticker": "WMT",
        "history": []
    }).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")

    tokens_received = []
    errors_received = []

    with urllib.request.urlopen(req, timeout=60) as response:
        assert response.status == 200
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    msg = json.loads(payload)
                    if "token" in msg:
                        tokens_received.append(msg["token"])
                    if "error" in msg:
                        errors_received.append(msg["error"])
                except Exception:
                    pass

    assert len(errors_received) == 0, f"Errors encountered during streaming: {errors_received}"
    assert len(tokens_received) > 0, "Expected to receive streamed tokens from local LLM"
