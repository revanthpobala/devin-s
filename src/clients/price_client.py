"""
src/clients/price_client.py

Workload-Segregated Price Client.
Delegates to QuoteRouter for condition-based routing:
  - Intraday execution / active positions -> Schwab API / Schwab WebSocket
  - Surveillance / watchlists / screeners -> Yahoo Direct REST / Alpaca (split among others)
  - Broad indices (VIX, SPX) -> Yahoo Direct REST
"""

import logging
from typing import Dict, List, Optional

from src.clients.quote_router import QuoteData, quote_router

logger = logging.getLogger(__name__)


def get_current_price(symbol: str, context: str = "surveillance") -> Optional[float]:
    """
    Fetch the current price for a ticker symbol.
    
    Args:
        symbol: Ticker symbol (e.g. 'AAPL', 'SPY', 'VIX')
        context: Workload domain:
                 - 'execution' or 'intraday': Powered by Schwab API / WebSocket exclusively.
                 - 'surveillance' (default): Split across Yahoo Direct REST and Alpaca.
    """
    return quote_router.get_price(symbol, context=context)


def get_current_prices_batch(symbols: List[str], context: str = "surveillance") -> Dict[str, float]:
    """
    Fetch live current prices for multiple symbols in batch.
    
    Args:
        symbols: List of ticker symbols
        context: Workload domain ('surveillance' or 'execution')
    """
    return quote_router.get_prices_batch(symbols, context=context)


def get_realtime_quote_data(symbol: str, context: str = "surveillance") -> Optional[Dict[str, any]]:
    """
    Fetch structured quote dictionary (last_price, bid, ask, high, low, open, close, volume, net_change).
    """
    if not symbol:
        return None

    if context.lower() in ("intraday", "execution", "live", "trade"):
        qd = quote_router.get_live_execution_quote(symbol)
    else:
        qd = quote_router.get_watchlist_quote(symbol)

    return qd.to_dict() if qd else None


def format_realtime_quote_text(symbol: str) -> Optional[str]:
    """
    Format a concise real-time quote block for LLM prompt context.
    """
    return quote_router.format_quote_text(symbol)
