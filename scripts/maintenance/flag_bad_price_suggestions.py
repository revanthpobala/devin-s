#!/usr/bin/env python3
"""Flag suggestions whose stored spot or stop is far from that day's Yahoo daily close.

A wrong TradingView listing (HAL 4725 vs a $35 close) or a later corporate action leaves levels that no
daily bar can score. Flagged rows get gate_status='BAD_PRICE' and drop out of the scorers; nothing is deleted.
Yahoo is the reference, not get_bars_since_date: that reads the local datawindow CSVs first, which carry the
same wrong listing. BAD_PRICE rows that now check out are restored to their previous gate.

  python scripts/maintenance/flag_bad_price_suggestions.py            # dry run
  python scripts/maintenance/flag_bad_price_suggestions.py --execute
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tracking.watch_manager import _db_lock, _get_connection  # noqa: E402

MAX_SPOT_DEV = 0.15
STOP_BAND = (0.25, 1.5)
PREFIX = re.compile(r"^(?:was (\S+); )?spot [\d.]+ vs daily close [\d.]+; ")


def _closes(tickers: list, start: str):
    import yfinance as yf

    import time

    import pandas as pd

    frames = []
    todo = [t.replace(".", "-") for t in tickers]
    for attempt in range(3):
        if not todo:
            break
        if attempt:
            time.sleep(20)
        missing = []
        for i in range(0, len(todo), 50):
            chunk = todo[i:i + 50]
            df = yf.download(chunk, start=start, auto_adjust=False, progress=False, threads=False)["Close"]
            if not hasattr(df, "columns"):
                df = df.to_frame(chunk[0])
            got = df.dropna(axis=1, how="all")
            frames.append(got)
            missing += [t for t in chunk if t not in got.columns]
        todo = missing
    if todo:
        print(f"no Yahoo bars for {len(todo)} ticker(s), not checked: {' '.join(todo)}")
    df = pd.concat(frames, axis=1) if frames else pd.DataFrame()
    df.columns = [str(c).replace("-", ".") for c in df.columns]
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df


def check() -> tuple:
    with _db_lock, _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, ticker, date, spot_at_signal, stop, gate_status, gate_reasons FROM suggestions WHERE spot_at_signal > 0"
        ).fetchall()
    rows = [tuple(r) for r in rows]
    start = (datetime.strptime(min(str(r[2])[:10] for r in rows), "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    px = _closes(sorted({str(r[1]).upper() for r in rows}), start)
    flag, restore = [], []
    for sid, tk, d, spot, stop, gate, reasons in rows:
        if tk.upper() not in px.columns:
            continue
        s = px[tk.upper()].dropna()
        s = s[s.index <= str(d)[:10]]
        if s.empty:
            continue
        close = float(s.iloc[-1])
        bad = abs(float(spot) / close - 1) > MAX_SPOT_DEV or (
            stop is not None and float(stop) > 0 and not STOP_BAND[0] < float(stop) / close < STOP_BAND[1])
        if bad and gate != "BAD_PRICE":
            flag.append((sid, tk, str(d)[:10], float(spot), close, gate, reasons))
        elif not bad and gate == "BAD_PRICE":
            restore.append((sid, tk, str(d)[:10], float(spot), close, gate, reasons))
    return flag, restore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    flag, restore = check()
    for sid, tk, d, spot, close, *_ in flag:
        print(f"flag    {sid:>6} {tk:<6} {d} spot {spot:.2f} vs close {close:.2f}")
    for sid, tk, d, spot, close, *_ in restore:
        print(f"restore {sid:>6} {tk:<6} {d} spot {spot:.2f} vs close {close:.2f}")
    print(f"{len(flag)} to flag, {len(restore)} to restore")
    if not a.execute:
        return
    with _db_lock, _get_connection() as conn:
        for sid, tk, d, spot, close, gate, reasons in flag:
            conn.execute(
                "UPDATE suggestions SET gate_status = 'BAD_PRICE', gate_reasons = ? WHERE id = ?",
                (f"was {gate or 'NONE'}; spot {spot:.2f} vs daily close {close:.2f}; {reasons or ''}", sid),
            )
        for sid, tk, d, spot, close, gate, reasons in restore:
            m = PREFIX.match(reasons or "")
            prev = (m.group(1) if m and m.group(1) else "REJECTED_BY_GATE")
            conn.execute(
                "UPDATE suggestions SET gate_status = ?, gate_reasons = ? WHERE id = ?",
                (None if prev == "NONE" else prev, PREFIX.sub("", reasons or ""), sid),
            )
        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
