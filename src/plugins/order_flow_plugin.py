"""
src/plugins/order_flow_plugin.py

Order Flow & Institutional Volume Profile Analytics Plugin.
Computes:
1. Multi-horizon Volume Accumulation/Distribution ratios (20d, 60d, 120d Up vs Down Volume).
2. Chaikin Money Flow (CMF 20) & Money Flow Multiplier.
3. Money Flow Index (MFI 14) & On-Balance Volume (OBV) divergence.
4. Volume Profile Liquidity Nodes (VP POC, VAH, VAL, HVN shelves).
5. Anchored VWAP distances and options gamma walls (Call/Put Walls).
"""

import logging
from typing import Any, Dict
import numpy as np
import pandas as pd

from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float | None:
    if val is None or val == "" or str(val).strip().lower() in ("none", "n/a", "null", "nan"):
        return None
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").strip()
        f = float(val)
        return f if not np.isnan(f) else None
    except (ValueError, TypeError):
        return None


class OrderFlowPlugin(BaseAnalyticsPlugin):
    @property
    def name(self) -> str:
        return "order_flow"

    @property
    def description(self) -> str:
        return (
            "Computes institutional order flow metrics: 20d/60d/120d volume accumulation ratios, "
            "Chaikin Money Flow (CMF), OBV divergences, Volume Profile liquidity nodes (POC/VAH/VAL/HVN), "
            "and options dealer gamma walls."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        if df.empty or len(df) < 20:
            return {"_order_flow_status": "Insufficient historical bar data"}

        # Find price and volume columns
        c_col = next((c for c in df.columns if c.lower() == "close"), None)
        h_col = next((c for c in df.columns if c.lower() == "high"), None)
        l_col = next((c for c in df.columns if c.lower() == "low"), None)
        v_col = next((c for c in df.columns if c.lower() == "volume"), None)

        if not (c_col and h_col and l_col and v_col):
            return {"_order_flow_status": "Missing required OHLCV columns"}

        closes = pd.to_numeric(df[c_col], errors="coerce")
        highs = pd.to_numeric(df[h_col], errors="coerce")
        lows = pd.to_numeric(df[l_col], errors="coerce")
        volumes = pd.to_numeric(df[v_col], errors="coerce").fillna(0.0)

        spot = _safe_float(dw.get("close")) or float(closes.iloc[-1])

        # 1. Multi-Horizon Up/Down Volume Accumulation Ratios
        price_diff = closes.diff()
        for window in [20, 60, 120]:
            if len(df) >= window:
                w_diff = price_diff.tail(window)
                w_vol = volumes.tail(window)
                up_vol = float(w_vol[w_diff > 0].sum())
                dn_vol = float(w_vol[w_diff < 0].sum())
                ratio = round(up_vol / dn_vol, 3) if dn_vol > 0 else 999.0
                results[f"_vol_accumulation_ratio_{window}d"] = ratio
                if window == 60:
                    results["_volume_acc_dist_ratio_60d"] = ratio
                    results["_institutional_flow_bias"] = (
                        "Institutional Accumulation"
                        if ratio >= 1.15
                        else "Institutional Distribution"
                        if ratio <= 0.85
                        else "Balanced Flow"
                    )

        # 2. Chaikin Money Flow (CMF 20)
        # MFM = ((Close - Low) - (High - Close)) / (High - Low)
        hl_range = highs - lows
        hl_range = hl_range.replace(0, np.nan)
        mfm = ((closes - lows) - (highs - closes)) / hl_range
        mfm = mfm.fillna(0.0)
        mfv = mfm * volumes

        tot_vol_20 = volumes.tail(20).sum()
        cmf_20 = float(mfv.tail(20).sum() / tot_vol_20) if tot_vol_20 > 0 else 0.0
        results["_chaikin_money_flow_20d"] = round(cmf_20, 3)
        results["_cmf_state"] = (
            "Strong Buying Pressure (CMF > +0.15)"
            if cmf_20 >= 0.15
            else "Moderate Inflow (CMF > 0)"
            if cmf_20 > 0.0
            else "Strong Selling Pressure (CMF < -0.15)"
            if cmf_20 <= -0.15
            else "Moderate Outflow (CMF < 0)"
        )

        # 3. On-Balance Volume (OBV) & Divergence
        obv = (np.sign(price_diff.fillna(0.0)) * volumes).cumsum()
        if len(obv) >= 20:
            obv_20d_slope = float((obv.iloc[-1] - obv.iloc[-20]) / (abs(obv.iloc[-20]) + 1e-6))
            price_20d_ret = float((closes.iloc[-1] - closes.iloc[-20]) / closes.iloc[-20])

            # Divergence check
            if price_20d_ret > 0.05 and obv_20d_slope < -0.02:
                obv_div = "Bearish Divergence (Price rising, Volume outflow)"
            elif price_20d_ret < -0.05 and obv_20d_slope > 0.02:
                obv_div = "Bullish Divergence (Price falling, Volume accumulation)"
            else:
                obv_div = "Confirmed (Price and Volume aligned)"

            results["_obv_20d_trend"] = "Rising" if obv_20d_slope > 0 else "Falling"
            results["_obv_divergence"] = obv_div

        # 4. Volume Profile Liquidity Nodes (From Data Window)
        vp_poc = _safe_float(dw.get("VP POC"))
        vp_vah = _safe_float(dw.get("VP VAH"))
        vp_val = _safe_float(dw.get("VP VAL"))
        vp_hvn_above = _safe_float(dw.get("VP HVN Above"))
        vp_hvn_below = _safe_float(dw.get("VP HVN Below"))

        vp_metrics = {}
        if vp_poc:
            vp_metrics["poc_price"] = vp_poc
            vp_metrics["poc_dist_pct"] = round((spot - vp_poc) / vp_poc * 100, 2)

        if vp_vah and vp_val:
            vp_metrics["vah_price"] = vp_vah
            vp_metrics["val_price"] = vp_val
            vp_metrics["in_value_area"] = vp_val <= spot <= vp_vah
            vp_metrics["value_area_state"] = (
                "Inside Value Area (Fair Value)"
                if vp_val <= spot <= vp_vah
                else "Above Value Area (Premium/Extension)"
                if spot > vp_vah
                else "Below Value Area (Discount/Value)"
            )

        if vp_hvn_above:
            vp_metrics["hvn_overhead_resistance"] = vp_hvn_above
        if vp_hvn_below:
            vp_metrics["hvn_underlying_support"] = vp_hvn_below

        if vp_metrics:
            results["_volume_profile_liquidity"] = vp_metrics

        # 5. Anchored VWAP & Cost Basis Confluence
        avwap_sup = _safe_float(dw.get("AVWAP Support"))
        avwap_res = _safe_float(dw.get("AVWAP Resistance"))
        if avwap_sup:
            results["_avwap_support_price"] = avwap_sup
            results["_dist_to_avwap_support_pct"] = round((spot - avwap_sup) / avwap_sup * 100, 2)
        if avwap_res:
            results["_avwap_resistance_price"] = avwap_res
            results["_dist_to_avwap_resistance_pct"] = round((avwap_res - spot) / spot * 100, 2)

        # 6. Options Gamma / Order Flow Walls
        call_wall = _safe_float(dw.get("_call_wall") or dw.get("Call Wall"))
        put_wall = _safe_float(dw.get("_put_wall") or dw.get("Put Wall"))
        if call_wall:
            results["_options_major_call_wall"] = call_wall
        if put_wall:
            results["_options_major_put_wall"] = put_wall

        # Summary Synthesis Line
        summary_verdict = (
            f"Institutional {results.get('_institutional_flow_bias', 'Flow')} "
            f"[CMF: {results.get('_chaikin_money_flow_20d', 0.0):+.3f}, "
            f"60d AccRatio: {results.get('_volume_acc_dist_ratio_60d', 1.0):.2f}x]"
        )
        results["_order_flow_summary"] = summary_verdict

        return results
