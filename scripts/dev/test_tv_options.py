"""
scripts/dev/test_tv_options.py

Test targeted Strategy Finder scraper with Prediction Period & Expected Move filters.
"""

import logging
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.data.tv_options_scraper import TVOptionsScraper

CHROME_PROFILE_DIR = BASE_DIR / "tv_chrome_profile_1"
OUT_DIR = BASE_DIR / "data" / "raw" / "test_options"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TICKER = "AAPL"
CHART_URL = f"https://www.tradingview.com/chart/jPAQSlZC/?symbol={TICKER}"


def run_targeted_test():
    logger.info(f"--- TESTING TARGETED STRATEGY FINDER FOR {TICKER} ---")

    if CHROME_PROFILE_DIR.exists():
        for item in CHROME_PROFILE_DIR.rglob("*"):
            if item.is_file() and item.name.upper() in ("SINGLETONLOCK", "SINGLETONCOOKIE", "SINGLETONSOCKET", "LOCKFILE", "LOCK"):
                try:
                    item.unlink()
                except Exception:
                    pass

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        logger.info(f"Navigating to {CHART_URL}...")
        page.goto(CHART_URL, wait_until="domcontentloaded")

        try:
            page.wait_for_selector("canvas", timeout=25000)
            time.sleep(4.0)
        except Exception:
            pass

        scraper = TVOptionsScraper(load_wait_s=8.0)
        result = scraper.scrape_options_suite(
            page,
            TICKER,
            OUT_DIR,
            prediction_period="Next month",
            expected_move="+5% to +10%",
        )

        logger.info(f"Scraped {len(result.get('strategies', []))} targeted options strategies.")
        context.close()


if __name__ == "__main__":
    run_targeted_test()
