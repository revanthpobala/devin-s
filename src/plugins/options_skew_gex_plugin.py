"""
src/plugins/options_skew_gex_plugin.py

Quantitative Options Skew & Dealer Gamma Exposure (GEX) Analytics Plugin.
Calculates 25-Delta Put/Call Volatility Skew, Institutional Call Wall,
Put Wall, Zero-Gamma Flip Line, and Net Dealer Positioning Regime.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import pandas as pd
import requests

from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)

ALPACA_OPTIONS_DATA_HOST = os.getenv("ALPACA_OPTIONS_DATA_HOST", "https://data.alpaca.markets")


def _alpaca_creds():
    key = (
        os.getenv("ALPACA_OPTIONS_KEY")
        or os.getenv("ALPACA_API_KEY")
        or os.getenv("ALPACA_KEY_ID")
    )
    secret = os.getenv("ALPACA_OPTIONS_SECRET") or os.getenv("ALPACA_SECRET_KEY")
    return key, secret


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int:
    try:
        if v is None:
            return 0
        f = float(v)
        return 0 if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return 0


def _parse_occ(symbol: str):
    """Parse OCC option symbol into (root, exp_date, otype, strike)."""
    try:
        strike = int(symbol[-8:]) / 1000.0
        otype = "CALL" if symbol[-9] == "C" else "PUT"
        exp_str = symbol[-15:-9]
        exp_date = datetime.strptime(exp_str, "%y%m%d").date()
        root = symbol[:-15]
        return root, exp_date, otype, strike
    except Exception:
        return None, None, None, None


class OptionsSkewGEXPlugin(BaseAnalyticsPlugin):
    """Computes 25-Delta Put/Call Volatility Skew and Dealer Gamma Exposure (GEX) walls."""

    @property
    def name(self) -> str:
        return "options_skew_gex"

    @property
    def description(self) -> str:
        return (
            "Computes 25-Delta Put/Call Volatility Skew, Call Wall (overhead ceiling), "
            "Put Wall (institutional floor), Zero-Gamma Flip Point, and Net Dealer Positioning Regime."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        safe_ticker = ticker.strip().upper()
        if safe_ticker in ("SPX", "VIX", "NDX", "RUT", "DJI"):
            return {}

        spot_price = _safe_float(dw.get("price"))
        if not spot_price and not df.empty and "close" in df.columns:
            spot_price = _safe_float(df["close"].iloc[-1])

        if not spot_price or spot_price <= 0:
            return {}

        # 1. Attempt primary Alpaca options snapshot pull
        res = self._calc_alpaca_skew_gex(safe_ticker, spot_price)
        if res:
            return res

        # 2. Attempt yfinance options chain fallback
        res_yf = self._calc_yfinance_skew_gex(safe_ticker, spot_price)
        if res_yf:
            return res_yf

        # 3. Fallback heuristic from existing Data Window / Tastytrade volatility metrics
        return self._calc_heuristic_skew(safe_ticker, spot_price, dw)

    def _calc_alpaca_skew_gex(self, ticker: str, spot: float) -> Optional[Dict[str, Any]]:
        key, secret = _alpaca_creds()
        if not key or not secret:
            return None

        today = date.today()
        # Look 20 to 60 days out (standard 30-45 DTE institutional trading horizon)
        exp_min = (today + timedelta(days=20)).isoformat()
        exp_max = (today + timedelta(days=60)).isoformat()
        strike_min = round(spot * 0.75, 2)
        strike_max = round(spot * 1.25, 2)

        try:
            params = {
                "limit": 1000,
                "expiration_date_gte": exp_min,
                "expiration_date_lte": exp_max,
                "strike_price_gte": strike_min,
                "strike_price_lte": strike_max,
            }
            url = f"{ALPACA_OPTIONS_DATA_HOST}/v1beta1/options/snapshots/{ticker}?{urlencode(params)}"
            r = requests.get(
                url,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                timeout=12,
            )
            if r.status_code != 200:
                return None

            snapshots = r.json().get("snapshots", {})
            if not snapshots:
                return None

            return self._process_chain_snapshots(snapshots, spot)
        except Exception as e:
            logger.debug(f"Alpaca Skew/GEX calculation failed for {ticker}: {e}")
            return None

    def _calc_yfinance_skew_gex(self, ticker: str, spot: float) -> Optional[Dict[str, Any]]:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            expirations = t.options
            if not expirations:
                return None

            today = date.today()
            # Select target expiration closest to 30-45 DTE
            target_exp = None
            min_diff = 9999
            for exp in expirations:
                try:
                    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                    dte = (exp_date - today).days
                    if 20 <= dte <= 60:
                        diff = abs(dte - 35)
                        if diff < min_diff:
                            min_diff = diff
                            target_exp = exp
                except Exception:
                    continue

            if not target_exp:
                target_exp = expirations[0]

            chain = t.option_chain(target_exp)
            calls = chain.calls
            puts = chain.puts
            if calls.empty or puts.empty:
                return None

            # Calculate Call Wall and Put Wall by Open Interest
            calls_sorted = calls.sort_values(by="openInterest", ascending=False)
            puts_sorted = puts.sort_values(by="openInterest", ascending=False)
            call_wall = float(calls_sorted["strike"].iloc[0]) if not calls_sorted.empty else spot * 1.1
            put_wall = float(puts_sorted["strike"].iloc[0]) if not puts_sorted.empty else spot * 0.9

            # 25-Delta Skew approximation (IV at ~90% spot for Put vs ~110% spot for Call)
            put_25 = puts.iloc[(puts["strike"] - (spot * 0.92)).abs().argsort()[:1]]
            call_25 = calls.iloc[(calls["strike"] - (spot * 1.08)).abs().argsort()[:1]]

            put_iv = float(put_25["impliedVolatility"].iloc[0] * 100) if not put_25.empty else 0.0
            call_iv = float(call_25["impliedVolatility"].iloc[0] * 100) if not call_25.empty else 0.0
            skew_25d = round(put_iv - call_iv, 2)

            regime = "LONG_GAMMA (Mean-Reverting Resistance/Support)" if spot >= put_wall else "SHORT_GAMMA (Breakdown Acceleration Risk)"

            return {
                "options_skew_25d": skew_25d,
                "options_skew_regime": self._classify_skew(skew_25d),
                "options_call_wall": round(call_wall, 2),
                "options_put_wall": round(put_wall, 2),
                "options_zero_gamma_flip": round((call_wall + put_wall) / 2, 2),
                "options_net_gex_regime": regime,
                "options_gamma_pin_zone": f"${put_wall:.2f} - ${call_wall:.2f}",
                "options_source": "yfinance",
            }
        except Exception as e:
            logger.debug(f"yfinance Skew/GEX calculation failed for {ticker}: {e}")
            return None

    def _process_chain_snapshots(self, snapshots: dict, spot: float) -> Dict[str, Any]:
        calls_by_strike = {}
        puts_by_strike = {}
        target_call_iv = None
        target_put_iv = None
        min_call_delta_dist = 999.0
        min_put_delta_dist = 999.0

        for symbol, snap in snapshots.items():
            root, exp_date, otype, strike = _parse_occ(symbol)
            if not strike or not otype:
                continue

            vol = _safe_int((snap.get("dailyBar") or {}).get("v"))
            g = snap.get("greeks") or {}
            delta = _safe_float(g.get("delta"))
            gamma = _safe_float(g.get("gamma")) or 0.0
            iv = _safe_float(snap.get("impliedVolatility")) or 0.0
            if iv and iv < 2.0:
                iv = iv * 100.0

            # Estimate Gamma Exposure (GEX in $ millions per 1% move): OI * Gamma * Spot^2 * 0.01
            # When OI isn't returned inline, we use active daily volume as a liquidity proxy
            liquidity_weight = max(vol, 10)
            gex_val = liquidity_weight * gamma * (spot ** 2) * 0.0001

            if otype == "CALL":
                calls_by_strike[strike] = calls_by_strike.get(strike, 0.0) + gex_val
                if delta is not None and iv > 0:
                    dist = abs(delta - 0.25)
                    if dist < min_call_delta_dist:
                        min_call_delta_dist = dist
                        target_call_iv = iv
            else:
                puts_by_strike[strike] = puts_by_strike.get(strike, 0.0) + gex_val
                if delta is not None and iv > 0:
                    dist = abs(abs(delta) - 0.25)
                    if dist < min_put_delta_dist:
                        min_put_delta_dist = dist
                        target_put_iv = iv

        # Identify Call Wall and Put Wall
        call_wall = max(calls_by_strike.items(), key=lambda x: x[1])[0] if calls_by_strike else spot * 1.10
        put_wall = max(puts_by_strike.items(), key=lambda x: x[1])[0] if puts_by_strike else spot * 0.90

        # Calculate 25-Delta Skew
        if target_put_iv and target_call_iv:
            skew_25d = round(target_put_iv - target_call_iv, 2)
        else:
            skew_25d = 0.0

        # Zero-Gamma approximation
        zero_gamma = round((call_wall * 0.4 + put_wall * 0.6), 2)
        net_gex_regime = (
            "LONG_GAMMA (Vol-Dampening Floor Defense)"
            if spot >= zero_gamma
            else "SHORT_GAMMA (Vol-Accelerating Cascade Risk)"
        )

        return {
            "options_skew_25d": skew_25d,
            "options_skew_regime": self._classify_skew(skew_25d),
            "options_call_wall": round(call_wall, 2),
            "options_put_wall": round(put_wall, 2),
            "options_zero_gamma_flip": zero_gamma,
            "options_net_gex_regime": net_gex_regime,
            "options_gamma_pin_zone": f"${put_wall:.2f} - ${call_wall:.2f}",
            "options_source": "alpaca_live",
        }

    def _calc_heuristic_skew(self, ticker: str, spot: float, dw: Dict[str, Any]) -> Dict[str, Any]:
        """Heuristic fallback leveraging Tastytrade institutional market metrics."""
        iv_rank = _safe_float(dw.get("tastytrade_iv_rank"))
        iv_hv = _safe_float(dw.get("tastytrade_iv_hv_spread"))

        # If not present in dw, query Tastytrade directly
        if iv_rank is None or iv_hv is None:
            try:
                from src.clients.tastytrade_client import TastytradeClient
                tt_client = TastytradeClient()
                m_list = tt_client.get_market_metrics(ticker)
                if m_list:
                    m = m_list[0]
                    iv_rank_raw = m.get("tos-implied-volatility-index-rank") or m.get("implied-volatility-index-rank")
                    if iv_rank_raw is not None:
                        iv_rank = float(iv_rank_raw) * 100.0
                    iv_hv_raw = m.get("iv-hv-30-day-difference")
                    if iv_hv_raw is not None:
                        iv_hv = float(iv_hv_raw)
            except Exception as e:
                logger.debug(f"Direct Tastytrade fallback fetch failed for {ticker}: {e}")

        iv_rank = iv_rank if iv_rank is not None else 50.0
        iv_hv = iv_hv if iv_hv is not None else 0.0

        atr = _safe_float(dw.get("atr")) or (spot * 0.03)
        call_wall = round(spot + (atr * 3.5), 2)
        put_wall = round(spot - (atr * 3.0), 2)
        zero_gamma = round(spot - atr, 2)

        skew_est = round((iv_hv * 0.5) if iv_hv > 0 else 1.5, 2)
        regime = "NEUTRAL_GAMMA" if iv_rank < 60 else "ELEVATED_SKEW_DEFENSE"

        return {
            "options_skew_25d": skew_est,
            "options_skew_regime": self._classify_skew(skew_est),
            "options_call_wall": call_wall,
            "options_put_wall": put_wall,
            "options_zero_gamma_flip": zero_gamma,
            "options_net_gex_regime": regime,
            "options_gamma_pin_zone": f"${put_wall:.2f} - ${call_wall:.2f}",
            "options_source": "volatility_heuristic",
        }

    def _classify_skew(self, skew: float) -> str:
        if skew > 6.0:
            return "STEEP_PUT_SKEW (High Institutional Downside Fear)"
        elif skew > 2.0:
            return "NORMAL_PUT_SKEW (Standard Hedging Baseline)"
        elif skew < -2.0:
            return "CALL_SKEW (Aggressive Bullish Speculation)"
        else:
            return "FLAT_SKEW (Low Demand for Downside Puts / Complacent)"
