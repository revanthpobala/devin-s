"""Can the system be trusted? Scored-suggestion statistics with uncertainty, split by who took what.

Groups: gated (gate PASS), rejected (REJECTED_BY_GATE), taken (user filled). Pending rows have no
outcome yet and are reported beside the scored ones, because only fast stops and targets are
scored before the 21-bar horizon, which biases a recent window toward fast outcomes.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, Iterable, List

MIN_N_FOR_CLAIM = 30
FLAT_R = 0.01


def wilson(wins: int, n: int, z: float = 1.96) -> List[float]:
    if n <= 0:
        return [0.0, 0.0]
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(100 * (c - m) / d, 1), round(100 * (c + m) / d, 1)]


def summarize(r_vals: Iterable[float]) -> Dict[str, Any]:
    r = [float(x) for x in r_vals]
    n = len(r)
    if n == 0:
        return {"n": 0, "verdict": "no scored trades"}
    wins = sum(1 for x in r if x > FLAT_R)
    mean = statistics.mean(r)
    sd = statistics.stdev(r) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    ranked = sorted(r, reverse=True)
    total = sum(r)
    lo, hi = round(mean - 1.96 * se, 3), round(mean + 1.96 * se, 3)
    if n < MIN_N_FOR_CLAIM:
        verdict = f"too few trades (n={n} < {MIN_N_FOR_CLAIM}); no claim"
    elif lo > 0:
        verdict = "positive expectancy, interval above 0"
    elif hi < 0:
        verdict = "negative expectancy, interval below 0"
    else:
        verdict = "interval includes 0; edge not established"
    return {
        "n": n,
        "wins": wins,
        "flat": sum(1 for x in r if abs(x) <= FLAT_R),
        "win_rate": round(100 * wins / n, 1),
        "win_ci": wilson(wins, n),
        "mean_r": round(mean, 3),
        "mean_ci": [lo, hi],
        "median_r": round(statistics.median(r), 3),
        "sum_r": round(total, 2),
        "mean_ex_top2": round(statistics.mean(ranked[2:]), 3) if n > 2 else None,
        "top1_share_pct": round(100 * ranked[0] / total, 1) if total > 0 else None,
        "verdict": verdict,
    }


READY_MIN_N = 200


def pipeline_funnel(live: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Where the pipeline's own ENTER/ARMED suggestions go: gate, fill, then how each filled one ended."""
    gate = {"PASS": 0, "REJECTED_BY_GATE": 0}
    unfilled: Dict[str, int] = {}
    ended: Dict[str, List[float]] = {}
    for r in live:
        g = r.get("gate_status")
        if g in gate:
            gate[g] += 1
        if g != "PASS" or not r.get("scored_at"):
            continue
        reason = r.get("exit_reason") or "UNKNOWN"
        if r.get("fill_price") and r.get("r_net") is not None:
            ended.setdefault(reason, []).append(float(r["r_net"]))
        else:
            unfilled[reason] = unfilled.get(reason, 0) + 1
    filled = sum(len(v) for v in ended.values())
    scored = filled + sum(unfilled.values())
    return {
        "suggestions": len(live), "gate": gate, "scored_gated": scored,
        "fill_rate_pct": round(100 * filled / scored, 1) if scored else None,
        "unfilled_by_reason": unfilled,
        "filled_by_exit": {k: {"n": len(v), "mean_r": round(sum(v) / len(v), 3)} for k, v in sorted(ended.items())},
    }


def delegation_readiness(gated: Dict[str, Any]) -> Dict[str, Any]:
    """Pass/fail on the same standard the forward log uses. All four must hold before trusting it unattended."""
    checks = {
        f"n >= {READY_MIN_N}": gated.get("n", 0) >= READY_MIN_N,
        "mean R interval above 0": (gated.get("mean_ci") or [0, 0])[0] > 0,
        "mean R above 0 without top 2 trades": (gated.get("mean_ex_top2") or 0) > 0,
        "best trade under 25% of total R": (gated.get("top1_share_pct") or 0) < 25 and gated.get("n", 0) > 0,
    }
    return {"ready": all(checks.values()), "checks": checks}


def build_trust_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`rows` are suggestions rows as dicts (id, ticker, verdict, gate_status, taken, r_net, scored_at)."""
    live = [r for r in rows if str(r.get("verdict") or "").upper() in ("ENTER", "STALK")]
    scored = [r for r in live if r.get("r_net") is not None]
    gated = [r for r in scored if r.get("gate_status") == "PASS"]
    rejected = [r for r in scored if r.get("gate_status") == "REJECTED_BY_GATE"]
    taken = [r for r in scored if r.get("taken")]
    pending = [r for r in live if r.get("r_net") is None and r.get("gate_status") == "PASS"]

    names: Dict[str, int] = {}
    for r in gated:
        names[r["ticker"]] = names.get(r["ticker"], 0) + 1
    repeats = {t: c for t, c in names.items() if c >= 3}

    return {
        "gated": summarize(r["r_net"] for r in gated),
        "rejected": summarize(r["r_net"] for r in rejected),
        "taken": summarize(r["r_net"] for r in taken),
        "pending_gated": len(pending),
        "taken_total": sum(1 for r in live if r.get("taken")),
        "repeat_tickers": repeats,
        "funnel": pipeline_funnel(live),
        "readiness": delegation_readiness(summarize(r["r_net"] for r in gated)),
        "note": "Only trades that hit a stop or target before 21 bars are scored early; pending trades are excluded.",
    }


def load_rows() -> List[Dict[str, Any]]:
    from src import config
    from src.tracking.db_locks import no_lock, readonly_connection

    with no_lock:
        with readonly_connection(config.research_watch_db_path(), timeout=2.0) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, ticker, verdict, gate_status, taken, r_net, exit_reason, fill_price, scored_at FROM suggestions "
                "WHERE source = 'judge' AND kind = 'NEW'"
            ).fetchall()]
