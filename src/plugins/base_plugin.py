"""
src/plugins/base_plugin.py

Abstract Base Class for all Quantitative & Market Analytics Plugins.
Each plugin takes the 1-year OHLCV DataFrame (df) and Data Window snapshot (dw)
and returns an isolated, structured dictionary of calculated features.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict
import pandas as pd


class BaseAnalyticsPlugin(ABC):
    """Abstract interface for all decoupled quantitative market analytics plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier (e.g. 'earnings_history', 'squeeze_expansion')."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human and LLM-readable description of what this plugin computes."""
        pass

    @abstractmethod
    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        """Executes the quantitative calculation.
        
        Args:
            ticker: Stock ticker symbol (e.g. 'AMD')
            df: Trailing 1-year OHLCV + indicators DataFrame (250-300 bars)
            dw: Current bar Data Window snapshot dictionary

        Returns:
            Dict containing calculated metrics to enrich the research dossier.
        """
        pass
