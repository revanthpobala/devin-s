"""
src/plugins/htf_confluence_plugin.py

Plugin: Higher-Timeframe (HTF) Resampling & Moving Average Confluence.
Resamples daily OHLCV into Weekly and Monthly series to derive true HTF anchor levels.
"""

from typing import Any, Dict
import pandas as pd
import numpy as np

from src.plugins.base_plugin import BaseAnalyticsPlugin


class HTFConfluencePlugin(BaseAnalyticsPlugin):
    @property
    def name(self) -> str:
        return "htf_confluence"

    @property
    def description(self) -> str:
        return "Resamples daily bars to compute Weekly (W10/W20 EMA, W50 SMA) and Monthly (M3 EMA) structural anchors."

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        if df.empty or len(df) < 50 or "time" not in df.columns:
            return {}

        try:
            df_temp = df.copy()
            df_temp["dt"] = pd.to_datetime(df_temp["time"])
            df_temp = df_temp.sort_values("dt").set_index("dt")

            c_col = next((c for c in df_temp.columns if c.lower() == "close"), None)
            h_col = next((c for c in df_temp.columns if c.lower() == "high"), None)
            l_col = next((c for c in df_temp.columns if c.lower() == "low"), None)
            o_col = next((c for c in df_temp.columns if c.lower() == "open"), None)

            if not c_col:
                return {}

            # Resample to Weekly ('W-FRI')
            agg_dict = {c_col: "last"}
            if o_col: agg_dict[o_col] = "first"
            if h_col: agg_dict[h_col] = "max"
            if l_col: agg_dict[l_col] = "min"

            w_df = df_temp.resample("W-FRI").agg(agg_dict).dropna()
            w_closes = w_df[c_col]

            w_ema10 = round(float(w_closes.ewm(span=10, adjust=False).mean().iloc[-1]), 2) if len(w_closes) >= 10 else None
            w_ema20 = round(float(w_closes.ewm(span=20, adjust=False).mean().iloc[-1]), 2) if len(w_closes) >= 20 else None
            w_sma50 = round(float(w_closes.rolling(50).mean().iloc[-1]), 2) if len(w_closes) >= 50 else None

            # Resample to Monthly (ME in pandas >= 2.2, M in older pandas)
            try:
                m_df = df_temp.resample("ME").agg(agg_dict).dropna()
            except ValueError:
                m_df = df_temp.resample("M").agg(agg_dict).dropna()

            m_closes = m_df[c_col]
            m_ema3 = round(float(m_closes.ewm(span=3, adjust=False).mean().iloc[-1]), 2) if len(m_closes) >= 3 else None
            m_ema6 = round(float(m_closes.ewm(span=6, adjust=False).mean().iloc[-1]), 2) if len(m_closes) >= 6 else None

            levels = {}
            if w_ema10: levels["Weekly 10 EMA"] = w_ema10
            if w_ema20: levels["Weekly 20 EMA"] = w_ema20
            if w_sma50: levels["Weekly 50 SMA"] = w_sma50
            if m_ema3: levels["Monthly 3 EMA"] = m_ema3
            if m_ema6: levels["Monthly 6 EMA"] = m_ema6

            return {
                "_htf_levels": levels,
                "_w10_ema": w_ema10,
                "_w20_ema": w_ema20,
                "_w50_sma": w_sma50,
                "_m3_ema": m_ema3,
                "_htf_confluence_summary": f"Weekly 10 EMA: ${w_ema10} | Weekly 20 EMA: ${w_ema20} | Monthly 3 EMA: ${m_ema3}",
            }
        except Exception:
            return {}
