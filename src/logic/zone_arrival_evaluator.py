"""
src/logic/zone_arrival_evaluator.py

On-Arrival Real-Time Evaluator & Tactical Triage for Stalking Watch Targets.
Automatically evaluates setups when live price reaches or enters the designated
entry zone (e.g. hits around $340 for GOOGL). Replaces passive "STALKING" with
decisive institutional verdicts (ACTIONABLE BUY SHARES vs STAND ASIDE / VETO).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.clients.price_client import get_current_price

logger = logging.getLogger(__name__)


def evaluate_target_on_zone_arrival(
    ticker: str,
    spot_price: float,
    watch_target: Dict[str, Any],
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Evaluate a watch target when price arrives at/near its entry zone.
    
    Returns structured evaluation dict:
    {
        "ticker": "GOOGL",
        "spot_price": 343.35,
        "side": "LONG",
        "is_actionable_now": True,
        "verdict": "ACTIONABLE_BUY",
        "verdict_label": "🟢 BUY SHARES NOW",
        "playbook": "Structural floor defended at $340.00...",
        "rr_ratio": 3.29,
        "entry_mid": 343.78,
        "tactical_stop": 340.00,
        "target_1": 354.37,
        "target_2": 364.17,
        "evaluated_at": "2026-10-02T10:55:00-06:00",
        "reasons": [...]
    }
    """
    sym = ticker.strip().upper()
    side = str(watch_target.get("side") or "LONG").upper()
    
    # Levels from watch target or raw_json
    entry_low = float(watch_target.get("entry_zone_low") or 0.0)
    entry_high = float(watch_target.get("entry_zone_high") or 0.0)
    stop = float(watch_target.get("tactical_stop") or watch_target.get("invalidation_price") or 0.0)
    target_1 = float(watch_target.get("target_1") or 0.0)
    target_2 = float(watch_target.get("target_2") or 0.0)
    
    if entry_low > entry_high and entry_high > 0:
        entry_low, entry_high = entry_high, entry_low
    
    entry_mid = round((entry_low + entry_high) / 2.0, 2) if (entry_low > 0 and entry_high > 0) else spot_price

    # Guard against unquoted / missing spot price (do not treat $0.00 as stop breach!)
    if spot_price <= 0:
        return {
            "ticker": sym,
            "spot_price": 0.0,
            "side": side,
            "is_actionable_now": False,
            "verdict": "UNQUOTED",
            "verdict_label": "⏳ UNQUOTED",
            "playbook": f"⏳ UNQUOTED — Awaiting live market quote for {sym}.",
            "live_rr": 0.0,
            "risk_dollars": 0.0,
            "risk_pct": 0.0,
            "reward_dollars": 0.0,
            "reward_pct": 0.0,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "entry_mid": entry_mid,
            "tactical_stop": stop,
            "target_1": target_1,
            "target_2": target_2,
            "in_zone": False,
            "evaluated_at": datetime.now(ZoneInfo("America/Denver")).isoformat(),
            "reasons": ["Price is unquoted (<= 0). Cannot evaluate without market price."],
        }
    
    # No levels means return UNMEASURED and write nothing
    if stop <= 0 or target_1 <= 0:
        return {
            "ticker": sym,
            "spot_price": spot_price,
            "side": side,
            "is_actionable_now": False,
            "verdict": "UNMEASURED",
            "verdict_label": "⏳ UNMEASURED",
            "playbook": f"⏳ UNMEASURED — Missing tactical stop or target levels for {sym}.",
            "live_rr": 0.0,
            "risk_dollars": 0.0,
            "risk_pct": 0.0,
            "reward_dollars": 0.0,
            "reward_pct": 0.0,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "entry_mid": entry_mid,
            "tactical_stop": stop,
            "target_1": target_1,
            "target_2": target_2,
            "in_zone": False,
            "evaluated_at": datetime.now(ZoneInfo("America/Denver")).isoformat(),
            "reasons": ["Missing tactical stop or target levels."],
        }

    # 1. Level defense check
    is_stop_breached = False
    if side == "LONG" and spot_price <= stop:
        is_stop_breached = True
    elif side == "SHORT" and spot_price >= stop:
        is_stop_breached = True

    # 2. Target already met check
    target_already_hit = False
    if side == "LONG" and target_1 > 0 and spot_price >= target_1:
        target_already_hit = True
    elif side == "SHORT" and target_1 > 0 and spot_price <= target_1:
        target_already_hit = True

    # 3. Live R:R Calculation on equity shares from current spot
    risk_dollars = abs(spot_price - stop)
    risk_pct = round((risk_dollars / spot_price) * 100.0, 2) if spot_price > 0 else 0.0
    
    reward_dollars = abs(target_1 - spot_price)
    reward_pct = round((reward_dollars / spot_price) * 100.0, 2) if spot_price > 0 else 0.0
    
    live_rr = round(reward_dollars / max(0.01, risk_dollars), 2)

    # 4. Proximity to Entry Zone
    in_zone = False
    if entry_low > 0 and entry_high > 0:
        in_zone = (entry_low <= spot_price <= entry_high)
    else:
        in_zone = (abs(spot_price - entry_mid) / max(1.0, entry_mid)) <= 0.015

    # Near zone threshold (within 1.5% of zone boundary)
    near_zone = False
    dist_to_zone = 0.0
    if not in_zone and entry_low > 0 and entry_high > 0:
        if side == "LONG":
            if spot_price > entry_high:
                dist_to_zone = round(((spot_price - entry_high) / entry_high) * 100.0, 2)
                near_zone = (dist_to_zone <= 2.0)
            elif spot_price < entry_low:
                dist_to_zone = round(((entry_low - spot_price) / entry_low) * 100.0, 2)
                near_zone = (dist_to_zone <= 1.0)
        else:
            if spot_price < entry_low:
                dist_to_zone = round(((entry_low - spot_price) / entry_low) * 100.0, 2)
                near_zone = (dist_to_zone <= 2.0)
            elif spot_price > entry_high:
                dist_to_zone = round(((spot_price - entry_high) / entry_high) * 100.0, 2)
                near_zone = (dist_to_zone <= 1.0)

    # 5. Earnings proximity check
    days_to_earnings = None
    try:
        from src.clients.earnings_client import get_next_earnings_days
        days_to_earnings = get_next_earnings_days(sym)
    except Exception:
        pass

    now_iso = datetime.now(ZoneInfo("America/Denver")).isoformat()
    reasons = []

    # Determine Verdict
    if is_stop_breached:
        verdict = "STAND_ASIDE"
        verdict_label = "🛑 INVALIDATED (STOP BREACHED)"
        is_actionable = False
        reasons.append(f"Price ${spot_price:.2f} breached structural stop at ${stop:.2f}.")
        playbook = (
            f"⛔ STAND ASIDE — Thesis Invalidated: Price ${spot_price:.2f} has dropped through "
            f"the critical defense floor at ${stop:.2f}. Do not buy falling knife."
        )
    elif target_already_hit:
        verdict = "TARGET_HIT"
        verdict_label = "🏁 TARGET 1 HIT (DO NOT CHASE)"
        is_actionable = False
        reasons.append(f"Price ${spot_price:.2f} already hit Target 1 (${target_1:.2f}).")
        playbook = (
            f"🏁 COMPLETED / TRIM — Price reached ${spot_price:.2f}, crossing Target 1 (${target_1:.2f}). "
            f"Do not chase new share entries here; lock gains on runners."
        )
    elif days_to_earnings is not None and days_to_earnings <= 2:
        verdict = "STAND_ASIDE"
        verdict_label = "⚠️ EARNINGS BLACKOUT (<48H)"
        is_actionable = False
        reasons.append(f"Binary earnings catalyst in {days_to_earnings} day(s).")
        playbook = (
            f"⚠️ EARNINGS GATE VETO — Earnings report due in {days_to_earnings} day(s). "
            f"Avoid establishing new equity swing positions ahead of overnight gap risk."
        )
    elif in_zone:
        if live_rr >= 2.0 and side == "LONG":
            verdict = "ACTIONABLE_BUY"
            verdict_label = "🟢 BUY SHARES NOW"
            is_actionable = True
            reasons.append(f"Price ${spot_price:.2f} is in buy box [${entry_low:.2f}–${entry_high:.2f}] holding above stop ${stop:.2f}.")
            reasons.append(f"Asymmetric risk/reward: {live_rr:.2f}:1 R:R (Target: ${target_1:.2f}, Risk: {risk_pct}%).")
            
            # Exact execution recommendation
            limit_price = round(spot_price, 2)
            playbook = (
                f"🟢 ACTIONABLE EXECUTION PERMISSION ({side}):\n"
                f"• Level Defense: Confirmed structural floor shelf at ${stop:.2f} is holding intact.\n"
                f"• Share Order: Enter {side} shares around ${limit_price:.2f} (Zone: ${entry_low:.2f}–${entry_high:.2f}).\n"
                f"• Risk & Target: Tactical Stop = ${stop:.2f} (Risk: -{risk_pct}%) | Target 1 = ${target_1:.2f} (+{reward_pct}%) | R:R = {live_rr:.2f}:1."
            )
        else:
            verdict = "STALKING"
            verdict_label = f"⏳ IN ZONE (POOR R:R {live_rr:.1f}:1)"
            is_actionable = False
            reasons.append(f"Price is in zone but reward/risk ratio ({live_rr:.2f}:1) is under minimum 2.0:1 institutional threshold.")
            playbook = (
                f"⏳ STALK FOR BETTER FILL — Spot ${spot_price:.2f} is in zone, but current stop distance "
                f"yields only {live_rr:.2f}:1 R:R. Wait for pullback closer to ${stop:.2f} before entry."
            )
    elif near_zone:
        verdict = "STALKING"
        verdict_label = f"⚡ HOT STALK ({dist_to_zone:.1f}% to zone)"
        is_actionable = False
        reasons.append(f"Price ${spot_price:.2f} is within {dist_to_zone:.1f}% of entry zone [${entry_low:.2f}–${entry_high:.2f}].")
        playbook = (
            f"⚡ STALKING PULLBACK — Price ${spot_price:.2f} is approaching the designated entry zone "
            f"[${entry_low:.2f}–${entry_high:.2f}]. Awaiting test of support shelf at ${stop:.2f}."
        )
    else:
        verdict = "STALKING"
        verdict_label = "⏳ STALKING"
        is_actionable = False
        reasons.append(f"Price ${spot_price:.2f} is outside entry zone [${entry_low:.2f}–${entry_high:.2f}].")
        playbook = (
            f"⏳ STALKING — Price ${spot_price:.2f} remains outside the entry zone. "
            f"No execution permitted until price pulls back to [${entry_low:.2f}–${entry_high:.2f}]."
        )

    eval_result = {
        "ticker": sym,
        "spot_price": spot_price,
        "side": side,
        "is_actionable_now": is_actionable,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "playbook": playbook,
        "live_rr": live_rr,
        "risk_dollars": risk_dollars,
        "risk_pct": risk_pct,
        "reward_dollars": reward_dollars,
        "reward_pct": reward_pct,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_mid": entry_mid,
        "tactical_stop": stop,
        "target_1": target_1,
        "target_2": target_2,
        "in_zone": in_zone,
        "evaluated_at": now_iso,
        "reasons": reasons,
    }


    logger.info(
        f"🎯 [ZONE EVALUATION] [{sym}] Verdict: {verdict_label} | Spot: ${spot_price:.2f} | R:R: {live_rr:.2f}:1 | Actionable: {is_actionable}"
    )
    return eval_result
