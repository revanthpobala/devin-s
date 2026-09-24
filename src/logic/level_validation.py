"""
src/logic/level_validation.py — Level-validation gate for every write path.

validate_levels(plan, dw, side) -> (ok, reasons)

Runs deterministic checks on LLM-emitted trade levels before they are
persisted to any database or report. Failures are logged as
source=judge, verdict=REJECTED_BY_GATE so the gate itself is measurable.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from src import config
from src.logic.strike_validator import validate_strike_geometry

logger = logging.getLogger(__name__)

# Configurable gates imported directly from src.config
LEVEL_RR_FLOOR = config.LEVEL_RR_FLOOR
LEVEL_ATR_STOP_MIN = config.LEVEL_ATR_STOP_MIN
LEVEL_PINE_DRIFT_ATR = config.LEVEL_PINE_DRIFT_ATR


def _dw_num(dw: Dict[str, Any], *keys: str) -> float:
    """Case-insensitive lookup in a parsed data-window dict; returns 0.0 if absent."""
    if not dw:
        return 0.0
    lower = {str(k).lower(): v for k, v in dw.items()}
    for key in keys:
        val = dw.get(key)
        if val is None:
            val = lower.get(str(key).lower())
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def _zone_midpoint(entry_low: float, entry_high: float) -> float:
    if entry_low and entry_high:
        return (entry_low + entry_high) / 2.0
    return 0.0


def _planned_rr(entry_low: float, entry_high: float, stop: float, target_1: float, side: str) -> float:
    """Planned R:R from entry_high (worst-case fill for LONG)."""
    if not entry_high or not target_1 or not stop:
        return 0.0
    if side == "LONG":
        risk = entry_high - stop
        reward = target_1 - entry_high
    else:
        risk = stop - entry_low
        reward = entry_low - target_1
    if risk > 0 and reward > 0:
        return round(reward / risk, 4)
    return 0.0


def _f(val: Any) -> Optional[float]:
    """Safe float cast returning None on error."""
    if val is None or val == "" or val == "N/A":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _pine_drift(
    plan: Dict[str, Any], dw: Dict[str, Any], side: str
) -> List[Dict[str, Any]]:
    """Compare LLM levels against Pine exports; return list of drift dicts."""
    drifts: List[Dict[str, Any]] = []
    if side != "LONG":
        return drifts

    atr = _dw_num(dw, "RSI2 ATR14", "rsi2_atr14", "atr14", "ATR 14", "atr_14")
    if atr <= 0:
        return drifts

    pine_map = {
        "entry_low": ("Long Entry Zone Bot",),
        "entry_high": ("Long Entry Zone Top",),
        "stop": ("Long Stop Loss",),
        "target_1": ("Long Target", "Long Target T1 Waypoint"),
    }

    llm_field_to_pine_keys = {
        "entry_low": ("entry_zone_low",),
        "entry_high": ("entry_zone_high",),
        "stop": ("tactical_stop",),
        "target_1": ("target_1",),
    }

    for plan_key, pine_keys in pine_map.items():
        llm_keys = llm_field_to_pine_keys[plan_key]
        pine_val = _dw_num(dw, *pine_keys)
        llm_val = 0.0
        for k in llm_keys:
            try:
                v = plan.get(k)
                if v is not None:
                    llm_val = float(v)
                    if llm_val:
                        break
            except (ValueError, TypeError):
                continue
        if pine_val > 0 and llm_val:
            diff = abs(llm_val - pine_val)
            if atr > 0 and diff > LEVEL_PINE_DRIFT_ATR * atr:
                drifts.append({
                    "field": plan_key,
                    "pine_value": pine_val,
                    "llm_value": llm_val,
                    "abs_diff": round(diff, 4),
                    "atr": round(atr, 4),
                    "drift_in_atr": round(diff / atr, 4),
                    "threshold_atr": LEVEL_PINE_DRIFT_ATR,
                })

    return drifts


def _compute_atr14_from_bars(ticker: str, date_str: str) -> float:
    """Compute 14-period ATR from historical daily bars up to date_str."""
    if not ticker:
        return 0.0
    try:
        from datetime import datetime, timedelta
        from src.tracking.execution_validator import get_bars_since_date
        try:
            d_obj = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        except Exception:
            d_obj = datetime.now().date()
        start_date = (d_obj - timedelta(days=45)).strftime("%Y-%m-%d")
        bars = get_bars_since_date(ticker, start_date)
        if bars is None or len(bars) < 2:
            return 0.0

        if hasattr(bars, "index"):
            idx_strs = [str(x)[:10] for x in bars.index]
            sub_mask = [d <= str(date_str)[:10] for d in idx_strs]
            sub = bars[sub_mask]
            if len(sub) < 2:
                sub = bars
        else:
            sub = bars

        trs = []
        for i in range(1, len(sub)):
            prev_close = float(sub.iloc[i-1].get("Close", sub.iloc[i-1].get("close", 0.0)))
            high = float(sub.iloc[i].get("High", sub.iloc[i].get("high", 0.0)))
            low = float(sub.iloc[i].get("Low", sub.iloc[i].get("low", 0.0)))
            if high > 0 and low > 0 and prev_close > 0:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)

        if len(trs) >= 14:
            return round(sum(trs[-14:]) / 14.0, 4)
        elif trs:
            return round(sum(trs) / len(trs), 4)
    except Exception as e:
        logger.debug(f"Failed to compute fallback ATR14 for {ticker}: {e}")
    return 0.0


def validate_levels(
    plan: Dict[str, Any],
    dw: Dict[str, Any],
    side: str,
    ticker: Optional[str] = None,
    date_str: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Validate trade levels before persisting.

    Checks:
    1. Level ordering: LONG requires stop < entry_low <= entry_high < target_1 <= target_2.
    2. R:R floor: planned R:R from zone midpoint >= LEVEL_RR_FLOOR (default 1.5).
    3. Stop placement:
       - Measured Pine lanes (RR_SETUP, RR_SETUP_STRONG, CODE20, OVERSOLD): must match Pine Long Stop Loss & Long Target within 0.05.
       - RSI2 lane: abs(stop - (close - 2*ATR)) <= 0.05*ATR and abs(t1 - (close + 4*ATR)) <= 0.05*ATR.
       - Judge invented (FLOOR_DEFENSE, BREAKOUT, WATCH_SHADOW): 1-ATR stop floor.
    4. Pine drift: flag when LLM levels differ from Pine by more than LEVEL_PINE_DRIFT_ATR x ATR (skipped for RSI2).
    5. Options: strike_validator.validate_strike_geometry on options_plan.

    Returns (ok, reasons). ok=True means all checks pass. Failures are logged
    as source=judge, verdict=REJECTED_BY_GATE.
    """
    reasons: List[str] = []
    side = (side or "LONG").upper()
    ticker = (ticker or str(plan.get("ticker") or dw.get("ticker") or "")).strip().upper()
    date_str = date_str or str(plan.get("date") or dw.get("date") or "")

    # Safely cast plan values with float(); return gate failure on bad values, never raise
    try:
        entry_low = float(plan.get("entry_low") or plan.get("entry_zone_low") or 0.0)
        entry_high = float(plan.get("entry_high") or plan.get("entry_zone_high") or 0.0)
        stop = float(plan.get("stop") or plan.get("tactical_stop") or 0.0)
        target_1 = float(plan.get("target_1") or 0.0)
        target_2 = float(plan.get("target_2") or 0.0)
    except (ValueError, TypeError) as e_cast:
        return False, [f"Invalid non-numeric level in plan: {e_cast}"]

    # ── Reject SHORT ──────────────────────────────────────────
    if side != "LONG":
        reasons.append("SHORT side rejected — measured edge is long-only post-COVID")
        return False, reasons

    # ── 1. Level ordering ──────────────────────────────────────
    t2_ok = (target_1 <= target_2) if target_2 > 0 else True
    if not (stop < entry_low <= entry_high < target_1 and t2_ok):
        if stop >= entry_low:
            reasons.append(f"stop ${stop:.4f} >= entry_low ${entry_low:.4f}")
        if entry_low > entry_high:
            reasons.append(f"entry_low ${entry_low:.4f} > entry_high ${entry_high:.4f}")
        if entry_high >= target_1:
            reasons.append(f"entry_high ${entry_high:.4f} >= target_1 ${target_1:.4f}")
        if target_2 > 0 and target_1 > target_2:
            reasons.append(f"target_1 ${target_1:.4f} > target_2 ${target_2:.4f}")

    if not entry_low or not entry_high or not stop or not target_1:
        reasons.append("missing required levels (entry_low, entry_high, stop, target_1)")

    # ── 2. ATR14 presence (hard fail if missing) ───────────────
    atr = _dw_num(dw, "RSI2 ATR14", "rsi2_atr14", "atr14", "ATR 14", "atr_14", "ATR", "wilder_atr14")
    if atr <= 0:
        atr = _compute_atr14_from_bars(ticker, date_str)
    if atr <= 0:
        reasons.append("missing ATR14 — hard fail")

    # ── Lanes classification ──────────────────────────────────
    setup_lane = str(plan.get("setup_lane") or plan.get("lane") or dw.get("setup_lane") or "").upper()
    is_measured_pine = setup_lane in ("RR_SETUP", "RR_SETUP_STRONG", "CODE20", "OVERSOLD")
    is_rsi2 = (setup_lane == "RSI2")
    is_measured_lane = is_measured_pine or is_rsi2

    spot = _dw_num(dw, "close", "Close", "spot", "last", "price")
    if spot <= 0:
        try:
            spot = float(plan.get("spot") or plan.get("price") or 0.0)
        except (ValueError, TypeError):
            spot = 0.0

    # ── 3. Stop placement & geometry ──────────────────────────
    if is_measured_pine:
        pine_stop = _dw_num(dw, "Long Stop Loss", "long_stop_loss")
        pine_target = _dw_num(dw, "Long Target", "long_target")
        if pine_stop <= 0 or pine_target <= 0:
            reasons.append(f"pine_levels_missing: {setup_lane} lane requires Pine Long Stop Loss and Long Target, but levels are missing in Data Window (stop={pine_stop}, target={pine_target})")
        else:
            if abs(stop - pine_stop) > 0.05:
                reasons.append(
                    f"{setup_lane} lane requires stop to match Pine Long Stop Loss (${pine_stop:.2f}), got ${stop:.2f}"
                )
            if abs(target_1 - pine_target) > 0.05:
                reasons.append(
                    f"{setup_lane} lane requires target_1 to match Pine Long Target (${pine_target:.2f}), got ${target_1:.2f}"
                )
    elif is_rsi2:
        close_px = spot if spot > 0 else _dw_num(dw, "close", "Close")
        if close_px > 0 and atr > 0:
            exp_stop = close_px - (2.0 * atr)
            exp_t1 = close_px + (4.0 * atr)
            tol = 0.05 * atr
            if abs(stop - exp_stop) > tol or abs(target_1 - exp_t1) > tol:
                reasons.append(
                    f"rsi2_geometry: stop ${stop:.2f} (exp ${exp_stop:.2f}) or target_1 ${target_1:.2f} (exp ${exp_t1:.2f}) deviates > 0.05 ATR (${tol:.2f}) from close +/- 2/4 ATR"
                )
    else:
        # Judge-invented levels (FLOOR_DEFENSE, BREAKOUT, WATCH_SHADOW): 1-ATR stop floor
        if atr > 0 and entry_low > 0:
            pine_stop = _dw_num(dw, "Long Stop Loss", "long_stop_loss")
            max_allowed_stop = entry_low - (LEVEL_ATR_STOP_MIN * atr)
            if pine_stop > 0:
                max_allowed_stop = min(max_allowed_stop, pine_stop + 0.05)
            if stop > max_allowed_stop:
                reasons.append(
                    f"stop ${stop:.4f} exceeds max allowed stop ${max_allowed_stop:.4f} "
                    f"(must be <= min(entry_low - {LEVEL_ATR_STOP_MIN}*ATR, Pine stop))"
                )

    # ── 4. R:R floor & At-Market R:R ───────────────────────────
    if spot > stop and target_1 > spot:
        rr_at_market = round((target_1 - spot) / (spot - stop), 4)
    else:
        rr_at_market = 0.0
    plan["rr_at_market"] = rr_at_market

    # Skip planned R:R floor and at-market R:R floor for measured lanes
    if not is_measured_lane:
        rr = _planned_rr(entry_low, entry_high, stop, target_1, side)
        if rr > 0 and rr < LEVEL_RR_FLOOR:
            reasons.append(
                f"planned R:R {rr:.4f} from entry_high below floor {LEVEL_RR_FLOOR}"
            )
        if rr_at_market < 2.0:
            reasons.append(
                f"at-market R:R {rr_at_market:.2f} below 2.0 floor for lane {setup_lane or 'DEFAULT'}"
            )

    # ── 5. Target 1 ceiling vs 21b Expected Move (skip for all measured lanes & RSI2) ──
    if not is_measured_lane:
        close_px = spot if spot > 0 else _dw_num(dw, "close", "Close")
        exp_move_pct = _dw_num(dw, "Exp Move % (21b)", "Exp Move Pct 21b", "exp_move_pct", "exp_move")
        if exp_move_pct > 1.0:
            exp_move_pct = exp_move_pct / 100.0
        if close_px > 0 and exp_move_pct > 0 and target_1 > 0:
            max_t1 = close_px * (1.0 + 1.5 * exp_move_pct)
            if target_1 > round(max_t1 + 0.05, 2):
                reasons.append(
                    f"target_1 ${target_1:.2f} exceeds 1.5x 21b expected move ceiling ${max_t1:.2f}"
                )

    # ── 6. Earnings inside 21 bars (reject NEW) ────────────────
    kind = str(plan.get("kind") or "NEW").upper()
    if kind == "NEW" and ticker:
        try:
            from datetime import date, datetime
            signal_d = None
            if date_str:
                try:
                    signal_d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
                except Exception:
                    signal_d = None
            if signal_d is None:
                signal_d = date.today()

            from src.clients.earnings_client import get_next_earnings_days
            days_to_earnings = get_next_earnings_days(ticker, as_of_date=signal_d)
            dw_days = _dw_num(dw, "earnings_days", "days_to_earnings", "bars_to_earnings")
            if dw_days > 0 and (days_to_earnings is None or dw_days < days_to_earnings):
                days_to_earnings = int(dw_days)
            if days_to_earnings is not None and 0 <= days_to_earnings <= 21:
                reasons.append(
                    f"earnings inside 21 bars ({days_to_earnings} trading bars away from {signal_d}) — fail for NEW"
                )
        except Exception as e:
            logger.debug(f"Earnings lookup failed in validate_levels: {e}")

    # ── 7. Pine drift (skip for RSI2) ──────────────────────────
    if not is_rsi2:
        drifts = _pine_drift(plan, dw, side)
        for d in drifts:
            reasons.append(
                f"pine drift on {d['field']}: LLM ${d['llm_value']:.4f} vs "
                f"Pine ${d['pine_value']:.4f} (diff {d['drift_in_atr']:.2f} ATR, "
                f"threshold {d['threshold_atr']} ATR)"
            )

    # ── 8. Options geometry ────────────────────────────────────
    options_plan = plan.get("options_plan", {}) or {}
    structure = str(options_plan.get("structure", "") or "").upper().replace(" ", "_")
    if structure and structure != "NONE":
        spot = _dw_num(dw, "close", "Close")
        exp_move = _dw_num(dw, "exp_move_pct", "Exp Move % (21b)", "Exp Move Pct 21b")
        long_strike = _f(options_plan.get("long_strike"))
        short_strike = _f(options_plan.get("short_strike"))
        if (options_plan.get("long_strike") is not None and long_strike is None) or (options_plan.get("short_strike") is not None and short_strike is None):
            reasons.append("bad_strike: non-numeric or invalid strike in options_plan")
        is_valid, defects = validate_strike_geometry(
            strategy_type=structure,
            spot_price=spot,
            exp_move_pct_21b=exp_move,
            long_strike=long_strike or None,
            short_strike=short_strike or None,
        )
        if not is_valid:
            for defect in defects:
                reasons.append(f"options geometry: {defect}")

    ok = len(reasons) == 0
    if not ok:
        logger.warning(
            f"[LEVEL_GATE] side={side} FAILED: {'; '.join(reasons)}"
        )

    return ok, reasons
