"""Separate books scored by one scorer: Pine lanes, LLM discretionary plans, and plans the LLM vetoed."""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from typing import Any, Dict, List

from src import config
from src.tracking.suggestion_scorer import score_suggestion
from src.tracking.watch_manager import _db_lock, _get_connection

logger = logging.getLogger(__name__)

PINE_LANES = ("RR_SETUP", "RR_SETUP_STRONG", "CODE20", "OVERSOLD", "RSI2")
MIN_N = 30
MAX_RESCORE = 40


def _stats(rs: List[float]) -> Dict[str, Any]:
    if not rs:
        return {"n": 0}
    return {
        "n": len(rs),
        "win_pct": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1),
        "mean_r": round(statistics.mean(rs), 3),
        "median_r": round(statistics.median(rs), 3),
    }


def _cache_path():
    return config.BASE_DIR / "data" / "cache" / "vetoed_scores.json"


def _vetoed_scores() -> List[float]:
    """Score CASH_SKIP plans that carried a complete, gate-passing plan. Closed results are cached, open ones rescored."""
    cp = _cache_path()
    try:
        cache = json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                """SELECT * FROM suggestions WHERE source = 'judge' AND verdict = 'CASH_SKIP' AND gate_status = 'PASS'
                   AND (kind IS NULL OR kind = 'NEW') AND entry_low IS NOT NULL AND stop IS NOT NULL AND target_1 IS NOT NULL
                   ORDER BY date DESC"""
            )]
    todo = 0
    for r in rows:
        key = f"{r['ticker']}|{r['date']}"
        if key in cache and cache[key].get("closed"):
            continue
        if todo >= MAX_RESCORE:
            break
        todo += 1
        try:
            s = score_suggestion(r)
        except Exception as e:
            logger.debug(f"vetoed score {key} failed: {e}")
            continue
        if s.get("r_net") is not None:
            cache[key] = {"r": float(s["r_net"]), "closed": bool(s.get("exit_date"))}
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass
    return [v["r"] for v in cache.values() if v.get("closed")]


def _user_scores() -> List[float]:
    """Closed positions you took yourself (mode != MODEL), R from entry, exit and initial stop."""
    from src.tracking.alert_db import DB_PATH

    try:
        with sqlite3.connect(str(DB_PATH), timeout=10.0) as conn:
            rows = conn.execute(
                """SELECT entry_price, exit_price, COALESCE(initial_stop, stop) FROM positions
                   WHERE status = 'CLOSED' AND COALESCE(mode, 'MODEL') != 'MODEL' AND exit_price IS NOT NULL"""
            ).fetchall()
    except Exception:
        return []
    return [(x - e) / (e - st) for e, x, st in rows if e and st and e > st]


def book_stats() -> Dict[str, Dict[str, Any]]:
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                """SELECT setup_lane, r_net FROM suggestions WHERE source = 'judge' AND gate_status = 'PASS' AND kind = 'NEW'
                   AND scorer_version = 2 AND verdict IN ('ENTER', 'STALK') AND r_net IS NOT NULL"""
            )]
    pine = [float(r["r_net"]) for r in rows if r["setup_lane"] in PINE_LANES]
    disc = [float(r["r_net"]) for r in rows if r["setup_lane"] == "LLM_DISCRETIONARY"]
    return {"USER": _stats(_user_scores()), "PINE_LANE": _stats(pine), "LLM_DISCRETIONARY": _stats(disc), "LLM_VETOED": _stats(_vetoed_scores())}


def scorecard_text() -> str:
    try:
        b = book_stats()
    except Exception as e:
        logger.debug(f"scorecard unavailable: {e}")
        return ""

    def line(name: str, s: Dict[str, Any]) -> str:
        if not s.get("n"):
            return f"- {name}: no closed trades yet"
        return f"- {name}: n={s['n']}, win {s['win_pct']}%, mean {s['mean_r']:+.3f}R, median {s['median_r']:+.3f}R"

    out = [
        "YOUR SCORECARD (same scorer, 21 bars, fees in; below n=30 it is noise):",
        line("The user's own trades (reference, not yours)", b["USER"]),
        line("Pine-lane plans", b["PINE_LANE"]),
        line("Your discretionary plans", b["LLM_DISCRETIONARY"]),
        line("Plans you vetoed (CASH_SKIP, scored as if taken)", b["LLM_VETOED"]),
    ]
    v, p, d = b["LLM_VETOED"], b["PINE_LANE"], b["LLM_DISCRETIONARY"]
    if v.get("n", 0) >= MIN_N and v["mean_r"] > 0:
        out.append("RULE: your vetoes lost money as a group. Veto only on a concrete fact, otherwise take the plan.")
    if d.get("n", 0) >= MIN_N and p.get("n", 0) >= MIN_N and d["mean_r"] < p["mean_r"]:
        out.append("RULE: your discretionary plans trail the Pine lanes. Prefer CASH_SKIP over a weak discretionary plan.")
    return "\n".join(out)
