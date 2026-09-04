import logging
import os
from typing import Optional

# pyrefly: ignore [missing-import]
import requests

logger = logging.getLogger(__name__)

# Try importing yfinance. If it fails, we will use requests to query a backup source.
try:
    # pyrefly: ignore [missing-import]
    import yfinance as yf

    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance is not installed or available. Will use REST API fallback.")


def get_current_price(symbol: str) -> Optional[float]:
    """
    Fetch the current price for a ticker symbol.
    Tries Alpaca API first for real-time equity/ETF quotes (if credentials are set),
    falls back to yfinance, and finally falls back to Yahoo Query API.
    """
    symbol = symbol.upper().strip()
    if not symbol:
        return None

    # Indices like VIX/SPX need the ^ prefix for Yahoo Finance
    INDEX_SYMBOLS = {"VIX", "SPX", "NDX", "RUT", "DJI"}
    yahoo_symbol = f"^{symbol}" if symbol in INDEX_SYMBOLS else symbol

    # 0. Try Schwab API FIRST (official live NBBO quotes, real-time from user's authenticated Schwab connection)
    try:
        from src.clients.schwab_client import get_realtime_quote
        if symbol not in INDEX_SYMBOLS and not symbol.startswith("^"):
            sq = get_realtime_quote(symbol)
            if sq and sq.get("last_price") and sq["last_price"] > 0:
                p = sq["last_price"]
                logger.info(f"Schwab real-time price for {symbol}: {p:.2f} (Net: {sq.get('net_change', 0):+.2f} / {sq.get('net_percent_change', 0):+.2f}%)")
                return float(p)
    except Exception as e_schwab:
        logger.debug(f"Schwab price lookup skipped for {symbol}: {e_schwab}")

    # 1. Try Alpaca API second if credentials are configured in environment (excludes indices like SPX/VIX)
    alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY")

    if alpaca_key and alpaca_secret:
        try:
            # Alpaca data API only supports stocks/ETFs, not index indices directly
            if symbol not in ["SPX", "VIX", "COMP"]:
                # Load and resolve base URL, mapping trading endpoints to their data API equivalents
                base_url = (
                    os.getenv("ALPACA_DATA_URL")
                    or os.getenv("ALPACA_API_URL")
                    or "https://data.alpaca.markets"
                )
                base_url = base_url.strip().rstrip("/")

                if "paper-api.alpaca.markets" in base_url:
                    base_url = base_url.replace("paper-api.alpaca.markets", "data.alpaca.markets")
                elif "api.alpaca.markets" in base_url:
                    base_url = base_url.replace("api.alpaca.markets", "data.alpaca.markets")

                # Ensure the /v2 path suffix is present
                if "/v2" not in base_url:
                    base_url = f"{base_url}/v2"

                logger.info(f"Fetching current price for {symbol} using Alpaca API ({base_url})...")
                headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}
                # Use Alpaca Data API v2 latest trade endpoint
                url = f"{base_url}/stocks/{symbol}/trades/latest"
                response = requests.get(url, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    trade = data.get("trade") or {}
                    price = trade.get("p")
                    if price is not None and price > 0:
                        logger.info(f"Alpaca real-time price for {symbol}: {price:.2f}")
                        return float(price)
        except Exception as e:
            logger.warning(f"Alpaca price fetch failed for {symbol}: {e}")

    # 2. Try Tastytrade API next (DXLink real-time streaming feed)
    try:
        from src.clients.tastytrade_client import TastytradeClient

        tt_client = TastytradeClient()
        if os.path.exists(tt_client.token_path) and symbol not in ["SPX", "VIX", "COMP"]:
            tt_quote = tt_client.get_realtime_quote(symbol)
            if tt_quote and tt_quote.get("price"):
                p = tt_quote["price"]
                logger.info(f"Tastytrade real-time price for {symbol}: {p:.2f} (Bid: {tt_quote.get('bid')}, Ask: {tt_quote.get('ask')})")
                return float(p)
    except Exception as e_tt:
        logger.debug(f"Tastytrade price lookup skipped: {e_tt}")

    # 3. Try yfinance if available (use ^SYMBOL for indices)
    if YFINANCE_AVAILABLE:
        try:
            logger.info(f"Fetching current price for {symbol} using yfinance...")
            ticker = yf.Ticker(yahoo_symbol)
            # fast_info is efficient and quick
            price = ticker.fast_info.get("last_price", None)
            if price is not None and price > 0:
                logger.info(f"yfinance price for {symbol}: {price:.2f}")
                return float(price)

            # Fallback to history if fast_info fails
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = hist["Close"].iloc[-1]
                logger.info(f"yfinance history price for {symbol}: {price:.2f}")
                return float(price)
        except Exception as e:
            logger.warning(f"yfinance failed for {symbol}: {e}")

    # 3. Fallback to Yahoo Query API via requests directly (use ^ prefix for indices)
    try:
        logger.info(f"Fetching current price for {symbol} using Yahoo Finance query endpoint...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?interval=1m&range=1d"
        )
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice", None)
            if price is not None:
                logger.info(f"Yahoo Query price for {symbol}: {price:.2f}")
                return float(price)
    except Exception as e:
        logger.warning(f"Yahoo Query API failed for {symbol}: {e}")

    logger.error(f"Could not fetch price for {symbol} using any available method.")
    return None


def get_current_prices_batch(symbols: list[str]) -> dict[str, float]:
    """
    Fetch live current prices for multiple symbols in batch.
    Uses Schwab get_realtime_quotes_batch as high-speed primary source,
    falling back to individual get_current_price for any missing.
    """
    prices: dict[str, float] = {}
    if not symbols:
        return prices

    # 1. Primary: Batch fetch from Schwab
    try:
        from src.clients.schwab_client import get_realtime_quotes_batch

        sq_batch = get_realtime_quotes_batch(symbols)
        for s, q in sq_batch.items():
            if q and q.get("last_price") and q["last_price"] > 0:
                prices[s] = float(q["last_price"])
    except Exception as e:
        logger.debug(f"Schwab batch price fetch error: {e}")

    # 2. Fallback for any symbols not returned by Schwab
    missing = [s for s in symbols if s.upper().strip() not in prices]
    for s in missing:
        try:
            p = get_current_price(s)
            if p is not None and p > 0:
                prices[s.upper().strip()] = float(p)
        except Exception:
            pass

    return prices
