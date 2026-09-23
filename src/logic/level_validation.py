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
from typing import Any, Dict, List, Tuple

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
    """Planned R:R from zone midpoint."""
    mid = _zone_midpoint(entry_low, entry_high)
    if not mid:
        return 0.0
    if side == "LONG":
        risk = mid - stop
        reward = target_1 - mid
    else:
        risk = stop - mid
        reward = mid - target_1
    if risk > 0 and reward > 0:
        return round(reward / risk, 4)
    return 0.0


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
        "stop": ("Long Stop Loss", "rsi2 fixed stop"),
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
            llm_val = plan.get(k, 0.0) or 0.0
            if llm_val:
                break
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


def validate_levels(plan: Dict[str, Any], dw: Dict[str, Any], side: str) -> Tuple[bool, List[str]]:
    """Validate trade levels before persisting.

    Checks:
    1. Level ordering: LONG requires stop < entry_low <= entry_high < target_1 <= target_2.
    2. R:R floor: planned R:R from zone midpoint >= LEVEL_RR_FLOOR (default 1.5).
    3. Stop distance: at least LEVEL_ATR_STOP_MIN x ATR14 from zone midpoint.
    4. Pine drift: flag when LLM levels differ from Pine by more than LEVEL_PINE_DRIFT_ATR x ATR.
    5. Options: strike_validator.validate_strike_geometry on options_plan.

    Returns (ok, reasons). ok=True means all checks pass. Failures are logged
    as source=judge, verdict=REJECTED_BY_GATE.
    """
    reasons: List[str] = []
    side = (side or "LONG").upper()

    entry_low = float(plan.get("entry_low") or plan.get("entry_zone_low") or 0.0)
    entry_high = float(plan.get("entry_high") or plan.get("entry_zone_high") or 0.0)
    stop = float(plan.get("stop") or plan.get("tactical_stop") or 0.0)
    target_1 = float(plan.get("target_1") or 0.0)
    target_2 = float(plan.get("target_2") or 0.0)

    # ── 1. Level ordering ──────────────────────────────────────
    if side == "LONG":
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
    else:
        t2_ok = (target_2 <= target_1) if target_2 > 0 else True
        if not (t2_ok and target_1 < entry_low <= entry_high < stop):
            if target_2 > 0 and not (target_2 <= target_1):
                reasons.append(f"target_2 ${target_2:.4f} > target_1 ${target_1:.4f}")
            if not (target_1 < entry_low):
                reasons.append(f"target_1 ${target_1:.4f} >= entry_low ${entry_low:.4f}")
            if entry_low > entry_high:
                reasons.append(f"entry_low ${entry_low:.4f} > entry_high ${entry_high:.4f}")
            if not (entry_high < stop):
                reasons.append(f"entry_high ${entry_high:.4f} >= stop ${stop:.4f}")

    if not entry_low or not entry_high or not stop or not target_1:
        reasons.append("missing required levels (entry_low, entry_high, stop, target_1)")

    # ── 2. R:R floor ───────────────────────────────────────────
    rr = _planned_rr(entry_low, entry_high, stop, target_1, side)
    if rr > 0 and rr < LEVEL_RR_FLOOR:
        reasons.append(
            f"planned R:R {rr:.4f} from zone midpoint below floor {LEVEL_RR_FLOOR}"
        )

    # ── 3. Stop distance ───────────────────────────────────────
    mid = _zone_midpoint(entry_low, entry_high)
    if mid > 0 and stop > 0:
        stop_dist = abs(mid - stop)
        atr = _dw_num(dw, "RSI2 ATR14", "rsi2_atr14", "atr14", "ATR 14", "atr_14")
        if atr <= 0:
            reasons.append("atr_unavailable")
        else:
            dist_in_atr = stop_dist / atr
            if dist_in_atr < LEVEL_ATR_STOP_MIN:
                reasons.append(
                    f"stop distance {dist_in_atr:.4f} ATR from zone midpoint "
                    f"below minimum {LEVEL_ATR_STOP_MIN} ATR"
                )

    # ── 4. Pine drift ──────────────────────────────────────────
    drifts = _pine_drift(plan, dw, side)
    for d in drifts:
        reasons.append(
            f"pine drift on {d['field']}: LLM ${d['llm_value']:.4f} vs "
            f"Pine ${d['pine_value']:.4f} (diff {d['drift_in_atr']:.2f} ATR, "
            f"threshold {d['threshold_atr']} ATR)"
        )

    # ── 5. Options geometry ────────────────────────────────────
    options_plan = plan.get("options_plan", {}) or {}
    structure = str(options_plan.get("structure", "") or "").upper().replace(" ", "_")
    if structure and structure != "NONE":
        spot = _dw_num(dw, "close", "Close")
        exp_move = _dw_num(dw, "exp_move_pct", "Exp Move % (21b)", "Exp Move Pct 21b")
        long_strike = float(options_plan.get("long_strike") or 0.0)
        short_strike = float(options_plan.get("short_strike") or 0.0)
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
