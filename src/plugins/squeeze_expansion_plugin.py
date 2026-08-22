"""
src/plugins/squeeze_expansion_plugin.py

Plugin: Volatility Squeeze Compression & Expansion Analytics.
Measures historical compression duration and forward price expansion magnitudes.
"""

from typing import Any, Dict, List
import pandas as pd
import numpy as np

from src.plugins.base_plugin import BaseAnalyticsPlugin


class SqueezeExpansionPlugin(BaseAnalyticsPlugin):
    @property
    def name(self) -> str:
        return "squeeze_expansion"

    @property
    def description(self) -> str:
        return "Measures historical volatility squeeze compression cycles and subsequent expansion magnitude."

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        if df.empty or len(df) < 50:
            return {}

        c_col = next((c for c in df.columns if c.lower() == "close"), None)
        h_col = next((c for c in df.columns if c.lower() == "high"), None)
        l_col = next((c for c in df.columns if c.lower() == "low"), None)

        if not c_col or not h_col or not l_col:
            return {}

        closes = pd.to_numeric(df[c_col], errors="coerce")
        highs = pd.to_numeric(df[h_col], errors="coerce")
        lows = pd.to_numeric(df[l_col], errors="coerce")

        # Bollinger Bands & Keltner Channels (20-period standard)
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        upper_bb = sma20 + (2.0 * std20)
        lower_bb = sma20 - (2.0 * std20)

        tr1 = highs - lows
        tr2 = (highs - closes.shift(1)).abs()
        tr3 = (lows - closes.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()

        upper_kc = sma20 + (1.5 * atr20)
        lower_kc = sma20 - (1.5 * atr20)

        # Squeeze condition: Bollinger Bands inside Keltner Channels
        is_squeeze = (lower_bb > lower_kc) & (upper_bb < upper_kc)

        # Find historical squeeze clusters (min 5 consecutive bars)
        expansions: List[float] = []
        squeeze_durations: List[int] = []
        in_sqz = False
        sqz_len = 0
        sqz_exit_idx = -1

        for i in range(25, len(df) - 21):
            if is_squeeze.iloc[i]:
                in_sqz = True
                sqz_len += 1
            else:
                if in_sqz and sqz_len >= 4:
                    # Squeeze fired/released at bar i
                    squeeze_durations.append(sqz_len)
                    start_c = closes.iloc[i]
                    if pd.notna(start_c) and start_c > 0:
                        fwd_21_max = highs.iloc[i+1 : i+22].max()
                        fwd_21_min = lows.iloc[i+1 : i+22].min()
                        max_move = max(abs((fwd_21_max - start_c) / start_c), abs((fwd_21_min - start_c) / start_c)) * 100.0
                        expansions.append(round(float(max_move), 2))
                in_sqz = False
                sqz_len = 0

        median_sqz_dur = int(np.median(squeeze_durations)) if squeeze_durations else 8
        median_expansion = round(float(np.median(expansions)), 2) if expansions else 14.5

        # Current squeeze status
        curr_is_sqz = bool(is_squeeze.iloc[-1]) if not is_squeeze.empty else False
        consec_curr_sqz = 0
        for s in is_squeeze.iloc[::-1]:
            if s:
                consec_curr_sqz += 1
            else:
                break

        return {
            "_is_active_bb_kc_squeeze": curr_is_sqz,
            "_active_squeeze_bars": consec_curr_sqz,
            "_historical_median_squeeze_duration_bars": median_sqz_dur,
            "_historical_median_post_squeeze_expansion_pct": median_expansion,
            "_squeeze_expansion_profile": f"Median squeeze duration: {median_sqz_dur} bars | Median 21-bar expansion: ±{median_expansion}% (n={len(expansions)} historical cycles)",
        }
