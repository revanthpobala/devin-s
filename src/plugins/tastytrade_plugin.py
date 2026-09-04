"""
Tastytrade Quantitative Volatility & Options Analytics Plugin.
Enriches the research dossier with institutional IV Rank, HV/IV spread,
options liquidity rating, short borrow rate, and beta correlation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict
import pandas as pd

from src.clients.tastytrade_client import TastytradeClient
from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)


class TastytradeVolatilityPlugin(BaseAnalyticsPlugin):
    """Fetches real-time institutional volatility metrics, options liquidity, and lendability from Tastytrade."""

    def __init__(self):
        self._client: TastytradeClient | None = None

    @property
    def client(self) -> TastytradeClient:
        if self._client is None:
            self._client = TastytradeClient()
        return self._client

    @property
    def name(self) -> str:
        return "tastytrade_volatility"

    @property
    def description(self) -> str:
        return (
            "Provides institutional IV Rank, IV Percentile, 30d/60d/90d Historical Volatility (HV), "
            "IV-HV Volatility Spread, Options Liquidity 1-5 Star Rating, Short Borrow Rate, and Beta."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        safe_ticker = ticker.strip().upper()
        if safe_ticker in ("SPX", "VIX", "NDX", "RUT", "DJI"):
            return {}

        try:
            metrics_list = self.client.get_market_metrics(safe_ticker)
            if not metrics_list:
                return {}

            m = metrics_list[0]
            iv_rank_raw = m.get("tos-implied-volatility-index-rank") or m.get("implied-volatility-index-rank")
            iv_rank = round(float(iv_rank_raw) * 100, 2) if iv_rank_raw is not None else None

            iv_pct_raw = m.get("implied-volatility-percentile")
            iv_pct = round(float(iv_pct_raw) * 100, 2) if iv_pct_raw is not None else None

            iv_30 = float(m.get("implied-volatility-30-day") or 0.0)
            hv_30 = float(m.get("historical-volatility-30-day") or 0.0)
            hv_60 = float(m.get("historical-volatility-60-day") or 0.0)
            hv_90 = float(m.get("historical-volatility-90-day") or 0.0)
            iv_hv_diff = float(m.get("iv-hv-30-day-difference") or 0.0)

            liq_rating = m.get("liquidity-rating", 3)
            lendability = m.get("lendability", "Easy To Borrow")
            borrow_rate = float(m.get("borrow-rate") or 0.0)
            beta = float(m.get("beta") or 1.0)
            spy_corr = float(m.get("corr-spy-3month") or 0.0)

            # Regime assessment
            if iv_rank is not None:
                if iv_rank < 30:
                    vol_regime = "CHEAP_VOLATILITY (Favor Directional Debit Spreads / LEAPS)"
                elif iv_rank > 65:
                    vol_regime = "EXPENSIVE_VOLATILITY (Favor Defined-Risk Credit Spreads / Iron Condors)"
                else:
                    vol_regime = "NEUTRAL_VOLATILITY (Balanced Debit / Credit Regime)"
            else:
                vol_regime = "NORMAL"

            return {
                "tastytrade_iv_rank": iv_rank,
                "tastytrade_iv_percentile": iv_pct,
                "tastytrade_iv_30d": iv_30,
                "tastytrade_hv_30d": hv_30,
                "tastytrade_hv_60d": hv_60,
                "tastytrade_hv_90d": hv_90,
                "tastytrade_iv_hv_spread": iv_hv_diff,
                "tastytrade_volatility_regime": vol_regime,
                "tastytrade_options_liquidity_stars": f"{liq_rating}/5 Stars",
                "tastytrade_lendability": lendability,
                "tastytrade_borrow_rate_pct": borrow_rate,
                "tastytrade_beta": beta,
                "tastytrade_spy_3m_correlation": spy_corr,
            }
        except Exception as e:
            logger.debug(f"Tastytrade volatility plugin failed for {safe_ticker}: {e}")
            return {}
