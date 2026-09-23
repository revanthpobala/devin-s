"""
src/plugins/schwab_plugin.py

Schwab Quantitative Analytics Plugin.
Enriches the research dossier with user's active brokerage holdings (cost basis,
shares, unrealized P/L, asset type) and 90-day institutional unusual options sweeps
(Volume > 1.5x Open Interest, call/put premium ratios).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
import pandas as pd

from src.plugins.base_plugin import BaseAnalyticsPlugin

logger = logging.getLogger(__name__)


class SchwabPlugin(BaseAnalyticsPlugin):
    """Fetches real-time Schwab brokerage holdings and institutional options flow."""

    @property
    def name(self) -> str:
        return "schwab_analytics"

    @property
    def description(self) -> str:
        return (
            "Provides user's active Schwab brokerage holdings (shares, cost basis, unrealized P/L) "
            "and institutional unusual options sweep flow (volume > 1.5x OI, call/put premium splits)."
        )

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        safe_ticker = (ticker or "").strip().upper()
        if not safe_ticker or safe_ticker in ("SPX", "VIX", "NDX", "RUT", "DJI"):
            return {}

        results: Dict[str, Any] = {}

        # 1. Active Portfolio Position check
        try:
            from src.clients.schwab_client import get_active_position_for_ticker

            pos = get_active_position_for_ticker(safe_ticker)
            if pos and pos.get("has_position"):
                results.update({
                    "schwab_has_position": True,
                    "schwab_position_source": pos.get("source", "SCHWAB"),
                    "schwab_position_type": pos.get("asset_type", "EQUITY"),
                    "schwab_quantity": pos.get("quantity", 0),
                    "schwab_cost_basis": pos.get("average_price", 0.0),
                    "schwab_current_price": pos.get("last_price") or pos.get("current_price", 0.0),
                    "schwab_market_value": pos.get("market_value", 0.0),
                    "schwab_day_pnl": pos.get("day_pnl", 0.0),
                    "schwab_day_pnl_pct": pos.get("day_pnl_pct", 0.0),
                    "schwab_account": pos.get("account", "N/A"),
                    "schwab_side": pos.get("side", "LONG"),
                })
            else:
                results["schwab_has_position"] = False
        except Exception as e:
            logger.debug(f"SchwabPlugin position check failed for {safe_ticker}: {e}")
            results["schwab_has_position"] = False

        # 2. Institutional Unusual Options Flow check
        try:
            from src.clients.schwab_client import get_unusual_options_flow_data

            flow = get_unusual_options_flow_data(safe_ticker)
            if flow and flow.get("status") == "ok":
                anomalies: List[Dict[str, Any]] = flow.get("anomalies", [])
                top_summaries = [
                    f"{a['type']} ${a['strike']} exp {a['expiry']} (vol {a['volume']} vs OI {a['open_interest']}, prem ${a.get('notional_premium', 0):,})"
                    for a in anomalies[:3]
                ]

                results.update({
                    "schwab_flow_sentiment": flow.get("sentiment", "NEUTRAL"),
                    "schwab_flow_sentiment_label": flow.get("sentiment_label", ""),
                    "schwab_call_sweeps_count": flow.get("call_sweeps_count", 0),
                    "schwab_put_sweeps_count": flow.get("put_sweeps_count", 0),
                    "schwab_total_call_premium": flow.get("total_call_premium", 0),
                    "schwab_total_put_premium": flow.get("total_put_premium", 0),
                    "schwab_put_call_volume_ratio": flow.get("put_call_volume_ratio", 0.0),
                    "schwab_put_call_premium_ratio": flow.get("put_call_premium_ratio", 0.0),
                    "schwab_total_sweeps_count": flow.get("total_anomalies_count", 0),
                    "schwab_top_sweeps": top_summaries,
                })
            else:
                results["schwab_flow_sentiment"] = "NO_DATA"
        except Exception as e:
            logger.debug(f"SchwabPlugin options flow check failed for {safe_ticker}: {e}")
            results["schwab_flow_sentiment"] = "UNAVAILABLE"

        return results


def format_active_position_block(ticker: str) -> str:
    """Format active broker position into a prominent prompt block for Deep Research & Senior PM."""
    try:
        from src.clients.schwab_client import get_active_position_for_ticker

        pos = get_active_position_for_ticker(ticker)
        if not pos or not pos.get("has_position"):
            return "- USER ACTIVE BROKER POSITION: NONE (No current holdings in Schwab or tracked portfolio; evaluate as fresh prospective trade)."

        source = pos.get("source", "SCHWAB")
        qty = pos.get("quantity", 0)
        side = pos.get("side", "LONG")
        avg_px = pos.get("average_price", 0.0)
        curr_px = pos.get("last_price") or pos.get("current_price", 0.0)
        mv = pos.get("market_value", 0.0)
        pnl = pos.get("day_pnl", 0.0)
        pnl_pct = pos.get("day_pnl_pct", 0.0)
        acct = pos.get("account", "N/A")
        asset_type = pos.get("asset_type", "EQUITY")

        return (
            f"--- USER ACTIVE BROKER POSITION ({source}) ---\n"
            f"• Status: ACTIVE HOLDING ({side} {qty} units / {asset_type})\n"
            f"• Cost Basis: ${avg_px:,.2f} | Current Price: ${curr_px:,.2f} | Market Value: ${mv:,.2f}\n"
            f"• Unrealized / Day P/L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"• Account: {acct}\n"
            f"• MANDATORY SENIOR PM DIRECTIVE: The user already OWNS this position. Do NOT just output a generic limit entry. "
            f"Provide an explicit 'Active Holding Playbook' covering:\n"
            f"  1. Profit scale-out at Target 1 / Target 2\n"
            f"  2. Stop advance to protect capital / trail to breakeven\n"
            f"  3. Covered call yield opportunities (if 100+ shares held)"
        )
    except Exception as e:
        logger.debug(f"format_active_position_block failed for {ticker}: {e}")
        return "- USER ACTIVE BROKER POSITION: UNKNOWN (Error checking holdings; evaluate as prospective trade)."
