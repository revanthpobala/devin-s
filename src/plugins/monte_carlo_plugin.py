"""
src/plugins/monte_carlo_plugin.py

Deterministic CPU Quantitative Analytics Plugin:
1. Core Technical Structure: 52W High/Low, Moving Average Extensions (SMA20/50/200, EMA8/13/21), ATR14, Unfilled Gaps.
2. Volatility Risk Premium (VRP): Realized Volatilities (HV10/20/30/60/90), Implied Volatility (IV30), IV-HV spread.
3. 20,000-Path Vectorized Monte Carlo Simulation:
   - Geometric Brownian Motion with Ito drift correction across 21d, 45d, 63d, 126d horizons.
   - First-passage barrier probabilities: P(Target 1 Hit before Stop Loss), P(Stop Loss Hit before Target).
   - Multi-horizon percentile price cones: 5th, 25th, 50th (median), 75th, 95th percentiles.
   - Key structural barrier touch probabilities (AVWAPs, Darvas boundaries, 52W High).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float | None:
    if val is None or val == "" or str(val).strip().lower() in ("none", "n/a", "null", "nan"):
        return None
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").replace("%", "").strip()
        f = float(val)
        return f if not np.isnan(f) else None
    except (ValueError, TypeError):
        return None


class MonteCarloPlugin(BaseAnalyticsPlugin):
    """Deterministic CPU plugin for 20,000-path Monte Carlo simulations, moving average extensions, and VRP."""

    @property
    def name(self) -> str:
        return "monte_carlo"

    @property
    def description(self) -> str:
        return (
            "Performs 20,000-path vectorized Geometric Brownian Motion Monte Carlo simulation across 21d, 45d, 63d, "
            "and 126d horizons. Computes P(Target before Stop), P(Stop before Target), percentile cones (5th to 95th), "
            "barrier touch probabilities, core moving average extensions (SMA 20/50/200, EMA 8/13/21), and IV vs HV spread."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        safe_ticker = ticker.strip().upper()
        if df.empty or len(df) < 20:
            return {"_monte_carlo_status": "Insufficient historical bar data"}

        # Resolve case-insensitive columns
        c_col = next((c for c in df.columns if c.lower() == "close"), None)
        h_col = next((c for c in df.columns if c.lower() == "high"), None)
        l_col = next((c for c in df.columns if c.lower() == "low"), None)
        v_col = next((c for c in df.columns if c.lower() == "volume"), None)

        if not (c_col and h_col and l_col):
            return {"_monte_carlo_status": "Missing required price columns"}

        closes = pd.to_numeric(df[c_col], errors="coerce").dropna()
        highs = pd.to_numeric(df[h_col], errors="coerce").dropna()
        lows = pd.to_numeric(df[l_col], errors="coerce").dropna()

        if len(closes) < 20:
            return {"_monte_carlo_status": "Insufficient valid price rows"}

        S0 = _safe_float(dw.get("close")) or float(closes.iloc[-1])

        # -------------------------------------------------------------
        # 1. CORE TECHNICAL EXTENSIONS & MOVING AVERAGES
        # -------------------------------------------------------------
        lookback_52w = min(252, len(highs))
        high_52w = float(highs.tail(lookback_52w).max())
        low_52w = float(lows.tail(lookback_52w).min())

        dist_52w_high_pct = ((S0 - high_52w) / high_52w) * 100 if high_52w > 0 else 0.0
        dist_52w_low_pct = ((S0 - low_52w) / low_52w) * 100 if low_52w > 0 else 0.0

        sma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else S0
        sma50 = float(closes.tail(50).mean()) if len(closes) >= 50 else S0
        sma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else S0

        dist_sma20_pct = ((S0 - sma20) / sma20) * 100 if sma20 > 0 else 0.0
        dist_sma50_pct = ((S0 - sma50) / sma50) * 100 if sma50 > 0 else 0.0
        dist_sma200_pct = ((S0 - sma200) / sma200) * 100 if sma200 > 0 else 0.0

        ema8 = float(closes.ewm(span=8, adjust=False).mean().iloc[-1])
        ema13 = float(closes.ewm(span=13, adjust=False).mean().iloc[-1])
        ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])

        # True Range & ATR14
        prev_closes = closes.shift(1)
        tr1 = highs - lows
        tr2 = (highs - prev_closes).abs()
        tr3 = (lows - prev_closes).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = float(tr.tail(14).mean()) if len(tr) >= 14 else float(tr.mean())
        atr14_pct = (atr14 / S0) * 100 if S0 > 0 else 0.0

        # Unfilled gap detection (last 30 bars)
        recent_bars = min(30, len(df))
        unfilled_gaps = []
        for i in range(len(df) - recent_bars + 1, len(df)):
            prev_h = float(highs.iloc[i - 1])
            prev_l = float(lows.iloc[i - 1])
            curr_l = float(lows.iloc[i])
            curr_h = float(highs.iloc[i])
            if curr_l > prev_h:  # Gap Up
                unfilled_gaps.append(f"Gap Up [${prev_h:.2f} – ${curr_l:.2f}]")
            elif curr_h < prev_l:  # Gap Down
                unfilled_gaps.append(f"Gap Down [${curr_h:.2f} – ${prev_l:.2f}]")

        # -------------------------------------------------------------
        # 2. VOLATILITY RISK PREMIUM (IV vs HV)
        # -------------------------------------------------------------
        log_rets = np.diff(np.log(closes.values))
        mu_daily = float(np.mean(log_rets))
        sigma_daily = float(np.std(log_rets, ddof=1))

        hv10 = float(np.std(log_rets[-10:], ddof=1) * math.sqrt(252) * 100) if len(log_rets) >= 10 else sigma_daily * math.sqrt(252) * 100
        hv20 = float(np.std(log_rets[-20:], ddof=1) * math.sqrt(252) * 100) if len(log_rets) >= 20 else hv10
        hv30 = float(np.std(log_rets[-30:], ddof=1) * math.sqrt(252) * 100) if len(log_rets) >= 30 else hv20
        hv60 = float(np.std(log_rets[-60:], ddof=1) * math.sqrt(252) * 100) if len(log_rets) >= 60 else hv30
        hv90 = float(np.std(log_rets[-90:], ddof=1) * math.sqrt(252) * 100) if len(log_rets) >= 90 else hv60

        iv30_raw = (
            _safe_float(dw.get("Energy IV30 Ann Pct"))
            or _safe_float(dw.get("tastytrade_iv_30d"))
            or _safe_float(dw.get("IV30"))
            or _safe_float(dw.get("iv30"))
        )
        iv30 = float(iv30_raw) if iv30_raw is not None else None

        if iv30 is not None and iv30 > 0:
            iv_hv_diff = round(iv30 - hv20, 2)
            if iv_hv_diff > 25:
                vrp_regime = "EXTREME_VOLATILITY_INFLATION (Favor Defined-Risk Credit Spreads / High Premium Selling)"
            elif iv_hv_diff > 8:
                vrp_regime = "MODERATE_VOLATILITY_INFLATION (Credit Spreads favored over outright Long calls/puts)"
            elif iv_hv_diff < -8:
                vrp_regime = "CHEAP_VOLATILITY (Options underpriced vs realized movement — Favor Debit Spreads / LEAPS)"
            else:
                vrp_regime = "FAIRLY_PRICED_VOLATILITY (Balanced Debit / Credit Regime)"
        else:
            iv_hv_diff = None
            vrp_regime = "HISTORICAL_ONLY (No Live IV Available)"

        # -------------------------------------------------------------
        # 3. VECTORIZED MONTE CARLO SIMULATION (20,000 Paths)
        # -------------------------------------------------------------
        N = 20000
        rng = np.random.default_rng(42)

        # Resolve Trade Setup Anchors (Pine / DataWindow)
        pine_target1 = (
            _safe_float(dw.get("Long Target"))
            or _safe_float(dw.get("Long Target T1 Waypoint"))
            or _safe_float(dw.get("target_1"))
            or round(S0 * 1.15, 2)
        )
        pine_stop = (
            _safe_float(dw.get("Long Stop Loss"))
            or _safe_float(dw.get("tactical_stop"))
            or round(S0 * 0.92, 2)
        )
        pine_target2 = _safe_float(dw.get("Long Target 2")) or _safe_float(dw.get("target_2"))

        # Other structural barriers to test touch probability
        barriers: Dict[str, float] = {}
        avwap = _safe_float(dw.get("Anchored VWAP")) or _safe_float(dw.get("AVWAP"))
        if avwap:
            barriers[f"AVWAP (${avwap:.2f})"] = avwap

        darvas_top = _safe_float(dw.get("Darvas Box Top"))
        if darvas_top and darvas_top > 0:
            barriers[f"Darvas Ceiling (${darvas_top:.2f})"] = darvas_top

        darvas_bot = _safe_float(dw.get("Darvas Box Bot"))
        if darvas_bot and darvas_bot > 0:
            barriers[f"Darvas Floor (${darvas_bot:.2f})"] = darvas_bot

        if high_52w > S0:
            barriers[f"52W High (${high_52w:.2f})"] = high_52w

        # Use blended or historical daily vol for simulation
        sim_sigma_daily = (iv30 / 100 / math.sqrt(252)) if (iv30 and iv30 > 0) else sigma_daily

        horizons = [21, 45, 63, 126]
        mc_results: Dict[int, Dict[str, Any]] = {}

        for H in horizons:
            # Geometric Brownian Motion with Ito drift correction
            r = rng.normal(mu_daily - 0.5 * sim_sigma_daily**2, sim_sigma_daily, size=(N, H))
            paths = S0 * np.exp(np.cumsum(r, axis=1))

            end_prices = paths[:, -1]
            pmin = paths.min(axis=1)
            pmax = paths.max(axis=1)

            # First-passage hitting time logic (Target 1 vs Stop Loss)
            hit_target = paths >= pine_target1
            hit_stop = paths <= pine_stop

            has_target = hit_target.any(axis=1)
            has_stop = hit_stop.any(axis=1)

            first_target_idx = np.where(has_target, np.argmax(hit_target, axis=1), H + 1)
            first_stop_idx = np.where(has_stop, np.argmax(hit_stop, axis=1), H + 1)

            p_target_first = float(np.mean((first_target_idx < first_stop_idx) & has_target))
            p_stop_first = float(np.mean((first_stop_idx < first_target_idx) & has_stop))
            p_neither = float(np.mean(~has_target & ~has_stop))

            # Barrier touch probabilities
            touch_probs = {}
            for b_name, b_level in barriers.items():
                if b_level >= S0:
                    touch_probs[b_name] = round(float(np.mean(pmax >= b_level)) * 100, 1)
                else:
                    touch_probs[b_name] = round(float(np.mean(pmin <= b_level)) * 100, 1)

            mc_results[H] = {
                "median": round(float(np.median(end_prices)), 2),
                "p05": round(float(np.percentile(end_prices, 5)), 2),
                "p25": round(float(np.percentile(end_prices, 25)), 2),
                "p75": round(float(np.percentile(end_prices, 75)), 2),
                "p95": round(float(np.percentile(end_prices, 95)), 2),
                "p_target_first_pct": round(p_target_first * 100, 1),
                "p_stop_first_pct": round(p_stop_first * 100, 1),
                "p_neither_pct": round(p_neither * 100, 1),
                "p_reach_target_pct": round(float(has_target.mean()) * 100, 1),
                "p_reach_stop_pct": round(float(has_stop.mean()) * 100, 1),
                "barrier_touches": touch_probs,
            }

        # -------------------------------------------------------------
        # 4. PREPARE PRE-FORMATTED MARKDOWN FOR ZERO-LATENCY INJECTION
        # -------------------------------------------------------------
        md_lines = [
            f"### 🎯 PRE-COMPUTED DETERMINISTIC QUANTITATIVE BASELINE (CPU) FOR {safe_ticker}",
            f"- **Spot Reference Price**: ${S0:.2f}",
            "",
            "**1. Core Structural Levels & Moving Average Extensions:**",
            f"- 52-Week Range: High ${high_52w:.2f} ({dist_52w_high_pct:+.1f}%) | Low ${low_52w:.2f} ({dist_52w_low_pct:+.1f}%)",
            f"- Moving Averages: SMA20: ${sma20:.2f} ({dist_sma20_pct:+.1f}%) | SMA50: ${sma50:.2f} ({dist_sma50_pct:+.1f}%) | SMA200: ${sma200:.2f} ({dist_sma200_pct:+.1f}%)",
            f"- Fast Trend Anchors: EMA8: ${ema8:.2f} | EMA13: ${ema13:.2f} | EMA21: ${ema21:.2f} | ATR14: ${atr14:.2f} ({atr14_pct:.1f}% of price)",
        ]
        if unfilled_gaps:
            md_lines.append(f"- Recent Unfilled Gaps (30d): {', '.join(unfilled_gaps[:3])}")

        md_lines.extend([
            "",
            "**2. Volatility Structure & Risk Premium (VRP):**",
            f"- Realized Volatilities: HV10: {hv10:.1f}% | HV20: {hv20:.1f}% | HV30: {hv30:.1f}% | HV60: {hv60:.1f}% | HV90: {hv90:.1f}%",
        ])
        if iv30 is not None:
            md_lines.extend([
                f"- Implied Volatility (IV30): {iv30:.1f}% | IV-HV Spread: {iv_hv_diff:+.1f}pp",
                f"- Volatility Regime: **{vrp_regime}**",
            ])
        else:
            md_lines.append(f"- Volatility Regime: **{vrp_regime}**")

        md_lines.extend([
            "",
            f"**3. Monte Carlo Path & Touch Probabilities (20,000 Paths, Target 1: ${pine_target1:.2f}, Stop: ${pine_stop:.2f}):**",
            "| Horizon | Median Price | 5th – 95th Price Cone | P(Target First) | P(Stop First) | P(Neither) | Key Touch Probabilities |",
            "|---|---|---|---|---|---|---|",
        ])

        for H in horizons:
            r = mc_results[H]
            touch_str = ", ".join([f"{k}: {v}%" for k, v in r["barrier_touches"].items()]) or "None"
            md_lines.append(
                f"| **{H} Days** | ${r['median']:.2f} | [${r['p05']:.2f} – ${r['p95']:.2f}] | "
                f"**{r['p_target_first_pct']}%** | **{r['p_stop_first_pct']}%** | {r['p_neither_pct']}% | {touch_str} |"
            )

        return {
            "spot_price": S0,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "dist_52w_high_pct": round(dist_52w_high_pct, 2),
            "dist_52w_low_pct": round(dist_52w_low_pct, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "ema8": round(ema8, 2),
            "ema13": round(ema13, 2),
            "ema21": round(ema21, 2),
            "atr14": round(atr14, 2),
            "atr14_pct": round(atr14_pct, 2),
            "unfilled_gaps": unfilled_gaps,
            "hv10": round(hv10, 2),
            "hv20": round(hv20, 2),
            "hv30": round(hv30, 2),
            "hv60": round(hv60, 2),
            "hv90": round(hv90, 2),
            "iv30": round(iv30, 2) if iv30 else None,
            "iv_hv_diff": iv_hv_diff,
            "vrp_regime": vrp_regime,
            "target_1": pine_target1,
            "stop_loss": pine_stop,
            "monte_carlo_horizons": mc_results,
            "_monte_carlo_markdown": "\n".join(md_lines),
        }
