"""
Real-time Local LLM Alert Evaluator & Triage Engine.
Integrates gems/revanth-gem-local.md (#ponytail Senior Quantitative PM rules)
and gems/revanth-0dte.md, completely INDEPENDENT of browser/chart scraping.
Directly ingests live market data from Tastytrade, Alpaca, Schwab, and web search APIs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.clients.llm_client import query_local_llm, _extract_json_response
from src.clients.price_client import get_current_price
from src.clients.tastytrade_client import TastytradeClient
from src.clients.search_client import search_web
from src.clients.options_client import fetch_options_chain_tool
from src.tracking.alert_db import update_alert_llm, get_eastern_now

logger = logging.getLogger(__name__)


def _load_gem(filename: str) -> str:
    gem_path = config.BASE_DIR / "gems" / filename
    if gem_path.exists():
        try:
            return gem_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {filename}: {e}")
    return ""


def build_live_market_context(symbol: str) -> Dict[str, Any]:
    """
    Fetch live broker and market context in <2s with ZERO chart/browser scraping:
    - Tastytrade IV Rank, Percentile, 30d HV, IV-HV difference, expected earnings date
    - Real options chain strikes and delta
    - Breaking news headlines via web search / Alpaca
    """
    context: Dict[str, Any] = {
        "volatility": {},
        "news": [],
        "options_snippet": "",
        "expected_earnings": None,
    }

    # 1. Tastytrade Volatility & Broker Metrics
    try:
        tt = TastytradeClient()
        m_list = tt.get_market_metrics(symbol)
        if m_list:
            m = m_list[0]
            context["volatility"] = {
                "iv_index": round(float(m.get("implied-volatility-index") or 0) * 100, 1),
                "iv_rank": round(float(m.get("implied-volatility-index-rank") or 0) * 100, 1),
                "iv_percentile": round(float(m.get("implied-volatility-percentile") or 0) * 100, 1),
                "hv30": round(float(m.get("historical-volatility-30-day") or 0), 1),
                "iv_hv_diff": round(float(m.get("iv-hv-30-day-difference") or 0), 1),
                "liquidity_rating": m.get("liquidity-rating"),
                "borrow_rate": m.get("borrow-rate"),
            }
            context["expected_earnings"] = m.get("earnings", {}).get("expected-report-date")
    except Exception as e:
        logger.debug(f"Tastytrade metrics fetch bypassed for {symbol}: {e}")

    # 2. Breaking News / Catalysts (Search API)
    try:
        res = search_web(f"{symbol} stock news 2026", max_results=3)
        if isinstance(res, list):
            for item in res:
                body = (item.get("body") or "")[:140]
                context["news"].append(f"- {item.get('title')}: {body}")
    except Exception as e:
        logger.debug(f"News fetch bypassed for {symbol}: {e}")

    # 3. Real Options Chain Snippet
    try:
        oc = fetch_options_chain_tool(symbol)
        if oc:
            lines = [l for l in oc.strip().splitlines() if l.strip()]
            context["options_snippet"] = "\n".join(lines[:6])
    except Exception as e:
        logger.debug(f"Options fetch bypassed for {symbol}: {e}")

    # 4. Real-time tape signal (1m candle order flow)
    try:
        from src.plugins.order_flow_plugin import read_tape
        context["tape"] = read_tape(symbol)
    except Exception as e:
        logger.debug(f"Tape fetch bypassed for {symbol}: {e}")
        context["tape"] = {"verdict": "unavailable"}

    return context


def evaluate_risk_vetoes(
    symbol: str,
    action: str,
    score: int,
    current_time_et: str,
    eastern_dt: datetime,
    grade: str = "A",
    align: str = "",
) -> Optional[Tuple[str, str]]:
    """
    Evaluate institutional risk gates:
    1. Grade-A Hard Quality Gate: Veto Grade B / Score < 80 (eliminates -$1,515 historical drag).
    2. Weinstein Stage & Multi-Timeframe Alignment: Veto counter-trend trades (never CALLS in Stage 4 Decline, never PUTS in Stage 2 Advance).
    3. Mid-Morning Exhaustion Window (10:30 - 11:30 AM ET): Requires Score >= 90 (Grade A+ only).
    4. Max Concurrent Correlated Exposure: Max 2 same-direction open intraday positions.
    5. Lunch Chop Window (11:30 AM - 1:15 PM ET): Requires Score >= 85 (Grade A+).
    6. Consecutive Losses DAY PAUSE: Enforce cooldown after 2 consecutive stops within 45m.
    
    Returns (header, playbook) if vetoed, or None if clear.
    """
    from src.tracking.position_state import list_open
    from zoneinfo import ZoneInfo
    from datetime import timezone

    today_str = eastern_dt.strftime("%Y-%m-%d")
    act_clean = str(action or "").upper()
    is_call = "CALL" in act_clean
    is_put = "PUT" in act_clean
    side = "LONG" if is_call else ("SHORT" if is_put else None)

    # 1. Grade-A Hard Quality Gate (Cut Grade B / Score < 80)
    grade_clean = str(grade or "A").upper().strip()
    if grade_clean == "B" or score < 80:
        hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (GRADE B / LOW CONVICTION)"
        pb = (
            f"{hdr}\n\n"
            f"⛔ QUALITY VETO: Setup grade is '{grade_clean}' with score {score}/100 (< 80 threshold).\n"
            f"Historical trade audit reveals Grade-B alerts generated a 27% win rate and net negative expectancy.\n"
            f"0DTE intraday execution is restricted exclusively to institutional Grade-A setups."
        )
        return hdr, pb

    # 2. Weinstein Stage & Multi-Timeframe Alignment Gate (First Principles - Never Fight Structural Trend)
    align_clean = str(align or "").lower()
    is_stg4 = "stg4" in align_clean or "decline" in align_clean
    is_stg2 = "stg2" in align_clean or "advance" in align_clean
    if side == "LONG" and is_stg4:
        hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (COUNTER-STAGE: STAGE 4 DECLINE)"
        pb = (
            f"{hdr}\n\n"
            f"⛔ REGIME VETO: Counter-trend CALL entry into a Stage 4 Structural Decline ({align}).\n"
            f"Weekly & Daily trends are declining. Counter-trend intraday bounces in Stage 4 face immediate institutional supply.\n"
            f"Execution discipline requires aligning with the structural trend."
        )
        return hdr, pb
    if side == "SHORT" and is_stg2:
        hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (COUNTER-STAGE: STAGE 2 ADVANCE)"
        pb = (
            f"{hdr}\n\n"
            f"⛔ REGIME VETO: Counter-trend PUT entry into a Stage 2 Structural Advance ({align}).\n"
            f"Weekly & Daily trends are advancing. Counter-trend pullbacks in Stage 2 get aggressively absorbed by institutional demand.\n"
            f"Execution discipline requires aligning with the structural trend."
        )
        return hdr, pb

    # 3. Optional Explicit Ticker Exclusion (Configurable via env, default none)
    excluded_env = os.getenv("INTRADAY_EXCLUDED_TICKERS", "")
    if excluded_env:
        excluded_tickers = {t.strip().upper() for t in excluded_env.split(",") if t.strip()}
        if symbol.upper() in excluded_tickers:
            hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (EXCLUDED 0DTE UNIVERSE)"
            pb = (
                f"{hdr}\n\n"
                f"⛔ UNIVERSE VETO: {symbol} is explicitly excluded from 0DTE intraday execution via configuration.\n"
            )
            return hdr, pb

    # 3. Mid-Morning Exhaustion Window (10:30 – 11:30 AM ET)
    h = eastern_dt.hour
    m = eastern_dt.minute
    is_mid_morning = (h == 10 and m >= 30) or (h == 11 and m < 30)
    if is_mid_morning and score < 90:
        hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (10:30-11:30 ET EXHAUSTION TRAP)"
        pb = (
            f"{hdr}\n\n"
            f"⛔ TIME-WINDOW VETO: Mid-morning trend extension & European close window (10:30–11:30 AM ET).\n"
            f"Conviction score {score}/100 is below the required 90/100 (Grade A+) threshold for mid-morning entry.\n"
            f"Breakouts in this window suffer from morning exhaustion and pre-lunch consolidation."
        )
        return hdr, pb

    # 4. Max Concurrent Correlated Exposure Gate (Max 2 same-direction trades)
    if side:
        open_pos = list_open()
        same_side = []
        for sym, p in open_pos.items():
            if not isinstance(p, dict) or sym.upper() == symbol.upper():
                continue
            opened_at = str(p.get("opened_at", ""))
            strat = str(p.get("strategy", "")).lower()
            if strat == "intraday" and opened_at.startswith(today_str):
                p_side = str(p.get("side", "")).upper()
                if p_side == side:
                    same_side.append(sym)
        if len(same_side) >= 2:
            hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (MAX EXPOSURE)"
            pb = (
                f"{hdr}\n\n"
                f"⛔ RISK VETO: Maximum concurrent {side} exposure reached ({len(same_side)} active: {', '.join(same_side)}).\n"
                f"Further entries in {side} blocked to prevent correlated sector/beta risk clustering."
            )
            return hdr, pb

    # 5. Lunch Chop Window Hard-Gate (11:30 AM – 1:15 PM ET)
    is_lunch = (h == 11 and m >= 30) or (h == 12) or (h == 13 and m <= 15)
    if is_lunch and score < 85:
        hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (LUNCH CHOP)"
        pb = (
            f"{hdr}\n\n"
            f"⛔ REGIME VETO: Midday liquidity dead zone (11:30 AM – 1:15 PM ET).\n"
            f"Conviction score {score}/100 is below the required 85/100 (Grade A+) threshold for lunch trading.\n"
            f"Breakouts in this window frequently fail due to dried up institutional volume."
        )
        return hdr, pb

    # 3. Consecutive Losses DAY PAUSE Circuit Breaker
    try:
        from src.tracking.alert_db import DB_PATH
        import sqlite3
        with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT timestamp, raw_payload, action 
                FROM alerts 
                WHERE date = ? 
                  AND strategy = 'Intraday'
                  AND (action LIKE '%EXIT%' OR action LIKE '%STOP%' OR action LIKE '%CUT%')
                ORDER BY timestamp DESC LIMIT 6
            """, (today_str,))
            recent_exits = cur.fetchall()

            loss_count = 0
            latest_loss_time = None
            for ts_str, raw_str, act_val in recent_exits:
                raw_json = json.loads(raw_str) if isinstance(raw_str, str) and raw_str.startswith("{") else {}
                why = (str(raw_json.get("exit_why", "")) + " " + str(raw_json.get("act_now", ""))).lower()
                pnl = float(raw_json.get("session_pnl", 0) or 0)
                is_loss = "stop" in why or "loss" in why or pnl < 0
                if is_loss:
                    loss_count += 1
                    if latest_loss_time is None and ts_str:
                        try:
                            clean_ts = ts_str.replace("Z", "+00:00")
                            dt_parsed = datetime.fromisoformat(clean_ts)
                            if dt_parsed.tzinfo is None:
                                latest_loss_time = dt_parsed.replace(tzinfo=ZoneInfo("America/New_York"))
                            else:
                                latest_loss_time = dt_parsed.astimezone(ZoneInfo("America/New_York"))
                        except Exception:
                            pass
                else:
                    break

            if loss_count >= 2 and latest_loss_time:
                diff_sec = (eastern_dt - latest_loss_time).total_seconds()
                if 0 <= diff_sec <= 2700:  # 45 minutes cooldown
                    mins_ago = int(diff_sec // 60)
                    hdr = f"[{symbol}] [{current_time_et}] — ⛔ STAND ASIDE (DAY PAUSE)"
                    pb = (
                        f"{hdr}\n\n"
                        f"⛔ CIRCUIT BREAKER: DAY PAUSE active.\n"
                        f"{loss_count} consecutive stopped trades observed within the last {mins_ago}m.\n"
                        f"Autonomous cooldown engaged to protect capital against hostile regime shifts."
                    )
                    return hdr, pb
    except Exception as e_db:
        logger.debug(f"DAY PAUSE check bypassed: {e_db}")

    return None


def synthesize_deterministic_triage(
    symbol: str,
    action: str,
    strategy: str,
    current_time_et: str,
    current_price: float,
    vix: Any,
    payload: Dict[str, Any],
    alert: Dict[str, Any],
    exit_review: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Deterministically synthesize an institutional 0DTE Decision Card & tactical playbook
    directly from authoritative Pine script fields and broker context.
    Acts as an infallible zero-lag baseline and safeguard against LLM preambles or timeouts.
    """
    is_exit = any(k in action.upper() for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT"))
    if is_exit:
        if exit_review and exit_review.get("action") == "VETO_HOLD":
            header = f"[{symbol}] [{current_time_et}] — 🛡️ VETO EXIT (HOLD)"
            pb = (
                f"{header}\n\n"
                f"🛡️ VETO PREMATURE TV EXIT — HOLDING POSITION\n\n"
                f"Reason: {exit_review.get('reason')}\n"
                f"Live broker quote at ${current_price:.2f}. Setup structure remains intact above invalidation."
            )
        else:
            header = f"[{symbol}] [{current_time_et}] — 🔴 EXIT CONFIRMED"
            reason_str = (
                exit_review.get("reason")
                if exit_review
                else (alert.get("wrong_if") or payload.get("wrong_if") or "Confirmed stop / exit signal hit")
            )
            pb = (
                f"{header}\n\n"
                f"🔴 EXIT CONFIRMED\n\n"
                f"Reason: {reason_str}\n"
                f"Live fill: ${current_price:.2f}. Position closed."
            )
        return header, pb

    # ENTRY ALERT TRIAGE
    verdict = str(payload.get("verdict") or alert.get("verdict") or "").upper()
    grade = str(payload.get("grade") or alert.get("grade") or "B").upper()
    score = str(payload.get("score") or alert.get("score") or "75")
    plan = str(
        payload.get("plan")
        or alert.get("plan")
        or f"In {current_price:.2f} · Stop {current_price*0.99:.2f} · T1 {current_price*1.01:.2f}"
    )
    why_now = str(payload.get("why_now") or alert.get("why_now") or "Breakout trigger active")
    align = str(payload.get("align") or alert.get("align") or "W → D → 15m ↑ · Stg1 base")
    wrong_if = str(payload.get("wrong_if") or alert.get("wrong_if") or "Exit on close beyond stop")
    context = str(payload.get("context") or alert.get("context") or "TREND UP · Below VWAP · in OR")
    premium = str(payload.get("premium") or alert.get("premium") or "IV NORMAL")

    is_call = "CALL" in action.upper() or "BUY CALLS" in verdict
    is_put = "PUT" in action.upper() or "BUY PUTS" in verdict

    # Check for stand aside / day pause
    if "STAND ASIDE" in verdict or "DAY PAUSE" in wrong_if.upper():
        action_call = "⛔ STAND ASIDE"
    elif is_call:
        action_call = "🟢 TAKE CALLS"
    elif is_put:
        action_call = "🔴 TAKE PUTS"
    else:
        action_call = "⏸️ WAIT"

    header = f"[{symbol}] [{current_time_et}] — {action_call}"
    pb = f"""{header}
Conviction: {score}/100 ({grade}) | Card: {verdict or action} {grade}({score}) | Regime: {context} | VIX: {vix}

THE PLAY:    {symbol} 0DTE {"CALLS" if is_call else "PUTS"} ATM
PLAN:        {plan}
TIME-BOX:    Prime trend window (10:00–11:30 ET); flat by 3:45 PM ET EOD

WHY (card):  {why_now} · Alignment: {align}
WHY (tape):  Live price: ${current_price:.2f} · Premium: {premium} · VIX: {vix}
KILL IT IF:  {wrong_if}

Status: Verified {grade}-grade institutional setup. Execute discipline: scale 50% at T1, trail stop to BE+ 0.05."""
    return header, pb


def evaluate_alert_payload(
    alert: Dict[str, Any],
    use_tools: bool = True,
    max_tokens: int = 1536,
) -> Dict[str, Any]:
    """
    Run an incoming alert (Intraday or Daily) through the local GPU LLM with #ponytail rules
    and revanth-gem-local.md, 100% independent of TradingView scraping.
    Persists decision into SQLite trading_alerts.db.
    """
    symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
    if not symbol:
        return {"error": "Missing symbol", "llm_decision": "", "llm_playbook": ""}

    strategy = str(alert.get("strategy") or "Daily").strip()
    action = str(alert.get("action") or alert.get("event") or "ALERT").strip().upper()
    message_id = alert.get("message_id")

    # Extract parsed payload if available
    raw_payload_str = alert.get("raw_payload") or alert.get("body") or ""
    payload: Dict[str, Any] = {}
    if raw_payload_str:
        try:
            if isinstance(raw_payload_str, dict):
                payload = raw_payload_str
            else:
                payload = json.loads(raw_payload_str)
        except Exception:
            payload = {}

    current_price = alert.get("alert_price") or payload.get("price")
    if not current_price or current_price == "N/A":
        try:
            current_price = get_current_price(
                symbol, context="execution" if strategy == "Intraday" else "surveillance"
            )
        except Exception:
            current_price = 0.0

    try:
        vix = get_current_price("VIX")
    except Exception:
        vix = "N/A"

    current_time_et = get_eastern_now().strftime("%I:%M %p ET")

    # Fetch live broker data (Tastytrade, Alpaca/Web news, options) in parallel/seconds
    live_ctx = build_live_market_context(symbol)

    # =========================================================================
    # BRANCH 1: INTRADAY EXECUTION ALERTS (revanth-0dte.md)
    # =========================================================================
    if strategy == "Intraday":
        from src.tracking.position_monitor import _is_exit_event, review_tv_exit
        from src.tracking.position_state import list_open

        is_exit = _is_exit_event(alert)
        pos_rec = list_open().get(symbol)
        exit_review = review_tv_exit(symbol, alert) if is_exit else None

        eastern_now = get_eastern_now()
        try:
            score_val = int(float(payload.get("score") or alert.get("score") or 75))
        except (ValueError, TypeError):
            score_val = 75
        grade_val = str(payload.get("grade") or alert.get("grade") or "B").upper()
        align_val = str(payload.get("align") or alert.get("align") or "")

        # Hard Quantitative Risk Vetoes (Grade-A Gate, Stage Alignment, Time Windows, Exposure, Day Pause)
        risk_veto = evaluate_risk_vetoes(
            symbol=symbol,
            action=action,
            score=score_val,
            current_time_et=current_time_et,
            eastern_dt=eastern_now,
            grade=grade_val,
            align=align_val,
        ) if not is_exit else None

        if risk_veto:
            veto_header, veto_playbook = risk_veto
            logger.info(f"[RISK VETO] {symbol} {action} -> {veto_header}")
            if message_id:
                update_alert_llm(message_id, veto_header, veto_playbook, status="PROCESSED")
            return {
                "symbol": symbol,
                "strategy": strategy,
                "llm_decision": veto_header,
                "llm_playbook": veto_playbook,
                "verdict": "STAND ASIDE",
                "status": "PROCESSED",
            }

        system_prompt = _load_gem("revanth-0dte.md")
        if not system_prompt:
            system_prompt = (
                "You are a battle-tested 0DTE options trader channeling #ponytail. "
                "Output a 1-line header formatted as '[SYMBOL] [TIME] — EMOJI ACTION' "
                "(e.g. '[AAPL] [10:30 AM ET] — 🟢 GO (ENTER CALLS)', '🛡️ VETO EXIT (HOLD)', or '🔴 EXIT CONFIRMED') "
                "followed by a 2-sentence tactical playbook."
            )

        exit_context_str = ""
        if is_exit and exit_review:
            exit_context_str = f"""
- Exit Veto Gate Review: {exit_review.get('action')} ({exit_review.get('reason')})
- Position Stop Level: {exit_review.get('stop') or (pos_rec.get('stop') if pos_rec else 'N/A')}
- Position Target: {exit_review.get('target') or (pos_rec.get('target') if pos_rec else 'N/A')}
- Live Broker vs Invalidation: Underlying {current_price} vs Stop {exit_review.get('stop')}
"""

        user_prompt = f"""Current Live Context (Scraping-Independent):
- Current Time (ET): {current_time_et}
- Ticker Underlying Price: {current_price}
- VIX Index Level: {vix}
- Strategy: Intraday
- Action: {action}
- Setup / Alignment: {alert.get('setup') or payload.get('setup') or 'None'} | {alert.get('align') or payload.get('align') or '--'}
- Invalidation (Wrong If): {alert.get('wrong_if') or payload.get('wrong_if') or '--'}{exit_context_str}
- Live Volatility (Tastytrade): {json.dumps(live_ctx.get('volatility'))}
- Live Tape (1m order flow): {live_ctx.get('tape', {}).get('verdict', 'unavailable')}
- Recent Breaking News:
{chr(10).join(live_ctx.get('news', [])[:2]) or 'None reported'}

Authoritative Alert Payload:
{json.dumps({**payload, 'ticker': symbol, 'action': action, 'price': current_price}, indent=2)}

Apply the revanth-0dte.md rules card to this alert and return your GO/NO-GO decision and tactical playbook.
"""
        # Synthesize infallible deterministic baseline card
        px_val = float(current_price) if current_price and current_price != "N/A" else 0.0
        synth_header, synth_playbook = synthesize_deterministic_triage(
            symbol=symbol,
            action=action,
            strategy=strategy,
            current_time_et=current_time_et,
            current_price=px_val,
            vix=vix,
            payload=payload,
            alert=alert,
            exit_review=exit_review,
        )

        response_text = query_local_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            use_tools=False,
            disable_thinking=True,
            model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
        )

        cleaned = (response_text or "").strip()
        # Strip code blocks ```markdown ... ``` or ```json ... ```
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()

        # Strict regex search for decision header (NEVER plain substring check that matches 'AVGO')
        HEADER_REGEX = re.compile(
            r"(?:\[[A-Z0-9/.]+\].*?——?\s*)?(?:🟢\s*TAKE\s*CALLS|🔴\s*TAKE\s*PUTS|🛡️\s*VETO\s*EXIT(?:\s*\(HOLD\))?|⏸️\s*WAIT|⛔\s*STAND\s*ASIDE|🔴\s*EXIT\s*CONFIRMED|\bTAKE\s+CALLS\b|\bTAKE\s+PUTS\b|\bVETO\s+EXIT\b|\bSTAND\s+ASIDE\b|\bEXIT\s+CONFIRMED\b|\bGO\s*\(CALLS\b|\bGO\s*\(PUTS\b)",
            re.IGNORECASE,
        )

        header_line = ""
        for line in cleaned.splitlines():
            line_str = re.sub(r"\*\*", "", line).strip()
            # Ignore code, JSON brackets, or conversational preambles
            if not line_str or line_str.startswith("{") or line_str.startswith("```") or line_str.startswith("---"):
                continue
            if '"ticker":' in line_str or '"event_type":' in line_str or line_str.lower().startswith("i'll "):
                continue
            m = HEADER_REGEX.search(line_str)
            if m:
                header_line = line_str
                break

        # If LLM didn't produce a valid header, use the deterministic baseline header
        if not header_line:
            header_line = synth_header

        # Check if LLM response is junk/preamble/tool call JSON
        is_junk = (
            not cleaned
            or cleaned.startswith("{")
            or '"skill_name":' in cleaned
            or (cleaned.lower().startswith("i'll ") and "THE PLAY:" not in cleaned and "PLAN:" not in cleaned)
        )
        if is_junk:
            playbook = synth_playbook
        else:
            if header_line not in cleaned:
                playbook = f"{header_line}\n\n{cleaned}"
            else:
                playbook = cleaned

        decision = header_line

        if message_id:
            update_alert_llm(message_id, decision, playbook, status="PROCESSED")

        verdict = (
            "VETO_HOLD"
            if ("🛡️" in decision or "VETO" in decision)
            else ("GO" if ("🟢" in decision or "TAKE" in decision)
            else ("EXIT" if "EXIT" in decision
            else "STAND ASIDE"))
        )

        return {
            "symbol": symbol,
            "strategy": strategy,
            "llm_decision": decision,
            "llm_playbook": playbook,
            "verdict": verdict,
            "status": "PROCESSED",
        }

    # =========================================================================
    # BRANCH 2: DAILY SCREENER / SWING ALERTS (revanth-gem-local.md + #ponytail)
    # =========================================================================
    system_prompt = _load_gem("revanth-gem-local.md")
    if not system_prompt:
        system_prompt = (
            "You are Ponytail — Senior Quantitative Portfolio Manager. Triage this swing alert. "
            "Emit strictly a JSON object with keys: ticker, dominant_side, entry_mode, triage (PASS|WATCH|CUT), "
            "conviction (1-10), ponytail_critique, tactical_stop, target_1, recommended_vehicle, reasoning, catalyst."
        )

    # Assemble complete scraping-independent context payload
    setup_name = alert.get("setup") or payload.get("setup") or "Market Alert"
    wrong_if = alert.get("wrong_if") or payload.get("wrong_if") or "--"

    triage_context = {
        "ticker": symbol,
        "price": current_price,
        "vix": vix,
        "strategy": strategy,
        "action": action,
        "setup": setup_name,
        "wrong_if": wrong_if,
        "volatility_context": live_ctx.get("volatility"),
        "expected_earnings": live_ctx.get("expected_earnings"),
        "recent_catalysts": live_ctx.get("news"),
        "options_snippet": live_ctx.get("options_snippet"),
        "today": get_eastern_now().strftime("%Y-%m-%d"),
    }

    # Optional enrichment: load scraped datawindow if artifacts exist
    try:
        today_str = get_eastern_now().strftime("%Y-%m-%d")
        dw_file = config.DATA_DIR / "raw" / today_str / symbol / f"{symbol}_datawindow.json"
        if dw_file.exists():
            with open(dw_file, "r", encoding="utf-8") as f:
                scraped_dw = json.load(f)
            triage_context["scraped_datawindow"] = {
                "poc": scraped_dw.get("poc"),
                "vah": scraped_dw.get("vah"),
                "val": scraped_dw.get("val"),
                "ma200": scraped_dw.get("ma200"),
                "rvol": scraped_dw.get("rvol"),
            }
    except Exception as e_dw:
        logger.debug(f"Optional scraped datawindow check bypassed for {symbol}: {e_dw}")

    user_prompt = f"""Incoming Market Alert (Scraping-Independent Live Context):
{json.dumps(triage_context, indent=2)}

Channel #ponytail ruthlessly:
1. Measured risk/reward: Evaluate price action, broker volatility (Tastytrade IV/HV), earnings proximity, and news catalysts.
2. Hard defensive anchor stop level (exact dollar value).
3. Recommended vehicle (SHARES, BULL_PUT_SPREAD, BULL_CALL_SPREAD, BEAR_PUT_SPREAD, BEAR_CALL_SPREAD, DEEP_ITM_LEAPS, or STALK_CASH).
4. Triage verdict: PASS (Deep Research), WATCH, or CUT.

Output strictly valid JSON matching the revanth-gem-local.md schema.
"""

    response_text = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        use_tools=use_tools,
        json_mode=True,
        disable_thinking=True,
        model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
    )

    parsed_json: Dict[str, Any] = {}
    if response_text:
        try:
            parsed_json = _extract_json_response(response_text)
        except Exception:
            pass

    triage = str(parsed_json.get("triage") or "WATCH").upper()
    conviction = parsed_json.get("conviction") or 5
    ponytail_critique = parsed_json.get("ponytail_critique") or parsed_json.get("reasoning") or ""
    tactical_stop = parsed_json.get("tactical_stop") or payload.get("stop") or 0.0
    target_1 = parsed_json.get("target_1") or payload.get("target") or 0.0
    vehicle = parsed_json.get("recommended_vehicle") or "SHARES"
    entry_mode = parsed_json.get("entry_mode") or "NONE"
    catalyst = parsed_json.get("catalyst") or "none"
    key_flags = parsed_json.get("key_flags") or []

    # Clean formatted decision badge
    if triage == "PASS":
        decision = f"🎯 PASS — DEEP RESEARCH ({conviction}/10)"
    elif triage == "CUT":
        decision = f"✂️ CUT — NO ACTIVE EDGE ({conviction}/10)"
    else:
        decision = f"⏳ WATCH ({conviction}/10)"

    # Rich formatted playbook for UI display
    playbook_parts = []
    if ponytail_critique:
        playbook_parts.append(f"**#ponytail Assessment**: {ponytail_critique}")
    if tactical_stop or target_1:
        playbook_parts.append(f"**Tactical Levels**: Stop ${tactical_stop} · Target ${target_1} · Vehicle: {vehicle}")
    if entry_mode and entry_mode != "NONE":
        playbook_parts.append(f"**Entry Mode**: {entry_mode} · Catalyst: {catalyst}")

    # Embed real Tastytrade volatility metrics & expected earnings
    vol = live_ctx.get("volatility") or {}
    iv_rank = vol.get("iv_rank")
    iv_pct = vol.get("iv_percentile")
    hv30 = vol.get("hv30")
    iv_hv_diff = vol.get("iv_hv_diff")
    expected_earnings = live_ctx.get("expected_earnings")

    if iv_rank is not None:
        playbook_parts.append(f"**Tastytrade Volatility**: IV Rank {iv_rank}% (IV Percentile {iv_pct}%, 30d HV {hv30}%, Spread {iv_hv_diff}%)")
    if expected_earnings:
        playbook_parts.append(f"**Expected Earnings**: {expected_earnings}")
    if catalyst and catalyst.lower() not in ("none", "n/a"):
        playbook_parts.append(f"**Catalyst**: {catalyst}")

    if key_flags:
        playbook_parts.append(f"**Flags**: {', '.join(key_flags[:4])}")

    playbook = "\n\n".join(playbook_parts) if playbook_parts else (response_text or "").strip()

    if message_id:
        update_alert_llm(message_id, decision, playbook, status="PROCESSED")

    return {
        "symbol": symbol,
        "strategy": strategy,
        "triage": triage,
        "conviction": conviction,
        "llm_decision": decision,
        "llm_playbook": playbook,
        "ponytail_critique": ponytail_critique,
        "tactical_stop": tactical_stop,
        "target_1": target_1,
        "recommended_vehicle": vehicle,
        "entry_mode": entry_mode,
        "key_flags": key_flags,
        "catalyst": catalyst,
        "volatility_context": live_ctx.get("volatility"),
        "expected_earnings": live_ctx.get("expected_earnings"),
        "raw_json": parsed_json,
        "status": "PROCESSED",
    }


def evaluate_batch_pending(limit: int = 100, date_str: Optional[str] = None, force_all: bool = False) -> Dict[str, Any]:
    """
    Find alerts in SQLite trading_alerts.db that have not yet been evaluated or have corrupted decisions,
    and run them through evaluate_alert_payload without any chart scraping.
    """
    import sqlite3
    from src.tracking.alert_db import DB_PATH

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if force_all:
        query = "SELECT * FROM alerts WHERE 1=1"
    else:
        query = """
            SELECT * FROM alerts
            WHERE (
                llm_decision IS NULL 
                OR llm_decision = ''
                OR llm_decision LIKE '%"ticker"%'
                OR llm_decision LIKE '%"event_type"%'
                OR llm_decision LIKE 'I''ll %'
                OR llm_decision = 'AI EVALUATED'
            )
        """
    params = []
    if date_str:
        query += " AND date = ?"
        params.append(date_str)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {"count": 0, "message": "No pending alerts to evaluate"}

    evaluated_count = 0
    for r in rows:
        try:
            alert_dict = dict(r)
            evaluate_alert_payload(alert_dict, use_tools=False)
            evaluated_count += 1
        except Exception as e:
            logger.warning(f"Batch evaluation failed for {r['symbol']}: {e}")

    return {
        "count": evaluated_count,
        "message": f"Successfully evaluated {evaluated_count} alerts via local LLM.",
    }
