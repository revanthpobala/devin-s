"""Zone ledger: every long entry zone any dossier named recently, merged and watched live.

A new deep research replaces the stored plan, so the zone it named last week stops being watched even
when price comes back to it and rebounds (AMZN: 245.00-248.53 on 09-29, 250.00-253.00 on 10-08; price
retested the first zone three times and ran to 260 while only the second was armed). The ledger keeps
every zone from the last LOOKBACK_DAYS of plans, independent of thesis status, expiry or target hits.

Events (information only; none of these is a buy signal):
    ZONE_APPROACH  price within APPROACH_PCT above a zone's top
    ZONE_TOUCH     price inside the zone, or within TOUCH_TOL above it, and above the zone's stop
    ZONE_TOUCH_LOW_RR  same touch when R:R at market is under the floor or the stop is under MIN_RISK_PCT away
    ZONE_REBOUND   price at least REBOUND_PCT above the zone's top after a touch in the last REBOUND_DAYS

The distances are policy values, not measured edges. Measured on full_v4_av2 (near_zone_lane.py): entries
0-1% above the zone with at-market R:R >= 2 are no worse than in-zone ones (+0.09R vs +0.06R net), so every
touch message states the R:R at market against the floor; that ratio, not the touch, is what decides.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src import config
from src.tracking import rr_config

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 30
MERGE_PCT = 0.5
APPROACH_PCT = 1.0
TOUCH_TOL_PCT = 0.3
TOUCH_BELOW_PCT = 1.0
REBOUND_PCT = 1.0
REBOUND_DAYS = 10
MIN_RISK_PCT = 0.5


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def state_path() -> Path:
    return config.BASE_DIR / "data" / "zone_state.json"


def load_plans(today: str, reports_root: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Long zone plans per ticker from reports/<date>/<T>_watch_levels.json in the lookback window."""
    root = reports_root or (config.BASE_DIR / "reports")
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name) and cutoff <= d.name <= today):
            continue
        for fp in d.glob("*_watch_levels.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            sp = data.get("shares_plan") or {}
            if str(data.get("side") or sp.get("side") or "LONG").upper() != "LONG":
                continue
            lo, hi = _f(sp.get("entry_zone_low")), _f(sp.get("entry_zone_high"))
            if lo <= 0 or hi < lo:
                continue
            tk = str(data.get("ticker") or fp.name.replace("_watch_levels.json", "")).upper()
            out.setdefault(tk, []).append({
                "date": d.name, "low": lo, "high": hi,
                "stop": _f(sp.get("tactical_stop")), "t1": _f(sp.get("target_1")),
            })
    return out


def merge_zones(plans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Overlapping or near-adjacent zones become one; the newest plan supplies stop and T1."""
    zones: List[Dict[str, Any]] = []
    for p in sorted(plans, key=lambda x: (x["low"], x["high"])):
        z = zones[-1] if zones else None
        if z and p["low"] <= z["high"] * (1 + MERGE_PCT / 100.0):
            z["high"] = max(z["high"], p["high"])
            z["names"].append(p["date"])
            if p["date"] >= z["newest"]:
                z["newest"], z["stop"], z["t1"] = p["date"], p["stop"], p["t1"]
            elif p["stop"] > 0:
                z["stop"] = min(z["stop"], p["stop"]) if z["stop"] > 0 else p["stop"]
            continue
        zones.append({"low": p["low"], "high": p["high"], "names": [p["date"]], "newest": p["date"],
                      "stop": p["stop"], "t1": p["t1"]})
    return zones


def _zone_stop(z: Dict[str, Any]) -> float:
    return z["stop"] if 0 < z["stop"] < z["low"] else z["low"] * 0.97


def _in_band(p: float, z: Dict[str, Any]) -> bool:
    return z["low"] * (1 - TOUCH_BELOW_PCT / 100.0) <= p <= z["high"] * (1 + TOUCH_TOL_PCT / 100.0)


def _touch_count(touches: List[Dict[str, Any]], z: Dict[str, Any]) -> int:
    return len({t["d"] for t in touches if _in_band(t["p"], z)})


def _rr(price: float, stop: float, t1: float) -> Optional[float]:
    if stop > 0 and t1 > price > stop:
        return round((t1 - price) / (price - stop), 2)
    return None


def evaluate(ticker: str, price: float, today: str, zones: List[Dict[str, Any]],
             st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Events for one ticker at one price. Mutates `st` (the ticker's persisted state)."""
    touches: List[Dict[str, Any]] = st.setdefault("touches", [])
    rebounded: Dict[str, str] = st.setdefault("rebounded", {})
    events: List[Dict[str, Any]] = []
    newest_zone = max(zones, key=lambda z: z["newest"]) if zones else None

    for z in sorted(zones, key=lambda z: abs(price - z["high"])):
        stop = _zone_stop(z)
        band = f"${z['low']:.2f}-${z['high']:.2f}"
        named = f"named {', '.join(sorted(set(z['names'])))}"
        newer = ""
        if newest_zone is not None and newest_zone is not z:
            newer = f" Newer plan zone ${newest_zone['low']:.2f}-${newest_zone['high']:.2f} ({newest_zone['newest']})."
        rr = _rr(price, stop, z["t1"])
        rr_txt = (f"R:R at market {rr:.2f} to T1 ${z['t1']:.2f} (floor {rr_config.min_rr():.1f}: "
                  f"{'meets' if rr >= rr_config.min_rr() else 'below'})") if rr is not None else "R:R at market n/a"
        top_tol = z["high"] * (1 + TOUCH_TOL_PCT / 100.0)

        if price > stop and _in_band(price, z):
            if not any(t["d"] == today and _in_band(t["p"], z) for t in touches):
                touches.append({"d": today, "p": round(price, 4)})
            n = _touch_count(touches, z)
            risk_pct = 100 * (price - stop) / price
            ok = rr is not None and rr >= rr_config.min_rr() and risk_pct >= MIN_RISK_PCT
            why = ("Actionable only if you want it: R:R meets the floor." if ok else
                   f"No room: stop only {risk_pct:.1f}% away, R:R not meaningful." if risk_pct < MIN_RISK_PCT else
                   "Not actionable: R:R at market is below the floor.")
            events.append({"type": "ZONE_TOUCH" if ok else "ZONE_TOUCH_LOW_RR", "price": price, "n": n, "message": (
                f"[{ticker}] Touch #{n} of tracked zone {band} ({named}) at ${price:.2f}; stop ${stop:.2f} ({risk_pct:.1f}% away). "
                f"{rr_txt}. {why} UNMEASURED: near-zone entries measured no worse than in-zone, the R:R decides.{newer}")})
        elif top_tol < price <= z["high"] * (1 + APPROACH_PCT / 100.0):
            rr_top = _rr(z["high"], stop, z["t1"])
            if rr_top is None or rr_top < rr_config.min_rr() or 100 * (z["high"] - stop) / z["high"] < MIN_RISK_PCT:
                continue
            events.append({"type": "ZONE_APPROACH", "price": price, "n": 0, "message": (
                f"[{ticker}] ${price:.2f} is {100 * (price / z['high'] - 1):.1f}% above tracked zone {band} ({named}). "
                f"If it fills at the zone top ${z['high']:.2f}: R:R {rr_top:.2f} to T1 ${z['t1']:.2f}, stop ${stop:.2f} (floor {rr_config.min_rr():.1f}: meets). "
                f"At market now: {rr_txt}.{newer}")})
        elif price >= z["high"] * (1 + REBOUND_PCT / 100.0):
            cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=REBOUND_DAYS)).strftime("%Y-%m-%d")
            hit = [t for t in touches if t["d"] >= cutoff and _in_band(t["p"], z)]
            if hit:
                last = max(hit, key=lambda t: t["d"])
                key = f"{z['low']:.2f}-{z['high']:.2f}"
                if rebounded.get(key) != last["d"]:
                    rebounded[key] = last["d"]
                    low_px = min(t["p"] for t in hit)
                    events.append({"type": "ZONE_REBOUND", "price": price, "n": _touch_count(touches, z), "message": (
                        f"[{ticker}] Rebounded off tracked zone {band} ({named}): ${low_px:.2f} on {last['d']} -> ${price:.2f} "
                        f"({100 * (price / low_px - 1):+.1f}%), {_touch_count(touches, z)} touch day(s). "
                        f"The zone entry was missed; do not chase, re-check R:R at market.{newer}")})
    keep_from = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    st["touches"] = [t for t in touches if t["d"] >= keep_from]
    best: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if ev["type"] not in best or ev["n"] > best[ev["type"]]["n"]:
            best[ev["type"]] = ev
    return list(best.values())


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    p = state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, p)


def run_cycle(prices: Dict[str, float], today: str, emit: Callable[[str, str, str, float], Any],
              fetch_missing: Optional[Callable[[List[str]], Dict[str, float]]] = None,
              reports_root: Optional[Path] = None) -> int:
    """Check every tracked zone against live prices. `emit(ticker, type, message, price)` files the alert."""
    plans = load_plans(today, reports_root)
    if not plans:
        return 0
    prices = dict(prices)
    missing = [t for t in plans if not prices.get(t)]
    if missing and fetch_missing:
        try:
            prices.update({k: v for k, v in fetch_missing(missing).items() if v and v > 0})
        except Exception as e:
            logger.debug(f"[zone_ledger] price fetch failed: {e}")
    state = _load_state()
    fired = 0
    for tk, pl in plans.items():
        px = prices.get(tk)
        if not px or px <= 0:
            continue
        evs = evaluate(tk, float(px), today, merge_zones(pl), state.setdefault(tk, {}))
        seen = set()
        for ev in evs:
            if ev["type"] in seen:
                continue
            seen.add(ev["type"])
            if emit(tk, ev["type"], ev["message"], ev["price"]):
                fired += 1
    _save_state(state)
    return fired
