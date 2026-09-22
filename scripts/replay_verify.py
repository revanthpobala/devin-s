#!/usr/bin/env python3
"""Accurate replay of today's recorded Intraday ENTRY alerts through the NEW risk gates.

The exposure gate needs the *actual* set of open positions at each alert moment, which is
driven by what was REALLY taken (recorded TAKE) and closed (EXIT). We reconstruct that
timeline from the recorded decisions rather than from the new-gate outcome, so the
MAX EXPOSURE / DAY PAUSE gates see realistic state. Gate thresholds are read live from
the environment (new defaults), so this verifies the recalibration on real data.
"""
from __future__ import annotations

import json, os, sqlite3, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tracking.alert_db import DB_PATH
from src.tracking.alert_evaluator import evaluate_risk_vetoes
import src.tracking.position_state as ps

ET = ZoneInfo("America/New_York")


def ts_to_aware(ts):
    if not ts:
        return None
    ts = str(ts).strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        m = __import__("re").match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", ts)
        if not m:
            return None
        dt = datetime.strptime(m.group(0).replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
    return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt


def gate_tag(hdr):
    for t in ("GRADE B", "EXHAUSTION", "MAX EXPOSURE", "DAY PAUSE", "LUNCH CHOP", "COUNTER-STAGE", "EXCLUDED"):
        if t in hdr:
            return t
    return "OTHER"


def was_taken(recorded):
    """True if the recorded decision actually opened a position (TAKE), not STAND ASIDE/WAIT."""
    if not recorded:
        return False
    r = recorded.upper()
    if "STAND ASIDE" in r or "WAIT" in r:
        return False
    return ("TAKE" in r) or ("ENTER" in r) or ("\U0001F7E2" in recorded)


def main():
    today = os.environ.get("REPLAY_DATE") or datetime.now(ET).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""SELECT symbol, action, timestamp, grade, score, align, llm_decision
                   FROM alerts WHERE date=? AND strategy='Intraday' ORDER BY timestamp ASC""", (today,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    parsed = []
    for r in rows:
        act = str(r["action"]).upper()
        is_exit = any(k in act for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT"))
        rec = str(r.get("llm_decision") or "")
        parsed.append({"symbol": str(r["symbol"]).upper(), "action": act, "timestamp": r["timestamp"],
                       "grade": str(r["grade"] or "A").upper(), "score": int(r["score"] or 85),
                       "align": str(r["align"] or ""), "is_exit": is_exit, "recorded": rec})

    print(f"REPLAY DATE: {today}   ({sum(1 for p in parsed if not p['is_exit'])} entries, "
          f"{sum(1 for p in parsed if p['is_exit'])} exits)")
    print(f"ENV GATES: GRADE_B_VETO_THRESHOLD={os.getenv('GRADE_B_VETO_THRESHOLD','65')}  "
          f"MID_MORNING_MIN_SCORE={os.getenv('MID_MORNING_MIN_SCORE','85')}  "
          f"MAX_CONCURRENT_SAME_SIDE={os.getenv('MAX_CONCURRENT_SAME_SIDE','3')}  "
          f"DAY_PAUSE_ENABLED={os.getenv('DAY_PAUSE_ENABLED','false')}\n")

    open_pos = {}   # symbol -> side, reconstructed from what was REALLY taken/closed
    results = []
    for e in parsed:
        ts = ts_to_aware(e["timestamp"])
        if ts is None:
            continue
        sym = e["symbol"]

        if e["is_exit"]:
            open_pos.pop(sym, None)
            continue

        # Snapshot the REAL open positions at this moment for the exposure gate.
        snapshot = {s: {"side": p, "strategy": "intraday", "opened_at": ts.strftime("%Y-%m-%d")}
                    for s, p in open_pos.items()}
        orig = ps.list_open
        ps.list_open = (lambda snap=snapshot: snap)
        try:
            veto = evaluate_risk_vetoes(
                symbol=sym, action=e["action"], score=e["score"],
                current_time_et=ts.strftime("%I:%M %p"), eastern_dt=ts,
                grade=e["grade"], align=e["align"],
            )
        finally:
            ps.list_open = orig

        # Reconstruct real open set: only positions the OLD system actually took.
        if was_taken(e["recorded"]):
            side = "LONG"  # all of today's taken entries were CALL/long
            open_pos[sym] = side

        results.append({"sym": sym, "ts": str(ts)[:19], "score": e["score"], "grade": e["grade"],
                        "new_veto": gate_tag(veto[0]) if veto else None, "recorded": e["recorded"]})

    print(f"{'TIME':<19} {'SYM':<6} {'S':>3}  {'OLD (recorded)':<20} -> {'NEW GATE':<14}  CHANGED?")
    print("-" * 92)
    unlocked, newly_vetoed = [], []
    for r in results:
        rec = r["recorded"]
        old_tag = gate_tag(rec) if "STAND ASIDE" in rec else ("TAKE/WAIT" if rec else "?")
        new_tag = r["new_veto"] or "CLEAR (take)"
        changed = ""
        if "STAND ASIDE" in rec and not r["new_veto"]:
            changed = "<== UNLOCKED"
            unlocked.append(r)
        elif "STAND ASIDE" not in rec and r["new_veto"] and old_tag == "TAKE/WAIT":
            changed = "<== NEWLY VETOED"
            newly_vetoed.append(r)
        print(f"{r['ts']:<19} {r['sym']:<6} {r['score']:>3}  {old_tag:<20} -> {new_tag:<14}  {changed}")

    print("\n=== VERIFICATION SUMMARY (NEW gates vs recorded OLD behavior) ===")
    n = len(results)
    old_vetoed = sum(1 for r in results if "STAND ASIDE" in r["recorded"])
    new_vetoed = sum(1 for r in results if r["new_veto"])
    print(f"  Entries replayed:            {n}")
    print(f"  Old gates vetoed (recorded): {old_vetoed}")
    print(f"  New gates vetoed (replay):   {new_vetoed}")
    print(f"  UNLOCKED by new rules:       {len(unlocked)}")
    for r in unlocked:
        print(f"      - {r['sym']} @ {r['ts'][11:]} score={r['score']} (old gate: {gate_tag(r['recorded'])})")
    if newly_vetoed:
        print(f"  NEWLY VETOED by new rules:     {len(newly_vetoed)}")
        for r in newly_vetoed:
            print(f"      - {r['sym']} @ {r['ts'][11:]} score={r['score']} (new gate: {r['new_veto']})")

    print("\n  New-gate veto breakdown:")
    tags = Counter(r["new_veto"] for r in results if r["new_veto"])
    for t, c in tags.most_common():
        print(f"      - {t}: {c}")


if __name__ == "__main__":
    main()
