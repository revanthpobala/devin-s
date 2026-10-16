"""Repair a judge long plan to the rulebook before it is gated, so a good idea at a bad price becomes a resting limit instead of a rejection."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from src.tracking import trade_rules

MAX_LIMIT_DISTANCE_ATR = 1.5


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def repair_long_shares_plan(
    sp: Dict[str, Any], close: float, atr: float, pine_stop: bool = False, rr: Optional[float] = None
) -> List[str]:
    """Mutates sp in place. Returns notes; sets sp['repair_rejected'] when no fillable limit exists."""
    notes: List[str] = []
    stop, t1 = _f(sp.get("tactical_stop")), _f(sp.get("target_1"))
    if not (close > 0 and atr > 0 and t1 > close and stop > 0):
        return notes
    lo, hi = _f(sp.get("entry_zone_low")), _f(sp.get("entry_zone_high"))
    fill = hi if hi > 0 else close

    if not pine_stop:
        cap = trade_rules.max_allowed_stop(fill, atr)
        if stop > cap:
            sp["tactical_stop"] = stop = round(cap, 2)
            notes.append(f"stop widened to {trade_rules.STOP_ATR_MIN:g} ATR below the fill: {stop}")

    entry_max = trade_rules.entry_max_for_rr(t1, stop, rr)
    sp["entry_max"] = round(entry_max, 2)
    if entry_max <= stop:
        sp["repair_rejected"] = "no entry above the stop reaches the R:R floor"
        return notes + [sp["repair_rejected"]]

    if fill > entry_max:
        if (close - entry_max) / atr > MAX_LIMIT_DISTANCE_ATR:
            sp["repair_rejected"] = f"R:R floor needs a limit at {entry_max:.2f}, {(close - entry_max) / atr:.1f} ATR below spot; unlikely to fill"
            return notes + [sp["repair_rejected"]]
        sp["entry_zone_high"] = round(entry_max, 2)
        sp["entry_zone_low"] = round(max(stop + 0.01, min(lo, entry_max) if lo > 0 else entry_max - 0.25 * atr), 2)
        sp["entry_type"] = "LIMIT"
        sp["limit_price"] = sp["trigger_price"] = sp["entry_zone_high"]
        sp["wait_for"] = f"resting limit {entry_max:.2f} (R:R floor)"
        notes.append(f"converted to resting limit at {entry_max:.2f}")
    return notes


def options_span_earnings_ok(options_plan: Dict[str, Any], report_date: Optional[str], stance: Optional[str]) -> Optional[str]:
    """None when fine; else the reason the option leg must be dropped."""
    exp = str((options_plan or {}).get("expiration") or "")
    if not exp or not report_date or not (options_plan or {}).get("actionable"):
        return None
    try:
        spans = date.fromisoformat(exp) >= date.fromisoformat(report_date)
    except ValueError:
        return None
    if spans and trade_rules.normalize_stance(stance) not in ("HOLD_THROUGH", "EARNINGS_PLAY"):
        return f"option expiry {exp} spans earnings {report_date} but stance is {stance or 'missing'}"
    return None
