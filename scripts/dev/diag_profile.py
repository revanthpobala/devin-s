import os, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from src import config

AUTH_HINTS = ("session", "auth", "token", "cook", "id", "user", "login", "ecos", "tv_")

def diag(profile):
    ud = str(config.BASE_DIR / profile)
    pp = Path(ud)
    if pp.exists():
        for it in pp.rglob("*"):
            if it.is_file() and it.name.upper() in ("SINGLETONLOCK","SINGLETONCOOKIE","SINGLETONSOCKET","LOCKFILE","LOCK"):
                try: it.unlink()
                except Exception: pass
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=ud, headless=False,
            viewport={"width":1920,"height":1080},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()
        page.goto("https://www.tradingview.com/chart/?symbol=NASDAQ:ADBE", wait_until="domcontentloaded")
        time.sleep(10)
        try:
            page.wait_for_selector("canvas", timeout=20000)
        except Exception:
            pass
        cookies = ctx.cookies("https://www.tradingview.com")
        tv = [c for c in cookies if "tradingview" in c.get("domain","")]
        auth = []
        for c in tv:
            n = c["name"].lower()
            if any(h in n for h in AUTH_HINTS):
                auth.append((c["name"], len(c.get("value",""))))
        print(f"=== {profile} ===")
        print(f"total TV cookies: {len(tv)}; auth-ish cookies: {len(auth)}")
        for name, vlen in sorted(auth):
            print(f"   {name} (val_len={vlen})")
        if not auth:
            print("   >>> NO session/auth cookies -> NOT logged in")
        print()
        ctx.close()

if __name__ == "__main__":
    for prof in ["tv_chrome_profile_1","tv_chrome_profile_2","tv_chrome_profile_4","tv_chrome_profile_3","tv_chrome_profile_5"]:
        diag(prof)
