"""Cap already-stored alert convictions to the band their own verdict supports.

The live triage caps conviction deterministically in alert_evaluator.cap_conviction(), but
rows triaged before that cap existed keep their inflated numbers (on 2026-10-12 four rows
ran 5-6/10 behind critiques saying "no defined structural floor"). This rewrites those
decision strings in place, deriving the caps from data already on each row: the flags line
of its own playbook and whether it carries an entry mode / tactical levels.

Run once:
    python scripts/triage/cap_alert_convictions.py --date 2026-10-12
Dry run (default):
    python scripts/triage/cap_alert_convictions.py --date 2026-10-12 --apply
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tracking.alert_db import DB_PATH  # noqa: E402
from src.tracking.alert_evaluator import cap_conviction  # noqa: E402

_DECISION_RE = re.compile(
    r"^(?P<prefix>.*?\()(?P<conviction>\d+)(?P<suffix>/10\))$", re.IGNORECASE
)
_FLAGS_RE = re.compile(r"\*\*Flags\*\*:\s*(.+)")
_ENTRY_MODE_RE = re.compile(r"\*\*Entry Mode\*\*:\s*([A-Z_]+)")
_LEVELS_RE = re.compile(r"\*\*Tactical Levels\*\*:")


def _triage_label(decision: str) -> str:
    d = decision.upper()
    if "PASS" in d:
        return "PASS"
    if "CUT" in d:
        return "CUT"
    return "WATCH"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (alert date)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT message_id, symbol, llm_decision, llm_playbook FROM alerts "
        "WHERE date = ? AND llm_decision LIKE '%/10)'",
        (args.date,),
    ).fetchall()

    changed = 0
    for r in rows:
        decision = r["llm_decision"] or ""
        playbook = r["llm_playbook"] or ""
        m = _DECISION_RE.match(decision.strip())
        if not m:
            continue

        flags_m = _FLAGS_RE.search(playbook)
        key_flags = [f.strip() for f in flags_m.group(1).split(",")] if flags_m else []
        entry_m = _ENTRY_MODE_RE.search(playbook)
        entry_mode = entry_m.group(1) if entry_m else "NONE"
        has_plan = bool(_LEVELS_RE.search(playbook))

        triage = _triage_label(decision)
        old = int(m.group("conviction"))
        new = cap_conviction(
            old, triage, key_flags, entry_mode=entry_mode, has_plan=has_plan
        )
        if new == old:
            continue

        new_decision = f"{m.group('prefix')}{new}{m.group('suffix')}"
        print(
            f"{r['symbol']:6s} {decision.strip():28s} -> {new_decision.strip():28s} "
            f"flags={key_flags}"
        )
        if args.apply:
            con.execute(
                "UPDATE alerts SET llm_decision = ? WHERE message_id = ?",
                (new_decision, r["message_id"]),
            )
        changed += 1

    if args.apply:
        con.commit()
    con.close()
    print(f"{'applied' if args.apply else 'would change'}: {changed} row(s) of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
