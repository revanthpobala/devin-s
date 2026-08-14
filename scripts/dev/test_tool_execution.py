"""
scripts/dev/test_tool_execution.py

Test tool invocation of scrape_tradingview_options_finder.
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.data.tv_options_scraper import scrape_tv_options_finder_tool


def test_tool():
    logger.info("Testing scrape_tv_options_finder_tool for HOOD...")
    result = scrape_tv_options_finder_tool(
        ticker="HOOD",
        prediction_period="Next month",
        expected_move="+5% to +10%",
    )
    print("\n--- TOOL OUTPUT ---")
    print(result)


if __name__ == "__main__":
    test_tool()
