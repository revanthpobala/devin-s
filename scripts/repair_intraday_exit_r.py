"""One-time repair of intraday_signals R columns corrupted before the Oct 2026 ledger fix.

Three write bugs (fixed in alert_db.upsert_intraday_signal, main.py and position_monitor):
  1. Pine zero-fills "exit_r" on every ENTRY payload. main.py stored it as pine_exit_r, and the
     upsert copied pine_exit_r into exit_r, so ENTRY alerts carried a phantom 0.0R.
  2. The upsert's ON CONFLICT preferred the incoming exit_r, so an ENTRY alert processed after a
     fast close overwrote the realized R with that 0.0 (META 10-15: -0.26R became 0.0).
  3. The taken row was written after the monitor thread started; a same-second close found no
     row and its realized R was lost (SPY 10-28, MSFT 10-27, GOOGL 10-16).

What this script changes (only with --write; a backup is taken first):
  A. taken rows: exit_r := the realized exit_r from the matching trade_events EXIT (by trade_id).
  B. untaken rows: exit_r := NULL where it is a copy of pine_exit_r (bug 1). There was no position,
     so there is no live R. The scoreboard reads pine_exit_r first, so signal stats do not move.
  C. untaken rows: pine_exit_r := NULL where it is 0 and no exit was ever recorded (exit_why empty):
     the 0 came from the ENTRY payload, not from a Pine exit.
Taken rows with pine_exit_r = 0 are only reported: a monitor close also writes exit_why, so a
phantom Pine 0 cannot be told apart from a real Pine scratch there.

Usage (run on the machine that owns the database; services may stay up, writes are short):
  python scripts/repair_intraday_exit_r.py              # dry run, prints every planned change
  python scripts/repair_intraday_exit_r.py --write      # backup + apply
  python scripts/repair_intraday_exit_r.py --db path/to/trading_alerts.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402

EPS = 1e-6


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < EPS


def _realized_exit_r(conn: sqlite3.Connection) -> dict:
    """trade_id -> realized exit_r from the latest trade_events EXIT that carries one."""
    out: dict = {}
    for trade_id, details in conn.execute(
        "SELECT trade_id, details FROM trade_events WHERE event_type = 'EXIT' AND trade_id IS NOT NULL ORDER BY id"
    ):
        try:
            det = json.loads(details) if isinstance(details, str) else (details or {})
        except (TypeError, ValueError):
            continue
        r = det.get("exit_r")
        if r is None:
            continue
        try:
            out[trade_id] = float(r)
        except (TypeError, ValueError):
            continue
    return out


def plan(conn: sqlite3.Connection) -> dict:
    realized = _realized_exit_r(conn)
    rows = conn.execute(
        "SELECT id, trade_id, ticker, date, taken, exit_r, pine_exit_r, exit_why FROM intraday_signals ORDER BY id"
    ).fetchall()
    fix_a, fix_b, fix_c, report_taken_pine0, taken_no_event = [], [], [], [], []
    for rid, tid, tkr, d, taken, exit_r, pine, why in rows:
        if taken:
            if tid in realized:
                if not _same(exit_r, realized[tid]):
                    fix_a.append((rid, tid, tkr, d, exit_r, realized[tid]))
            else:
                taken_no_event.append((rid, tid, tkr, d))
            if pine is not None and _same(pine, 0.0):
                report_taken_pine0.append((rid, tkr, d))
            continue
        if exit_r is not None and pine is not None and _same(exit_r, pine):
            fix_b.append((rid, tkr, d, exit_r))
        if pine is not None and _same(pine, 0.0) and not (why or "").strip():
            fix_c.append((rid, tkr, d))
    return {"A": fix_a, "B": fix_b, "C": fix_c, "taken_pine0": report_taken_pine0, "taken_no_event": taken_no_event}


def backup(db: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{db.stem}_pre_exit_r_repair_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(str(db), timeout=30)
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)  # consistent snapshot, WAL included
    src.close()
    dst.close()
    return dest


def apply(conn: sqlite3.Connection, p: dict) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.executemany("UPDATE intraday_signals SET exit_r = ?, updated_at = ? WHERE id = ?",
                         [(new, now, rid) for rid, _t, _k, _d, _old, new in p["A"]])
        conn.executemany("UPDATE intraday_signals SET exit_r = NULL, updated_at = ? WHERE id = ?",
                         [(now, rid) for rid, *_ in p["B"]])
        conn.executemany("UPDATE intraday_signals SET pine_exit_r = NULL, updated_at = ? WHERE id = ?",
                         [(now, rid) for rid, *_ in p["C"]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=config.alerts_db_path())
    ap.add_argument("--write", action="store_true", help="back up the database, then apply the changes")
    ap.add_argument("--backup-dir", type=Path, default=config.BASE_DIR / "data" / "backups")
    a = ap.parse_args()
    if not a.db.exists():
        print(f"database not found: {a.db}")
        return 2

    conn = sqlite3.connect(str(a.db), timeout=30)
    p = plan(conn)

    print(f"database: {a.db}")
    print(f"\nA. taken rows whose exit_r differs from the realized trade_events exit_r: {len(p['A'])}")
    for rid, tid, tkr, d, old, new in p["A"]:
        print(f"   id {rid:<5} {tkr:<6} {d}  exit_r {old!s:>8} -> {new:+.4f}   ({tid})")
    print(f"\nB. untaken rows with exit_r copied from pine_exit_r (-> NULL): {len(p['B'])}")
    print(f"C. untaken rows with phantom pine_exit_r = 0 and no recorded exit (-> NULL): {len(p['C'])}")
    print(f"\nReport only: taken rows with pine_exit_r = 0 (phantom or real scratch, not changed): {len(p['taken_pine0'])}")
    for rid, tkr, d in p["taken_pine0"]:
        print(f"   id {rid:<5} {tkr:<6} {d}")
    print(f"Report only: taken rows with no trade_events EXIT to repair from: {len(p['taken_no_event'])}")

    if not a.write:
        print("\nDry run. Re-run with --write to back up and apply.")
        conn.close()
        return 0

    conn.close()
    dest = backup(a.db, a.backup_dir)
    print(f"\nbackup written: {dest}")
    conn = sqlite3.connect(str(a.db), timeout=30)
    apply(conn, p)
    conn.close()
    print("applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
