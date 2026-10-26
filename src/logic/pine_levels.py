"""Pine structural cluster as the binding geometry for judge plans.

The only measured long edge (in zone, at-market R:R >= 2) was earned on Pine's own zone, stop and target. LLM-drawn
levels have no measured basis: in October 2026 none of 118 judge plans passed the level gate, mostly on stops tighter
than Pine's. So when Pine has a valid long plan its levels overwrite the judge's; the judge keeps the verdict, the
narrative and target_2 above Pine's target.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _f(dw: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        try:
            v = float(dw.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return None


def pine_long_plan(dw: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Pine zone/stop/target when they form a usable long: stop < close < target, zone (if any) above stop."""
    close = _f(dw, "close", "Close")
    stop = _f(dw, "Long Stop Loss", "long_stop_loss")
    target = _f(dw, "Long Target", "long_target")
    if not (close and stop and target) or not (stop < close < target):
        return None
    lo = _f(dw, "Long Entry Zone Bot", "long_entry_zone_bot")
    hi = _f(dw, "Long Entry Zone Top", "long_entry_zone_top")
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    if not (lo and hi and stop < lo < target):
        lo = hi = None
    return {"close": close, "stop": stop, "target": target, "zone_low": lo, "zone_high": hi}


def apply_pine_levels(watch_data: Dict[str, Any], dw: Dict[str, Any]) -> str:
    """Overwrite shares_plan stop/target/zone with Pine's when valid. Returns 'pine' or 'llm'."""
    if str(watch_data.get("side") or "LONG").upper() != "LONG":
        watch_data["levels_source"] = "llm"
        return "llm"
    pine = pine_long_plan(dw)
    sp = watch_data.setdefault("shares_plan", {})
    if not pine:
        watch_data["levels_source"] = "llm"
        return "llm"
    watch_data["llm_levels"] = {k: sp.get(k) for k in ("entry_zone_low", "entry_zone_high", "tactical_stop", "target_1", "target_2")}
    sp["tactical_stop"] = round(pine["stop"], 2)
    sp["target_1"] = round(pine["target"], 2)
    try:
        t2 = float(sp.get("target_2") or 0)
    except (TypeError, ValueError):
        t2 = 0.0
    sp["target_2"] = t2 if t2 > pine["target"] else 0.0
    if pine["zone_low"]:
        sp["entry_zone_low"], sp["entry_zone_high"] = round(pine["zone_low"], 2), round(pine["zone_high"], 2)
        if str(sp.get("entry_type") or "").upper() in ("", "NO_ENTRY", "NONE"):
            sp["entry_type"] = "LIMIT"
    else:
        try:
            lo = float(sp.get("entry_zone_low") or 0)
        except (TypeError, ValueError):
            lo = 0.0
        if lo <= pine["stop"]:
            sp["entry_zone_low"] = sp["entry_zone_high"] = 0.0
            sp["entry_type"] = "NO_ENTRY"
    inv = watch_data.get("invalidation")
    if isinstance(inv, dict):
        inv["price_level"] = sp["tactical_stop"]
    watch_data["levels_source"] = "pine"
    return "pine"


def apply_rsi2_order(watch_data: Dict[str, Any], dw: Dict[str, Any]) -> bool:
    """RSI2 lane: Pine's pending open-only order is the plan. The judge may veto it (CASH_SKIP) but not re-level it."""
    try:
        state = int(float(dw.get("_rsi2_state_code") or 0))
    except (TypeError, ValueError):
        state = 0
    stop, t1 = _f(dw, "RSI2 Fixed Stop", "rsi2_fixed_stop"), _f(dw, "RSI2 Fixed Target", "rsi2_fixed_target")
    ceil = _f(dw, "RSI2 Entry Or Opening Ceiling", "rsi2_entry_ceiling")
    if state != 2 or not (stop and t1 and ceil) or not stop < ceil < t1:
        return False
    sp = watch_data.get("shares_plan") or {}
    watch_data["llm_levels"] = {k: sp.get(k) for k in ("entry_zone_low", "entry_zone_high", "tactical_stop", "target_1", "target_2")}
    watch_data["shares_plan"] = {
        "side": "LONG", "entry_type": "NEXT_OPEN",
        "entry_zone_low": round(stop + 0.01, 2), "entry_zone_high": round(ceil, 2), "entry_max": round(ceil, 2),
        "tactical_stop": round(stop, 2), "target_1": round(t1, 2), "target_2": 0.0,
        "breakout_level": 0.0, "breakout_stop": 0.0,
        "exit_rule": "sell next open after a close above EMA5, or after 10 bars",
    }
    inv = watch_data.get("invalidation")
    if isinstance(inv, dict):
        inv["price_level"] = round(stop, 2)
    watch_data["levels_source"] = "rsi2"
    return True
