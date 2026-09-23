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


def parse_event_condition(pred_item: Any) -> Tuple[Optional[str], Optional[float], str, Optional[float]]:
    """
    Parses structured prediction dict or event text.
    Returns (direction 'ABOVE'|'BELOW', price_level, eval_type 'TOUCH'|'CLOSE', before_level).
    """
    if isinstance(pred_item, dict):
        ev_type = str(pred_item.get("type", "")).lower()
        level = pred_item.get("level")
        before_lvl = pred_item.get("before_level")
        if level is not None:
            try:
                lvl_f = float(level)
                b_lvl_f = float(before_lvl) if before_lvl is not None else None
                if ev_type in ("touch", "touches"):
                    # Infer direction if not explicit
                    return ("ABOVE", lvl_f, "TOUCH", b_lvl_f)
                elif "close_below" in ev_type:
                    return ("BELOW", lvl_f, "CLOSE", b_lvl_f)
                elif "close_above" in ev_type:
                    return ("ABOVE", lvl_f, "CLOSE", b_lvl_f)
            except (ValueError, TypeError):
                pass
        desc = str(pred_item.get("event", "")).lower()
    else:
        desc = str(pred_item).lower()

    prices = [float(p) for p in re.findall(r"\$\s*([0-9]+\.?[0-9]*)", desc)]
    if not prices:
        return None, None, "CLOSE", None

    is_touch = any(k in desc for k in ("touch", "reaches", "enter", "pullback")) or bool(re.search(r"\btest\b", desc))
    eval_type = "TOUCH" if is_touch else "CLOSE"

    before_level = None
    before_m = re.search(r"before\s+(?:hitting\s+)?(?:stop\s+(?:loss\s+)?of\s+)?\$\s*([0-9]+\.?[0-9]*)", desc)
    if before_m:
        try:
            before_level = float(before_m.group(1))
        except ValueError:
            pass

    if any(k in desc for k in ("enter", "zone", "pullback")) or re.search(r"\btest\b", desc):
        return "BELOW", max(prices), "TOUCH", before_level

    price = prices[0]
    if any(k in desc for k in ("above", "reclaim", "target", "high", "up to", "breakout")):
        return "ABOVE", price, eval_type, before_level
    elif any(k in desc for k in ("below", "break", "down to", "low", "stop", "drop")):
        return "BELOW", price, eval_type, before_level

    return None, price, eval_type, before_level


def get_historical_price_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch daily OHLC history between start_date and end_date."""
    today_str = date.today().isoformat()
    if start_date >= end_date or start_date >= today_str:
        return pd.DataFrame()

    # 1. Check local datawindow CSV first
    raw_root = config.BASE_DIR / "data" / "raw"
    if raw_root.exists():
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
                        return pd.DataFrame()
                except Exception:
                    pass

    # 2. Fallback to yfinance historical prices with silenced logger
    try:
        yf_l = logging.getLogger("yfinance")
        yf_l.setLevel(logging.CRITICAL)
        yf_l.propagate = False
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
    pred_data: Any,
    report_date_str: str,
    target_date_str: str,
) -> Tuple[Optional[int], Optional[float]]:
    """
    Evaluates whether an event occurred between report_date and target_date.
    Uses daily High/Low for TOUCH events and daily Close for CLOSE events.
    Honors before_level (invalidates if before_level was touched first).
    Returns (actual_outcome 1/0, eval_price) or (None, None) if still pending.
    """
    today_str = date.today().isoformat()
    if today_str < target_date_str:
        return None, None

    cond_type, target_price, eval_type, before_lvl = parse_event_condition(pred_data)
    if not cond_type or target_price is None:
        return None, None

    try:
        t_end = (datetime.strptime(target_date_str, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        hist = get_historical_price_history(ticker, report_date_str, t_end)
        if hist.empty:
            return None, None

        col_map = {c.lower(): c for c in hist.columns}
        close_col = col_map.get("close")
        high_col = col_map.get("high") or close_col
        low_col = col_map.get("low") or close_col

        if not close_col:
            return None, None

        eval_price = float(hist[close_col].dropna().values[-1]) if len(hist[close_col].dropna()) > 0 else 0.0

        occurred = 0
        for _, bar in hist.iterrows():
            b_high = float(bar[high_col])
            b_low = float(bar[low_col])
            b_close = float(bar[close_col])

            # Check before_level invalidation (e.g. stop hit before target)
            if before_lvl is not None and before_lvl > 0:
                if cond_type == "ABOVE" and b_low <= before_lvl:
                    occurred = 0
                    break
                elif cond_type == "BELOW" and b_high >= before_lvl:
                    occurred = 0
                    break

            if eval_type == "TOUCH":
                if cond_type == "ABOVE" and b_high >= target_price:
                    occurred = 1
                    break
                elif cond_type == "BELOW" and b_low <= target_price:
                    occurred = 1
                    break
            else:
                if cond_type == "ABOVE" and b_close >= target_price:
                    occurred = 1
                    break
                elif cond_type == "BELOW" and b_close <= target_price:
                    occurred = 1
                    break

        return occurred, eval_price
    except Exception as e:
        logger.debug(f"Evaluation failed for {ticker} {pred_data}: {e}")
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
                outcome, eval_price = evaluate_prediction_outcome(ticker, p, report_date_str, target_date)
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
