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
    """GET an Adanos endpoint. Returns parsed JSON or None on any failure/quota hit."""
    if not config.ADANOS_API_KEY:
        return None
    url = f"{config.ADANOS_BASE_URL}/{path.lstrip('/')}"
    try:
        req = urllib.request.Request(url, headers=_ADANOS_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"[Adanos] Monthly quota exceeded (429) on {path}")
        elif e.code == 401:
            logger.warning("[Adanos] API key missing/invalid (401).")
        else:
            logger.warning(f"[Adanos] HTTP {e.code} on {path}: {e.read().decode()[:120]}")
    except Exception as e:
        logger.warning(f"[Adanos] request failed for {path}: {e}")
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
    """Human-readable social-sentiment block for the deep-research prompt."""
    ss = get_social_sentiment(ticker)
    if not ss.get("news") and not ss.get("reddit"):
        return f"--- PER-TICKER SOCIAL SENTIMENT ({ticker}) ---\n(no Adanos data available for this ticker)\n"

    lines = [f"--- PER-TICKER SOCIAL SENTIMENT ({ticker} Adanos) ---"]
    for label, plat in (("NEWS", ss.get("news")), ("REDDIT", ss.get("reddit"))):
        if not plat or not plat.get("found"):
            continue
        lines.append(
            f"{label}: sentiment_score={plat.get('sentiment_score')} "
            f"bullish={plat.get('bullish_pct')}% bearish={plat.get('bearish_pct')}% "
            f"buzz={plat.get('buzz_score')} trend={plat.get('trend')}"
        )
    return "\n".join(lines) + "\n"


def get_market_sentiment() -> dict:
    """Service-wide market mood for the macro context (deep research).

    Returns {"news": {...}, "reddit": {...}} aggregates, each with overall
    sentiment_score / bullish_pct / bearish_pct / buzz_score / trend / trend_history
    plus top `drivers` (hottest tickers by buzz). Empty dicts if unavailable.
    Costs 2 quota calls per call (one per platform) — call once per deep-research run.
    """
    out = {}
    for platform in ("news", "reddit"):
        cache_key = ("market", platform)
        if cache_key in _daily_cache:
            out[platform] = _daily_cache[cache_key]
            continue
        data = _get_json(f"{platform}/stocks/v1/market-sentiment")
        plat = {}
        if data:
            plat = {
                "found": True,
                "buzz_score": data.get("buzz_score"),
                "sentiment_score": data.get("sentiment_score"),
                "bullish_pct": data.get("bullish_pct"),
                "bearish_pct": data.get("bearish_pct"),
                "trend": data.get("trend"),
                "mentions": data.get("mentions"),
                "active_tickers": data.get("active_tickers"),
                "drivers": [
                    {
                        "ticker": d.get("ticker"),
                        "mentions": d.get("mentions"),
                        "buzz_score": d.get("buzz_score"),
                        "sentiment_score": d.get("sentiment_score"),
                    }
                    for d in (data.get("drivers") or [])[:8]
                ],
            }
        _daily_cache[cache_key] = plat
        out[platform] = plat
    return out


def format_market_sentiment_block() -> str:
    """Human-readable macro social-sentiment block for the deep-research prompt."""
    ms = get_market_sentiment()
    if not ms.get("news") and not ms.get("reddit"):
        return ""
    lines = ["--- MACRO SOCIAL SENTIMENT (Adanos, overall market mood) ---"]
    for label, plat in (("NEWS", ms.get("news")), ("REDDIT", ms.get("reddit"))):
        if not plat:
            continue
        lines.append(
            f"{label}: sentiment_score={plat.get('sentiment_score')} "
            f"bullish={plat.get('bullish_pct')}% bearish={plat.get('bearish_pct')}% "
            f"buzz={plat.get('buzz_score')} trend={plat.get('trend')} "
            f"active_tickers={plat.get('active_tickers')}"
        )
        drivers = plat.get("drivers") or []
        if drivers:
            top = ", ".join(
                f"{d['ticker']}(buzz={d.get('buzz_score')},sent={d.get('sentiment_score')})"
                for d in drivers[:5]
            )
            lines.append(f"  top movers: {top}")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_social_sentiment("AAPL"), indent=2))
