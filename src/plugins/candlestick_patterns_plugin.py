"""
src/plugins/candlestick_patterns_plugin.py

Industry-standard, vetted TA-Lib candlestick and price-action pattern recognition.
Executes official TA-Lib C-routines for 61 classical Japanese candlestick patterns
combined with gap-fill retest analysis directly on OHLCV data.
"""

from typing import Any, Dict, List
import pandas as pd
import numpy as np
import talib

from src.plugins.base_plugin import BaseAnalyticsPlugin


# Human-friendly descriptions for standard TA-Lib pattern codes
TALIB_PATTERN_METADATA = {
    "CDLHAMMER": {"name": "Hammer (Bullish Pin Bar)", "type": "BULLISH", "desc": "Rejection of lower price levels with long lower shadow."},
    "CDLSHOOTINGSTAR": {"name": "Shooting Star (Bearish Pin Bar)", "type": "BEARISH", "desc": "Rejection of higher price levels with long upper shadow."},
    "CDLMORNINGSTAR": {"name": "Morning Star", "type": "BULLISH", "desc": "3-bar bottom reversal cluster."},
    "CDLEVENINGSTAR": {"name": "Evening Star", "type": "BEARISH", "desc": "3-bar top reversal cluster."},
    "CDLMORNINGDOJISTAR": {"name": "Morning Doji Star", "type": "BULLISH", "desc": "3-bar bottom reversal with Doji star."},
    "CDLEVENINGDOJISTAR": {"name": "Evening Doji Star", "type": "BEARISH", "desc": "3-bar top reversal with Doji star."},
    "CDLENGULFING": {"name": "Engulfing", "type": "DYNAMIC", "desc": "Candle body completely swallows prior body."},
    "CDLDRAGONFLYDOJI": {"name": "Dragonfly Doji", "type": "BULLISH", "desc": "Capitulation reversal with long lower shadow and open=close=high."},
    "CDLGRAVESTONEDOJI": {"name": "Gravestone Doji", "type": "BEARISH", "desc": "Overhead exhaustion with long upper shadow and open=close=low."},
    "CDLLONGLEGGEDDOJI": {"name": "Long-Legged Doji", "type": "NEUTRAL", "desc": "Extreme market indecision with long upper and lower wicks."},
    "CDLDOJI": {"name": "Doji", "type": "NEUTRAL", "desc": "Tight open/close compression signaling potential turning point."},
    "CDL3WHITESOLDIERS": {"name": "Three White Soldiers", "type": "BULLISH", "desc": "3 consecutive strong green bars with rising closes (institutional ignition)."},
    "CDL3BLACKCROWS": {"name": "Three Black Crows", "type": "BEARISH", "desc": "3 consecutive heavy red bars with falling closes (distribution breakdown)."},
    "CDLPIERCING": {"name": "Piercing Line", "type": "BULLISH", "desc": "Bullish thrust closing >50% into prior red candle body."},
    "CDLDARKCLOUDCOVER": {"name": "Dark Cloud Cover", "type": "BEARISH", "desc": "Bearish thrust closing >50% into prior green candle body."},
    "CDLHARAMI": {"name": "Harami (Inside Bar)", "type": "DYNAMIC", "desc": "Price coiled entirely inside prior range (compression)."},
    "CDLHARAMICROSS": {"name": "Harami Cross", "type": "DYNAMIC", "desc": "Doji coiled inside prior range (high-volatility squeeze)."},
    "CDLMARUBOZU": {"name": "Marubozu", "type": "DYNAMIC", "desc": "Full solid trend bar with negligible wicks (pure momentum)."},
    "CDLINVERTEDHAMMER": {"name": "Inverted Hammer", "type": "BULLISH", "desc": "Bottom reversal attempt with upper wick."},
    "CDLHIKKAKE": {"name": "Hikkake Pattern", "type": "DYNAMIC", "desc": "Trap/false breakout reversal pattern."},
    "CDLMATCHINGLOW": {"name": "Matching Low (Tweezer Bottom)", "type": "BULLISH", "desc": "Two consecutive candles sharing exact support low."},
    "CDLSTICKSANDWICH": {"name": "Stick Sandwich", "type": "BULLISH", "desc": "Support floor defense sandwich."},
    "CDLTAKURI": {"name": "Takuri (Dragonfly with very long lower shadow)", "type": "BULLISH", "desc": "Severe lower shadow rejection at support floor."},
    "CDLBELTHOLD": {"name": "Belt-Hold", "type": "DYNAMIC", "desc": "Strong opening thrust holding price extreme throughout session."},
    "CDLBREAKAWAY": {"name": "Breakaway", "type": "DYNAMIC", "desc": "5-bar acceleration away from consolidation."},
    "CDLSPINNINGTOP": {"name": "Spinning Top", "type": "NEUTRAL", "desc": "Small real body with balanced upper/lower shadows."},
}


class CandlestickPatternsPlugin(BaseAnalyticsPlugin):
    """Detects industry-standard candlestick patterns using official TA-Lib C-routines."""

    @property
    def name(self) -> str:
        return "candlestick_patterns"

    @property
    def description(self) -> str:
        return (
            "Detects validated, industry-standard TA-Lib candlestick patterns: "
            "Hammers, Pin Bars, Engulfing, Morning/Evening Stars, Dojis, Crows/Soldiers, and Gap Retests."
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
        t_col = cols.get("time", "time")

        df_sorted = df.copy().reset_index(drop=True)
        n_bars = len(df_sorted)

        opens = df_sorted[o_col].values.astype(float)
        highs = df_sorted[h_col].values.astype(float)
        lows = df_sorted[l_col].values.astype(float)
        closes = df_sorted[c_col].values.astype(float)
        times = df_sorted[t_col].values if t_col in df_sorted else [f"Bar-{i}" for i in range(n_bars)]

        # Run all official TA-Lib Pattern Recognition Functions
        talib_funcs = talib.get_function_groups().get("Pattern Recognition", [])
        raw_talib_results = {}

        for func_name in talib_funcs:
            fn = getattr(talib, func_name, None)
            if fn:
                try:
                    res = fn(opens, highs, lows, closes)
                    if np.any(res != 0):
                        raw_talib_results[func_name] = res
                except Exception:
                    pass

        # Build trailing 10-bar pattern timeline
        lookback = min(10, n_bars)
        pattern_history: List[Dict[str, Any]] = []

        for i in range(n_bars - lookback, n_bars):
            bar_date = str(times[i])
            c = float(closes[i])
            l = float(lows[i])
            h = float(highs[i])
            o = float(opens[i])
            total_range = max(h - l, 0.0001)
            lower_wick_pct = round(((min(o, c) - l) / total_range) * 100, 1)
            upper_wick_pct = round(((h - max(o, c)) / total_range) * 100, 1)

            bar_patterns = []

            for fn_name, res_arr in raw_talib_results.items():
                val = int(res_arr[i])
                if val != 0:
                    meta = TALIB_PATTERN_METADATA.get(fn_name, {
                        "name": fn_name.replace("CDL", "").title(),
                        "type": "BULLISH" if val > 0 else "BEARISH",
                        "desc": "Standard TA-Lib pattern signal.",
                    })
                    
                    is_bull = val > 0 or (val == 0 and meta["type"] == "BULLISH")
                    icon = "🟢" if is_bull else ("🔴" if val < 0 else "🟡")
                    sig_label = f"{icon} {meta['name']}"
                    
                    desc = meta["desc"]
                    if "Hammer" in meta["name"] or "Takuri" in meta["name"]:
                        desc = f"Rejected low of ${l:.2f} with {lower_wick_pct}% lower wick (TA-Lib: {val:+d})."
                    elif "Shooting Star" in meta["name"]:
                        desc = f"Rejected high of ${h:.2f} with {upper_wick_pct}% upper wick (TA-Lib: {val:+d})."

                    bar_patterns.append({
                        "pattern": fn_name,
                        "talib_score": val,
                        "signal": sig_label,
                        "description": desc,
                        "rejected_level": round(l if is_bull else h, 2),
                    })

            # Also check Inside Day / Compression if not already covered
            if i > 0 and float(highs[i]) < float(highs[i-1]) and float(lows[i]) > float(lows[i-1]):
                if not any(p["pattern"] in ("CDLHARAMI", "CDLHARAMICROSS") for p in bar_patterns):
                    bar_patterns.append({
                        "pattern": "INSIDE_DAY",
                        "talib_score": 50,
                        "signal": "🟡 Inside Day Compression",
                        "description": f"Price coiled entirely inside prior range (${lows[i-1]:.2f} - ${highs[i-1]:.2f}).",
                        "rejected_level": round(l, 2),
                    })

            if bar_patterns:
                pattern_history.append({
                    "date": bar_date,
                    "close": round(c, 2),
                    "patterns": bar_patterns,
                })

        # Gap-Fill & Retest Opportunities (Trailing 25 bars)
        gap_retest_info = None
        curr_l = float(lows[-1])
        curr_h = float(highs[-1])

        for g_idx in range(max(0, n_bars - 25), n_bars - 1):
            prior_close = float(closes[g_idx])
            next_open = float(opens[g_idx + 1])
            gap_pct = ((next_open - prior_close) / prior_close) * 100.0

            if gap_pct >= 2.5:
                gap_top = max(prior_close, next_open, float(lows[g_idx + 1]))
                gap_bottom = min(prior_close, float(highs[g_idx]))
                gap_date = str(times[g_idx + 1])

                if curr_l <= gap_top * 1.01 and curr_h >= gap_bottom * 0.99:
                    gap_retest_info = {
                        "gap_type": "BULLISH_EARNINGS_OR_CATALYST_GAP",
                        "gap_date": gap_date,
                        "gap_window": f"${gap_bottom:.2f} - ${gap_top:.2f}",
                        "status": "🎯 ACTIVE_GAP_RETEST_IN_PROGRESS",
                        "description": (
                            f"Price is currently retesting the {gap_date} gap floor (${gap_bottom:.2f} - ${gap_top:.2f}) "
                            f"after an earlier expansion to ${np.max(highs[g_idx+1:]):.2f}."
                        ),
                    }
                    break

        active_patterns_list = []
        if pattern_history and pattern_history[-1]["date"] == str(times[-1]):
            for p in pattern_history[-1]["patterns"]:
                active_patterns_list.append(f"{p['signal']}: {p['description']}")

        if gap_retest_info:
            active_patterns_list.append(f"🎯 Gap Retest: {gap_retest_info['description']}")

        summary_text = (
            " | ".join(active_patterns_list)
            if active_patterns_list
            else "No prominent reversal or compression candlestick pattern active on current bar."
        )

        return {
            "active_candlestick_patterns": active_patterns_list,
            "candlestick_summary": summary_text,
            "gap_retest_analysis": gap_retest_info,
            "recent_pattern_history": pattern_history[-4:] if pattern_history else [],
            "talib_engine": "TA-Lib C-Library v0.7.1",
        }
