"""
tests/test_quote_router.py

Unit tests for Workload-Segregated QuoteRouter & PriceClient:
  - Verifies Intraday Execution channel routes to Schwab
  - Verifies Surveillance / Watchlist channel routes to non-Schwab (Yahoo/Alpaca)
  - Verifies Broad Market Index channel routes to Yahoo Direct REST (^VIX, ^SPX)
  - Verifies in-memory TTL caching and request coalescing
  - Verifies circuit breaking behavior
"""

import time
from unittest.mock import MagicMock, patch
import pytest

from src.clients.quote_router import QuoteData, QuoteRouter
from src.clients.price_client import (
    get_current_price,
    get_current_prices_batch,
    get_realtime_quote_data,
    format_realtime_quote_text,
)


@pytest.fixture
def mock_router():
    router = QuoteRouter()
    return router


def test_index_routing_direct_to_yahoo(mock_router):
    """Broad indices (VIX, SPX, NDX, RUT) must route to Yahoo REST, never Schwab/Alpaca."""
    with patch.object(mock_router, "_fetch_yahoo_direct") as mock_yahoo, \
         patch("src.clients.schwab_client.get_realtime_quote") as mock_schwab:
        
        mock_yahoo.return_value = QuoteData(
            symbol="VIX",
            last_price=15.25,
            source="YAHOO_DIRECT",
            timestamp=time.time(),
        )

        q = mock_router.get_index_quote("VIX")
        assert q is not None
        assert q.last_price == 15.25
        assert q.source == "YAHOO_DIRECT"

        # Verify Schwab was NEVER called for VIX
        mock_schwab.assert_not_called()
        mock_yahoo.assert_called_once_with("VIX", is_index=True)


def test_execution_channel_routes_to_schwab(mock_router):
    """Intraday execution requests must route to Schwab API."""
    fake_schwab = {
        "symbol": "AAPL",
        "last_price": 250.0,
        "bid": 249.95,
        "ask": 250.05,
        "net_change": 1.5,
        "net_percent_change": 0.6,
        "volume": 50000000,
    }
    with patch("src.clients.schwab_client.get_realtime_quote", return_value=fake_schwab) as mock_sq:
        q = mock_router.get_live_execution_quote("AAPL")
        assert q is not None
        assert q.last_price == 250.0
        assert q.source == "SCHWAB"
        mock_sq.assert_called_once_with("AAPL")


def test_surveillance_channel_bypasses_schwab(mock_router):
    """Watchlist stalking must use Yahoo/Alpaca and bypass Schwab."""
    fake_yahoo = QuoteData(
        symbol="TSLA",
        last_price=220.0,
        source="YAHOO_DIRECT",
        timestamp=time.time(),
    )
    with patch.object(mock_router, "_fetch_alpaca_snapshot_single", return_value=None), \
         patch.object(mock_router, "_fetch_yahoo_direct", return_value=fake_yahoo) as mock_yh, \
         patch("src.clients.schwab_client.get_realtime_quote") as mock_schwab:

        q = mock_router.get_watchlist_quote("TSLA")
        assert q is not None
        assert q.last_price == 220.0
        assert q.source == "YAHOO_DIRECT"
        mock_schwab.assert_not_called()
        mock_yh.assert_called_once_with("TSLA")


def test_in_memory_cache_prevents_duplicate_calls(mock_router):
    """Repeated calls within TTL must hit cache with 0 network calls."""
    fake_schwab = {"symbol": "NVDA", "last_price": 120.0}
    with patch("src.clients.schwab_client.get_realtime_quote", return_value=fake_schwab) as mock_sq:
        q1 = mock_router.get_live_execution_quote("NVDA")
        q2 = mock_router.get_live_execution_quote("NVDA")
        assert q1.last_price == 120.0
        assert q2.last_price == 120.0
        # Only ONE call should have been made
        mock_sq.assert_called_once_with("NVDA")


def test_batch_surveillance_partitions_indices_and_equities(mock_router):
    """Batch watchlist fetching must partition indices to Yahoo and equities to Alpaca/Yahoo."""
    fake_quotes = {
        "AAPL": QuoteData(symbol="AAPL", last_price=250.0, source="YAHOO_DIRECT"),
        "MSFT": QuoteData(symbol="MSFT", last_price=450.0, source="YAHOO_DIRECT"),
        "VIX": QuoteData(symbol="VIX", last_price=15.0, source="YAHOO_DIRECT"),
    }
    with patch.object(mock_router, "_fetch_yahoo_direct_batch", return_value=fake_quotes), \
         patch("src.clients.schwab_client.get_realtime_quotes_batch") as mock_schwab_batch:

        res = mock_router.get_watchlist_quotes_batch(["AAPL", "MSFT", "VIX"])
        assert len(res) == 3
        assert res["VIX"].last_price == 15.0
        assert res["AAPL"].last_price == 250.0
        # Schwab batch was NEVER called for surveillance
        mock_schwab_batch.assert_not_called()


def test_circuit_breaker_cooldown_on_failure(mock_router):
    """When Schwab fails repeatedly, circuit breaker trips and routes to emergency fallback."""
    with patch("src.clients.schwab_client.get_realtime_quote", side_effect=Exception("401 Unauthorized")), \
         patch.object(mock_router, "_fetch_yahoo_direct") as mock_backup:

        mock_backup.return_value = QuoteData(
            symbol="AMZN",
            last_price=180.0,
            source="YAHOO_DIRECT",
            timestamp=time.time(),
        )

        q = mock_router.get_live_execution_quote("AMZN")
        assert q is not None
        assert q.last_price == 180.0
        assert "EMERGENCY_FALLBACK" in q.source
        assert not mock_router.schwab_breaker.is_available()


def test_price_client_public_api_compatibility():
    """Verify price_client functions maintain expected signatures and types."""
    with patch("src.clients.quote_router.quote_router.get_price", return_value=150.25):
        p = get_current_price("AAPL")
        assert isinstance(p, float)
        assert p == 150.25

    with patch("src.clients.quote_router.quote_router.get_prices_batch", return_value={"AAPL": 150.25, "SPY": 550.0}):
        batch = get_current_prices_batch(["AAPL", "SPY"])
        assert len(batch) == 2
        assert batch["SPY"] == 550.0

    with patch("src.clients.quote_router.quote_router.format_quote_text", return_value="REAL-TIME QUOTE for AAPL: Last 150.25"):
        text = format_realtime_quote_text("AAPL")
        assert "REAL-TIME QUOTE" in text


def test_tastytrade_surveillance_fallback(mock_router):
    """When Alpaca and Yahoo are unavailable, surveillance can fallback to Tastytrade."""
    fake_tt = QuoteData(
        symbol="QQQ",
        last_price=490.50,
        bid=490.45,
        ask=490.55,
        source="TASTYTRADE",
        timestamp=time.time(),
    )
    with patch.object(mock_router, "_fetch_alpaca_snapshot_single", return_value=None), \
         patch.object(mock_router, "_fetch_yahoo_direct", return_value=None), \
         patch.object(mock_router, "_fetch_tastytrade_quote", return_value=fake_tt) as mock_tt:

        q = mock_router.get_watchlist_quote("QQQ")
        assert q is not None
        assert q.last_price == 490.50
        assert q.source == "TASTYTRADE"
        mock_tt.assert_called_once_with("QQQ")


def test_tastytrade_metrics_enrichment(mock_router):
    """Verify Tastytrade market metrics enrich quote text with IV rank and 30d HV."""
    fake_metrics = {
        "NVDA": {"iv_rank": 35.5, "iv_percentile": 42.0, "hv30": 28.4}
    }
    fake_quote = QuoteData(
        symbol="NVDA",
        last_price=125.0,
        source="YAHOO_DIRECT",
        timestamp=time.time(),
    )
    with patch.object(mock_router, "get_watchlist_quote", return_value=fake_quote), \
         patch.object(mock_router, "enrich_with_tastytrade_metrics", return_value=fake_metrics):

        text = mock_router.format_quote_text("NVDA")
        assert text is not None
        assert "IV Rank: 35.5%" in text
        assert "30d HV: 28.4%" in text
