"""The agent takes trades. A plan that clears the measured technical gate (live R:R at or above the floor, 1.5 by default) goes to the LLM with the
dossier's fundamental thesis; the LLM answers TAKE or PASS. TAKE opens a PAPER position (ACT: at the live price,
WAIT: a limit order at the zone top that expires), managed to the plan's stop and T1 and scored in R after fees.

Hard rules stay in code (src/tracking/trade_rules.py), the same ones the user trades by: R:R gate, 100 shares, no earnings blackout.
The LLM can only say TAKE or PASS; it cannot resize or move levels. No broker order is ever sent from here.
Agent results are reported apart from yours (decision_log), so 'is the machine worth trusting' is its own record.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src import config
from src.tracking import rr_config, trade_rules

logger = logging.getLogger(__name__)

PENDING_DAYS = 10
MAX_HOLD_DAYS = 30
LOOK_COOLDOWN_S = 1800
FEE = 0.0005
THESIS_CHARS = 2500

SYSTEM = (
    "You are the trading agent. A setup already passed the technical gate. The packet's `order` field says what you would place: buy now (price in zone) or a resting limit at the zone top (price above the zone; it fills only if price comes back, so judge reward:risk at the limit, not at the live price). "
    "Decide TAKE or PASS using the technical facts and the fundamental research thesis together. "
    "PASS when the thesis says this is not an entry, the catalyst risk is binary, the thesis contradicts the technical read, or the evidence is thin. "
    "TAKE only when both agree. Answer with JSON only: "
    '{"decision":"TAKE or PASS","technical":"one sentence","fundamental":"one sentence","reason":"one sentence naming the deciding fact"}'
)


def db_path() -> Path:
    return config.BASE_DIR / "data" / "agent_trades.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS agent_order (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, ticker TEXT, plan_date TEXT,"
                 " state TEXT, kind TEXT, limit_px REAL, stop REAL, t1 REAL, qty INTEGER, risk_usd REAL, created_at TEXT, fill_px REAL,"
                 " filled_at TEXT, exit_px REAL, exit_at TEXT, exit_reason TEXT, r_net REAL, why TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS agent_look (plan_id INTEGER, ticker TEXT, decision TEXT, reason TEXT, at REAL, detail TEXT)")
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def thesis_text(ticker: str, plan_date: str, root: Optional[Path] = None) -> str:
    p = (root or config.BASE_DIR / "reports") / plan_date / f"{ticker}_summary.md"
    try:
        return p.read_text(encoding="utf-8")[:THESIS_CHARS]
    except Exception:
        return ""


def packet(plan: Dict[str, Any], thesis: str, earnings_in_days: Optional[int]) -> str:
    a = plan["action"]
    return json.dumps({
        "ticker": plan["ticker"], "system_verdict": a["label"],
        "order": "buy now at live price" if a["label"] == "ACT" else f"resting limit buy at zone top {plan.get('entry_zone_high')}, do not chase", "system_reason": a["why"],
        "live_price": plan.get("last_price"), "entry_zone": [plan.get("entry_zone_low"), plan.get("entry_zone_high")],
        "stop": plan.get("tactical_stop"), "target_1": plan.get("target_1"), "target_2": plan.get("target_2"),
        "rr_at_market": plan.get("rr_live"), "rr_at_zone_top": a.get("rr_top"), "earnings_in_days": earnings_in_days,
        "dossier_conviction": plan.get("conviction"), "fundamental_thesis": thesis or "no dossier text available",
    })


def parse(raw: str) -> Optional[Dict[str, str]]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    dec = str(d.get("decision", "")).upper()
    if dec not in ("TAKE", "PASS"):
        return None
    return {"decision": dec, "reason": str(d.get("reason", ""))[:300], "technical": str(d.get("technical", ""))[:200], "fundamental": str(d.get("fundamental", ""))[:200]}


def candidates(plans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in plans:
        a = p.get("action") or {}
        if p.get("is_taken") or p.get("my_decision") or p.get("side", "LONG") != "LONG":
            continue
        if a.get("label") == "ACT" or (a.get("label") == "WAIT" and (a.get("rr_top") or 0) >= rr_config.min_rr()):
            out.append(p)
    return out


def size(equity: float, entry: float, stop: float) -> int:
    return trade_rules.SHARES_PER_TRADE if entry > stop > 0 and equity > 0 else 0


def _r(entry: float, exit_px: float, stop: float) -> float:
    return round((exit_px * (1 - FEE) - entry * (1 + FEE)) / (entry - stop), 3)


def manage(prices: Dict[int, float], today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Fill or expire pending limits, stop out or target open positions. Returns notification events."""
    today = today or date.today()
    ev = []
    with _conn() as conn:
        for o in conn.execute("SELECT * FROM agent_order WHERE state IN ('PENDING','OPEN')").fetchall():
            px = prices.get(o["plan_id"])
            if not px:
                continue
            age = (today - date.fromisoformat(o["created_at"][:10])).days
            if o["state"] == "PENDING":
                if px <= o["stop"] or age > PENDING_DAYS:
                    conn.execute("UPDATE agent_order SET state='CANCELLED', exit_reason=?, exit_at=? WHERE id=?",
                                 ("price fell through the stop before the limit" if px <= o["stop"] else f"limit not reached in {PENDING_DAYS} days", _now(), o["id"]))
                elif px <= o["limit_px"]:
                    conn.execute("UPDATE agent_order SET state='OPEN', fill_px=?, filled_at=? WHERE id=?", (o["limit_px"], _now(), o["id"]))
                    ev.append(_event(o, "AGENT_FILL", f"limit ${o['limit_px']:.2f} filled", "INFO"))
                continue
            held = (today - date.fromisoformat((o["filled_at"] or o["created_at"])[:10])).days
            reason = exit_px = None
            if px <= o["stop"]:
                reason, exit_px = "STOP", min(px, o["stop"])
            elif px >= o["t1"]:
                reason, exit_px = "T1", o["t1"]
            elif held >= MAX_HOLD_DAYS:
                reason, exit_px = "TIME", px
            if reason:
                r = _r(o["fill_px"], exit_px, o["stop"])
                conn.execute("UPDATE agent_order SET state='CLOSED', exit_px=?, exit_at=?, exit_reason=?, r_net=? WHERE id=?", (exit_px, _now(), reason, r, o["id"]))
                ev.append(_event(o, "AGENT_EXIT", f"{reason} at ${exit_px:.2f}: {r:+.2f}R after fees", "WARN" if reason == "STOP" else "INFO"))
    return ev


def _event(o, kind, text, sev):
    return {"kind": kind, "severity": sev, "ticker": o["ticker"], "key": f"{kind}:{o['id']}",
            "title": f"AGENT {o['ticker']}: {text}", "body": f"Paper position, no broker order. Entry plan stop ${o['stop']:.2f}, T1 ${o['t1']:.2f}, qty {o['qty']}.\n{o['why'] or ''}"}


def propose(plans: List[Dict[str, Any]], equity: float, earnings_days: Callable[[str], Optional[int]],
            ask: Callable[[str, str], str], thesis: Callable[[str, str], str] = thesis_text, max_calls: int = 2) -> List[Dict[str, Any]]:
    """Run candidates through the LLM. At most max_calls LLM calls per cycle."""
    ev, calls = [], 0
    with _conn() as conn:
        live = conn.execute("SELECT * FROM agent_order WHERE state IN ('PENDING','OPEN')").fetchall()
        have = {o["plan_id"] for o in conn.execute("SELECT plan_id FROM agent_order").fetchall()}
        for p in candidates(plans):
            pid, tk = p["id"], p["ticker"]
            if pid in have:
                continue
            seen = conn.execute("SELECT at FROM agent_look WHERE plan_id=? ORDER BY at DESC LIMIT 1", (pid,)).fetchone()
            if seen and time.time() - seen["at"] < LOOK_COOLDOWN_S:
                continue
            entry = p["last_price"] if p["action"]["label"] == "ACT" else p["entry_zone_high"]
            stop = p["tactical_stop"]
            qty = size(equity, entry, stop)
            ed = earnings_days(tk)
            veto = None
            if qty < 1:
                veto = "no valid entry above the stop"
            if veto:
                conn.execute("INSERT INTO agent_look VALUES (?,?,?,?,?,?)", (pid, tk, "PASS", veto, time.time(), "rule"))
                continue
            if calls >= max_calls:
                break
            calls += 1
            try:
                d = parse(ask(SYSTEM, packet(p, thesis(tk, p["date"]), ed)))
            except Exception as e:
                logger.warning("agent LLM failed for %s: %s", tk, e)
                d = None
            if not d:
                conn.execute("INSERT INTO agent_look VALUES (?,?,?,?,?,?)", (pid, tk, "ERROR", "no usable answer", time.time(), ""))
                continue
            why = f"Technical: {d['technical']} Fundamental: {d['fundamental']} Deciding fact: {d['reason']}"
            conn.execute("INSERT INTO agent_look VALUES (?,?,?,?,?,?)", (pid, tk, d["decision"], d["reason"], time.time(), why))
            if d["decision"] != "TAKE":
                continue
            act = p["action"]["label"] == "ACT"
            cur = conn.execute("INSERT INTO agent_order (plan_id,ticker,plan_date,state,kind,limit_px,stop,t1,qty,risk_usd,created_at,fill_px,filled_at,why)"
                               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (pid, tk, p["date"], "OPEN" if act else "PENDING", "MARKET" if act else "LIMIT", entry, stop, p["target_1"], qty,
                                round(qty * (entry - stop), 2), _now(), entry if act else None, _now() if act else None, why))
            o = dict(conn.execute("SELECT * FROM agent_order WHERE id=?", (cur.lastrowid,)).fetchone())
            live.append(o)
            ev.append(_event(o, "AGENT_TAKE", f"{'bought' if act else 'limit order'} {qty} @ ${entry:.2f}", "WARN"))
    return ev


def passed_plan_ids() -> list:
    if not db_path().exists():
        return []
    with _conn() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT plan_id FROM agent_look WHERE decision = 'PASS'")]


def by_plan() -> Dict[int, Dict[str, Any]]:
    """Latest agent stance per plan for the Trades tab."""
    if not db_path().exists():
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    with _conn() as conn:
        for r in conn.execute("SELECT plan_id, decision, reason FROM agent_look ORDER BY at").fetchall():
            out[r["plan_id"]] = {"stance": r["decision"], "reason": r["reason"]}
        for o in conn.execute("SELECT * FROM agent_order ORDER BY id").fetchall():
            out[o["plan_id"]] = {"stance": o["state"], "reason": o["exit_reason"] or o["why"], "r_net": o["r_net"], "fill_px": o["fill_px"]}
    return out


def summary() -> Dict[str, Any]:
    from src.tracking.trust_report import summarize

    if not db_path().exists():
        return {"closed": summarize([]), "open": [], "passes": 0}
    with _conn() as conn:
        closed = [o["r_net"] for o in conn.execute("SELECT r_net FROM agent_order WHERE state='CLOSED'").fetchall()]
        opn = [dict(o) for o in conn.execute("SELECT ticker, state, fill_px, limit_px, stop, t1, qty FROM agent_order WHERE state IN ('OPEN','PENDING')").fetchall()]
        passes = conn.execute("SELECT COUNT(*) FROM agent_look WHERE decision='PASS'").fetchone()[0]
    return {"closed": summarize(closed), "open": opn, "passes": passes}
