import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.clients.llm_client import run_quantitative_plugin_tool
from src.plugins.plugin_manager import plugin_manager
from src.plugins.candlestick_patterns_plugin import CandlestickPatternsPlugin

class TestCandlestickPatternsPlugin(unittest.TestCase):
    def test_plugin_registration(self):
        plugin = plugin_manager.get_plugin("candlestick_patterns")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.name, "candlestick_patterns")

    def test_amzn_candlestick_detection(self):
        res = run_quantitative_plugin_tool("AMZN", plugin_name="candlestick_patterns", date_str="2026-08-18")
        self.assertIn("Quantitative Plugin Results for AMZN", res)
        self.assertIn("active_candlestick_patterns", res)
        print("\n--- AMZN Candlestick Patterns Tool Output ---")
        print(res.encode("ascii", errors="replace").decode("ascii"))

if __name__ == "__main__":
    unittest.main()
