"""debate.py — Multi-Agent Bull vs Bear debate (local LLM, free path)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from src.clients.llm_client import query_local_llm

logger = logging.getLogger(__name__)

_BULL_SYS = (
    "You are a rigorous, evidence-based Bullish Technical & Fundamental Analyst operating under Ponytail Finance rules (Occam's Razor, minimal bloat, hard math). "
    "Your job is to identify valid positive drivers, catalyst timelines, and support levels for this ticker. "
    "CRITICAL RULES: Every claim must cite an exact Data Window field name or verified news item. "
    "Never fabricate moving average levels or invent technical indicators. Never contradict the pre-decoded Section 2d-1 engine math. "
    "Output your bull case in a concise, punchy markdown format."
)

_BEAR_SYS = (
    "You are a skeptical, disciplined Bearish Technical & Fundamental Analyst operating under Ponytail Finance rules (Occam's Razor, minimal bloat, hard math). "
    "Your job is to identify risks, overhead supply resistance, catalyst timing risks, and valuation/extension headwinds. "
    "CRITICAL RULES: Every claim must cite an exact Data Window field name or verified news item. "
    "Never fabricate moving average levels or invent technical indicators. Never contradict the pre-decoded Section 2d-1 engine math. "
    "Output your bear case in a concise, punchy markdown format."
)


@dataclass
class DebateResult:
    bull_case: str = ""
    bear_case: str = ""
    bull_rebuttal: str = ""
    bear_rebuttal: str = ""


def _run_agent(sys_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
    return query_local_llm(
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        use_openrouter=False,
        use_tools=False,
        disable_thinking=True,
        max_tokens=max_tokens,
    )


def run_debate(
    ticker: str,
    date_str: str,
    debate_payload: str,
    tdir: Path,
) -> DebateResult:
    """Run Bull/Bear debate + rebuttals (local LLM, 2 workers each round).

    Uses ``{ticker}_debate_v2.json`` as a cache so re-runs skip the LLM calls.
    Returns a :class:`DebateResult`.
    """
    import concurrent.futures
    import hashlib

    safe = ticker.replace(":", "_")
    cache_file = tdir / f"{safe}_debate_v2.json"
    payload_hash = hashlib.sha256(debate_payload.encode("utf-8")).hexdigest()[:16]

    if cache_file.exists():
        try:
            d = json.loads(cache_file.read_text(encoding="utf-8"))
            if d.get("payload_hash") == payload_hash:
                logger.info(f"[{ticker}] Found cached Multi-Agent Debate with matching DW hash — skipping to Pass 2...")
                return DebateResult(
                    bull_case=d.get("bull_case", ""),
                    bear_case=d.get("bear_case", ""),
                    bull_rebuttal=d.get("bull_rebuttal", ""),
                    bear_rebuttal=d.get("bear_rebuttal", ""),
                )
            else:
                logger.info(f"[{ticker}] Cached debate has stale payload hash ({d.get('payload_hash')} != {payload_hash}), re-running debate...")
        except Exception as e:
            logger.warning(f"[{ticker}] Failed to read cached debate ({e}), recomputing...")

    logger.info(f"[{ticker}] Running Bull and Bear Agents (workers=2)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_bull = executor.submit(_run_agent, _BULL_SYS, f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}", 4096)
        f_bear = executor.submit(_run_agent, _BEAR_SYS, f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}", 4096)
        bull_case = f_bull.result()
        bear_case = f_bear.result()

    logger.info(f"[{ticker}] Running Rebuttal Agents (workers=2)...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_bull_reb = executor.submit(
            _run_agent,
            _BULL_SYS + " You are now in the rebuttal phase. Read the Bear Case below and systematically destroy their arguments.",
            f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}\n\n--- THE BEAR CASE ---\n{bear_case}",
            4096,
        )
        f_bear_reb = executor.submit(
            _run_agent,
            _BEAR_SYS + " You are now in the rebuttal phase. Read the Bull Case below and systematically destroy their arguments.",
            f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}\n\n--- THE BULL CASE ---\n{bull_case}",
            4096,
        )
        bull_rebuttal = f_bull_reb.result()
        bear_rebuttal = f_bear_reb.result()

    # Cache to disk
    try:
        cache_file.write_text(
            json.dumps({
                "payload_hash": payload_hash,
                "bull_case": bull_case,
                "bear_case": bear_case,
                "bull_rebuttal": bull_rebuttal,
                "bear_rebuttal": bear_rebuttal,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[{ticker}] Failed to cache debate: {e}")

    return DebateResult(
        bull_case=bull_case,
        bear_case=bear_case,
        bull_rebuttal=bull_rebuttal,
        bear_rebuttal=bear_rebuttal,
    )


def format_debate_block(result: DebateResult) -> str:
    return (
        "--- 2f. LOCAL RESEARCHER DEBATE (MULTI-AGENT) ---\n"
        "Local analysts conducted a debate on this ticker citing Data Window ground truth:\n\n"
        "🟢 BULL CASE:\n"
        f"{result.bull_case}\n\n"
        "🔴 BEAR CASE:\n"
        f"{result.bear_case}\n\n"
        "🟢 BULL REBUTTAL:\n"
        f"{result.bull_rebuttal}\n\n"
        "🔴 BEAR REBUTTAL:\n"
        f"{result.bear_rebuttal}\n\n"
        "You are the Portfolio Manager (Judge). You MUST settle these disagreements in your final thesis and formulate the trade plan."
    )
