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
