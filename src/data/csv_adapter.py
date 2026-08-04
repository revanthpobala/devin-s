"""
src/data/csv_adapter.py

CSV Data Window Adapter — Converts TradingView chart CSV exports into Data Window
snapshots and computes trailing 10-day volatility (realvol_10d) and return (ret_10d).
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def csv_to_datawindow(
    csv_path: str, json_out_path: Optional[str] = None
) -> Tuple[Dict[str, Any], pd.DataFrame, Optional[float], Optional[float]]:
    """Parse a TradingView exported CSV into a Data Window snapshot dict & trailing metrics.

    Args:
        csv_path: Path to the downloaded CSV file.
        json_out_path: Optional output path to write <symbol>_datawindow.json.

    Returns:
        (snapshot_dict, history_df, realvol_10d, ret_10d)
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError(f"CSV file is empty: {csv_path}")

    # Clean column names (strip whitespace)
    df.columns = [str(c).strip() for c in df.columns]

    # Extract last row as snapshot dict (keyed by CSV header names)
    last_row = df.iloc[-1]
    snapshot = {}
    for col in df.columns:
        val = last_row[col]
        if pd.isna(val):
            snapshot[col] = None
        else:
            snapshot[col] = str(val)

    # Write snapshot to JSON if path provided
    if json_out_path:
        out_dir = os.path.dirname(json_out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(json_out_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=4)
        logger.info(f"Saved snapshot Data Window JSON to {json_out_path}")

    # Compute trailing metrics (ret_10d and realvol_10d)
    realvol_10d: Optional[float] = None
    ret_10d: Optional[float] = None

    # Locate 'close' column (case-insensitive & prefix-tolerant)
    close_col = None
    for col in df.columns:
        c_clean = col.lower()
        if c_clean == "close" or c_clean.endswith(": close") or c_clean.endswith(" close"):
            close_col = col
            break
    if not close_col:
        for col in df.columns:
            if "close" in col.lower():
                close_col = col
                break

    if close_col and len(df) >= 2:
        close_series = pd.to_numeric(df[close_col], errors="coerce").dropna()
        n = len(close_series)

        # 10-day return: (close[-1] - close[-11]) / close[-11] * 100.0
        if n >= 11:
            c_now = close_series.iloc[-1]
            c_10d = close_series.iloc[-11]
            if c_10d > 0:
                calc_ret = float((c_now - c_10d) / c_10d * 100.0)
                if not np.isnan(calc_ret):
                    ret_10d = calc_ret

        # 10-day annualized volatility: std(daily_returns) * sqrt(252) * 100.0
        if n >= 11:
            trailing_closes = close_series.iloc[-11:]
            pct_returns = trailing_closes.pct_change().dropna()
            if len(pct_returns) >= 10:
                std_daily = float(pct_returns.std(ddof=1))
                calc_vol = float(std_daily * np.sqrt(252) * 100.0)
                if not np.isnan(calc_vol):
                    realvol_10d = calc_vol

    return snapshot, df, realvol_10d, ret_10d
