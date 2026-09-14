"""
src/tracking/superforecasting_auditor.py

Automated Superforecasting Brier Calibration Engine.
Scans markdown dossiers in reports/, indexes 14d/30d/60d probability forecasts
from Model A (Pine) and Model B (Independent), checks price history against target
dates, calculates Brier calibration scores, and persists calibration metrics to SQLite.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src import config
from src.tracking.watch_manager import (
    get_superforecasting_stats,
    init_watch_db,
    upsert_superforecasting_prediction,
)

logger = logging.getLogger(__name__)

SUPERFORECAST_BLOCK_REGEX = re.compile(
    r"## 🔮 SUPERFORECASTING PREDICTIONS[\s\S]*?```(?:json)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*```",
    re.IGNORECASE,
)


def extract_predictions_from_report(report_path: Path) -> List[Dict[str, Any]]:
    """Extract structured superforecasting JSON from a research report markdown file."""
    if not report_path.exists():
        return []

    try:
        content = report_path.read_text(encoding="utf-8")
        match = SUPERFORECAST_BLOCK_REGEX.search(content)
        if not match:
            return []

        raw_json = match.group(1).strip()
        data = json.loads(raw_json)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.debug(f"Failed to extract predictions from {report_path}: {e}")
        return []


def parse_event_condition(event_desc: str) -> Tuple[Optional[str], Optional[float]]:
    """
    Parses event text to determine operator and threshold.
    Returns (condition_type, price_level)
    e.g. 'closes above $14.50' -> ('ABOVE', 14.50)
         'closes below $12.83' -> ('BELOW', 12.83)
         'reaches Profit Target 1 of $104.92' -> ('ABOVE', 104.92)
         'enters Pine Script Buy Zone [$90.00 – $93.00]' -> ('BELOW', 93.00)
    """
    desc = event_desc.lower()
    prices = [float(p) for p in re.findall(r"\$\s*([0-9]+\.?[0-9]*)", desc)]
    if not prices:
        return None, None

    if any(k in desc for k in ("enter", "zone", "pullback", "test")):
        return "BELOW", max(prices)

    price = prices[0]
    if any(k in desc for k in ("above", "reclaim", "target", "high", "up to", "breakout")):
        return "ABOVE", price
    elif any(k in desc for k in ("below", "break", "down to", "low", "stop", "drop")):
        return "BELOW", price

    return None, price


def get_historical_price_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch daily OHLC history between start_date and end_date."""
    # 1. Check local datawindow CSV first
    raw_root = config.BASE_DIR / "data" / "raw"
    for d in sorted(raw_root.glob("*/"), reverse=True):
        cand = d / ticker / f"{ticker}_datawindow.csv"
        if cand.exists():
            try:
                df = pd.read_csv(cand)
                col_map = {c.lower(): c for c in df.columns}
                if "close" in col_map:
                    time_col = col_map.get("time") or col_map.get("date")
                    if time_col:
                        time_s = df[time_col].astype(str).str.slice(0, 10)
                        mask = (time_s >= start_date) & (time_s <= end_date)
                        filtered = df[mask]
                        if not filtered.empty:
                            return filtered
                    return df
            except Exception:
                pass

    # 2. Fallback to yfinance historical prices
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(start=start_date, end=end_date)
        if not hist.empty:
            return hist
    except Exception as e:
        logger.debug(f"yfinance history fetch failed for {ticker}: {e}")

    return pd.DataFrame()


def evaluate_prediction_outcome(
    ticker: str,
    event_desc: str,
    report_date_str: str,
    target_date_str: str,
) -> Tuple[Optional[int], Optional[float]]:
    """
    Evaluates whether an event occurred between report_date and target_date.
    Returns (actual_outcome 1/0, eval_price) or (None, None) if still pending.
    """
    today_str = date.today().isoformat()
    # If horizon has not matured yet, it is still pending
    if today_str < target_date_str:
        return None, None

    cond_type, target_price = parse_event_condition(event_desc)
    if not cond_type or target_price is None:
        return None, None

    # Pull price history from report date to target date (+ 1 day buffer)
    try:
        t_end = (datetime.strptime(target_date_str, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        hist = get_historical_price_history(ticker, report_date_str, t_end)
        if hist.empty:
            return None, None

        col_map = {c.lower(): c for c in hist.columns}
        close_col = col_map.get("close")
        if not close_col:
            return None, None

        closes = hist[close_col].dropna().values.astype(float)
        if len(closes) == 0:
            return None, None

        eval_price = float(closes[-1])

        if cond_type == "ABOVE":
            # Event occurred if ANY daily close exceeded the target price
            occurred = int(bool(np.any(closes >= target_price)))
        else:
            # Event occurred if ANY daily close breached below the target price
            occurred = int(bool(np.any(closes <= target_price)))

        return occurred, eval_price
    except Exception as e:
        logger.debug(f"Evaluation failed for {ticker} {event_desc}: {e}")
        return None, None


import numpy as np


def run_superforecasting_audit() -> Dict[str, Any]:
    """
    Scans reports/ directory, indexes all superforecasting predictions,
    evaluates resolved horizons, calculates Brier scores, and saves to SQLite.
    """
    init_watch_db()
    reports_dir = config.BASE_DIR / "reports"
    if not reports_dir.exists():
        return {"indexed": 0, "evaluated": 0}

    indexed_count = 0
    evaluated_count = 0

    # Discover date subdirectories (e.g. reports/2026-08-31/)
    date_dirs = [d for d in reports_dir.glob("*/") if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)]

    for d in sorted(date_dirs):
        report_date_str = d.name
        try:
            r_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        for r_file in d.glob("*.md"):
            fname = r_file.name
            if fname.endswith("_summary.md"):
                ticker = fname.replace("_summary.md", "").upper()
                model_type = "MODEL_A_PINE"
            elif fname.endswith("_independent.md"):
                ticker = fname.replace("_independent.md", "").upper()
                model_type = "MODEL_B_INDEPENDENT"
            else:
                continue

            predictions = extract_predictions_from_report(r_file)
            for p in predictions:
                h_days = int(p.get("horizon_days", 14))
                event_desc = str(p.get("event", "")).strip()
                prob = float(p.get("probability", 0.50))

                if not event_desc:
                    continue

                target_date = (r_date + timedelta(days=h_days)).isoformat()

                # Evaluate outcome if target date has passed
                outcome, eval_price = evaluate_prediction_outcome(ticker, event_desc, report_date_str, target_date)
                brier_score = round((prob - outcome) ** 2, 4) if outcome is not None else None
                eval_at = datetime.now().isoformat(timespec="seconds") if outcome is not None else None

                row = {
                    "ticker": ticker,
                    "report_date": report_date_str,
                    "model_type": model_type,
                    "horizon_days": h_days,
                    "target_date": target_date,
                    "event_description": event_desc,
                    "predicted_probability": prob,
                    "actual_outcome": outcome,
                    "brier_score": brier_score,
                    "evaluation_price": eval_price,
                    "evaluated_at": eval_at,
                }

                upsert_superforecasting_prediction(row)
                indexed_count += 1
                if outcome is not None:
                    evaluated_count += 1

    stats = get_superforecasting_stats()
    return {
        "indexed_count": indexed_count,
        "evaluated_count": evaluated_count,
        "stats": stats,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running Superforecasting Brier Calibration Audit...")
    res = run_superforecasting_audit()
    print(f"Indexed {res['indexed_count']} predictions ({res['evaluated_count']} resolved).")
    print(json.dumps(res["stats"], indent=2))
