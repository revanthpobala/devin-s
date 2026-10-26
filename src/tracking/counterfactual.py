"""What would the gate-rejected suggestions have done? Learning only; never touches the live record.

Each rejected rule row is scored two ways, stored in `suggestion_counterfactual`:
  plan: its own zone/stop/target replayed by the honest scorer (filled? R?)
  fwd:  close-to-close return from the signal close, minus SPY over the same bars
Report groups by lane, R:R band and gate reason so a rejection reason that blocks winners shows up.

Gate-passed long rows also get an entry-policy replay in `suggestion_policy`: fill at the zone top, zone top plus
0.1 / 0.25 / 0.5 ATR, or the next open, then the same stop/target path walk (21 bars, stop first). The next-open
column is the R a missed (MISSED_RUNAWAY / NOT_FILLED) row would have carried. Measurement only; nothing trades on it.
Run: python -m src.tracking.counterfactual [--report]
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from src.tracking.rr_config import GATE_CHANGE_DATE
from src.tracking.trust_report import summarize
from src.tracking.watch_manager import _db_lock, _get_connection, _now_iso

logger = logging.getLogger(__name__)

HORIZON = 21
REASONS = (("at-market R:R", "at_market_rr"), ("planned R:R", "planned_rr"), ("stop", "stop_floor"),
           ("earnings", "earnings"), ("target_1", "target_ceiling"), ("missing", "missing_levels"),
           ("drift", "pine_drift"), ("geometry", "geometry"), ("order", "geometry"))


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS suggestion_counterfactual ("
        "suggestion_id INTEGER PRIMARY KEY, ticker TEXT, date TEXT, lane TEXT, reason TEXT, rr_band TEXT,"
        " plan_exit TEXT, plan_r REAL, fwd5 REAL, fwd10 REAL, fwd21 REAL, spy21 REAL, mature INTEGER, scored_at TEXT)"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suggestion_counterfactual)")}
    for col, typ in (("source", "TEXT"), ("post_change", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE suggestion_counterfactual ADD COLUMN {col} {typ}")


POLICIES = (("r_zone", 0.0), ("r_p10", 0.1), ("r_p25", 0.25), ("r_p50", 0.5), ("r_open", None))
FILL_WINDOW = 5
MISS_EXITS = ("MISSED_RUNAWAY", "NOT_FILLED")


def _init_policy(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS suggestion_policy (suggestion_id INTEGER PRIMARY KEY, source TEXT, lane TEXT, ticker TEXT,"
        " date TEXT, plan_exit TEXT, r_zone REAL, r_p10 REAL, r_p25 REAL, r_p50 REAL, r_open REAL, mature INTEGER, scored_at TEXT)"
    )


def _walk(bars: List[Dict[str, float]], i0: int, fill: float, stop: float, target: float, n: int = HORIZON) -> Optional[float]:
    risk = fill - stop
    if risk <= 0:
        return None
    window = bars[i0:i0 + n]
    for x in window:
        if x["low"] <= stop:
            return -1.0
        if x["high"] >= target:
            return (target - fill) / risk
    return (window[-1]["close"] - fill) / risk


def policy_replay(row: Dict[str, Any], bars: List[Dict[str, float]]) -> Dict[str, Any]:
    """R per entry policy for one long plan; `bars` are daily bars strictly after the signal date. None = no fill."""
    eh, stop, tgt, atr = (float(row[k]) for k in ("entry_high", "stop", "target_1", "atr_at_signal"))
    out: Dict[str, Optional[float]] = {}
    for col, k in POLICIES:
        if k is None:
            out[col] = _walk(bars, 0, bars[0]["open"], stop, tgt) if bars and bars[0]["open"] > stop else None
            continue
        lim, res = eh + k * atr, None
        for i, x in enumerate(bars[:FILL_WINDOW]):
            if x["low"] <= lim:
                fill = min(lim, x["open"])
                res = _walk(bars, i, fill, stop, tgt) if fill > stop else None
                break
        out[col] = res
    out["mature"] = int(len(bars) >= FILL_WINDOW + HORIZON)
    return out


def run_policies() -> Dict[str, int]:
    from datetime import datetime, timedelta

    from src.tracking.execution_validator import get_bars_since_date

    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init_policy(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT s.* FROM suggestions s LEFT JOIN suggestion_policy p ON p.suggestion_id = s.id "
            "WHERE s.gate_status = 'PASS' AND s.source IN ('rule', 'judge') AND (s.kind IS NULL OR s.kind = 'NEW') "
            "AND COALESCE(s.side, 'LONG') = 'LONG' AND (s.verdict IS NULL OR s.verdict IN ('ENTER', 'STALK')) "
            "AND s.entry_high > 0 AND s.stop > 0 AND s.stop < s.entry_low AND s.target_1 > s.entry_high AND s.atr_at_signal > 0 "
            "AND (p.suggestion_id IS NULL OR p.mature = 0) ORDER BY s.date")]
    done = mature = 0
    for r in rows:
        d = str(r["date"])[:10]
        start = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        df = get_bars_since_date(r["ticker"], start)
        if df is None or df.empty:
            continue
        lc = {c.lower(): c for c in df.columns}
        bars = [{k: float(row[lc[k]]) for k in ("open", "high", "low", "close")}
                for idx, row in df.iterrows() if str(idx)[:10] > d]
        if not bars:
            continue
        res = policy_replay(r, bars)
        with _db_lock, _get_connection() as conn:
            _init_policy(conn)
            conn.execute(
                "INSERT OR REPLACE INTO suggestion_policy (suggestion_id, source, lane, ticker, date, plan_exit, r_zone, r_p10, r_p25,"
                " r_p50, r_open, mature, scored_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["source"], r.get("setup_lane"), r["ticker"], d, r.get("exit_reason"), res["r_zone"], res["r_p10"],
                 res["r_p25"], res["r_p50"], res["r_open"], res["mature"], _now_iso()))
            conn.commit()
        done += 1
        mature += res["mature"]
    return {"policy_scored": done, "policy_mature": mature, "policy_candidates": len(rows)}


def policy_report() -> Dict[str, Any]:
    """Per source: fills, win rate and R for each entry policy, and the R that missed rows would have carried."""
    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init_policy(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM suggestion_policy")]
    out: Dict[str, Any] = {}
    for src in sorted({r["source"] for r in rows}):
        g = [r for r in rows if r["source"] == src]
        pol = {}
        for col, _ in POLICIES:
            rs = [r[col] for r in g if r[col] is not None]
            pol[col] = {"signals": len(g), "fills": len(rs), **({"summary": summarize(rs)} if rs else {})}
        missed = [r["r_open"] for r in g if r["plan_exit"] in MISS_EXITS and r["r_open"] is not None]
        out[src] = {"n": len(g), "mature": sum(r["mature"] for r in g), "policies": pol,
                    "missed_at_next_open": summarize(missed) if missed else {"n": 0}}
    return out


def reason_key(gate_reasons: Optional[str]) -> str:
    if not gate_reasons:
        return "unrecorded"
    low = gate_reasons.lower()
    hits = sorted({k for needle, k in REASONS if needle in low})
    return "+".join(hits) or "other"


def rr_band(row: Dict[str, Any]) -> str:
    v = row.get("rr_at_market_at_signal")
    v = v if v is not None else row.get("planned_rr")
    if v is None:
        return "none"
    v = float(v)
    return "<0.5" if v < 0.5 else "0.5-1" if v < 1 else "1-1.5" if v < 1.5 else "1.5-2" if v < 2 else ">=2"


def _fwd(bars, date_str: str, spot: float, n: int) -> Optional[float]:
    after = bars[[str(i)[:10] > date_str for i in bars.index]]
    if len(after) < n or spot <= 0:
        return None
    col = "Close" if "Close" in after.columns else "close"
    return float(after[col].iloc[n - 1]) / spot - 1.0


def _spot_close(bars, date_str: str) -> float:
    col = "Close" if "Close" in bars.columns else "close"
    sub = bars[[str(i)[:10] == date_str for i in bars.index]]
    return float(sub[col].iloc[0]) if len(sub) else 0.0


def run() -> Dict[str, int]:
    from datetime import datetime, timedelta

    from src.tracking.execution_validator import get_bars_since_date
    from src.tracking.suggestion_scorer import score_suggestion

    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT s.* FROM suggestions s LEFT JOIN suggestion_counterfactual c ON c.suggestion_id = s.id "
            "WHERE s.gate_status = 'REJECTED_BY_GATE' AND s.source IN ('rule', 'judge') AND (s.kind IS NULL OR s.kind = 'NEW') "
            "AND (c.suggestion_id IS NULL OR c.mature = 0) ORDER BY s.date")]
    done = mature = 0
    spy_cache: Dict[str, Any] = {}
    for r in rows:
        d = str(r["date"])[:10]
        start = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=40)).strftime("%Y-%m-%d")
        bars = get_bars_since_date(r["ticker"], start)
        if start not in spy_cache:
            spy_cache[start] = get_bars_since_date("SPY", start)
        spy = spy_cache[start]
        if bars is None or bars.empty:
            continue
        spot = float(r.get("spot_at_signal") or 0.0) or _spot_close(bars, d)
        f5, f10, f21 = (_fwd(bars, d, spot, n) for n in (5, 10, HORIZON))
        s21 = _fwd(spy, d, _spot_close(spy, d), HORIZON) if spy is not None and not spy.empty else None
        plan_exit = plan_r = None
        if r.get("entry_low") is not None and r.get("stop") is not None and r.get("target_1") is not None:
            sc = score_suggestion(r)
            if sc.get("scored_at"):
                plan_exit, plan_r = sc.get("exit_reason"), sc.get("r_net")
        is_mature = int(f21 is not None)
        with _db_lock, _get_connection() as conn:
            _init(conn)
            conn.execute(
                "INSERT OR REPLACE INTO suggestion_counterfactual (suggestion_id, ticker, date, lane, reason, rr_band, plan_exit,"
                " plan_r, fwd5, fwd10, fwd21, spy21, mature, scored_at, source, post_change) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["ticker"], d, r.get("setup_lane"), reason_key(r.get("gate_reasons")), rr_band(r),
                 plan_exit, plan_r, f5, f10, f21, s21, is_mature, _now_iso(), r.get("source"),
                 int(d >= GATE_CHANGE_DATE)),
            )
            conn.commit()
        done += 1
        mature += is_mature
    return {"scored": done, "mature": mature, "candidates": len(rows)}


def run_all() -> Dict[str, int]:
    out = run()
    try:
        out.update(run_policies())
    except Exception as e:
        logger.warning("policy replay failed: %s", e)
        out["policy_error"] = str(e)
    return out


def _group(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in sorted({r[key] or "none" for r in rows}):
        g = [r for r in rows if (r[key] or "none") == k]
        mat = [r for r in g if r["mature"]]
        rel = [r["fwd21"] - r["spy21"] for r in mat if r["spy21"] is not None]
        planned = [r for r in g if r["plan_r"] is not None]
        out[k] = {
            "n": len(g), "mature": len(mat),
            "plan_filled": len(planned), "plan_mean_r": round(sum(r["plan_r"] for r in planned) / len(planned), 3) if planned else None,
            "plan_unfilled": {x: sum(1 for r in g if r["plan_exit"] == x) for x in {r["plan_exit"] for r in g if r["plan_r"] is None and r["plan_exit"]}},
            "fwd21_vs_spy_pct": round(100 * sum(rel) / len(rel), 2) if rel else None,
            "up_rate_pct": round(100 * sum(1 for r in rel if r > 0) / len(rel), 1) if rel else None,
        }
    return out


def report() -> Dict[str, Any]:
    with _db_lock, _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _init(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM suggestion_counterfactual")]
    filled = [r["plan_r"] for r in rows if r["plan_r"] is not None]
    return {
        "rows": len(rows), "mature": sum(r["mature"] for r in rows),
        "plan_as_written": summarize(filled),
        "by_lane": _group(rows, "lane"), "by_rr_band": _group(rows, "rr_band"), "by_reason": _group(rows, "reason"),
        "by_source": _group(rows, "source"), "by_gate_era": _group([{**r, "era": "post" if r.get("post_change") else "pre"} for r in rows], "era"),
        "note": "fwd21 is close-to-close from the signal close minus SPY; a beta-adjusted read, not an entry rule. "
                "Rows before the gate logged reasons are 'unrecorded'. No claim below n=30 per group.",
    }


if __name__ == "__main__":
    import json
    import sys

    if "--report" not in sys.argv:
        print(json.dumps(run_all()))
    print(json.dumps({"gate": report(), "policy": policy_report()}, indent=1, default=str))
