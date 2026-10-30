"""Research scorecard: what did every research plan actually do? Measurement only; nothing trades on it.

One row per (ticker, date) research plan in `suggestions` (judge preferred over rule, LONG, kind NEW), gate
PASS or REJECTED alike. Daily bars after the research date, scored four ways:

  hold   buy at the research spot, close-to-close 5/10/21 bars later, minus SPY over the same bars
  market enter next open, plan stop / T1, 21-bar walk from the signal, stop first on a shared bar
  limit  one resting limit at the zone top, good for 10 sessions (order_ticket.TICKET_GOOD_TIL_SESSIONS)
  layer  src.logic.layered_entry tranches (starter when R:R at market >= 1.5; earnings unknown historically,
         so no earnings block), equal shares, R = P&L / risk-if-all-fill; unfilled tranches cancel on a
         close below the stop or when T1 prints

`run_bucket` = stock 10-bar return minus SPY's before the research date: RAN (>= +5%), FELL (<= -5%), MIDDLE.
(The Oct 28 audit used the Schwab-1000 percentile; this proxy needs no universe download.) Rows re-score each
night until 21 bars have passed (mature = 1). Run: python -m src.tracking.research_scorecard [--report]

`b1_n` = B1 composite count from the research run's own Pine datawindow (src.logic.b1_label); by_b1 splits
plans into B1 (>= 3), not_B1, no_datawindow. Label only: B1 passed the T20 holdout as a ranking rule, not a gate.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.tracking.watch_manager import _db_lock, _get_connection, _now_iso

logger = logging.getLogger(__name__)

HORIZON = 21
LIMIT_GOOD_BARS = 10
START_DATE = "2026-08-01"
RAN, FELL = 0.05, -0.05
MIN_READ = 30
B1_MIN = 3  # src.logic.b1_label.MIN_COUNT; frozen 2026-10-29

_COLS = (
    "suggestion_id INTEGER PRIMARY KEY, ticker TEXT, date TEXT, source TEXT, lane TEXT, gate TEXT, verdict TEXT,"
    " spot REAL, rel10_pre REAL, run_bucket TEXT, h5 REAL, h10 REAL, h21 REAL, s5 REAL, s10 REAL, s21 REAL,"
    " r_market REAL, r_limit REAL, r_layer REAL, limit_filled INTEGER, layer_fills INTEGER, layer_mode TEXT,"
    " bars INTEGER, mature INTEGER, scored_at TEXT, b1_n INTEGER"
)


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS research_scorecard ({_COLS})")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(research_scorecard)")}
    if "b1_n" not in cols:
        conn.execute("ALTER TABLE research_scorecard ADD COLUMN b1_n INTEGER")


def _bars(ticker: str, start: str) -> List[Dict[str, Any]]:
    from src.tracking.execution_validator import get_bars_since_date

    df = get_bars_since_date(ticker, start)
    if df is None or df.empty:
        return []
    lc = {c.lower(): c for c in df.columns}
    if not all(k in lc for k in ("open", "high", "low", "close")):
        return []
    return [{"date": str(idx)[:10], **{k: float(row[lc[k]]) for k in ("open", "high", "low", "close")}}
            for idx, row in df.iterrows()]


def _atr14(before: List[Dict[str, Any]]) -> float:
    if len(before) < 15:
        return 0.0
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
           for a, b in zip(before[:-1], before[1:])]
    atr = sum(trs[:14]) / 14
    for tr in trs[14:]:
        atr = (atr * 13 + tr) / 14
    return atr


def _walk(after, i0: int, fill: float, stop: float, t1: float) -> float:
    """R of one entry from bar i0 (the fill bar) to the end of the 21-bar window; stop first on a shared bar."""
    risk = fill - stop
    end = min(len(after), HORIZON)
    for x in after[i0:end]:
        if x["low"] <= stop:
            return -1.0
        if x["high"] >= t1:
            return (t1 - fill) / risk
    return (after[end - 1]["close"] - fill) / risk


def score_market(after, stop: float, t1: float) -> Optional[float]:
    if not after:
        return None
    o = after[0]["open"]
    return _walk(after, 0, o, stop, t1) if stop < o < t1 else None


def score_limit(after, zone_hi: float, stop: float, t1: float) -> Optional[float]:
    """None = never filled within LIMIT_GOOD_BARS (or the stop/T1 printed first)."""
    for i, x in enumerate(after[:min(LIMIT_GOOD_BARS, HORIZON)]):
        if x["low"] <= zone_hi:
            fill = min(zone_hi, x["open"])
            return _walk(after, i, fill, stop, t1) if fill > stop else None
        if x["high"] >= t1:
            return None
    return None


def score_layers(after, lay: Dict[str, Any]) -> Dict[str, Any]:
    """Equal-share tranches; R = total P&L / risk if every tranche filled."""
    stop, t1 = float(lay["stop"]), float(lay["target_1"])
    tr = lay["tranches"]
    full_risk = sum(t["shares"] * (t["price"] - stop) for t in tr)
    if full_risk <= 0 or not after:
        return {"r": None, "fills": 0}
    filled: List[tuple] = []
    exit_px = None
    end = min(len(after), HORIZON)
    for i, x in enumerate(after[:end]):
        for k, t in enumerate(tr):
            if any(f[0] == k for f in filled):
                continue
            if t["type"] == "MARKET":
                if i == 0:
                    cap = float(t.get("limit_cap") or t["price"])
                    if x["open"] <= cap and x["open"] > stop:
                        filled.append((k, x["open"], t["shares"]))
                    elif x["low"] <= cap < x["high"] and cap > stop:
                        filled.append((k, cap, t["shares"]))
            elif i < LIMIT_GOOD_BARS and x["low"] <= t["price"]:
                filled.append((k, min(t["price"], x["open"]), t["shares"]))
        if filled and x["low"] <= stop:
            exit_px = stop
            break
        if filled and x["high"] >= t1:
            exit_px = t1
            break
        if not filled and (x["close"] < stop or x["high"] >= t1):
            break  # cancel rule: nothing filled and the plan is over
    if not filled:
        return {"r": None, "fills": 0}
    if exit_px is None:
        exit_px = after[end - 1]["close"]
    pnl = sum(sh * (exit_px - px) for _, px, sh in filled)
    return {"r": pnl / full_risk, "fills": len(filled)}


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return (a / b - 1.0) if a and b else None


def score_row(r: Dict[str, Any], bars: List[Dict[str, Any]], spy: Dict[str, float], spy_dates: List[str]) -> Dict[str, Any]:
    from src.logic.layered_entry import build_layers

    d = str(r["date"])[:10]
    before = [b for b in bars if b["date"] <= d]
    after = [b for b in bars if b["date"] > d]
    close0 = before[-1]["close"] if before else 0.0
    spot = float(r.get("spot_at_signal") or 0) or close0
    if close0 and spot and abs(spot / close0 - 1) > 0.15:
        spot = close0  # bad stored spot (e.g. HAL 4725 vs a $36 close); trust the bar
    out: Dict[str, Any] = {"spot": spot or None, "bars": len(after), "mature": int(len(after) >= HORIZON)}

    # SPY on the signal date and on each horizon bar's date
    spy0_d = max((x for x in spy_dates if x <= d), default=None)
    spy0 = spy.get(spy0_d) if spy0_d else None
    for k in (5, 10, 21):
        if spot and len(after) >= k:
            out[f"h{k}"] = _pct(after[k - 1]["close"], spot)
            out[f"s{k}"] = _pct(spy.get(after[k - 1]["date"]), spy0)
        else:
            out[f"h{k}"] = out[f"s{k}"] = None

    # what the stock did vs SPY in the 10 bars BEFORE the research
    rel = None
    if len(before) >= 11 and spy0:
        d10 = before[-11]["date"]
        spy10 = spy.get(max((x for x in spy_dates if x <= d10), default=""), None)
        s_ret, m_ret = _pct(before[-1]["close"], before[-11]["close"]), _pct(spy0, spy10)
        if s_ret is not None and m_ret is not None:
            rel = s_ret - m_ret
    out["rel10_pre"] = rel
    out["run_bucket"] = None if rel is None else ("RAN" if rel >= RAN else ("FELL" if rel <= FELL else "MIDDLE"))

    zl, zh = float(r.get("entry_low") or 0), float(r.get("entry_high") or 0)
    stop, t1 = float(r.get("stop") or 0), float(r.get("target_1") or 0)
    out.update(r_market=None, r_limit=None, r_layer=None, limit_filled=None, layer_fills=None, layer_mode=None)
    if not (0 < stop < zl <= zh < t1) or not after:
        return out
    out["r_market"] = score_market(after, stop, t1)
    lim = score_limit(after, zh, stop, t1)
    out["r_limit"], out["limit_filled"] = lim, int(lim is not None)
    atr = float(r.get("atr_at_signal") or 0) or _atr14(before)
    lay = build_layers(spot, zl, zh, stop, t1, atr, risk_budget_usd=500.0) if spot else None
    if lay:
        res = score_layers(after, lay)
        out.update(r_layer=res["r"], layer_fills=res["fills"], layer_mode=lay["mode"])
    return out


def _candidates() -> List[Dict[str, Any]]:
    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init(conn)
        rows = [dict(x) for x in conn.execute(
            "SELECT s.* FROM suggestions s LEFT JOIN research_scorecard c ON c.suggestion_id = s.id "
            "WHERE s.source IN ('judge', 'rule') AND (s.kind IS NULL OR s.kind = 'NEW') "
            "AND COALESCE(s.side, 'LONG') = 'LONG' AND s.date >= ? AND (c.suggestion_id IS NULL OR c.mature = 0) "
            "ORDER BY s.date", (START_DATE,))]
        done = {(x[0], x[1]) for x in conn.execute("SELECT ticker, date FROM research_scorecard WHERE source = 'judge'")}
    best: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r["ticker"], str(r["date"])[:10])
        if r["source"] == "rule" and key in done:
            continue  # a judge row for the same plan is already scored
        if key not in best or (r["source"] == "judge" and best[key]["source"] != "judge"):
            best[key] = r
    return list(best.values())


def run() -> Dict[str, int]:
    rows = _candidates()
    if not rows:
        return {"candidates": 0, "scored": 0, "mature": 0}
    first = min(str(r["date"])[:10] for r in rows)
    start = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d")
    spy_b = _bars("SPY", start)
    spy = {b["date"]: b["close"] for b in spy_b}
    spy_dates = sorted(spy)
    by_tk: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_tk.setdefault(r["ticker"], []).append(r)
    scored = mature = 0
    for tk, rs in by_tk.items():
        t0 = min(str(r["date"])[:10] for r in rs)
        try:
            bars = _bars(tk, (datetime.strptime(t0, "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"[scorecard] {tk} bars failed: {e}")
            continue
        if not bars:
            continue
        for r in rs:
            try:
                res = score_row(r, bars, spy, spy_dates)
            except Exception as e:
                logger.debug(f"[scorecard] {tk} {r['date']} failed: {e}")
                continue
            with _db_lock, _get_connection() as conn:
                _init(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO research_scorecard (suggestion_id, ticker, date, source, lane, gate, verdict,"
                    " spot, rel10_pre, run_bucket, h5, h10, h21, s5, s10, s21, r_market, r_limit, r_layer, limit_filled,"
                    " layer_fills, layer_mode, bars, mature, scored_at, b1_n) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    " (SELECT b1_n FROM research_scorecard WHERE suggestion_id = ?))",
                    (r["id"], tk, str(r["date"])[:10], r["source"], r.get("setup_lane"), r.get("gate_status"), r.get("verdict"),
                     res["spot"], res["rel10_pre"], res["run_bucket"], res["h5"], res["h10"], res["h21"], res["s5"],
                     res["s10"], res["s21"], res["r_market"], res["r_limit"], res["r_layer"], res["limit_filled"],
                     res["layer_fills"], res["layer_mode"], res["bars"], res["mature"], _now_iso(), r["id"]))
                conn.commit()
            scored += 1
            mature += res["mature"]
    out = {"candidates": len(rows), "scored": scored, "mature": mature}
    try:
        out["b1_labeled"] = label_b1()
    except Exception as e:
        logger.warning(f"[scorecard] B1 labeling failed: {e}")
    return out


def label_b1() -> int:
    """Fill b1_n on rows that lack it. -1 = no research datawindow; NULL stays NULL until the HV20 median exists."""
    from zoneinfo import ZoneInfo

    from src import config
    from src.logic import b1_label

    with _db_lock, _get_connection() as conn:
        _init(conn)
        rows = conn.execute("SELECT suggestion_id, ticker, date FROM research_scorecard WHERE b1_n IS NULL").fetchall()
    if not rows:
        return 0
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    comps: Dict[int, Optional[Dict[str, Any]]] = {}
    for sid, tk, d in rows:
        dw = b1_label.load_datawindow(config.BASE_DIR, tk, str(d)[:10])
        comps[sid] = b1_label.components(dw, str(d)[:10]) if dw is not None else None
    need = sorted({c["bar_date"] for c in comps.values() if c and c["bar_date"] < today})
    with _db_lock, _get_connection() as conn:
        med: Dict[str, Optional[float]] = dict(b1_label.cached_medians(conn, need))
    missing = [d for d in need if d not in med]
    if missing:
        built = {d: v for d, v in b1_label.build_medians(missing[0], missing[-1]).items() if d in set(missing)}
        if built:
            with _db_lock, _get_connection() as conn:
                b1_label.store_medians(conn, built)
            med.update({d: v[0] for d, v in built.items()})
    labeled = 0
    with _db_lock, _get_connection() as conn:
        for sid, c in comps.items():
            if c is None:
                n: Optional[int] = -1
            else:
                n = b1_label.count(c, med.get(c["bar_date"]))
                if n is None:
                    continue
            conn.execute("UPDATE research_scorecard SET b1_n = ? WHERE suggestion_id = ?", (n, sid))
            labeled += 1
        conn.commit()
    return labeled


def _stats(vals: List[float]) -> Dict[str, Any]:
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "mean": round(st.mean(v), 3), "median": round(st.median(v), 3),
            "pos_pct": round(100 * sum(x > 0 for x in v) / len(v), 1), "read": len(v) >= MIN_READ}


def _group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ex = lambda k: [r[f"h{k}"] - r[f"s{k}"] for r in rows if r[f"h{k}"] is not None and r[f"s{k}"] is not None]
    planned = [r for r in rows if r["r_market"] is not None or r["r_limit"] is not None or r["r_layer"] is not None]
    return {
        "n": len(rows),
        "mature": sum(r["mature"] or 0 for r in rows),
        "hold_vs_spy_10d": _stats(ex(10)),
        "hold_vs_spy_21d": _stats(ex(21)),
        "with_plan": len(planned),
        "r_market": _stats([r["r_market"] for r in planned]),
        "r_limit": {**_stats([r["r_limit"] for r in planned]),
                    "fill_pct": round(100 * sum(r["limit_filled"] or 0 for r in planned) / len(planned), 1) if planned else None},
        "r_layer": {**_stats([r["r_layer"] for r in planned]),
                    "any_fill_pct": round(100 * sum((r["layer_fills"] or 0) > 0 for r in planned) / len(planned), 1) if planned else None},
    }


def report() -> Dict[str, Any]:
    """hold = % vs SPY; r_* = R per plan (unfilled limit/layers count as None in R, see fill pcts)."""
    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM research_scorecard")]
    if not rows:
        return {"n": 0}
    for r in rows:
        b = r.get("b1_n")
        r["b1"] = "unlabeled" if b is None else ("no_datawindow" if b < 0 else ("B1" if b >= B1_MIN else "not_B1"))
    out: Dict[str, Any] = {"all": _group(rows), "dates": [min(r["date"] for r in rows), max(r["date"] for r in rows)]}
    for key in ("gate", "source", "run_bucket", "lane", "layer_mode", "b1"):
        out[f"by_{key}"] = {str(k): _group([r for r in rows if r[key] == k]) for k in sorted({r[key] for r in rows}, key=str)}
    out["note"] = ("Measurement only. hold = close-to-close % vs SPY from the research spot; r_market / r_limit / r_layer = "
                   "plan stop & T1, 21 bars, stop first, no costs. Read a cell only when read=true (n >= 30).")
    return out


def _fmt(g: Dict[str, Any]) -> str:
    def s(x, pct=False):
        if not x.get("n"):
            return "      n/a"
        m = x["mean"] * (100 if pct else 1)
        return f"{m:+7.2f}{'%' if pct else 'R'} n={x['n']}"
    return (f"n={g['n']:<4} hold10 {s(g['hold_vs_spy_10d'], True)} | mkt {s(g['r_market'])} | "
            f"limit {s(g['r_limit'])} fill {g['r_limit'].get('fill_pct')}% | layer {s(g['r_layer'])}")


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if not a.report:
        print(run())
    rep = report()
    if rep.get("n") == 0:
        print("no rows")
    else:
        print("ALL        ", _fmt(rep["all"]))
        for key in ("gate", "run_bucket", "source", "layer_mode", "b1"):
            for k, g in rep[f"by_{key}"].items():
                print(f"{key}={k:<14}", _fmt(g))
        if "--json" in __import__("sys").argv:
            print(json.dumps(rep, indent=1))
