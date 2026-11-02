#!/usr/bin/env python3
"""Clear pre-epoch research, chat, alerts, scorecards and paper ledgers. Run with the backend stopped.

Kept: IN_TRADE watch rows and their suggestions, coverage placeholders, the open MODEL position and its events,
options/GEX/darkpool data, the Schwab portfolio, covered-call postmortems, notification dedupe, configs and
tokens, last_researched (re-research cadence). Requires the epoch_snapshot.py copy of research_watch.db.
Day folders before the epoch (data/raw, data/triage, data/artifacts, data/briefings, reports/) move into the
snapshot folder; reports/ is what the Today page and plan repair rebuild cards and watch levels from.

  python scripts/maintenance/epoch_reset.py            # dry run: counts only
  python scripts/maintenance/epoch_reset.py --execute
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402

DATA = ROOT / "data"
DEST = Path(os.getenv("STOCK_ARCHIVE_DIR", "D:/My-Projects/Stock_Archive"))
KEEP_SUG = "SELECT suggestion_id FROM watch_targets WHERE status = 'IN_TRADE' AND suggestion_id IS NOT NULL"
OPEN_TRADES = "SELECT trade_id FROM positions WHERE status = 'OPEN' AND trade_id IS NOT NULL"
PLAN = {
    "research_watch.db": [
        ("watch_targets", "status NOT IN ('IN_TRADE', 'UNRESEARCHED')"),
        ("suggestions", f"id NOT IN ({KEEP_SUG})"),
        *((t, None) for t in ("research_scorecard", "suggestion_counterfactual", "suggestion_policy", "watch_alerts",
                              "active_research_jobs", "research_timings", "copilot_chat_history",
                              "superforecasting_audits", "suggested_trades_audit", "rejected_plans",
                              "watch_targets_history", "thesis_paper_trades", "ticket_paper_trades")),
    ],
    "trading_alerts.db": [
        ("alerts", None), ("intraday_signals", None), ("research_queue", None),
        ("trade_events", f"trade_id IS NULL OR trade_id NOT IN ({OPEN_TRADES})"),
        ("positions", "status != 'OPEN'"),
    ],
    "notifications.db": [("notifications", None)],
    "thesis_ledger.db": [(t, None) for t in ("marks", "state", "thesis", "thesis_updates", "tranches")],
    "agent_trades.db": [("agent_look", None), ("agent_order", None)],
    "catalyst_ledger.db": [("catalyst", None), ("mover", None)],
}
DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _backend_up() -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8050)) == 0


def _old_entries():
    out = []
    for d in [DATA / s for s in ("raw", "triage", "artifacts", "briefings")] + [ROOT / "reports"]:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            m = DAY.match(p.name) or re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
            if (m and m.group(1) < config.RECORD_EPOCH) or (d.name == "raw" and p.name in ("test_options", "2029-01-01")):
                out.append(p)
    cache = DATA / "desk_warm_cache.json"
    return out + ([cache] if cache.exists() else [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dest", type=Path, default=DEST)
    a = ap.parse_args()
    snap = a.dest / f"epoch_{config.RECORD_EPOCH}"
    if not (snap / "db" / "research_watch.db").exists():
        sys.exit(f"no snapshot at {snap}/db; run epoch_snapshot.py --execute first")
    if a.execute and _backend_up():
        sys.exit("backend is running on :8050; stop it first")
    for db, steps in PLAN.items():
        path = DATA / db
        if not path.exists() or path.stat().st_size == 0:
            continue
        conn = sqlite3.connect(path, timeout=30)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, where in steps:
            if table not in tables:
                continue
            w = f" WHERE {where}" if where else ""
            n = conn.execute(f"SELECT COUNT(*) FROM {table}{w}").fetchone()[0]
            left = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - n
            print(f"{db:<20} {table:<28} delete {n:>6}  keep {left}")
            if a.execute and n:
                conn.execute(f"DELETE FROM {table}{w}")
        if a.execute:
            conn.commit()
            conn.execute("VACUUM")
        conn.close()
    old = _old_entries()
    print(f"move {len(old)} day folder(s)/file(s) before {config.RECORD_EPOCH} to {snap / 'folders'}")
    if not a.execute:
        print("dry run; stop the backend, then add --execute")
        return
    for p in old:
        dst = snap / "folders" / p.parent.name / p.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.copytree(p, dst, dirs_exist_ok=True) if p.is_dir() else shutil.copy2(p, dst)
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        else:
            shutil.move(str(p), str(dst))
    print("done; restart the backend")


if __name__ == "__main__":
    main()
