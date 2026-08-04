import json
import logging
import os
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

from src import config

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EXPECTED_MIN_FIELDS = 65


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

    def capture_ticker(self, symbol: str):
        """
        Navigates to the chart for the symbol and captures:
        1. {symbol}_chart.png      — Full 1Y daily view
        2. {symbol}_datawindow.json — Today's indicator values from Data Window
        3. {symbol}_chart_zoom.png  — Zoomed-in daily view with future offset
        """
        logger.info(f"Starting Playwright to capture screenshots for {symbol}...")

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

            # ── Position crosshair on the LATEST (non-future) bar ─────────────────
            # The chart has a right margin of empty/future bars, so a fixed
            # fraction can land on a future bar. Step right-to-left and lock onto
            # the most-recent bar whose date is NOT in the future (prefer today).
            # We must NOT accept the first parseable bar blindly: `d <= now` is
            # true for yesterday too, and a parse failure must be skipped (not
            # accepted), otherwise the Data Window captures the wrong day.
            try:
                box = page.evaluate("""() => {
                    let cv = document.querySelector('canvas');
                    if (!cv) return null;
                    const r = cv.getBoundingClientRect();
                    return { x: r.x, y: r.y, w: r.width, h: r.height };
                }""")
                if box:
                    today = datetime.now().date()
                    _date_fmts = ["%a %d %b '%y", "%a %d %b %Y", "%d %b '%y", "%d %b %Y"]
                    best = None  # (date_obj, mx, my)

                    for frac in [
                        0.92,
                        0.90,
                        0.88,
                        0.86,
                        0.84,
                        0.82,
                        0.80,
                        0.78,
                        0.76,
                        0.74,
                        0.72,
                        0.70,
                        0.68,
                        0.66,
                        0.64,
                        0.62,
                        0.60,
                    ]:
                        mx = box["x"] + box["w"] * frac
                        my = box["y"] + box["h"] * 0.5
                        page.mouse.move(box["x"] + 5, box["y"] + 5)
                        time.sleep(0.15)
                        page.mouse.move(mx, my)
                        time.sleep(0.4)
                        date_str = page.evaluate("""() => {
                            let el = document.querySelector('.chart-data-window [data-test-id-value-title="Date"]');
                            return el && el.nextElementSibling ? el.nextElementSibling.textContent.trim() : null;
                        }""")
                        if not date_str:
                            continue
                        d = None
                        for _fmt in _date_fmts:
                            try:
                                d = datetime.strptime(date_str, _fmt)
                                break
                            except Exception:
                                continue
                        if d is None:
                            # Unparseable date: skip this bar, do NOT accept it.
                            continue
                        if d.date() > today:
                            # Future/empty bar in the right margin — skip.
                            continue
                        # Most-recent non-future bar; first one found (scanning
                        # right->left) is the latest, so keep the max defensively.
                        if best is None or d.date() >= best[0].date():
                            best = (d, mx, my)
                            if d.date() == today:
                                logger.info(f"Positioned crosshair on TODAY's bar ({date_str}).")
                                break

                    if best is not None:
                        # Re-assert the chosen crosshair position before capture.
                        page.mouse.move(box["x"] + 5, box["y"] + 5)
                        time.sleep(0.15)
                        page.mouse.move(best[1], best[2])
                        time.sleep(0.6)
                        logger.info(
                            f"Crosshair locked to latest bar date={best[0].strftime('%a %d %b %y')}."
                        )
                    else:
                        # Fallback: no valid bar resolved — use a sane default.
                        logger.warning(
                            "Could not resolve any valid bar via crosshair; using default position."
                        )
                        page.mouse.move(box["x"] + 5, box["y"] + 5)
                        time.sleep(0.15)
                        page.mouse.move(box["x"] + box["w"] * 0.84, box["y"] + box["h"] * 0.5)
                        time.sleep(0.6)
                else:
                    logger.warning("No chart canvas found for crosshair positioning.")
            except Exception as e:
                logger.warning(f"Crosshair positioning failed: {e}")

            # ── Clear crosshair → force Data Window to latest (today's) bar ────────
            # Moving the mouse to the bottom-right corner of the viewport takes the
            # pointer off the chart, which clears any manual crosshair and makes the
            # Data Window snap to the most recent bar (today) — the reliable way to
            # guarantee the captured values are for today, not yesterday.
            try:
                vp = page.viewport_size or {"width": 1920, "height": 1080}
                page.mouse.move(box["x"] + 5, box["y"] + 5) if box else page.mouse.move(2, 2)
                time.sleep(0.15)
                page.mouse.move(vp["width"] - 2, vp["height"] - 2)
                time.sleep(1.0)
                logger.info(
                    "Cleared crosshair (moved to bottom-right); Data Window now on latest bar."
                )
            except Exception as e:
                logger.warning(f"Crosshair clear failed: {e}")

            logger.info("Capture initiated!")
            safe_symbol = symbol.replace(":", "_")

            # ── 1. Full-view chart screenshot (1Y daily) ───────────────────────────
            chart_path = self.screenshots_dir / f"{safe_symbol}_chart.png"
            logger.info("Taking full-view chart screenshot...")
            page.screenshot(path=str(chart_path))

            # ── 2. Export Chart CSV Data Window & Parse Snapshot ─────────────────
            logger.info("Exporting Chart CSV data...")
            csv_path = self.screenshots_dir / f"{safe_symbol}_datawindow.csv"
            data_window_path = self.screenshots_dir / f"{safe_symbol}_datawindow.json"
            csv_success = False

            try:
                with page.expect_download(timeout=15000) as download_info:
                    page.keyboard.press("Alt+s")
                    time.sleep(0.5)
                    export_btn = page.query_selector(
                        'button[data-name="submit"], button:has-text("Export"), [data-dialog-name] button.submit-button'
                    )
                    if export_btn:
                        export_btn.click()

                download = download_info.value
                download.save_as(str(csv_path))
                logger.info(f"Saved chart data CSV to {csv_path}")

                from src.data.csv_adapter import csv_to_datawindow
                data_dict, hist_df, realvol_10d, ret_10d = csv_to_datawindow(str(csv_path), str(data_window_path))
                csv_success = True
                logger.info(f"Successfully processed CSV snapshot into {data_window_path} (10d realvol={realvol_10d}, 10d ret={ret_10d})")
            except Exception as csv_err:
                logger.warning(f"CSV export download skipped or failed for {symbol}: {csv_err}. Falling back to DOM extraction...")

            if not csv_success:
                # ── DOM Extraction Fallback ───────────────────────────────────────
                try:
                    data_dict = page.evaluate("""() => {
                        let data = {};
                        let container = document.querySelector('.chart-data-window') ||
                                        document.querySelector('[data-name="data-window-container"]') ||
                                        document.querySelector('div[class*="data-window"]') ||
                                        document.querySelector('.widgetbar-widget-datawindow') ||
                                        document.querySelector('.widgetbar-pages');
                        if (!container) {
                            return { "ERROR": "Data Window container not found. It might be closed." };
                        }
                        let titleEls = container.querySelectorAll('[data-test-id-value-title]');
                        titleEls.forEach(el => {
                            let key = el.getAttribute('data-test-id-value-title');
                            let nextSib = el.nextElementSibling;
                            if (key && nextSib) {
                                data[key.trim()] = nextSib.textContent.trim();
                            }
                        });
                        return data;
                    }""")

                    if not data_dict or "ERROR" in data_dict:
                        logger.error(
                            f"Structured extraction failed for {symbol}. Data Window may be closed or UI changed."
                        )
                        raise ValueError(f"Failed to extract structured data window for {symbol}.")

                    if len(data_dict) < EXPECTED_MIN_FIELDS:
                        msg = (
                            f"Data window for {symbol} returned {len(data_dict)} fields, "
                            f"which is less than EXPECTED_MIN_FIELDS ({EXPECTED_MIN_FIELDS})."
                        )
                        logger.error(msg)
                        raise ValueError(msg)

                    with open(data_window_path, "w", encoding="utf-8") as f:
                        json.dump(data_dict, f, indent=4)
                    logger.info(f"Saved pristine Data Window JSON to {data_window_path}")

                except Exception as e:
                    logger.error(f"Failed to scrape data window text: {e}")
                    raise e

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
    import sys

    scraper = TVScraper(worker_id=2)
    symbol_to_test = sys.argv[1] if len(sys.argv) > 1 else "NASDAQ:NVDA"
    scraper.capture_ticker(symbol_to_test)
