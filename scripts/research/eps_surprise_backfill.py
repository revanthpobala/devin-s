"""Resumable Alpha Vantage EARNINGS backfill into data/research/eps_surprise.json.

Priority: held names (schwab_positions), watch_targets, then the tickers with the most big drops in the latest
move_postmortem report, then the rest. Stops cleanly at the daily quota. Usage: python ... [--limit N]
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.clients.alphavantage_client import fetch_earnings_history  # noqa: E402

DATA = next((d for d in (ROOT / "data", Path("/Volumes/Stock/data")) if (d / "universe" / "profiles.json").is_file()), ROOT / "data")
CACHE = ROOT / "data" / "research" / "eps_surprise.json"
UNIVERSE = DATA / "universe" / "profiles.json"


def _names(db: Path, sql: str) -> list[str]:
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=5)
        out = [str(r[0]).upper() for r in c.execute(sql) if r[0]]
        c.close()
        return out
    except sqlite3.Error:
        return []


def priority_order(universe: list[str]) -> list[str]:
    uni = set(universe)
    held = _names(DATA / "schwab_portfolio.db", "select distinct symbol from schwab_positions")
    watch = _names(DATA / "research_watch.db", "select ticker from watch_targets")
    drops: list[str] = []
    reports = sorted(glob.glob(str(ROOT / "data" / "research" / "move_postmortem_*.json")))
    if reports:
        by = json.loads(Path(reports[-1]).read_text()).get("big_drops", {}).get("by_ticker", {})
        drops = [t for t, _ in sorted(by.items(), key=lambda kv: -kv[1])]
    order, seen = [], set()
    for t in held + watch + drops + sorted(uni):
        if t in uni and t not in seen:
            seen.add(t)
            order.append(t)
    return order


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max tickers to fetch this run")
    args = ap.parse_args()

    universe = sorted(json.loads(UNIVERSE.read_text()).keys()) if UNIVERSE.is_file() else []
    cache = json.loads(CACHE.read_text()) if CACHE.is_file() else {}
    todo = [t for t in priority_order(universe) if t not in cache]
    if args.limit is not None:
        todo = todo[: args.limit]
    fetched = 0
    for t in todo:
        quarters = fetch_earnings_history(t)
        if quarters is None:
            print(f"quota or rate limit reached after {fetched} tickers; rerun tomorrow")
            break
        cache[t] = {"fetched": date.today().isoformat(), "quarters": quarters}
        fetched += 1
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    print(f"cached {len(cache)} tickers, fetched {fetched} this run, {len(todo) - fetched} pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
