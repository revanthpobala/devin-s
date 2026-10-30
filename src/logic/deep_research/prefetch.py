"""prefetch.py — parallel pre-fetch of deterministic quant analytics for deep research context."""
from __future__ import annotations

import concurrent.futures
import logging

logger = logging.getLogger(__name__)

# In-memory cache to prevent duplicate prefetch across multi-pass pipeline runs
_PREFETCH_CACHE: dict[tuple[str, str], dict] = {}


def prefetch_deep_research_context(ticker: str, date_str: str) -> dict:
    """Pre-fetch deterministic technical analytics and quantitative plugins in parallel.

    Options chains are NOT pre-fetched blindly; the LLM explicitly requests
    specific expiration chains on-demand via the ``fetch_options_chain`` tool.
    Cached per (ticker, date_str) to prevent redundant computation across pipeline stages.
    """
    tkr_u = ticker.upper().strip()
    cache_key = (tkr_u, date_str)
    if cache_key in _PREFETCH_CACHE:
        return dict(_PREFETCH_CACHE[cache_key])

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

    # Optimized task list without duplicate executions
    tasks = [
        ("quant_plugins", run_quantitative_plugin_tool, tkr_u, "order_flow", date_str),
        ("candlestick_patterns", run_quantitative_plugin_tool, tkr_u, "candlestick_patterns", date_str),
        ("tastytrade_volatility", run_quantitative_plugin_tool, tkr_u, "tastytrade_volatility", date_str),
        ("schwab_analytics", run_quantitative_plugin_tool, tkr_u, "schwab_analytics", date_str),
        ("prior_research", fetch_prior_research_tool, tkr_u, 14, date_str),
        ("historical_analytics", fetch_historical_zone_and_regime_analytics_tool, tkr_u, 60, date_str),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(_fetch_task, *task) for task in tasks]
        for fut in concurrent.futures.as_completed(futures):
            try:
                key, value = fut.result()
                results[key] = value
            except Exception as e:
                logger.debug(f"[{tkr_u}] Prefetch future error: {e}")

    # Monte Carlo simulation is available on demand via run_monte_carlo_simulation tool
    results["monte_carlo"] = (
        "• *Note: Multi-horizon Monte Carlo simulation cones (20,000 paths) are available on demand via the `run_monte_carlo_simulation` tool.*"
    )

    _PREFETCH_CACHE[cache_key] = dict(results)
    return results
