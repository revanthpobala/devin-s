"""
Watchlist Trigger & Price Alert Engine
Polls live real-time quotes against extracted research report levels,
evaluates state transitions (STALKING -> IN_ZONE -> TARGET_HIT / INVALIDATED),
emits desktop/console alerts, and syncs live status to Google Sheets.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from src import config
from src.clients.price_client import get_current_price
from src.clients.tastytrade_client import TastytradeClient
from src.logic.report_level_extractor import extract_watch_levels_from_report
from src.tracking.sheets_tracker import SheetsTracker
from src.tracking.alert_db import get_eastern_now
from src.tracking.watch_manager import (
    get_active_watch_targets,
    get_all_watch_targets,
    log_trigger_alert,
    update_target_live_state,
    upsert_watch_target,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (WatchAlerts) %(message)s"
)
logger = logging.getLogger(__name__)


def sync_reports_to_watchlist(
    target_date: Optional[str] = None, target_ticker: Optional[str] = None, sync_tastytrade: bool = True
) -> int:
    """Discover all generated research reports and index their structured levels into SQLite & Tastytrade."""
    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    reports_dir = config.BASE_DIR / "reports" / date_str
    count = 0

    if target_ticker:
        tickers = [target_ticker.strip().upper()]
    elif reports_dir.exists():
        # Find all summary or arbitration files
        tickers = set()
        for p in reports_dir.glob("*_summary.md"):
            t = p.name.replace("_summary.md", "").upper()
            tickers.add(t)
        for p in reports_dir.glob("*_arbitration.md"):
            t = p.name.replace("_arbitration.md", "").upper()
            tickers.add(t)
    else:
        tickers = set()

    tasty_client = TastytradeClient() if sync_tastytrade else None

    logger.info(f"Indexing research levels for {len(tickers)} ticker(s) on date '{date_str}'...")
    for t in sorted(tickers):
        data = extract_watch_levels_from_report(t, date_str)
        if data:
            from src.logic.level_validation import validate_levels
            _ok, _reasons = validate_levels(
                data.get("shares_plan", {}), data, data.get("side", "LONG")
            )
            if not _ok:
                logger.warning(
                    f"[{t}] Level gate FAILED: {'; '.join(_reasons)} — "
                    f"logged as REJECTED_BY_GATE but still upserting for measurement."
                )
                data["verdict"] = "REJECTED_BY_GATE"
                data["_gate_reasons"] = _reasons
            upsert_watch_target(data)
            count += 1

            # Sync to Tastytrade cloud alerts: ONLY after deep research levels pass the validation gate
            if tasty_client and _ok and data.get("verdict") != "REJECTED_BY_GATE":
                try:
                    tt_alerts = tasty_client.sync_watch_levels(data)
                    if tt_alerts:
                        logger.info(f"[{t}] Registered {len(tt_alerts)} cloud alert(s) in Tastytrade.")
                except Exception as e_tt:
                    logger.warning(f"[{t}] Tastytrade alert sync failed: {e_tt}")
            elif tasty_client and not _ok:
                logger.info(f"[{t}] Skipped Tastytrade cloud alert registration because level validation failed.")

    return count


def evaluate_watch_cycle(sync_sheets: bool = True) -> List[Dict[str, Any]]:
    """Run one single price polling and trigger evaluation cycle across all active watch targets."""
    targets = get_active_watch_targets()
    if not targets:
        logger.info("No active watch targets in SQLite DB.")
        return []

    logger.info(f"Evaluating {len(targets)} active watch target(s)...")
    updated_targets = []

    target_syms = [t["ticker"] for t in targets if t.get("ticker")]
    from src.clients.quote_router import quote_router

    # Offload watchlist stalking to Surveillance Channel (Yahoo Direct REST / Alpaca) - zero Schwab calls
    q_batch = quote_router.get_watchlist_quotes_batch(target_syms)
    prices: Dict[str, float] = {
        sym: qd.last_price for sym, qd in q_batch.items() if qd and qd.last_price > 0
    }

    for t in targets:
        ticker = t["ticker"]
        side = str(t.get("side") or "LONG").upper()
        entry_low = t.get("entry_zone_low")
        entry_high = t.get("entry_zone_high")
        breakout_level = t.get("breakout_level")
        breakout_stop = t.get("breakout_stop")
        tactical_stop = t.get("tactical_stop")
        target_1 = t.get("target_1")
        target_2 = t.get("target_2")
        inv_price = t.get("invalidation_price") or tactical_stop
        old_status = t.get("status", "STALKING")

        # 1. Fetch live quote (from parallel pre-fetch or cached spot)
        live_price = prices.get(ticker) or get_current_price(ticker)
        if not live_price or live_price <= 0:
            logger.warning(f"[{ticker}] Unable to fetch live price; using cached spot.")
            live_price = t.get("last_price") or t.get("spot_price") or 0.0

        if not live_price or live_price <= 0:
            continue

        # 2. Compute Distance to Entry Zone
        distance_pct = None
        if entry_low and entry_high:
            if side == "LONG":
                if live_price > entry_high:
                    distance_pct = round(((live_price - entry_high) / entry_high) * 100, 2)
                elif live_price < entry_low:
                    if inv_price and live_price > inv_price:
                        distance_pct = 0.0
                    else:
                        distance_pct = round(((live_price - entry_low) / entry_low) * 100, 2)
                else:
                    distance_pct = 0.0
            else:  # SHORT
                if live_price < entry_low:
                    distance_pct = round(((entry_low - live_price) / entry_low) * 100, 2)
                elif live_price > entry_high:
                    if inv_price and live_price < inv_price:
                        distance_pct = 0.0
                    else:
                        distance_pct = round(((entry_high - live_price) / entry_high) * 100, 2)
                else:
                    distance_pct = 0.0

        # 3. Evaluate State Transitions & Alerts
        new_status = "STALKING"
        alert_fired = None

        # Check conditions
        inv_cond = str(t.get("invalidation_condition") or "DAILY_CLOSE_BELOW").upper()
        is_stop_breached = False
        is_testing_floor = False
        now_et = get_eastern_now()
        is_after_close = (now_et.hour > 16) or (now_et.hour == 16 and now_et.minute >= 0)

        if inv_price and inv_price > 0:
            if side == "LONG":
                if live_price <= inv_price:
                    # Daily close defense: symmetric 1.0% band; only triggers close breach after 16:00 ET
                    if "CLOSE" in inv_cond:
                        if not is_after_close:
                            is_testing_floor = True
                        elif live_price > (inv_price * 0.99):
                            is_testing_floor = True
                        else:
                            is_stop_breached = True
                    else:
                        is_stop_breached = True
            elif side == "SHORT":
                if live_price >= inv_price:
                    if "CLOSE" in inv_cond:
                        if not is_after_close:
                            is_testing_floor = True
                        elif live_price < (inv_price * 1.01):
                            is_testing_floor = True
                        else:
                            is_stop_breached = True
                    else:
                        is_stop_breached = True

        hit_t2 = False
        if target_2 and target_2 > 0:
            if side == "LONG" and live_price >= target_2:
                hit_t2 = True
            elif side == "SHORT" and live_price <= target_2:
                hit_t2 = True

        hit_t1 = False
        if target_1 and target_1 > 0:
            if side == "LONG" and live_price >= target_1:
                hit_t1 = True
            elif side == "SHORT" and live_price <= target_1:
                hit_t1 = True

        hit_breakout = False
        if breakout_level and breakout_level > 0:
            # Breakouts require daily close above breakout_level
            if is_after_close:
                if side == "LONG" and live_price >= breakout_level:
                    hit_breakout = True
                elif side == "SHORT" and live_price <= breakout_level:
                    hit_breakout = True

        # Check Entry Zone: strictly entry_low <= live_price <= entry_high (no 1% buffer, no stop-to-zone gap)
        hit_in_zone = False
        in_proximity_zone = False
        if entry_low and entry_high and entry_low > 0 and entry_high > 0:
            if entry_low <= live_price <= entry_high:
                hit_in_zone = True

        # A. Invalidation / Hard Stop Breach Check
        if is_stop_breached:
            new_status = "INVALIDATED"
            if old_status != "INVALIDATED":
                alert_fired = "STOP_BREACHED"
                msg = f"[{ticker}] Price ${live_price:.2f} breached invalidation level (${inv_price:.2f}). Thesis dead."
                log_trigger_alert(ticker, alert_fired, msg, live_price)

        # A2. Floor Probe / Testing Support Check (intraday wicks on DAILY_CLOSE rules)
        elif is_testing_floor:
            new_status = "TESTING_SUPPORT"
            if old_status != "TESTING_SUPPORT":
                alert_fired = "TESTING_SUPPORT"
                msg = f"[{ticker}] Structural floor probe: Price ${live_price:.2f} testing support (${inv_price:.2f}). Invalidation requires Daily Close Below."
                log_trigger_alert(ticker, alert_fired, msg, live_price)

        # B. Breakout Entry Trigger Check (Instant IN_TRADE)
        elif hit_breakout and old_status in ("STALKING", "IN_ZONE"):
            new_status = "IN_TRADE"
            alert_fired = "BREAKOUT_ENTERED" if side == "LONG" else "BREAKDOWN_ENTERED"
            stop_str = f" | Tactical Stop: ${breakout_stop:.2f}" if breakout_stop else ""
            msg = f"[{ticker}] Breakout triggered! Price ${live_price:.2f} crossed breakout level (${breakout_level:.2f}). Trade active.{stop_str}"
            log_trigger_alert(ticker, alert_fired, msg, live_price)

        # C. Target Reached Check (Differentiate IN_TRADE / IN_ZONE vs STALKING with bar verification)
        elif hit_t2:
            if old_status in ("IN_TRADE", "IN_ZONE"):
                new_status = "TARGET_HIT"
                if old_status != "TARGET_HIT":
                    alert_fired = "TARGET_2_REACHED"
                    msg = f"[{ticker}] Price ${live_price:.2f} reached Target 2 (${target_2:.2f})! Full profit target met."
                    log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                # Check bar extremes before declaring runaway
                from src.tracking.execution_validator import evaluate_setup_lifecycle
                eval_res = evaluate_setup_lifecycle(
                    ticker=ticker,
                    setup_date=str(t.get("date") or ""),
                    side=side,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop_loss=inv_price,
                    target_1=target_1,
                    target_2=target_2,
                    live_price=live_price,
                    current_status=old_status,
                )
                if eval_res["was_filled"]:
                    new_status = "TARGET_HIT"
                    if old_status != "TARGET_HIT":
                        alert_fired = "TARGET_2_REACHED"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 2 (${target_2:.2f}) after filling entry (${eval_res['fill_price']:.2f})! Full profit target met."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)
                else:
                    new_status = "MISSED_RUNAWAY"
                    if old_status != "MISSED_RUNAWAY":
                        alert_fired = "MISSED_RUNAWAY_TARGET_2"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 2 (${target_2:.2f}) without entry filling! Stock ran away from stalking zone."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)

        elif hit_t1:
            if old_status in ("IN_TRADE", "IN_ZONE"):
                new_status = "TARGET_HIT"
                if old_status != "TARGET_HIT":
                    alert_fired = "TARGET_1_REACHED"
                    msg = f"[{ticker}] Price ${live_price:.2f} reached Target 1 (${target_1:.2f}). Consider trimming."
                    log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                from src.tracking.execution_validator import evaluate_setup_lifecycle
                eval_res = evaluate_setup_lifecycle(
                    ticker=ticker,
                    setup_date=str(t.get("date") or ""),
                    side=side,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop_loss=inv_price,
                    target_1=target_1,
                    target_2=target_2,
                    live_price=live_price,
                    current_status=old_status,
                )
                if eval_res["was_filled"]:
                    new_status = "TARGET_HIT"
                    if old_status != "TARGET_HIT":
                        alert_fired = "TARGET_1_REACHED"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 1 (${target_1:.2f}) after filling entry (${eval_res['fill_price']:.2f}). Consider trimming."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)
                else:
                    new_status = "MISSED_RUNAWAY"
                    if old_status != "MISSED_RUNAWAY":
                        alert_fired = "MISSED_RUNAWAY_TARGET_1"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 1 (${target_1:.2f}) without entry filling! Stock ran away from stalking zone."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)

        # D. Entry Zone Stalking Trigger Check (With 1.0% Institutional Floor Proximity Buffer)
        elif hit_in_zone:
            new_status = "IN_ZONE"
            if old_status in ("STALKING", "INVALIDATED", "STOP_BREACHED"):
                alert_fired = "ENTRY_TRIGGERED"
                prox_tag = " [Floor Proximity Buffer]" if in_proximity_zone else ""
                opt_str = f" | Play: {t.get('options_summary')}" if t.get("options_summary") else ""
                reclaim_tag = " [Support Reclaimed]" if old_status in ("INVALIDATED", "STOP_BREACHED") else ""
                msg = f"[{ticker}] Price ${live_price:.2f} entered buy zone{prox_tag}{reclaim_tag} [${entry_low:.2f} – ${entry_high:.2f}]. Order active.{opt_str}"
                log_trigger_alert(ticker, alert_fired, msg, live_price)
        else:
            if old_status in ("IN_TRADE", "IN_ZONE"):
                # Maintain active trade holding above stop loss
                new_status = "IN_TRADE"
            elif old_status == "MISSED_RUNAWAY":
                new_status = "MISSED_RUNAWAY"
            elif old_status == "INVALIDATED" and is_stop_breached:
                new_status = "INVALIDATED"
            elif old_status in ("INVALIDATED", "STOP_BREACHED") and not is_stop_breached:
                new_status = "STALKING"
                alert_fired = "SUPPORT_RECLAIMED"
                msg = f"[{ticker}] Bullish Reclaim! Price ${live_price:.2f} dipped and recovered above support level (${inv_price:.2f}). Thesis restored."
                log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                setup_date_str = str(t.get("date") or "")
                is_expired = False
                if setup_date_str:
                    try:
                        from datetime import datetime, timedelta
                        s_dt = datetime.strptime(setup_date_str[:10], "%Y-%m-%d").date()
                        t_dt = get_eastern_now().date()
                        days_diff = (t_dt - s_dt).days
                        if days_diff > 0:
                            bus_days = sum(1 for d in range(days_diff) if (s_dt + timedelta(days=d)).weekday() < 5)
                            if bus_days >= 5:
                                is_expired = True
                    except Exception:
                        pass
                new_status = "EXPIRED" if is_expired else "STALKING"

        # Update SQLite DB
        update_target_live_state(
            ticker,
            live_price=live_price,
            status=new_status,
            distance_to_entry_pct=distance_pct,
            alert_type=alert_fired,
        )

        t_copy = dict(t)
        t_copy["last_price"] = live_price
        t_copy["status"] = new_status
        t_copy["distance_to_entry_pct"] = distance_pct
        if alert_fired:
            t_copy["last_alert_type"] = alert_fired
        updated_targets.append(t_copy)

        status_icon = (
            "🎯" if new_status == "IN_ZONE"
            else "📈" if new_status == "IN_TRADE"
            else "⏳" if new_status == "STALKING"
            else "🏃" if new_status == "MISSED_RUNAWAY"
            else "🏁" if new_status == "TARGET_HIT"
            else "🛑"
        )
        dist_str = f"{distance_pct:+.2f}%" if distance_pct is not None else "N/A"
        logger.info(
            f"[{ticker}] ({side}) Spot: ${live_price:.2f} | Dist: {dist_str} | Status: {status_icon} {new_status}"
        )

    # 4. Mirror to Google Sheets WATCH-TRIGGERS tab
    if sync_sheets and updated_targets:
        all_targets = get_all_watch_targets()
        try:
            tracker = SheetsTracker()
            tracker.sync_watch_targets_to_sheet(all_targets)
        except Exception as e:
            logger.warning(f"Google Sheets sync failed: {e}")

    return updated_targets


def run_watch_loop(poll_interval: int = 60, sync_sheets: bool = True):
    """Continuous background polling loop."""
    logger.info(f"Starting Watchlist Alert Daemon (Polling interval: {poll_interval}s)...")
    try:
        while True:
            try:
                evaluate_watch_cycle(sync_sheets=sync_sheets)
            except Exception as e:
                logger.error(f"Error in watch cycle: {e}", exc_info=True)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Watchlist Alert Daemon stopped by user.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watchlist Trigger & Price Alert Engine")
    parser.add_argument("--sync", action="store_true", help="Sync/index research reports into Watchlist DB")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Target date (YYYY-MM-DD)")
    parser.add_argument("--ticker", type=str, default=None, help="Target specific ticker")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    parser.add_argument("--no-sheets", action="store_true", help="Disable Google Sheets sync")

    args = parser.parse_args()

    # Index reports
    if args.sync or not get_all_watch_targets():
        indexed = sync_reports_to_watchlist(args.date, args.ticker)
        logger.info(f"Indexed {indexed} report(s) into Watchlist DB.")

    if args.loop:
        run_watch_loop(poll_interval=args.interval, sync_sheets=not args.no_sheets)
    else:
        results = evaluate_watch_cycle(sync_sheets=not args.no_sheets)
        print("\n" + "=" * 60)
        print(f"WATCHLIST EVALUATION COMPLETE ({len(results)} targets evaluated)")
        print("=" * 60)
        for r in results:
            dist = f"{r.get('distance_to_entry_pct'):+.2f}%" if r.get('distance_to_entry_pct') is not None else "-"
            print(
                f"[{r.get('ticker')}] Spot: ${r.get('last_price', 0):.2f} | "
                f"Status: {r.get('status')} | "
                f"Dist to Entry: {dist} | "
                f"Zone: ${r.get('entry_zone_low', 0):.2f}-${r.get('entry_zone_high', 0):.2f} | "
                f"Stop: ${r.get('tactical_stop', 0):.2f}"
            )
