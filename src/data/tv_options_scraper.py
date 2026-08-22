"""
src/data/tv_options_scraper.py

Targeted Strategy Finder & Volume Visual Scraper for TradingView Options Suite:
1. Opens Options Dialog via button[data-name="options-builder-dialog-button"]
2. Switches to Strategy Finder tab (#spreadex) and applies dynamic filters:
   - Prediction Period: [data-qa-id*="dates-range-pill"]
   - Expected Price Range: [data-qa-id*="price-range-pill"]
   - Volume: [data-qa-id*="volume-pill"]
   - Moneyness: [data-qa-id*="moneyness-pill"]
3. Scrapes and normalizes the Strategy Finder table data -> {symbol}_tv_strategies.json
4. Captures Volume Charts on demand directed by LLM:
   - Heatmap: #volume -> #heatmap -> {symbol}_options_heatmap.png
   - By Expiration: #volume -> #by-expiration -> {symbol}_options_expirations.png
   - By Strike: #volume -> #by-strike -> {symbol}_options_strikes.png
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

# Verified TradingView DOM Selectors
OPTIONS_BTN = 'button[data-name="options-builder-dialog-button"], button[aria-label="Options"]'
OPTIONS_DIALOG = 'div[class*="isDialog"], div[class*="root-uXkTVtpl"], div[role="dialog"]'
STRATEGY_FINDER_TAB = '#spreadex, button:has-text("Strategy finder")'
VOLUME_TAB = '#volume, button:has-text("Volume")'
HEATMAP_SUBTAB = '#heatmap, button[data-qa-id="heatmap"]'
BY_EXPIRATION_SUBTAB = '#by-expiration, button[data-qa-id="by-expiration"]'
BY_STRIKE_SUBTAB = '#by-strike, button[data-qa-id="by-strike"]'
CLOSE_BTN = 'button[data-name="close"], button[aria-label="Close"], header button:has(svg)'

# Filter Pill Selectors
DATES_RANGE_PILL = 'button[data-qa-id*="dates-range-pill"]'
PRICE_RANGE_PILL = 'button[data-qa-id*="price-range-pill"]'
STRATEGY_TYPES_PILL = 'button[data-qa-id*="strategy-types-pill"]'
VOLUME_PILL = 'button[data-qa-id*="volume-pill"]'
MONEYNESS_PILL = 'button[data-qa-id*="moneyness-pill"]'


def _clean_str(val: Optional[str]) -> str:
    """Normalizes scraped text by stripping unicode minus, em-dashes, and excessive spaces."""
    if not val:
        return ""
    s = str(val).strip()
    s = s.replace("\u2212", "-").replace("—", "").replace("–", "-")
    return s.strip()


def _parse_float(val: Optional[str]) -> Optional[float]:
    """Parses clean numeric float from string, handling currency signs, commas, and percentage."""
    if not val:
        return None
    s = _clean_str(val)
    s = s.replace("$", "").replace(",", "").replace("%", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


class TVOptionsScraper:
    """Automates targeted options strategy extraction and volume visual capture from TradingView."""

    def __init__(self, load_wait_s: float = 8.0):
        self.load_wait_s = load_wait_s

    def open_options_modal(self, page) -> bool:
        """Opens TradingView Options Suite."""
        logger.info("Opening TradingView Options Suite...")
        try:
            btn = page.locator(OPTIONS_BTN).first
            if not btn.is_visible():
                btn = page.locator('button[data-tooltip="Options"]').first

            if btn.is_visible():
                try:
                    btn.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("el => el.click()", btn.element_handle())
                time.sleep(self.load_wait_s)
                return True

            logger.warning("Options button not found.")
            return False
        except Exception as e:
            logger.warning(f"Failed to open Options modal: {e}")
            return False

    def set_prediction_period(self, page, period_text: str = "Next month") -> bool:
        """Sets the prediction period filter (e.g., 'Next month', 'Next 2 weeks', 'Next 3 months', 'Next 6 months', 'Next year')."""
        try:
            pill = page.locator(DATES_RANGE_PILL).first
            if pill.is_visible():
                current_text = pill.inner_text().strip()
                # If already set
                clean_target = period_text.lower().replace("next ", "").strip()
                if clean_target in current_text.lower():
                    return True
                try:
                    pill.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("el => el.click()", pill.element_handle())
                time.sleep(1.2)

                # Try multi-level matching for TradingView dropdown items
                candidates = [
                    period_text,
                    clean_target,
                    period_text.capitalize(),
                ]
                clicked = False
                for cand in candidates:
                    for selector in (
                        f'[role="menuitem"]:has-text("{cand}")',
                        f'[data-role="menuitem"]:has-text("{cand}")',
                        f'div[class*="item"]:has-text("{cand}")',
                        f'span:has-text("{cand}")',
                        f'button:has-text("{cand}")',
                    ):
                        loc = page.locator(selector).first
                        if loc.is_visible():
                            loc.click()
                            time.sleep(1.5)
                            clicked = True
                            break
                    if clicked:
                        break

                new_text = pill.inner_text().strip()
                if clean_target in new_text.lower():
                    logger.info(f"Successfully set prediction period to '{new_text}'")
                    return True
                else:
                    logger.warning(f"Failed to switch prediction period to '{period_text}' (pill still '{new_text}')")
                    page.keyboard.press("Escape")
        except Exception as e:
            logger.warning(f"Could not set prediction period: {e}")
        return False

    def set_expected_price_range(self, page, move_text: str = "+5% to +10%") -> bool:
        """Sets the expected price range move pill (e.g., '+5% to +10%', '-5% to -10%', '+10% to +15%')."""
        try:
            pill = page.locator(PRICE_RANGE_PILL).first
            if pill.is_visible():
                current_text = pill.inner_text().strip()
                clean_target = move_text.lower().replace(" ", "")
                if clean_target in current_text.lower().replace(" ", ""):
                    return True
                pill.click()
                time.sleep(1.2)

                candidates = [move_text, move_text.replace("+", ""), move_text.replace(" ", "")]
                clicked = False
                for cand in candidates:
                    for selector in (
                        f'[role="menuitem"]:has-text("{cand}")',
                        f'[data-role="menuitem"]:has-text("{cand}")',
                        f'div[class*="item"]:has-text("{cand}")',
                        f'span:has-text("{cand}")',
                        f'button:has-text("{cand}")',
                    ):
                        loc = page.locator(selector).first
                        if loc.is_visible():
                            loc.click()
                            time.sleep(1.5)
                            clicked = True
                            break
                    if clicked:
                        break

                new_text = pill.inner_text().strip()
                logger.info(f"Set expected price range (pill is now '{new_text}')")
                return True
        except Exception as e:
            logger.warning(f"Could not set price range: {e}")
        return False

    def set_volume_filter(self, page, volume_tier: str = "100 to 500") -> bool:
        """Sets the volume liquidity filter (e.g., '100 to 500', '500 to 2 K', '2 K to 10 K', 'Above 10 K')."""
        try:
            pill = page.locator('button[data-qa-id*="volume-pill"]').first
            if pill.is_visible():
                pill.click()
                time.sleep(1.0)
                option = page.locator(f'[role="menuitem"]:has-text("{volume_tier}"), [data-role="menuitem"]:has-text("{volume_tier}"), span:has-text("{volume_tier}")').first
                if option.is_visible():
                    option.click()
                    time.sleep(1.5)
                    logger.info(f"Set volume filter to '{volume_tier}'")
                    return True
                else:
                    page.keyboard.press("Escape")
        except Exception as e:
            logger.warning(f"Could not set volume filter: {e}")
        return False

    def set_moneyness_filter(self, page, moneyness_label: str = "Out of the money") -> bool:
        """Sets the moneyness filter (e.g., 'Out of the money', 'At the money', 'In the money')."""
        try:
            pill = page.locator('button[data-qa-id*="moneyness-pill"]').first
            if pill.is_visible():
                pill.click()
                time.sleep(1.0)
                checkbox = page.locator(f'label:has-text("{moneyness_label}"), [role="menuitem"]:has-text("{moneyness_label}"), span:has-text("{moneyness_label}")').first
                if checkbox.is_visible():
                    checkbox.click()
                    time.sleep(1.5)
                    logger.info(f"Set moneyness filter to '{moneyness_label}'")
                    page.keyboard.press("Escape")
                    return True
                else:
                    page.keyboard.press("Escape")
        except Exception as e:
            logger.warning(f"Could not set moneyness filter: {e}")
        return False

    def capture_volume_visuals(self, page, symbol: str, out_dir: Path, chart_types: Optional[List[str]] = None) -> Dict[str, str]:
        """Captures clean, uncropped dialog screenshots of the requested Volume charts (heatmap, by_expiration, by_strike)."""
        if not chart_types:
            chart_types = ["heatmap", "by_expiration"]

        results = {}
        try:
            vol_tab = page.locator(VOLUME_TAB).first
            if vol_tab.is_visible():
                try:
                    vol_tab.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("el => el.click()", vol_tab.element_handle())
                time.sleep(2.0)

            dialog = page.locator(OPTIONS_DIALOG).first

            # 1. Heatmap
            if "heatmap" in chart_types:
                hm_tab = page.locator(HEATMAP_SUBTAB).first
                if hm_tab.is_visible():
                    try:
                        hm_tab.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("el => el.click()", hm_tab.element_handle())
                    time.sleep(2.0)
                    hm_path = out_dir / f"{symbol}_options_heatmap.png"
                    if dialog.is_visible():
                        dialog.screenshot(path=str(hm_path))
                    else:
                        page.screenshot(path=str(hm_path))
                    results["heatmap"] = str(hm_path)
                    logger.info(f"[{symbol}] Captured Volume Heatmap -> {hm_path}")

            # 2. By Expiration
            if "by_expiration" in chart_types:
                exp_tab = page.locator(BY_EXPIRATION_SUBTAB).first
                if exp_tab.is_visible():
                    try:
                        exp_tab.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("el => el.click()", exp_tab.element_handle())
                    time.sleep(2.0)
                    exp_path = out_dir / f"{symbol}_options_expirations.png"
                    if dialog.is_visible():
                        dialog.screenshot(path=str(exp_path))
                    else:
                        page.screenshot(path=str(exp_path))
                    results["by_expiration"] = str(exp_path)
                    logger.info(f"[{symbol}] Captured Volume by Expiration -> {exp_path}")

            # 3. By Strike
            if "by_strike" in chart_types:
                strike_tab = page.locator(BY_STRIKE_SUBTAB).first
                if strike_tab.is_visible():
                    try:
                        strike_tab.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("el => el.click()", strike_tab.element_handle())
                    time.sleep(2.0)
                    strike_path = out_dir / f"{symbol}_options_strikes.png"
                    if dialog.is_visible():
                        dialog.screenshot(path=str(strike_path))
                    else:
                        page.screenshot(path=str(strike_path))
                    results["by_strike"] = str(strike_path)
                    logger.info(f"[{symbol}] Captured Volume by Strike -> {strike_path}")

        except Exception as e:
            logger.warning(f"[{symbol}] Error capturing volume visuals: {e}")

        return results

    def scrape_strategies(
        self,
        page,
        symbol: str,
        out_dir: Path,
        prediction_period: str = "Next month",
        expected_move: str = "+5% to +10%",
        min_volume: Optional[str] = "100 to 500",
        moneyness: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Scrapes the Strategy Finder table into structured JSON with all cleaned & normalized fields."""
        logger.info(f"[{symbol}] Scraping Strategy Finder (Period: {prediction_period}, Move: {expected_move})...")
        strategies: List[Dict[str, Any]] = []

        try:
            # 1. Switch to Strategy finder tab (#spreadex)
            tab = page.locator(STRATEGY_FINDER_TAB).first
            if tab.is_visible():
                try:
                    tab.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("el => el.click()", tab.element_handle())
                time.sleep(3.0)

            # 2. Configure Filters if requested
            if prediction_period:
                self.set_prediction_period(page, prediction_period)
            if expected_move:
                self.set_expected_price_range(page, expected_move)
            if min_volume:
                self.set_volume_filter(page, min_volume)
            if moneyness:
                self.set_moneyness_filter(page, moneyness)

            time.sleep(2.5)  # Wait for table recalculation

            # 3. Parse Strategy Table Rows
            rows = page.locator("tr.row-NU7JvpHi, table tbody tr").all()
            for row in rows:
                try:
                    text = row.inner_text().strip()
                    if not text or "Expiration" in text or "Strategy type" in text:
                        continue

                    # Extract formula badges (e.g. 320C, 325C)
                    badges = row.locator("span.badgeCall-O5krINmH, span.badgePut-O5krINmH, span[class*='badge']").all()
                    formula_strikes = [_clean_str(b.inner_text()) for b in badges if b.inner_text().strip()]

                    cells = [_clean_str(c) for c in text.split("\t") if c.strip()]
                    if not cells or len(cells) < 4:
                        cells = [_clean_str(c) for c in text.split("\n") if c.strip()]

                    if len(cells) >= 6:
                        exp_str = cells[0] if len(cells) > 0 else ""
                        days_val = int(_parse_float(cells[1]) or 0) if len(cells) > 1 else 0
                        strat_type = cells[2] if len(cells) > 2 else ""
                        formula_str = " / ".join(formula_strikes) if formula_strikes else (cells[3] if len(cells) > 3 else "")

                        max_profit_str = cells[4] if len(cells) > 4 else ""
                        max_loss_str = cells[5] if len(cells) > 5 else ""
                        rr_str = cells[6] if len(cells) > 6 else ""
                        be_str = cells[7] if len(cells) > 7 else ""
                        theo_str = cells[8] if len(cells) > 8 else ""
                        bid_str = cells[9] if len(cells) > 9 else ""
                        ask_str = cells[10] if len(cells) > 10 else ""
                        spread_pct_str = cells[11] if len(cells) > 11 else ""
                        exp_payoff_str = cells[12] if len(cells) > 12 else ""

                        strategies.append({
                            "expiration": exp_str,
                            "days": days_val,
                            "strategy_type": strat_type,
                            "formula": formula_str,
                            "max_profit": _parse_float(max_profit_str),
                            "max_loss": _parse_float(max_loss_str),
                            "reward_risk": _parse_float(rr_str),
                            "breakeven": _parse_float(be_str),
                            "theo_price": _parse_float(theo_str),
                            "bid": _parse_float(bid_str),
                            "ask": _parse_float(ask_str),
                            "bid_ask_spread_pct": _clean_str(spread_pct_str),
                            "expected_payoff": _parse_float(exp_payoff_str),
                            "max_profit_display": f"${max_profit_str}",
                            "max_loss_display": f"${max_loss_str}",
                            "reward_risk_display": f"{rr_str}:1" if rr_str else "",
                            "breakeven_display": f"${be_str}",
                        })
                except Exception:
                    continue

            if strategies:
                is_default = (
                    "month" in (prediction_period or "").lower()
                    and not any(k in (prediction_period or "").lower() for k in ("3", "6", "year"))
                    and "+5" in (expected_move or "")
                )
                if is_default:
                    out_path = out_dir / f"{symbol}_tv_strategies.json"
                else:
                    p_slug = (prediction_period or "default").lower().replace(" ", "_")
                    m_slug = (expected_move or "default").replace("%", "").replace(" ", "").replace("+", "").replace("-", "down_")
                    out_path = out_dir / f"{symbol}_tv_strategies_{p_slug}_{m_slug}.json"
                out_path.write_text(json.dumps(strategies, indent=2, ensure_ascii=False), encoding="utf-8")
                logger.info(f"[{symbol}] Successfully saved {len(strategies)} clean targeted strategies -> {out_path}")

            return strategies
        except Exception as e:
            logger.warning(f"[{symbol}] Strategy scrape error: {e}")
            return strategies

    def close_options_modal(self, page):
        """Closes Options Suite modal."""
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
            close_btn = page.locator(CLOSE_BTN).first
            if close_btn.is_visible():
                close_btn.click()
                time.sleep(0.5)
        except Exception:
            page.keyboard.press("Escape")

    def scrape_options_suite(
        self,
        page,
        symbol: str,
        out_dir: Path,
        prediction_period: str = "Next month",
        expected_move: str = "+5% to +10%",
        min_volume: Optional[str] = "100 to 500",
        moneyness: Optional[str] = None,
        capture_volume_charts: bool = False,
    ) -> Dict[str, Any]:
        """Runs targeted Strategy Finder extraction (focuses on clean structured spread math; skips modal screenshots)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        data = {"strategies": [], "visuals": {}}

        opened = self.open_options_modal(page)
        if not opened:
            return data

        try:
            data["strategies"] = self.scrape_strategies(
                page, symbol, out_dir,
                prediction_period=prediction_period,
                expected_move=expected_move,
                min_volume=min_volume,
                moneyness=moneyness,
            )
            if capture_volume_charts:
                data["visuals"] = self.capture_volume_visuals(page, symbol, out_dir)
        finally:
            self.close_options_modal(page)

        return data


def scrape_tv_options_finder_tool(
    ticker: str,
    prediction_period: str = "Next month",
    expected_move: str = "+5% to +10%",
    min_volume: Optional[str] = "100 to 500",
    moneyness: Optional[str] = None,
    capture_volume_charts: bool = False,
) -> str:
    """Tool function callable by the Brain LLM to dynamically fetch TradingView Strategy Finder spreads."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "raw" / today_str / ticker.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    
    period_slug = prediction_period.lower().replace(" ", "_")
    move_slug = expected_move.replace("%", "").replace(" ", "").replace("+", "").replace("-", "down_")
    strat_file = out_dir / f"{ticker.upper()}_tv_strategies_{period_slug}_{move_slug}.json"
    
    # If default 1-month is requested, fall back to standard tv_strategies.json if present
    if not strat_file.exists() and "month" in period_slug and not any(k in period_slug for k in ("3", "6", "year")):
        default_strat = out_dir / f"{ticker.upper()}_tv_strategies.json"
        if default_strat.exists():
            strat_file = default_strat

    strategies = []
    if strat_file.exists():
        try:
            strategies = json.loads(strat_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not strategies:
        try:
            from playwright.sync_api import sync_playwright
            profile_dir = config.BASE_DIR / "tv_chrome_profile_1"
            chart_url = f"https://www.tradingview.com/chart/jPAQSlZC/?symbol={ticker.upper()}"
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=True,
                    viewport={"width": 1920, "height": 1080},
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.new_page()
                page.goto(chart_url, wait_until="domcontentloaded")
                page.wait_for_selector("canvas", timeout=25000)
                time.sleep(3.0)
                scraper = TVOptionsScraper(load_wait_s=8.0)
                res = scraper.scrape_options_suite(
                    page, ticker.upper(), out_dir,
                    prediction_period=prediction_period,
                    expected_move=expected_move,
                    min_volume=min_volume,
                    moneyness=moneyness,
                    capture_volume_charts=capture_volume_charts,
                )
                strategies = res.get("strategies", [])
                if strategies:
                    try:
                        strat_file.write_text(json.dumps(strategies, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                context.close()
        except Exception as e:
            logger.error(f"Live TV options scrape failed for {ticker}: {e}")

    if not strategies:
        return f"No options strategies found for {ticker} with period='{prediction_period}' and move='{expected_move}'."

    lines = [
        f"### TRADINGVIEW STRATEGY FINDER RESULTS FOR {ticker.upper()} (Period: {prediction_period}, Move: {expected_move})",
        f"Visuals: Heatmap -> `{ticker.upper()}_options_heatmap.png`, By Expiration -> `{ticker.upper()}_options_expirations.png`",
        "| Expiry | DTE | Strategy Type | Strikes / Formula | Max Profit | Max Loss | R:R | Breakeven | Theo Price | Bid / Ask | Spread % | Exp Payoff |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in strategies[:12]:
        lines.append(
            f"| {s.get('expiration')} | {s.get('days')} | {s.get('strategy_type')} | "
            f"`{s.get('formula')}` | {s.get('max_profit_display')} | {s.get('max_loss_display')} | "
            f"**{s.get('reward_risk_display')}** | {s.get('breakeven_display')} | "
            f"${s.get('theo_price')} | ${s.get('bid')}/${s.get('ask')} | "
            f"{s.get('bid_ask_spread_pct')} | ${s.get('expected_payoff')} |"
        )
    return "\n".join(lines)
