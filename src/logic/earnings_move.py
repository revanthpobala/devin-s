from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

FORECAST = config.BASE_DIR / "data" / "earnings_move_forecast.json"
LEDGER = config.BASE_DIR / "data" / "earnings_ledger.jsonl"
SCORED = config.BASE_DIR / "data" / "earnings_ledger_scored.json"
RUN21_CUT_KEY, EXTZ_CUT_KEY, IVR_KEY = "run21", "extz", "ivr"

_mtime: Optional[float] = None
_cache: Optional[dict] = None


def load() -> dict:
    global _mtime, _cache
    path = Path(FORECAST)
    if not path.is_file():
        return {}
    try:
        mt = path.stat().st_mtime
        if _cache is not None and _mtime == mt:
            return _cache
        raw = json.loads(path.read_text(encoding="utf-8"))
        _cache = raw if isinstance(raw, dict) else {}
        _mtime = mt
        return _cache
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def forecast(ticker: str) -> Optional[dict]:
    row = load().get("tickers", {}).get(str(ticker or "").upper())
    return row if isinstance(row, dict) else None


def gap_pct(ticker: str, implied_pct: Optional[float] = None) -> Optional[float]:
    fc = forecast(ticker)
    hist = float((fc or {}).get("p95_abs") or 0) * 100.0
    try:
        opt = 2.0 * float(implied_pct) if implied_pct is not None else 0.0
    except (TypeError, ValueError):
        opt = 0.0
    out = max(hist, opt)
    if out <= 0:
        return None
    return round(out, 2)


def _cond_bucket(conds: dict, name: str, bucket: str) -> Optional[dict]:
    b = (conds or {}).get(name, {}).get(bucket)
    return b if isinstance(b, dict) and b.get("n") else None


def condition_lines(run21: Optional[float], extz: Optional[float], ivr: Optional[float]) -> List[str]:
    """Only the measured elevated down-tail conditions that hold today (context for the judge, not a veto)."""
    data = load()
    conds = data.get("conditions") or {}
    base = data.get("base_p_down_2x")
    hits = []
    checks = [
        (RUN21_CUT_KEY, "low", run21, "21-bar run in the bottom quintile"),
        (EXTZ_CUT_KEY, "low", extz, "Ext Z in the bottom quintile"),
        (IVR_KEY, "high", ivr, "IV rank in the top quintile"),
    ]
    for name, bucket, val, label in checks:
        b = _cond_bucket(conds, name, bucket)
        if b is None or val is None:
            continue
        cuts = conds[name]["cuts"]
        hit = val <= cuts[0] if bucket == "low" else val >= cuts[1]
        if hit:
            p = b["p_down_2x_hist"] * 100
            hits.append(f"{label} ({p:.1f}% vs {base * 100:.1f}% base)" if base is not None else label)
    return (["- Measured elevated -2x down-tail at prior close: " + "; ".join(hits)] if hits else [])


def append_ledger(row: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Append one row per (ticker, report_date, recorded day). Returns False when already recorded."""
    path = Path(path or LEDGER)
    key = (row["ticker"], row["report_date"], row["recorded"][:10])
    seen = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            seen.add((r.get("ticker"), r.get("report_date"), str(r.get("recorded", ""))[:10]))
    if key in seen:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return True


def realized_legs(hist, report_date: date) -> Optional[Dict[str, float]]:
    """Report-day leg (pre-market release) and next-session leg (after-close release), close to close.

    hist: DataFrame indexed by date with a Close column. The release time is not stored, so the reaction is taken
    as the larger-magnitude leg.
    """
    closes = hist["Close"]
    before = closes[closes.index < _ts(report_date)]
    on = closes[closes.index == _ts(report_date)]
    after = closes[closes.index > _ts(report_date)]
    if on.empty or after.empty:
        return None
    day_leg = float(on.iloc[0] / before.iloc[-1] - 1) if not before.empty else None
    next_leg = float(after.iloc[0] / on.iloc[0] - 1)
    legs = [x for x in (day_leg, next_leg) if x is not None]
    return {"report_day": None if day_leg is None else round(day_leg, 4), "next_session": round(next_leg, 4),
            "reaction": round(max(legs, key=abs), 4)}


def realized_move(hist, report_date: date) -> Optional[float]:
    legs = realized_legs(hist, report_date)
    return legs["reaction"] if legs else None


def _ts(d: date):
    import pandas as pd

    return pd.Timestamp(d)


def _default_history(ticker: str, start: date, end: date):
    import yfinance as yf

    h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
    if h is None or h.empty:
        return None
    h.index = h.index.tz_localize(None).normalize()
    return h


def score_ledger(today: Optional[date] = None, history: Optional[Callable] = None,
                 ledger: Optional[Path] = None, scored: Optional[Path] = None) -> Dict[str, int]:
    """Fill realized_move for ledger rows whose report date has passed; keeps earlier scores."""
    today = today or date.today()
    history = history or _default_history
    ledger, scored = Path(ledger or LEDGER), Path(scored or SCORED)
    rows = []
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    done = {}
    if scored.is_file():
        try:
            done = {(r["ticker"], r["report_date"]): r for r in json.loads(scored.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError):
            done = {}
    new = 0
    for r in rows:
        key = (r["ticker"], r["report_date"])
        rd = datetime.strptime(r["report_date"], "%Y-%m-%d").date()
        if key in done or rd >= today:
            continue
        try:
            hist = history(r["ticker"], rd - timedelta(days=7), today + timedelta(days=1))
        except Exception as e:
            logger.debug("[%s] ledger history failed: %s", r["ticker"], e)
            continue
        legs = realized_legs(hist, rd) if hist is not None and len(hist) else None
        if legs is None:
            continue
        rm = legs["reaction"]
        imp = r.get("implied_move_pct")
        done[key] = {**r, "realized_move_pct": round(rm * 100, 2), "legs": legs,
                     "implied_vs_realized": round(imp / abs(rm * 100), 2) if imp and rm else None}
        new += 1
    if new:
        scored.parent.mkdir(parents=True, exist_ok=True)
        scored.write_text(json.dumps(list(done.values()), indent=2), encoding="utf-8")
    return {"ledger_rows": len(rows), "scored_total": len(done), "newly_scored": new}


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["score"]:
        print(score_ledger())
