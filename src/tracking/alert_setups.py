"""Per-setup scoreboard for TradingView email alerts (long side only).

Each real Daily LONG email is replayed with the frozen honest-replay contract
(data-windows/scripts/honest_entry_replay._walk_trade): first-occurrence only, next-open entry, gap skips,
21 bars, 5bps per side, planned risk = signal close - stop. Email alerts carry no Data Window levels, so the
geometry is the screener proxy those emails already quote: stop = 10-bar low, target = 60-bar high. R is
reported raw and relative to the same-day mean R of the same geometry over every symbol the alert DB has seen.
UNMEASURED until n >= 200.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import sqlite3
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

LONG_SETUPS = (
    "A+ Trend Long", "Early Action Long", "Coiled VCP", "Bullish Squeeze Release",
    "Early Ignition", "Research Reversal (Any Stage)",
)
MIN_N = 200
STOP_LOOKBACK = 10
TARGET_LOOKBACK = 60
CACHE_TTL_S = 6 * 3600
RETRY_S = 600

_lock = threading.Lock()
_running = False
_last_try = 0.0
_error: Optional[str] = None


def _tracking_dir() -> Path:
    d = config.BASE_DIR / "data" / "tracking"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _replay():
    here = Path(config.BASE_DIR).resolve()
    cands = [os.environ.get("HONEST_REPLAY_DIR", "")] + [str(p / "data-windows" / "scripts") for p in (here, *here.parents)]
    for c in cands:
        if c and (Path(c) / "honest_entry_replay.py").exists():
            if c not in sys.path:
                sys.path.insert(0, c)
            import honest_entry_replay
            return honest_entry_replay
    raise RuntimeError("honest_entry_replay.py not found; set HONEST_REPLAY_DIR")


def load_alerts(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Real Daily LONG emails as {(setup, symbol): sorted dates}, plus every symbol the table has seen."""
    path = db_path or config.alerts_db_path()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as conn:
        rows = conn.execute(
            "SELECT setup, symbol, date, raw_payload FROM alerts WHERE strategy = 'Daily' AND message_id NOT LIKE 'synth-%'"
        ).fetchall()
        universe = {r[0].upper() for r in conn.execute("SELECT DISTINCT symbol FROM alerts")}
    events: Dict[tuple, set] = {}
    for setup, sym, date, payload in rows:
        if setup not in LONG_SETUPS:
            continue
        try:
            if json.loads(payload).get("side") != "LONG":
                continue
        except (TypeError, ValueError):
            continue
        events.setdefault((setup, sym.upper()), set()).add(date)
    return {"events": {k: sorted(v) for k, v in events.items()}, "universe": sorted(universe)}


def fetch_bars(symbols: List[str], start: str) -> Dict[str, Any]:
    """One bounded batch yfinance download of daily OHLC, cached per closed session."""
    from src.tracking.execution_validator import _get_last_closed_session_date

    last_closed = _get_last_closed_session_date()
    cache = _tracking_dir() / "alert_bars_cache.pkl"
    if cache.exists():
        try:
            c = pickle.loads(cache.read_bytes())
            if c["last_closed"] == last_closed and c["start"] <= start and set(symbols) <= set(c["bars"]):
                return c["bars"]
        except Exception:
            pass
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    raw = yf.download(symbols, start=start, end=(datetime.fromisoformat(last_closed) + timedelta(days=1)).strftime("%Y-%m-%d"),
                      group_by="ticker", auto_adjust=False, progress=False, threads=True, timeout=60)
    bars: Dict[str, Any] = {}
    for s in symbols:
        try:
            d = raw[s].dropna(subset=["Open", "High", "Low", "Close"])
        except KeyError:
            continue
        if len(d):
            d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].copy()
            d["date"] = [str(i)[:10] for i in d.index]
            bars[s] = d.reset_index(drop=True)
    cache.write_bytes(pickle.dumps({"last_closed": last_closed, "start": start, "bars": bars}))
    return bars


def _geometry(d):
    return d["low"].rolling(STOP_LOOKBACK).min().to_numpy(), d["high"].rolling(TARGET_LOOKBACK).max().to_numpy()


def score(alerts: Dict[str, Any], bars: Dict[str, Any]) -> List[Dict[str, Any]]:
    import numpy as np

    rp = _replay()
    geo, pos = {}, {}
    for s, d in bars.items():
        d = d.sort_values("date").reset_index(drop=True)
        geo[s] = (*(d[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")), *_geometry(d), len(d))
        pos[s] = {dt: i for i, dt in enumerate(d["date"])}

    def walk(s, i):
        o, h, l, c, stop, tgt, n = geo[s]
        if i >= n - rp.HORIZON - 1:
            return None, "PENDING"
        r, skip, _ = rp._walk_trade(o, h, l, c, stop, tgt, i, min_price=rp.MIN_PRICE)
        return (None, "SKIPPED") if skip else (r, "SCORED")

    out: List[Dict[str, Any]] = []
    for (setup, sym), dates in alerts["events"].items():
        if sym not in geo:
            continue
        prev_i = None
        for dt in dates:
            i = pos[sym].get(dt)
            if i is None:
                continue
            first = prev_i is None or i - prev_i > 1
            prev_i = i
            if first:
                r, status = walk(sym, i)
                out.append({"setup": setup, "tk": sym, "date": dt, "i": i, "R": r, "status": status})

    need = {r["date"] for r in out if r["status"] == "SCORED"}
    acc: Dict[str, List[float]] = {}
    for s in geo:
        for dt in need:
            i = pos[s].get(dt)
            if i is not None:
                r, st = walk(s, i)
                if st == "SCORED":
                    acc.setdefault(dt, []).append(r)
    tape = {d: float(np.mean(v)) for d, v in acc.items()}
    for r in out:
        r["R_rel"] = r["R"] - tape[r["date"]] if r["status"] == "SCORED" and r["date"] in tape else None
    return out


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for setup in LONG_SETUPS:
        mine = [r for r in rows if r["setup"] == setup]
        done = [r for r in mine if r["status"] == "SCORED"]
        rel = [r["R_rel"] for r in done if r["R_rel"] is not None]
        n = len(done)
        per: Dict[str, List[float]] = {}
        for r in done:
            if r["R_rel"] is not None:
                per.setdefault(r["tk"], []).append(r["R_rel"])
        e: Dict[str, Any] = {
            "n": n, "pending": sum(r["status"] == "PENDING" for r in mine), "skipped": sum(r["status"] == "SKIPPED" for r in mine),
            "signals": len(mine), "status": "MEASURED" if n >= MIN_N else "UNMEASURED",
        }
        if n:
            rs = [r["R"] for r in done]
            e.update({
                "mean_r": round(statistics.mean(rs), 3), "median_r": round(statistics.median(rs), 3),
                "win_pct": round(100 * sum(x > 0 for x in rs) / n, 1),
                "mean_r_rel": round(statistics.mean(rel), 3) if rel else None,
                "tickers": len(per),
                "breadth_pct": round(100 * sum(statistics.mean(v) > 0 for v in per.values()) / len(per), 1) if per else None,
            })
        out[setup] = e
    return out


def build_scoreboard(db_path: Optional[Path] = None, bars_fn: Callable[[List[str], str], Dict[str, Any]] = fetch_bars) -> Dict[str, Any]:
    alerts = load_alerts(db_path)
    dates = [d for v in alerts["events"].values() for d in v]
    if not dates:
        return {"setups": summarize([]), "as_of": None, "note": "no long email alerts"}
    start = (datetime.fromisoformat(min(dates)) - timedelta(days=110)).strftime("%Y-%m-%d")
    syms = sorted(set(alerts["universe"]) | {s for _, s in alerts["events"]})
    rows = score(alerts, bars_fn(syms, start))
    return {
        "setups": summarize(rows),
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "note": ("Real Daily LONG emails, first occurrence, honest replay (next open, 21 bars, 5bps/side) on the screener proxy "
                 "geometry (stop 10-bar low, target 60-bar high); win = net R > 0; breadth = tickers with mean R vs tape > 0. "
                 f"UNMEASURED until n>={MIN_N}. Information events, not signals."),
    }


def _cache_path() -> Path:
    return _tracking_dir() / "alert_setups.json"


def _refresh() -> None:
    global _running, _error
    try:
        _cache_path().write_text(json.dumps(build_scoreboard()), encoding="utf-8")
        _error = None
    except Exception as e:
        logger.warning("alert setup scoreboard failed: %s", e)
        _error = str(e)
    finally:
        with _lock:
            _running = False


def summary() -> Dict[str, Any]:
    """Cached scoreboard; a stale or missing cache starts one background refresh and never blocks the caller."""
    global _running, _last_try
    p = _cache_path()
    cached = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    if cached is None or time.time() - p.stat().st_mtime > CACHE_TTL_S:
        with _lock:
            start = not _running and time.time() - _last_try > RETRY_S
            if start:
                _running, _last_try = True, time.time()
        if start:
            threading.Thread(target=_refresh, daemon=True).start()
    if cached:
        return cached
    return {"computing": not _error, "error": _error}
