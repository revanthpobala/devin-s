"""
scripts/dev/inspect_dates_popup_html.py
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


def dump_dates_popup():
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
            pill.click()
            time.sleep(1.5)

            # Dump body children added after click
            popups = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('div[data-name="popup-menu-container"], div[class*="menu-"], div[class*="popup-"]').forEach(el => {
                    if (el.innerText.trim()) {
                        results.push({
                            html: el.outerHTML,
                            text: el.innerText
                        });
                    }
                });
                return results;
            }''')

            for p_info in popups:
                logger.info(f"POPUP FOUND:\n{p_info['text']}\nHTML:\n{p_info['html'][:500]}")

        context.close()


if __name__ == "__main__":
    dump_dates_popup()
