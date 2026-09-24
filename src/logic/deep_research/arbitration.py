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
        "Output in this exact markdown format (JSON BLOCK MUST COME FIRST):\n\n"
        "```json:watch_levels\n"
        "{\n"
        f'  "ticker": "{ticker}",\n'
        '  "verdict": "ENTER|STALK|CASH_SKIP|WATCH|CUT",\n'
        '  "conviction": 5,\n'
        '  "side": "LONG|SHORT",\n'
        '  "shares_plan": {\n'
        '    "side": "LONG|SHORT",\n'
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
        '    "structure": "BULL_CALL_SPREAD|BULL_PUT_SPREAD|BEAR_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_CALL|LONG_PUT|CASH_SECURED_PUT|COVERED_CALL|NONE",\n'
        '    "expiration": "YYYY-MM-DD",\n'
        '    "long_strike": 0.0,\n'
        '    "short_strike": 0.0,\n'
        '    "target_debit": 0.0,\n'
        '    "target_credit": 0.0,\n'
        '    "max_loss": 0.0,\n'
        '    "max_profit": 0.0,\n'
        '    "summary": "Short description of primary options play"\n'
        '  },\n'
        '  "options_menu": {\n'
        '    "tactical_spread": {\n'
        '      "structure": "BULL_CALL_SPREAD|BEAR_PUT_SPREAD|BULL_PUT_SPREAD|BEAR_CALL_SPREAD",\n'
        '      "expiration": "YYYY-MM-DD",\n'
        '      "long_strike": 0.0,\n'
        '      "short_strike": 0.0,\n'
        '      "target_debit": 0.0,\n'
        '      "summary": "Tactical 30-45 DTE defined risk spread"\n'
        '    },\n'
        '    "leaps": {\n'
        '      "structure": "LONG_CALL|LONG_PUT",\n'
        '      "expiration": "YYYY-MM-DD",\n'
        '      "long_strike": 0.0,\n'
        '      "target_debit": 0.0,\n'
        '      "summary": "6-12 Month Deep ITM LEAPS (0.75-0.85 delta)"\n'
        '    },\n'
        '    "income_or_csp": {\n'
        '      "structure": "COVERED_CALL|CASH_SECURED_PUT|BULL_PUT_SPREAD",\n'
        '      "expiration": "YYYY-MM-DD",\n'
        '      "short_strike": 0.0,\n'
        '      "long_strike": 0.0,\n'
        '      "target_credit": 0.0,\n'
        '      "summary": "Yield/harvest below floor or against existing position"\n'
        '    }\n'
        '  },\n'
        '  "invalidation": {\n'
        '    "condition": "DAILY_CLOSE_BELOW|DAILY_CLOSE_ABOVE|INTRADAY_TOUCH",\n'
        '    "price_level": 0.0,\n'
        '    "rationale": "Short explanation"\n'
        '  },\n'
        '  "setup_lane": "RR_SETUP_STRONG|RR_SETUP|CODE20|OVERSOLD|RSI2",\n'
        '  "lane_prior_win": 0.0,\n'
        '  "lane_prior_ev": 0.0\n'
        '}\n'
        '```\n\n'
        f"# {ticker} | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n"
        "## 🟢 THE CASE FOR (Bull Cross-Examination)\n"
        "[The strongest, evidence-backed arguments synthesized across both reports for why this trade should be taken]\n\n"
        "## 🔴 THE CASE AGAINST (Bear Cross-Examination & Traps)\n"
        "[The strongest risk arguments, hidden traps, and friction points synthesized across both reports for why this trade should be avoided or hedged]\n\n"
        "## ⚖️ THE JUDGE'S FINAL RULING\n"
        "* **Concurrence:** [Where Model A and Model B 100% agree]\n"
        "* **Conflict Resolution:** [Where they disagreed, which model is correct, and why]\n"
        "* **Floor Defense & Proximity Rule:** [If defending an indisputable structural Put Wall, VP POC, or gap floor, DO NOT demand an exact tick fill. Expand entry_zone_high by +1.0% to catch institutional front-running (e.g. $300 Put Wall -> $300.00–$303.00 entry zone), and use a local tactical stop just below the floor to yield >4:1 R:R].\n"
        "* **Tiered Options Directives:** Always provide a 3-tiered options menu across durations and account structures:\n"
        "  1. Tactical 30-45 DTE Defined Risk Spread (primary directional vehicle; mark options_plan.actionable = true ONLY if R:R >= 2.5:1 AND the short strike is at ≥1.25× Expected Move (Exp Move Pct 21b) AND outside major GEX Put/Call walls — IV Rank > 50 alone does NOT justify selling premium. If the strike is unscaled (inside 1.25× EM), mark actionable = false and lead with equity/debit by side; do not force a credit just because IV is rich).\n"
        "  2. Secular Trend LEAPS (6-12 Months, deep ITM 0.75-0.85 delta to participate in secular move with defined capital and low theta decay).\n"
        "  3. Floor Income / Capital Efficiency (Covered Call against existing long shares/LEAPS, or Cash-Secured Put / Floor Bull Put Spread below Put Wall floor).\n"
        "* **Final Verdict:** **[ENTER (Limit @ Floor) / ENTER (Breakout) / ENTER (Options Structure) / STALK / CASH_SKIP]** (Conviction: X/10)\n\n"
        "## 🎯 FINAL ACTIONABLE DIRECTIVES\n"
        "* **Equity (Shares):** [Exact Limit Price, Tactical Stop, Target 1, Target 2, Breakout Trigger Level & Stop, R:R]\n"
        "* **Options Directives (Tiered Menu):**\n"
        "  - **Primary Tactical Spread (30-45 DTE):** [Structure, Expiry, Strikes, Net Debit/Credit, Max Loss, Break-Even, R:R]\n"
        "  - **Secular Trend LEAPS (6-12 Months):** [Expiry, Deep ITM Strike (0.75-0.85 Delta), Target Debit, Rationale]\n"
        "  - **Income / Floor Support Structure:** [Covered Call / PMCC if holding position, or CSP / Bull Put Spread below Put Wall floor]\n"
        "* **The ONE Thing Invalidation:** [The single binary price condition that kills the trade immediately]\n"
    )


def _decode_zone_rr_flags(val) -> str:
    """Decode 4-bit Zone RR Flags Pack: 1 Long In Zone, 2 Short In Zone, 4 Long RR Valid, 8 Short RR Valid."""
    if val is None or val == "N/A":
        return "N/A"
    try:
        v = int(round(float(val)))
        parts = []
        if (v & 1):
            parts.append("Long In Zone (1)")
        if (v & 2):
            parts.append("Short In Zone (2)")
        if (v & 4):
            parts.append("Long RR Valid (4)")
        if (v & 8):
            parts.append("Short RR Valid (8)")
        return f"{v} [{', '.join(parts) if parts else 'None'}]"
    except Exception:
        return str(val)


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
    kind: str = "NEW",
    setup_lane: str | None = None,
    triage_reason: str = "",
    lane_prior_win: float | None = None,
    lane_prior_ev: float | None = None,
) -> str:
    """Run Pass 2-JUDGE locally, write ``{ticker}_arbitration.md``, extract watch_levels.

    Returns the cleaned arbitration text (empty string on failure).
    """
    if not (clean_response and clean_ind_response):
        logger.warning(f"[{ticker}] Pass 2-JUDGE skipped because one or both reports failed.")
        return ""

    logger.info(f"[{ticker}] Running Senior PM Ponytail Judge (Cross-Examining Report A vs Report B)...")

    judge_sys_prompt = _build_judge_sys_prompt(ticker)

    atr14_val = (
        dw_dict.get("RSI2 ATR14")
        or dw_dict.get("rsi2_atr14")
        or dw_dict.get("Wilder ATR 14")
        or dw_dict.get("ATR 14")
        or f_parsed.get("atr14")
        or f_parsed.get("rsi2_atr14")
        or "N/A"
    )
    exp_move_val = dw_dict.get("Exp Move Pct 21b") or f_parsed.get("exp_move_pct") or "N/A"
    raw_zone_flags = dw_dict.get("Zone RR Flags Pack") or dw_dict.get("Zone RR Flags") or f_parsed.get("zone_rr_flags")
    zone_rr_flags = _decode_zone_rr_flags(raw_zone_flags)

    raw_act = dw_dict.get("Action Long Code")
    if raw_act is None and dw_dict.get("Context Action Pack") is not None:
        try:
            raw_act = int(round(float(dw_dict.get("Context Action Pack")))) % 32
        except (ValueError, TypeError):
            raw_act = None
    if raw_act is None:
        raw_act = f_parsed.get("action_long_code")
    if raw_act is None:
        raw_act = f_parsed.get("action_long")
    act_long_code = str(raw_act) if raw_act is not None else "N/A"

    dw_close = dw_dict.get("Close") or dw_dict.get("close") or "N/A"
    dw_bar_date = dw_dict.get("bar_date") or date_str

    iv30_val = dw_dict.get("energy_iv30") or dw_dict.get("iv30") or f_parsed.get("energy_iv30") or f_parsed.get("iv30") or "N/A"
    iv_rank_val = dw_dict.get("energy_ivrank") or dw_dict.get("iv_rank") or f_parsed.get("energy_ivrank") or f_parsed.get("iv_rank") or "N/A"

    lane_prior_str = f"win={lane_prior_win:.0%}, ev={lane_prior_ev:.2f}R" if lane_prior_win is not None and lane_prior_ev is not None else "N/A"

    ground_truth = (
        f"--- GROUND TRUTH MARKET FACTS (VERIFIED AT RUN TIME) ---\n"
        f"- Ticker: {ticker} | Date: {date_str} | DW Bar Date: {dw_bar_date} | DW Close: {dw_close}\n"
        f"- Position Mode: {kind} (MANAGE = already owned in portfolio; NEW = prospective entry)\n"
        f"- Triage Lane & Setup: {setup_lane or 'STANDARD'}\n"
        f"- Triage Reason: {triage_reason or 'None'}\n"
        f"- Lane Prior: {lane_prior_str}\n"
        f"{active_pos_block}\n"
        f"- Live Quote: {live_quote_block.strip() if live_quote_block else 'N/A'}\n"
        f"- Earnings Date & Event Risk: {earnings_fact_block.strip() if earnings_fact_block else 'N/A'}\n"
        f"- Recent Daily Alerts / Triggers: {'; '.join(alerts_list) if alerts_list else 'None recorded'}\n"
        f"- Macro News & Fed/Yields: {macro_news.strip() if macro_news else 'N/A'}\n"
        f"- Macro Grounding: {macro_grounded_block.strip() if macro_grounded_block else 'N/A'}\n"
        f"- Key Technical & Volatility Ground Truths:\n"
        f"  * Pine ATR(14): {atr14_val} | Exp Move Pct 21b: {exp_move_val}%\n"
        f"  * Decoded Zone RR Flags: {zone_rr_flags} | Action Long Code: {act_long_code}\n"
        f"  * Moving Averages: MA20={f_parsed.get('ma20', 'N/A')}, MA50={f_parsed.get('ma50', 'N/A')}, MA200={f_parsed.get('ma200', 'N/A')}\n"
        f"  * Volume Profile: POC={f_parsed.get('vp_poc', 'N/A')}, VAL={f_parsed.get('vp_val', 'N/A')}, VAH={f_parsed.get('vp_vah', 'N/A')}\n"
        f"  * Volatility: HV20={f_parsed.get('hv20', 'N/A')}%, IV30={iv30_val}%, IV Rank={iv_rank_val}%\n"
        f"  * Pine Levels: Long Entry Zone Bot={dw_dict.get('Long Entry Zone Bot', 'N/A')}, "
        f"Long Entry Zone Top={dw_dict.get('Long Entry Zone Top', 'N/A')}, "
        f"Long Stop Loss={dw_dict.get('Long Stop Loss', 'N/A')}, "
        f"Long Target={dw_dict.get('Long Target', 'N/A')}, "
        f"Long RR At Market={dw_dict.get('Long RR At Market', 'N/A')}\n"
        f"- EMPIRICAL PRIORS & EVIDENCE DISCIPLINE:\n"
        f"  * You must NOT cite or rely on Directional Probability or Buy Score (they are empirical noise).\n"
        f"  * Evaluate solely based on measured structural zones, R:R tier (>= 3.0), and empirical priors."
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
                3. Issue the final binding directive and output the exact json:watch_levels block FIRST.
                4. If USER ACTIVE BROKER POSITION is present, issue an explicit 'Active Holding Playbook' covering profit scale-out at Target 1, advancing stop to breakeven, and covered call yield tactics.
                """

    judge_response = query_local_llm(
        system_prompt=judge_sys_prompt,
        user_prompt=judge_user_prompt,
        json_mode=False,
        use_openrouter=False,
        use_tools=False,
        disable_thinking=True,
        max_tokens=4096,
    )

    if not judge_response:
        logger.warning(f"[{ticker}] Pass 2-JUDGE returned empty response.")
        return ""

    raw_judge = judge_response.strip()

    # Extract structured watch_levels JSON from raw response BEFORE any trimming
    watch_json_match = re.search(
        r"```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{.*?\})\s*```", raw_judge, re.DOTALL
    )

    safe = ticker.replace(":", "_")
    arbitration_path = reports_dir / f"{safe}_arbitration.md"

    # Trim preamble before JSON block or heading
    clean_judge = raw_judge
    first_block = re.search(r"(?m)^(?:```json|#+\s+)", clean_judge)
    if first_block and first_block.start() > 0:
        clean_judge = clean_judge[first_block.start():].strip()
    if not re.search(r"(?m)^#\s+", clean_judge):
        clean_judge = f"# {ticker} | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n" + clean_judge

    if not watch_json_match:
        logger.warning(f"[{ticker}] Senior PM Arbitration missing watch_levels JSON block! Marking verdict = NO_LEVELS.")
        clean_judge += "\n\n> ⚠️ **VERDICT: NO_LEVELS** — Arbitration failed to emit structured watch_levels JSON block. Not persisted to watchlist."
        arbitration_path.write_text(clean_judge, encoding="utf-8")
        return clean_judge

    try:
        watch_data = json.loads(watch_json_match.group(1))
        watch_data.setdefault("ticker", ticker)
        watch_data.setdefault("date", date_str)
        watch_data["kind"] = kind
        model_lane = watch_data.get("setup_lane") or setup_lane or "RR_SETUP"
        watch_data["setup_lane"] = model_lane

        from src.logic.level_validation import validate_levels
        from src.tracking.watch_manager import upsert_watch_target
        from src.tracking.suggestions_ledger import append_suggestion, log_rejected_plan
        plan = {
            **watch_data.get("shares_plan", {}),
            "options_plan": watch_data.get("options_plan", {}),
            "ticker": ticker,
            "date": date_str,
            "side": watch_data.get("side", "LONG"),
            "kind": kind,
            "setup_lane": model_lane,
        }
        _ok, _reasons = validate_levels(plan, dw_dict, watch_data.get("side", "LONG"), ticker=ticker, date_str=date_str)
        if not _ok:
            logger.warning(
                f"[{ticker}] Level gate FAILED: {'; '.join(_reasons)} — "
                f"NOT persisting to watch_targets or suggestions ledger."
            )
            log_rejected_plan(ticker, date_str, plan, _reasons)
            clean_judge += f"\n\n> 🛑 **LEVEL GATE REJECTED**: {'; '.join(_reasons)}"
            arbitration_path.write_text(clean_judge, encoding="utf-8")
            return clean_judge

        watch_path = tdir / f"{safe}_watch_levels.json"
        watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")

        raw_watch_path = raw_dir / safe / f"{safe}_watch_levels.json"
        if raw_watch_path != watch_path:
            raw_watch_path.parent.mkdir(parents=True, exist_ok=True)
            raw_watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")

        logger.info(f"[{ticker}] Extracted structured watch levels -> {watch_path}")

        sp = watch_data.get("shares_plan", {})
        try:
            atr_at_signal = float(atr14_val) if atr14_val != "N/A" else None
        except (ValueError, TypeError):
            atr_at_signal = None
        try:
            spot_at_signal = float(dw_close) if dw_close != "N/A" else None
        except (ValueError, TypeError):
            spot_at_signal = None
        try:
            raw_rr_mkt = dw_dict.get("Long RR At Market") or dw_dict.get("rr_at_market")
            rr_at_market_at_signal = float(raw_rr_mkt) if raw_rr_mkt is not None else None
        except (ValueError, TypeError):
            rr_at_market_at_signal = None

        sugg_id = append_suggestion({
            "ticker": ticker,
            "date": date_str,
            "source": "judge",
            "side": watch_data.get("side", "LONG"),
            "entry_type": sp.get("entry_type", "LIMIT"),
            "entry_low": sp.get("entry_zone_low"),
            "entry_high": sp.get("entry_zone_high"),
            "breakout_level": sp.get("breakout_level"),
            "stop": sp.get("tactical_stop"),
            "target_1": sp.get("target_1"),
            "target_2": sp.get("target_2"),
            "planned_rr": sp.get("rr_ratio"),
            "verdict": watch_data.get("verdict"),
            "gate_status": "PASS",
            "setup_lane": model_lane,
            "kind": kind,
            "atr_at_signal": atr_at_signal,
            "spot_at_signal": spot_at_signal,
            "rr_at_market_at_signal": rr_at_market_at_signal,
            "lane_prior_win": lane_prior_win,
            "lane_prior_ev": lane_prior_ev,
            "_datawindow": dw_dict,
            "notes": f"Judge directive: {watch_data.get('verdict')}",
        })
        if sugg_id and sugg_id > 0:
            watch_data["suggestion_id"] = sugg_id

        upsert_watch_target(watch_data)
        logger.info(f"[{ticker}] Watch levels upserted to SQLite watch DB (suggestion_id={watch_data.get('suggestion_id')}).")

        # Record into last_researched table
        try:
            from src.tracking.watch_manager import record_last_researched
            try:
                ac_raw = dw_dict.get("Action Long Code")
                if ac_raw is None and dw_dict.get("Context Action Pack") is not None:
                    ac = int(round(float(dw_dict.get("Context Action Pack")))) % 32
                elif ac_raw is not None:
                    ac = int(round(float(ac_raw)))
                else:
                    ac = 0
            except (ValueError, TypeError):
                ac = 0
            try:
                raw_z = dw_dict.get("Zone RR Flags Pack") or dw_dict.get("zone_rr_flags_pack")
                if raw_z is not None:
                    iz = int(round(float(raw_z))) & 1
                else:
                    iz = 1 if (dw_dict.get("Long In Zone") or dw_dict.get("in_zone")) else 0
            except (ValueError, TypeError):
                iz = 0
            try:
                rr_mkt = float(dw_dict.get("Long RR At Market") or dw_dict.get("rr_at_market") or 0.0)
            except (ValueError, TypeError):
                rr_mkt = 0.0
            try:
                st_val = float(dw_dict.get("Long Stop Loss") or sp.get("tactical_stop") or 0.0)
            except (ValueError, TypeError):
                st_val = 0.0
            try:
                tgt_val = float(dw_dict.get("Long Target") or sp.get("target_1") or 0.0)
            except (ValueError, TypeError):
                tgt_val = 0.0
            record_last_researched(
                ticker=ticker,
                date_str=date_str,
                action_code=ac,
                in_zone=iz,
                rr_at_market=rr_mkt,
                stop=st_val,
                target=tgt_val,
            )
        except Exception as e_rec:
            logger.debug(f"[{ticker}] Failed recording last_researched: {e_rec}")

    except Exception as e:
        logger.warning(f"[{ticker}] Failed to process watch levels JSON: {e}")
        try:
            from src.tracking.suggestions_ledger import log_rejected_plan
            log_rejected_plan(ticker, date_str, locals().get("watch_data", {}), [f"JSON/Processing exception: {e}"])
        except Exception:
            pass
        clean_judge += f"\n\n> ⚠️ **VERDICT: NO_LEVELS** — Failed to process watch levels JSON: {e}"

    arbitration_path.write_text(clean_judge, encoding="utf-8")
    logger.info(f"[{ticker}] Senior PM Arbitration generated at {arbitration_path}!")
    return clean_judge
