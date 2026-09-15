"""arbitration.py — Pass 2-JUDGE: Ponytail Senior PM cross-examination and watch_levels extraction."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.clients.llm_client import query_local_llm

logger = logging.getLogger(__name__)


def _build_judge_sys_prompt(ticker: str) -> str:
    return (
        "You are the Chief Investment Officer & Senior Portfolio Manager operating under Ponytail Finance rules "
        "(Occam's Razor, minimal bloat, hard math, ruthless risk management).\n\n"
        "You have received two independent reports for this ticker:\n"
        "- REPORT A: Proprietary Quantitative Engine (Bible rules, Code 8 / Zone R:R, Titanium levels)\n"
        "- REPORT B: Independent Macro & Volume Profile Study (Macro attribution, 12 MAs, VRVP POC, IV/HV Forensics)\n\n"
        "Your job is to cross-examine both reports with a strict FOR vs AGAINST trial, settle their disagreements, and issue the FINAL binding trading directive.\n\n"
        "Output in this exact markdown format:\n\n"
        f"# {ticker} | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n"
        "## 🟢 THE CASE FOR (Bull Cross-Examination)\n"
        "[The strongest, evidence-backed arguments synthesized across both reports for why this trade should be taken]\n\n"
        "## 🔴 THE CASE AGAINST (Bear Cross-Examination & Traps)\n"
        "[The strongest risk arguments, hidden traps, and friction points synthesized across both reports for why this trade should be avoided or hedged]\n\n"
        "## ⚖️ THE JUDGE'S FINAL RULING\n"
        "* **Concurrence:** [Where Model A and Model B 100% agree]\n"
        "* **Conflict Resolution:** [Where they disagreed, which model is correct, and why]\n"
        "* **Floor Defense & Proximity Rule:** [If defending an indisputable structural Put Wall, VP POC, or gap floor, DO NOT demand an exact tick fill. Expand entry_zone_high by +1.0% to catch institutional front-running (e.g. $300 Put Wall -> $300.00–$303.00 entry zone), and use a local tactical stop just below the floor to yield >4:1 R:R].\n"
        "* **Decoupled Options Directives:** [If direct equity requires waiting for a breakout or deeper pullback, but Plan B identifies an asymmetric defined-risk options structure (e.g. Bull Call Spread with R:R ≥ 2.5:1, or Bull Put Spread at the floor), mark options_plan.actionable = true so options can be traded immediately while shares stalk].\n"
        "* **Final Verdict:** **[ENTER (Limit @ Floor) / ENTER (Breakout) / ENTER (Options Structure) / STALK / CASH_SKIP]** (Conviction: X/10)\n\n"
        "## 🎯 FINAL ACTIONABLE DIRECTIVES\n"
        "* **Equity (Shares):** [Exact Limit Price, Tactical Stop, Target 1, Target 2, Breakout Trigger Level & Stop, R:R]\n"
        "* **Options (Derivatives):** [Actionable: YES/NO, Exact Structure, Expiry, Strikes, Net Credit/Debit, Max Loss, Break-Even]\n"
        "* **The ONE Thing Invalidation:** [The single binary price condition that kills the trade immediately]\n\n"
        "```json:watch_levels\n"
        "{\n"
        f'  "ticker": "{ticker}",\n'
        '  "verdict": "ENTER|STALK|CASH_SKIP|WATCH|CUT",\n'
        '  "conviction": 5,\n'
        '  "shares_plan": {\n'
        '    "entry_type": "LIMIT|MARKET|NO_ENTRY",\n'
        '    "entry_zone_low": 0.0,\n'
        '    "entry_zone_high": 0.0,\n'
        '    "breakout_level": 0.0,\n'
        '    "breakout_stop": 0.0,\n'
        '    "tactical_stop": 0.0,\n'
        '    "target_1": 0.0,\n'
        '    "target_2": 0.0\n'
        '  },\n'
        '  "options_plan": {\n'
        '    "actionable": true,\n'
        '    "entry_trigger": "AT_MARKET|AT_FLOOR_LIMIT|BREAKOUT",\n'
        '    "structure": "BULL_CALL_SPREAD|BULL_PUT_SPREAD|BEAR_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_CALL|LONG_PUT|CASH_SECURED_PUT|NONE",\n'
        '    "expiration": "YYYY-MM-DD",\n'
        '    "long_strike": 0.0,\n'
        '    "short_strike": 0.0,\n'
        '    "target_debit": 0.0,\n'
        '    "max_loss": 0.0,\n'
        '    "max_profit": 0.0,\n'
        '    "summary": "Short description"\n'
        '  },\n'
        '  "invalidation": {\n'
        '    "condition": "DAILY_CLOSE_BELOW|DAILY_CLOSE_ABOVE|INTRADAY_TOUCH",\n'
        '    "price_level": 0.0,\n'
        '    "rationale": "Short explanation"\n'
        '  },\n'
        '  "status": "STALKING|IN_ZONE|IN_TRADE|INVALIDATED"\n'
        "}\n"
        "```\n"
    )


def run_arbitration(
    ticker: str,
    date_str: str,
    clean_response: str,
    clean_ind_response: str,
    f_parsed: dict,
    dw_dict: dict,
    live_quote_block: str,
    earnings_fact_block: str,
    alerts_list: list,
    macro_news: str,
    macro_grounded_block: str,
    active_pos_block: str,
    tdir: Path,
    raw_dir: Path,
    reports_dir: Path,
) -> str:
    """Run Pass 2-JUDGE locally, write ``{ticker}_arbitration.md``, extract watch_levels.

    Returns the cleaned arbitration text (empty string on failure).
    """
    if not (clean_response and clean_ind_response):
        logger.warning(f"[{ticker}] Pass 2-JUDGE skipped because one or both reports failed.")
        return ""

    logger.info(f"[{ticker}] Running Senior PM Ponytail Judge (Cross-Examining Report A vs Report B)...")

    judge_sys_prompt = _build_judge_sys_prompt(ticker)

    ground_truth = (
        f"--- GROUND TRUTH MARKET FACTS (VERIFIED AT RUN TIME) ---\n"
        f"- Ticker: {ticker} | Date: {date_str}\n"
        f"{active_pos_block}\n"
        f"- Live Quote: {live_quote_block.strip() if live_quote_block else 'N/A'}\n"
        f"- Earnings Date & Event Risk: {earnings_fact_block.strip() if earnings_fact_block else 'N/A'}\n"
        f"- Recent Daily Alerts / Triggers: {'; '.join(alerts_list) if alerts_list else 'None recorded'}\n"
        f"- Macro News & Fed/Yields: {macro_news.strip() if macro_news else 'N/A'}\n"
        f"- Macro Grounding: {macro_grounded_block.strip() if macro_grounded_block else 'N/A'}\n"
        f"- Key Technical & Volatility Ground Truths:\n"
        f"  * Moving Averages: MA20={f_parsed.get('ma20', 'N/A')}, MA50={f_parsed.get('ma50', 'N/A')}, MA200={f_parsed.get('ma200', 'N/A')}\n"
        f"  * Volume Profile: POC={f_parsed.get('vp_poc', 'N/A')}, VAL={f_parsed.get('vp_val', 'N/A')}, VAH={f_parsed.get('vp_vah', 'N/A')}\n"
        f"  * Volatility: HV20={f_parsed.get('hv20', 'N/A')}%, IV30={f_parsed.get('iv30', 'N/A')}%, IV Rank={f_parsed.get('iv_rank', 'N/A')}%\n"
        f"  * 52W Milestones: 52W High={dw_dict.get('52 Week High', 'N/A')}, 52W Low={dw_dict.get('52 Week Low', 'N/A')}"
    )

    judge_user_prompt = f"""
                TICKER: {ticker} | DATE: {date_str}

                {ground_truth}

                --- REPORT A: PROPRIETARY QUANTITATIVE ENGINE ---
                {clean_response}

                --- REPORT B: INDEPENDENT MACRO & VOLUME PROFILE STUDY ---
                {clean_ind_response}

                MANDATE FOR THE SENIOR PM JUDGE:
                1. Cross-examine Report A and Report B directly against the GROUND TRUTH MARKET FACTS.
                2. Settle any discrepancies in price levels, earnings risk, or volatility regime between the two models.
                3. Issue the final binding directive and output the exact json:watch_levels block.
                4. If USER ACTIVE BROKER POSITION is present, issue an explicit 'Active Holding Playbook' covering profit scale-out at Target 1, advancing stop to breakeven, and covered call yield tactics.
                """

    judge_response = query_local_llm(
        system_prompt=judge_sys_prompt,
        user_prompt=judge_user_prompt,
        json_mode=False,
        use_openrouter=False,
        use_tools=False,
        disable_thinking=True,
        max_tokens=3072,
    )

    if not judge_response:
        logger.warning(f"[{ticker}] Pass 2-JUDGE returned empty response.")
        return ""

    clean_judge = judge_response.strip()
    match_j = re.search(r"(?m)^#+\s+.*", clean_judge)
    if match_j and match_j.start() > 0:
        clean_judge = clean_judge[match_j.start():].strip()
    if not re.search(r"(?m)^#\s+", clean_judge):
        clean_judge = f"# {ticker} | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n" + clean_judge

    safe = ticker.replace(":", "_")
    arbitration_path = reports_dir / f"{safe}_arbitration.md"
    arbitration_path.write_text(clean_judge, encoding="utf-8")
    logger.info(f"[{ticker}] Senior PM Arbitration generated at {arbitration_path}!")

    # Extract structured watch_levels JSON and upsert to DB
    watch_json_match = re.search(
        r"```(?:json)?(?::watch_levels)?\s*(\{.*?\})\s*```", clean_judge, re.DOTALL
    )
    if watch_json_match:
        try:
            watch_data = json.loads(watch_json_match.group(1))
            watch_data.setdefault("ticker", ticker)
            watch_data.setdefault("date", date_str)

            watch_path = tdir / f"{safe}_watch_levels.json"
            watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")

            raw_watch_path = raw_dir / safe / f"{safe}_watch_levels.json"
            if raw_watch_path != watch_path:
                raw_watch_path.parent.mkdir(parents=True, exist_ok=True)
                raw_watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")

            logger.info(f"[{ticker}] Extracted structured watch levels -> {watch_path}")

            from src.tracking.watch_manager import upsert_watch_target
            upsert_watch_target(watch_data)
            logger.info(f"[{ticker}] Watch levels upserted to SQLite watch DB.")

        except Exception as e:
            logger.warning(f"[{ticker}] Failed to parse embedded watch levels JSON: {e}")

    return clean_judge
