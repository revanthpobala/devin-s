"""context_builder.py — prompt block formatters and full prompt assembly for deep research."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level block formatters  (previously at module level in deep_research.py)
# ---------------------------------------------------------------------------

def format_flags_block(flags: list) -> str:
    if not flags:
        return ""
    return "--- 2d. ENGINE FLAGS ---\n" + "\n".join(f"  • {f}" for f in flags)


def format_engine_math_block(rec: dict) -> str:
    if not isinstance(rec, dict) or not rec:
        return ""
    lines = ["--- 2d-i. ENGINE MATH (deterministic — do not recompute) ---"]
    keys = [
        "action_code", "action_long_code", "action_short_code",
        "triage", "verdict", "action",
        "long_entry_zone_bot", "long_entry_zone_top",
        "long_stop_loss", "long_target", "long_target_t1",
        "short_entry_zone_bot", "short_entry_zone_top",
        "short_stop_loss", "short_target",
        "long_rr_at_market", "rr_to_target",
        "stage", "regime",
        "ext_pct_vs_ma200", "ext_z_self_relative",
        "z_velocity", "buy_score", "sell_score",
        "energy_state", "energy_iv30", "energy_hv20", "energy_iv_rank",
        "adx_14",
    ]
    content_lines = []
    for k in keys:
        v = rec.get(k)
        if v is not None:
            content_lines.append(f"  {k}: {v}")
    # Also include any keys not in the explicit list
    for k, v in rec.items():
        if k not in keys and v is not None and not isinstance(v, (dict, list)):
            content_lines.append(f"  {k}: {v}")
    if not content_lines:
        return ""
    return "\n".join(lines + content_lines)


def format_triggers_block(triage_record: dict) -> str:
    if not isinstance(triage_record, dict):
        return ""
    triggers = triage_record.get("triggers")
    if not triggers:
        return ""
    if isinstance(triggers, str):
        return f"--- 2d-ii. BUY-TRIGGER GAP ENGINE ---\n{triggers}"
    lines = ["--- 2d-ii. BUY-TRIGGER GAP ENGINE (deterministic gate distances) ---"]
    if isinstance(triggers, dict):
        for gate, info in triggers.items():
            if isinstance(info, dict):
                dist = info.get("distance_pct")
                price = info.get("trigger_price")
                lines.append(f"  {gate}: trigger=${price}, dist={dist}%")
            else:
                lines.append(f"  {gate}: {info}")
    elif isinstance(triggers, list):
        for t in triggers:
            lines.append(f"  {t}")
    return "\n".join(lines)


def format_scenario_block(scenarios: list) -> str:
    if not scenarios:
        return ""
    lines = ["--- 2d-iii. PRICE SCENARIO TRAJECTORY ---"]
    for s in scenarios:
        if isinstance(s, dict):
            candidate = s.get("candidate_price", "?")
            ma50 = s.get("ma50_proj", "?")
            ma200 = s.get("ma200_proj", "?")
            ext = s.get("ext_pct", "?")
            lane = s.get("lane", "?")
            lane_edge = s.get("lane_edge", "?")
            zone_top = s.get("zone_top", "?")
            zone_bot = s.get("zone_bot", "?")
            lines.append(
                f"  Zone=[{zone_bot},{zone_top}] Entry=${candidate} "
                f"MA50=${ma50} MA200=${ma200} Ext={ext}% "
                f"Lane={lane} ({lane_edge})"
            )
        else:
            lines.append(f"  {s}")
    return "\n".join(lines)


def format_state_response_block(f_parsed: dict, scenarios: list) -> str:
    if not f_parsed:
        return ""
    try:
        from src.logic.response_model import state_response
        stage = f_parsed.get("stage")
        regime = f_parsed.get("regime")
        action_code = f_parsed.get("action_long_code") or f_parsed.get("action_code")
        result = state_response(f_parsed)
        if result:
            return f"--- 2d-iv. STATE RESPONSE (historical, honest) ---\n{result}"
    except Exception as e:
        logger.debug(f"State response lookup failed: {e}")
    return ""


def format_unmasked_recency_block(dw_dict: dict) -> str:
    if not isinstance(dw_dict, dict):
        return ""
    mask_keys = {
        "Reversal Pattern Mask", "Reversal Pattern Age",
        "Bear Warning Mask", "Bear Warning Age",
        "Weak Level Mask", "Weak Level Age",
        "Signal Pack", "PreMove Pack",
        "MTF Long Short Pack",
    }
    lines = ["--- 1b. UNMASKED PATTERN & RECENCY SIGNALS ---"]
    found = False
    for k in sorted(mask_keys):
        v = dw_dict.get(k)
        if v is not None:
            lines.append(f"  {k}: {v}")
            found = True
    if not found:
        return ""
    return "\n".join(lines)


def build_preloaded_block(pre_ctx: dict) -> str:
    return f"""
        --- 2g. PRE-LOADED DETERMINISTIC QUANT, TA-LIB & VOLATILITY ANALYTICS ---
        [DETERMINISTIC CPU MONTE CARLO PROBABILITIES (20,000 PATHS), VRP & CORE MOVING AVERAGE EXTENSIONS]:
        {pre_ctx.get('monte_carlo')}

        [QUANTITATIVE PLUGINS (Order Flow, Squeeze, HTF Confluence, VP Nodes)]:
        {pre_ctx.get('quant_plugins')}

        [TA-LIB MULTI-TIMEFRAME CANDLESTICK & PATTERN SIGNALS]:
        {pre_ctx.get('candlestick_patterns')}

        [TASTYTRADE VOLATILITY & IV RANK / HV SPREAD]:
        {pre_ctx.get('tastytrade_volatility')}

        [HISTORICAL REGIME & STATE ANALYTICS (60 BARS)]:
        {pre_ctx.get('historical_analytics')}

        [PRIOR 14-DAY RESEARCH DOSSIER]:
        {pre_ctx.get('prior_research')}

        ⚡ OPTIONS & QUANT DIRECTIVE: The 20,000-path Monte Carlo trajectory, IV/HV volatility risk premium, and core moving average extensions are ALREADY pre-computed above on the CPU. Formulate your directional thesis, options plan, and target expiration window directly from these deterministic probabilities. Emit tool calls (`fetch_options_chain` or `scrape_tradingview_options_finder`) to retrieve the exact strikes and expirations tailored to your trade plan.
        """


# ---------------------------------------------------------------------------
# Daily alert history block
# ---------------------------------------------------------------------------

def build_daily_alerts_block(ticker: str, raw_dir: Path) -> tuple[str, list[str]]:
    """Query SQLite watch_alerts + survivors.json for recent signals.

    Returns ``(formatted_block, alerts_list)`` where *alerts_list* is reused
    by the arbitration judge.
    """
    alerts_list = []
    try:
        watch_db = config.BASE_DIR / "data" / "research_watch.db"
        if watch_db.exists():
            with sqlite3.connect(str(watch_db)) as wconn:
                wcur = wconn.cursor()
                wrows = wcur.execute(
                    "SELECT date, trigger_type, message, spot_price, triggered_at "
                    "FROM watch_alerts WHERE ticker = ? ORDER BY id DESC LIMIT 5",
                    (ticker.upper(),),
                ).fetchall()
                for wr in wrows:
                    alerts_list.append(f"- [{wr[0]} | {wr[1]}] Spot: ${wr[3]:.2f} — {wr[2]}")
    except Exception as e:
        logger.debug(f"Could not load watch_alerts for {ticker}: {e}")

    try:
        surv_file = raw_dir / "survivors.json"
        if surv_file.exists():
            s_data = json.loads(surv_file.read_text(encoding="utf-8"))
            for s_item in s_data:
                if (s_item.get("Ticker") or s_item.get("ticker") or "").upper() == ticker.upper():
                    alerts_list.append(
                        f"- Daily Screener Setup Flag: {s_item.get('screener_setup', 'Active Candidate')} "
                        f"(Source: {s_item.get('source', 'screener')})"
                    )
    except Exception:
        pass

    header = "--- 2f-i. RECENT DAILY TRADINGVIEW & TRIGGER ALERTS ---"
    body = "\n".join(alerts_list) if alerts_list else f"No prior trigger alerts recorded for {ticker}."
    return f"{header}\n{body}\n", alerts_list


# ---------------------------------------------------------------------------
# Pine Script benchmark block
# ---------------------------------------------------------------------------

def build_pine_benchmark_block(ticker: str, dw_dict: dict) -> str:
    pine_entry_bot = float(dw_dict.get("Long Entry Zone Bot") or 0.0)
    pine_entry_top = float(dw_dict.get("Long Entry Zone Top") or 0.0)
    pine_stop = float(dw_dict.get("Long Stop Loss") or 0.0)
    pine_target1 = float(dw_dict.get("Long Target") or dw_dict.get("Long Target T1 Waypoint") or 0.0)
    pine_score = dw_dict.get("Long Setup Score", "N/A")
    pine_code = dw_dict.get("Action Long Code", "N/A")
    pine_rr = dw_dict.get("RR To Target", "N/A")

    if pine_entry_bot > 0 and pine_entry_top > 0 and pine_stop > 0:
        return f"""--- 🎯 PINE SCRIPT TRADE BENCHMARK (GROUND TRUTH SETUP) ---
The TradingView Pine Script quantitative engine has mathematically computed the following structural setup:
- Pine Script Buy Zone: [${pine_entry_bot:.2f} – ${pine_entry_top:.2f}]
- Pine Script Tactical Stop Loss: ${pine_stop:.2f}
- Pine Script Profit Target 1 (T1): ${pine_target1:.2f}
- Pine Script Setup Score: {pine_score} | Action Code: {pine_code} | Reward-to-Risk: {pine_rr}

⚠️ CRITICAL SUPERFORECASTING EVALUATION DIRECTIVE:
You MUST anchor your `## 🔮 SUPERFORECASTING PREDICTIONS` directly to this Pine Script trade setup!
Do NOT invent arbitrary prices or random numbers. Evaluate the exact mathematical probability that:
1. {ticker} enters/tests the Pine Script Buy Zone [${pine_entry_bot:.2f} – ${pine_entry_top:.2f}] within 14 days.
2. {ticker} reaches Pine Script Profit Target 1 of ${pine_target1:.2f} before Stop Loss within 30 days.
3. {ticker} closes below Pine Script Tactical Stop Loss of ${pine_stop:.2f} before Target within 45 days.
"""
    return ""


# ---------------------------------------------------------------------------
# Sanitize data window for independent model
# ---------------------------------------------------------------------------

def sanitize_dw_for_independent(dw: dict) -> str:
    """Remove proprietary Pine Script signals from *dw* for Model B (independent)."""
    if not dw:
        return "{}"
    blacklist = [
        "action", "pine", "entry", "stop loss", "long stop", "short stop",
        "long target", "short target", "setup score", "pressure score",
        "long rr", "rr to target", "rev zone", "long ignition", "long anchor",
        "long_zone", "long zone", "short zone", "sigma", "evidence",
        "overextension", "exhaustion gradient", "ext z", "ext pct",
        "pattern mask", "pattern age", "warning mask", "warning age",
        "level mask", "level age", "signal pack", "premove pack",
        "zone rr flags", "fade gate", "ignition fresh", "revanth",
        "bible", "pillar", "entry at market", "mtf long", "mtf short",
        "zone 0 long", "zone 0 short", "_premove", "_long_zone",
        "_consecutive_bars_in_long_zone", "_long_anchor_name",
        "buy score", "sell score", "dir prob", "directional probability", "direction prob",
    ]

    def is_proprietary(key: str) -> bool:
        k = key.lower()
        if any(b in k for b in blacklist):
            return True
        if "stage" in k and any(x in k for x in ["1=", "1 base", "age"]):
            return True
        if "regime" in k and any(x in k for x in ["0hlt", "0 hlt", "0="]):
            return True
        return False

    return json.dumps({k: v for k, v in dw.items() if not is_proprietary(k)}, indent=2)


# ---------------------------------------------------------------------------
# Debate payload
# ---------------------------------------------------------------------------

def build_debate_payload(
    ticker: str,
    date_str: str,
    data_window_str: str,
    live_quote_block: str,
    unmasked_recency_block: str,
    news_dossier: str,
    fresh_news: str,
    macro_news: str,
    av_block: str,
    grounded_block: str,
    macro_grounded_block: str,
    gex_block: str,
    flags_block: str,
    engine_math_block: str,
    earnings_fact_block: str,
    preloaded_block: str,
    active_pos_block: str = "",
) -> str:
    return f"""
        RESEARCH DATE: {date_str}

        {active_pos_block}

        --- 1. DATA WINDOW (Exact Math State from TradingView) ---
        {data_window_str}

        --- 1a. LIVE QUOTE ---
        {live_quote_block}

        {unmasked_recency_block}

        --- 2. NEWS RESEARCH DOSSIER ---
        {news_dossier}

        --- 2a. FRESH NEWS (LIVE) ---
        {fresh_news}

        --- 2b. MACRO NEWS (LIVE) ---
        {macro_news}

        --- 2c. FUNDAMENTAL & CATALYST INTEL ---
        {av_block}
        {grounded_block}
        {macro_grounded_block}

        {gex_block}

        --- 2d. ENGINE FLAGS ---
        {flags_block}
        {engine_math_block}

        --- 2e. EARNINGS DATE ---
        {earnings_fact_block}

        {preloaded_block}
        """


# ---------------------------------------------------------------------------
# Main Pass 2 user prompt
# ---------------------------------------------------------------------------

def build_user_prompt(
    ticker: str,
    date_str: str,
    data_window_str: str,
    live_quote_block: str,
    unmasked_recency_block: str,
    news_dossier: str,
    fresh_news: str,
    macro_news: str,
    av_block: str,
    institutional_block: str,
    grounded_block: str,
    macro_grounded_block: str,
    flags_block: str,
    engine_math_block: str,
    triggers_block: str,
    scenario_block: str,
    state_response_block: str,
    earnings_fact_block: str,
    preloaded_block: str,
    debate_block: str,
    daily_alerts_block: str,
    pine_setup_benchmark_block: str,
    csv_path: Path,
    dw_path: Path,
    options_block: str = "",
    active_pos_block: str = "",
    stale: bool = False,
) -> str:
    return f"""
        RESEARCH DATE: {date_str}   (SYSTEM/TODAY: {datetime.now().strftime("%Y-%m-%d")})
        VERIFY every macro, CPI, Fed, and earnings reference against this date. Do NOT assume
        prior-session news is current.

        I am requesting a Deep Research Validation for the ticker: {ticker}.

        {active_pos_block}

        --- 1. DATA WINDOW (Exact Math State from TradingView — the last CLOSED bar) ---
        {data_window_str}

        {pine_setup_benchmark_block}

        --- 1a. LIVE QUOTE (pre-fetched at run time — where the market is NOW) ---
        {live_quote_block}

        {unmasked_recency_block}

        ⚠️ CRITICAL BITMASK RULE FOR REVERSAL PATTERN MASK:
        When filling the Reversal Pattern Mask table row in your output report, you MUST copy the exact pattern names and polarities from Section 1b (UNMASKED PATTERN & RECENCY SIGNALS). Do NOT infer or change suffixes.
        - Bit 256 IS HIKKAKE_BULL (Bullish) — NOT Hikkake Bear
        - Bit 1024 IS OOPS_BULL (Bullish) — NOT Oops Bear
        - Bit 512 IS HIKKAKE_BEAR (Bearish)
        - Bit 2048 IS OOPS_BEAR (Bearish)

        --- 2. NEWS RESEARCH DOSSIER (Pre-compiled by Local Pipeline) ---
        {news_dossier}
        {"(NOTE: this cached dossier is STALE — written on a different date. Prefer live tools.)" if stale else ""}

        --- 2a. FRESH NEWS (LIVE) ---
        {fresh_news}

        --- 2b. MACRO NEWS (LIVE) ---
        {macro_news}

        --- 2c. FUNDAMENTAL & CATALYST INTEL (fetched live for this ticker) ---
        {av_block}
        {institutional_block}
        {grounded_block}

        --- 2c-ii. MACRO GROUNDING (Google Search) ---
        {macro_grounded_block}

        --- 2d. ENGINE FLAGS (deterministic) ---
        {flags_block}

        --- 2d-i. ENGINE MATH (deterministic — already computed, do not recompute) ---
        {engine_math_block}

        --- 2d-ii. BUY-TRIGGER GAP ENGINE (deterministic gate distances) ---
        {triggers_block}

        --- 2d-iii. PRICE SCENARIO TRAJECTORY ---
        {scenario_block}

        --- 2d-iv. STATE RESPONSE (historical, honest) ---
        {state_response_block}

        --- 2e. EARNINGS DATE (deterministic where available) ---
        {earnings_fact_block}

        {preloaded_block}

        {debate_block}

        {daily_alerts_block}

        ## 2B. MULTIMODAL CHART STATE (2 High-Resolution Vision Images Provided)
        - Image 1 (Plain Chart: `{ticker}_chart_plain.png`): Clean naked Japanese candlesticks & raw volume sub-pane. Use this to visually identify swing pivot highs/lows, rejection tails (pin bars), and gap boundaries without indicator clutter.
        - Image 2 (Indicator Overlay: `{ticker}_chart_zoom.png`): 90-day technical view displaying Darvas compression boxes, Volume Profile POC/VAH/VAL, and Anchored VWAPs.

        --- 2g. QUANTITATIVE SANDBOX & 1-YEAR HISTORICAL DATA DICTIONARY (`df` & `dw`) ---
        The quantitative sandbox (`execute_python_code`) automatically pre-loads:
        - `df`: 300 daily bars x 85 columns (CSV File: `{csv_path}`)
        - `dw`: Latest closed bar dictionary (JSON File: `{dw_path}`)
        - `np`, `pd`, `scipy`, `stats`, `math`, `json`, `datetime`
        
        📊 COLUMN HEADERS & DEFINITIONS IN `df`:
        1. OHLCV & Volume:
           - `time` (bar date), `open`, `high`, `low`, `close`, `Volume`
           - `RVOL Vs Avg` (Relative Volume vs 20-day baseline; >1.5 = institutional surge)
           - `Z Volume` (Normalized volume Z-score)
        2. Moving Averages & Trend Anchors:
           - `Sprint Line EMA` (Fast 8 EMA), `Hull Baseline HMA` (Hull moving average baseline)
           - `MA 20 Fast`, `MA 50 Mid`, `MA 200 Slow`
           - `Weinstein MA 150` (150-day SMA, Stan Weinstein Stage Analysis baseline)
           - `Golden Cross`, `Death Cross` (50/200 MA cross flags: 1/0)
        3. Multi-Factor Scores & Market Stages:
           - `Buy Score` (0-100 composite score), `Sell Score` (0-100)
           - `Buy Sigma Evidence`, `Sell Sigma Evidence` (Statistical sigma evidence for directional bias)
           - `Stage 1 Base 2 Up 3 Top 4 Down` (1=Base/Accumulation, 2=Advancing Uptrend, 3=Top/Distribution, 4=Declining Downtrend, 5=Recovery)
           - `Stage Age Bars` (Number of bars elapsed in current stage)
           - `Action Long Code` (8=Watch, 10=Wait, 16=Blow-off Exhaustion, 20=Reversal, etc.)
           - `Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz` (0=Healthy, 1=Extended, 2=Climax, 3=Distribution, 4=Down, 5=Ignition, 6=Squeeze)
        4. Trade Geometry & Structural Zones:
           - `Long Entry Zone Bot`, `Long Entry Zone Top` (Bounding box of low-risk buyer defense)
           - `Long Stop Loss`, `Long Target`, `Long Target T1 Waypoint`
           - `Short Entry Zone Bot`, `Short Entry Zone Top`, `Short Stop Loss`, `Short Target`
           - `Long RR At Market` (Current at-market Reward-to-Risk ratio), `RR To Target` (Zone R:R)
           - `Entry At Market 0No 1L 2S 3Both` (0=No market entry, 1=Long at market OK)
           - `Long Ignition Fresh Breakout` (Flag: 1 if fresh breakout ignition bar)
        5. Extension, Momentum & Z-Scores:
           - `Ext Pct vs MA200` ((Close - MA200)/MA200 * 100; >25-60% = negative expectancy exclusion)
           - `Exhaustion Gradient` (Slope of overextension), `Ext Z Self Relative` (Z-score vs distribution)
           - `Z Velocity` (>2.0 indicates blow-off velocity), `Z RSI`, `Z Elasticity`
           - `Trend Bars Up` (Consecutive bars closing higher)
        6. Volume Profile & Anchored VWAPs:
           - `Darvas Box Top` (Upper boundary of recent consolidation base)
           - `VP POC` (Point of Control), `VP VAH` (Value Area High), `VP VAL` (Value Area Low)
           - `VP HVN Above`, `VP HVN Below` (High Volume Nodes)
           - `AVWAP Support`, `AVWAP Resistance` (Anchored VWAP from key swing pivots)
        7. Volatility, Implied Energy & Options State:
           - `HV20 Ann Pct` (20-day annualized Historical Realized Volatility)
           - `Energy IV30 Ann Pct` (30-day annualized Implied Volatility)
           - `Energy IV Rank Pct` (IV percentile rank 0-100%)
           - `Energy IV HV Spread` (IV minus HV; positive = IV rich / premium selling, negative = IV cheap / debit buying)
           - `Energy State 3 Exp 2 Warm 1 Sqz 0 Dorm` (3=Expansion, 2=Warming, 1=Squeeze, 0=Dormant)
           - `Exp Move Pct 21b` (21-bar Expected Move percentage)
           - `ADX 14`, `DMI DI Plus`, `DMI DI Minus`
        8. Bitmasks & Signal Packs:
           - `Signal Pack` (Bit 2 = Fade Gate active/inactive)
           - `Reversal Pattern Mask`, `Bear Warning Mask`, `Weak Level Mask`
           - `MTF Long Short Pack` (Multi-Timeframe alignment)

        You have access to LIVE TOOLS for fundamental discovery, pattern recognition, and quantitative execution:
        - `detect_candlestick_patterns` to scan the 1-year OHLCV dataset for high-conviction Pin Bars (rejection wicks), Gap Retests, Inside Day compressions, and Engulfing patterns.
        - `fetch_finnhub_news` and `fetch_alpaca_news` for the latest ticker-specific news.
        - `search_web` for broader macro or catalyst context.
        - `fetch_options_chain` for real-time Greeks, multi-horizon strikes (both short-dated and LEAPS with min_dte=120, max_dte=365+), and exact contract quotes.
        - `run_quantitative_plugin` to run specialized analytics ('monte_carlo', 'candlestick_patterns', 'order_flow', 'earnings_history', 'squeeze_expansion', 'htf_confluence', or 'all').
        - `fetch_prior_research` to retrieve our most recent prior research report from reports/<date>/<ticker>_summary.md within the last 14 days. Use this to audit active stalk states, track thesis evolution, and check whether prior limit orders or triggers have played out.
        - `execute_python_code`: NOTE: 20,000-path Monte Carlo simulations, IV/HV volatility risk premium, and core moving average extensions are ALREADY pre-computed on the host CPU in Section 2g. Use these deterministic numbers directly. Only call `execute_python_code` if you formulate a bespoke, non-standard quantitative hypothesis tailored to this specific ticker and market regime (e.g., custom setup backtesting on `df`, custom options payoff math, or multi-factor regression). Do NOT rewrite boilerplate Monte Carlo simulations that are already computed.

        --- PRIOR RESEARCH & PATTERN SYNTHESIS WORKFLOW ---
        When `fetch_prior_research` is called alongside `detect_candlestick_patterns`:
        1. **Stalk vs Trigger Audit:** Audit whether price tested or rejected the prior session's stalk limit, entry zone, or breakout trigger level.
        2. **Fresh Rejection Wicks & Gap Retests:** Check if a fresh Pin Bar (rejection wick) or Gap Retest formed today at the key floor that confirms buyer defense and resolves the stalk into an actionable entry.
        3. **Tactical Stop Refinement:** If buyer defense is confirmed by a lower rejection wick, anchor the updated tactical stop directly below the rejection wick low.

        Form your OWN independent verdict from the Data Window, chart, news, and the LIVE data you pull - do not 
        assume any prior read is correct. Act as Senior Quantitative Portfolio Manager and EMIT a single-pass, high-conviction trade thesis:
        - ACCURATE STRUCTURAL R:R: Calculate mathematical R:R as `(Target 1 - Entry) / (Entry - Tactical Stop)`. Adhere strictly to validated zone geometry, stop levels, and measured R:R requirements; do not override or invent artificial floors to bypass zone rules.
        - EXPECTED STOCK PRICE RANGE: support floor, resistance ceiling, and your projected 14-120 day trading range.
        - OPTIONS PLAN (DUAL HORIZON): Evaluate both Tactical Swing (21-45 DTE credit/debit) AND Multi-Quarter / LEAPS (90-365+ DTE Deep ITM Calls). If IV Rank > 70% and IV/HV spread is positive, explicitly favor defined-risk credit spreads (e.g. Bull Put Spread) over buying expensive extrinsic premium.
        - ENTRY, STOP LOSS, and PROFIT TARGET (exact prices) with strict binary invalidation ("The ONE Thing").
        - CONVICTION and risk/reward rationale.
        
        CRITICAL: Emit your final Portfolio Manager Thesis EXACTLY as instructed in the system prompt format.
        CRITICAL: At the very end of your response, you MUST append a strict JSON array of falsifiable predictions with exact probabilities (the "SUPERFORECASTING PREDICTIONS" block) exactly as shown in the example.
        """


# ---------------------------------------------------------------------------
# Independent (Model B) user prompt
# ---------------------------------------------------------------------------

def build_independent_user_prompt(
    ticker: str,
    date_str: str,
    independent_dw_str: str,
    live_quote_block: str,
    news_dossier: str,
    fresh_news: str,
    macro_news: str,
    av_block: str,
    institutional_block: str,
    grounded_block: str,
    macro_grounded_block: str,
    earnings_fact_block: str,
    csv_path: Path,
    dw_path: Path,
) -> str:
    return f"""
        RESEARCH DATE: {date_str}   (SYSTEM/TODAY: {datetime.now().strftime("%Y-%m-%d")})
        VERIFY every macro, CPI, Fed, and earnings reference against this date.

        I am requesting an INDEPENDENT Macro & Technical Analysis for the ticker: {ticker}.

        --- 1. OBJECTIVE MARKET DATA & VOLUME PROFILE (TradingView Closed Bar Snapshot) ---
        {independent_dw_str}

        --- 1a. LIVE QUOTE (pre-fetched at run time) ---
        {live_quote_block}

        --- 2. NEWS RESEARCH DOSSIER ---
        {news_dossier}

        --- 2a. FRESH NEWS (LIVE) ---
        {fresh_news}

        --- 2b. MACRO NEWS (LIVE) ---
        {macro_news}

        --- 2c. FUNDAMENTAL & CATALYST INTEL ---
        {av_block}
        {institutional_block}
        {grounded_block}

        --- 2c-ii. MACRO GROUNDING ---
        {macro_grounded_block}

        --- 2e. EARNINGS DATE ---
        {earnings_fact_block}

        ## MULTIMODAL CHART STATE (Vision Image Provided)
        - Image 1: Naked Japanese candlesticks & raw volume (pure price action, support/resistance structure).

        --- QUANTITATIVE SANDBOX & 1-YEAR HISTORICAL DATAFRAME (`df`) ---
        The quantitative sandbox (`execute_python_code`) pre-loads:
        - `df`: 300 daily bars x 85 columns (CSV File: `{csv_path}`)
        - `dw`: Latest closed bar dictionary (JSON File: `{dw_path}`)
        - `np`, `pd`, `scipy`, `stats`, `talib` (all 161 TA-Lib indicator & candlestick C-routines), `math`, `json`, `datetime`
        - Live tools: `fetch_options_chain`, `detect_candlestick_patterns`, `run_quantitative_plugin`.

        MANDATORY QUANTITATIVE WORKFLOW (EXECUTE BEFORE WRITING REPORT):
        1. Act as Lead Quantitative Trader & Macro Strategist operating independently.
        2. MANDATORY TOOL EXECUTION: You MUST emit any needed tools (`fetch_options_chain`, `detect_candlestick_patterns`, and any specialized plugins or bespoke `execute_python_code`) SIMULTANEOUSLY in PARALLEL in your first response batch before writing narrative text.
           - NOTE: 20,000-path Monte Carlo simulations, 52W High/Low, and moving average extensions are ALREADY pre-computed on the host CPU in Section 2g. Use those exact mathematical baselines directly.
           - In `fetch_options_chain`, retrieve the live options chain to price actionable calls, puts, and spreads.
        3. FINAL REPORT: Only after receiving and verifying the quantitative tool calculations, synthesize the macro backdrop vs micro company catalysts and output your final structured Markdown thesis following the independent format.
        """
