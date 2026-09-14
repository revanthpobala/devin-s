from run_ui import config, _get_db, _build_events_past_chat_context, _extract_targeted_schwab_strikes
from src.clients.options_client import get_realtime_quote, fetch_options_chain_tool
from src.clients.price_client import get_current_price
from zoneinfo import ZoneInfo
from datetime import datetime
import json, re, time

def test_new_single_ticker_context(ticker_u: str, date_str: str, question: str):
    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    dates_set = set()
    if raw_root.exists():
        for d in raw_root.glob("202*"):
            t_dir = d / ticker_u
            if t_dir.is_dir() and any(t_dir.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)
    if rep_root.exists():
        for d in rep_root.glob("202*"):
            if any(d.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)

    sorted_hist_dates = sorted(list(dates_set), reverse=True)
    latest_dt = sorted_hist_dates[0] if sorted_hist_dates else (date_str if date_str else None)

    report_spot = None
    report_date = latest_dt
    shares_plan = {}
    options_plan = {}
    invalidation_rule = {}
    verdict = "STALK"
    conviction = 5

    # Check watch_levels.json & arbitration.md
    if latest_dt:
        t_raw_dir = raw_root / latest_dt / ticker_u
        t_rep_dir = rep_root / latest_dt
        levels_file = t_raw_dir / f"{ticker_u}_watch_levels.json"
        if not levels_file.exists():
            for od in sorted_hist_dates:
                cand_lvl = raw_root / od / ticker_u / f"{ticker_u}_watch_levels.json"
                if cand_lvl.exists():
                    levels_file = cand_lvl
                    report_date = od
                    break
        if levels_file.exists():
            try:
                wl_data = json.loads(levels_file.read_text(encoding="utf-8"))
                shares_plan = wl_data.get("shares_plan") or {}
                options_plan = wl_data.get("options_plan") or {}
                invalidation_rule = wl_data.get("invalidation") or {}
                verdict = wl_data.get("verdict") or verdict
                conviction = wl_data.get("conviction") or conviction
                if wl_data.get("last_price"):
                    report_spot = float(wl_data["last_price"])
            except Exception:
                pass

        arb_cand = t_rep_dir / f"{ticker_u}_arbitration.md"
        if not arb_cand.exists():
            for od in sorted_hist_dates:
                cand_arb = rep_root / od / f"{ticker_u}_arbitration.md"
                if cand_arb.exists():
                    arb_cand = cand_arb
                    if not report_date:
                        report_date = od
                    break
        if arb_cand.exists() and report_spot is None:
            try:
                arb_text = arb_cand.read_text(encoding="utf-8")
                m_sp = re.search(r'(?:spot price|current spot|spot)\s*(?::|\(|is|\$)\s*\$?([0-9]+\.[0-9]+)', arb_text, re.IGNORECASE)
                if m_sp:
                    report_spot = float(m_sp.group(1))
            except Exception:
                pass

    # Fetch Real-time quote & live spot
    quote_str = ""
    try:
        quote_str = get_realtime_quote(ticker_u)
    except Exception as qe:
        pass

    live_spot = None
    bid_str, ask_str = "N/A", "N/A"
    if quote_str:
        m_lp = re.search(r'Last:\s*([0-9]+\.[0-9]+)', quote_str)
        if m_lp:
            try: live_spot = float(m_lp.group(1))
            except: pass
        m_ba = re.search(r'Bid/Ask:\s*([0-9]+\.[0-9]+)\s*/\s*([0-9]+\.[0-9]+)', quote_str)
        if m_ba:
            bid_str, ask_str = f"${m_ba.group(1)}", f"${m_ba.group(2)}"

    if live_spot is None:
        try: live_spot = get_current_price(ticker_u)
        except: pass

    ez_low = shares_plan.get("entry_zone_low")
    ez_high = shares_plan.get("entry_zone_high")
    stop_p = shares_plan.get("tactical_stop") or invalidation_rule.get("price_level")
    t1_p = shares_plan.get("target_1")
    t2_p = shares_plan.get("target_2")
    breakout_p = shares_plan.get("breakout_level")

    calendar_today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")

    status_badge = "📊 ACTIVE LIVE TAPE"
    status_desc = f"Live spot: ${live_spot:.2f}" if live_spot else "Awaiting live quote"
    copilot_instruction = ""

    if live_spot:
        if stop_p and live_spot <= stop_p:
            status_badge = "🛑 STOP BREACHED / THESIS INVALIDATED"
            status_desc = f"Live spot (${live_spot:.2f}) has breached the tactical stop / invalidation floor (${stop_p:.2f})."
            copilot_instruction = f"The trade setup is INVALIDATED because live spot (${live_spot:.2f}) is at or below the stop (${stop_p:.2f}). Do not enter long."
        elif t1_p and live_spot >= t1_p:
            status_badge = "🏁 TARGET 1 REACHED"
            status_desc = f"Live spot (${live_spot:.2f}) reached Target 1 (${t1_p:.2f}). Consider taking partial profits."
            copilot_instruction = f"Live spot (${live_spot:.2f}) is in the profit target zone (T1 ${t1_p:.2f}). Advise locking in gains or trailing stop."
        elif ez_low and ez_high and ez_low <= live_spot <= ez_high:
            status_badge = "🎯 IN MANDATED ENTRY ZONE RIGHT NOW"
            status_desc = f"Live spot (${live_spot:.2f}) is INSIDE the suggested entry limit zone [${ez_low:.2f} – ${ez_high:.2f}]. Orders are filling live."
            copilot_instruction = f"The stock is ACTIVELY IN THE ENTRY ZONE (${ez_low:.2f}–${ez_high:.2f}). The pullback to ${ez_high:.2f} has already occurred. Setup is buyable/executable here with stop at ${stop_p or 'tactical stop'}."
        elif ez_low and stop_p and stop_p < live_spot < ez_low:
            dist_to_stop = ((live_spot - stop_p) / stop_p) * 100
            status_badge = "⚡ TESTING POC STRUCTURAL FLOOR (PULLBACK COMPLETE)"
            status_desc = f"Live spot (${live_spot:.2f}) pulled back through the limit ceiling (${ez_high:.2f}) down to the structural floor (+{dist_to_stop:.1f}% above ${stop_p:.2f} stop)."
            copilot_instruction = f"The stock HAS ALREADY PULLED BACK from ${report_spot or 'prior highs'} to ${live_spot:.2f}. It is testing the POC structural floor above the ${stop_p:.2f} stop. DO NOT tell the user to wait for a pullback to ${ez_high:.2f}—the pullback has already completed! Evaluate whether support is defending above ${stop_p:.2f}."
        elif ez_high and live_spot > ez_high:
            dist_above = ((live_spot - ez_high) / ez_high) * 100
            status_badge = f"⏳ STALKING (+{dist_above:.1f}% ABOVE ENTRY ZONE)"
            status_desc = f"Live spot (${live_spot:.2f}) is trading above the entry zone ceiling (${ez_high:.2f}). Awaiting pullback or breakout above ${breakout_p or 'resistance'}."
            copilot_instruction = f"Price (${live_spot:.2f}) is currently {dist_above:.1f}% above the top of the entry zone (${ez_high:.2f}). Await pullback to ${ez_high:.2f} or breakout above ${breakout_p or 'resistance'}."

    elapsed_days_str = ""
    if report_date:
        try:
            d_rep = datetime.strptime(report_date, "%Y-%m-%d")
            d_tod = datetime.strptime(calendar_today, "%Y-%m-%d")
            el = (d_tod - d_rep).days
            elapsed_days_str = f"({el} calendar day{'s' if el != 1 else ''} ago)" if el > 0 else "(today)"
        except Exception:
            pass

    delta_str = ""
    if live_spot and report_spot:
        d_val = live_spot - report_spot
        d_pct = (d_val / report_spot) * 100
        sign = "+" if d_val >= 0 else ""
        delta_str = f"• **NET CHANGE SINCE REPORT:** **{sign}${d_val:.2f} ({sign}{d_pct:.2f}%)**"

    top_card = [
        f"### 🚨 AUTHORITATIVE LIVE REAL-TIME MARKET QUOTE & EXECUTION STATUS ({ticker_u}):",
        f"• **CURRENT LIVE SPOT PRICE (NOW):** **${live_spot:.2f}**" if live_spot else f"• **CURRENT LIVE SPOT PRICE (NOW):** Quote Ingesting",
        f"• **REAL-TIME BID / ASK:** {bid_str} / {ask_str}" if (bid_str != "N/A" and ask_str != "N/A") else "",
        f"• **HISTORICAL REPORT BASELINE:** ${report_spot:.2f} (Compiled on {report_date or 'prior session'} {elapsed_days_str})" if report_spot else "",
    ]
    if delta_str:
        top_card.append(delta_str)
    if ez_low and ez_high:
        top_card.append(f"• **MANDATED ENTRY ZONE:** **${ez_low:.2f} – ${ez_high:.2f}** | **TACTICAL STOP:** **${stop_p:.2f}**" + (f" | **TARGET 1:** **${t1_p:.2f}**" if t1_p else ""))
    top_card.append(f"• **REAL-TIME EXECUTION STATUS:** **{status_badge}**")
    top_card.append(f"• **EXECUTION DETAIL:** {status_desc}")

    if quote_str:
        top_card.append(f"\n```\n{quote_str}\n```")

    top_card.append(f"""
> ⚠️ **MANDATORY INSTRUCTION FOR COPILOT / REV CHAT**:
> 1. The **AUTHORITATIVE LIVE SPOT PRICE** right now is **${live_spot:.2f}** (NOT {f'${report_spot:.2f}' if report_spot else 'historical prices'}).
> 2. Any prices cited in historical dossiers below were recorded on {report_date or 'earlier dates'}. NEVER repeat historical prices as today's live spot!
> 3. {copilot_instruction}
> 4. All trade recommendations, options strikes, delta/gamma risk, and distance to stops MUST be computed from the LIVE SPOT PRICE of **${live_spot:.2f}**.
""")

    print("\n".join(l for l in top_card if l))

test_new_single_ticker_context("TE", "2026-09-09", "are you not injecting the latest price?")
