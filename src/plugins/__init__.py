"""
src/plugins

Decoupled quantitative analytics plugin architecture.
"""

from src.plugins.base_plugin import BaseAnalyticsPlugin
from src.plugins.plugin_manager import PluginManager, enrich_datawindow_with_plugins

__all__ = ["BaseAnalyticsPlugin", "PluginManager", "enrich_datawindow_with_plugins"]
