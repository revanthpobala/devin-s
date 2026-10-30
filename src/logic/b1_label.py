"""B1 composite label — the one selection rule that passed out of sample (Pine AGENTS.md, T20, frozen 2026-10-29).

B1 = count of five conditions on the signal bar >= 3:
  1. Pine R:R at market >= 3        (Long Target - close) / (close - Long Stop Loss)
  2. Ext Z Self Relative < 0
  3. PB: close > 10-bar low, (60-bar high - close) / (close - 10-bar low) >= 3, Ext Z < 1.5
  4. Energy IV Rank Pct > 80
  5. HV20 Ann Pct below that date's universe median

Inputs 1-4 come from the research run's own Pine datawindow (data/raw/<date>/<SYM>/<SYM>_datawindow.csv).
The HV20 median is rebuilt from Yahoo daily closes for the in-sample universe (b1_universe.txt, the 532 live
names of full_v4_av2) with the Pine formula: population stdev of 20 log returns x sqrt(252) x 100, names with
close >= $20. Measured against the full_v6 Pine export, Jul-Sep 2026: mean abs diff 0.37 vol points (median range
23-34). The Pine median also requires valid Pine geometry, which Yahoo can't supply; that filter moved the median
0.26 points. Ranking label for measurement only — nothing gates on it.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

MIN_COUNT = 3
UNIVERSE_FILE = Path(__file__).with_name("b1_universe.txt")
MIN_UNIVERSE_N = 300  # fewer names with data on a date = no median (download problem)
MIN_PRICE = 20.0


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def components(dw, signal_date: str) -> Optional[Dict[str, Any]]:
    """Per-name B1 inputs from a Pine datawindow DataFrame, on the last bar dated <= signal_date."""
    if dw is None or len(dw) == 0 or "time" not in dw.columns:
        return None
    d = dw.assign(_d=dw["time"].astype(str).str[:10])
    d = d[d["_d"] <= signal_date].reset_index(drop=True)
    if len(d) < 61:
        return None
    last = d.iloc[-1]
    close = _f(last.get("close"))
    stop, tgt = _f(last.get("Long Stop Loss")), _f(last.get("Long Target"))
    rr = _f(last.get("Long RR At Market"))
    if rr is None and close and stop is not None and tgt is not None and close > stop and tgt > close:
        rr = (tgt - close) / (close - stop)
    extz = _f(last.get("Ext Z Self Relative"))
    lo10 = _f(d["low"].astype(float).iloc[-10:].min())
    hi60 = _f(d["high"].astype(float).iloc[-60:].max())
    pb = bool(close and lo10 is not None and hi60 is not None and close > lo10
              and (hi60 - close) / (close - lo10) >= 3 and extz is not None and extz < 1.5)
    return {"bar_date": str(last["_d"]), "close": close, "rr": rr, "extz": extz, "pb": pb,
            "ivr": _f(last.get("Energy IV Rank Pct")), "hv20": _f(last.get("HV20 Ann Pct"))}


def count(c: Dict[str, Any], hv_median: Optional[float]) -> Optional[int]:
    """B1 count 0-5, or None when the HV20 median for the date is unknown."""
    if hv_median is None:
        return None
    return (int(c["rr"] is not None and c["rr"] >= 3) + int(c["extz"] is not None and c["extz"] < 0)
            + int(bool(c["pb"])) + int(c["ivr"] is not None and c["ivr"] > 80)
            + int(c["hv20"] is not None and c["hv20"] < hv_median))


def load_datawindow(base_dir: Path, ticker: str, date: str):
    import pandas as pd

    p = Path(base_dir) / "data" / "raw" / date / ticker / f"{ticker}_datawindow.csv"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except Exception as e:
        logger.debug(f"[b1] {p} unreadable: {e}")
        return None


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS b1_hv_median (date TEXT PRIMARY KEY, median REAL, n INTEGER)")


def cached_medians(conn: sqlite3.Connection, dates: Iterable[str]) -> Dict[str, float]:
    _init(conn)
    want = sorted({str(d)[:10] for d in dates})
    if not want:
        return {}
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT date, median FROM b1_hv_median WHERE date IN ({','.join('?' * len(want))})", want)}


def store_medians(conn: sqlite3.Connection, built: Dict[str, tuple]) -> None:
    _init(conn)
    conn.executemany("INSERT OR REPLACE INTO b1_hv_median (date, median, n) VALUES (?,?,?)",
                     [(d, m, n) for d, (m, n) in built.items()])
    conn.commit()


def _universe() -> list:
    return [t.strip() for t in UNIVERSE_FILE.read_text().split() if t.strip()]


def _closes_alpaca(first: str):
    """Wide close frame (date x ticker) from Alpaca batch bars, or None."""
    import pandas as pd
    from datetime import date, datetime

    from src.tracking import bars_store

    days = (date.today() - datetime.strptime(first, "%Y-%m-%d").date()).days + 50
    bars = bars_store.fetch_daily(_universe(), days=days)
    if not bars:
        return None
    return pd.DataFrame({t: {b["date"]: b["close"] for b in bs} for t, bs in bars.items()}).sort_index()


def _closes_yahoo(first: str, last: str):
    import yfinance as yf
    from datetime import datetime, timedelta

    start = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=50)).strftime("%Y-%m-%d")
    end = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    names = [t.replace(".", "-") for t in _universe()]
    close = yf.download(names, start=start, end=end, progress=False, auto_adjust=False, threads=True)["Close"]
    close.index = [str(x)[:10] for x in close.index]
    return close


def medians_from_closes(close, first: str, last: str) -> Dict[str, tuple]:
    """Pine HV20 (population stdev of 20 log returns x sqrt(252) x 100) per name, median over names >= $20."""
    import numpy as np

    lr = np.log(close / close.shift(1))
    hv = (lr.rolling(20).std(ddof=0) * math.sqrt(252) * 100).where(close >= MIN_PRICE)
    out: Dict[str, tuple] = {}
    for idx, row in hv.iterrows():
        d = str(idx)[:10]
        n = int(row.notna().sum())
        if first <= d <= last and n >= MIN_UNIVERSE_N:
            out[d] = (float(row.median()), n)
    return out


def build_medians(first: str, last: str) -> Dict[str, tuple]:
    """{date: (median HV20, names)} for closed sessions first..last. Alpaca, then Yahoo. Network; hold no DB lock."""
    for name, fetch in (("alpaca", lambda: _closes_alpaca(first)), ("yahoo", lambda: _closes_yahoo(first, last))):
        try:
            close = fetch()
        except Exception as e:
            logger.warning(f"[b1] {name} universe download failed: {type(e).__name__} {e}")
            continue
        if close is None or close.empty:
            logger.warning(f"[b1] {name} returned no bars for the B1 universe")
            continue
        out = medians_from_closes(close, first, last)
        if out:
            logger.info(f"[b1] HV20 medians from {name}: {len(out)} dates ({first}..{last})")
            return out
        got = int(close.iloc[-1].notna().sum()) if len(close) else 0
        logger.warning(f"[b1] {name}: only {got} of {len(_universe())} names with bars (< {MIN_UNIVERSE_N}); no medians")
    return {}
