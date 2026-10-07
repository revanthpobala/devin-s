"""pass2.py — Dual-model Pass 2 LLM inference (Model A proprietary + Model B independent)."""
from __future__ import annotations

import concurrent.futures
import logging
import os
from dataclasses import dataclass

from src.clients.llm_client import query_local_llm

logger = logging.getLogger(__name__)


@dataclass
class Pass2Result:
    response: str = ""        # Model A (proprietary / Pine Gem)
    ind_response: str = ""    # Model B (independent macro)


def _detect_provider(use_remote: bool) -> tuple[bool, str]:
    """Return ``(will_use_remote, provider_label)``."""
    if not use_remote:
        return False, "local-gpu (llama-cpp-server)"
    has_key = bool(
        os.getenv("META_AI_API_KEY") or os.getenv("OPENROUTER_KEY") or os.getenv("OPENROUTER_API_KEY")
    )
    if not has_key:
        return False, "local-gpu (no remote key — forced sequential)"
    meta_key = os.getenv("META_AI_API_KEY")
    if meta_key:
        model = os.getenv("META_LLM", "muse-spark-1.2-contributor")
    else:
        model = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3")
    return True, f"remote API ({model})"


def _call_model(
    system_prompt: str,
    user_prompt: str,
    image_paths: list,
    date_str: str,
    use_remote: bool,
    label: str,
    ticker: str,
    is_independent: bool = False,
    max_tool_batches: Optional[int] = None,
    stats_out: Optional[dict] = None,
) -> str:
    """Run one LLM pass: remote first, local-GPU fallback."""
    resp = None
    tool_ctx = f"The simulated date is {date_str}. Treat {date_str} as the present day.{' [INDEPENDENT]' if is_independent else ''}"
    if use_remote:
        try:
            resp = query_local_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=False,
                use_openrouter=True,
                image_paths=image_paths,
                use_tools=True,
                max_tokens=16384,
                summarize_tool_context=tool_ctx,
                max_tool_batches=max_tool_batches,
                stats_out=stats_out,
            )
        except Exception as e:
            logger.warning(f"[{ticker}] Remote API inference for {label} failed ({e}) — falling back to Local GPU!")
            resp = None

    if not resp:
        logger.info(f"[{ticker}] Executing {label} with Local GPU LLM Server...")
        resp = query_local_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=False,
            use_openrouter=False,
            image_paths=image_paths,
            use_tools=True,
            disable_thinking=True,
            max_tokens=16384,
            summarize_tool_context=tool_ctx,
            max_tool_batches=max_tool_batches,
            stats_out=stats_out,
        )
    return resp or ""


def run_pass2(
    ticker: str,
    date_str: str,
    system_prompt: str,
    user_prompt: str,
    system_prompt_independent: str,
    independent_user_prompt: str,
    image_paths_model_a: list,
    image_paths_model_b: list,
    force_local: bool = False,
    skip_model_b: bool = False,
    max_tool_batches_a: int = 3,
    max_tool_batches_b: int = 3,
    stats_a: Optional[dict] = None,
    stats_b: Optional[dict] = None,
) -> Pass2Result:
    """Run Model A (and conditionally Model B), concurrent when remote key is present."""
    use_remote = not force_local
    will_use_remote, provider_label = _detect_provider(use_remote)

    logger.info(
        f"[{ticker}] Pass 2 — Model A (Pine Gem){' + Model B (Independent)' if not skip_model_b else ' (Model B Skipped)'}: {provider_label}"
    )

    if skip_model_b:
        logger.info(f"[{ticker}] Pass 2 — launching Model A (max tool batches: {max_tool_batches_a})...")
        response = _call_model(
            system_prompt, user_prompt, image_paths_model_a,
            date_str, will_use_remote, "Model A (Pine Gem)", ticker, False,
            max_tool_batches=max_tool_batches_a, stats_out=stats_a,
        )
        return Pass2Result(response=response, ind_response="")

    if will_use_remote:
        logger.info(f"[{ticker}] Pass 2 — launching Models A & B CONCURRENTLY ({provider_label})...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(
                _call_model, system_prompt, user_prompt, image_paths_model_a,
                date_str, True, "Model A (Pine Gem)", ticker, False,
                max_tool_batches_a, stats_a,
            )
            fut_b = executor.submit(
                _call_model, system_prompt_independent, independent_user_prompt, image_paths_model_b,
                date_str, True, "Model B (Independent)", ticker, True,
                max_tool_batches_b, stats_b,
            )
            response = fut_a.result()
            ind_response = fut_b.result()
    else:
        logger.info(f"[{ticker}] Pass 2 — launching Model A sequentially on Local GPU (max tool batches: {max_tool_batches_a})...")
        response = _call_model(
            system_prompt, user_prompt, image_paths_model_a,
            date_str, False, "Model A (Pine Gem)", ticker, False,
            max_tool_batches=max_tool_batches_a, stats_out=stats_a,
        )
        logger.info(f"[{ticker}] Pass 2 — launching Model B sequentially on Local GPU (max tool batches: {max_tool_batches_b})...")
        ind_response = _call_model(
            system_prompt_independent, independent_user_prompt, image_paths_model_b,
            date_str, False, "Model B (Independent)", ticker, True,
            max_tool_batches=max_tool_batches_b, stats_out=stats_b,
        )

    return Pass2Result(response=response, ind_response=ind_response)
