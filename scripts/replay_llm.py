#!/usr/bin/env python3
"""End-to-end replay of today's recorded Intraday ENTRY alerts through the FULL path:

    1. NEW hard risk gates evaluated at each alert's ORIGINAL timestamp (so time-window
       and exposure gates see realistic historical state), using real open-position state.
    2. If a gate clears it -> the local LLM (revanth-0dte.md) makes the GO/NO-GO call on
       the EXACT historical snapshot that was recorded (price, grade, score, plan, align,
       context). No live re-fetch — this is a faithful backtest of "what would the system
       have decided today with the new gates + LLM".

Then joins each decision to the next same-day EXIT for that symbol to show whether
taking it would have won.

Usage:
    python scripts/replay_llm.py                 # all entries for today
    python scripts/replay_llm.py --max 10        # first 10 only (LLM is slow)
    python scripts/replay_llm.py --only-vetoed   # only ones the OLD gates blocked
"""
from __future__ import annotations

import argparse, json, os, re, sqlite3, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tracking.alert_db import DB_PATH
from src.tracking.alert_evaluator import (
    evaluate_risk_vetoes, _load_gem, query_local_llm,
)
import src.tracking.position_state as ps
import src.tracking.alert_evaluator as ae

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
    for t in ("GRADE B", "EXHAUSTION", "MAX EXPOSURE", "DAY PAUSE", "LUNCH CHOP", "COUNTER-STAGE", "EXCLUDED"):
        if t in hdr:
            return t
    return "OTHER"


def was_taken(recorded):
    if not recorded:
        return False
    r = recorded.upper()
    if "STAND ASIDE" in r or "WAIT" in r:
        return False
    return ("TAKE" in r) or ("ENTER" in r) or ("\U0001F7E2" in recorded)


def llm_decision_for(symbol, action, score, grade, align, plan, why_now, wrong_if,
                     context, current_price, vix, current_time_et):
    """Replicate the intraday LLM call from evaluate_alert_payload on a historical snapshot."""
    system_prompt = _load_gem("revanth-0dte.md")
    if not system_prompt:
        return "NO GEM LOADED", ""

    is_call = "CALL" in action.upper() or "BUY CALLS" in (verdict := "")
    user_prompt = f"""Current Live Context (Historical Replay Snapshot):
- Current Time (ET): {current_time_et}
- Ticker Underlying Price: {current_price}
- VIX Index Level: {vix}
- Strategy: Intraday
- Action: {action}
- Conviction Score: {score}/100 ({grade})
- Setup / Alignment: {align or '--'}
- Plan: {plan or '--'}
- Why Now: {why_now or '--'}
- Invalidation (Wrong If): {wrong_if or '--'}
- Regime Context: {context or 'TREND UP · Below VWAP · in OR'}

Authoritative Alert Payload:
{{"ticker": "{symbol}", "action": "{action}", "price": {current_price}, "score": {score}, "grade": "{grade}"}}

Apply the revanth-0dte.md rules card to this alert and return your GO/NO-GO decision and tactical playbook.
"""
    response_text = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=1536,
        use_tools=False,
        disable_thinking=True,
        model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
    )
    cleaned = (response_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    HEADER_REGEX = re.compile(
        r"(?:🟢\s*TAKE\s*CALLS|🔴\s*TAKE\s*PUTS|⏸️\s*WAIT|⛔\s*STAND\s*ASIDE|"
        r"\bTAKE\s+CALLS\b|\bTAKE\s+PUTS\b|\bSTAND\s+ASIDE\b|\bGO\s*\()",
        re.IGNORECASE,
    )
    header_line = ""
    for line in cleaned.splitlines():
        ls = re.sub(r"\*\*", "", line).strip()
        if not ls or ls.startswith(("{", "```", "---")):
            continue
        m = HEADER_REGEX.search(ls)
        if m:
            header_line = ls
            break
    verdict = ("TAKE" if ("TAKE" in header_line.upper() or "GO(" in header_line.upper())
               else ("STAND ASIDE" if "STAND ASIDE" in header_line.upper() else "WAIT"))
    return (header_line or "NO HEADER"), verdict


def parse_plan(plan):
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=os.environ.get("REPLAY_DATE") or datetime.now(ET).strftime("%Y-%m-%d"))
    ap.add_argument("--max", type=int, default=0, help="limit number of entries (0=all)")
    ap.add_argument("--only-vetoed", action="store_true", help="only replay entries the OLD gates blocked")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""SELECT message_id, symbol, action, timestamp, grade, score, align, plan,
                          wrong_if, act_now, setup, alert_price, market_price, llm_decision, raw_payload
                   FROM alerts WHERE date=? AND strategy='Intraday' ORDER BY timestamp ASC""", (args.date,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Build exits + payload fields
    exits = {}
    parsed = []
    for r in rows:
        act = str(r["action"]).upper()
        is_exit = any(k in act for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT"))
        p = r.get("raw_payload") or ""
        payload = {}
        if isinstance(p, str) and p.startswith("{"):
            try: payload = json.loads(p)
            except Exception: pass
        elif isinstance(p, dict):
            payload = p
        rec = str(r.get("llm_decision") or "")
        entry_px = r.get("alert_price") or r.get("market_price")
        try:
            entry_px = float(entry_px) if entry_px not in (None, "", "N/A") else None
        except (ValueError, TypeError):
            entry_px = None

        if is_exit:
            ts = ts_to_aware(r["timestamp"])
            exits.setdefault(str(r["symbol"]).upper(), []).append((ts, entry_px))
            continue

        if args.only_vetoed and "STAND ASIDE" not in rec:
            continue

        parsed.append({
            "sym": str(r["symbol"]).upper(), "action": act, "ts": ts_to_aware(r["timestamp"]),
            "grade": str(r["grade"] or "A").upper(), "score": int(r["score"] or 75),
            "align": str(r["align"] or ""), "plan": str(r.get("plan") or payload.get("plan") or ""),
            "why_now": str(payload.get("why_now") or r.get("act_now") or r.get("setup") or ""),
            "wrong_if": str(r.get("wrong_if") or payload.get("wrong_if") or ""),
            "context": str(payload.get("context") or r.get("setup") or ""),
            "entry_px": entry_px, "recorded": rec,
        })
    for s in exits:
        exits[s].sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=ET))

    if args.max:
        parsed = parsed[:args.max]

    print(f"REPLAY (full path: NEW gates + local LLM)  date={args.date}  entries={len(parsed)}")
    print(f"GATES: GRADE_B<{os.getenv('GRADE_B_VETO_THRESHOLD','65')}  "
          f"MID_MORNING>={os.getenv('MID_MORNING_MIN_SCORE','85')}  "
          f"MAX_SIDE={os.getenv('MAX_CONCURRENT_SAME_SIDE','3')}\n")

    open_pos = {}   # real open positions, reconstructed from what OLD system took
    results = []
    for i, e in enumerate(parsed):
        sym, ts = e["sym"], e["ts"]
        if ts is None:
            continue
        snapshot = {s: {"side": p, "strategy": "intraday",
                        "opened_at": ts.strftime("%Y-%m-%d")} for s, p in open_pos.items()}
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

        if was_taken(e["recorded"]):
            open_pos[sym] = "LONG"

        if veto:
            final_verdict = "STAND ASIDE"
            header = f"GATE VETO ({gate_tag(veto[0])})"
            llm_ran = False
        else:
            # LLM call on historical snapshot
            ct = ts.strftime("%I:%M %p ET")
            try:
                header, final_verdict = llm_decision_for(
                    sym, e["action"], e["score"], e["grade"], e["align"], e["plan"],
                    e["why_now"], e["wrong_if"], e["context"], e["entry_px"], "N/A", ct,
                )
            except Exception as ex:
                header, final_verdict = f"LLM ERROR {type(ex).__name__}", "ERROR"
            llm_ran = True

        # Join to next same-day exit for outcome
        lv = parse_plan(e["plan"])
        entry_px, exit_px = e["entry_px"], None
        for (ets, epx) in exits.get(sym, []):
            if ets and ts and ets > ts:
                exit_px = epx
                break
        outcome = "NO EXIT"
        if exit_px is not None:
            stop, t1 = lv.get("stop"), lv.get("t1")
            if stop and exit_px <= stop:
                outcome = "LOSS (stop)"
            elif t1 and exit_px >= t1:
                outcome = "WIN (T1)"
            elif stop and t1:
                outcome = "WIN*" if exit_px >= (stop + t1) / 2 else "LOSS*"
            else:
                outcome = "n/a"

        results.append({**e, "new_gate": gate_tag(veto[0]) if veto else None,
                        "llm_ran": llm_ran, "final": final_verdict, "header": header[:48],
                        "exit_px": exit_px, "outcome": outcome})

        old_blocked = "STAND ASIDE" in e["recorded"]
        marker = ""
        if old_blocked and final_verdict == "TAKE":
            marker = "  <== WOULD NOW TAKE"
        print(f"[{i+1}/{len(parsed)}] {str(ts)[:16]} {sym:<5} s={e['score']:<3} "
              f"gate={'CLEAR' if not veto else gate_tag(veto[0]):<14} llm={final_verdict:<12} "
              f"-> {outcome}{marker}")

    # Summary
    print("\n=== FULL-PATH REPLAY SUMMARY (NEW gates + LLM) ===")
    n = len(results)
    took = [r for r in results if r["final"] == "TAKE"]
    stood = [r for r in results if r["final"] in ("STAND ASIDE",)]
    waited = [r for r in results if r["final"] == "WAIT"]
    print(f"  Entries: {n}   LLM ran on: {sum(1 for r in results if r['llm_ran'])}")
    print(f"  Final TAKE: {len(took)}   STAND ASIDE: {len(stood)}   WAIT: {len(waited)}")

    old_blocked = [r for r in results if "STAND ASIDE" in r["recorded"]]
    now_take_oldblocked = [r for r in old_blocked if r["final"] == "TAKE"]
    print(f"\n  Of the {len(old_blocked)} entries the OLD system blocked:")
    print(f"    -> new path would TAKE: {len(now_take_oldblocked)}")
    wins = sum(1 for r in now_take_oldblocked if r["outcome"].startswith("WIN"))
    losses = sum(1 for r in now_take_oldblocked if r["outcome"].startswith("LOSS"))
    print(f"       of those, would-be outcome: {wins} win / {losses} loss")
    for r in now_take_oldblocked:
        print(f"        - {r['sym']} @ {str(r['ts'])[:16]} s={r['score']} "
              f"(old gate {gate_tag(r['recorded'])}) -> {r['outcome']}")


if __name__ == "__main__":
    main()
