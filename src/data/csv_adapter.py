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


MIN_COLS = 60
MIN_ROWS = 260
MAX_BAR_GAP_DAYS = 4.0


def verify_csv_integrity(df: pd.DataFrame) -> Tuple[pd.Series, Optional[str]]:
    """Assert minimum column/row depth and verify daily bar interval (median gap <= 4 days)."""
    nrows, ncols = df.shape
    if ncols < MIN_COLS:
        raise ValueError(f"CSV has {ncols} columns, expected at least {MIN_COLS}")
    if nrows < MIN_ROWS:
        raise ValueError(f"CSV has {nrows} rows, expected at least {MIN_ROWS}")

    # Locate time/date column
    time_col = None
    for col in df.columns:
        if col.lower() in ("time", "date", "datetime", "timestamp"):
            time_col = col
            break

    last_bar_date = None
    if time_col:
        time_series = pd.to_datetime(df[time_col], errors="coerce").dropna()
        if len(time_series) >= 2:
            gaps = time_series.diff().dt.total_seconds() / 86400.0
            median_gap = float(gaps.median())
            if median_gap > MAX_BAR_GAP_DAYS:
                raise ValueError(
                    f"CSV interval appears weekly or monthly (median bar gap {median_gap:.1f} days > {MAX_BAR_GAP_DAYS} days)"
                )
            last_bar_date = time_series.iloc[-1].strftime("%Y-%m-%d")

    return df, last_bar_date


def _format_datawindow_val(val: Any) -> Optional[str]:
    """Format Data Window value: limit floats to 3 decimal places, keep ints clean, handle nulls."""
    if pd.isna(val) or val is None:
        return None
    s_val = str(val).strip()
    if not s_val or s_val.lower() in ("nan", "none", "null"):
        return None
    try:
        f = float(s_val)
        if np.isnan(f) or np.isinf(f):
            return None
        # Whole integer values (e.g. 0, 1, 4, 10, 360, "0.0") -> format cleanly
        if f.is_integer() and ("." not in s_val or s_val.endswith(".0")):
            return str(int(f))
        # Float values: round to at most 3 decimal places
        return str(round(f, 3))
    except (ValueError, TypeError):
        return s_val


def _extract_ticker_from_path(path: Optional[str]) -> Optional[str]:
    """Extract ticker symbol from file path or folder name."""
    if not path:
        return None
    clean_path = path.replace("\\", "/")
    base = os.path.basename(clean_path)
    base_no_ext = os.path.splitext(base)[0]
    candidate = base_no_ext.split("_")[0].split(",")[0].strip()
    if candidate and candidate.upper() not in ("DATAWINDOW", "CSV", "UNKNOWN", "SNAPSHOT", "HISTORY", "TEST"):
        return candidate.upper()
    parts = [p for p in clean_path.split("/") if p]
    if len(parts) >= 2:
        parent = parts[-2].strip()
        if parent and parent.upper() not in ("FORCE", "RAW", "TRIAGE", "CONSOLIDATE", "SCRAPES", "DATA"):
            return parent.upper()
    return None


def decode_and_enrich_datawindow(
    snapshot: Dict[str, Any], df: pd.DataFrame, ticker: Optional[str] = None
) -> Dict[str, Any]:
    """Decodes Pine Script bitpacks and derives MTF, Anchors, and Key Resistance/Support levels from history."""
    # Ensure ticker is set if provided or available
    ticker_name = ticker or snapshot.get("ticker") or snapshot.get("symbol")
    if ticker_name and ticker_name.upper() not in ("UNKNOWN", "NONE", ""):
        snapshot["ticker"] = ticker_name
    # 1. Premove Pack decoding (Line 6531-6553)
    try:
        p_val = int(float(snapshot.get("Premove Pack") or 0))
        darvas_states = {0: "None", 1: "IN BOX", 2: "BREAKING", 3: "BREAKOUT", 4: "ABOVE BOX", 5: "BELOW BOX"}
        sqz_dirs = {0: "Down", 1: "None", 2: "Up"}

        snapshot["_premove_darvas_state"] = darvas_states.get(p_val % 8, "None")
        snapshot["_premove_darvas_quality"] = (p_val // 8 % 8) * 20
        snapshot["_premove_squeeze_release_dir"] = sqz_dirs.get(p_val // 64 % 4, "None")
        snapshot["_premove_is_rs_leader"] = bool(p_val // 256 % 2)
        snapshot["_premove_is_accelerating"] = bool(p_val // 512 % 2)
        snapshot["_premove_market_bullish"] = bool(p_val // 1024 % 2)
        snapshot["_premove_power_breakout"] = bool(p_val // 2048 % 2)
        snapshot["_premove_ad_line_bullish"] = bool(p_val // 4096 % 2)
        snapshot["_premove_bull_flag"] = bool(p_val // 8192 % 2)
        snapshot["_premove_impulse_green"] = bool(p_val // 16384 % 2)
        snapshot["_premove_is_near_52w_high"] = bool(p_val // 32768 % 2)
    except Exception:
        pass

    # 2. Reversal Pattern Mask decoding (Line 6456-6459)
    try:
        r_val = int(float(snapshot.get("Reversal Pattern Mask") or 0))
        active_revs = []
        if r_val & 1: active_revs.append("KEY_REV_BULL")
        if r_val & 2: active_revs.append("KEY_REV_BEAR")
        if r_val & 4: active_revs.append("SWEEP_BULL")
        if r_val & 8: active_revs.append("SWEEP_BEAR")
        if r_val & 16: active_revs.append("FAILSWEEP_BULL")
        if r_val & 32: active_revs.append("FAILSWEEP_BEAR")
        if r_val & 64: active_revs.append("BULL_TRAP")
        if r_val & 128: active_revs.append("BEAR_TRAP")
        if r_val & 256: active_revs.append("HIKKAKE_BULL")
        if r_val & 512: active_revs.append("HIKKAKE_BEAR")
        if r_val & 1024: active_revs.append("OOPS_BULL")
        if r_val & 2048: active_revs.append("OOPS_BEAR")
        snapshot["_active_reversal_patterns"] = active_revs
    except Exception:
        pass

    # 3. Bear Warning Mask decoding (Line 6454-6455)
    try:
        b_val = int(float(snapshot.get("Bear Warning Mask") or 0))
        active_warnings = []
        if b_val & 1: active_warnings.append("TOP_RISK")
        if b_val & 2: active_warnings.append("RSI_CASCADE")
        if b_val & 4: active_warnings.append("INTERNAL_WEAKNESS")
        if b_val & 8: active_warnings.append("EXTREME_EXTENSION")
        if b_val & 16: active_warnings.append("BEAR_WEAKNESS")
        snapshot["_active_bear_warnings"] = active_warnings
    except Exception:
        pass

    # 4. Derive Anchor Names (Closest indicator line to Entry Zone)
    try:
        l_entry = float(snapshot.get("Long Entry") or snapshot.get("close") or 0)
        lines = {}
        for k in ("MA 50 Mid", "Hull Baseline HMA", "MA 20 Fast", "AVWAP Support", "MA 200 Slow", "AVWAP Resistance"):
            if snapshot.get(k):
                try:
                    lines[k] = float(snapshot[k])
                except Exception:
                    pass
        if lines and l_entry > 0:
            name_map = {
                "MA 50 Mid": "S50",
                "Hull Baseline HMA": "HMA",
                "MA 20 Fast": "EMA20",
                "AVWAP Support": "AVWAP",
                "MA 200 Slow": "S200",
                "AVWAP Resistance": "AVWAP.R",
            }
            closest_k = min(lines.keys(), key=lambda k: abs(lines[k] - l_entry))
            snapshot["_long_anchor_name"] = name_map.get(closest_k, closest_k)
    except Exception:
        pass

    # 5. Derive Key Resistance and Support Levels from 300 bars history
    try:
        if "high" in df.columns and "low" in df.columns and len(df) >= 20:
            highs = pd.to_numeric(df["high"], errors="coerce")
            lows = pd.to_numeric(df["low"], errors="coerce")
            closes = pd.to_numeric(df["close"], errors="coerce")

            curr_c = closes.iloc[-1]
            max_high = highs.max()
            recent_peaks = highs.tail(60)[(highs.tail(60) > highs.tail(60).shift(1)) & (highs.tail(60) > highs.tail(60).shift(-1))]
            overhead_peaks = [round(float(p), 2) for p in recent_peaks if p > curr_c]

            snapshot["_52w_high"] = round(float(max_high), 2)
            snapshot["_overhead_key_resistances"] = overhead_peaks[-3:] if overhead_peaks else [round(float(max_high), 2)]

            recent_lows = lows.tail(60)[(lows.tail(60) < lows.tail(60).shift(1)) & (lows.tail(60) < lows.tail(60).shift(-1))]
            under_lows = [round(float(p), 2) for p in recent_lows if p < curr_c]
            snapshot["_underlying_key_supports"] = under_lows[-3:] if under_lows else []
    except Exception:
        pass

    # 6. Multi-Timeframe Alignment from daily OHLCV
    try:
        if "time" in df.columns and "close" in df.columns and len(df) >= 60:
            df_temp = df.copy()
            df_temp["dt"] = pd.to_datetime(df_temp["time"])
            df_temp = df_temp.sort_values("dt")

            d_c = df_temp["close"].iloc[-1]
            d_ma20 = df_temp["close"].rolling(20).mean().iloc[-1]
            d_up = bool(d_c > d_ma20)

            w_df = df_temp.resample("W-FRI", on="dt").agg({"close": "last"}).dropna()
            w_up = False
            if len(w_df) >= 10:
                w_ma10 = w_df["close"].rolling(10).mean().iloc[-1]
                w_up = bool(w_df["close"].iloc[-1] > w_ma10)

            m_df = df_temp.resample("ME", on="dt").agg({"close": "last"}).dropna()
            m_up = False
            if len(m_df) >= 3:
                m_ma3 = m_df["close"].rolling(3).mean().iloc[-1]
                m_up = bool(m_df["close"].iloc[-1] > m_ma3)

            # Direct Pine MTF Pack export if available (bits 5..0)
            pack_val = snapshot.get("MTF Long Short Pack")
            if pack_val is not None:
                p = int(float(pack_val))
                m_up = bool(p & 32)
                w_up = bool(p & 16)
                d_up = bool(p & 8)
                m_dn = bool(p & 4)
                w_dn = bool(p & 2)
                d_dn = bool(p & 1)
                snapshot["_mtf_monthly_aligned"] = m_up
                snapshot["_mtf_weekly_aligned"] = w_up
                snapshot["_mtf_daily_aligned"] = d_up
                snapshot["_mtf_long_aligned"] = (1 if m_up else 0) + (1 if w_up else 0) + (1 if d_up else 0)
                snapshot["_mtf_short_aligned"] = (1 if m_dn else 0) + (1 if w_dn else 0) + (1 if d_dn else 0)
                snapshot["_mtf_alignment_string"] = f"M {'✓' if m_up else '✗'} W {'✓' if w_up else '✗'} D {'✓' if d_up else '✗'}"
                snapshot["_mtf_short_alignment_string"] = f"{'✓' if m_dn else '✗'}M {'✓' if w_dn else '✗'}W {'✓' if d_dn else '✗'}D"
            else:
                snapshot["_mtf_monthly_aligned"] = m_up
                snapshot["_mtf_weekly_aligned"] = w_up
                snapshot["_mtf_daily_aligned"] = d_up
                snapshot["_mtf_alignment_string"] = f"M {'✓' if m_up else '✗'} W {'✓' if w_up else '✗'} D {'✓' if d_up else '✗'}"
                snapshot["_mtf_short_alignment_string"] = f"{'✓' if not m_up else '✗'}M {'✓' if not w_up else '✗'}W {'✓' if not d_up else '✗'}D"
    except Exception:
        pass

    # 7. Pillar 2: 60-Bar Institutional Volume Accumulation vs Distribution Ratio
    try:
        c_col = next((c for c in df.columns if c.lower() == "close"), None)
        v_col = next((c for c in df.columns if c.lower() == "volume"), None)
        if c_col and v_col and len(df) >= 20:
            c = pd.to_numeric(df[c_col], errors="coerce")
            v = pd.to_numeric(df[v_col], errors="coerce")
            tail_c = c.tail(60)
            tail_v = v.tail(60)
            up_mask = tail_c > tail_c.shift(1)
            dn_mask = tail_c < tail_c.shift(1)
            up_vol = float(tail_v[up_mask].sum())
            dn_vol = float(tail_v[dn_mask].sum())
            if dn_vol > 0:
                acc_ratio = round(up_vol / dn_vol, 3)
                snapshot["_volume_acc_dist_ratio_60d"] = acc_ratio
                snapshot["_volume_accumulation_bias"] = "Institutional Accumulation" if acc_ratio >= 1.15 else "Institutional Distribution" if acc_ratio <= 0.85 else "Neutral Volume Flow"
    except Exception:
        pass

    # 8. Pillar 4: Realized Volatility Term Structure & Options Pricing Edge
    try:
        if "close" in df.columns and len(df) >= 25:
            c_ser = pd.to_numeric(df["close"], errors="coerce").dropna()
            log_rets = np.log(c_ser / c_ser.shift(1)).dropna()
            if len(log_rets) >= 20:
                hv20 = round(float(log_rets.tail(20).std(ddof=1) * np.sqrt(252) * 100.0), 2)
                snapshot["_realvol_20d"] = hv20
            if len(log_rets) >= 60:
                hv60 = round(float(log_rets.tail(60).std(ddof=1) * np.sqrt(252) * 100.0), 2)
                snapshot["_realvol_60d"] = hv60

            # Compare with IV30 if present
            iv30_val = snapshot.get("Energy IV30 Ann Pct")
            if iv30_val and "_realvol_60d" in snapshot:
                iv_f = float(iv30_val)
                spread = round(iv_f - snapshot["_realvol_60d"], 2)
                snapshot["_iv_minus_hv60_spread"] = spread
                snapshot["_options_vol_regime"] = "IV Underpriced / Cheap Vol (Favors Long LEAPS & Debit Spreads)" if spread < -10 else "IV Overpriced / Expensive Vol (Favors Credit Spreads & Premium Selling)" if spread > 10 else "Fairly Priced Options Vol"
    except Exception:
        pass

    # 9. Zone Dwell, Test Frequency, and Base Compression Analytics
    try:
        z_bot = float(snapshot.get("Long Entry Zone Bot") or 0)
        z_top = float(snapshot.get("Long Entry Zone Top") or 0)
        if z_bot > 0 and z_top > 0 and "high" in df.columns and "low" in df.columns:
            h_ser = pd.to_numeric(df["high"], errors="coerce")
            l_ser = pd.to_numeric(df["low"], errors="coerce")
            in_zone = (l_ser <= z_top) & (h_ser >= z_bot)

            # Consecutive bars currently in zone
            consec_zone = 0
            for z in reversed(in_zone):
                if z:
                    consec_zone += 1
                else:
                    break
            snapshot["_consecutive_bars_in_long_zone"] = consec_zone

            # Zone touches in last 30 bars
            touches_30b = int(in_zone.tail(30).sum())
            snapshot["_long_zone_touches_30b"] = touches_30b

            # Dwell Interpretation
            if consec_zone >= 5:
                snapshot["_long_zone_dwell_state"] = "Lingering / Saturated Zone (Risk of Support Weakening)"
            elif consec_zone == 1 and touches_30b <= 3:
                snapshot["_long_zone_dwell_state"] = "Fresh Zone Re-Test (High-Conviction Defense Bar)"
            elif touches_30b >= 8:
                snapshot["_long_zone_dwell_state"] = "Heavily Contested Zone (Multiple Tests / Compression)"
            else:
                snapshot["_long_zone_dwell_state"] = f"Zone Test ({consec_zone} consecutive bars)"

        # Darvas Box Duration
        box_top_val = float(snapshot.get("Darvas Box Top") or 0)
        if box_top_val > 0 and "high" in df.columns:
            h_ser = pd.to_numeric(df["high"], errors="coerce")
            under_box = h_ser <= box_top_val
            consec_box = 0
            for u in reversed(under_box):
                if u:
                    consec_box += 1
                else:
                    break
            snapshot["_darvas_box_duration_bars"] = consec_box
            if consec_box >= 12 and snapshot.get("_premove_darvas_state") == "IN BOX":
                snapshot["_darvas_base_status"] = f"Mature Base ({consec_box} bars) - High Breakout Compression"
            elif consec_box > 0:
                snapshot["_darvas_base_status"] = f"Consolidating ({consec_box} bars)"
    except Exception:
        pass

    # 10. Execute Decoupled Analytics Plugins
    try:
        from src.plugins.plugin_manager import enrich_datawindow_with_plugins
        ticker_name = ticker or snapshot.get("ticker") or snapshot.get("symbol") or "UNKNOWN"
        snapshot = enrich_datawindow_with_plugins(ticker_name, df, snapshot)
    except Exception:
        pass

    return snapshot


def csv_to_datawindow(
    csv_path: str,
    json_out_path: Optional[str] = None,
    ticker: Optional[str] = None,
) -> Tuple[Dict[str, Any], pd.DataFrame, Optional[float], Optional[float]]:
    """Parse a TradingView exported CSV into a Data Window snapshot dict & trailing metrics.

    Args:
        csv_path: Path to the downloaded CSV file.
        json_out_path: Optional output path to write <symbol>_datawindow.json.
        ticker: Optional ticker symbol. If omitted, inferred from file paths.

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

    # Verify column depth, row depth, and bar interval (daily)
    verify_csv_integrity(df)

    # Resolve ticker if not provided
    resolved_ticker = (
        ticker
        or _extract_ticker_from_path(json_out_path)
        or _extract_ticker_from_path(csv_path)
    )

    # Extract last row as snapshot dict (keyed by CSV header names)
    last_row = df.iloc[-1]
    snapshot = {}
    for col in df.columns:
        val = last_row[col]
        formatted = _format_datawindow_val(val)
        snapshot[col] = formatted

    if resolved_ticker:
        snapshot["ticker"] = resolved_ticker

    # Extract & stamp last bar date onto snapshot
    time_col = next((c for c in df.columns if c.lower() in ("time", "date", "datetime")), None)
    if time_col:
        try:
            last_dt = pd.to_datetime(last_row[time_col])
            snapshot["bar_date"] = last_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    # Enrich snapshot with decoded bitpacks, MTF, anchors, and plugins
    snapshot = decode_and_enrich_datawindow(snapshot, df, ticker=resolved_ticker)

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

        # 10-day annualized volatility via daily LOG returns (matching 42.8 calibration)
        if n >= 11:
            trailing_closes = close_series.iloc[-11:]
            if (trailing_closes > 0).all():
                log_returns = np.log(trailing_closes / trailing_closes.shift(1)).dropna()
                if len(log_returns) >= 10:
                    std_daily = float(log_returns.std(ddof=1))
                    calc_vol = float(std_daily * np.sqrt(252) * 100.0)
                    if not np.isnan(calc_vol):
                        realvol_10d = calc_vol

    return snapshot, df, realvol_10d, ret_10d
