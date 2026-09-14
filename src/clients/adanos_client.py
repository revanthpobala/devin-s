from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from src import config

logger = logging.getLogger(__name__)

_ADANOS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "X-API-Key": config.ADANOS_API_KEY,
}

# Free tier = 250 requests/month. We cache per (ticker, platform) for the day so a
# re-run of the same date doesn't burn quota. Cache is per-process (cheap, sufficient
# since a single research run touches each ticker once).
_daily_cache = {}


def _get_json(path: str) -> dict | None:
    """Disabled — Adanos retired to eliminate latency and quota limits."""
    return None


def get_news_sentiment(ticker: str) -> dict:
    """Financial-news sentiment for a ticker from Adanos News Stocks.

    Returns a normalized dict:
      {found, buzz_score, mentions, sentiment_score (-1..1), bullish_pct,
       bearish_pct, trend, source_count, period_days}
    Empty dict if unavailable.
    """
    cache_key = ("news", ticker.upper())
    if cache_key in _daily_cache:
        return _daily_cache[cache_key]
    out = {}
    data = _get_json(f"news/stocks/v1/stock/{ticker.upper()}")
    if data and data.get("found"):
        out = {
            "found": True,
            "buzz_score": data.get("buzz_score"),
            "mentions": data.get("mentions"),
            "sentiment_score": data.get("sentiment_score"),
            "bullish_pct": data.get("bullish_pct"),
            "bearish_pct": data.get("bearish_pct"),
            "trend": data.get("trend"),
            "source_count": data.get("source_count"),
            "period_days": data.get("period_days"),
        }
    _daily_cache[cache_key] = out
    return out


def get_reddit_sentiment(ticker: str) -> dict:
    """Reddit retail sentiment/buzz for a ticker from Adanos Reddit Stocks.

    Returns a normalized dict:
      {found, buzz_score, mentions, sentiment_score (-1..1), bullish_pct,
       bearish_pct, trend, total_upvotes, unique_posts, subreddit_count, period_days}
    Empty dict if unavailable.
    """
    cache_key = ("reddit", ticker.upper())
    if cache_key in _daily_cache:
        return _daily_cache[cache_key]
    out = {}
    data = _get_json(f"reddit/stocks/v1/stock/{ticker.upper()}")
    if data and data.get("found"):
        out = {
            "found": True,
            "buzz_score": data.get("buzz_score"),
            "mentions": data.get("mentions"),
            "sentiment_score": data.get("sentiment_score"),
            "bullish_pct": data.get("bullish_pct"),
            "bearish_pct": data.get("bearish_pct"),
            "trend": data.get("trend"),
            "total_upvotes": data.get("total_upvotes"),
            "unique_posts": data.get("unique_posts"),
            "subreddit_count": data.get("subreddit_count"),
            "period_days": data.get("period_days"),
        }
    _daily_cache[cache_key] = out
    return out


def get_social_sentiment(ticker: str) -> dict:
    """Combined news + Reddit social sentiment for a ticker.

    Shape: {"news": {...}, "reddit": {...}} (each empty dict if unavailable).
    """
    return {
        "news": get_news_sentiment(ticker),
        "reddit": get_reddit_sentiment(ticker),
    }


def format_social_block(ticker: str) -> str:
    """Human-readable social-sentiment block (Retired)."""
    return ""


def get_market_sentiment() -> dict:
    """Service-wide market mood for the macro context (Retired)."""
    return {}


def format_market_sentiment_block() -> str:
    """Human-readable macro social-sentiment block (Retired)."""
    return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_social_sentiment("AAPL"), indent=2))
