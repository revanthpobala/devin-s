"""
src/plugins/candlestick_patterns_plugin.py

Multi-Timeframe Candlestick & Pattern Analytics.
100% powered by the official C-based TA-Lib (Technical Analysis Library).
Executes all 61 standardized TA-Lib Pattern Recognition algorithms plus
official TA-Lib trend and volatility indicators across Daily, Weekly, and Monthly series.
"""

from typing import Any, Dict, List
import pandas as pd
import numpy as np
import logging
import talib

from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)


def _run_talib_pattern_recognition(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    times: List[str],
    timeframe_label: str = "Daily",
    lookback_bars: int = 5,
) -> Dict[str, Any]:
    """Execute all 61 official TA-Lib Pattern Recognition routines on OHLCV arrays."""
    n_bars = len(closes)
    if n_bars < 3:
        return {"active_patterns": [], "pattern_history": []}

    pattern_funcs = talib.get_function_groups().get("Pattern Recognition", [])
    raw_results = {}

    for func_name in pattern_funcs:
        fn = getattr(talib, func_name, None)
        if fn:
            try:
                res = fn(opens, highs, lows, closes)
                if np.any(res != 0):
                    raw_results[func_name] = res
            except Exception:
                pass

    # Active patterns on the latest closed bar
    active_patterns = []
    for func_name, res_arr in raw_results.items():
        score = int(res_arr[-1])
        if score != 0:
            name = func_name.replace("CDL", "").title()
            bias = "🟢 Bullish" if score > 0 else "🔴 Bearish"
            active_patterns.append(
                f"{bias} {timeframe_label} {name} (TA-Lib Score: {score:+d}) on {times[-1]}"
            )

    # Trailing pattern history
    pattern_history = []
    lb = min(lookback_bars, n_bars)
    for i in range(n_bars - lb, n_bars):
        bar_date = times[i]
        c = float(closes[i])
        bar_signals = []
        for func_name, res_arr in raw_results.items():
            score = int(res_arr[i])
            if score != 0:
                name = func_name.replace("CDL", "").title()
                bias = "🟢 Bullish" if score > 0 else "🔴 Bearish"
                bar_signals.append({
                    "pattern": func_name,
                    "name": f"{bias} {name}",
                    "talib_score": score,
                })
        if bar_signals:
            pattern_history.append({
                "date": bar_date,
                "close": round(c, 2),
                "patterns": bar_signals,
            })

    return {
        "active_patterns": active_patterns,
        "pattern_history": pattern_history,
    }


def _run_talib_htf_indicators(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timeframe_label: str = "Weekly",
) -> Dict[str, Any]:
    """Execute standardized TA-Lib moving averages and volatility bands on HTF arrays."""
    n_bars = len(closes)
    if n_bars < 3:
        return {}

    indicators = {}
    if timeframe_label == "Weekly":
        if n_bars >= 10:
            w_ema10 = talib.EMA(closes, timeperiod=10)
            if not np.isnan(w_ema10[-1]):
                indicators["Weekly EMA 10"] = round(float(w_ema10[-1]), 2)
        if n_bars >= 20:
            w_ema20 = talib.EMA(closes, timeperiod=20)
            if not np.isnan(w_ema20[-1]):
                indicators["Weekly EMA 20"] = round(float(w_ema20[-1]), 2)
        if n_bars >= 50:
            w_sma50 = talib.SMA(closes, timeperiod=50)
            if not np.isnan(w_sma50[-1]):
                indicators["Weekly SMA 50"] = round(float(w_sma50[-1]), 2)
        if n_bars >= 15:
            w_atr14 = talib.ATR(highs, lows, closes, timeperiod=14)
            if not np.isnan(w_atr14[-1]):
                indicators["Weekly ATR 14"] = round(float(w_atr14[-1]), 2)
            w_rsi14 = talib.RSI(closes, timeperiod=14)
            if not np.isnan(w_rsi14[-1]):
                indicators["Weekly RSI 14"] = round(float(w_rsi14[-1]), 2)
    elif timeframe_label == "Monthly":
        if n_bars >= 3:
            m_ema3 = talib.EMA(closes, timeperiod=3)
            if not np.isnan(m_ema3[-1]):
                indicators["Monthly EMA 3"] = round(float(m_ema3[-1]), 2)
        if n_bars >= 6:
            m_ema6 = talib.EMA(closes, timeperiod=6)
            if not np.isnan(m_ema6[-1]):
                indicators["Monthly EMA 6"] = round(float(m_ema6[-1]), 2)
        if n_bars >= 10:
            m_ema10 = talib.EMA(closes, timeperiod=10)
            if not np.isnan(m_ema10[-1]):
                indicators["Monthly EMA 10"] = round(float(m_ema10[-1]), 2)
        if n_bars >= 6:
            m_atr6 = talib.ATR(highs, lows, closes, timeperiod=6)
            if not np.isnan(m_atr6[-1]):
                indicators["Monthly ATR 6"] = round(float(m_atr6[-1]), 2)
            m_rsi6 = talib.RSI(closes, timeperiod=6)
            if not np.isnan(m_rsi6[-1]):
                indicators["Monthly RSI 6"] = round(float(m_rsi6[-1]), 2)

    return indicators


class CandlestickPatternsPlugin(BaseAnalyticsPlugin):
    """Detects multi-timeframe candlestick and price patterns using 100% official TA-Lib routines."""

    @property
    def name(self) -> str:
        return "candlestick_patterns"

    @property
    def description(self) -> str:
        return (
            "Multi-timeframe candlestick pattern recognition powered strictly by the official TA-Lib library: "
            "Evaluates all 61 standard TA-Lib pattern routines across Daily, Weekly, and Monthly resampled series."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        if df.empty or len(df) < 5:
            return {
                "active_candlestick_patterns": [],
                "candlestick_summary": "Insufficient historical bar data.",
            }

        cols = {c.lower(): c for c in df.columns}
        o_col = cols.get("open", "open")
        h_col = cols.get("high", "high")
        l_col = cols.get("low", "low")
        c_col = cols.get("close", "close")
        v_col = cols.get("volume", "volume")
        t_col = cols.get("time", cols.get("date", "time"))

        df_sorted = df.copy()
        if t_col in df_sorted:
            df_sorted["dt"] = pd.to_datetime(df_sorted[t_col])
            df_sorted = df_sorted.sort_values("dt").set_index("dt")

        # 1. Daily TA-Lib Pattern Recognition
        d_opens = df_sorted[o_col].values.astype(float)
        d_highs = df_sorted[h_col].values.astype(float)
        d_lows = df_sorted[l_col].values.astype(float)
        d_closes = df_sorted[c_col].values.astype(float)
        d_times = [str(x)[:10] for x in df_sorted.index] if isinstance(df_sorted.index, pd.DatetimeIndex) else [f"D-{i}" for i in range(len(d_closes))]

        daily_res = _run_talib_pattern_recognition(d_opens, d_highs, d_lows, d_closes, d_times, "Daily")

        # 2. Resample to Weekly ('W-FRI') & Run TA-Lib
        agg_dict = {c_col: "last", o_col: "first", h_col: "max", l_col: "min"}
        if v_col in df_sorted:
            agg_dict[v_col] = "sum"

        weekly_res = {"active_patterns": [], "pattern_history": []}
        weekly_indicators = {}
        try:
            w_df = df_sorted.resample("W-FRI").agg(agg_dict).dropna()
            w_opens = w_df[o_col].values.astype(float)
            w_highs = w_df[h_col].values.astype(float)
            w_lows = w_df[l_col].values.astype(float)
            w_closes = w_df[c_col].values.astype(float)
            w_times = [str(x)[:10] for x in w_df.index]

            weekly_res = _run_talib_pattern_recognition(w_opens, w_highs, w_lows, w_closes, w_times, "Weekly")
            weekly_indicators = _run_talib_htf_indicators(w_highs, w_lows, w_closes, "Weekly")
        except Exception as e_w:
            logger.debug(f"[{ticker}] Weekly TA-Lib error: {e_w}")

        # 3. Resample to Monthly ('ME' / 'M') & Run TA-Lib
        monthly_res = {"active_patterns": [], "pattern_history": []}
        monthly_indicators = {}
        try:
            try:
                m_df = df_sorted.resample("ME").agg(agg_dict).dropna()
            except ValueError:
                m_df = df_sorted.resample("M").agg(agg_dict).dropna()
            m_opens = m_df[o_col].values.astype(float)
            m_highs = m_df[h_col].values.astype(float)
            m_lows = m_df[l_col].values.astype(float)
            m_closes = m_df[c_col].values.astype(float)
            m_times = [str(x)[:10] for x in m_df.index]

            monthly_res = _run_talib_pattern_recognition(m_opens, m_highs, m_lows, m_closes, m_times, "Monthly")
            monthly_indicators = _run_talib_htf_indicators(m_highs, m_lows, m_closes, "Monthly")
        except Exception as e_m:
            logger.debug(f"[{ticker}] Monthly TA-Lib error: {e_m}")

        # Consolidate standard TA-Lib signals
        all_signals = []
        if daily_res["active_patterns"]:
            all_signals.extend(daily_res["active_patterns"])
        if weekly_res["active_patterns"]:
            all_signals.extend(weekly_res["active_patterns"])
        if monthly_res["active_patterns"]:
            all_signals.extend(monthly_res["active_patterns"])

        htf_ind_str = " | ".join(f"{k}: ${v}" for k, v in {**weekly_indicators, **monthly_indicators}.items())

        summary_text = (
            " | ".join(all_signals)
            if all_signals
            else "No active TA-Lib reversal pattern triggered on current bar."
        )

        return {
            "active_candlestick_patterns": all_signals,
            "candlestick_summary": summary_text,
            "daily_patterns": daily_res["active_patterns"],
            "weekly_patterns": weekly_res["active_patterns"],
            "monthly_patterns": monthly_res["active_patterns"],
            "htf_indicators": {**weekly_indicators, **monthly_indicators},
            "htf_indicator_summary": htf_ind_str,
            "recent_daily_pattern_history": daily_res["pattern_history"],
            "recent_weekly_pattern_history": weekly_res["pattern_history"],
            "recent_monthly_pattern_history": monthly_res["pattern_history"],
            "talib_engine": f"Official TA-Lib C-Library v{talib.__version__} (61 Pattern Functions)",
        }
