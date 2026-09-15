"""prefetch.py — parallel pre-fetch of deterministic quant analytics for deep research context."""
from __future__ import annotations

import concurrent.futures
import logging

logger = logging.getLogger(__name__)


def prefetch_deep_research_context(ticker: str, date_str: str) -> dict:
    """Pre-fetch deterministic technical analytics and quantitative plugins in parallel.

    Options chains are NOT pre-fetched blindly; the LLM explicitly requests
    specific expiration chains on-demand via the ``fetch_options_chain`` tool.
    """
    from src.clients.llm_client import (
        fetch_historical_zone_and_regime_analytics_tool,
        fetch_prior_research_tool,
        run_quantitative_plugin_tool,
    )

    results: dict = {}

    def _fetch_task(key, fn, *args, **kwargs):
        try:
            return key, fn(*args, **kwargs)
        except Exception as e:
            return key, f"Unavailable ({e})"

    tasks = [
        ("monte_carlo", run_quantitative_plugin_tool, ticker, "monte_carlo", date_str),
        ("quant_plugins", run_quantitative_plugin_tool, ticker, "all", date_str),
        ("candlestick_patterns", run_quantitative_plugin_tool, ticker, "candlestick_patterns", date_str),
        ("tastytrade_volatility", run_quantitative_plugin_tool, ticker, "tastytrade_volatility", date_str),
        ("prior_research", fetch_prior_research_tool, ticker, 14, date_str),
        ("historical_analytics", fetch_historical_zone_and_regime_analytics_tool, ticker, 60, date_str),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(_fetch_task, *task) for task in tasks]
        for fut in concurrent.futures.as_completed(futures):
            try:
                key, value = fut.result()
                results[key] = value
            except Exception as e:
                logger.debug(f"[{ticker}] Prefetch future error: {e}")

    return results
