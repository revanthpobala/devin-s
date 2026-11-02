"""Post-earnings drift scan. Writes UNMEASURED suggestions (source='earnings_drift', setup_lane='EARNINGS_DRIFT').

Frozen definition from scripts/research/pead_study.py (PASSED its pre-registered gate on full_v7, 544 names):
  reaction session r = first session after an SEC 8-K Item 2.02 filing; gap = open[r]/close[r-1]-1 >= +5%;
  volume[r] >= 2x its prior 20-bar mean; close[r+2] >= open[r] (gap held). Enter the open of r+3, the session after
  the signal close D = r+2. Stop 2 ATR14 below the signal close (the study put it below the entry open, which is
  unknown at signal time), 21-bar time exit.
In-sample (1,144 events, 225 names, 2006-2026, 10 bps): mean +0.61% vs universe, median +0.21%, all 4 eras positive.
Caveats: the measured universe is today's surviving full_v7 names (survivorship inflates it); the live universe is the
screener constituents, not the measured one; the 8-K timing is unknown, so pre-market releases are missed. The
variant without the hold condition did as well or better (+0.90% mean), so the hold filter adds nothing measurable.
Lane stays UNMEASURED in forward_log until 200 scored observations.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCE = "earnings_drift"
SETUP_LANE = "EARNINGS_DRIFT"
GAP_MIN = 0.05
VOL_MULT = 2.0
VOL_WINDOW = 20
ATR_N = 14
STOP_ATR = 2.0
HOLD_BARS = 21
MIN_BARS = 40


def _atr(bars: List[Dict[str, Any]], end: int) -> Optional[float]:
    if end < ATR_N:
        return None
    trs = []
    for i in range(end - ATR_N + 1, end + 1):
        h, lo, pc = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / ATR_N


def candidate(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Bar-only test on the last bar D: reaction session r = D-2 with the gap, volume and hold conditions.
    The 8-K filing check is separate (see filing_matches), so SEC is only queried for names that pass here."""
    n = len(bars)
    if n < MIN_BARS:
        return None
    d, r = n - 1, n - 3
    close_d = float(bars[d]["close"])
    open_r, prev_close = float(bars[r]["open"]), float(bars[r - 1]["close"])
    gap = open_r / prev_close - 1
    vols = [float(b["volume"]) for b in bars[r - VOL_WINDOW:r]]
    avg_vol = sum(vols) / VOL_WINDOW
    if gap < GAP_MIN or avg_vol <= 0 or float(bars[r]["volume"]) < VOL_MULT * avg_vol:
        return None
    if close_d < open_r:  # gap not held through day 3
        return None
    atr = _atr(bars, d)
    if not atr:
        return None
    return {"date": bars[d]["date"], "reaction": bars[r]["date"], "prev_date": bars[r - 1]["date"],
            "close": close_d, "gap_pct": 100 * gap, "vol_x": float(bars[r]["volume"]) / avg_vol,
            "atr": atr, "stop": close_d - STOP_ATR * atr}


def filing_matches(c: Dict[str, Any], filings: List[str]) -> bool:
    """An 8-K 2.02 filed on the session before the reaction day: prev_date <= filing < reaction."""
    return any(c["prev_date"] <= f < c["reaction"] for f in filings)


def recent_filings_202(cik: str, since: str) -> List[str]:
    """Item 2.02 8-K filing dates on or after `since`, from the submissions index's recent page."""
    from src.clients.sec_edgar_client import SEC_HEADERS

    headers = {"User-Agent": SEC_HEADERS["User-Agent"]}
    req = urllib.request.Request(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        recent = json.loads(r.read().decode("utf-8"))["filings"]["recent"]
    return [fd for form, fd, items in zip(recent["form"], recent["filingDate"], recent["items"])
            if form == "8-K" and "2.02" in (items or "").split(",") and fd >= since]


def _open_names(since: str, through: str) -> set:
    """Names with an unscored drift row in [since, through]; `through` includes the signal day so a rerun skips it."""
    from src import config

    try:
        conn = sqlite3.connect(f"file:{config.research_watch_db_path()}?mode=ro", uri=True, timeout=5)
        rows = conn.execute("SELECT ticker FROM suggestions WHERE source = ? AND date >= ? AND date <= ? AND scored_at IS NULL",
                            (SOURCE, since, through)).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception as e:
        logger.warning("earnings_drift open-trade check failed: %s", e)
        return set()


def run(bars: Dict[str, List[Dict[str, Any]]], filings_for, write: bool = True,
        now_et: Optional[datetime] = None) -> Dict[str, Any]:
    """filings_for(ticker) -> list of 8-K 2.02 dates. Only called for names that pass the bar test."""
    from src.tracking.rsi2_scan import entry_open_passed

    last = max((str(b[-1]["date"])[:10] for b in bars.values() if b), default=None)
    hits = []
    for t, b in sorted(bars.items()):
        c = candidate(b)
        if not c or str(c["date"])[:10] != last:
            continue
        try:
            fl = filings_for(t)
        except Exception as e:
            logger.warning("earnings_drift filings %s failed: %s", t, e)
            continue
        if filing_matches(c, fl):
            hits.append({"ticker": t, **c})
    day = hits[0]["date"] if hits else None
    stale = write and bool(hits) and entry_open_passed(day, now_et)
    written: List[str] = []
    if write and hits and not stale:
        from src.tracking.forward_log import record_forward_observation
        from src.tracking.suggestions_ledger import append_suggestion

        busy = _open_names((date.fromisoformat(day) - timedelta(days=HOLD_BARS + 5)).isoformat(), day)
        for h in hits:
            if h["ticker"] in busy:
                continue
            append_suggestion({
                "ticker": h["ticker"], "date": h["date"], "source": SOURCE, "side": "LONG", "entry_type": "NEXT_OPEN",
                "entry_low": h["close"], "entry_high": h["close"], "stop": h["stop"], "target_1": 0.0,
                "atr_at_signal": h["atr"], "spot_at_signal": h["close"], "stop_width_atr": STOP_ATR,
                "verdict": "STALK", "gate_status": "UNMEASURED", "setup_lane": SETUP_LANE, "kind": "NEW",
                "gate_reasons": "UNMEASURED: PEAD measured in-sample only; forward log decides",
                "notes": (f"8-K 2.02 reaction {h['reaction']} gap {h['gap_pct']:+.1f}%, volume {h['vol_x']:.1f}x; "
                          f"held through {h['date']}; buy next open; stop ~{h['stop']:.2f} (2 ATR from signal close); "
                          f"exit at 21-bar time limit or stop"),
            })
            record_forward_observation(SETUP_LANE, h["ticker"], {"date": h["date"], "reaction": h["reaction"],
                                                                 "gap_pct": round(h["gap_pct"], 2), "stop": round(h["stop"], 2)})
            written.append(h["ticker"])
    return {"date": day, "signals": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in h.items()} for h in hits],
            "written": written, "stale": stale}


def run_daily(today: str, tickers: List[str]) -> Dict[str, Any]:
    """Nightly entry point: 90 calendar days of bars for the universe, then SEC filings for candidates only."""
    from src.tracking.bars_store import fetch_daily
    from src.tracking.universe_sweep import completed
    from src.clients.sec_edgar_client import CIK_MAP_PATH

    if not CIK_MAP_PATH.exists():
        return {"error": f"missing {CIK_MAP_PATH}"}
    cmap = json.loads(CIK_MAP_PATH.read_text())
    bars = completed(fetch_daily(tickers, 90))
    since = (date.fromisoformat(today) - timedelta(days=10)).isoformat()

    def filings_for(t: str) -> List[str]:
        cik = cmap.get(t)
        return recent_filings_202(cik, since) if cik else []

    return run(bars, filings_for)
