"""live_fetcher.py — parallel live context retrieval (news, quotes, GEX, AV, Finnhub, Google)."""
from __future__ import annotations

import concurrent.futures
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LiveContext:
    fresh_news: str = ""
    av_block: str = ""
    institutional_block: str = ""
    grounded_block: str = ""
    macro_grounded_block: str = ""
    live_quote_block: str = ""
    gex_block: str = ""
    market_sentiment_block: str = ""
    social_block: str = ""
    earnings_fact_block: str = ""


def _fetch_safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.debug(f"Live fetch task failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Fresh + macro news
# ---------------------------------------------------------------------------

def pull_macro_news(date_str: str) -> str:
    """Pull macro/VIX/sector news for *date_str*."""
    from src.clients import news_client
    try:
        return news_client.get_macro_news(date_str) or ""
    except Exception as e:
        logger.debug(f"Macro news fetch failed: {e}")
        return ""


def pull_fresh_news(ticker: str, date_str: str) -> str:
    """Fetch fresh ticker-specific news via search + Alpaca."""
    from src.clients import news_client
    try:
        return news_client.get_fresh_news(ticker, date_str) or ""
    except Exception as e:
        logger.debug(f"Fresh news fetch failed for {ticker}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Quote + GEX (disk-cached)
# ---------------------------------------------------------------------------

def _load_or_fetch_quote(ticker: str, quote_path: Path, tdir: Path, date_str: str) -> str:
    """Return a live-quote string, loading from *quote_path* cache when fresh."""
    from src.clients import options_client

    if quote_path.exists():
        import time as _time
        try:
            if _time.time() - quote_path.stat().st_mtime < 300:
                q = json.loads(quote_path.read_text(encoding="utf-8")).get("quote", "")
                if q:
                    return q
        except Exception:
            pass

    try:
        result = options_client.get_realtime_quote(ticker) or ""
        if result:
            safe_path = tdir / f"{ticker.replace(':', '_')}_quote.json"
            safe_path.write_text(
                json.dumps({"ticker": ticker, "quote": result, "date": date_str}, indent=2),
                encoding="utf-8",
            )
        return result
    except Exception as e:
        logger.warning(f"[{ticker}] Live quote pre-fetch failed: {e}")
        return ""


def _load_or_fetch_gex(ticker: str, gex_path: Path, tdir: Path, date_str: str) -> str:
    """Return a GEX block string, loading from *gex_path* cache when fresh."""
    from src.clients import options_client

    if gex_path.exists():
        try:
            return json.loads(gex_path.read_text(encoding="utf-8")).get("gex", "")
        except Exception:
            pass

    try:
        result = options_client.format_gex_block(ticker) or ""
        if result:
            safe_path = tdir / f"{ticker.replace(':', '_')}_gex.json"
            safe_path.write_text(
                json.dumps({"ticker": ticker, "gex": result, "date": date_str}, indent=2),
                encoding="utf-8",
            )
        return result
    except Exception as e:
        logger.warning(f"[{ticker}] GEX block failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_live_context(
    ticker: str,
    tdir: Path,
    raw_dir: Path,
    date_str: str,
    dossier_path: Path,
    paths: dict,
    dw_dict: dict,
) -> LiveContext:
    """Fetch all live context for *ticker* in parallel and return a :class:`LiveContext`.

    Disk-cached quote/GEX snapshots are loaded when available; fresh data is
    written back to disk for auditability.
    """
    from src.clients import alphavantage_client, earnings_client, finnhub_client, google_grounding_client, options_client

    # Let live tools fall back to this ticker if the model omits the arg
    options_client.set_active_ticker(ticker)

    ctx = LiveContext()

    # Load earnings synchronously (cheap, synchronous, deterministic)
    ctx.earnings_fact_block = earnings_client.format_earnings_fact_block(ticker, dw=dw_dict)

    logger.info(f"[{ticker}] ⚡ Parallel live context retrieval (News, AlphaVantage, Finnhub, Google Grounding)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        fut_news = executor.submit(_fetch_safe, pull_fresh_news, ticker, date_str)
        fut_av = executor.submit(_fetch_safe, alphavantage_client.format_av_block, ticker)
        fut_inst = executor.submit(_fetch_safe, finnhub_client.format_finnhub_institutional_block, ticker)
        fut_ground = executor.submit(
            _fetch_safe,
            google_grounding_client.format_grounded_block,
            ticker,
            f"{ticker} stock latest news, analyst rating changes, and earnings outlook this week",
        )
        fut_macro_ground = executor.submit(
            _fetch_safe,
            google_grounding_client.format_grounded_block,
            "MACRO",
            f"US Macroeconomic news today {date_str}, including any CPI, NFP, or FOMC data released",
        )

        ctx.fresh_news = fut_news.result() or "No fresh news could be retrieved via live search."
        ctx.av_block = fut_av.result() or ""
        ctx.institutional_block = fut_inst.result() or ""
        ctx.grounded_block = fut_ground.result() or ""
        ctx.macro_grounded_block = fut_macro_ground.result() or ""

    # Quote + GEX (disk-cached, sequential — both hit the same options API)
    ctx.live_quote_block = _load_or_fetch_quote(ticker, paths["quote"], tdir, date_str)
    if not ctx.live_quote_block:
        ctx.live_quote_block = (
            "(unavailable — say so explicitly and anchor on the Data Window bar close; "
            "do NOT invent a live price)"
        )

    ctx.gex_block = _load_or_fetch_gex(ticker, paths["gex"], tdir, date_str)

    return ctx


# ---------------------------------------------------------------------------
# Persist deep context snapshot
# ---------------------------------------------------------------------------

def save_deep_context(
    tdir: Path,
    ticker: str,
    date_str: str,
    ctx: LiveContext,
    tv_strat_block: str,
) -> None:
    """Write ``{ticker}_deep_context.json`` for auditability."""
    payload = {
        "ticker": ticker,
        "date": date_str,
        "fresh_news": ctx.fresh_news,
        "macro_news": "",  # populated by caller from module-level macro_news
        "market_sentiment": ctx.market_sentiment_block,
        "av_block": ctx.av_block,
        "social_block": ctx.social_block,
        "earnings_fact_block": ctx.earnings_fact_block,
        "institutional_block": ctx.institutional_block,
        "grounded_block": ctx.grounded_block,
        "macro_grounded_block": ctx.macro_grounded_block,
        "live_quote_block": ctx.live_quote_block,
        "gex_block": ctx.gex_block,
        "tv_strat_block": tv_strat_block,
    }
    try:
        safe = ticker.replace(":", "_")
        (tdir / f"{safe}_deep_context.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.debug(f"[{ticker}] Failed to write deep_context.json: {e}")
