"""What would the gate-rejected suggestions have done? Learning only; never touches the live record.

Each rejected rule row is scored two ways, stored in `suggestion_counterfactual`:
  plan: its own zone/stop/target replayed by the honest scorer (filled? R?)
  fwd:  close-to-close return from the signal close, minus SPY over the same bars
Report groups by lane, R:R band and gate reason so a rejection reason that blocks winners shows up.
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
        print(json.dumps(run()))
    print(json.dumps(report(), indent=1, default=str))
