"""
tests/test_screener_gate.py

Unit tests for ScreenerGate and intelligent Schwab/Tastytrade routing:
  - Verifies set_screener_busy / is_screener_busy state lifecycle
  - Verifies busy state expiry auto-clearing
  - Verifies QuoteRouter routes quotes to Tastytrade when screener is active
  - Verifies SchwabTokenBucket rate limiter paces requests safely
"""

import time
from unittest.mock import MagicMock, patch
import pytest

from src.clients.quote_router import QuoteData, QuoteRouter
from src.tracking.screener_gate import (
    get_screener_gate_status,
    is_screener_busy,
    set_screener_busy,
)
from src.clients.schwab_client import SchwabTokenBucket


def test_screener_gate_lifecycle():
    """Verify screener gate sets, checks, and clears busy state."""
    set_screener_busy(True, duration_seconds=10.0, reason="test scan")
    assert is_screener_busy() is True

    status = get_screener_gate_status()
    assert status["is_screener_busy"] is True
    assert status["remaining_seconds"] > 0
    assert "test scan" in status["reason"]

    set_screener_busy(False)
    assert is_screener_busy() is False


def test_screener_gate_auto_expires():
    """Verify screener gate automatically expires after duration_seconds."""
    clock = [1000.0]
    with patch("src.tracking.screener_gate.time") as fake:
        fake.time.side_effect = lambda: clock[0]
        set_screener_busy(True, duration_seconds=10, reason="quick test")
        assert is_screener_busy() is True
        clock[0] += 11
        assert is_screener_busy() is False


def test_quote_router_routes_to_tastytrade_when_screener_busy():
    """When screener is active on Schwab, QuoteRouter must route quotes to Tastytrade."""
    router = QuoteRouter()
    fake_tt = QuoteData(symbol="MSFT", last_price=420.0, source="TASTYTRADE", timestamp=time.time())

    with patch("src.tracking.screener_gate.is_screener_busy", return_value=True), \
         patch.object(router, "_fetch_tastytrade_quote", return_value=fake_tt) as mock_tt, \
         patch("src.clients.schwab_client.get_realtime_quote") as mock_schwab:

        q = router.get_live_execution_quote("MSFT")
        assert q is not None
        assert q.last_price == 420.0
        assert "TASTYTRADE" in q.source
        # Schwab must NOT have been called because screener is active!
        mock_schwab.assert_not_called()
        mock_tt.assert_called_once_with("MSFT")


def test_schwab_token_bucket_paces_bursts():
    """Verify SchwabTokenBucket limits burst consumption to configured rate."""
    limiter = SchwabTokenBucket(rate_per_sec=10.0, burst=2)
    # 2 tokens available immediately
    assert limiter.acquire(timeout=0.1) is True
    assert limiter.acquire(timeout=0.1) is True
    clock = [1000.0]
    slept = []
    limiter.last_check = clock[0]
    limiter.tokens = 0.0

    def _sleep(sec):
        slept.append(sec)
        clock[0] += sec

    with patch("src.clients.schwab_client.time") as fake:
        fake.time.side_effect = lambda: clock[0]
        fake.sleep.side_effect = _sleep
        assert limiter.acquire(timeout=0.5) is True
    assert slept
