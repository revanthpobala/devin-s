"""
src/ui/services/opportunity_service.py

Real-Time Opportunity Synthesis & Live Market Dynamics Engine.
Continuously integrates rolling deep research reports, live market quotes,
Schwab 1000 screener coils, and actionable TradingView alerts into a unified,
dynamic opportunity stream.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.clients.price_client import get_current_prices_batch
from src.logic.report_level_extractor import extract_watch_levels_from_report

logger = logging.getLogger(__name__)

_OPPS_CACHE: Optional[List[Dict[str, Any]]] = None
_OPPS_CACHE_TIME: float = 0.0
_OPPS_CACHE_TTL: float = 20.0

_ALERTS_CACHE: Optional[List[Dict[str, Any]]] = None
_ALERTS_CACHE_TIME: float = 0.0
_ALERTS_CACHE_TTL: float = 10.0



def get_live_market_pulse() -> Dict[str, Any]:
    """Fetch live market phase, clocks, and quotes for SPY, QQQ, VIX."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    is_weekday = now_et.weekday() < 5

    rth_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    rth_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    pre_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    post_close = now_et.replace(hour=20, minute=0, second=0, microsecond=0)

    if not is_weekday:
        market_phase = "WEEKEND_CLOSED"
        market_open = False
        market_status_text = "🔴 MARKET CLOSED (Weekend)"
    elif rth_open <= now_et <= rth_close:
        market_phase = "REGULAR_OPEN"
        market_open = True
        market_status_text = f"🟢 REGULAR MARKET OPEN ({now_mt.strftime('%I:%M %p').lstrip('0')} MT)"
    elif rth_close < now_et <= post_close:
        market_phase = "AFTER_HOURS"
        market_open = False
        market_status_text = f"🟡 AFTER-HOURS ({now_mt.strftime('%I:%M %p').lstrip('0')} MT)"
    elif pre_open <= now_et < rth_open:
        market_phase = "PRE_MARKET"
        market_open = False
        market_status_text = f"🟠 PRE-MARKET ({now_mt.strftime('%I:%M %p').lstrip('0')} MT)"
    else:
        market_phase = "OVERNIGHT_CLOSED"
        market_open = False
        market_status_text = f"🔴 MARKET CLOSED ({now_mt.strftime('%I:%M %p').lstrip('0')} MT)"

    pulse_tickers = ["SPY", "QQQ", "^VIX"]
    quotes: Dict[str, float] = {}
    try:
        quotes = get_current_prices_batch(pulse_tickers, context="pulse")
    except Exception as e:
        logger.debug(f"Failed fetching market pulse quotes: {e}")

    spy_px = quotes.get("SPY") or quotes.get("spy")
    qqq_px = quotes.get("QQQ") or quotes.get("qqq")
    vix_px = quotes.get("VIX") or quotes.get("^VIX") or quotes.get("^vix")

    return {
        "market_open": market_open,
        "market_phase": market_phase,
        "market_status_text": market_status_text,
        "time_et": now_et.strftime("%I:%M:%S %p ET"),
        "time_mt": now_mt.strftime("%I:%M:%S %p MT"),
        "spy": round(spy_px, 2) if spy_px else None,
        "qqq": round(qqq_px, 2) if qqq_px else None,
        "vix": round(vix_px, 2) if vix_px else None,
        "timestamp": now_et.isoformat(),
    }


def _determine_vintage(report_file: Path, report_date_str: str, now_mt: datetime) -> str:
    """Human-readable vintage badge for research freshness."""
    try:
        mtime = datetime.fromtimestamp(report_file.stat().st_mtime, tz=timezone.utc)
        mtime_mt = mtime.astimezone(ZoneInfo("America/Denver"))
        today_mt_str = now_mt.strftime("%Y-%m-%d")
        yesterday_mt_str = (now_mt - timedelta(days=1)).strftime("%Y-%m-%d")

        if report_date_str == today_mt_str or mtime_mt.strftime("%Y-%m-%d") == today_mt_str:
            return f"⚡ TODAY ({mtime_mt.strftime('%I:%M %p').lstrip('0')} MT)"
        elif report_date_str == yesterday_mt_str or mtime_mt.strftime("%Y-%m-%d") == yesterday_mt_str:
            return f"📅 Yesterday ({mtime_mt.strftime('%b %d')})"
        else:
            days_ago = (now_mt.date() - mtime_mt.date()).days
            return f"📅 {mtime_mt.strftime('%b %d')} ({days_ago}d ago)"
    except Exception:
        return f"📅 {report_date_str}"


def _extract_pm_takeaways(arbitration_path: Path, max_bullets: int = 2) -> List[str]:
    """Extract PM Arbitration key directives & catalyst takeaway bullets."""
    if not arbitration_path.exists():
        return []
    try:
        text = arbitration_path.read_text(encoding="utf-8")
        bullets = []

        case_for_match = re.search(r"##\s*🟢\s*THE CASE FOR[\s\S]*?(?=##|\Z)", text, re.IGNORECASE)
        if case_for_match:
            case_text = case_for_match.group(0)
            items = re.findall(r"^\d+\.\s*\*\*([^*]+)\*\*:?\s*([^\n]+)", case_text, re.MULTILINE)
            for title, desc in items:
                bullets.append(f"<b>{title.strip()}</b>: {desc.strip()}")
                if len(bullets) >= max_bullets:
                    return bullets

        ruling_match = re.search(r"##\s*⚖️\s*THE JUDGE'S FINAL RULING[\s\S]*?(?=##|\Z)", text, re.IGNORECASE)
        if ruling_match and len(bullets) < max_bullets:
            ruling_text = ruling_match.group(0)
            items = re.findall(r"^\*\s*\*\*([^*]+)\*\*:?\s*([^\n]+)", ruling_text, re.MULTILINE)
            for title, desc in items:
                bullets.append(f"<b>{title.strip()}</b>: {desc.strip()}")
                if len(bullets) >= max_bullets:
                    return bullets

        if not bullets:
            clean = re.sub(r"```[\s\S]*?```", "", text)
            for line in clean.splitlines():
                line = line.strip()
                if line.startswith(("#", ">", "---", "* **Final Verdict")):
                    continue
                if len(line) > 30:
                    bullets.append(line[:130] + ("..." if len(line) > 130 else ""))
                    if len(bullets) >= max_bullets:
                        break
        return bullets
    except Exception as e:
        logger.debug(f"Failed extracting PM bullets from {arbitration_path}: {e}")
        return []


def collect_active_deep_research_opportunities(lookback_days: int = 3) -> List[Dict[str, Any]]:
    """
    Scan reports/ across lookback_days (default 3: today + recent session), parse deep research watch_levels,
    enrich with live quotes, compute proximity and R:R, and return structured setups.
    """
    global _OPPS_CACHE, _OPPS_CACHE_TIME
    now_ts = time.time()
    if _OPPS_CACHE is not None and (now_ts - _OPPS_CACHE_TIME) < _OPPS_CACHE_TTL:
        return _OPPS_CACHE

    reports_base = config.BASE_DIR / "reports"
    if not reports_base.exists():
        return []

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    date_dirs: List[Tuple[datetime, Path]] = []
    for d in reports_base.iterdir():
        if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name):
            try:
                dt = datetime.strptime(d.name, "%Y-%m-%d")
                if dt.date() > now_mt.date() or d.name in {"2029-01-01", "2099-12-31"}:
                    continue
                date_dirs.append((dt, d))
            except Exception:
                continue

    date_dirs.sort(key=lambda x: x[0], reverse=True)
    seen_tickers = set()
    raw_setups: List[Dict[str, Any]] = []

    for dt, d_path in date_dirs[:lookback_days]:
        date_str = d_path.name
        for arb_file in d_path.glob("*_arbitration.md"):
            ticker = arb_file.name.replace("_arbitration.md", "").upper()
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)

            sum_file = d_path / f"{ticker}_summary.md"
            raw_watch = config.BASE_DIR / "data" / "raw" / date_str / ticker / f"{ticker}_watch_levels.json"
            rep_watch = d_path / f"{ticker}_watch_levels.json"
            watch_json_p = raw_watch if raw_watch.exists() else (rep_watch if rep_watch.exists() else None)

            watch_data = None
            if watch_json_p and watch_json_p.exists():
                try:
                    watch_data = json.loads(watch_json_p.read_text(encoding="utf-8"))
                except Exception:
                    pass
            if not watch_data:
                watch_data = extract_watch_levels_from_report(ticker, date_str)

            if not watch_data:
                continue

            shares_p = watch_data.get("shares_plan") or {}
            options_p = watch_data.get("options_plan") or {}
            side = str(watch_data.get("side") or shares_p.get("side") or "LONG").upper()
            entry_low = float(shares_p.get("entry_zone_low") or 0.0)
            entry_high = float(shares_p.get("entry_zone_high") or 0.0)
            stop = float(shares_p.get("tactical_stop") or 0.0)
            target_1 = float(shares_p.get("target_1") or 0.0)
            target_2 = float(shares_p.get("target_2") or 0.0)
            breakout_lvl = float(shares_p.get("breakout_level") or 0.0)

            # Determine execution vehicle
            opt_actionable = bool(options_p.get("actionable", False))
            opt_structure = options_p.get("structure") or "NONE"
            if opt_actionable and opt_structure != "NONE":
                v_type = "OPTIONS"
                v_label = f"{opt_structure} ({options_p.get('expiration', '')} Strike: ${options_p.get('long_strike', 0)}/${options_p.get('short_strike', 0)})"
            else:
                v_type = "SHARES"
                v_label = f"EQUITY SHARES · Entry: ${entry_low:.2f}–${entry_high:.2f} · Stop: ${stop:.2f} · T1: ${target_1:.2f}"

            pm_bullets = _extract_pm_takeaways(arb_file, max_bullets=2)
            invalidation = watch_data.get("invalidation") or {}
            conviction = int(watch_data.get("conviction") or 6)

            raw_setups.append({
                "ticker": ticker,
                "report_date": date_str,
                "vintage": _determine_vintage(arb_file, date_str, now_mt),
                "side": side,
                "verdict": watch_data.get("verdict") or "STALK",
                "conviction": conviction,
                "score": int(watch_data.get("score") or (conviction * 10 + 15)),
                "entry_low": entry_low,
                "entry_high": entry_high,
                "tactical_stop": stop,
                "target_1": target_1,
                "target_2": target_2,
                "breakout_level": breakout_lvl,
                "vehicle_type": v_type,
                "vehicle_label": v_label,
                "options_plan": options_p,
                "pm_bullets": pm_bullets,
                "invalidation": invalidation,
                "summary_path": str(sum_file) if sum_file.exists() else None,
                "arbitration_path": str(arb_file),
                "stage": "DEEP_RESEARCH",
                "needs_deep_research": False,
            })

    # Also incorporate today's qualified Schwab 1000 screener coils that are coiling in zones
    today_mt_str = now_mt.strftime("%Y-%m-%d")
    yesterday_mt_str = (now_mt - timedelta(days=1)).strftime("%Y-%m-%d")
    for check_d in [today_mt_str, yesterday_mt_str]:
        raw_day_dir = config.BASE_DIR / "data" / "raw" / check_d
        screener_files = [raw_day_dir / "schwab_survivors.json", raw_day_dir / "survivors.json"]
        for sf in screener_files:
            if not sf.exists():
                continue
            try:
                candidates_data = json.loads(sf.read_text(encoding="utf-8"))
                if not isinstance(candidates_data, list):
                    continue
                for c in candidates_data:
                    sym = (c.get("Ticker") or c.get("Symbol") or c.get("symbol") or "").upper()
                    if not sym or sym in seen_tickers:
                        continue
                    seen_tickers.add(sym)

                    px = float(c.get("price") or 0.0)
                    sup = float(c.get("support_level") or 0.0)
                    stop_lvl = float(c.get("stop_level") or 0.0)
                    t1_lvl = float(c.get("target_level") or 0.0)
                    t2_lvl = float(c.get("ceiling_level") or 0.0)
                    stg = c.get("weinstein_stage")
                    setup_name = c.get("screener_setup") or "Trend Continuation (20 EMA Pullback)"
                    tt = c.get("tastytrade") or {}
                    ivr = tt.get("iv_rank")
                    ivr_str = f"{int(round(float(ivr)))}%" if ivr is not None else "N/A"
                    diff = tt.get("iv_hv_diff")
                    diff_str = f"{int(round(float(diff)))}%" if diff is not None else "N/A"

                    vintage_str = "⚡ TODAY (Screener)" if check_d == today_mt_str else "📅 Yesterday (Screener)"

                    # Validate coil levels with level_validation: stop below entry, stop width >= 0.7 ATR
                    from src.logic.level_validation import check_geometry
                    atr_val = float(c.get("atr") or c.get("atr14") or c.get("ATR") or 0.0)
                    geo_errs = check_geometry("LONG", "LIMIT", entry_low=sup, entry_high=sup, breakout_level=0.0, stop=stop_lvl, t1=t1_lvl, t2=t2_lvl)
                    if geo_errs:
                        logger.debug(f"[{sym}] Screener coil dropped due to invalid geometry: {geo_errs}")
                        continue
                    if atr_val > 0 and stop_lvl > 0 and sup > 0:
                        stop_w_atr = (sup - stop_lvl) / atr_val
                        if stop_w_atr < 0.7:
                            logger.debug(f"[{sym}] Screener coil dropped due to tight stop width {stop_w_atr:.2f} < 0.7 ATR")
                            continue

                    raw_setups.append({
                        "ticker": sym,
                        "report_date": check_d,
                        "vintage": vintage_str,
                        "side": "LONG",
                        "verdict": "COILED_BASE",
                        "conviction": 0,
                        "score": int(c.get("priority_score") or 75),
                        "entry_low": round(sup, 2) if sup > 0 else None,
                        "entry_high": round(sup, 2) if sup > 0 else None,
                        "tactical_stop": round(stop_lvl, 2) if stop_lvl > 0 else None,
                        "target_1": round(t1_lvl, 2) if t1_lvl > 0 else None,
                        "target_2": round(t2_lvl, 2) if t2_lvl > 0 else None,
                        "breakout_level": round(float(c.get("ceiling_level") or 0.0), 2) if c.get("ceiling_level") else None,
                        "vehicle_type": "SHARES",
                        "vehicle_label": f"EQUITY · Weinstein Stage {stg} · {setup_name} · IVR {ivr_str}",
                        "options_plan": {},
                        "pm_bullets": [
                            f"Screener: {setup_name} (Stage {stg} Advancing) · Support floor anchored at ${sup:.2f}",
                            f"Tastytrade: IV Rank {ivr_str} | IV-HV Spread: {diff_str} · Coiled base pullback",
                        ],
                        "invalidation": {
                            "level": round(stop_lvl, 2),
                            "rationale": f"Candle close below defended support ${stop_lvl:.2f}",
                        },
                        "summary_path": None,
                        "arbitration_path": None,
                        "stage": "SCREENER_COIL",
                        "needs_deep_research": True,
                        "is_actionable": False,
                        "gate_reasons": ["Screener coil: awaiting Data Window measurement"],
                    })
            except Exception as e:
                logger.debug(f"Error reading screener file {sf}: {e}")

    if not raw_setups:
        return []

    # Batch-fetch real-time spot quotes
    tickers = [s["ticker"] for s in raw_setups]
    live_quotes: Dict[str, float] = {}
    try:
        live_quotes = get_current_prices_batch(tickers, context="opportunities")
    except Exception as e:
        logger.warning(f"Batch price error for opportunities: {e}")

    # Evaluate gate for each setup
    from src.logic.actionable_gate import is_actionable as check_is_actionable, gate_inputs_from_datawindow
    from src.data.datawindow_loader import load_datawindow
    processed: List[Dict[str, Any]] = []

    for s in raw_setups:
        sym = s["ticker"]
        spot = live_quotes.get(sym) or 0.0
        s["spot_price"] = spot

        e_low = s["entry_low"]
        e_high = s["entry_high"]
        stop = s["tactical_stop"]
        t1 = s["target_1"]
        side = s["side"]
        d_str = s.get("report_date") or today_mt_str

        # 1. Authoritative Data Window Search across triage, raw, _DEEP_RESEARCH, and force dirs
        dw = load_datawindow(sym, str(d_str))

        # 2. Calculate distance and in-zone status
        in_zone = False
        dist_pct = 0.0
        if spot > 0:
            if side == "LONG":
                if e_low is not None and e_high is not None and e_low > 0 and e_high > 0:
                    if e_low <= spot <= (e_high * 1.004):
                        in_zone = True
                        dist_pct = 0.0
                    elif spot > e_high:
                        dist_pct = round(((spot - e_high) / e_high) * 100.0, 2)
                    else:
                        dist_pct = round(((spot - e_low) / e_low) * 100.0, 2)
                elif e_high is not None and e_high > 0:
                    dist_pct = round(((spot - e_high) / e_high) * 100.0, 2)
                    in_zone = abs(dist_pct) <= 0.4
            else:  # SHORT
                if e_low is not None and e_high is not None and e_low > 0 and e_high > 0:
                    if (e_low * 0.996) <= spot <= e_high:
                        in_zone = True
                        dist_pct = 0.0
                    elif spot < e_low:
                        dist_pct = round(((spot - e_low) / e_low) * 100.0, 2)
                    else:
                        dist_pct = round(((spot - e_high) / e_high) * 100.0, 2)

        # 3. Check terminal conditions
        is_target_hit = False
        is_stop_breached = False
        if spot > 0:
            if side == "LONG":
                if t1 is not None and t1 > 0 and spot >= t1:
                    is_target_hit = True
                elif stop is not None and stop > 0 and spot <= stop:
                    is_stop_breached = True
            else:  # SHORT
                if t1 is not None and t1 > 0 and spot <= t1:
                    is_target_hit = True
                elif stop is not None and stop > 0 and spot >= stop:
                    is_stop_breached = True

        if is_target_hit or is_stop_breached:
            in_zone = False

        s["in_zone"] = in_zone
        s["dist_pct"] = dist_pct

        # 4. Dynamic Live R:R (Guard against stop hugging spot artifact R:R)
        live_rr = 0.0
        if is_target_hit or is_stop_breached:
            live_rr = 0.0
        elif spot > 0 and stop is not None and stop > 0 and t1 is not None and t1 > 0:
            risk = (spot - stop) if side == "LONG" else (stop - spot)
            if risk > (0.005 * spot):
                if side == "LONG" and spot > stop and t1 > spot:
                    live_rr = round((t1 - spot) / risk, 2)
                elif side == "SHORT" and stop > spot and spot > t1:
                    live_rr = round((spot - t1) / risk, 2)
        elif spot <= 0 and e_high is not None and e_high > 0 and stop is not None and stop > 0 and t1 is not None and t1 > 0:
            if side == "LONG" and e_high > stop:
                live_rr = round((t1 - e_high) / (e_high - stop), 2)
            elif side == "SHORT" and stop > e_low:
                live_rr = round((e_low - t1) / (stop - e_low), 2)
        s["live_rr"] = live_rr

        # 5. Calculate exact Limit Price to reach active R:R floor (Task d6)
        from src.tracking import rr_config
        active_floor = rr_config.min_rr()
        limit_price = None
        s["limit_price"] = None
        s["wait_for"] = None
        
        # Only compute stalk limit for active, non-breached, geometrically valid stalking setups
        is_terminal = is_target_hit or is_stop_breached or (spot > 0 and stop is not None and spot <= stop)
        is_geo_valid = (
            stop is not None and stop > 0 
            and t1 is not None and t1 > stop 
            and (e_high is None or e_high > stop)
            and (e_low is None or e_low > stop)
        )
        
        atr_val = 0.0
        if dw:
            try:
                atr_val = float(dw.get("atr14") or dw.get("ATR") or dw.get("rsi2_atr14") or 0.0)
            except Exception:
                atr_val = 0.0
        if atr_val <= 0.0:
            try:
                atr_val = float(s.get("atr") or s.get("atr14") or 0.0)
            except Exception:
                atr_val = 0.0

        if not is_terminal and is_geo_valid and (live_rr < active_floor):
            calc_limit = round((t1 + active_floor * stop) / (1.0 + active_floor), 2)
            # Limit buy must be below current spot price and above stop, and stop width >= 0.7 ATR
            if calc_limit > stop and (spot <= 0 or calc_limit < spot):
                if atr_val <= 0.0 or ((calc_limit - stop) / atr_val) >= 0.7:
                    limit_price = calc_limit
                    s["limit_price"] = limit_price
                    s["wait_for"] = f"wait for ${limit_price:.2f}"

        # 6. Evaluate Canonical Gate with Live Inputs
        if dw:
            gate_in = gate_inputs_from_datawindow(dw)
            gate_in["price"] = spot if spot > 0 else gate_in.get("price")
            gate_in["long_in_zone"] = 1.0 if in_zone else (0.0 if spot > 0 else gate_in.get("long_in_zone"))
            if live_rr is not None:
                gate_in["long_rr_at_market"] = live_rr
            if stop and stop > 0:
                gate_in["long_stop_loss"] = stop

            is_act, fails = check_is_actionable(
                gate_in,
                {
                    "side": side.lower(),
                    "entry": e_high or spot,
                    "stop": stop,
                    "t1": t1,
                    "rr": live_rr if live_rr is not None else None,
                    "in_zone": 1.0 if in_zone else 0.0,
                }
            )
            s["is_actionable"] = is_act
            s["gate_reasons"] = fails

            # Honest PB messaging (Task f3): limit price only when RR/zone is sole blocker
            has_pb_fail = any("PB funnel" in f for f in fails)
            only_rr_or_zone_fail = all(("RR" in f or "floor" in f or "zone" in f) for f in fails) if fails else False
            
            if has_pb_fail:
                s["pb_message"] = "needs PB; waiting for price will not fix it"
                s["limit_price"] = None
                s["wait_for"] = None
            elif not only_rr_or_zone_fail and not is_act:
                s["wait_for"] = None
        elif s.get("stage") == "SCREENER_COIL":
            s["is_actionable"] = False
            s["gate_reasons"] = ["Awaiting Data Window measurement"]
            s["wait_for"] = None
        else:
            s["is_actionable"] = False
            s["gate_reasons"] = s.get("gate_reasons") or ["No Data Window available to evaluate gate"]
            s["wait_for"] = None

        # Opportunity State classification
        if is_target_hit:
            s["opportunity_state"] = "TARGET_HIT"
            s["state_label"] = "🏁 TARGET HIT"
            s["priority_tier"] = 7
        elif is_stop_breached:
            s["opportunity_state"] = "STOP_BREACHED"
            s["state_label"] = "🛑 STOP BREACHED"
            s["priority_tier"] = 8
        elif in_zone:
            s["opportunity_state"] = "IN_ZONE"
            s["state_label"] = "🎯 IN ENTRY ZONE"
            s["priority_tier"] = 1
        elif abs(dist_pct) <= 1.5 and spot > 0:
            s["opportunity_state"] = "COILED_TRIGGER"
            s["state_label"] = f"⚡ COILED TRIGGER ({'+' if dist_pct > 0 else ''}{dist_pct}%)"
            s["priority_tier"] = 2
        elif s.get("conviction", 0) >= 7 and s.get("is_actionable", False):
            s["opportunity_state"] = "HIGH_CONVICTION"
            s["state_label"] = f"💎 HIGH CONVICTION ({s['conviction']}/10)"
            s["priority_tier"] = 3
        elif s.get("stage") == "SCREENER_COIL":
            s["opportunity_state"] = "SCREENER_UNMEASURED"
            s["state_label"] = "⏳ screener coil, unmeasured"
            s["priority_tier"] = 5
        else:
            s["opportunity_state"] = "STALKING"
            s["state_label"] = f"⏳ STALKING ({'+' if dist_pct > 0 else ''}{dist_pct}%)"
            s["priority_tier"] = 4

        processed.append(s)

    today_mt_str = now_mt.strftime("%Y-%m-%d")
    yesterday_mt_str = (now_mt - timedelta(days=1)).strftime("%Y-%m-%d")

    # Sort opportunities:
    # 1. Date Freshness: TODAY's research ALWAYS first, then yesterday, etc.
    # 2. Priority tier: In-Zone (Tier 1) first, then Coiled (Tier 2), terminal states last
    # 3. Distance to entry
    # 4. Score
    # 5. Live R:R
    processed.sort(key=lambda x: (
        0 if x.get("report_date") == today_mt_str else (1 if x.get("report_date") == yesterday_mt_str else 2),
        x["priority_tier"],
        abs(x["dist_pct"]),
        -x["score"],
        -(x.get("live_rr") or 0.0),
    ))

    _OPPS_CACHE = processed
    _OPPS_CACHE_TIME = time.time()
    return processed


def get_actionable_alerts_stream(limit: int = 40) -> List[Dict[str, Any]]:
    """
    Extract fresh actionable alerts from SQLite, enrich with live quotes,
    filter for actionable entries, and categorize into execution states.
    """
    global _ALERTS_CACHE, _ALERTS_CACHE_TIME
    now_ts = time.time()
    if _ALERTS_CACHE is not None and (now_ts - _ALERTS_CACHE_TIME) < _ALERTS_CACHE_TTL:
        return _ALERTS_CACHE

    import sqlite3
    from src.tracking.alert_db import DB_PATH

    alerts: List[Dict[str, Any]] = []
    if not DB_PATH.exists():
        return alerts

    try:
        with sqlite3.connect(str(DB_PATH), timeout=20.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT message_id, date, timestamp, symbol, action, strategy, alert_price, market_price,
                       setup, llm_decision, llm_playbook, score
                FROM alerts
                WHERE (llm_decision IS NOT NULL AND llm_decision != '' AND UPPER(llm_decision) NOT LIKE '%CUT%')
                   OR action IN ('CALLS', 'PUTS', 'LONG', 'SHORT', 'ENTRY', 'BUY', 'SELL')
                ORDER BY rowid DESC
                LIMIT ?
            """, (limit * 2,))
            rows = cur.fetchall()
            for r in rows:
                alerts.append(dict(r))
    except Exception as e:
        logger.warning(f"Failed querying alerts for actionable stream: {e}")
        return []

    if not alerts:
        return []

    # Batch quotes
    symbols = list(set(a["symbol"].upper() for a in alerts if a.get("symbol")))
    live_quotes: Dict[str, float] = {}
    try:
        live_quotes = get_current_prices_batch(symbols, context="actionable_alerts")
    except Exception:
        pass

    actionable: List[Dict[str, Any]] = []

    for a in alerts:
        sym = a["symbol"].upper()
        spot = live_quotes.get(sym) or float(a.get("alert_price") or a.get("market_price") or 0.0)
        a["spot_price"] = spot

        action = str(a.get("action") or "").upper()
        side = "SHORT" if any(x in action for x in ("PUT", "SHORT", "SELL")) else "LONG"
        a["side"] = side

        # Extract tactical levels from playbook or alert price
        playbook = a.get("llm_playbook") or ""
        stop_match = re.search(r"\bStop(?:\s*Loss)?\b\s*[:=\s]\s*\$?(\d+(?:\.\d+)?)", playbook, re.IGNORECASE)
        target_match = re.search(r"\bTarget(?:\s*1)?\b\s*[:=\s]\s*\$?(\d+(?:\.\d+)?)", playbook, re.IGNORECASE)

        try:
            stop_val = float(stop_match.group(1)) if stop_match else 0.0
        except Exception:
            stop_val = 0.0

        try:
            target_val = float(target_match.group(1)) if target_match else 0.0
        except Exception:
            target_val = 0.0
        entry_val = float(a.get("alert_price") or spot)

        a["entry_price"] = round(entry_val, 2)
        a["stop_price"] = round(stop_val, 2)
        a["target_price"] = round(target_val, 2)

        # Distance % from entry
        dist = 0.0
        if spot > 0 and entry_val > 0:
            dist = round(((spot - entry_val) / entry_val) * 100.0, 2)
        a["dist_pct"] = dist

        # R:R
        rr = 0.0
        if spot > 0 and stop_val > 0 and target_val > 0:
            if side == "LONG" and spot > stop_val:
                rr = round((target_val - spot) / (spot - stop_val), 2)
            elif side == "SHORT" and stop_val > spot:
                rr = round((spot - target_val) / (stop_val - spot), 2)
        a["live_rr"] = max(rr, 0.0)

        # In Zone Check
        in_zone = abs(dist) <= 0.6
        a["in_zone"] = in_zone

        a["source"] = "tv_alert"
        a["measured_actionable"] = False
        a["gate_reasons"] = ["levels from alert text"]

        if in_zone:
            a["alert_state"] = "IN_ZONE"
            a["state_label"] = "alert-text levels, unmeasured"
            a["urgency_rank"] = 1
        elif abs(dist) <= 1.8:
            a["alert_state"] = "COILING"
            a["state_label"] = f"⚡ COILING ({'+' if dist > 0 else ''}{dist}%)"
            a["urgency_rank"] = 2
        else:
            a["alert_state"] = "STALKING"
            a["state_label"] = f"⏳ STALKING ({'+' if dist > 0 else ''}{dist}%)"
            a["urgency_rank"] = 3

        actionable.append(a)

    res = actionable[:limit]
    _ALERTS_CACHE = res
    _ALERTS_CACHE_TIME = time.time()
    return res
