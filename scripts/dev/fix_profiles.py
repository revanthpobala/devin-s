import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from src import config

SRC = "tv_chrome_profile_1"
DSTS = ["tv_chrome_profile_4", "tv_chrome_profile_5"]


def clear_locks(ud):
    p = Path(ud)
    if p.exists():
        for it in p.rglob("*"):
            if it.is_file() and it.name.upper() in ("SINGLETONLOCK", "SINGLETONCOOKIE", "SINGLETONSOCKET", "LOCKFILE", "LOCK"):
                try:
                    it.unlink()
                except Exception:
                    pass


def read_tv_cookies(ud):
    clear_locks(ud)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=ud, headless=False,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        page.goto("https://www.tradingview.com/", wait_until="domcontentloaded")
        time.sleep(6)
        cookies = ctx.cookies("https://www.tradingview.com")
        ctx.close()
    # only the auth/session cookies we need
    keep = [c for c in cookies if c["name"] in ("sessionid", "sessionid_sign", "tv_ecuid")]
    return keep


def inject(ud, cookies):
    clear_locks(ud)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=ud, headless=False,
            viewport={"width": 1920, "height": 1080},
            args=["--disable-blink-features=AutomationControlled"])
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto("https://www.tradingview.com/chart/?symbol=NASDAQ:ADBE", wait_until="domcontentloaded")
        time.sleep(8)
        try:
            page.wait_for_selector("canvas", timeout=20000)
        except Exception:
            pass
        after = [c for c in ctx.cookies("https://www.tradingview.com") if c["name"] == "sessionid"]
        print(f"{Path(ud).name}: sessionid after inject = {len(after)}")
        ctx.close()


if __name__ == "__main__":
    src_ud = str(config.BASE_DIR / SRC)
    tv_cookies = read_tv_cookies(src_ud)
    print(f"Read {len(tv_cookies)} TV session cookies from {SRC}: {[c['name'] for c in tv_cookies]}")
    for d in DSTS:
        inject(str(config.BASE_DIR / d), tv_cookies)
    print("Done.")
