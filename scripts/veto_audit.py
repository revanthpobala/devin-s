#!/usr/bin/env python3
"""
Veto Audit — measure what the AI actually filtered out.

Replays every STAND ASIDE (AI veto) recorded in trading_alerts.db against
TWO rule sets:

  OLD = the pre-2026-09-21 hard-coded gates:
        - Grade B / score < 80  -> hard veto
        - 10:30-11:30 ET score < 90 -> hard veto
        - 2 same-direction open positions -> hard veto
        - DAY PAUSE mandatory (3 losses/session, clears on A+)
  NEW = the recalibrated env-driven gates (src/tracking/alert_evaluator.py):
        - GRADE_B_VETO_THRESHOLD     (default 65)
        - MID_MORNING_MIN_SCORE      (default 85)
        - MAX_CONCURRENT_SAME_SIDE   (default 3)

For each vetoed entry it also:
  - counts same-direction open positions at the alert moment (from trade_events),
  - checks the DAY PAUSE history (from the alerts table, exits only up to the
    alert time),
  - finds the next same-day EXIT event (trade_events preferred, alerts table
    fallback) to estimate the theoretical P&L the entry would have had
    (parsed from the alert's plan: entry / stop / T1).

Output: console table + CSV at data/veto_audit/<date>_audit.csv.
No Google Sheets involved — the SQLite DB is the source of truth.

Usage:
    python scripts/veto_audit.py                          # today (ET)
    python scripts/veto_audit.py --date 2026-09-21
    python scripts/veto_audit.py --all                    # every recorded date
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.tracking.alert_db import DB_PATH

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

GATE_TAGS = ("MAX EXPOSURE", "DAY PAUSE", "LUNCH CHOP", "EXHAUSTION", "EXCLUDED", "COUNTER-STAGE", "GRADE B")


# --------------------------------------------------------------------------
# Time helpers (alerts store naive ET; trade_events store aware ISO-8601)
# --------------------------------------------------------------------------

def ts_to_naive_et(ts: Optional[str]) -> Optional[datetime]:
    """Parse any supported timestamp into a naive ET datetime."""
    if not ts:
        return None
    ts = str(ts).strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", ts)
        if not m:
            return None
        dt = datetime.strptime(m.group(0).replace("T", " "), "%Y-%m-%d %H:%M:%S")
        # naive -> already ET (alerts convention)
        return dt
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(ET).replace(tzinfo=None)


def et_date_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Gate simulation (mirrors src/tracking/alert_evaluator.py, old vs new)
# --------------------------------------------------------------------------

def in_mid_morning(dt: datetime) -> bool:
    return (dt.hour == 10 and dt.minute >= 30) or (dt.hour == 11 and dt.minute < 30)


def in_lunch(dt: datetime) -> bool:
    return (dt.hour == 11 and dt.minute >= 30) or dt.hour == 12 or (dt.hour == 13 and dt.minute <= 15)


def side_of(action: str, raw: Dict[str, Any]) -> str:
    action_u = (action or "").upper()
    if "CALL" in action_u:
        return "LONG"
    if "PUT" in action_u:
        return "SHORT"
    side = (raw or {}).get("side")
    if side:
        return str(side).upper()
    verdict = (raw or {}).get("verdict", "")
    vu = str(verdict).upper()
    if "PUT" in vu:
        return "SHORT"
    if "CALL" in vu:
        return "LONG"
    return "UNKNOWN"


def stage_veto(action: str, align: str, side: str) -> Optional[str]:
    align_u = (align or "").lower()
    is_stg4 = "stg4" in align_u or "decline" in align_u
    is_stg2 = "stg2" in align_u or "advance" in align_u
    if side == "LONG" and is_stg4:
        return "COUNTER-STAGE STG4"
    if side == "SHORT" and is_stg2:
        return "COUNTER-STAGE STG2"
    return None


def recorded_gate(decision: str) -> str:
    d = (decision or "").upper()
    for tag in GATE_TAGS:
        if tag in d:
            return tag
    return "LLM/OTHER"


def parse_plan(plan: str) -> Dict[str, Optional[float]]:
    """Parse entry / stop / T1 out of a plan string.

    Formats seen in the DB:
      ENTER alerts: "Held 355.19 · Stop 353.91 · T1 359.88"
      EXIT alerts:  "In 353.37 · Stop 352.11 · T1 354.98 (R:R 1.3) · EM cap 358.39"
    """
    def first(patterns: List[str]) -> Optional[float]:
        for pat in patterns:
            m = re.search(pat, plan or "")
            if not m:
                continue
            try:
                return float(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                return None
        return None

    return {
        "entry": first([r"Held\s+([\d,]+(?:\.\d+)?)", r"\bIn\s+([\d,]+(?:\.\d+)?)"]),
        "stop": first([r"Stop\s+([\d,]+(?:\.\d+)?)"]),
        "t1": first([r"\bT1\s+([\d,]+(?:\.\d+)?)"]),
    }


def theoretical_outcome(side: str, plan: Dict[str, Optional[float]], exit_price: Optional[float]) -> str:
    if exit_price is None:
        return "NO SAME-DAY EXIT EVENT"
    entry, stop, t1 = plan["entry"], plan["stop"], plan["t1"]
    if t1 is None or stop is None:
        return f"EXIT {exit_price:.2f} (INCOMPLETE PLAN)"
    if side == "SHORT":
        if exit_price <= t1:
            return f"HIT T1 {t1:.2f} (WIN)"
        if exit_price >= stop:
            return f"STOP {stop:.2f} (LOSS)"
    else:
        if exit_price >= t1:
            return f"HIT T1 {t1:.2f} (WIN)"
        if exit_price <= stop:
            return f"STOP {stop:.2f} (LOSS)"
    return f"EXIT {exit_price:.2f} BETWEEN T1 {t1:.2f} / STOP {stop:.2f}"


def same_side_open_count(
    events: List[tuple], date_str: str, symbol: str, side: str, t_naive: datetime
) -> int:
    """Count same-side open intraday positions at t (from trade_events).

    A symbol is open if it has an ENTRY before t with no EXIT before t.
    SCALE_OUT keeps the position open (partial quantity).
    """
    if side in ("", "UNKNOWN") or symbol is None:
        return 0
    open_sides = {}
    for sym, event_type, ts, details, _price in events:
        dt = ts_to_naive_et(ts)
        if dt is None or dt >= t_naive or et_date_of(dt) != date_str:
            continue
        if event_type.upper() == "ENTRY":
            side_j = (json.loads(details) if details and str(details).startswith("{") else {}).get("side")
            if side_j:
                open_sides[sym.upper()] = str(side_j).upper()
        elif event_type.upper() == "EXIT":
            open_sides.pop(sym.upper(), None)
    return sum(
        1 for s, sd in open_sides.items() if s != symbol.upper() and sd == side
    )


def day_pause_fired(
    conn: sqlite3.Connection, date_str: str, t_naive: datetime
) -> bool:
    """Replicates the DAY PAUSE gate against DB exits strictly before the alert time."""
    t_ts = t_naive.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """
        SELECT timestamp, raw_payload
        FROM alerts
        WHERE date = ?
          AND strategy = 'Intraday'
          AND (action LIKE ? OR action LIKE ? OR action LIKE ?)
          AND timestamp < ?
        ORDER BY timestamp DESC
        LIMIT 6
        """,
        (date_str, "%EXIT%", "%STOP%", "%CUT%", t_ts),
    )
    loss_count = 0
    latest_loss_time = None
    for ts_str, raw_str in cur.fetchall():
        raw_json = (
            json.loads(raw_str)
            if isinstance(raw_str, str) and raw_str.startswith("{")
            else {}
        )
        why = (str(raw_json.get("exit_why", "")) + " " + str(raw_json.get("act_now", ""))).lower()
        pnl = float(raw_json.get("session_pnl", 0) or 0)
        is_loss = "stop" in why or "loss" in why or pnl < 0
        if is_loss:
            loss_count += 1
            loss_dt = ts_to_naive_et(ts_str)
            if loss_dt is not None and latest_loss_time is None:
                latest_loss_time = loss_dt
        else:
            break
    if loss_count >= 3:
        return True
    return False


def simulate_gates(
    conn: sqlite3.Connection,
    events: List[tuple],
    date_str: str,
    symbol: str,
    action: str,
    score: float,
    grade: str,
    align: str,
    t_naive: datetime,
    env: Dict[str, Any],
    side: str,
) -> Dict[str, List[str]]:
    """Return {'old': [...], 'new': [...]} of gates that fire for this alert."""
    gates_old: List[str] = []
    gates_new: List[str] = []

    # 1. Quality gate
    if grade == "B" or score < 80:
        gates_old.append("QUALITY (GRADE B / <80)")
    if score < env["GRADE_B"]:
        gates_new.append("QUALITY (< %d)" % env["GRADE_B"])

    # 2. Stage alignment (identical in both)
    stage = stage_veto(action, align, side)
    if stage:
        gates_old.append(stage)
        gates_new.append(stage)

    # 3. Ticker exclusion (identical in both; env-driven both eras)
    excluded = {t.strip().upper() for t in os.getenv("INTRADAY_EXCLUDED_TICKERS", "").split(",") if t.strip()}
    if symbol.upper() in excluded:
        gates_old.append("EXCLUDED TICKER")
        gates_new.append("EXCLUDED TICKER")

    # 4. Mid-morning exhaustion
    if in_mid_morning(t_naive) and score < 90:
        gates_old.append("MID-MORNING (<90)")
    if in_mid_morning(t_naive) and score < env["MID_MORNING"]:
        gates_new.append("MID-MORNING (< %d)" % env["MID_MORNING"])

    # 5. Max exposure (count >= 2 old vs >= cap new)
    same_side = same_side_open_count(events, date_str, symbol, side, t_naive)
    if side not in ("", "UNKNOWN") and same_side >= 2:
        gates_old.append("MAX EXPOSURE (%d open)" % same_side)
    if side not in ("", "UNKNOWN") and same_side >= env["MAX_SIDE"]:
        gates_new.append("MAX EXPOSURE (%d open)" % same_side)

    # 6. Lunch chop (identical in both eras)
    if in_lunch(t_naive) and score < 85:
        gates_old.append("LUNCH CHOP (<85)")
        gates_new.append("LUNCH CHOP (<85)")

    # 7. DAY PAUSE: mandatory in both eras (trigger: 3 losses/session, clears on A+)
    if day_pause_fired(conn, date_str, t_naive):
        gates_old.append("DAY PAUSE (2 losses / 45m)")
        gates_new.append("DAY PAUSE (3 losses / session)")

    return {"old": gates_old, "new": gates_new}


# --------------------------------------------------------------------------
# DB access
# --------------------------------------------------------------------------

def load_events(conn: sqlite3.Connection) -> Dict[str, List[tuple]]:
    """All trade_events grouped by ET date: (symbol, event_type, timestamp, details, price)."""
    rows = conn.execute("SELECT symbol, event_type, timestamp, details, price FROM trade_events").fetchall()
    grouped: Dict[str, List[tuple]] = {}
    for sym, event_type, ts, details, price in rows:
        dt = ts_to_naive_et(ts)
        if dt is None:
            continue
        grouped.setdefault(et_date_of(dt), []).append((str(sym).upper(), str(event_type).upper(), ts, details, price))
    return grouped


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def next_exit_event(
    events_for_date: List[tuple], conn: sqlite3.Connection, symbol: str, date_str: str, t_naive: datetime
) -> Dict[str, Any]:
    """Next same-day EXIT for the symbol after the alert time.

    trade_events preferred (carries net_pnl), alerts table as fallback.
    """
    for sym, event_type, ts, details, price in events_for_date:
        if sym != symbol.upper():
            continue
        if event_type != "EXIT":
            continue
        dt = ts_to_naive_et(ts)
        if dt is None or dt <= t_naive:
            continue
        exit_price = float(price) if price is not None else None
        return {
            "ts": ts,
            "dt": dt,
            "price": exit_price,
            "details": details,
            "source": "trade_events",
        }
    cur = conn.execute(
        """
        SELECT timestamp, alert_price
        FROM alerts
        WHERE symbol = ? AND date = ?
          AND (action LIKE ? OR action LIKE ? OR action LIKE ?)
          AND timestamp > ?
        ORDER BY timestamp ASC
        LIMIT 1
        """,
        (symbol.upper(), date_str, "%EXIT%", "%STOP%", "%FLATTEN%", t_naive.strftime("%Y-%m-%d %H:%M:%S")),
    )
    row = cur.fetchone()
    if row is None:
        return {}
    return {"ts": row[0], "dt": ts_to_naive_et(row[0]), "price": row[1], "details": {}, "source": "alerts"}


def build_report_rows(conn: sqlite3.Connection, date_str: Optional[str], all_dates: bool) -> List[Dict[str, Any]]:
    env = {
        "GRADE_B": int(os.getenv("GRADE_B_VETO_THRESHOLD", "65")),
        "MID_MORNING": int(os.getenv("MID_MORNING_MIN_SCORE", "85")),
        "MAX_SIDE": int(os.getenv("MAX_CONCURRENT_SAME_SIDE", "3")),
    }

    conn.row_factory = sqlite3.Row
    if all_dates:
        date_list = sorted(r[0] for r in conn.execute("SELECT DISTINCT date FROM alerts ORDER BY date ASC").fetchall() if r[0])
    else:
        date_list = [] if date_str is None else [date_str]

    events_by_date = load_events(conn)

    rows: List[Dict[str, Any]] = []
    for d in date_list:
        cur = conn.execute(
            """
            SELECT message_id, date, timestamp, symbol, action, strategy, score, grade, plan,
                   alert_price, llm_decision, raw_payload
            FROM alerts
            WHERE llm_decision LIKE ? AND date = ?
            ORDER BY timestamp ASC
            """,
            ("%STAND ASIDE%", d),
        )
        for a in cur.fetchall():
            a = dict(a)
            raw_json: Dict[str, Any] = {}
            raw = a.get("raw_payload")
            if isinstance(raw, str) and raw.startswith("{"):
                try:
                    raw_json = json.loads(raw)
                except json.JSONDecodeError:
                    raw_json = {}

            try:
                score = float(a.get("score") or 75)
            except (ValueError, TypeError):
                score = 75.0
            score = int(score) if score == int(score) else score

            side = side_of(a.get("action"), raw_json)
            t_naive = ts_to_naive_et(a.get("timestamp"))
            if t_naive is None:
                continue
            date = a.get("date") or et_date_of(t_naive)

            gates = simulate_gates(
                conn,
                events_by_date.get(date, []),
                date,
                a.get("symbol", ""),
                a.get("action", ""),
                score,
                a.get("grade", "A"),
                a.get("align", ""),
                t_naive,
                env,
                side,
            )

            exit_event = next_exit_event(
                events_by_date.get(date, []), conn, a.get("symbol", ""), date, t_naive
            )
            exit_price = exit_event.get("price")

            plan = parse_plan(a.get("plan") or "")
            outcome = theoretical_outcome(side, plan, exit_price)
            net_pnl = None
            details_j = {}
            if exit_event.get("details") and str(exit_event["details"]).startswith("{"):
                try:
                    details_j = json.loads(exit_event["details"])
                except json.JSONDecodeError:
                    details_j = {}
            if isinstance(details_j.get("net_pnl"), (int, float)):
                net_pnl = float(details_j["net_pnl"])

            gates_new = gates["new"]
            if recorded_gate(a.get("llm_decision")) == "LLM/OTHER" and not gates_new:
                verdict = "RE-ROLL TO LLM"
            elif gates_new:
                verdict = "STILL FILTERED"
            else:
                verdict = "UNLOCKED"

            rows.append({
                "date": date,
                "time": a.get("timestamp", "")[:16],
                "symbol": a.get("symbol", ""),
                "side": side,
                "score": score,
                "grade": a.get("grade") or "",
                "recorded_gate": recorded_gate(a.get("llm_decision")),
                "old_gates": "; ".join(gates["old"]) or "-",
                "new_gates": "; ".join(gates_new) or "-",
                "verdict": verdict,
                "next_exit_time": exit_event.get("ts", ""),
                "next_exit_price": exit_price if exit_price is not None else "",
                "entry": plan["entry"] if plan["entry"] is not None else "",
                "stop": plan["stop"] if plan["stop"] is not None else "",
                "t1": plan["t1"] if plan["t1"] is not None else "",
                "theoretical_outcome": outcome,
                "actual_net_pnl": f"{net_pnl:+.2f}" if net_pnl is not None else "",
            })
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


# --------------------------------------------------------------------------
# Console output
# --------------------------------------------------------------------------

def print_report(rows: List[Dict[str, Any]], env: Dict[str, Any]) -> None:
    if not rows:
        print("No STAND ASIDE (vetoed) alerts found in the database for the selected range.")
        return

    cols = [
        ("date", "DATE"),
        ("time", "TIME"),
        ("symbol", "SYM"),
        ("side", "SIDE"),
        ("score", "SCORE"),
        ("recorded_gate", "RECORDED GATE"),
        ("new_gates", "NEW GATES (WILL FIRE)"),
        ("verdict", "VERDICT"),
        ("theoretical_outcome", "MARKET OUTCOME AFTER VETO"),
    ]
    widths = {c: 0 for _, c in cols}
    for r in rows:
        for c, _ in cols:
            widths[c] = max(widths.get(c, 0), len(str(r.get(c) or "")))
    # cap wide columns
    for c in ("recorded_gate", "new_gates", "theoretical_outcome"):
        widths[c] = min(widths.get(c, 20), 34)

    header = " | ".join(str(label).rjust(widths[c]) for (c, label) in cols)
    sep = "+-".join("-" * widths[c] for c, _ in cols)
    print(sep)
    print(" | ".join(str(label).center(widths[c]) for (c, label) in cols))
    print(sep)
    for r in rows:
        vals = []
        for c, _ in cols:
            v = str(r.get(c) or "")
            if len(v) > widths[c]:
                v = v[: widths[c] - 3] + "..."
            vals.append(v.ljust(widths[c]))
        print(" | ".join(vals))
    print(sep)
    print()

    # summary
    unlocked = [r for r in rows if r["verdict"] == "UNLOCKED"]
    still = [r for r in rows if r["verdict"] == "STILL FILTERED"]
    reroll = [r for r in rows if r["verdict"] == "RE-ROLL TO LLM"]

    print(f"RULE SET (NEW): GRADE_B_VETO_THRESHOLD={env['GRADE_B']}  MID_MORNING_MIN_SCORE={env['MID_MORNING']}  MAX_CONCURRENT_SAME_SIDE={env['MAX_SIDE']}")
    print(f"TOTAL VETES: {len(rows)}")
    print(f"  UNLOCKED BY NEW RULES      : {len(unlocked)}")
    print(f"  STILL FILTERED BY NEW RULES: {len(still)}")
    print(f"  RE-ROLL TO LLM (no hard gate): {len(reroll)}")
    print()

    print("UNLOCKED — trades the AI would have taken now:")
    if not unlocked:
        print("  (none)")
    for r in unlocked:
        pnl = f"  actual net {r['actual_net_pnl']}" if r["actual_net_pnl"] else ""
        print(f"  {r['symbol']:<6} {r['time']}  score={int(r['score'])} {r['grade']:>2}  {r['theoretical_outcome']}  {pnl}")

    print()
    print("STILL FILTERED — gates that fire under the new rules:")
    if not still:
        print("  (none)")
    for r in still:
        print(f"  {r['symbol']:<6} {r['time']}  {r['new_gates']}")

    # opportunity-cost summary over UNLOCKED entries with a following exit
    hits = sum(1 for r in unlocked if "WIN" in r["theoretical_outcome"])
    losses = sum(1 for r in unlocked if "LOSS" in r["theoretical_outcome"])
    mixed = sum(1 for r in unlocked if "BETWEEN" in r["theoretical_outcome"])
    no_exit = sum(1 for r in unlocked if r["theoretical_outcome"] == "NO SAME-DAY EXIT EVENT")
    total_unlocked_with_exit = hits + losses + mixed
    if total_unlocked_with_exit:
        win_rate = hits / total_unlocked_with_exit * 100
        print()
        print(f"OPPORTUNITY COST: of {len(unlocked)} unlocked entries, {total_unlocked_with_exit} had a same-day exit event:")
        print(f"  HIT T1 (theoretical WIN) : {hits}")
        print(f"  STOP (theoretical LOSS)  : {losses}")
        print(f"  BETWEEN T1 and STOP      : {mixed}")
        print(f"  No same-day exit         : {no_exit}")
        print(f"  Theoretical win rate     : {win_rate:.0f}%")


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------

def write_csv(rows: List[Dict[str, Any]]) -> Optional[Path]:
    out_dir = config.BASE_DIR / "data" / "veto_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "veto_audit.csv"
    if not rows:
        return None
    fieldnames = [
        "date", "time", "symbol", "side", "score", "grade", "recorded_gate",
        "old_gates", "new_gates", "verdict", "next_exit_time", "next_exit_price",
        "entry", "stop", "t1", "theoretical_outcome", "actual_net_pnl",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return out_path


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Audit AI STAND ASIDE vetoes: old vs new gates + market outcome.")
    ap.add_argument("--date", help="ET date to audit (YYYY-MM-DD). Default: today ET.")
    ap.add_argument("--all", action="store_true", help="Audit every recorded date.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s | %(message)s")

    date_str = args.date if args.date else None
    if not args.all and date_str is None:
        date_str = datetime.now(ET).strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        rows = build_report_rows(conn, None if args.all else date_str, args.all)

        env = {
            "GRADE_B": int(os.getenv("GRADE_B_VETO_THRESHOLD", "65")),
            "MID_MORNING": int(os.getenv("MID_MORNING_MIN_SCORE", "85")),
            "MAX_SIDE": int(os.getenv("MAX_CONCURRENT_SAME_SIDE", "3")),
        }

        print_report(rows, env)

        csv_path = write_csv(rows)
        if csv_path:
            print(f"CSV written to: {csv_path}")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
