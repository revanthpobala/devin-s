import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from src import config

def main():
    profile_dir = config.BASE_DIR / "tv_chrome_profile_1"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("LAUNCHING TRADINGVIEW CHROME PROFILE SETUP")
    print(f"Profile Path: {profile_dir}")
    print("Instructions:")
    print("1. Log into your TradingView account in the browser window.")
    print("2. Open your chart layout with 'Rev - Enhanced v2' indicator loaded.")
    print("3. Ensure the Data Window tab is open on the right panel.")
    print("4. When finished, press Ctrl+C in this terminal to save profile & exit.")
    print("=" * 70)

    # Clean Singleton locks
    for item in profile_dir.rglob("*"):
        if item.is_file() and item.name.upper() in ("SINGLETONLOCK", "SINGLETONCOOKIE", "SINGLETONSOCKET", "LOCKFILE", "LOCK"):
            try:
                item.unlink()
            except Exception:
                pass

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        chart_url = os.getenv("TV_CHART_URL", "https://www.tradingview.com/chart/")
        page.goto(chart_url)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nProfile setup complete. Closing browser...")
            context.close()

if __name__ == "__main__":
    main()
