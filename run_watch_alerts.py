"""
Watchlist Trigger & Price Alert Engine
Polls live real-time quotes against extracted research report levels,
evaluates state transitions (STALKING -> IN_ZONE -> TARGET_HIT / INVALIDATED),
emits desktop/console alerts, and syncs live status to Google Sheets.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
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
    sweep_expired_watch_targets,
    update_target_live_state,
    upsert_watch_target,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (WatchAlerts) %(message)s"
)
yf_log = logging.getLogger("yfinance")
yf_log.setLevel(logging.CRITICAL)
yf_log.propagate = False
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class SyncResult(int):
    """An int subclass representing the count of indexed targets, enriched with sync metadata."""
    def __new__(
        cls,
        count: int,
        indexed: Optional[List[str]] = None,
        rejected: Optional[List[Dict[str, Any]]] = None,
        tt_alerts_count: int = 0,
        tt_synced: Optional[List[str]] = None,
    ):
        obj = super().__new__(cls, count)
        obj.indexed = indexed or []
        obj.rejected = rejected or []
        obj.tt_alerts_count = tt_alerts_count
        obj.tt_synced = tt_synced or []
        return obj


def sync_reports_to_watchlist(
    target_date: Optional[str] = None,
    target_ticker: Optional[str] = None,
    sync_tastytrade: Optional[bool] = None,
) -> int:
    """Discover all generated research reports and index their structured levels into SQLite & Tastytrade.
    
    NOTE: Tastytrade cloud quote alert generation is strictly gated. By default, alerts are NOT generated
    after deep research unless explicitly enabled via sync_tastytrade=True or ENABLE_TASTYTRADE_ALERTS=1.
    """
    if sync_tastytrade is None:
        sync_tastytrade = os.getenv("ENABLE_TASTYTRADE_ALERTS", "0").lower() in ("1", "true", "yes")

    if target_ticker and not target_date:
        safe_sym = target_ticker.strip().upper().replace(":", "_")
        reports_base = config.BASE_DIR / "reports"
        if reports_base.exists():
            for d in sorted([p for p in reports_base.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True):
                if (d / f"{safe_sym}_summary.md").exists() or (d / f"{safe_sym}_arbitration.md").exists():
                    target_date = d.name
                    break
        if not target_date:
            raw_base = config.BASE_DIR / "data" / "raw"
            if raw_base.exists():
                for d in sorted([p for p in raw_base.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True):
                    if (d / safe_sym).exists():
                        target_date = d.name
                        break

    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    reports_dir = config.BASE_DIR / "reports" / date_str
    count = 0
    indexed_list: List[str] = []
    rejected_list: List[Dict[str, Any]] = []
    tt_synced_list: List[str] = []
    tt_alerts_total = 0

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
            # Load ticker's same-date data window: data/raw/<report date>/<T>/<T>_datawindow.json
            dw_dict = {}
            safe_sym = t.replace(":", "_")
            dw_paths = [config.BASE_DIR / "data" / "raw" / date_str / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "triage" / date_str / "_DEEP_RESEARCH" / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "triage" / date_str / "force" / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "raw" / date_str / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "raw" / date_str / f"{safe_sym}_triage.json"]
            dw_file = next((p for p in dw_paths if p.exists()), dw_paths[0])
            if dw_file.exists():
                try:
                    dw_dict = json.loads(dw_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[{t}] Error reading DW JSON {dw_file}: {e}")

            if not dw_dict:
                logger.warning(f"[{t}] No same-date Data Window found at data/raw/{date_str}/{safe_sym}/{safe_sym}_datawindow.json. Gate cannot run fully — SKIPPING upsert.")
                # Fallback to minimal dict with spot price so validate_levels can still run
                dw_dict = {"Close": data.get("current_price") or data.get("spot") or 0.0}

            from src.logic.level_validation import validate_levels
            plan = {
                **data.get("shares_plan", {}),
                "options_plan": data.get("options_plan", {}),
                "ticker": t,
                "date": date_str,
                "side": data.get("side", "LONG"),
                "setup_lane": data.get("setup_lane") or data.get("lane") or dw_dict.get("setup_lane"),
                "spot": data.get("current_price") or data.get("spot") or dw_dict.get("Close"),
                "source": data.get("source", "judge"),
                "is_judge": str(data.get("source", "judge")).lower() in ("judge", "arbitration"),
            }
            try:
                _ok, _reasons = validate_levels(plan, dw_dict, data.get("side", "LONG"), ticker=t, date_str=date_str)
            except Exception as e_gate:
                logger.warning(f"[{t}] Gate validation crashed: {e_gate} — SKIPPING upsert.")
                rejected_list.append({"ticker": t, "reasons": [f"Gate validation error: {e_gate}"]})
                continue

            if not _ok:
                data["verdict"] = "REJECTED_BY_GATE"
            verdict_str = str(data.get("verdict") or "").upper()
            source_str = str(data.get("source") or "").lower()

            if (
                not _ok
                or source_str == "fallback"
                or verdict_str in ("NO_LEVELS", "REJECTED", "REJECTED_BY_GATE")
            ):
                fail_reasons = list(_reasons)
                if source_str == "fallback":
                    fail_reasons.append("source is fallback regex")
                if verdict_str in ("NO_LEVELS", "REJECTED", "REJECTED_BY_GATE"):
                    fail_reasons.append(f"verdict is {verdict_str}")
                logger.warning(
                    f"[{t}] Plan rejected: {'; '.join(fail_reasons)} — SKIPPING upsert to watch_targets."
                )
                from src.tracking.suggestions_ledger import log_rejected_plan
                log_rejected_plan(t, date_str, plan, fail_reasons)
                rejected_list.append({"ticker": t, "reasons": fail_reasons})
                continue

            if not data.get("suggestion_id"):
                try:
                    from src.tracking.watch_manager import _get_connection, _db_lock
                    with _db_lock:
                        with _get_connection() as sconn:
                            scur = sconn.cursor()
                            srow = scur.execute(
                                "SELECT id FROM suggestions WHERE ticker = ? AND date = ? AND source = 'judge' ORDER BY id DESC LIMIT 1",
                                (t, date_str)
                            ).fetchone()
                            if srow:
                                data["suggestion_id"] = srow[0]
                            else:
                                from src.tracking.suggestions_ledger import append_suggestion
                                from src.logic.actionable_gate import gate_inputs_from_datawindow
                                sp = data.get("shares_plan", {})
                                dw_data = data.get("datawindow") or data.get("_datawindow") or {}
                                gate_in = gate_inputs_from_datawindow(dw_data)
                                atr_at_sig = data.get("atr_at_signal") or gate_in.get("atr14")
                                rr_at_sig = data.get("rr_at_market_at_signal") or gate_in.get("long_rr_at_market")
                                sig_pack = data.get("signal_pack") or gate_in.get("signal_pack")
                                fade_val = data.get("fade") or gate_in.get("fade_long")
                                ext_z_val = data.get("ext_z") or gate_in.get("ext_z_self")
                                stop_val = sp.get("tactical_stop") or gate_in.get("long_stop_loss")
                                entry_val = sp.get("entry_zone_high") or gate_in.get("price")
                                stop_width_atr = None
                                if atr_at_sig and atr_at_sig > 0 and stop_val and entry_val:
                                    stop_width_atr = round(abs(entry_val - stop_val) / atr_at_sig, 3)
                                raw_sig = data.get("pb_funnel")
                                if raw_sig is None and sig_pack is not None:
                                    try:
                                        raw_sig = int(bool(int(round(float(sig_pack))) & 32))
                                    except Exception:
                                        raw_sig = None
                                sid = append_suggestion({
                                    "ticker": t,
                                    "date": date_str,
                                    "source": "judge",
                                    "side": data.get("side", "LONG"),
                                    "entry_type": sp.get("entry_type", "LIMIT"),
                                    "entry_low": sp.get("entry_zone_low"),
                                    "entry_high": sp.get("entry_zone_high"),
                                    "breakout_level": sp.get("breakout_level"),
                                    "stop": stop_val,
                                    "target_1": sp.get("target_1"),
                                    "target_2": sp.get("target_2"),
                                    "planned_rr": sp.get("rr_ratio"),
                                    "verdict": data.get("verdict"),
                                    "gate_status": data.get("gate_status", "PASS"),
                                    "setup_lane": data.get("setup_lane") or data.get("lane"),
                                    "kind": data.get("kind", "NEW"),
                                    "atr_at_signal": atr_at_sig,
                                    "rr_at_market_at_signal": rr_at_sig,
                                    "pb_funnel": raw_sig,
                                    "signal_pack": sig_pack,
                                    "fade": fade_val,
                                    "ext_z": ext_z_val,
                                    "stop_width_atr": stop_width_atr,
                                    "notes": f"Judge directive: {data.get('verdict')}",
                                })
                                if sid and sid > 0:
                                    data["suggestion_id"] = sid
                except Exception:
                    pass

            upsert_watch_target(data)
            count += 1
            indexed_list.append(t)

            # Sync to Tastytrade cloud alerts: ONLY after deep research levels pass the validation gate
            if tasty_client and _ok and data.get("verdict") != "REJECTED_BY_GATE":
                try:
                    tt_alerts = tasty_client.sync_watch_levels(data)
                    if tt_alerts:
                        logger.info(f"[{t}] Registered {len(tt_alerts)} cloud alert(s) in Tastytrade.")
                        tt_alerts_total += len(tt_alerts)
                        tt_synced_list.append(t)
                except Exception as e_tt:
                    logger.warning(f"[{t}] Tastytrade alert sync failed: {e_tt}")
            elif tasty_client and not _ok:
                logger.info(f"[{t}] Skipped Tastytrade cloud alert registration because level validation failed.")

    return SyncResult(
        count,
        indexed=indexed_list,
        rejected=rejected_list,
        tt_alerts_count=tt_alerts_total,
        tt_synced=tt_synced_list,
    )


def evaluate_watch_cycle(sync_sheets: bool = True) -> List[Dict[str, Any]]:
    """Run one single price polling and trigger evaluation cycle across all active watch targets."""
    try:
        now_et = get_eastern_now()
        sweep_expired_watch_targets(days=5, as_of_date=now_et.strftime("%Y-%m-%d"))
    except Exception as e_swp:
        logger.debug(f"Sweep expired watch targets exception: {e_swp}")
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
                    # Invalidation requires market close: intraday wicks are testing floor
                    if not is_after_close:
                        is_testing_floor = True
                    elif "CLOSE" in inv_cond and live_price > (inv_price * 0.99):
                        is_testing_floor = True
                    else:
                        is_stop_breached = True
            elif side == "SHORT":
                if live_price >= inv_price:
                    if not is_after_close:
                        is_testing_floor = True
                    elif "CLOSE" in inv_cond and live_price < (inv_price * 1.01):
                        is_testing_floor = True
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

        # C. Target Reached Check (Differentiate IN_TRADE vs STALKING with bar verification; IN_ZONE is NOT filled)
        elif hit_t2:
            if old_status in ("IN_TRADE",):
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
            if old_status in ("IN_TRADE",):
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

        # D. Entry Zone Stalking Trigger Check (With Real-Time On-Arrival Local Evaluation)
        elif hit_in_zone:
            new_status = "IN_ZONE"
            # Trigger On-Arrival Real-Time Local Evaluation & Tactical Triage only on status transition
            if old_status != "IN_ZONE":
                try:
                    from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival
                    eval_res = evaluate_target_on_zone_arrival(ticker, live_price, t)
                    if eval_res.get("is_actionable_now"):
                        alert_fired = "ENTRY_ACTIONABLE_BUY" if side == "LONG" else "ENTRY_ACTIONABLE_SHORT"
                        msg = (
                            f"[{ticker}] 🟢 ACTIONABLE {side} ENTRY: Price ${live_price:.2f} confirmed in zone "
                            f"[${entry_low:.2f}–${entry_high:.2f}]. Stop: ${eval_res['tactical_stop']:.2f}, "
                            f"T1: ${eval_res['target_1']:.2f}, R:R: {eval_res['live_rr']:.2f}:1."
                        )
                    else:
                        alert_fired = "ENTRY_TRIGGERED"
                        msg = f"[{ticker}] Price ${live_price:.2f} in zone [${entry_low:.2f}–${entry_high:.2f}] ({eval_res.get('verdict_label')})."
                    log_trigger_alert(ticker, alert_fired, msg, live_price)
                except Exception as e_eval:
                    logger.warning(f"[{ticker}] On-arrival zone evaluation error: {e_eval}")
                    if old_status in ("STALKING", "WATCH", "INVALIDATED", "STOP_BREACHED"):
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

        # Determine if target should remain actionable or be reset
        target_actionable = None
        eval_verdict = None
        if new_status in ("TARGET_HIT", "COMPLETED"):
            target_actionable = 0
            eval_verdict = "TARGET_HIT"
        elif new_status in ("INVALIDATED", "STOP_BREACHED", "STOPPED", "MISSED_RUNAWAY", "EXPIRED", "REJECTED_BY_GATE"):
            target_actionable = 0
            eval_verdict = "STAND_ASIDE"
        elif new_status != "IN_ZONE":
            target_actionable = 0
            eval_verdict = "STALKING"
        elif alert_fired in ("ENTRY_ACTIONABLE_BUY", "ENTRY_ACTIONABLE_SHORT"):
            target_actionable = 1

        # Update SQLite DB
        update_target_live_state(
            ticker,
            live_price=live_price,
            status=new_status,
            distance_to_entry_pct=distance_pct,
            alert_type=alert_fired,
            is_actionable_now=target_actionable,
            evaluation_verdict=eval_verdict,
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
    parser.add_argument(
        "--sync-tastytrade",
        action="store_true",
        default=False,
        help="Explicitly enable Tastytrade cloud quote alert generation (default: False)",
    )
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Target date (YYYY-MM-DD)")
    parser.add_argument("--ticker", type=str, default=None, help="Target specific ticker")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    parser.add_argument("--no-sheets", action="store_true", help="Disable Google Sheets sync")

    args = parser.parse_args()

    # Index reports
    if args.sync or not get_all_watch_targets():
        should_sync_tt = args.sync_tastytrade or (
            os.getenv("ENABLE_TASTYTRADE_ALERTS", "0").lower() in ("1", "true", "yes")
        )
        indexed = sync_reports_to_watchlist(args.date, args.ticker, sync_tastytrade=should_sync_tt)
        logger.info(f"Indexed {indexed} report(s) into Watchlist DB (Tastytrade alerts: {'ENABLED' if should_sync_tt else 'DISABLED'}).")

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
