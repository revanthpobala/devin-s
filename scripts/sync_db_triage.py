import sqlite3
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/research_watch.db")
if not DB_PATH.exists():
    print("Database not found.")
    exit(0)

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

today_str = "2026-10-08"
updated = 0

# Scan all triage and thesis files for today
files = (
    list(glob.glob(f"data/raw/{today_str}/*_triage.json"))
    + list(glob.glob(f"data/raw/{today_str}/*/*_thesis.json"))
    + list(glob.glob(f"data/triage/{today_str}/*/*/*_thesis.json"))
)
for p in files:
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        sym = (d.get("ticker") or "").upper()
        if not sym or sym in ("CONSOLIDATE", "XYZUNKNOWN"):
            continue
        tr = d.get("triage") or {}
        verdict = tr.get("triage") if isinstance(tr, dict) else d.get("triage")
        reason = tr.get("reason") if isinstance(tr, dict) else d.get("reason")
        is_in_deep_dir = "_DEEP_RESEARCH" in str(p) or "force" in str(p)
        send_flag = bool(
            is_in_deep_dir
            or d.get("send_for_deep_research")
            or (isinstance(tr, dict) and (tr.get("send_for_deep_research") or tr.get("pursue")))
            or (isinstance(d.get("llm_data"), dict) and d["llm_data"].get("send_for_deep_research"))
        )
        if verdict:
            if send_flag:
                detail = f"{verdict} (Satisfied -> Enqueued for Deep)"
            else:
                reason_str = str(reason) if reason else "Filtered Out"
                detail = f"{verdict} ({reason_str})"
            
            c.execute(
                "UPDATE active_research_jobs SET stage_detail = ?, status = 'COMPLETED' WHERE ticker = ? AND mode = 'local_only'",
                (detail, sym)
            )
            if c.rowcount > 0:
                updated += 1
    except Exception as e:
        pass

conn.commit()
print(f"Synced {updated} local_only jobs in DB.")

# Print current state of all local_only jobs
rows = c.execute("SELECT ticker, stage_detail, started_at FROM active_research_jobs WHERE mode = 'local_only' ORDER BY started_at DESC LIMIT 25").fetchall()
for r in rows:
    print(f"  {r[0]:6s} -> {r[1]}")
conn.close()
