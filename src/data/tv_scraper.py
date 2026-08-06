import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from src import config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# TradingView chart controls. These exact selectors are the ones proven to work in
# data-windows/scripts/tv_export.py; do not "simplify" them.
MENU_BTN = 'button[data-name="save-load-menu"]'
DL_ITEM = 'text=/Download chart data/i'
DL_BTN = 'button[data-qa-id="download-btn"]'
GOTO_BTN = 'button[data-name="go-to-date"]'
GOTO_START = 'input[data-name="start-date-range"]'
GOTO_END = 'input[data-name="end-date-range"]'
GOTO_SUBMIT = 'button[data-name="submit-button"]'
MOVE_RIGHT_BTN = 'div.control-bar__btn--move-right'
ZOOM_OUT_BTN = 'div.control-bar__btn--zoom-out'
MOVE_RIGHT_TIMES = 4
ZOOM_OUT_TIMES = 3

# MEASURED: this does NOT control how many rows the CSV contains. TradingView
# exports whatever it has LOADED, which is 300 daily bars, and 35d vs 90d were
# verified byte-identical - 300 rows both times, all 14 indicator fields equal.
# Depth only changes once the range reaches past the loaded window (the corpus
# builder asks for 2017 and gets ~2400 rows).
#
# So for live use this is really a SCREENSHOT ZOOM setting: it sets the visible
# range the zoomed image is taken from. 90d ~= 63 candles, which stays legible
# per-bar while still showing swing structure; 30d ~= 21 candles loses the
# structure for no gain, since any shorter window can be sliced out of the same
# CSV in pandas for free.
#
# 300 rows also clears the 260-bar floor the indicator's 250-bar z-scores need,
# so the live pull never has to go deeper.
DEFAULT_LOOKBACK_DAYS = 90
SETTLE_SECONDS = 6.0     # let the new range load and the indicator recompute

class TVScraper:
    def __init__(self, worker_id: int = None, target_date: str = None, chrome_profile: str = None):
        self.chart_url = os.getenv("TV_CHART_URL", "https://www.tradingview.com/chart/")
        # Store Chrome profile locally so user only logs in once
        if chrome_profile:
            self.user_data_dir = str(config.BASE_DIR / chrome_profile)
        elif worker_id is not None:
            self.user_data_dir = str(config.BASE_DIR / f"tv_chrome_profile_{worker_id}")
        else:
            self.user_data_dir = str(config.BASE_DIR / "tv_chrome_profile")

        # Create a date-specific subfolder for today's screenshots
        today_str = target_date or datetime.now().strftime("%Y-%m-%d")
        self.screenshots_dir = config.BASE_DIR / "data" / "raw" / today_str
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Clean stale Singleton lock files recursively from Chrome profile directory
        profile_path = Path(self.user_data_dir)
        if profile_path.exists():
            for item in profile_path.rglob("*"):
                if item.is_file() and item.name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
                    try:
                        item.unlink()
                    except Exception:
                        pass

    def _pan_zoom_chart(self, page, move_right: int = MOVE_RIGHT_TIMES,
                        zoom_out: int = ZOOM_OUT_TIMES):
        # Scroll the chart forward (into future / projection space) and zoom out
        # so the captured view shows more context. Two equivalent input paths
        # (same as a human): click the chart control-bar button, or use the
        # keyboard shortcut (ArrowRight to pan right, Ctrl+ArrowDown to zoom
        # out). The control bar can be lazy-rendered, so we make sure the
        # button is visible (hover) before clicking.
        def _click_btn(selector, times):
            btn = page.query_selector(selector)
            if btn is None:
                return False
            box = btn.bounding_box()
            if box is None:
                return False
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            try:
                page.mouse.move(cx, cy)
                page.wait_for_selector(selector, state="visible", timeout=5000)
            except Exception:
                pass
            for _ in range(times):
                try:
                    page.mouse.click(cx, cy)
                except Exception:
                    return False
                time.sleep(0.15)
            return True

        if not _click_btn(MOVE_RIGHT_BTN, move_right):
            logger.warning("move-right button unavailable; using ArrowRight key")
            for _ in range(move_right):
                page.keyboard.press("ArrowRight")
                time.sleep(0.15)

        if not _click_btn(ZOOM_OUT_BTN, zoom_out):
            logger.warning("zoom-out button unavailable; using Ctrl+ArrowDown key")
            for _ in range(zoom_out):
                page.keyboard.press("Control+ArrowDown")
                time.sleep(0.15)

        # Clear the crosshair. Escape dismisses any lingering tooltips/menus/crosshairs.
        # We move the mouse out of the main chart area and press Escape.
        vp = page.viewport_size or {"width": 1920, "height": 1080}
        page.mouse.move(vp["width"] - 1, vp["height"] // 2)
        time.sleep(0.5)
        page.keyboard.press("Escape")
        time.sleep(1.0)

    def capture_ticker(self, symbol: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                       settle_s: float = SETTLE_SECONDS):
        """
        Navigates to the chart for the symbol and captures:
        1. {symbol}_chart.png      — Full 1Y daily view
        2. {symbol}_datawindow.json — Today's indicator values from Data Window
        3. {symbol}_chart_zoom.png  — Zoomed-in daily view with future offset
        """
        logger.info(f"Starting Playwright to capture screenshots for {symbol}...")

        profile_path = Path(self.user_data_dir)
        if profile_path.exists():
            for item in profile_path.rglob("*"):
                if item.is_file() and item.name.upper() in ("SINGLETONLOCK", "SINGLETONCOOKIE", "SINGLETONSOCKET", "LOCKFILE", "LOCK"):
                    try:
                        item.unlink()
                    except Exception:
                        pass

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=False,
                viewport={"width": 1920, "height": 1080},
                args=["--disable-blink-features=AutomationControlled"],
            )

            page = context.new_page()
            url = f"{self.chart_url}?symbol={symbol}"
            logger.info(f"Navigating to {url}")
            page.goto(url, wait_until="domcontentloaded")

            logger.info("Waiting for chart canvas to render...")
            try:
                page.wait_for_selector("canvas", timeout=30000)
            except Exception as e:
                error_screenshot = self.screenshots_dir / f"{symbol}_error.png"
                page.screenshot(path=str(error_screenshot))
                logger.error(f"Failed to find canvas, screenshot saved to {error_screenshot}")
                raise e

            logger.info(
                "Chart loaded. Waiting 60 seconds for indicators and Data Window to fully populate..."
            )
            time.sleep(60)

            # ── Ensure Data Window panel is open & visible ─────────────────────────
            logger.info("Ensuring Data Window is active and visible...")
            try:

                def _dw_visible(page):
                    # The real TradingView container is `.chart-data-window`
                    # (class `container-... chart-data-window`); there is no
                    # [data-name="data-window-container"] element.
                    return page.evaluate("""() => {
                        let el = document.querySelector('.chart-data-window');
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 10 || r.height < 10) return false;
                        const cs = getComputedStyle(el);
                        return cs.display !== 'none' && cs.visibility !== 'hidden' &&
                               cs.opacity !== '0';
                    }""")

                def _object_tree_btn(page):
                    # Toggle button: <button data-name="object_tree"
                    #   aria-label="Object tree and data window"
                    #   aria-pressed="false|true">.
                    btn = page.query_selector('button[data-name="object_tree"]')
                    if btn:
                        return btn
                    # Fallback: find by label/title if attribute changed.
                    for h in page.query_selector_all('button, [role="button"]'):
                        attrs = " ".join(
                            filter(
                                None,
                                [
                                    h.get_attribute("data-name") or "",
                                    h.get_attribute("aria-label") or "",
                                    h.get_attribute("title") or "",
                                ],
                            )
                        ).lower()
                        if "object tree" in attrs or "data window" in attrs:
                            return h
                    return None

                def _pane_open(page):
                    # The pane is OPEN when the toggle button reports
                    # aria-pressed="true" AND the object-tree container is in
                    # the DOM. Both must hold to avoid a stale-closed state.
                    btn = _object_tree_btn(page)
                    if btn and btn.get_attribute("aria-pressed") == "true":
                        return True
                    return page.evaluate("""() => {
                        return !!document.querySelector('.widgetbar-widget-object_tree') ||
                               !!document.querySelector('.chart-data-window');
                    }""")

                def _ensure_pane_open(page):
                    # Open the pane ONLY if it is actually closed (aria-pressed
                    # != "true"). Clicking an already-open pane would toggle it
                    # shut. Use a REAL mouse click at the button's center
                    # coordinates so the topmost element (the button, not the
                    # inner <svg>/<path>) receives a trusted pointer event —
                    # TradingView toggles via pointer events, and Playwright's
                    # element.click() can land on the inner icon and be ignored.
                    btn = _object_tree_btn(page)
                    if not btn:
                        return False
                    if btn.get_attribute("aria-pressed") == "true":
                        return True
                    box = btn.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        try:
                            page.mouse.click(cx, cy)
                        except Exception:
                            try:
                                btn.click(timeout=2000, force=True)
                            except Exception:
                                pass
                        time.sleep(1.0)
                        if btn.get_attribute("aria-pressed") == "true":
                            return True
                    # Last resort: forced element click.
                    try:
                        btn.click(timeout=2000, force=True)
                    except Exception:
                        pass
                    time.sleep(0.5)
                    return btn.get_attribute("aria-pressed") == "true"

                def _ensure_data_window_tab(page):
                    # The pane has two tabs: "Object tree" and "Data window".
                    # Make sure the Data window tab is the selected one.
                    try:
                        tab = page.query_selector("#data-window")
                        if tab and tab.get_attribute("aria-selected") != "true":
                            tbox = tab.bounding_box()
                            if tbox:
                                page.mouse.click(
                                    tbox["x"] + tbox["width"] / 2,
                                    tbox["y"] + tbox["height"] / 2,
                                )
                            else:
                                tab.click(timeout=1500, force=True)
                            time.sleep(0.8)
                            return True
                    except Exception:
                        pass
                    return False

                # Open the pane ONLY if it is actually closed — clicking an
                # already-open pane would toggle it shut. Then ensure the
                # "Data window" tab (not "Object tree") is the active one.
                opened = False
                for attempt in range(8):
                    if _dw_visible(page):
                        opened = True
                        break
                    if not _pane_open(page):
                        _ensure_pane_open(page)
                    else:
                        # Pane claims open but Data Window not visible yet
                        # (e.g. wrong tab) — bump the tab selection.
                        _ensure_data_window_tab(page)
                    _ensure_data_window_tab(page)
                    time.sleep(2.5)
                if not opened:
                    logger.warning(
                        "Data Window still not visible after retries — extraction may fail."
                    )
                else:
                    logger.info("Data Window is open and visible.")
                time.sleep(2)  # let values populate
            except Exception as e:
                logger.warning(f"Error while ensuring Data Window is active: {e}")

            logger.info("Capture initiated!")
            safe_symbol = symbol.replace(":", "_")

            # ── 1. Wide-view chart screenshot (default range) ─────────────────────
            # Taken BEFORE the Go-to, so it keeps the long structural view: where price
            # sits against the 200MA, prior swings, the overall stage. The gem needs that
            # context and it is not visible once we zoom in. Pan right + zoom out first so
            # the view scrolls into future space and widens the context window.
            self._pan_zoom_chart(page)
            chart_path = self.screenshots_dir / f"{safe_symbol}_chart.png"
            logger.info("Taking wide-view chart screenshot...")
            page.screenshot(path=str(chart_path))

            # ── 2. Zoomed screenshot (exactly 3 months) ───────────────
            # The zoomed screenshot should always cover 3 months (90 days) for proper visual context.
            zoom_start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            logger.info(f"Setting range to 3 months for zoomed screenshot (range: {zoom_start_date} -> today)...")

            page.wait_for_selector(GOTO_BTN, state="visible", timeout=30000)
            page.click(GOTO_BTN, timeout=15000)
            page.fill(GOTO_START, zoom_start_date, timeout=15000)
            page.fill(GOTO_END, datetime.now().strftime("%Y-%m-%d"), timeout=15000)
            page.click(GOTO_SUBMIT, timeout=15000)
            time.sleep(settle_s)   # let the new range load and the indicator recompute

            # ── 2b. Take Zoomed screenshot ─────────────
            self._pan_zoom_chart(page, move_right=4, zoom_out=0)
            page.keyboard.press("Escape")  # extra escape to ensure crosshair is gone
            time.sleep(0.5)
            
            zoom_path = self.screenshots_dir / f"{safe_symbol}_chart_zoom.png"
            page.screenshot(path=str(zoom_path))
            logger.info(f"Saved zoomed chart screenshot to {zoom_path}")

            # ── 2c. Download chart CSV ───────────────
            # Set the range to lookback_days if it's different from 90 (e.g. 365 days for SPX)
            if lookback_days != 90:
                start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                logger.info(f"Setting range for CSV download (range: {start_date} -> today)...")
                page.click(GOTO_BTN, timeout=15000)
                page.fill(GOTO_START, start_date, timeout=15000)
                page.fill(GOTO_END, datetime.now().strftime("%Y-%m-%d"), timeout=15000)
                page.click(GOTO_SUBMIT, timeout=15000)
                time.sleep(settle_s)
                
            csv_path = self.screenshots_dir / f"{safe_symbol}_datawindow.csv"
            data_window_path = self.screenshots_dir / f"{safe_symbol}_datawindow.json"

            page.click(MENU_BTN, timeout=15000)
            page.click(DL_ITEM, timeout=15000)
            with page.expect_download(timeout=90000) as info:
                page.click(DL_BTN, timeout=15000)
            dl = info.value

            # The saved filename carries whatever symbol was ACTUALLY charted. If the
            # URL symbol never applied, the layout is still on its own default and we
            # would file another company's data under this ticker - a silent
            # wrong-data bug, so refuse it rather than write it.
            name = dl.suggested_filename or f"{safe_symbol}, 1D.csv"
            charted = name.split(",")[0].split("_")[-1].strip()
            wanted = symbol.split(":")[-1].strip()
            if charted.upper() != wanted.upper():
                raise ValueError(f"charted {charted}, not {wanted} - symbol never applied")

            dl.save_as(str(csv_path))
            logger.info(f"Saved chart CSV to {csv_path}")

            from src.data.csv_adapter import csv_to_datawindow
            data_dict, hist_df, realvol_10d, ret_10d = csv_to_datawindow(
                str(csv_path), str(data_window_path)
            )
            logger.info(
                f"Snapshot -> {data_window_path} "
                f"(bar_date={data_dict.get('bar_date')}, rows={len(hist_df)}, "
                f"realvol_10d={realvol_10d}, ret_10d={ret_10d})"
            )

            # ── 3. Zoomed-in screenshot (DISABLED) ────────────────────────────────
            # logger.info("Taking zoomed-in chart screenshot...")
            # try:
            #     page.mouse.move(0, 0)
            #     time.sleep(0.3)
            #     zoom_path = self.screenshots_dir / f"{safe_symbol}_chart_zoom.png"
            #     page.screenshot(path=str(zoom_path))
            #     logger.info(f"Zoomed screenshot saved to {zoom_path}")
            # except Exception as zoom_err:
            #     logger.error(f"Zoomed screenshot failed for {symbol}: {zoom_err}")

            context.close()
            logger.info(f"Finished capturing {symbol}.")


if __name__ == "__main__":
    import concurrent.futures
    import sys

    # Fixed, pre-logged-in Chrome profiles (one per parallel worker so sessions
    # never collide on the Singleton lock). Order matters only for assignment.
    CHROME_PROFILES = [
        "tv_chrome_profile_1",
        "tv_chrome_profile_2",
        "tv_chrome_profile_4",
        "tv_chrome_profile_3",
        "tv_chrome_profile_5",
    ]

    symbols = []
    target_date = None
    for arg in sys.argv[1:]:
        if arg.startswith("--date="):
            target_date = arg.split("=", 1)[1]
        else:
            symbols.append(arg)
    if not symbols:
        symbols = ["NASDAQ:NVDA"]

    def _run_one(idx_symbol):
        idx, symbol = idx_symbol
        profile = CHROME_PROFILES[idx % len(CHROME_PROFILES)]
        scraper = TVScraper(chrome_profile=profile, target_date=target_date)
        try:
            scraper.capture_ticker(symbol)
        except Exception as e:
            logger.error(f"Scrape failed for {symbol} (profile {profile}): {e}")

    logger.info(f"Scraping {len(symbols)} ticker(s) across {len(CHROME_PROFILES)} Chrome profiles...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CHROME_PROFILES)) as executor:
        list(executor.map(_run_one, list(enumerate(symbols))))