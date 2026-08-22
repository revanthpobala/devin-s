import time
from pathlib import Path
from playwright.sync_api import sync_playwright
import sys

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.data.tv_options_scraper import TVOptionsScraper

def inspect_tv_options():
    p_dir = BASE_DIR / "tv_chrome_profile_1"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(p_dir),
            headless=True,
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        page.goto("https://www.tradingview.com/chart/jPAQSlZC/?symbol=AMD", wait_until="domcontentloaded")
        page.wait_for_selector("canvas", timeout=25000)
        time.sleep(3.0)

        s = TVOptionsScraper()
        s.open_options_modal(page)
        time.sleep(2.0)

        # Find all tab buttons
        tabs = page.locator('button[role="tab"]').all()
        tab_info = []
        for t in tabs:
            tab_info.append({
                "id": t.get_attribute("id"),
                "text": t.inner_text().strip(),
                "aria_selected": t.get_attribute("aria-selected"),
            })
        print("Modal Tabs:", tab_info)

        # Find the overflow dropdown button next to Strategy builder
        print("Looking for tab overflow button...")
        overflow_info = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            // Find buttons with overflow, dots, more, or chevron in className or aria
            return btns
                .filter(b => b.className.includes('overflow') || b.className.includes('more') || b.getAttribute('data-role') === 'overflow-button')
                .map(b => ({
                    id: b.id,
                    className: b.className,
                    rect: b.getBoundingClientRect()
                }));
        }""")
        import json
        print("Overflow buttons:", json.dumps(overflow_info, indent=2))

        # Click overflow button if present
        overflow_btn = page.locator('button[class*="overflow"], button[data-role="overflow-button"]').first
        if overflow_btn.is_visible():
            print("Clicking overflow button...")
            overflow_btn.click()
            time.sleep(1.5)
            menu_items = page.locator('[role="menuitem"], div[class*="item-"]').all_inner_texts()
            print("Menu items opened:", menu_items)
            
            # Click Strategy finder in menu
            strat_item = page.locator('[role="menuitem"]:has-text("Strategy finder"), div[class*="item-"]:has-text("Strategy finder")').first
            if strat_item.is_visible():
                print("Clicking Strategy finder in dropdown...")
                strat_item.click()
                time.sleep(3.0)
                ths = page.locator('table thead th, th').all_inner_texts()
                print("\nActive Table Headers after clicking menu item:", ths)

        ctx.close()
        return

        # Check active headers and rows
        ths = page.locator('table thead th, th').all_inner_texts()
        print("\nActive Table Headers:", ths)

        rows = page.locator('table tbody tr').all()
        print(f"\nFound {len(rows)} table rows.")
        if rows:
            for idx, r in enumerate(rows[:3]):
                td_texts = r.locator('td').all_inner_texts()
                print(f"Row {idx} TD texts:", td_texts)

        ctx.close()

if __name__ == "__main__":
    inspect_tv_options()
