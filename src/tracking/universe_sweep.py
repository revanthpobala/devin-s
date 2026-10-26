"""Universe sweep: one batched bars pull for the whole screener universe, market and sector read, pullback candidates.

Candidates are queued for the TradingView scrape so the engine's zone and at-market R:R decide. The screen itself
(above 50d, 3%+ off the 20d high, within 1.5 ATR of the 10d low, proxy R:R >= 2) has no measured edge; it only picks
what to scrape first. The same bars feed rsi2_scan, which writes measured RSI2 suggestions. Runs standalone: python -m src.tracking.universe_sweep [--no-queue] [--loop MINUTES]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

MAX_QUEUE = 25
MIN_BARS = 210
PULLBACK_OFF_HIGH = 0.03
NEAR_LOW_ATR = 1.5
MIN_PROXY_RR = 2.0
_state: Dict[str, Any] = {"running": False}


def out_path():
    return config.BASE_DIR / "data" / "universe_sweep.json"


def metrics(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(bars) < MIN_BARS:
        return None
    c = [float(b["close"]) for b in bars]
    h = [float(b["high"]) for b in bars]
    lo = [float(b["low"]) for b in bars]
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(len(c) - 14, len(c))]
    atr = sum(trs) / 14
    if atr <= 0:
        return None
    sma = lambda n: sum(c[-n:]) / n
    last = c[-1]
    low10, high20, high60 = min(lo[-10:]), max(h[-20:]), max(h[-60:])
    rr = (high60 - last) / (last - low10) if last > low10 else 0.0
    return {"close": round(last, 2), "r1": last / c[-2] - 1, "r5": last / c[-6] - 1, "r21": last / c[-22] - 1,
            "r63": last / c[-64] - 1, "a20": last > sma(20), "a50": last > sma(50), "a200": last > sma(200),
            "new_high": last >= max(h[-252:-1]), "new_low": last <= min(lo[-252:-1]), "atr_pct": atr / last * 100,
            "atrs_above_low": (last - low10) / atr, "off_high20": 1 - last / high20, "proxy_rr": rr,
            "pullback": last > sma(50) and (last - low10) / atr <= NEAR_LOW_ATR
            and last <= high20 * (1 - PULLBACK_OFF_HIGH) and rr >= MIN_PROXY_RR}


def completed(bars: Dict[str, List[Dict[str, Any]]], now_et: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Drops today's still-forming bar when the sweep runs during the session (catch-up after the machine was off)."""
    now_et = now_et or datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5 or (now_et.hour, now_et.minute) >= (16, 10):
        return bars
    today = now_et.strftime("%Y-%m-%d")
    return {t: [b for b in v if str(b["date"])[:10] != today] for t, v in bars.items()}


def _sectors(tickers: List[str]) -> Dict[str, str]:
    try:
        from src.screener.universe import _profiles, _yf

        prof = _profiles()
        return {t: (prof.get(_yf(t)) or [0, {}])[1].get("sector") or "Other" for t in tickers}
    except Exception:
        return {t: "Other" for t in tickers}


def summarize(rows: Dict[str, Dict[str, Any]], sector_of: Dict[str, str]) -> Dict[str, Any]:
    n = len(rows)
    pct = lambda k: round(100 * sum(1 for r in rows.values() if r[k]) / n, 1) if n else None
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    sectors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t, r in rows.items():
        sectors[sector_of.get(t, "Other")].append(r)
    sec = {s: {"n": len(v), "r5_pct": round(100 * med([x["r5"] for x in v]), 1),
               "r21_pct": round(100 * med([x["r21"] for x in v]), 1), "r63_pct": round(100 * med([x["r63"] for x in v]), 1),
               "above_50d_pct": round(100 * sum(x["a50"] for x in v) / len(v), 1),
               "above_200d_pct": round(100 * sum(x["a200"] for x in v) / len(v), 1),
               "pullbacks": sum(x["pullback"] for x in v)} for s, v in sectors.items() if len(v) >= 5}
    return {"names": n, "above_20d_pct": pct("a20"), "above_50d_pct": pct("a50"), "above_200d_pct": pct("a200"),
            "new_highs": sum(1 for r in rows.values() if r["new_high"]), "new_lows": sum(1 for r in rows.values() if r["new_low"]),
            "advancing_pct": round(100 * sum(1 for r in rows.values() if r["r1"] > 0) / n, 1) if n else None,
            "median_r21_pct": round(100 * med([r["r21"] for r in rows.values()]), 1) if n else None,
            "sectors": dict(sorted(sec.items(), key=lambda kv: -kv[1]["r21_pct"]))}


def run(queue: bool = True, bars_fetch=None, tickers: Optional[List[str]] = None, held: Optional[set] = None) -> Dict[str, Any]:
    if bars_fetch is None:
        from src.tracking.bars_store import fetch_daily as bars_fetch
    if tickers is None:
        from src.screener.universe import load_constituents

        tickers = [t.replace("/", ".") for t in load_constituents()]
    started = time.time()
    names = sorted(set(tickers) | {"SPY", "QQQ", "IWM"})
    bars = completed(bars_fetch(names, 760))
    try:
        from src.tracking import rsi2_scan

        rsi2 = rsi2_scan.run(bars, write=queue)
    except Exception as e:
        logger.warning("rsi2 scan failed: %s", e)
        rsi2 = {"error": str(e)}
    try:
        from src.tracking import ticket_paper

        extra = sorted(set(ticket_paper.pending_tickers()) - set(bars))
        paper = ticket_paper.score_pending({**bars, **(completed(bars_fetch(extra, 250)) if extra else {})})
    except Exception as e:
        logger.warning("paper scoring failed: %s", e)
        paper = {"error": str(e)}
    rows = {t: m for t in names if t in bars and (m := metrics(bars[t])) is not None}
    etfs = {t: rows.pop(t) for t in ("SPY", "QQQ", "IWM") if t in rows}
    if held is None:
        try:
            from src.tracking.catalyst_ledger import held_book

            held = set(held_book()["equity"])
        except Exception:
            held = set()
    cands = sorted((t for t, r in rows.items() if r["pullback"]),
                   key=lambda t: (t not in held, -rows[t]["proxy_rr"]))
    queued: List[str] = []
    if queue:
        from src.tracking.alert_db import queue_for_research

        for t in cands[:MAX_QUEUE]:
            try:
                if queue_for_research(t, source="universe_sweep", setup="Pullback to support",
                                      reason=f"sweep: proxy R:R {rows[t]['proxy_rr']:.1f}, {rows[t]['atrs_above_low']:.1f} ATR above 10d low"):
                    queued.append(t)
            except Exception as e:
                logger.warning("sweep queue %s failed: %s", t, e)
    res = {"as_of": datetime.now().isoformat(timespec="seconds"), "universe": len(tickers), "with_bars": len(rows),
           "seconds": round(time.time() - started, 1),
           "etfs": {t: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                        if k in ("close", "r1", "r5", "r21", "a50", "a200")} for t, r in etfs.items()},
           "market": summarize(rows, _sectors(list(rows))),
           "candidates": [{"ticker": t, "proxy_rr": round(rows[t]["proxy_rr"], 2), "atrs_above_low": round(rows[t]["atrs_above_low"], 2),
                           "off_high_pct": round(100 * rows[t]["off_high20"], 1), "held": t in held} for t in cands],
           "queued": queued,
           "rsi2": rsi2,
           "paper": paper,
           "note": "Screen only picks scrape order; no measured edge. The engine zone and at-market R:R decide."}
    out_path().write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def start_background(queue: bool = True) -> bool:
    """Run once in a thread (UI trigger). False when already running."""
    import threading

    if _state["running"]:
        return False
    _state["running"] = True

    def _work():
        try:
            run(queue)
        except Exception as e:
            logger.error("universe sweep failed: %s", e)
        finally:
            _state["running"] = False

    threading.Thread(target=_work, daemon=True).start()
    return True


def latest() -> Dict[str, Any]:
    try:
        return {**json.loads(out_path().read_text(encoding="utf-8")), "running": _state["running"]}
    except Exception:
        return {"running": _state["running"], "as_of": None}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-queue", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="repeat every N minutes")
    a = ap.parse_args()
    from dotenv import load_dotenv

    load_dotenv()
    while True:
        r = run(not a.no_queue)
        print(f"{r['with_bars']}/{r['universe']} names, {len(r['candidates'])} pullbacks, queued {r['queued']}, {r['seconds']}s")
        if not a.loop:
            break
        time.sleep(a.loop * 60)
