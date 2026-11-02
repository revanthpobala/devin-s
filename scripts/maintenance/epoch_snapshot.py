#!/usr/bin/env python3
"""Snapshot data/ at a record epoch so the pre-epoch ledgers survive outside the live folder.

Every non-empty SQLite DB is copied with the online backup API (consistent while the backend runs) and
integrity-checked; top-level JSON/JSONL/CSV files are copied, except credentials. Stray non-data files are
moved into the snapshot. Nothing in the live DBs changes: the record views start at config.RECORD_EPOCH.

  python scripts/maintenance/epoch_snapshot.py                 # dry run
  python scripts/maintenance/epoch_snapshot.py --execute
Then archive old day folders/logs/backups with archive_repo_data.py.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402

DATA = ROOT / "data"
DEST = Path(os.getenv("STOCK_ARCHIVE_DIR", "D:/My-Projects/Stock_Archive"))
SECRETS = ("token", "secret", "credential", "service_account")
STRAY = ("repair_20261024.py", "verify_modal_dp.py", "darkpool_verified_glw.png")


def plan():
    dbs = [p for p in sorted(DATA.glob("*.db")) if p.stat().st_size > 0]
    files = [p for p in sorted(DATA.iterdir()) if p.is_file() and p.suffix in (".json", ".jsonl", ".csv")
             and not any(s in p.name.lower() for s in SECRETS)]
    stray = [DATA / n for n in STRAY if (DATA / n).exists()]
    return dbs, files, stray


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dest", type=Path, default=DEST)
    a = ap.parse_args()
    out = a.dest / f"epoch_{config.RECORD_EPOCH}"
    dbs, files, stray = plan()
    mb = lambda ps: sum(p.stat().st_size for p in ps) / 1e6
    print(f"snapshot -> {out}")
    print(f"  {len(dbs)} DBs ({mb(dbs):.1f} MB): {' '.join(p.name for p in dbs)}")
    print(f"  {len(files)} files ({mb(files):.1f} MB)")
    print(f"  move {len(stray)} stray: {' '.join(p.name for p in stray)}")
    if not a.execute:
        print("dry run; add --execute")
        return
    (out / "db").mkdir(parents=True, exist_ok=True)
    (out / "files").mkdir(exist_ok=True)
    for p in dbs:
        dst = out / "db" / p.name
        src = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
        tgt = sqlite3.connect(dst)
        with tgt:
            src.backup(tgt)
        ok = tgt.execute("PRAGMA integrity_check").fetchone()[0]
        src.close()
        tgt.close()
        print(f"  {p.name}: {ok}")
        if ok != "ok":
            sys.exit(f"integrity check failed on {dst}")
    for p in files:
        shutil.copy2(p, out / "files" / p.name)
    for p in stray:
        shutil.copy2(p, out / "files" / p.name)
        if (out / "files" / p.name).stat().st_size == p.stat().st_size:
            p.unlink()
    print("done")


if __name__ == "__main__":
    main()
