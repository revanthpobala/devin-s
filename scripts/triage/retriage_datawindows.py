"""
scripts/triage/retriage_datawindows.py

Batch re-run the DETERMINISTIC Data Window pre-filter over every scraped
*_datawindow.json for a given date, using the current (fixed) filter code.
Pure-technical only: no news is fetched, so the verdict reflects the math gate
alone (PASS/WATCH/CUT + send_for_deep_research). Use it to see which names the fix
flips PASS -> WATCH (e.g. the KVUE poor-R:R class) before spending Minimax passes.

Usage:
    python3 scripts/triage/retriage_datawindows.py [YYYY-MM-DD] [--only TICKER,TICKER]

Reads:  data/raw/<date>/<TICKER>_datawindow.json
Writes: nothing (read-only report to stdout).
"""

import argparse
import glob
import json
import sys
from pathlib import Path

# Make `src` importable when run from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.logic.data_window_filter import (  # noqa: E402
    _CORE_FIELDS,
    parse_data_window,
    run_data_window_filter,
)


def _ticker_from_path(p: Path) -> str:
    # "<TICKER>_datawindow.json" -> "<TICKER>"; handles "EXCH_SYM_datawindow.json".
    return p.name[: -len("_datawindow.json")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default="2026-07-14")
    ap.add_argument("--only", default="", help="comma-separated tickers to restrict to")
    args = ap.parse_args()

    raw_dir = REPO_ROOT / "data" / "raw" / args.date
    triage_root = REPO_ROOT / "data" / "triage" / args.date
    files = sorted(glob.glob(str(raw_dir / "*_datawindow.json")))
    # Local-research segregation moves data windows into _DEEP_RESEARCH (the only
    # triage subfolder the pipeline creates), so include it too.
    for sub in ("_DEEP_RESEARCH", "force"):
        sd = triage_root / sub
        if sd.exists():
            files += glob.glob(str(sd / "*_datawindow.json"))
    only = {t.strip().upper() for t in args.only.split(",") if t.strip()}

    if not files:
        print(f"No *_datawindow.json found in {raw_dir} or {triage_root}/<triage subfolders>")
        print("Drop the scraped Data Window files there and re-run.")
        return

    rows = []
    bad = []
    for fp in files:
        p = Path(fp)
        ticker = _ticker_from_path(p)
        if only and ticker.upper() not in only:
            continue
        try:
            dw = json.load(open(fp))
        except Exception as e:
            bad.append((ticker, f"unreadable json: {e}"))
            continue

        out = run_data_window_filter(ticker, dw)
        if out.get("bad_data"):
            f = parse_data_window(dw)
            missing = [k for k in _CORE_FIELDS if f.get(k) is None]
            bad.append((ticker, "missing core: " + ",".join(missing)))
            continue
        rows.append(out)

    # Sort: PASS first, then WATCH, then CUT; within, by conviction desc.
    order = {"PASS": 0, "WATCH": 1, "CUT": 2, None: 3}
    rows.sort(key=lambda r: (order.get(r["triage"], 3), -(r["conviction"] or 0)))

    print(f"\n=== Re-triage {args.date} - {len(rows)} tickers (pure-technical, fixed filter) ===\n")
    hdr = f"{'TICKER':10} {'SIDE':5} {'MODE':13} {'TRIAGE':5} {'MSCORE':>7} {'RR':>6} {'DIRP':>6} {'REG':>3}   FLAGS/REASON"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        rr = f"{r['rr']:.2f}" if r["rr"] is not None else "-"
        dirp = f"{r['dir_prob']:.0f}" if r.get("dir_prob") is not None else "-"
        reg = int(round(r["regime"])) if r.get("regime") is not None else "-"
        _ms = r.get("rank_model_score")
        ms = f"{_ms:7.4f}" if _ms is not None else "      -"
        tail = ",".join(r["flags"]) or r.get("reason", "")
        print(
            f"{r['ticker']:10} {str(r['chosen_side'] or '-'):5} {r['mode']:13} "
            f"{r['triage']:5} {ms:>7} {rr:>6} {dirp:>6} {str(reg):>3}  {tail}"
        )

    # Summary.
    counts = {}
    for r in rows:
        counts[r["triage"]] = counts.get(r["triage"], 0) + 1
    print("\n--- SUMMARY ---")
    print("counts:", counts)
    if bad:
        print(f"\nbad_data / unreadable ({len(bad)}):")
        for tk, why in bad:
            print(f"  {tk}: {why}")


if __name__ == "__main__":
    main()
