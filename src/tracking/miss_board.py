"""Miss board: every researched name since its research bar, with what price did and why it was not taken.

Read-only, deterministic, no outcome claim: counts are descriptive until the scored ledger reaches n>=200.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.tracking.watch_manager import _get_connection


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _first_reason(raw: Any) -> str:
    try:
        r = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(r, list) and r:
            return str(r[0])[:140]
    except Exception:
        pass
    return str(raw or "")[:140]


def classify(row: Dict[str, Any], bars: List[Dict[str, Any]], spy_bars: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Walk daily bars after the research date: zone touch, then stop/target, stop first on a shared bar."""
    sig = str(row["date"])[:10]
    after = [b for b in bars if str(b["date"])[:10] > sig]
    spot = _num(row.get("spot_at_signal"))
    atr = _num(row.get("atr_at_signal"))
    lo_z, hi_z = _num(row.get("entry_low")), _num(row.get("entry_high"))
    stop, t1 = _num(row.get("stop")), _num(row.get("target_1"))
    out: Dict[str, Any] = {"ticker": row["ticker"], "date": sig, "verdict": row.get("verdict"),
                           "gate": row.get("gate_status"), "spot": spot, "zone": [lo_z, hi_z], "stop": stop, "target_1": t1}
    if not after or not spot:
        out.update(outcome="PENDING", why="no bars after the research date")
        return out
    last = float(after[-1]["close"])
    hi = max(float(b["high"]) for b in after)
    lo = min(float(b["low"]) for b in after)
    sign = -1.0 if str(row.get("side") or "LONG").upper() == "SHORT" else 1.0
    pct = lambda px: round((px / spot - 1) * 100, 2)
    spy = [b for b in (spy_bars or []) if str(b["date"])[:10] >= sig]
    spy_move = None
    if len(spy) > 1 and len(spy) >= len(after):
        spy_move = round((float(spy[-1]["close"]) / float(spy[0]["close"]) - 1) * 100, 2)
    out.update(last=round(last, 2), move_pct=pct(last), high=round(hi, 2), low=round(lo, 2), bars=len(after),
               ret_1d=pct(float(after[0]["close"])), ret_5d=pct(float(after[min(4, len(after) - 1)]["close"])),
               mfe_pct=pct(hi) if sign > 0 else -pct(lo), mae_pct=pct(lo) if sign > 0 else -pct(hi),
               move_atr=round((last - spot) / atr, 1) if atr else None,
               with_thesis=(last - spot) * sign > 0, spy_move_pct=spy_move,
               conditions={k: row.get(k) for k in ("ext_z", "fade", "action_long", "rr_at_market_at_signal", "pb_funnel",
                                                   "signal_pack", "setup_lane", "source") if row.get(k) is not None})
    reason = _first_reason(row.get("gate_reasons")) if row.get("gate_status") == "REJECTED_BY_GATE" else ""
    if not (hi_z and stop and t1):
        out.update(outcome="NO_PLAN", why=reason or "no complete zone, stop and target")
        return out
    state, fill = "UNFILLED", None
    for b in after:
        h, l = float(b["high"]), float(b["low"])
        if state == "UNFILLED":
            if l <= hi_z and h >= (lo_z or 0):
                state, fill = "OPEN", min(hi_z, max(lo_z or 0, float(b["open"])))
                if l <= stop:
                    state = "STOPPED"
                    break
                if h >= t1:
                    state = "TARGET"
                    break
        else:
            if l <= stop:
                state = "STOPPED"
                break
            if h >= t1:
                state = "TARGET"
                break
    out["fill"] = None if fill is None else round(fill, 2)
    if state == "UNFILLED":
        gap = (lo - hi_z) / atr if atr else None
        side = "ran up without touching the zone" if last > hi_z else "fell below the zone without a fill"
        out.update(outcome="MISSED_UP" if hi >= t1 else "NOT_TRIGGERED",
                   why=f"{side}: low {lo:.2f} vs zone top {hi_z:.2f}" + (f" ({gap:.1f} ATR above)" if gap and gap > 0 else ""))
        if reason:
            out["why"] += f"; gate: {reason}"
    else:
        out.update(outcome={"OPEN": "FILLED_OPEN", "STOPPED": "FILLED_STOPPED", "TARGET": "FILLED_TARGET"}[state],
                   why=reason or f"filled near {fill:.2f}")
    return out


def board(days: int = 21, bars_for: Optional[Callable[[str], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    custom_bars = bars_for
    if bars_for is None:
        from src.tracking.thesis_ledger import _default_bars as bars_for
    since = (date.today() - timedelta(days=days)).isoformat()
    with _get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM suggestions WHERE date >= ? AND (kind IS NULL OR kind = 'NEW') ORDER BY id", (since,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        k = (r["ticker"], str(r["date"])[:10])
        if k not in latest or (r.get("source") == "judge") >= (latest[k].get("source") == "judge"):
            latest[k] = r
    tickers = sorted({r["ticker"] for r in latest.values()} | {"SPY"})
    batch: Dict[str, List[Dict[str, Any]]] = {}
    if custom_bars is None:
        from src.tracking.bars_store import fetch_daily

        batch = fetch_daily(tickers, days + 10)

    def _bars(t: str) -> List[Dict[str, Any]]:
        if t in batch:
            return batch[t]
        try:
            return bars_for(t) or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        cache = dict(zip(tickers, ex.map(_bars, tickers)))
    spy = cache["SPY"]
    items: List[Dict[str, Any]] = [classify(r, cache[r["ticker"]], spy) for r in latest.values()]
    items.sort(key=lambda x: (x["date"], x["ticker"]), reverse=True)
    counts: Dict[str, int] = {}
    for i in items:
        counts[i["outcome"]] = counts.get(i["outcome"], 0) + 1
    done = [i for i in items if "with_thesis" in i]
    return {"days": days, "counts": counts, "items": items,
            "with_thesis_pct": round(100 * sum(i["with_thesis"] for i in done) / len(done), 1) if done else None,
            "note": "Descriptive only. Zone touch uses daily highs/lows; stop-first on a shared bar. UNMEASURED."}
