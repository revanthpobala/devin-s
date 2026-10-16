"""One display state per plan, from measured facts only: ACTIONABLE / ARMED / NO TRADE.

Replaces the LLM verdict word and the n/10 conviction on cards. Numbers come from AGENTS.md:
- in zone & at-market R:R bands (full_v4_av2, path-accurate 21 bars): 2-3 +0.08R, >=3 +0.14R, win ~31-35%.
- limit fill odds: P(low touches close - k*ATR within 10 bars), 532 names: 0.5 77%, 1 58%, 1.5 43%, 2 31%.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

RR_MIN = 2.0
MAX_ARM_ATR = 2.0
_FILL_10D = ((0.0, 100.0), (0.5, 77.0), (1.0, 58.0), (1.5, 43.0), (2.0, 31.0))


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def fill_odds_10d(atr_below: float) -> int:
    for (k0, p0), (k1, p1) in zip(_FILL_10D, _FILL_10D[1:]):
        if atr_below <= k1:
            return round(p0 + (p1 - p0) * (atr_below - k0) / (k1 - k0))
    return round(_FILL_10D[-1][1])


def _band(rr: float) -> str:
    return "hist +0.14R, ~31% win" if rr >= 3 else "hist +0.08R, ~35% win"


def trade_state(price: Any, zone_low: Any, zone_high: Any, stop: Any, target: Any, atr: Any = None,
                side: str = "LONG", gate_rejected: bool = False, reasons: Optional[Iterable[str]] = None,
                fade: Any = None) -> Dict[str, Any]:
    px, lo, hi, st, t1, a = (_f(x) for x in (price, zone_low, zone_high, stop, target, atr))
    out = lambda state, label, detail="": {"state": state, "label": label, "detail": detail}
    if str(side or "LONG").upper() == "SHORT":
        return out("NO_TRADE", "NO TRADE · levels only", "short side has no measured edge")
    if gate_rejected:
        first = next(iter(reasons or []), "") or "rejected by the level gate"
        return out("NO_TRADE", "NO TRADE · gate", str(first)[:160])
    if not (px and st and t1):
        return out("NO_TRADE", "NO TRADE · no plan", "missing price, stop or target")
    if px <= st:
        return out("NO_TRADE", "NO TRADE · below stop", f"price {px:.2f} at or under stop {st:.2f}")
    if px >= t1:
        return out("NO_TRADE", "NO TRADE · at target", f"price {px:.2f} at or above target {t1:.2f}")
    rr = (t1 - px) / (px - st)
    in_zone = bool(lo and hi and lo <= px <= hi)
    faded = fade is not None and str(fade) not in ("0", "0.0", "False", "")
    if in_zone and rr >= RR_MIN and not faded:
        return out("ACTIONABLE", f"ACTIONABLE · R:R {rr:.1f}", f"in zone, R:R {rr:.2f} at market ({_band(rr)})")
    limit = (t1 + RR_MIN * st) / (1 + RR_MIN)
    if hi and rr >= RR_MIN:
        limit = min(limit, hi)
    if not a or limit <= st:
        return out("NO_TRADE", f"NO TRADE · R:R {rr:.1f}", f"R:R {rr:.2f} at market; no usable limit")
    k = max(0.0, (px - limit) / a)
    odds = fill_odds_10d(k)
    if k > MAX_ARM_ATR:
        return out("NO_TRADE", f"NO TRADE · R:R {rr:.1f}",
                   f"R:R {rr:.2f} at market; R:R {RR_MIN:g} needs {limit:.2f}, {k:.1f} ATR below (~{odds}% fill in 10d)")
    why = "fade active" if faded else ("out of zone" if rr >= RR_MIN else f"R:R {rr:.2f} at market")
    return out("ARMED", f"ARMED {limit:.2f} · {odds}% fill",
               f"{why}; limit {limit:.2f} is {k:.1f} ATR below, ~{odds}% fill odds in 10 days, R:R >= {RR_MIN:g} there")
