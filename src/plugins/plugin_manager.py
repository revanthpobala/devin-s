"""
src/plugins/plugin_manager.py

Plugin Registry & Execution Manager for decoupled quantitative analytics plugins.
"""

import logging
from typing import Any, Dict, List
import pandas as pd

from src.plugins.base_plugin import BaseAnalyticsPlugin
from src.plugins.earnings_history_plugin import EarningsHistoryPlugin
from src.plugins.squeeze_expansion_plugin import SqueezeExpansionPlugin
from src.plugins.htf_confluence_plugin import HTFConfluencePlugin
from src.plugins.order_flow_plugin import OrderFlowPlugin
from src.plugins.candlestick_patterns_plugin import CandlestickPatternsPlugin
from src.plugins.tastytrade_plugin import TastytradeVolatilityPlugin
from src.plugins.options_skew_gex_plugin import OptionsSkewGEXPlugin
from src.plugins.conformal_prediction_plugin import ConformalPredictionPlugin
from src.plugins.monte_carlo_plugin import MonteCarloPlugin

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self):
        self._plugins: Dict[str, BaseAnalyticsPlugin] = {}
        # Register standard default plugins
        self.register_plugin(EarningsHistoryPlugin())
        self.register_plugin(SqueezeExpansionPlugin())
        self.register_plugin(HTFConfluencePlugin())
        self.register_plugin(OrderFlowPlugin())
        self.register_plugin(CandlestickPatternsPlugin())
        self.register_plugin(TastytradeVolatilityPlugin())
        self.register_plugin(OptionsSkewGEXPlugin())
        self.register_plugin(ConformalPredictionPlugin())
        self.register_plugin(MonteCarloPlugin())

    def register_plugin(self, plugin: BaseAnalyticsPlugin):
        self._plugins[plugin.name] = plugin
        logger.debug(f"Registered analytics plugin: {plugin.name}")

    def get_plugin(self, name: str) -> BaseAnalyticsPlugin:
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, str]]:
        return [{"name": p.name, "description": p.description} for p in self._plugins.values()]

    def run_all(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        """Runs all registered plugins and combines their enriched outputs."""
        combined: Dict[str, Any] = {}
        for name, plugin in self._plugins.items():
            try:
                res = plugin.run(ticker, df, dw)
                if isinstance(res, dict):
                    combined.update(res)
            except Exception as e:
                logger.warning(f"Plugin '{name}' execution failed for {ticker}: {e}")
        return combined


# Global singleton instance
plugin_manager = PluginManager()


def enrich_datawindow_with_plugins(ticker: str, df: pd.DataFrame, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to run all registered analytics plugins and enrich the snapshot."""
    plugin_results = plugin_manager.run_all(ticker, df, snapshot)
    snapshot.update(plugin_results)
    return snapshot
