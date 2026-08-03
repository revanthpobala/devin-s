"""
src/clients/news_researcher.py

Multi-pass local-LLM news synthesis (Free).
Replaces the old GPT-Researcher scraping loop with a faster, API-based headline synthesis.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Tuple

_DDGS_SEM = threading.Semaphore(int(os.getenv("DDGS_CONCURRENCY", "2")))
_search_cache = {}  # (ticker, date) -> results


def _ddgs_search_cached(ticker, date_str, query):
    key = (ticker.upper(), date_str, query)
    if key in _search_cache:
        return _search_cache[key]
    for attempt in range(3):
        try:
            with _DDGS_SEM:
                res = search_web(query, max_results=5, backend="auto")
            if res:
                _search_cache[key] = res
                return res
        except Exception:
            pass
        time.sleep(2**attempt)  # 1s, 2s, 4s backoff on empty/failure
    _search_cache[key] = []
    return []


from src.clients.llm_client import query_local_llm
from src.clients.news_client import _fetch_finnhub_news, get_ticker_news
from src.clients.search_client import search_web

logger = logging.getLogger(__name__)


def run_news_research(
    ticker: str, date_str: str, out_dir: Path, data_window: dict
) -> Tuple[Path, bool, str]:
    """
    Multi-pass local-LLM synthesis pipeline.
    Pass 1: Extract sentiment + catalyst from Finnhub, Alpaca, Yahoo, DDGS.
    Pass 2: Synthesize into dated dossier.
    Pass 3: Cross-check against Data Window -> confirm/contradict.

    Returns: (dossier_path, contradicts_bool, sentiment_label)
    """
    out_path = out_dir / f"{ticker}_news_research.md"

    # ---------------------------------------------------------
    # Pass 1: Gather raw context from sources
    # ---------------------------------------------------------
    raw_texts = []

    # 1. DDGS Search -> REMOVE or gate off (returns Wikipedia junk on this ddgs version)
    # results = _ddgs_search_cached(ticker, date_str, f"{ticker} stock news catalyst")
    # for r in results: ddgs_text += f"- [{r.get('title')}] {r.get('body')}\n"
    # if ddgs_text: raw_texts.append(f"SOURCE: Web Search (DDGS)\n{ddgs_text}")

    # 2. Finnhub
    finnhub_text = _fetch_finnhub_news(ticker, days=3)
    if finnhub_text:
        raw_texts.append(f"SOURCE: Finnhub\n{finnhub_text}")

    # 3. Alpaca / Yahoo (get_ticker_news handles this fallback chain)
    alpaca_data = get_ticker_news(ticker, days=3)
    if alpaca_data and alpaca_data.get("raw_news"):
        raw_texts.append(f"SOURCE: Alpaca/Yahoo\n{alpaca_data['raw_news']}")

    combined_raw = "\n\n".join(raw_texts)

    if not combined_raw.strip():
        logger.warning(f"[{ticker}] No news found from any free source.")
        # Write empty dossier anyway so it doesn't fail later
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# {ticker} — News Research Dossier\n**Date:** {date_str}\n\n(No news found.)")
        return (out_path, False, "NEUTRAL")

    # ---------------------------------------------------------
    # Pass 2: Synthesize Dossier
    # ---------------------------------------------------------
    logger.info(f"[{ticker}] News Pass 2: Synthesizing dossier from {len(raw_texts)} sources...")
    sys_prompt = "You are a senior financial analyst. Synthesize the provided raw news into a concise, dated dossier."
    usr_prompt = f"TICKER: {ticker}\nDATE: {date_str}\n\nRAW NEWS:\n{combined_raw}\n\nWrite a 2-3 paragraph markdown dossier. Deduplicate events, highlight the main catalyst, and state the overall sentiment."

    dossier = query_local_llm(sys_prompt, usr_prompt, max_tokens=1024, disable_thinking=True)
    if not dossier:
        logger.warning(f"[{ticker}] Local LLM failed to synthesize dossier. Using raw text.")
        dossier = combined_raw

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {ticker} — News Research Dossier\n**Date:** {date_str}\n\n{dossier}")

    # ---------------------------------------------------------
    # Pass 3: Reconcile with Data Window
    # ---------------------------------------------------------
    logger.info(f"[{ticker}] News Pass 3: Cross-checking against Technicals...")
    tech_bias = data_window.get("chosen_side", "NONE")

    sys_prompt_recon = "You are a strict risk manager. Output ONLY JSON."
    usr_prompt_recon = (
        f"TICKER: {ticker}\n"
        f"TECHNICAL BIAS: {tech_bias} (long/short)\n"
        f"DOSSIER:\n{dossier}\n\n"
        f"Does the news fundamentally CONTRADICT the technical bias? (e.g. bias is 'long' but news is a severe downgrade/lawsuit). "
        f'Output JSON: {{"contradicts": true/false, "sentiment": "BULLISH/BEARISH/NEUTRAL", "reason": "<brief>"}}'
    )

    recon_json = query_local_llm(
        sys_prompt_recon, usr_prompt_recon, json_mode=True, max_tokens=256, disable_thinking=True
    )

    contradicts = False
    sentiment = "NEUTRAL"
    if recon_json:
        try:
            if "</think>" in recon_json:
                recon_json = recon_json.split("</think>")[-1]
            start, end = recon_json.find("{"), recon_json.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(recon_json[start : end + 1])
                contradicts = bool(parsed.get("contradicts", False))
                sentiment = parsed.get("sentiment", "NEUTRAL").upper()
                logger.info(
                    f"[{ticker}] Reconcile -> Contradicts: {contradicts}, Sentiment: {sentiment}"
                )
        except Exception as e:
            logger.warning(f"[{ticker}] Reconcile pass failed to parse JSON: {e}")

    return (out_path, contradicts, sentiment)
