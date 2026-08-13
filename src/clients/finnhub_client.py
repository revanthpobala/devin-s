import logging
import os
import threading
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# ── Finnhub free-tier rate limit: 60 calls / minute ────────────────────────────
# 157 survivors × (local + deep research) would blow past 60/min if fired in a
# burst, so we serialize behind a minimum inter-request interval and back off on
# 429. A 60s in-process cache (keyed by URL) further cuts redundant calls.
_fh_lock = threading.Lock()
_fh_last_call = 0.0
_fh_backoff_until = 0.0
_fh_min_interval = 1.1  # ~54 calls/min safe headroom under the 60/min cap
_fh_backoff_seconds = 10.0  # cool-down after a 429

_cache: dict = {}
_cache_ts: dict = {}


def _fh_key() -> str:
    return os.getenv("FINHUB_API_KEY", "")


def _fh_throttle():
    global _fh_last_call
    with _fh_lock:
        now = __import__("time").time()
        wait = 0.0
        if now < _fh_backoff_until:
            wait = _fh_backoff_until - now
        elif _fh_last_call > 0:
            elapsed = now - _fh_last_call
            if elapsed < _fh_min_interval:
                wait = _fh_min_interval - elapsed
        if wait > 0:
            __import__("time").sleep(wait)
        _fh_last_call = __import__("time").time()


def _trigger_fh_backoff():
    global _fh_backoff_until
    with _fh_lock:
        _fh_backoff_until = max(_fh_backoff_until, __import__("time").time() + _fh_backoff_seconds)


def _cached_get(url: str, ttl: int = 60) -> object:
    now = __import__("time").time()
    if url in _cache and (now - _cache_ts.get(url, 0)) < ttl:
        return _cache[url]
    _fh_throttle()
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 429:
            logger.warning("Finnhub 429 rate limit hit — backing off.")
            _trigger_fh_backoff()
            return None
        if r.status_code != 200:
            logger.warning(f"Finnhub returned {r.status_code}: {r.text[:120]}")
            return None
        data = r.json()
        _cache[url] = data
        _cache_ts[url] = __import__("time").time()
        return data
    except Exception as e:
        logger.warning(f"Finnhub request failed: {e}")
        _trigger_fh_backoff()
        return None


def get_company_news(symbol: str, days: int = 2) -> list:
    """Recent company news from Finnhub as a list of article dicts.

    Returns [] if no key / no articles / error so callers can fall back.
    """
    key = _fh_key()
    if not key:
        return []
    end = datetime.now()
    start = end - timedelta(days=days)
    url = (
        f"https://finnhub.io/api/v1/company-news"
        f"?symbol={symbol.upper()}&from={start.strftime('%Y-%m-%d')}"
        f"&to={end.strftime('%Y-%m-%d')}&token={key}"
    )
    data = _cached_get(url)
    if not isinstance(data, list):
        return []
    return data


def get_general_news(days: int = 2, max_items: int = 8) -> list:
    """General market news from Finnhub as a list of article dicts."""
    key = _fh_key()
    if not key:
        return []
    url = f"https://finnhub.io/api/v1/news?category=general&token={key}"
    data = _cached_get(url, ttl=300)
    if not isinstance(data, list):
        return []
    return data


def format_news_context(articles: list, days: int = 2, max_items: int = 15) -> str:
    """Render Finnhub article dicts into the [date] headline / Summary text the
    LLM already consumes. Returns '' when empty."""
    if not articles:
        return ""
    ctx = ""
    seen = set()
    cutoff = datetime.now() - timedelta(days=days)
    for art in articles:
        ts = art.get("datetime", 0)
        try:
            dt = datetime.fromtimestamp(ts)
        except Exception:
            dt = None
        if dt and dt < cutoff:
            continue
        headline = art.get("headline", "").strip()
        if not headline or headline in seen:
            continue
        seen.add(headline)
        date_str = dt.strftime("%Y-%m-%d") if dt else ""
        ctx += f"[{date_str}] {headline}\nSummary: {art.get('summary', '')}\n\n"
        if len(seen) >= max_items:
            break
    return ctx.strip()


def get_ticker_news_context(symbol: str, days: int = 2) -> str:
    """Convenience: company news for a symbol rendered as LLM context text."""
    return format_news_context(get_company_news(symbol, days), days=days)


def get_analyst_ratings(symbol: str) -> dict:
    """Fetch analyst recommendation trends from Finnhub."""
    key = _fh_key()
    if not key: return {}
    url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}"
    data = _cached_get(url, ttl=3600)
    if not isinstance(data, list) or len(data) == 0:
        return {}
    return data[0]  # Return the most recent period


def get_insider_sentiment(symbol: str) -> dict:
    """Fetch insider sentiment for the current year from Finnhub."""
    key = _fh_key()
    if not key: return {}
    # Fetch from Jan 1st of current year to today
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    start_of_year = f"{datetime.now().year}-01-01"
    url = f"https://finnhub.io/api/v1/stock/insider-sentiment?symbol={symbol}&from={start_of_year}&to={today}&token={key}"
    data = _cached_get(url, ttl=3600)
    if not isinstance(data, dict) or "data" not in data or not data["data"]:
        return {}
    
    # Aggregate the changes
    total_change = sum(month.get("change", 0) for month in data["data"])
    total_mspr = sum(month.get("mspr", 0) for month in data["data"])
    return {"total_change": total_change, "net_mspr": total_mspr, "months_counted": len(data["data"])}


def get_earnings_history(symbol: str) -> list:
    """Fetch recent earnings surprises from Finnhub."""
    key = _fh_key()
    if not key: return []
    url = f"https://finnhub.io/api/v1/stock/earnings?symbol={symbol}&token={key}"
    data = _cached_get(url, ttl=3600)
    if not isinstance(data, list):
        return []
    return data[:4]  # Return last 4 quarters


def format_finnhub_institutional_block(symbol: str) -> str:
    """Compile analyst ratings, insider sentiment, and earnings surprises into a string block."""
    lines = []
    
    # 1. Analyst Ratings
    ratings = get_analyst_ratings(symbol)
    if ratings:
        period = ratings.get('period', '')
        sb, b, h, s, ss = ratings.get('strongBuy', 0), ratings.get('buy', 0), ratings.get('hold', 0), ratings.get('sell', 0), ratings.get('strongSell', 0)
        lines.append(f"Analyst Consensus ({period}): {sb} Strong Buy | {b} Buy | {h} Hold | {s} Sell | {ss} Strong Sell")

    # 2. Insider Sentiment
    insider = get_insider_sentiment(symbol)
    if insider:
        change = insider.get('total_change', 0)
        mspr = insider.get('net_mspr', 0)
        months = insider.get('months_counted', 1)
        direction = "BUYING" if change > 0 else "SELLING" if change < 0 else "NEUTRAL"
        lines.append(f"Insider Sentiment (YTD): Net {direction} ({change:,} shares changed) | Avg MSPR: {mspr/months:.2f}")

    # 3. Earnings Surprises
    earnings = get_earnings_history(symbol)
    if earnings:
        surprises = []
        for e in earnings:
            period = e.get('period', '')
            actual = e.get('actual')
            estimate = e.get('estimate')
            if actual is not None and estimate is not None:
                diff = actual - estimate
                indicator = "[+] BEAT" if diff > 0 else "[-] MISS" if diff < 0 else "[=] IN-LINE"
                surprises.append(f"[{period} {indicator} (Est: {estimate}, Act: {actual})]")
        if surprises:
            lines.append("Recent Earnings Surprises:")
            lines.append("  " + "\n  ".join(surprises))

    if not lines:
        return ""
    
    return "--- INSTITUTIONAL FLOW & SENTIMENT ---\n" + "\n".join(lines) + "\n"
