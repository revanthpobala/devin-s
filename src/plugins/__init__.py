"""
src/plugins

Decoupled quantitative analytics plugin architecture.
"""

from src.plugins.base_plugin import BaseAnalyticsPlugin
from src.plugins.plugin_manager import plugin_manager, enrich_datawindow_with_plugins

__all__ = ["BaseAnalyticsPlugin", "plugin_manager", "enrich_datawindow_with_plugins"]
