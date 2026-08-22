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
    {
        "type": "function",
        "function": {
            "name": "fetch_historical_zone_and_regime_analytics",
            "description": "Calculates deep historical statistics from the 1-year OHLCV data: zone dwell duration, consecutive bars in zone, 30-day touch count, Darvas box duration, volume accumulation ratio, and realized vs implied volatility spread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'AMD')",
                    },
                    "lookback_bars": {
                        "type": "integer",
                        "description": "Number of bars to analyze (default: 60, max: 250)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_quantitative_plugin",
            "description": "Runs a specialized quantitative market analytics plugin ('order_flow', 'earnings_history', 'squeeze_expansion', 'htf_confluence', or 'all') on the trailing 1-year data and Data Window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g. 'CRWD', 'AMD', 'CRWV')",
                    },
                    "plugin_name": {
                        "type": "string",
                        "enum": ["all", "order_flow", "earnings_history", "squeeze_expansion", "htf_confluence"],
                        "description": "The specific analytics plugin to execute. Use 'order_flow' for volume accumulation ratios, Chaikin Money Flow, and Volume Profile liquidity nodes.",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python_code",
            "description": (
                "Executes Python in a quantitative sandbox with pre-loaded 'df' (300 daily bars x 85 indicators), 'dw' (Data Window dict), 'np', 'pd', 'scipy', 'stats', and 'math'.\n"
                "ROLE: You are the Lead Quantitative Strategist. Do NOT blindly copy boilerplate code. Formulate an open-ended mathematical hypothesis for this specific stock and execute custom Python to prove or disprove it.\n\n"
                "APPLICATIONS & QUANTITATIVE WORKFLOWS:\n"
                "1. Empirical Regime & Setup Backtesting: Query `df` for similar historical setups (e.g., matching Stage, Buy Score, RVOL, or Extension) and compute sample size N, forward return distribution, and win rates.\n"
                "2. Volatility Risk Premium & Edge: Compare Historical Volatility (`HV20`) vs Implied Volatility (`Energy IV30`) to determine if options premium is statistically overpriced (sell credit) or cheap (buy debit).\n"
                "3. Volume Flow & Absorption Dynamics: Analyze volume accumulation ratio (`_volume_acc_dist_ratio_60d`) and price interaction at High Volume Nodes (`VP HVN`) or Anchored VWAPs.\n"
                "4. Options Payoff, Breakeven & EV Modeling: Model candidate multi-leg spreads (Bull Put, Bull Call, LEAP Diagonal) with exact net debit/credit, maximum profit, max risk, and breakeven.\n"
                "5. Probabilistic Path & Touch Modeling: Simulate empirical price paths (e.g. Monte Carlo or drift-diffusion) to estimate probability of touching specific structural stops vs targets over 21-120 days.\n\n"
                "MUST use print() to output results. Code runs in a secure sandbox with instant execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Executable Python code. Pre-loaded variables: df, dw, ticker, np, pd, math, json, datetime. Use print() to output results.",
                    },
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'AMD')",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_prior_research",
            "description": "Retrieves the most recent prior research summary and trade plan from 'reports/<date>/<ticker>_summary.md' within the last lookback_days (default 14 days). Use this to audit active stalk states, track thesis evolution, and check whether prior limit orders or triggers have played out.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g., 'HOOD', 'AAPL', 'NVDA')",
                    },
                    "lookback_days": {
                        "type": "integer",
                        "description": "Maximum calendar days to look back for prior research (default: 14)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_candlestick_patterns",
            "description": (
                "Scans the 1-year OHLCV dataset for high-conviction candlestick and price action patterns:\n"
                "1. Bullish Pin Bars / Hammers (lower rejection wicks, buyer absorption at key floors).\n"
                "2. Bearish Shooting Stars (upper rejection wicks, seller defense at overhead supply).\n"
                "3. Gap Fill Retests (detects if price is testing an earnings or momentum gap window).\n"
                "4. Inside Day Compressions (Harami volatility squeeze before ignition).\n"
                "5. Bullish & Bearish Engulfing Bars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The stock ticker symbol (e.g. 'AMZN', 'AAPL', 'NVDA')",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
]


def run_quantitative_plugin_tool(ticker: str, plugin_name: str = "all", date_str: str = None) -> str:
    """Executes quantitative analytics plugins on demand for the LLM."""
    import pandas as pd
    import json
    from src.plugins import plugin_manager

    ticker = ticker.upper()
    date_str = date_str or time.strftime("%Y-%m-%d")

    chart_dir = config.BASE_DIR / "data" / "raw" / date_str / ticker
    csv_path = chart_dir / f"{ticker}_datawindow.csv"
    dw_path = chart_dir / f"{ticker}_datawindow.json"

    if not csv_path.exists():
        raw_root = config.BASE_DIR / "data" / "raw"
        for d in sorted(raw_root.glob("*/"), reverse=True):
            cand = d / ticker / f"{ticker}_datawindow.csv"
            if cand.exists():
                csv_path = cand
                dw_path = d / ticker / f"{ticker}_datawindow.json"
                break

    if not csv_path.exists():
        # Also check triage directory
        triage_root = config.BASE_DIR / "data" / "triage"
        for d in sorted(triage_root.glob("*/"), reverse=True):
            for sub in ["_DEEP_RESEARCH", "force", ""]:
                cand = d / sub / ticker / f"{ticker}_datawindow.csv" if sub else d / ticker / f"{ticker}_datawindow.csv"
                if cand.exists():
                    csv_path = cand
                    dw_path = cand.parent / f"{ticker}_datawindow.json"
                    break

    if not csv_path.exists():
        return f"Error: No historical datawindow.csv found for {ticker}."

    df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
    dw = json.loads(dw_path.read_text(encoding="utf-8")) if dw_path.exists() else {}

    p_key = plugin_name.lower().strip().replace("-", "_").replace(" ", "_")
    if p_key in ("orderflow", "order_flow_plugin"):
        p_key = "order_flow"
    elif p_key in ("squeeze", "squeeze_expansion_plugin"):
        p_key = "squeeze_expansion"
    elif p_key in ("earnings", "earnings_history_plugin"):
        p_key = "earnings_history"
    elif p_key in ("htf", "htf_confluence_plugin"):
        p_key = "htf_confluence"
    elif p_key in ("candlesticks", "candlestick", "patterns", "candles", "candle", "candlestick_patterns_plugin"):
        p_key = "candlestick_patterns"

    if p_key == "all":
        res = plugin_manager.run_all(ticker, df, dw)
    else:
        plugin = plugin_manager.get_plugin(p_key)
        if not plugin:
            return f"Error: Unknown plugin '{plugin_name}'. Available: {list(plugin_manager._plugins.keys())}"
        res = plugin.run(ticker, df, dw)

    lines = [f"### Quantitative Plugin Results for {ticker} [Plugin: {p_key}]"]
    for k, v in res.items():
        if isinstance(v, list):
            lines.append(f"- **{k}**:")
            for item in v:
                lines.append(f"  • {item}")
        elif isinstance(v, dict):
            lines.append(f"- **{k}**:")
            for sub_k, sub_v in v.items():
                lines.append(f"  • {sub_k}: {sub_v}")
        else:
            lines.append(f"- **{k}**: {v}")

    return "\n".join(lines)


def execute_python_code_tool(code: str, ticker: str = "AMD", date_str: str = None) -> str:
    """Safely executes a Python code snippet with pre-loaded df, dw, and math/pandas modules."""
    import io
    import sys
    import math
    import json
    import numpy as np
    import pandas as pd
    from datetime import datetime

    # Security check: Block destructive OS commands or file modifications
    forbidden_terms = ["os.system", "os.remove", "os.rmdir", "shutil.rmtree", "subprocess", "socket", "write_text", "to_csv", "__import__('os')"]
    for term in forbidden_terms:
        if term in code:
            return f"Security Error: '{term}' is not permitted in the quantitative sandbox."

    ticker = ticker.upper()
    date_str = date_str or time.strftime("%Y-%m-%d")

    # Locate datawindow.csv and datawindow.json
    chart_dir = config.BASE_DIR / "data" / "raw" / date_str / ticker
    csv_path = chart_dir / f"{ticker}_datawindow.csv"
    dw_path = chart_dir / f"{ticker}_datawindow.json"

    if not csv_path.exists():
        raw_root = config.BASE_DIR / "data" / "raw"
        for d in sorted(raw_root.glob("*/"), reverse=True):
            cand = d / ticker / f"{ticker}_datawindow.csv"
            if cand.exists():
                csv_path = cand
                dw_path = d / ticker / f"{ticker}_datawindow.json"
                break

    if not csv_path.exists():
        triage_root = config.BASE_DIR / "data" / "triage"
        for d in sorted(triage_root.glob("*/"), reverse=True):
            for sub in ["_DEEP_RESEARCH", "force", ""]:
                cand = d / sub / ticker / f"{ticker}_datawindow.csv" if sub else d / ticker / f"{ticker}_datawindow.csv"
                if cand.exists():
                    csv_path = cand
                    dw_path = cand.parent / f"{ticker}_datawindow.json"
                    break

    df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
    dw = json.loads(dw_path.read_text(encoding="utf-8")) if dw_path.exists() else {}

    try:
        import scipy
        import scipy.stats as stats
    except ImportError:
        scipy = None
        stats = None

    # Sandbox environment
    sandbox_globals = {
        "pd": pd,
        "np": np,
        "scipy": scipy,
        "stats": stats,
        "math": math,
        "json": json,
        "datetime": datetime,
        "df": df,
        "dw": dw,
        "ticker": ticker,
        "print": print,
    }

    # Capture stdout
    stdout_buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = stdout_buf

    try:
        exec(code, sandbox_globals)
        output = stdout_buf.getvalue()
        if not output.strip():
            output = "(Code executed successfully with no print output. Tip: Use print(...) to return calculated values.)"
        return output.strip()
    except Exception as e:
        return f"Python Execution Error: {type(e).__name__}: {str(e)}"
    finally:
        sys.stdout = old_stdout


def fetch_historical_zone_and_regime_analytics_tool(ticker: str, lookback_bars: int = 60, date_str: str = None) -> str:
    """Computes on-demand deep historical analytics from datawindow.csv for the LLM brain."""
    import pandas as pd
    ticker = ticker.upper()
    date_str = date_str or time.strftime("%Y-%m-%d")
    chart_dir = config.BASE_DIR / "data" / "raw" / date_str / ticker
    csv_path = chart_dir / f"{ticker}_datawindow.csv"
    if not csv_path.exists():
        raw_root = config.BASE_DIR / "data" / "raw"
        for d in sorted(raw_root.glob("*/"), reverse=True):
            cand = d / ticker / f"{ticker}_datawindow.csv"
            if cand.exists():
                csv_path = cand
                break
    if not csv_path.exists():
        return f"Error: No historical datawindow.csv found for {ticker}."

    df = pd.read_csv(csv_path)
    if df.empty:
        return f"Error: Empty CSV for {ticker}."

    lines = [f"### Quantitative Zone & Regime Analytics for {ticker} (Lookback: {lookback_bars} bars)"]
    curr_c = float(df['close'].iloc[-1]) if 'close' in df.columns else 0.0
    lines.append(f"- Current Price: ${curr_c:.2f}")

    # 1. Zone Analytics
    z_bot = float(df['Long Entry Zone Bot'].iloc[-1]) if 'Long Entry Zone Bot' in df.columns else 0.0
    z_top = float(df['Long Entry Zone Top'].iloc[-1]) if 'Long Entry Zone Top' in df.columns else 0.0
    if z_bot > 0 and z_top > 0 and 'low' in df.columns and 'high' in df.columns:
        in_zone = (df['low'] <= z_top) & (df['high'] >= z_bot)
        consec_zone = 0
        for z in reversed(in_zone):
            if z: consec_zone += 1
            else: break
        touches = int(in_zone.tail(lookback_bars).sum())
        lines.append(f"- Long Entry Zone: ${z_bot:.2f} – ${z_top:.2f}")
        lines.append(f"- Consecutive Bars in Zone: {consec_zone}")
        lines.append(f"- Zone Touches in last {lookback_bars} bars: {touches} touches")
        if consec_zone >= 5:
            lines.append("- Dwell Assessment: ⚠️ LINGERING / SATURATED (Support weakening risk)")
        elif consec_zone == 1 and touches <= 3:
            lines.append("- Dwell Assessment: ✅ FRESH RE-TEST (High-conviction buyer defense)")
        elif touches >= 8:
            lines.append("- Dwell Assessment: ⚔️ HEAVILY CONTESTED ZONE (Compression at boundary)")

    # 2. Darvas Base Duration
    if 'Darvas Box Top' in df.columns and 'high' in df.columns:
        box_top = float(df['Darvas Box Top'].iloc[-1])
        if box_top > 0:
            under_box = df['high'] <= box_top
            consec_box = 0
            for u in reversed(under_box):
                if u: consec_box += 1
                else: break
            lines.append(f"- Darvas Base Ceiling: ${box_top:.2f}")
            lines.append(f"- Compression Duration: {consec_box} consecutive bars inside base")
            if consec_box >= 12:
                lines.append(f"- Base Readiness: 🚀 MATURE BASE ({consec_box} bars) — High energy compression ready for expansion")

    # 3. Volume Flow (60-day)
    c_col = next((c for c in df.columns if c.lower() == 'close'), None)
    v_col = next((c for c in df.columns if c.lower() == 'volume'), None)
    if c_col and v_col and len(df) >= 20:
        c_ser = pd.to_numeric(df[c_col], errors='coerce').tail(60)
        v_ser = pd.to_numeric(df[v_col], errors='coerce').tail(60)
        up_vol = float(v_ser[c_ser > c_ser.shift(1)].sum())
        dn_vol = float(v_ser[c_ser < c_ser.shift(1)].sum())
        if dn_vol > 0:
            ratio = round(up_vol / dn_vol, 3)
            lines.append(f"- Volume Accumulation Ratio (60d): {ratio:.3f}x (Up-volume / Down-volume)")
            lines.append(f"- Institutional Flow: {'Institutional Accumulation' if ratio >= 1.15 else 'Institutional Distribution' if ratio <= 0.85 else 'Balanced Flow'}")

    return "\n".join(lines)


def fetch_prior_research_tool(ticker: str, lookback_days: int = 14, date_str: str = None) -> str:
    """Retrieves the most recent prior research summary and trade plan from reports/<date>/<ticker>_summary.md."""
    import re
    from datetime import datetime

    reports_dir = config.BASE_DIR / "reports"
    if not reports_dir.exists():
        return f"No prior research found for {ticker} (reports/ directory does not exist)."

    date_cutoff = date_str or datetime.now().strftime("%Y-%m-%d")
    dates = sorted([d.name for d in reports_dir.glob("202*") if d.is_dir() and d.name < date_cutoff], reverse=True)

    for d in dates[:lookback_days]:
        p = reports_dir / d / f"{ticker}_summary.md"
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                header = lines[0] if lines else f"# {ticker} | {d}"

                tldr_match = re.search(r"## ⚡ TLDR / EXECUTIVE SUMMARY\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
                tldr = tldr_match.group(1).strip() if tldr_match else ""

                plan_a_match = re.search(r"### Plan A.*?\n(.*?)(?=\n###|\n##|\Z)", text, re.DOTALL)
                plan_a = plan_a_match.group(1).strip() if plan_a_match else ""

                pm_audit_match = re.search(r"## 🧐 SENIOR PM PONYTAIL AUDIT.*?\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
                pm_audit = pm_audit_match.group(1).strip() if pm_audit_match else ""

                try:
                    d_obj = datetime.strptime(d, "%Y-%m-%d")
                    curr_obj = datetime.strptime(date_cutoff, "%Y-%m-%d")
                    days_ago = (curr_obj - d_obj).days
                except Exception:
                    days_ago = "N/A"

                output = [
                    f"### PRIOR RESEARCH SUMMARY FOR {ticker} (From {d} — {days_ago} days ago)",
                    f"**File:** `reports/{d}/{ticker}_summary.md`",
                    f"**Header:** {header}",
                    "",
                    "#### Prior Executive Summary & Verdict:",
                    tldr,
                ]
                if plan_a:
                    output.extend(["", "#### Prior Plan A Levels:", plan_a])
                if pm_audit:
                    output.extend(["", "#### Prior Senior PM Audit & Invalidation Level:", pm_audit])

                return "\n".join(output)
            except Exception as e:
                return f"Error reading prior report for {ticker} from {d}: {e}"

    return f"No prior research found for {ticker} in reports/ prior to {date_cutoff} (looked back {min(len(dates), lookback_days)} dates)."


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

    from src.clients import options_client
    active_ticker = options_client._ACTIVE_TICKER
    req_ticker = (args.get("ticker") or args.get("symbol") or active_ticker or "GLOBAL").upper()

    if active_ticker and req_ticker != active_ticker and function_name != "search_web":
        logger.warning(
            f"Cross-contamination guardrail: LLM attempted tool '{function_name}' for ticker '{req_ticker}' "
            f"while researching '{active_ticker}'. Overriding to '{active_ticker}'."
        )
        req_ticker = active_ticker
        args["ticker"] = active_ticker
        if "symbol" in args:
            args["symbol"] = active_ticker

    ticker = req_ticker

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
    elif function_name == "run_quantitative_plugin":
        plugin_slug = str(args.get("plugin_name", "all")).lower()
        cache_key = f"quant_plugin_{plugin_slug}"
    elif function_name == "fetch_prior_research":
        lookback_slug = str(args.get("lookback_days", 14))
        cache_key = f"prior_research_{lookback_slug}"
    elif function_name == "execute_python_code":
        import hashlib
        code_hash = hashlib.md5(str(args.get("code", "")).encode("utf-8")).hexdigest()[:8]
        cache_key = f"py_code_{code_hash}"

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
    elif function_name == "fetch_historical_zone_and_regime_analytics":
        lookback = int(args.get("lookback_bars", 60))
        logger.info(f"LLM executed tool: fetch_historical_zone_and_regime_analytics(ticker='{ticker}', lookback={lookback})")
        res_str = fetch_historical_zone_and_regime_analytics_tool(ticker, lookback_bars=lookback, date_str=date_str)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "execute_python_code":
        code_str = args.get("code", "")
        logger.info(f"LLM executed tool: execute_python_code(ticker='{ticker}', code_len={len(code_str)})")
        res_str = execute_python_code_tool(code=code_str, ticker=ticker, date_str=date_str)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "run_quantitative_plugin":
        plugin_name = args.get("plugin_name", "all")
        logger.info(f"LLM executed tool: run_quantitative_plugin(ticker='{ticker}', plugin='{plugin_name}')")
        res_str = run_quantitative_plugin_tool(ticker, plugin_name=plugin_name, date_str=date_str)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "fetch_prior_research":
        lookback = int(args.get("lookback_days", 14))
        logger.info(f"LLM executed tool: fetch_prior_research(ticker='{ticker}', lookback_days={lookback})")
        res_str = fetch_prior_research_tool(ticker=ticker, lookback_days=lookback, date_str=date_str)
        artifact_cache.save(date_str, ticker, cache_key, res_str)
        return res_str
    elif function_name == "detect_candlestick_patterns":
        logger.info(f"LLM executed tool: detect_candlestick_patterns(ticker='{ticker}')")
        res_str = run_quantitative_plugin_tool(ticker, plugin_name="candlestick_patterns", date_str=date_str)
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
    openrouter_key = os.getenv("OPENROUTER_KEY") or os.getenv("OPENROUTER_API_KEY")

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
        MAX_TOOL_CALLS = 25
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

                import concurrent.futures
                
                def _process_tool_call(tool_call):
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
                    
                    return {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": tool_result_str,
                    }

                with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(message.tool_calls))) as executor:
                    futures = [executor.submit(_process_tool_call, tc) for tc in message.tool_calls]
                    for future in futures:
                        messages.append(future.result())

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
                            "content": (
                                "Your response was cut off due to the model output token limit. "
                                "Please CONTINUE your analysis from the exact character/word where you stopped. "
                                "CRITICAL: Maintain strict mathematical continuity for all options spreads and strike calculations "
                                "(e.g., Max Profit + Max Loss == Spread Width * 100). Do NOT repeat headings or sections you already wrote."
                            )
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
