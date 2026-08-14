"""
scripts/dev/inspect_tv_dates_menu.py
"""

import logging
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CHROME_PROFILE_DIR = BASE_DIR / "tv_chrome_profile_1"
TICKER = "AAPL"
CHART_URL = f"https://www.tradingview.com/chart/jPAQSlZC/?symbol={TICKER}"


def inspect_dates_menu():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto(CHART_URL, wait_until="domcontentloaded")
        page.wait_for_selector("canvas", timeout=25000)
        time.sleep(3.0)

        # Open options
        opt_btn = page.locator('button[data-name="options-builder-dialog-button"]').first
        if opt_btn.is_visible():
            opt_btn.click()
            time.sleep(8.0)

        # Strategy finder
        strat_tab = page.locator('#spreadex').first
        if strat_tab.is_visible():
            strat_tab.click()
            time.sleep(3.0)

        # Click Dates Range pill
        pill = page.locator('button[data-qa-id*="dates-range-pill"]').first
        if pill.is_visible():
            logger.info("Clicking Dates Range pill...")
            pill.click()
            time.sleep(2.0)

            # Look for all elements in the popup
            popups = page.locator('div[data-name="popup-menu-container"], div[class*="menuWrap"], div[class*="dropdown"]').all()
            for p_el in popups:
                logger.info(f"Popup text:\n{p_el.inner_text()}")

            items = page.locator('div[data-role="menuitem"], tr, [role="menuitem"]').all()
            for i, it in enumerate(items):
                try:
                    logger.info(f"MenuItem {i}: {it.inner_text().strip()}")
                except Exception:
                    pass

        time.sleep(4.0)
        context.close()


if __name__ == "__main__":
    inspect_dates_menu()
