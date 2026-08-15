from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any

from openai import OpenAI

from src import config  # Ensure load_dotenv is triggered
from src.clients.search_client import search_web

logger = logging.getLogger(__name__)

API_URL = os.getenv("LOCAL_LLM_API_URL", "http://127.0.0.1:8000/v1")

_LOCAL_LLM_CONCURRENCY = max(1, int(getattr(config, "LLM_LOCAL_CONCURRENCY", 4)))
_local_llm_semaphore = threading.Semaphore(_LOCAL_LLM_CONCURRENCY)


def _create_completion(client, provider: str, **kwargs):
    """
    Wrap chat.completions.create with an optional non-blocking retry mechanism
    or a strict block to prevent thread pool starvation during heavy local inference.
    """
    if provider == "local":
        # Standard blocking approach (ensure your ThreadPool is large enough,
        # e.g., max_workers=20+, so I/O tasks always have free threads)
        with _local_llm_semaphore:
            return client.chat.completions.create(**kwargs)

    return client.chat.completions.create(**kwargs)


import io

def encode_image_to_base64(image_path: str, max_dim: int = 640) -> str:
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_earnings_calendar",
            "description": "Deterministic ground-truth lookup for the next confirmed earnings date and days remaining. ALWAYS use this deterministic tool instead of web search when verifying earnings gates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'AAPL')",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Performs concurrent multi-source web search (Brave + DuckDuckGo + Parallel.ai). Use this for non-deterministic catalyst research: breaking news, analyst commentary, product announcements, and sentiment. For deterministic earnings dates, use fetch_earnings_calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. ALWAYS include the current date/year! (e.g., 'AAPL product AI announcement August 2026', 'site:finviz.com AAPL 2026')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_finnhub_news",
            "description": "Fetches recent fundamental company news from Finnhub for a given ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'AMZN')",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_alpaca_news",
            "description": "Fetches recent market news articles from Alpaca for a given ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'AMZN')",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_realtime_quote",
            "description": "Fetches the real-time exact price for a ticker (the LIVE price, which may differ from the closed Data Window).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_options_chain",
            "description": "Fetches live options chain data (bid/ask, delta, gamma) to help define exact strikes and options strategies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["CALL", "PUT"],
                        "description": "Call or Put chain",
                    },
                    "strike_low": {
                        "type": "number",
                        "description": "Lower bound of strike prices (derive from chart zones)",
                    },
                    "strike_high": {
                        "type": "number",
                        "description": "Upper bound of strike prices (derive from chart zones)",
                    },
                    "min_dte": {
                        "type": "integer",
                        "description": "Minimum days to expiry (default 14, or 120+ for multi-month / LEAPS)",
                    },
                    "max_dte": {
                        "type": "integer",
                        "description": "Maximum days to expiry (default 120, or 365-730 for long-term LEAPS)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_tradingview_options_finder",
            "description": "Opens TradingView Options Suite for the ticker, applies custom parameters (prediction_period: 'Next week', 'Next 2 weeks', 'Next month', 'Next 3 months', 'Next 6 months'; expected_move; min_volume; moneyness), and returns pre-computed multi-leg options spreads (Bull Call Spread, Bull Put Spread, Jade Lizard) with exact Max Profit, Max Loss, R:R, and Breakevens across both tactical (30d) and multi-quarter swing horizons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g. AAPL, HOOD, UBER)",
                    },
                    "prediction_period": {
                        "type": "string",
                        "enum": ["Next week", "Next 2 weeks", "Next month", "Next 3 months", "Next 6 months"],
                        "description": "Target time horizon / expiry window ('Next month' for 30d tactical swing, 'Next 3 months' or 'Next 6 months' for multi-quarter swing/LEAPS).",
                    },
                    "expected_move": {
                        "type": "string",
                        "enum": ["+5% to +10%", "+10% to +15%", "+15% to +20%", "-5% to -10%", "-10% to -15%"],
                        "description": "Expected price direction and percentage magnitude.",
                    },
                    "min_volume": {
                        "type": "string",
                        "enum": ["100 to 500", "500 to 2 K", "2 K to 10 K", "Above 10 K"],
                        "description": "Minimum option liquidity tier.",
                    },
                    "moneyness": {
                        "type": "string",
                        "enum": ["Out of the money", "At the money", "In the money"],
                        "description": "Moneyness filter preference.",
                    },
                    "capture_volume_charts": {
                        "type": "boolean",
                        "description": "Whether to capture high-res dialog screenshots of the Volume Heatmap and Expiration charts.",
                    },
                },
                "required": ["ticker", "prediction_period", "expected_move"],
            },
        },
    },
]


def execute_tool_call(tool_call, date_str: str = None):
    """Executes the mapped python function for a given tool call with TTL artifact caching."""
    from src.data.artifact_cache import artifact_cache
    
    function_name = tool_call.function.name
    date_str = date_str or time.strftime("%Y-%m-%d")

    try:
        args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse arguments for tool {function_name}: {e}")
        return f"Error: Invalid JSON arguments provided for tool '{function_name}': {str(e)}. Please correct your JSON and try again."

    ticker = (args.get("ticker") or args.get("symbol") or "GLOBAL").upper()

    # Check TTL Artifact Cache first
    cache_key = function_name
    if function_name in ("search_web", "fetch_prediction_market"):
        query_slug = str(args.get("query", ""))[:30].replace(" ", "_").replace("/", "_")
        cache_key = f"{function_name}_{query_slug}"
    elif function_name == "scrape_tradingview_options_finder":
        period_slug = str(args.get("prediction_period", "month")).replace(" ", "_")
        move_slug = str(args.get("expected_move", "5to10")).replace("%", "").replace(" ", "").replace("+", "").replace("-", "down_")
        cache_key = f"tv_options_{period_slug}_{move_slug}"
    elif function_name == "fetch_options_chain":
        direction_slug = str(args.get("direction", "CALL")).upper()
        min_dte_slug = str(args.get("min_dte", 30))
        max_dte_slug = str(args.get("max_dte", 120))
        cache_key = f"options_chain_{direction_slug}_{min_dte_slug}_{max_dte_slug}"

    cached_val = artifact_cache.get(date_str, ticker, cache_key)
    if cached_val is not None:
        return cached_val

    if function_name == "search_web":
        query = args.get("query")
        logger.info(f"LLM executed tool: search_web(query='{query}')")
        results = search_web(query, max_results=3, backend="auto")
        if not results:
            return "No results found."

        output = f"Search Results for '{query}':\n"
        for r in results:
            output += f"- [{r['title']}] {r['body']}\n"
        artifact_cache.save(date_str, ticker, cache_key, output)
        return output
    elif function_name == "fetch_earnings_calendar":
        from src.clients.earnings_client import format_earnings_fact_block

        logger.info(f"LLM executed deterministic tool: fetch_earnings_calendar(ticker='{ticker}')")
        res_str = format_earnings_fact_block(ticker)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "get_realtime_quote":
        from src.clients.options_client import get_realtime_quote

        logger.info(f"LLM executed tool: get_realtime_quote(ticker='{ticker}')")
        result = get_realtime_quote(ticker)
        res_str = result or "No real-time quote data returned."
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "fetch_options_chain":
        from src.clients.options_client import fetch_options_chain_tool

        logger.info(f"LLM executed tool: fetch_options_chain(ticker='{ticker}')")
        result = fetch_options_chain_tool(**args)
        res_str = result or "No options chain data returned."
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "fetch_finnhub_news":
        from src.clients.news_client import _fetch_finnhub_news
        
        logger.info(f"LLM executed tool: fetch_finnhub_news(ticker='{ticker}')")
        result = _fetch_finnhub_news(ticker, days=3)
        res_str = result or f"No Finnhub news found for {ticker}."
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "fetch_alpaca_news":
        from src.clients.news_client import get_ticker_news
        
        logger.info(f"LLM executed tool: fetch_alpaca_news(ticker='{ticker}')")
        result_dict = get_ticker_news(ticker, days=3)
        res_str = result_dict.get("raw_news") if result_dict and result_dict.get("raw_news") else f"No Alpaca/Yahoo news found for {ticker}."
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "scrape_tradingview_options_finder":
        from src.data.tv_options_scraper import scrape_tv_options_finder_tool

        logger.info(f"LLM executed tool: scrape_tradingview_options_finder(ticker='{ticker}', args={args})")
        res_str = scrape_tv_options_finder_tool(**args)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "fetch_prediction_market":
        from src.clients.kalshi_client import fetch_prediction_market
        query = args.get("query")
        logger.info(f"LLM executed tool: fetch_prediction_market(query='{query}')")
        res_str = fetch_prediction_market(query)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    else:
        logger.warning(f"Unknown tool called: {function_name}")
        return f"Error: Tool '{function_name}' is not supported."


def _extract_text(message) -> str:
    """Return the message content, falling back to reasoning_content for models
    (e.g. Qwen3.x, thinkingmachines/inkling) that emit their actual output there
    when thinking is enabled."""
    content = getattr(message, "content", None)
    if content:
        return content
    extra = getattr(message, "model_extra", None) or {}
    reasoning = (
        extra.get("reasoning_content")
        or getattr(message, "reasoning_content", None)
        or extra.get("reasoning")
        or getattr(message, "reasoning", None)
    )
    if reasoning:
        logger.warning("LLM returned empty content; using reasoning_content as fallback.")
        return reasoning
    return ""


def _extract_json_response(llm_response: str) -> dict:
    """Robust JSON extraction from LLM response with multiple fallback strategies."""
    if not llm_response:
        return {}

    # Strategy 0: Strip any <think>...</think> reasoning trace (some local models
    # emit it even with thinking disabled, or wrap JSON inside it). Keeps only the
    # post-think content so the JSON parse below is never polluted by prose.
    _json_str = llm_response
    if "<think>" in _json_str:
        _json_str = _json_str.split("</think>")[-1]
    elif "<thinking>" in _json_str:
        _json_str = _json_str.split("</thinking>")[-1]

    # Strategy 1: Clean markdown code blocks. Strip any leading "> " quote, the
    # "```json\n" opening fence (and a lone "```" opening), then any trailing
    # "```" closing fence. We strip fences from BOTH ends so a fenced block like
    # "```json\n{...}\n```" reduces to just the JSON.
    json_str = _json_str.strip()
    if json_str.startswith("> "):
        json_str = json_str[2:].strip()
    if json_str.startswith("```json"):
        json_str = json_str[len("```json") :].strip()
    elif json_str.startswith("```"):
        json_str = json_str[3:].strip()
    if json_str.endswith("```"):
        json_str = json_str[:-3].strip()

    # Strategy 2: Find outermost braces
    start_idx = json_str.find("{")
    end_idx = json_str.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_str = json_str[start_idx : end_idx + 1]

    # Strategy 3: Try to parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Strategy 4: Repair a TRUNCATED json_schema output (model hit max_tokens
    # mid-object). Close any unterminated string, then balance braces/brackets,
    # then see if it parses. This rescues the 200-OK-but-malformed case without
    # burning a full retry round-trip.
    repaired = _complete_truncated_json(json_str)
    if repaired is not None:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Strategy 5: Try json_repair as last resort
    try:
        import json_repair

        repaired = json_repair.repair_json(json_str, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
    except Exception:
        pass

    logger.warning(f"Failed to parse LLM JSON response: {llm_response[:100]}...")
    return {}


def _complete_truncated_json(s: str):
    """Best-effort completion of a JSON string cut off mid-generation.

    Returns a balanced JSON string (may still be semantically incomplete, but
    structurally parseable so json.loads succeeds) or None if it looks fine.
    """
    if not s or s.isspace():
        return None
    # Already balanced? leave to json.loads / json_repair.
    if s.count("{") == s.count("}") and s.count("[") == s.count("]"):
        # but an unterminated string still needs closing quote
        if (
            s.rstrip().endswith(('"', "}", "]", "true", "false", "null"))
            or s.rstrip()[-1].isdigit()
        ):
            return None
    # Close an open string if we're mid-value
    work = s
    # Count quotes that are NOT escaped
    quote_count = len(re.findall(r'(?<!\\)"', work))
    if quote_count % 2 == 1:
        work = work + '"'
    # Balance brackets/braces (closing in reverse order of opens)
    open_b = work.count("{") - work.count("}")
    open_p = work.count("[") - work.count("]")
    # trim a dangling comma + value fragment before closing
    work = work.rstrip()
    if work.endswith(","):
        work = work[:-1]
    # if the last token is a key with no value (ends with ':'), drop the whole
    # trailing '"key":' fragment (back to the preceding comma/bracket).
    if work.rstrip().endswith(":"):
        work = re.sub(r',\s*"[^"]*"\s*:\s*$', "", work)
        work = re.sub(r'\s*"[^"]*"\s*:\s*$', "", work)
    # Close brackets in LIFO order: arrays first (innermost), then objects.
    work += "]" * max(open_p, 0) + "}" * max(open_b, 0)
    return work



def _build_client_and_model(use_openrouter: bool, model: str | None = None):
    """Resolve the (client, model, provider_tag) triple from env config.

    LOCAL-FIRST for local research. When the caller passes use_openrouter=False
    (swing triage, position monitor — the "free local" tasks), the LOCAL
    9B server is ALWAYS used, regardless of which remote keys (NVIDIA /
    OpenRouter) are present in the environment. Remote providers are strictly
    OPT-IN: only reached when use_openrouter=True is explicitly requested
    (e.g. paid deep research).

    Priority when use_openrouter=True:
      1. Meta AI              — META_AI_API_KEY set
      2. OpenRouter (paid)    — OPENROUTER_KEY set
      3. NVIDIA NIM           — NVIDIA_API_KEY / NVDIA_DEV_API_KEY (+ model) set
      4. Local Unsloth server (fallback)
    Returns (client, model, provider_tag). provider_tag is "meta" | "openrouter" | "nvidia" | "local".
    """
    meta_key = os.getenv("META_AI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_KEY")

    # LOCAL-FIRST: any use_openrouter=False call uses the local 9B. No remote
    # key can hijack the free local-research path. The local 9B can be
    # slow under load (thinking + grammar + high token budgets), so we use a
    # generous client timeout — a request that's merely queued behind
    # other workers should not be aborted mid-generation.
    if not use_openrouter:
        logger.info("Using Local LLM Server (local-first for local research)...")
        client = OpenAI(
            base_url=API_URL,
            api_key="sk-no-key-required",
            timeout=int(os.getenv("LOCAL_LLM_TIMEOUT", "1800")),
        )
        try:
            models = client.models.list()
            model = models.data[0].id
        except Exception:
            model = "gpt-4"  # matches the llama-server -a alias
        return client, model, "local"

    # Below: explicit opt-in to remote (use_openrouter=True).
    # META FIRST: Meta AI API is checked first if META_AI_API_KEY is available.
    if meta_key:
        resolved_model = model or os.getenv("META_LLM", "muse-spark-1.2-contributor")
        logger.info(
            f"Routing request to Meta AI (Model: {resolved_model})"
        )
        client = OpenAI(
            base_url=os.getenv("META_BASE_URL", "https://api.meta.ai/v1"),
            api_key=meta_key,
            timeout=180,
        )
        return client, resolved_model, "meta"

    # OPENROUTER SECOND: the paid deep-research pass sets use_openrouter=True and
    # points at OPENROUTER_MODEL (default minimax/minimax-m3) — Minimax M3 is the
    # MULTIMODAL model we provisioned for chart vision, available on OpenRouter.
    # NVIDIA NIM is kept as the fallback (its VL model, e.g. nemotron-nano-12b-v2-vl,
    # is a different model than the intended Minimax). Local 9B is last resort.
    if openrouter_key:
        resolved_model = model or os.getenv("OPENROUTER_MODEL", "openrouter/free")
        logger.info(
            f"Routing request to OpenRouter (Model: {resolved_model})"
        )
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key,
            timeout=180,
            default_headers={"HTTP-Referer": "http://localhost", "X-Title": "Swing Triage"},
        )
        return client, resolved_model, "openrouter"



    logger.warning(
        "use_openrouter=True but no remote key/model configured — falling back to Local LLM Server."
    )
    client = OpenAI(
        base_url=API_URL,
        api_key="sk-no-key-required",
        timeout=600,
    )
    try:
        models = client.models.list()
        model = models.data[0].id
    except Exception:
        model = "gpt-4"
    return client, model, "local"


def query_local_llm(
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = False,
    max_tokens: int = 4096,
    use_openrouter: bool = False,
    image_paths: list | None = None,
    use_tools: bool = True,
    disable_thinking: bool = False,
    model: str | None = None,
    json_schema: dict | None = None,
    summarize_tool_context: str | None = None,
) -> str:
    """
    Run inference via local FastAPI Unsloth server, Meta AI, NVIDIA NIM (free), or OpenRouter,
    using the standard OpenAI SDK. Supports native tool calling loops.

    Provider priority (all env-driven, nothing hardcoded to a specific model so a
    NVIDIA free-model swap never breaks the pipeline):
      Meta AI (META_AI_API_KEY) -> OpenRouter (OPENROUTER_KEY) -> NVIDIA NIM (NVIDIA_API_KEY / NVDIA_DEV_API_KEY) -> Local server.
    `model` selects a specific NVIDIA/Meta/OpenRouter model;
    when None, META_LLM (or OPENROUTER_MODEL) env is used.
    """
    try:
        user_content = []
        if user_prompt and user_prompt.strip():
            user_content.append({"type": "text", "text": user_prompt})
        else:
            # NVIDIA NIM rejects empty `content` with HTTP 400. Provide a
            # minimal placeholder when the caller passes nothing (e.g. a
            # vision-only request with no text prompt).
            user_content.append({"type": "text", "text": "Analyze the provided chart and context."})

        if image_paths and isinstance(image_paths, list):
            valid_paths = [p for p in image_paths if os.path.exists(p)]
            for path in valid_paths:
                base64_img = encode_image_to_base64(path)
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_img}"},
                    }
                )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        client, model, provider = _build_client_and_model(use_openrouter, model=model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            # Deterministic sampling: fixed seed + greedy (temp 0) so local runs
            # are reproducible and stable (no sampling noise -> consistent JSON,
            # easier validation). Harmless for remote providers that ignore it.
            "seed": 42,
        }

        # Qwen3.x / reasoning models (e.g. the local Qwen3.5-9B via llama-server)
        # will otherwise burn the token budget on chain-of-thought and return an
        # empty `content` with finish_reason='length'. Disable thinking for direct
        # summarization / extraction tasks. NVIDIA models ignore this extra_body.
        if provider == "local":
            extra_body = kwargs.setdefault("extra_body", {})
            extra_body["cache_prompt"] = True
            if disable_thinking:
                extra_body["chat_template_kwargs"] = {"enable_thinking": False}

        # Only attach tool definitions when the caller explicitly wants tool-calling.
        # NVIDIA's free models change often; some don't support function calling and
        # return HTTP 400 if `tools` is sent. We therefore guard: try with tools, and
        # on a 400/unsupported-tool error, retry the whole loop WITHOUT tools so a
        # model swap never crashes the run.
        if use_tools and provider != "local":
            # For non-local providers, default tools ON but allow opt-out via env
            # (e.g. a text-only / non-tool-capable NVIDIA model).
            if os.getenv("NVIDIA_TOOLS", "on").lower() in ("off", "false", "0"):
                use_tools = False

        attach_tools = use_tools
        if attach_tools:
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"

        if provider == "local":
            # STRUCTURED OUTPUT (LOCAL). Now that thinking is OFF server-side
            # (--reasoning off, enforced by enable_thinking:False), the model never
            # emits a leading <think> trace, so the local GBNF grammar is SAFE.
            # Enforcing a strict json_schema is the single strongest reliability
            # tool: the model structurally cannot emit invalid/unparseable JSON.
            # We pass it via the native response_format json_schema; llama-server
            # compiles it to GBNF. Fall back to loose json_object if no schema.
            if json_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "triage", "schema": json_schema},
                }
            elif json_mode:
                kwargs["response_format"] = {"type": "json_object"}
        else:
            # REMOTE providers (NVIDIA NIM / OpenRouter) support strict json_schema
            # without the local GBNF/<think:6124c78e> conflict, so prefer it for guaranteed JSON.
            if json_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "triage", "schema": json_schema},
                }
            elif json_mode:
                kwargs["response_format"] = {"type": "json_object"}

        # Tool execution loop
        MAX_TOOL_CALLS = 10
        tool_call_count = 0

        while tool_call_count < MAX_TOOL_CALLS:
            try:
                if not attach_tools and provider == "local":
                    kwargs["stream"] = True
                    stream_response = _create_completion(client, provider, **kwargs)
                    collected_chunks = []
                    finish_reason = None
                    for chunk in stream_response:
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if hasattr(delta, "content") and delta.content:
                                collected_chunks.append(delta.content)
                            if chunk.choices[0].finish_reason:
                                finish_reason = chunk.choices[0].finish_reason
                    full_content = "".join(collected_chunks)

                    class _DummyMessage:
                        def __init__(self, content):
                            self.content = content
                            self.tool_calls = None

                    class _DummyChoice:
                        def __init__(self, content, finish_reason):
                            self.message = _DummyMessage(content)
                            self.finish_reason = finish_reason

                    class _DummyResponse:
                        def __init__(self, content, finish_reason):
                            self.choices = [_DummyChoice(content, finish_reason)]

                    response = _DummyResponse(full_content, finish_reason)
                else:
                    response = _create_completion(client, provider, **kwargs)
            except Exception as e:
                # NVIDIA model doesn't support tools (HTTP 400) — retry without them.
                if attach_tools and ("400" in str(e) or "tool" in str(e).lower()):
                    logger.warning(
                        f"Tool calling rejected by {provider} ({e}); retrying without tools."
                    )
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    attach_tools = False
                    response = _create_completion(client, provider, **kwargs)
                else:
                    raise
            message = response.choices[0].message

            # If the model wants to call tools
            if message.tool_calls:
                # Add the assistant's tool_calls message to the history
                messages.append(message)
                
                # Extract simulated date if present in context
                sim_date = None
                if summarize_tool_context:
                    import re
                    m_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", summarize_tool_context)
                    if m_date:
                        sim_date = m_date.group(1)

                for tool_call in message.tool_calls:
                    tool_result = execute_tool_call(tool_call, date_str=sim_date)
                    tool_result_str = tool_result if isinstance(tool_result, str) else str(tool_result)

                    if summarize_tool_context and tool_call.function.name in ("search_web", "fetch_finnhub_news", "fetch_alpaca_news") and len(tool_result_str) > 200:
                        logger.info(f"Summarizing raw output of {tool_call.function.name} locally to filter hallucinations...")
                        sys_prompt = "You are a strict data analyst. You are provided with raw news/web search data. Summarize the key catalysts, fundamental data, and sentiment concisely. " + summarize_tool_context
                        usr_prompt = f"RAW TOOL OUTPUT:\n{tool_result_str}\n\nSummarize the core facts."
                        
                        summary = query_local_llm(
                            system_prompt=sys_prompt,
                            user_prompt=usr_prompt,
                            use_openrouter=False, # Force local model
                            use_tools=False,      # No recursive tools
                            disable_thinking=True,
                            max_tokens=1024,
                        )
                        if summary:
                            tool_result_str = f"[LOCAL LLM SYNTHESIS]:\n{summary}"
                        else:
                            logger.warning(f"Local summarization of {tool_call.function.name} failed, falling back to raw output.")

                    # Append the tool's response to the history
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.function.name,
                            "content": tool_result_str,
                        }
                    )

                tool_call_count += 1
                logger.info(
                    f"Tool execution loop {tool_call_count} complete. Requesting next action from LLM..."
                )
            else:
                # Model returned a final string response
                content = _extract_text(message)
                finish_reason = getattr(response.choices[0], "finish_reason", None)

                # Check if output was truncated due to output token limit (finish_reason='length')
                full_content = content or ""
                cont_attempts = 0

                MAX_CONT_ATTEMPTS = int(os.getenv("MAX_CONT_ATTEMPTS", "10"))
                while finish_reason == "length":
                    cont_attempts += 1
                    if cont_attempts > MAX_CONT_ATTEMPTS:
                        logger.warning(f"Max auto-continuation passes ({MAX_CONT_ATTEMPTS}) reached. Aborting continuation.")
                        break

                    logger.warning(
                        f"[{provider}:{model}] Output truncated (finish_reason='length') at {len(full_content)} chars. "
                        f"Auto-continuation pass {cont_attempts}..."
                    )

                    # Build continuation payload (without tools)
                    kwargs_cont = dict(kwargs)
                    kwargs_cont.pop("tools", None)
                    kwargs_cont.pop("tool_choice", None)

                    cont_messages = list(messages)
                    if full_content.strip():
                        cont_messages.append({"role": "assistant", "content": full_content})
                        cont_messages.append({
                            "role": "user",
                            "content": "Your response was cut off due to the model output token limit. Please CONTINUE your analysis from the exact sentence/character where you stopped. Do NOT repeat headings or sections you already wrote."
                        })
                    else:
                        cont_messages.append({
                            "role": "user",
                            "content": "Your previous response hit the token limit before outputting the final report. Please SKIP your reasoning preamble and output the final required format directly."
                        })
                    kwargs_cont["messages"] = cont_messages

                    try:
                        cont_response = _create_completion(client, provider, **kwargs_cont)
                        cont_msg = cont_response.choices[0].message
                        cont_text = _extract_text(cont_msg)
                        if cont_text:
                            full_content += "\n" + cont_text.strip()
                        finish_reason = getattr(cont_response.choices[0], "finish_reason", None)
                    except Exception as ce:
                        logger.error(f"Auto-continuation pass failed: {ce}")
                        break

                if not full_content:
                    logger.error(f"API returned empty content. Full response: {response}")
                    
                import re
                final_text = full_content.strip()
                final_text = re.sub(r'<think>.*?</think>', '', final_text, flags=re.DOTALL).strip()
                final_text = re.sub(r'<thinking>.*?</thinking>', '', final_text, flags=re.DOTALL).strip()
                # If there's an unclosed <think> tag, strip everything after it
                if "<think>" in final_text and "</think>" not in final_text:
                    final_text = final_text.split("<think>")[0].strip()
                if "<thinking>" in final_text and "</thinking>" not in final_text:
                    final_text = final_text.split("<thinking>")[0].strip()
                    
                return final_text

        logger.warning("Max tool calls reached. Forcing LLM to finish.")
        # Force a final completion without tools
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        final_response = _create_completion(client, provider, **kwargs)
        final_text = _extract_text(final_response.choices[0].message).strip()
        import re
        final_text = re.sub(r'<think>.*?</think>', '', final_text, flags=re.DOTALL).strip()
        final_text = re.sub(r'<thinking>.*?</thinking>', '', final_text, flags=re.DOTALL).strip()
        if "<think>" in final_text and "</think>" not in final_text:
            final_text = final_text.split("<think>")[0].strip()
        if "<thinking>" in final_text and "</thinking>" not in final_text:
            final_text = final_text.split("<thinking>")[0].strip()
        return final_text

    except Exception as e:
        logger.error(f"API LLM inference failed: {e}", exc_info=True)
        return ""
