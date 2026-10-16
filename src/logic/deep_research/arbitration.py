"""arbitration.py — Pass 2-JUDGE: Ponytail Senior PM cross-examination and watch_levels extraction."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.clients.llm_client import query_local_llm

MAX_LIMIT_ATR_BELOW_SPOT = 2.0

logger = logging.getLogger(__name__)


def _record_research_completed(ticker: str, date_str: str, dw_dict: Dict[str, Any], sp: Dict[str, Any]) -> None:
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
            st_val = float(dw_dict.get("Long Stop Loss") or sp.get("tactical_stop") or sp.get("stop") or 0.0)
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


def _build_judge_sys_prompt(ticker: str, prior_plan_outcome_block: str = "") -> str:
    prior_plan_section = ""
    if prior_plan_outcome_block:
        prior_plan_section = (
            "PRIOR PLAN OUTCOME (history — what the previous plan's levels actually did):\n"
            f"{prior_plan_outcome_block}\n\n"
            "Adjudicate with this history in view: a trigger that broke and held, a failed breakout that closed "
            "back below, a retest of the entry zone, an untouched level, or a crossed stop are facts about the "
            "levels — never conclusions from the spot price alone. Judge your binding levels against what those "
            "levels have already done, and keep any still-relevant trigger armed rather than dropping it.\n\n"
        )
    return (
        prior_plan_section
        + "You are the Chief Investment Officer & Senior Portfolio Manager operating under Ponytail Finance rules "
        "(Occam's Razor, minimal bloat, hard math, ruthless risk management).\n\n"
        "You have received two independent reports for this ticker:\n"
        "- REPORT A: Proprietary Quantitative Engine (Bible rules, Code 8 / Zone R:R, Titanium levels)\n"
        "- REPORT B: Independent Macro & Volume Profile Study (Macro attribution, 12 MAs, VRVP POC, IV/HV Forensics)\n\n"
        "Your job is to cross-examine both reports with a strict FOR vs AGAINST trial, settle their disagreements, and issue the FINAL binding trading directive.\n\n"
        "LEVELS ARE NOT YOURS TO SET. When the ground truth shows a valid Pine plan, its entry zone, stop and target_1 "
        "are the binding geometry and the code overwrites whatever you write with them. Copy them into shares_plan. "
        "You may add target_2 only above the Pine target. Never tighten the stop: a stop inside one normal day's range is the "
        "measured reason most of these trades stop out. When there is no valid Pine plan, do not invent one: output CASH_SKIP "
        "and name the level you would want to see in the summary.\n\n"
        "YOUR JOB is the judgment the engine cannot do: is there a concrete reason NOT to take the Pine plan (earnings or "
        "another binary event inside the hold, news that changes the story, a broken trend the zone sits in, distribution on "
        "volume), and what single event kills it. A veto needs a specific fact, not a mood.\n\n"
        "VERDICTS (the ground truth's DETERMINISTIC STATE line already applies the measured gate):\n"
        "- ENTER (Limit @ Floor): state is ACTIONABLE and you have no concrete veto.\n"
        "- ARMED: state is ARMED (a resting limit within reach) and you have no concrete veto. Say which price fills it.\n"
        "- CASH_SKIP: state is NO TRADE, or you have a concrete veto. Say which.\n"
        "- WATCH (UNMEASURED MOMENTUM): a level break on RVOL >= 1.0 that fails the gate. Start the summary with "
        "'UNMEASURED MOMENTUM:'. No measured edge.\n"
        "Do not write a conviction score; it carries no information.\n"
        "CRITICAL CONSTRAINT: You CANNOT output ENTER if engine triage is 'no_setup' or the actionable gate fails.\n\n"
        "Output in this exact markdown format (JSON BLOCK MUST COME FIRST):\n\n"
        "```json:watch_levels\n"
        "{\n"
        f'  "ticker": "{ticker}",\n'
        '  "verdict": "ENTER (Limit @ Floor)|ENTER (Breakout)|ARMED|WATCH|CASH_SKIP",\n'
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
        "LEVEL GEOMETRY HARD RULE: for a LONG breakout, target_1 and target_2 MUST both sit strictly "
        "ABOVE breakout_level, and the R:R measured AT the trigger — (target_1 - breakout_level) / "
        "(breakout_level - breakout_stop) — must clear the R:R floor. A trigger whose targets sit at "
        "or below it is not a breakout: the trigger stays armed as a real watched level and its "
        "targets are rebuilt above it (tagged targets_rebuilt) — the trigger itself is never zeroed.\n\n"
        f"# {ticker} | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n"
        "## 🟢 THE CASE FOR (Bull Cross-Examination)\n"
        "[The strongest, evidence-backed arguments synthesized across both reports for why this trade should be taken]\n\n"
        "## 🔴 THE CASE AGAINST (Bear Cross-Examination & Traps)\n"
        "[The strongest risk arguments, hidden traps, and friction points synthesized across both reports for why this trade should be avoided or hedged]\n\n"
        "## ⚖️ THE JUDGE'S FINAL RULING\n"
        "* **Concurrence:** [Where Model A and Model B 100% agree]\n"
        "* **Conflict Resolution:** [Where they disagreed, which model is correct, and why]\n"
        "* **Tiered Options Directives & Strict Soundness Rules:** Always provide a 3-tiered options menu across durations and account structures:\n"
        "  1. Tactical 30-45 DTE Directional Spread (primary options vehicle):\n"
        "     - FOR LONG / BULLISH SETUPS: Strongly favor BULL CALL DEBIT SPREADS (e.g. 30-45 DTE, ATM Long Call / OTM Short Call at Target 1, targeting >= 1.5:1 to 3:1 R:R in trader's favor) or Deep ITM LEAPS (0.75-0.85 delta).\n"
        "     - FORBIDDEN NEAR-THE-MONEY CREDIT SPREADS: NEVER sell a Bull Put Spread whose short strike is within 5% of spot or above the tactical stop loss! If price hitting your stop loss would put the short put in the money, the trade is structurally invalid.\n"
        "     - CREDIT-TO-WIDTH FLOOR: NEVER suggest a credit spread collecting < 25% of the spread width (risking > 3:1 against the trader). If an edge-appropriate credit spread does not exist at >= 1.25x ExpMove below the stop loss, set options_plan.structure = 'NONE' and actionable = false. Do NOT force an options trade when shares provide the superior R:R.\n"
        "  2. Secular Trend LEAPS (6-12 Months, deep ITM 0.75-0.85 delta to participate in secular move with defined capital and low theta decay).\n"
        "  3. Floor Income / Capital Efficiency (Covered Call against existing long shares/LEAPS, or Cash-Secured Put strictly below invalidation floor).\n"
        "* **Final Verdict:** **[ENTER (Limit @ Floor) / ARMED / CASH_SKIP / WATCH]**, plus the veto fact if any\n\n"
        "## 🎯 FINAL ACTIONABLE DIRECTIVES\n"
        "* **Equity (Shares):** [Pine zone, Pine stop, Pine target, optional Target 2, R:R at market]\n"
        "* **Options Directives (Tiered Menu):**\n"
        "  - **Primary Tactical Spread (30-45 DTE):** [Structure (favor Bull Call Debit Spread for longs), Expiry, Strikes, Net Debit/Credit, Max Loss, Break-Even, R:R]\n"
        "  - **Secular Trend LEAPS (6-12 Months):** [Expiry, Deep ITM Strike (0.75-0.85 Delta), Target Debit, Rationale]\n"
        "  - **Income / Floor Support Structure:** [Covered Call / PMCC if holding position, or CSP strictly below invalidation floor]\n"
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
    prior_plan_outcome_block: str = "",
) -> str:
    """Run Pass 2-JUDGE locally, write ``{ticker}_arbitration.md``, extract watch_levels.

    Returns the cleaned arbitration text (empty string on failure).
    """
    if not clean_response:
        logger.warning(f"[{ticker}] Pass 2-JUDGE skipped because Model A (Pine Gem) report failed.")
        return ""

    if not clean_ind_response or len(clean_ind_response.strip()) < 50:
        logger.info(f"[{ticker}] Model B independent report not provided or short — arbitrating from Model A findings.")
        clean_ind_response = f"(Model B independent report omitted for {ticker} — senior PM arbitrating directly from proprietary Pine Gem findings)"

    logger.info(f"[{ticker}] Running Senior PM Ponytail Judge (Cross-Examining Report A vs Report B)...")

    judge_sys_prompt = _build_judge_sys_prompt(ticker, prior_plan_outcome_block=prior_plan_outcome_block)

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

    lane_prior_str = f"win={lane_prior_win:.0f}%, ev={lane_prior_ev:.2f}R" if lane_prior_win is not None and lane_prior_ev is not None else "N/A"

    from src.logic.pine_levels import pine_long_plan
    from src.logic.trade_state import trade_state

    _pine = pine_long_plan(dw_dict)
    if _pine:
        _ts = trade_state(_pine["close"], _pine["zone_low"], _pine["zone_high"], _pine["stop"], _pine["target"],
                          atr14_val if atr14_val != "N/A" else None, fade=dw_dict.get("fade_long"))
        state_line = (f"- DETERMINISTIC STATE (binding Pine plan): {_ts['label']} — {_ts['detail']}. Pine zone "
                      f"{_pine['zone_low']}–{_pine['zone_high']}, stop {_pine['stop']}, target {_pine['target']}\n")
    else:
        state_line = "- DETERMINISTIC STATE: NO TRADE — Pine has no valid long plan (stop below close below target). Do not invent levels.\n"

    ground_truth = (
        f"--- GROUND TRUTH MARKET FACTS (VERIFIED AT RUN TIME) ---\n"
        f"{state_line}"
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
        f"  * Evaluate solely based on measured structural zones, R:R gate 2.0, stronger tier 3.0, and empirical priors.\n"
        f"  * HARD CONSTRAINT: If engine triage is 'no_setup' or actionable gate fails, you CANNOT output ENTER."
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
        triage_lane = setup_lane
        if not triage_lane or triage_lane in ("WATCH_SHADOW", "UNKNOWN", "DEFAULT", "STANDARD"):
            triage_lane = "JUDGE"
        watch_data["setup_lane"] = triage_lane

        from src.logic.pine_levels import apply_pine_levels

        apply_pine_levels(watch_data, dw_dict)

        from src.logic.report_level_extractor import _apply_options_cleanup
        from src.logic.level_validation import validate_levels
        from src.tracking.watch_manager import upsert_watch_target
        from src.tracking.suggestions_ledger import append_suggestion, log_rejected_plan

        spot_val = watch_data.get("spot") or dw_dict.get("Close") or dw_dict.get("close")
        try:
            spot_num = float(spot_val) if spot_val is not None and spot_val != "N/A" else 0.0
        except (ValueError, TypeError):
            spot_num = 0.0
        _apply_options_cleanup(watch_data, dw_dict, spot_num, safe)

        plan = {
            **watch_data.get("shares_plan", {}),
            "options_plan": watch_data.get("options_plan", {}),
            "ticker": ticker,
            "date": date_str,
            "side": watch_data.get("side", "LONG"),
            "kind": kind,
            "setup_lane": triage_lane,
            "source": "judge",
            "is_judge": True,
            "spot": spot_num or watch_data.get("spot"),
        }

        # Code caps the verdict if triage is no_setup or actionable gate fails
        from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
        gate_in = gate_inputs_from_datawindow(dw_dict)
        gate_ok, gate_reasons = is_actionable(gate_in, plan)
        is_no_setup = str(setup_lane or "").strip().lower() in ("no_setup", "none", "")

        verdict = str(watch_data.get("verdict", "")).strip()
        if verdict.upper() == "ARMED":
            # ARMED is the judge's word; STALK stays the stored token every ledger query filters on.
            verdict = "STALK"
            watch_data["verdict"] = verdict
        if "ENTER" in verdict.upper():
            if is_no_setup or not gate_ok:
                capped_verdict = "CASH_SKIP" if is_no_setup else "STALK"
                logger.warning(
                    f"[{ticker}] Capping judge verdict '{verdict}' to '{capped_verdict}' "
                    f"(no_setup={is_no_setup}, gate_ok={gate_ok}, reasons={gate_reasons})"
                )
                watch_data["verdict"] = capped_verdict
                verdict = capped_verdict

        # The number follows the verdict's band, capped or not: cap_conviction is the single
        # rule the alert triage uses, and a "STALK" card that keeps the judge's 8 is the same
        # absurd badge in a different column.
        from src.tracking.alert_evaluator import cap_conviction

        _panel_triage = (
            "PASS" if "ENTER" in verdict.upper()
            else ("CUT" if "CASH_SKIP" in verdict.upper() else "WATCH")  # STALK and WATCH both → "WATCH" band
        )
        watch_data["conviction"] = cap_conviction(
            watch_data.get("conviction"), _panel_triage, [], has_plan=bool(plan)
        )

        _ok, _reasons = validate_levels(plan, dw_dict, watch_data.get("side", "LONG"), ticker=ticker, date_str=date_str)

        # C2: the C1 rebuild happens inside validate_levels on `plan`, but the plan that
        # gets persisted (upsert_watch_target, append_suggestion, and the display-only
        # watch_levels.json files) is watch_data["shares_plan"]. Propagate the kept
        # trigger and its rebuilt targets so no surface keeps stale values — a trigger is
        # never zeroed, and the rebuild tag rides with it.
        if plan.get("breakout_demoted_reason") and isinstance(watch_data.get("shares_plan"), dict):
            _sp = watch_data["shares_plan"]
            _sp["breakout_level"] = plan.get("breakout_level") or _sp.get("breakout_level") or 0.0
            _sp["breakout_stop"] = plan.get("breakout_stop") or _sp.get("breakout_stop") or 0.0
            _sp["target_1"] = plan.get("target_1", _sp.get("target_1"))
            _sp["target_2"] = plan.get("target_2", _sp.get("target_2"))
            _sp["targets_rebuilt"] = bool(plan.get("targets_rebuilt"))
            _sp["targets_rebuilt_basis"] = plan.get("targets_rebuilt_basis")
            _sp["breakout_demoted_reason"] = plan["breakout_demoted_reason"]
            logger.info(
                f"[{ticker}] C1 trigger kept: ${_sp['breakout_level']} with rebuilt targets "
                f"T1 ${_sp['target_1']} / T2 ${_sp['target_2']}"
            )

        # STALK is only a label for armed levels: a judge plan that arms nothing has
        # no cross to alert on and is rejected at write time like any other.
        if _ok:
            from src.tracking.level_engine import has_armed_level

            if not has_armed_level(
                {k: plan.get(k) for k in (
                    "entry_zone_low", "entry_zone_high", "breakout_level", "breakout_stop",
                    "tactical_stop", "target_1", "target_2", "invalidation_price",
                    "breakout_demoted_reason",
                )},
                side=str(watch_data.get("side", "LONG")).upper(),
            ):
                _ok = False
                _reasons.append("ARMED with no armed level — the plan arms nothing to watch")

        if not _ok:
            logger.warning(
                f"[{ticker}] Level gate FAILED: {'; '.join(_reasons)} — "
                f"NOT persisting to watch_targets or suggestions ledger."
            )
            log_rejected_plan(ticker, date_str, plan, _reasons)
            try:
                _rsp = watch_data.get("shares_plan") or {}
                _num = lambda k: dw_dict.get(k) if dw_dict.get(k) not in (None, "", "N/A") else None
                append_suggestion({
                    "ticker": ticker, "date": date_str, "source": "judge",
                    "side": watch_data.get("side", "LONG"),
                    "entry_type": _rsp.get("entry_type", "LIMIT"),
                    "entry_low": _rsp.get("entry_zone_low"), "entry_high": _rsp.get("entry_zone_high"),
                    "breakout_level": _rsp.get("breakout_level"), "stop": _rsp.get("tactical_stop"),
                    "target_1": _rsp.get("target_1"), "target_2": _rsp.get("target_2"),
                    "verdict": watch_data.get("verdict"), "gate_status": "REJECTED_BY_GATE",
                    "gate_reasons": "; ".join(_reasons), "setup_lane": triage_lane, "kind": kind,
                    "atr_at_signal": atr14_val if atr14_val != "N/A" else None,
                    "spot_at_signal": spot_num or None,
                    "rr_at_market_at_signal": plan.get("rr_at_market"),
                    "signal_pack": _num("Signal Pack"), "ext_z": _num("Ext Z Self Relative"),
                    "_datawindow": dw_dict, "notes": "Judge plan rejected by level gate",
                })
            except Exception as e_rej:
                logger.debug(f"[{ticker}] rejected judge suggestion not logged: {e_rej}")
            clean_judge += f"\n\n> 🛑 **LEVEL GATE REJECTED**: {'; '.join(_reasons)}"
            # Still write watch_levels.json so the briefing/UI can display the thesis card.
            # Mark level_gate_rejected=True and options_plan actionable=False so callers know not to treat these as actionable levels.
            watch_data["level_gate_rejected"] = True
            watch_data["gate_reasons"] = _reasons
            if "options_plan" in watch_data and isinstance(watch_data["options_plan"], dict):
                watch_data["options_plan"]["actionable"] = False
            # Write to reports_dir (primary) and raw_dir (secondary) — mirrors normal pass-through.
            # Both are needed because get_report_bundle checks raw_dir first.
            gate_watch_path = reports_dir / f"{safe}_watch_levels.json"
            gate_watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")
            raw_gate_watch_path = raw_dir / safe / f"{safe}_watch_levels.json"
            raw_gate_watch_path.parent.mkdir(parents=True, exist_ok=True)
            raw_gate_watch_path.write_text(json.dumps(watch_data, indent=2), encoding="utf-8")
            logger.info(f"[{ticker}] watch_levels.json written (gate-rejected, display-only) → {gate_watch_path}")
            arbitration_path.write_text(clean_judge, encoding="utf-8")
            _record_research_completed(ticker, date_str, dw_dict, watch_data.get("shares_plan") or plan)
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
        # PB funnel: Signal Pack bit 32. Stays None when the column is absent (pre-PB scrape) so a
        # ledger row is never mistaken for a measured no-PB row.
        try:
            raw_sig = dw_dict.get("Signal Pack") if dw_dict.get("Signal Pack") is not None else dw_dict.get("signal_pack")
            pb_funnel = None if raw_sig is None else int(bool(int(round(float(raw_sig))) & 32))
        except (ValueError, TypeError):
            pb_funnel = None

        try:
            el = float(sp.get("entry_zone_low") or 0.0)
            eh = float(sp.get("entry_zone_high") or 0.0)
            st = float(sp.get("tactical_stop") or 0.0)
            t1 = float(sp.get("target_1") or 0.0)
            mid = (el + eh) / 2.0 if (el and eh) else (eh or el)
            if mid > st and t1 > mid:
                computed_rr = round((t1 - mid) / (mid - st), 4)
            else:
                computed_rr = None
        except Exception:
            computed_rr = None

        # When R:R at spot is below active floor, compute exact limit price where R:R reaches floor
        from src.tracking import rr_config
        active_floor = rr_config.min_rr()
        limit_price = None
        curr_spot = spot_num or spot_at_signal or 0.0
        is_geo_valid = (
            st > 0 and t1 > st 
            and (eh == 0.0 or eh > st)
            and (el == 0.0 or el > st)
        )
        is_terminal = (curr_spot > 0 and st > 0 and curr_spot <= st) or (curr_spot > 0 and t1 > 0 and curr_spot >= t1)
        if not is_terminal and is_geo_valid:
            spot_rr = ((t1 - curr_spot) / (curr_spot - st)) if (curr_spot > st) else 0.0
            if spot_rr < active_floor:
                calc_limit = round((t1 + active_floor * st) / (1.0 + active_floor), 2)
                try:
                    atr_chk = float(atr_at_signal or (atr14_val if atr14_val and atr14_val != "N/A" else 1.0))
                except Exception:
                    atr_chk = 1.0
                far_atr = (curr_spot - calc_limit) / max(0.01, atr_chk) if curr_spot > 0 else 0.0
                # Corpus touch odds (532 names, 21 bars): limit 2 ATR below close fills 46%, 3 ATR 29%, 4 ATR 19%.
                if far_atr > MAX_LIMIT_ATR_BELOW_SPOT and calc_limit > st:
                    watch_data["limit_too_far"] = True
                    watch_data["wait_for"] = (
                        f"R:R {spot_rr:.2f} at market; floor {active_floor:g} needs ${calc_limit:.2f}, "
                        f"{far_atr:.1f} ATR below, unlikely to fill. No entry now."
                    )
                elif calc_limit > st and (curr_spot <= 0 or calc_limit < curr_spot) and (calc_limit - st) / max(0.01, atr_chk) >= 0.7:
                    limit_price = calc_limit
                    sp["limit_price"] = limit_price
                    sp["trigger_price"] = limit_price
                    sp["wait_for"] = f"wait for ${limit_price:.2f}"
                    watch_data["trigger_price"] = limit_price
                    watch_data["wait_for_price"] = limit_price
                    watch_data["wait_for"] = f"wait for ${limit_price:.2f}"

        planned_rr = computed_rr if computed_rr is not None else sp.get("rr_ratio")
        real_gate_status = "PASS" if (gate_ok and _ok) else "REJECTED_BY_GATE"

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
            "planned_rr": planned_rr,
            "verdict": watch_data.get("verdict"),
            "gate_status": real_gate_status,
            "setup_lane": triage_lane,
            "kind": kind,
            "atr_at_signal": atr_at_signal,
            "spot_at_signal": spot_at_signal,
            "rr_at_market_at_signal": rr_at_market_at_signal,
            "lane_prior_win": lane_prior_win,
            "lane_prior_ev": lane_prior_ev,
            "pb_funnel": pb_funnel,
            "_datawindow": dw_dict,
            "notes": f"Judge directive: {watch_data.get('verdict')}" + (f" | {sp.get('wait_for')}" if sp.get('wait_for') else ""),
        })
        if sugg_id and sugg_id > 0:
            watch_data["suggestion_id"] = sugg_id

        watch_data["signal_pack"] = gate_in.get("signal_pack")
        watch_data["fade"] = gate_in.get("fade_long")
        watch_data["action_long"] = gate_in.get("action_long")
        watch_data["ext_z"] = gate_in.get("ext_z_self")
        watch_data["atr_at_signal"] = atr_at_signal or gate_in.get("atr14")
        watch_data["zone_rr_flags"] = dw_dict.get("Zone RR Flags Pack") or dw_dict.get("zone_rr_flags")
        watch_data["rr_at_market_at_signal"] = rr_at_market_at_signal or gate_in.get("long_rr_at_market")
        watch_data["datawindow"] = gate_in
        watch_data["actionable"] = 1 if (gate_ok and _ok) else 0

        upsert_watch_target(watch_data)
        logger.info(f"[{ticker}] Watch levels upserted to SQLite watch DB (suggestion_id={watch_data.get('suggestion_id')}).")

        # Immediately sync into suggested_trades_audit so the trade vehicles (Shares & Options) are tracked
        try:
            from src.tracking.suggested_trades_auditor import sync_suggested_trades_from_watch_targets
            sync_suggested_trades_from_watch_targets()
        except Exception as e_sync:
            logger.debug(f"[{ticker}] Note syncing suggested_trades_audit: {e_sync}")

        # Record into last_researched table
        _record_research_completed(ticker, date_str, dw_dict, sp)

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
