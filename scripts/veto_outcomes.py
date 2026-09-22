#!/usr/bin/env python3
"""For each of today's recorded STAND ASIDE (old-gate) vetoes, find the next same-day
EXIT for that symbol and estimate whether taking it would have won (plan vs exit price).
Confirms the plan's claim that filtered trades closed green."""
from __future__ import annotations

import json, os, sqlite3, sys, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.tracking.alert_db import DB_PATH

ET = ZoneInfo("America/New_York")


def ts_to_aware(ts):
    if not ts:
        return None
    ts = str(ts).strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", ts)
        if not m:
            return None
        dt = datetime.strptime(m.group(0).replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
    return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt


def gate_tag(hdr):
    for t in ("GRADE B", "EXHAUSTION", "MAX EXPOSURE", "DAY PAUSE", "LUNCH CHOP", "COUNTER-STAGE"):
        if t in hdr:
            return t
    return "OTHER"


def parse_plan(plan, entry):
    """Extract stop and T1 from a plan string like 'In 502.00 · Stop 498.00 · T1 506.00'."""
    res = {}
    if not plan:
        return res
    m = re.search(r"[Ss]top\s*[:$]?\s*([\d.]+)", plan)
    if m:
        res["stop"] = float(m.group(1))
    m = re.search(r"T1\s*[:$]?\s*([\d.]+)", plan)
    if m:
        res["t1"] = float(m.group(1))
    return res


def main():
    today = os.environ.get("REPLAY_DATE") or datetime.now(ET).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""SELECT symbol, action, timestamp, grade, score, align, plan,
                          alert_price, market_price, llm_decision
                   FROM alerts WHERE date=? AND strategy='Intraday' ORDER BY timestamp ASC""", (today,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Build exits per symbol with timestamps + exit price
    exits = {}  # sym -> list of (aware_dt, price)
    for r in rows:
        act = str(r["action"]).upper()
        if any(k in act for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT")):
            ts = ts_to_aware(r["timestamp"])
            px = r.get("alert_price") or r.get("market_price")
            try:
                px = float(px) if px not in (None, "", "N/A") else None
            except (ValueError, TypeError):
                px = None
            exits.setdefault(str(r["symbol"]).upper(), []).append((ts, px))
    for s in exits:
        exits[s].sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=ET))

    print(f"REPLAY DATE: {today} — outcome of today's recorded STAND ASIDE vetoes\n")
    print(f"{'TIME':<17} {'SYM':<6} {'S':>3}  {'GATE':<14} {'ENTRY':>8} {'EXIT':>8}  OUTCOME IF TAKEN")
    print("-" * 90)

    wins, losses, unknown = 0, 0, 0
    by_gate_win = {}
    for r in rows:
        act = str(r["action"]).upper()
        if any(k in act for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT")):
            continue
        rec = str(r.get("llm_decision") or "")
        if "STAND ASIDE" not in rec:
            continue
        sym = str(r["symbol"]).upper()
        ts = ts_to_aware(r["timestamp"])
        entry = r.get("alert_price") or r.get("market_price")
        try:
            entry = float(entry) if entry not in (None, "", "N/A") else None
        except (ValueError, TypeError):
            entry = None
        plan = str(r.get("plan") or "")
        levels = parse_plan(plan, entry)
        gtag = gate_tag(rec)

        # Find next exit for this symbol after the entry time
        outcome = "NO EXIT FOUND"
        exit_px = None
        for (ets, epx) in exits.get(sym, []):
            if ets and ts and ets > ts:
                exit_px = epx
                break
        if exit_px is not None:
            stop = levels.get("stop")
            t1 = levels.get("t1")
            if stop and exit_px <= stop:
                outcome = f"STOP {exit_px:.2f} (LOSS)"
                losses += 1
            elif t1 and exit_px >= t1:
                outcome = f"HIT T1 {exit_px:.2f} (WIN)"
                wins += 1
                by_gate_win[gtag] = by_gate_win.get(gtag, 0) + 1
            else:
                # between stop and T1 — approximate by midpoint
                if stop and t1:
                    mid = (stop + t1) / 2
                    outcome = f"EXIT {exit_px:.2f} ~{'WIN' if exit_px >= mid else 'LOSS'} (between S/T1)"
                    if exit_px >= mid:
                        wins += 1
                        by_gate_win[gtag] = by_gate_win.get(gtag, 0) + 1
                    else:
                        losses += 1
                else:
                    outcome = f"EXIT {exit_px:.2f} (no levels)"
                    unknown += 1
        else:
            unknown += 1

        print(f"{str(ts)[:16]:<17} {sym:<6} {int(r['score'] or 0):>3}  {gtag:<14} "
              f"{(f'{entry:.2f}' if entry else '   --'):>8} {(f'{exit_px:.2f}' if exit_px else '--'):>8}  {outcome}")

    print("\n=== IF THE OLD GATES HAD LET THESE THROUGH ===")
    print(f"  Clear WINS:  {wins}")
    print(f"  Clear LOSSES:{losses}")
    print(f"  Unknown/no exit: {unknown}")
    total = wins + losses
    if total:
        print(f"  Win rate (resolved): {100*wins/total:.0f}%")
    if by_gate_win:
        print("  Wins by old gate that filtered them:")
        for g, c in sorted(by_gate_win.items(), key=lambda x: -x[1]):
            print(f"      - {g}: {c} win(s)")


if __name__ == "__main__":
    main()
