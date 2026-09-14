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

    return context


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
            current_price = get_current_price(symbol)
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
- Recent Breaking News:
{chr(10).join(live_ctx.get('news', [])[:2]) or 'None reported'}

Authoritative Alert Payload:
{json.dumps({**payload, 'ticker': symbol, 'action': action, 'price': current_price}, indent=2)}

Apply the revanth-0dte.md rules card to this alert and return your GO/NO-GO decision and tactical playbook.
"""
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

        lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
        header_line = ""
        for l in lines:
            if l.startswith("```") or l.startswith("---"):
                continue
            l_clean = re.sub(r"\*\*", "", l).strip()
            if any(x in l_clean for x in ["🟢", "🔴", "🛡️", "🛡", "⏸️", "⏸", "⛔", "TAKE", "WAIT", "STAND", "GO", "EXIT", "VETO", "HOLD", "PASS"]):
                header_line = l_clean
                break
        if not header_line and lines:
            for l in lines:
                if not l.startswith("```") and not l.startswith("---"):
                    header_line = re.sub(r"\*\*", "", l).strip()
                    break

        if not header_line:
            if is_exit and exit_review:
                if exit_review.get("action") == "VETO_HOLD":
                    header_line = f"[{symbol}] [{current_time_et}] — 🛡️ VETO EXIT (HOLD)"
                else:
                    header_line = f"[{symbol}] [{current_time_et}] — 🔴 EXIT CONFIRMED"
            elif alert.get("llm_decision"):
                header_line = alert.get("llm_decision")

        decision = header_line or "AI EVALUATED"
        playbook = cleaned or (exit_review.get("reason") if exit_review else "")

        if message_id:
            update_alert_llm(message_id, decision, playbook, status="PROCESSED")

        verdict = (
            "VETO_HOLD"
            if ("🛡️" in decision or "VETO" in decision)
            else ("GO" if ("🟢" in decision or "GO" in decision)
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


def evaluate_batch_pending(limit: int = 50, date_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Find alerts in SQLite trading_alerts.db that have not yet been evaluated,
    and run them through evaluate_alert_payload without any chart scraping.
    """
    import sqlite3
    from src.tracking.alert_db import DB_PATH

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
        SELECT * FROM alerts
        WHERE (llm_decision IS NULL OR llm_decision = '')
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
