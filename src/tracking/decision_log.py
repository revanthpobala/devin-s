"""Your take/skip decisions next to what the system said at that moment, so 'did I beat or lose to the system' is measurable.

Each row stores the system's verdict and live R:R when you decided. Outcomes are scored later from the plan's
levels (see trust_report); this module only records and reads.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

REASONS = ("RR_TOO_LOW", "DONT_LIKE_CHART", "EARNINGS_SOON", "TOO_EXTENDED", "ALREADY_OWN", "NO_CASH", "MARKET_RISK", "OTHER")
DECISIONS = ("TAKE", "SKIP", "REJECT")


def db_path() -> Path:
    return config.BASE_DIR / "data" / "decisions.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS decision (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, ticker TEXT, plan_date TEXT,"
                 " decision TEXT, reason TEXT, system_label TEXT, system_rr REAL, spot REAL, decided_at TEXT)")
    return conn


def record(plan_id: int, ticker: str, plan_date: str, decision: str, reason: Optional[str],
           system_label: Optional[str], system_rr: Optional[float], spot: Optional[float]) -> Dict[str, Any]:
    decision = decision.upper()
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    reason = (reason or "OTHER").upper()
    if decision != "TAKE" and reason not in REASONS:
        raise ValueError(f"reason must be one of {REASONS}")
    with _conn() as conn:
        conn.execute("INSERT INTO decision (plan_id, ticker, plan_date, decision, reason, system_label, system_rr, spot, decided_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (plan_id, ticker.upper(), plan_date, decision, None if decision == "TAKE" else reason,
                      system_label, system_rr, spot, datetime.now().isoformat(timespec="seconds")))
    return {"ok": True}


def latest_by_plan() -> Dict[int, Dict[str, Any]]:
    if not db_path().exists():
        return {}
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM decision ORDER BY id").fetchall()
    return {r["plan_id"]: dict(r) for r in rows}


def summary() -> Dict[str, Any]:
    """How your calls compare with the system's: counts by system verdict and your decision."""
    if not db_path().exists():
        return {"n": 0, "by_system": {}}
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT system_label, decision, reason FROM decision")]
    by: Dict[str, Dict[str, int]] = {}
    for r in rows:
        d = by.setdefault(r["system_label"] or "NONE", {})
        key = r["decision"] if r["decision"] == "TAKE" else f"{r['decision']}:{r['reason']}"
        d[key] = d.get(key, 0) + 1
    return {"n": len(rows), "by_system": by}


def _outcome(t: Dict[str, Any]) -> str:
    st = t.get("status")
    if st in ("TARGET_HIT", "COMPLETED", "MISSED_RUNAWAY"):
        return "would_have_won"
    if st in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
        return "would_have_lost"
    return "open"


def score(trades: List[Dict[str, Any]], skipped_ids, passed_ids) -> Dict[str, Any]:
    """Plan-level result of plans you skipped and plans the agent passed on: did price reach T1 or the stop.
    Not fill-accurate (a plan can reach T1 without the zone filling), so it overstates winners: read the trend, not the level."""
    by = {t.get("id"): t for t in trades}
    out: Dict[str, Any] = {}
    for name, ids in (("skipped", skipped_ids), ("agent_passed", passed_ids)):
        c = {"would_have_won": 0, "would_have_lost": 0, "open": 0}
        for i in ids:
            if i in by:
                c[_outcome(by[i])] += 1
        out[name] = c
    return out
