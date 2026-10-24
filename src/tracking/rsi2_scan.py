"""Universe RSI2 pullback scan on the nightly sweep bars; writes measured-lane suggestions (source='rsi2_scan').

Mirrors the Pine calcRsi2Engine: Wilder RSI(2) < 10 and close > EMA200, rising edge, close >= $20. Stop close - 2 ATR14,
target close + 4 ATR14, next-open entry only at or below the opening ceiling, exit next open after a close above EMA5,
10-bar max hold. suggestion_scorer runs that lifecycle for setup_lane RSI2, so these rows feed the forward record.

In-sample (Pine repo rsi2_context_study.py: 20,959 trades / 511 tickers, 5bps/side): 62.3% win, +0.050R. By QQQ
distance below its 252-day closing high: 0-3% +0.018R, 3-8% +0.075R (64.0% win), 8-15% +0.232R (67.4% win, both
halves positive), 15%+ -0.015R. ENTER only inside the 3-15% band; STALK elsewhere so the scorer keeps measuring it.
Levels are fixed by construction, so the level gate is bypassed.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCE = "rsi2_scan"
MIN_BARS = 260
MIN_CLOSE = 20.0
RSI_MAX = 10.0
STOP_ATR, TARGET_ATR = 2.0, 4.0
MAX_RISK_PCT = 0.05
ENTER_BAND = (3.0, 15.0)
BAND_PRIORS = {(3.0, 8.0): (64.0, 0.075), (8.0, 15.0): (67.4, 0.232)}
ALL_PRIOR = (62.3, 0.050)
HIGH_VOL_ATR_PCT = 2.601844534208747
ETFS = ("SPY", "QQQ", "IWM")


def _rma(xs: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    if len(xs) < n:
        return out
    v = sum(xs[:n]) / n
    out[n - 1] = v
    for i in range(n, len(xs)):
        v = (v * (n - 1) + xs[i]) / n
        out[i] = v
    return out


def _ema(xs: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    if len(xs) < n:
        return out
    v, a = sum(xs[:n]) / n, 2.0 / (n + 1)
    out[n - 1] = v
    for i in range(n, len(xs)):
        v = a * xs[i] + (1 - a) * v
        out[i] = v
    return out


def signal(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fresh RSI2 setup on the last bar, else None."""
    if len(bars) < MIN_BARS:
        return None
    c = [float(b["close"]) for b in bars]
    h = [float(b["high"]) for b in bars]
    lo = [float(b["low"]) for b in bars]
    d = [c[i] - c[i - 1] for i in range(1, len(c))]
    up, dn = _rma([max(x, 0.0) for x in d], 2), _rma([max(-x, 0.0) for x in d], 2)
    rsi = [None] + [None if u is None else (100.0 if v == 0 else 100 - 100 / (1 + u / v)) for u, v in zip(up, dn)]
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]
    atr, ema = _rma(tr, 14)[-1], _ema(c, 200)

    def setup(i: int) -> bool:
        return rsi[i] is not None and ema[i] is not None and rsi[i] < RSI_MAX and c[i] > ema[i]

    if not setup(-1) or setup(-2) or c[-1] < MIN_CLOSE or not atr:
        return None
    stop, target = c[-1] - STOP_ATR * atr, c[-1] + TARGET_ATR * atr
    return {"date": bars[-1]["date"], "close": c[-1], "rsi2": rsi[-1], "atr": atr, "atr_pct": 100 * atr / c[-1],
            "stop": stop, "target": target, "ceiling": min(c[-1], stop / (1 - MAX_RISK_PCT))}


def qqq_off_high(bars: List[Dict[str, Any]]) -> Optional[float]:
    c = [float(b["close"]) for b in bars[-252:]]
    return 100 * (1 - c[-1] / max(c)) if len(c) >= 50 else None


def _open_names(since: str, before: str) -> set:
    from src import config

    try:
        conn = sqlite3.connect(f"file:{config.research_watch_db_path()}?mode=ro", uri=True, timeout=5)
        rows = conn.execute("SELECT ticker FROM suggestions WHERE source = ? AND date >= ? AND date < ? AND scored_at IS NULL",
                            (SOURCE, since, before)).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception as e:
        logger.warning("rsi2_scan open-trade check failed: %s", e)
        return set()


def run(bars: Dict[str, List[Dict[str, Any]]], write: bool = True) -> Dict[str, Any]:
    off = qqq_off_high(bars.get("QQQ") or [])
    enter = off is not None and ENTER_BAND[0] < off <= ENTER_BAND[1]
    win, ev = next((p for (lo, hi), p in BAND_PRIORS.items() if off is not None and lo < off <= hi), ALL_PRIOR)
    off_txt = "n/a" if off is None else f"{off:.1f}%"
    hits = [{"ticker": t, **s} for t, b in sorted(bars.items()) if t not in ETFS and (s := signal(b))]
    written: List[str] = []
    if write and hits:
        from src.tracking.suggestions_ledger import append_suggestion

        day = hits[0]["date"]
        busy = _open_names((date.fromisoformat(day) - timedelta(days=20)).isoformat(), day)
        for s in hits:
            if s["ticker"] in busy:
                s["skipped"] = "open RSI2 trade"
                continue
            vol = "high" if s["atr_pct"] > HIGH_VOL_ATR_PCT else "normal"
            append_suggestion({
                "ticker": s["ticker"], "date": s["date"], "source": SOURCE, "side": "LONG", "entry_type": "NEXT_OPEN",
                "entry_low": s["ceiling"], "entry_high": s["ceiling"], "stop": s["stop"], "target_1": s["target"],
                "planned_rr": TARGET_ATR / STOP_ATR, "atr_at_signal": s["atr"], "spot_at_signal": s["close"],
                "stop_width_atr": STOP_ATR, "verdict": "ENTER" if enter else "STALK", "gate_status": "PASS",
                "gate_reasons": "measured lane: fixed RSI2 geometry, level gate bypassed", "setup_lane": "RSI2",
                "kind": "NEW", "lane_prior_win": win, "lane_prior_ev": ev,
                "notes": (f"RSI2 {s['rsi2']:.1f}; QQQ {off_txt} below 252d high; ATR {s['atr_pct']:.2f}% ({vol} vol); "
                          f"buy next open only at or below {s['ceiling']:.2f}; risk/share {s['close'] - s['stop']:.2f}; "
                          "exit next open after close > EMA5, or 10 bars"),
            })
            written.append(s["ticker"])
    return {"qqq_off_high_pct": None if off is None else round(off, 2), "verdict": "ENTER" if enter else "STALK",
            "signals": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()} for s in hits],
            "written": written}
