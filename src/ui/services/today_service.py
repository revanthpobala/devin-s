"""
Multi-Threaded Today Desk Background Service & High-Performance Cache Engine.
Runs continuous background synthesis across market pulse, regime, deep opportunities,
coverage, missed moves, morning briefing, and feedback loop so UI GET requests return instantly.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.tracking.alert_db import get_eastern_date_str
from src.tracking.db_locks import get_readonly_connection
from src.tracking import rr_config
from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
from src.logic.setup_lane_map import inbox_row_mapping
from src.tracking.ops_alerts import CORE_SYMBOLS, core_coverage_gaps, is_test_ticker
from src.logic.data_window_filter import LANE_TO_SETUP_LANE, lane_prior

logger = logging.getLogger("today_service")

# Thresholds
STOP_ATR_NOISE_FLOOR = rr_config.STOP_ATR_MIN
_SETUP_LANE_TO_REASON = {v: k for k, v in LANE_TO_SETUP_LANE.items()}
_RR_LANES = ("RR_SETUP", "RR_SETUP_STRONG")

# Global In-Memory Cache Store
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {
    "today": None,
    "today_ts": 0.0,
    "coverage": None,
    "coverage_ts": 0.0,
    "missed_moves": None,
    "missed_moves_ts": 0.0,
    "morning_briefing": None,
    "morning_briefing_ts": 0.0,
    "feedback_loop": None,
    "feedback_loop_ts": 0.0,
}

# Per-payload refresh cadence (seconds). today / coverage / missed-moves refresh
# every 15s cycle; the briefing and the feedback loop read a day-scoped file and a
# position service that barely move, so they are loaded lazily on their own clocks
# instead of being rebuilt on every poll.
_PAYLOAD_TTL_S: Dict[str, float] = {
    "today": 15.0,
    "coverage": 15.0,
    "missed_moves": 120.0,
    "morning_briefing": 300.0,
    "feedback_loop": 120.0,
}

# Protective build timeouts per payload (a hung build never stalls the cycle).
_PAYLOAD_TIMEOUT_S: Dict[str, float] = {
    "today": 75.0,
    "coverage": 30.0,
    "missed_moves": 45.0,
    "morning_briefing": 30.0,
    "feedback_loop": 25.0,
}

# One build lock per payload: a build in flight is waited on, never duplicated.
# The measured 120s regression was the same payload being built in a request thread
# AND in the background cycle at once, each waiting on the other's DB connection.
_PAYLOAD_LOCKS: Dict[str, threading.Lock] = {name: threading.Lock() for name in _PAYLOAD_TTL_S}

# How long a cold GET waits for an in-flight background build before it builds itself.
_COLD_GET_WAIT_S = 20.0

_WORKER_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()
_TRIGGER_EVENT = threading.Event()
_IS_UPDATING = False
_FORCE_NEXT_CYCLE = False


def _normalized_local_score(raw: Any) -> Any:
    if raw is None or raw == "":
        return ""
    s = str(raw).strip()
    if "/" in s:
        try:
            return float(s.split("/", 1)[0].strip())
        except ValueError:
            return ""
    try:
        v = float(s)
    except ValueError:
        return ""
    return round(v / 10.0, 1)


def _live_lane_tier(setup_lane: Optional[str], rr_at_market: Optional[float]) -> Optional[str]:
    if setup_lane not in _RR_LANES or rr_at_market is None:
        return setup_lane
    if rr_at_market >= rr_config.hi_rr():
        return "RR_SETUP_STRONG"
    if rr_at_market >= rr_config.min_rr():
        return "RR_SETUP"
    return None


def _lane_priors(setup_lane: Optional[str], pb_funnel: Any) -> Tuple[Optional[float], Optional[float]]:
    return lane_prior(_SETUP_LANE_TO_REASON.get(setup_lane), pb_funnel) or (None, None)


_DEAD_STATUSES = frozenset({"TARGET_HIT", "EXPIRED", "INVALIDATED"})


def _pb_bucket(pb_funnel: Any) -> str:
    if pb_funnel is None:
        return "pre-PB"
    return "PB" if bool(pb_funnel) else "no PB"


def _has_deep_report(ticker: str, date_str: str) -> bool:
    ticker = ticker.upper()
    base = config.BASE_DIR / "reports" / date_str
    if not base.exists():
        return False
    return (base / f"{ticker}_summary.md").exists() or (base / f"{ticker}_arbitration.md").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 1. CORE SYNTHESIS WORKERS (RUN IN MULTI-THREADED BACKGROUND POOL)
# ─────────────────────────────────────────────────────────────────────────────

_BUILD_TIMINGS: Dict[str, float] = {}


_LAST_GOOD: Dict[str, Any] = {}


def _timed_call(label: str, fn, *args):
    t0 = time.time()
    try:
        return fn(*args)
    finally:
        _BUILD_TIMINGS[label] = round(time.time() - t0, 2)


def _build_today_payload_sync() -> Dict[str, Any]:
    """Execute high-speed read-only WAL queries and enrich Today desk."""
    _t_build = time.time()
    today_str = get_eastern_date_str()
    alerts_db_path = config.alerts_db_path()
    watch_db_path = config.research_watch_db_path()

    found_rows = []
    alert_rows = []
    suggestion_rows = []
    job_rows = []

    # 1. Read Alerts DB via dedicated Read-Only WAL connection (timeout 2s, NO _db_lock)
    try:
        with get_readonly_connection(alerts_db_path, timeout=2.0) as alert_conn:
            ac = alert_conn.cursor()
            ac.execute("""
                SELECT setup, GROUP_CONCAT(symbol) as tickers_str, COUNT(*) as cnt, source, status, reason
                FROM research_queue
                WHERE date = ?
                GROUP BY setup
                ORDER BY id ASC
            """, (today_str,))
            found_rows = [dict(r) for r in ac.fetchall()]

            ac.execute("""
                SELECT LOWER(symbol) as sym, UPPER(symbol) as ticker, date, llm_decision, llm_playbook, setup, alert_price, market_price, score
                FROM alerts
                WHERE date = ?
                  AND llm_decision IS NOT NULL
                  AND llm_decision != ''
                ORDER BY rowid ASC
            """, (today_str,))
            alert_rows = [dict(r) for r in ac.fetchall()]
    except Exception as e:
        logger.warning(f"[_build_today_payload] Read-only alerts query exception: {e}")

    found = []
    for r in found_rows:
        tickers_str = r.pop("tickers_str", "") or ""
        r["tickers"] = [t.strip().upper() for t in tickers_str.split(",") if t.strip()]
        setup_name = r.get("setup") or ""
        if r.get("reason") == f"Screener candidate setup: {setup_name}":
            r.pop("reason", None)
        found.append(r)

    # 2. Read Watch DB via dedicated Read-Only WAL connection (timeout 2s, NO _db_lock)
    try:
        with get_readonly_connection(watch_db_path, timeout=2.0) as watch_conn:
            cursor = watch_conn.cursor()
            cursor.execute("""
                WITH ranked_suggestions AS (
                    SELECT
                        s.*,
                        w.status,
                        w.distance_to_entry_pct as dist,
                        w.last_price,
                        w.action_long as w_action_long,
                        w.signal_pack as w_signal_pack,
                        w.fade as w_fade,
                        w.ext_z as w_ext_z,
                        w.atr_at_signal as w_atr_at_signal,
                        w.rr_at_market_at_signal as w_rr_at_market_at_signal,
                        ROW_NUMBER() OVER (
                            PARTITION BY LOWER(s.ticker)
                            ORDER BY s.id DESC
                        ) as rn
                    FROM suggestions s
                    LEFT JOIN watch_targets w
                        ON LOWER(s.ticker) = LOWER(w.ticker)
                    WHERE s.gate_status = 'PASS'
                      AND s.date >= date('now', '-21 days')
                )
                SELECT * FROM ranked_suggestions
                WHERE rn = 1
                ORDER BY
                    CASE
                        WHEN status = 'IN_TRADE' THEN 1
                        WHEN status = 'IN_ZONE' THEN 2
                        WHEN dist IS NOT NULL AND ABS(dist) <= 1.5 THEN 3
                        ELSE 4
                    END,
                    (last_price - stop) / NULLIF(entry_high - stop, 0) DESC
            """)
            suggestion_rows = [dict(r) for r in cursor.fetchall()]

            cursor.execute("""
                SELECT LOWER(ticker) as sym, status as job_status, stage, stage_detail
                FROM active_research_jobs
                WHERE status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')
                  AND target_date = ?
            """, (today_str,))
            job_rows = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.warning(f"[_build_today_payload] Read-only watch query exception: {e}")

    _BUILD_TIMINGS["db_reads"] = round(time.time() - _t_build, 2)

    def _decision_rank(dec: str) -> int:
        d = (dec or "").upper()
        if "PASS" in d:
            return 3
        if "WATCH" in d:
            return 2
        if "CUT" in d:
            return 1
        return 0

    alert_map: Dict[str, Any] = {}
    for ar in alert_rows:
        sym = ar.get("sym")
        if not sym:
            continue
        if sym not in alert_map:
            alert_map[sym] = ar
        else:
            existing_rank = _decision_rank(alert_map[sym].get("llm_decision"))
            new_rank = _decision_rank(ar.get("llm_decision"))
            if new_rank > existing_rank:
                alert_map[sym] = ar
            elif new_rank == existing_rank and ar.get("score") and not alert_map[sym].get("score"):
                alert_map[sym] = ar

    job_map: Dict[str, Any] = {}
    for jr in job_rows:
        sym = jr.get("sym")
        if sym:
            job_map[sym] = jr

    actionable = []
    stalking = []
    watch_list = []
    done_list = []
    cut_list = []
    needs_you = []
    unmeasured = []
    closest_to_gate = []
    missing_symbols = set()
    processed_syms = set()

    triaged_syms = set(alert_map.keys())
    all_sugg_syms = set((r.get("ticker") or "").lower() for r in suggestion_rows)
    for sym in all_sugg_syms:
        if sym not in triaged_syms:
            missing_symbols.add(sym.upper())

    min_rr_floor = rr_config.min_rr()

    for r in suggestion_rows:
        ticker = r.get("ticker", "")
        sym = (ticker or "").lower()
        processed_syms.add(sym)

        local = alert_map.get(sym, {})
        r["date"] = (local.get("date") if local else None) or today_str
        r["llm_decision"] = local.get("llm_decision")
        r["llm_playbook"] = (local.get("llm_playbook") or "")[:120]
        r["local_score"] = _normalized_local_score(local.get("score"))

        job = job_map.get(sym)
        has_real_deep = _has_deep_report(sym, today_str)
        local_dec_raw = (local.get("llm_decision") or "").upper()
        if job:
            job_st = job.get("job_status", "none")
            job_stage = job.get("stage", "none")
            if job_st == "COMPLETED" and (job_stage == "LOCAL_DONE" or not has_real_deep):
                if "CUT" in local_dec_raw or "SKIP" in local_dec_raw:
                    r["deep_status"] = "CUT_BY_LOCAL"
                elif "WATCH" in local_dec_raw:
                    r["deep_status"] = "WATCH_NO_DEEP"
                else:
                    r["deep_status"] = "DEEP_CAP_DEFERRED"
            else:
                r["deep_status"] = job_st
            r["deep_stage"] = job_stage
            r["deep_stage_detail"] = job.get("stage_detail", "")
        else:
            if has_real_deep:
                r["deep_status"] = "COMPLETED"
                r["deep_stage"] = "DONE"
            elif "CUT" in local_dec_raw or "SKIP" in local_dec_raw:
                r["deep_status"] = "CUT_BY_LOCAL"
                r["deep_stage"] = "LOCAL_DONE"
            elif "WATCH" in local_dec_raw:
                r["deep_status"] = "WATCH_NO_DEEP"
                r["deep_stage"] = "LOCAL_DONE"
            elif "PASS" in local_dec_raw:
                r["deep_status"] = "AWAITING_QUEUE"
                r["deep_stage"] = "LOCAL_DONE"
            else:
                r["deep_status"] = "none"
                r["deep_stage"] = "none"
            r["deep_stage_detail"] = ""

        entry_h = r.get("entry_high") or 0.0
        entry_l = r.get("entry_low") or 0.0
        stop = r.get("stop") or 0.0
        target = r.get("target_1") or 0.0
        last_px = r.get("last_price") or 0.0

        room_to_stop = 0.0
        if last_px and entry_h and entry_h > stop:
            room_to_stop = (last_px - stop) / (entry_h - stop)
        r["room_to_stop"] = round(room_to_stop, 3)

        atr_at_signal = r.get("atr_at_signal") if r.get("atr_at_signal") is not None else r.get("w_atr_at_signal")
        if atr_at_signal and atr_at_signal > 0 and entry_h and stop:
            r["stop_width_atr"] = round(abs(entry_h - stop) / atr_at_signal, 2)
        else:
            r["stop_width_atr"] = None

        r["setup_lane"] = r.get("setup_lane") or None
        r["pb_bucket"] = _pb_bucket(r.get("pb_funnel"))

        rr_at_signal = r.get("rr_at_market_at_signal") if r.get("rr_at_market_at_signal") is not None else r.get("w_rr_at_market_at_signal")
        try:
            rr_at_signal = float(rr_at_signal) if rr_at_signal else None
        except (TypeError, ValueError):
            rr_at_signal = None

        if rr_at_signal is not None:
            rr_at_market, rr_source = rr_at_signal, "signal"
        elif last_px and stop and last_px > stop and target and target > last_px:
            rr_at_market = round((target - last_px) / (last_px - stop), 2)
            rr_source = "live"
        else:
            rr_at_market, rr_source = None, None

        r["rr_at_market_at_signal"] = rr_at_signal
        r["rr_at_market"] = rr_at_market
        r["rr_at_market_source"] = rr_source

        rr_zone_at_entry_high = None
        if entry_h and stop and entry_h > stop and target and target > entry_h:
            rr_zone_at_entry_high = round((target - entry_h) / (entry_h - stop), 2)
        r["rr_zone_at_entry_high"] = rr_zone_at_entry_high

        breakout_lvl = 0.0
        rr_breakout_at_trigger = None
        breakout_trigger_broken = False
        try:
            breakout_lvl = float(r.get("breakout_level") or 0.0)
        except (TypeError, ValueError):
            breakout_lvl = 0.0
        bstop = 0.0
        try:
            bstop = float(r.get("breakout_stop") or stop or 0.0)
        except (TypeError, ValueError):
            bstop = 0.0
        try:
            target_2 = float(r.get("target_2") or 0.0)
        except (TypeError, ValueError):
            target_2 = 0.0
        # The breakout path is judged on the nearest researched target that sits above its trigger.
        breakout_target = next((x for x in (target, target_2) if x and x > breakout_lvl), 0.0)
        r["breakout_target_used"] = "T1" if breakout_target and breakout_target == target else ("T2" if breakout_target else None)
        if breakout_lvl > 0:
            if bstop and breakout_lvl > bstop and breakout_target:
                rr_breakout_at_trigger = round((breakout_target - breakout_lvl) / (breakout_lvl - bstop), 2)
            else:
                breakout_trigger_broken = True
        r["breakout_level"] = breakout_lvl
        r["rr_breakout_at_trigger"] = rr_breakout_at_trigger
        r["breakout_trigger_broken"] = breakout_trigger_broken

        _path_bits = []
        if rr_zone_at_entry_high is not None:
            _path_bits.append(f"zone R:R {rr_zone_at_entry_high:.2f} @ ${entry_h:.2f}")
        if rr_breakout_at_trigger is not None:
            _path_bits.append(f"breakout R:R {rr_breakout_at_trigger:.2f} @ ${breakout_lvl:.2f} (to {r['breakout_target_used']})")
        elif breakout_trigger_broken:
            _path_bits.append("breakout needs a target above its trigger: re-run research")
        r["rr_paths_text"] = " | ".join(_path_bits)

        status = r.get("status") or "STALKING"
        dist = r.get("dist")
        near = dist is not None and abs(float(dist)) <= 1.5

        in_zone_flag = 1.0 if (r.get("in_zone") or status in ("IN_ZONE", "ENTER") or near) else 0.0
        entry_px = r.get("entry_high") or last_px or r.get("entry_low")

        def _first(*vals):
            return next((v for v in vals if v is not None), None)

        # Unknown stays None so the gate names the missing field instead of passing on a made-up value.
        pb_known = r.get("pb_funnel")
        sig_pack_val = _first(r.get("signal_pack"), r.get("w_signal_pack"), None if pb_known is None else (36.0 if pb_known else 4.0))
        fade_gate = r.get("fade_gate")
        fade_val = _first(r.get("fade"), r.get("w_fade"), None if fade_gate is None else (1.0 if fade_gate else 0.0))
        action_long_val = _first(r.get("action_long"), r.get("w_action_long"), r.get("action_code"))
        ext_z_val = _first(r.get("ext_z"), r.get("w_ext_z"))
        atr_val = float(atr_at_signal) if (atr_at_signal and float(atr_at_signal) > 0) else ((abs(entry_h - stop)) if (entry_h and stop and entry_h > stop) else 1.0)

        dw_dict = {
            "long_in_zone": in_zone_flag,
            "long_rr_at_market": rr_at_market,
            "long_stop_loss": stop,
            "atr14": atr_val,
            "price": last_px or r.get("price") or entry_px,
            "signal_pack": sig_pack_val,
            "fade_long": fade_val,
            "action_long": action_long_val,
            "ext_z_self": ext_z_val,
        }
        gate_in = gate_inputs_from_datawindow(dw_dict)
        is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": entry_px})
        r["measured"] = is_act
        r["measured_actionable"] = is_act
        r["gate_reasons"] = gate_fails

        r["lane"] = _live_lane_tier(r["setup_lane"], rr_at_market)
        r["lane_persisted"] = r["setup_lane"]
        prior_win, prior_ev = _lane_priors(r["lane"], r.get("pb_funnel"))
        r["lane_prior_win"] = prior_win
        r["lane_prior_ev"] = prior_ev
        r["stop_tight"] = bool(r["stop_width_atr"] is not None and r["stop_width_atr"] < STOP_ATR_NOISE_FLOOR)
        r["sort_rr"] = -1.0 if r["stop_tight"] else (rr_at_market or -1.0)

        # Wait-for limit price
        r["limit_price"] = None
        r["wait_for"] = None
        is_geo_valid = (stop and target and target > stop and (entry_h == 0.0 or entry_h > stop) and (entry_l == 0.0 or entry_l > stop))
        is_terminal_row = (last_px and stop and last_px <= stop) or (last_px and target and last_px >= target)
        if not is_terminal_row and is_geo_valid and (rr_at_market is None or rr_at_market < min_rr_floor):
            calc_limit = round((target + min_rr_floor * stop) / (1.0 + min_rr_floor), 2)
            atr_chk = float(atr_at_signal) if (atr_at_signal and float(atr_at_signal) > 0) else 1.0
            if calc_limit > stop and (not last_px or calc_limit < last_px) and (calc_limit - stop) / max(0.01, atr_chk) >= 0.7:
                r["limit_price"] = calc_limit
                r["wait_for"] = f"wait for ${calc_limit:.2f}"

        if not is_act and gate_fails:
            dist_val = abs(float(dist)) if dist is not None else 999.0
            is_close = False
            missing_cond = None
            if len(gate_fails) == 1:
                is_close = True
                missing_cond = gate_fails[0]
            elif len(gate_fails) == 2 and "not PB funnel" in gate_fails:
                other_fail = [f for f in gate_fails if f != "not PB funnel"][0]
                if other_fail == "not in the long zone":
                    if dist_val <= 3.0 or near or in_zone_flag == 1.0:
                        is_close = True
                        missing_cond = f"Entry Zone ({dist:+.1f}%)" if dist is not None else "Entry Zone"
                else:
                    is_close = True
                    missing_cond = other_fail

            if is_close:
                missing_cond_display = missing_cond
                if missing_cond == "not PB funnel":
                    missing_cond_display = "Pullback confirmation"
                elif "RR@mkt" in str(missing_cond):
                    missing_cond_display = f"R:R < {min_rr_floor:.1f}"
                elif "stop width" in str(missing_cond):
                    missing_cond_display = "Stop width < 0.7 ATR"
                elif "not in the long zone" in str(missing_cond):
                    missing_cond_display = f"Entry Zone ({dist:+.1f}%)" if dist is not None else "Entry Zone"

                r_close = dict(r)
                r_close["missing_condition"] = missing_cond_display
                r_close["raw_missing_condition"] = missing_cond
                r_close["dist"] = dist
                r_close["rr_at_market"] = rr_at_market
                closest_to_gate.append(r_close)

        live_rr = None
        flag = None
        if last_px and last_px > stop and target and target > last_px:
            live_rr = round((target - last_px) / (last_px - stop), 2)
        if last_px and last_px <= stop:
            live_rr = None
            flag = "BELOW_STOP"
        elif room_to_stop < 0.25:
            live_rr = None
            flag = "AT_STOP"
        r["live_rr"] = live_rr
        r["live_rr_flag"] = flag

        if not r.get("llm_decision"):
            continue

        local_dec = (local.get("llm_decision") or "").upper()
        deep_status = r.get("deep_status") or "none"
        r["setup"] = local.get("setup") or r.get("setup") or ""
        r["llm_playbook"] = local.get("llm_playbook") or r.get("llm_playbook") or ""

        if "PASS" in local_dec:
            if not r["measured"]:
                r["unmeasured_reason"] = "; ".join(gate_fails) if gate_fails else "below_bar"
                unmeasured.append(r)
            elif has_real_deep and deep_status == "COMPLETED":
                if status in ("IN_ZONE", "IN_TRADE") or near:
                    actionable.append(r)
                else:
                    stalking.append(r)
            elif is_act:
                needs_you.append(r)
            else:
                r["unmeasured_reason"] = "; ".join(gate_fails) if gate_fails else "below_bar"
                unmeasured.append(r)
        elif "WATCH" in local_dec:
            if str(r.get("status") or "").upper() in _DEAD_STATUSES:
                done_list.append(r)
            else:
                watch_list.append(r)
        elif "CUT" in local_dec:
            cut_list.append(r)

    # Inbox construction
    inbox = []
    for sym, local in alert_map.items():
        job = job_map.get(sym, {})
        s_match = next((s for s in suggestion_rows if ((s["ticker"] or "").lower() == sym)), None)
        score_match = re.search(r"\((\d+(?:/\d+)?)\)", local.get("llm_decision") or "")
        score_val = _normalized_local_score(
            score_match.group(1) if score_match else local.get("score")
        )
        j_st = job.get("job_status", "none")
        j_stg = job.get("stage", "none")
        has_real_deep = _has_deep_report(sym, today_str)
        if j_st == "COMPLETED" and (j_stg == "LOCAL_DONE" or not has_real_deep):
            if "CUT" in (local.get("llm_decision") or "").upper():
                ib_deep_status = "CUT_BY_LOCAL"
            elif "WATCH" in (local.get("llm_decision") or "").upper():
                ib_deep_status = "WATCH_NO_DEEP"
            else:
                ib_deep_status = "DEEP_CAP_DEFERRED"
        elif has_real_deep and j_st == "none":
            ib_deep_status = "COMPLETED"
        elif j_st == "none":
            if "CUT" in (local.get("llm_decision") or "").upper():
                ib_deep_status = "CUT_BY_LOCAL"
            elif "WATCH" in (local.get("llm_decision") or "").upper():
                ib_deep_status = "WATCH_NO_DEEP"
            elif "PASS" in (local.get("llm_decision") or "").upper():
                ib_deep_status = "AWAITING_QUEUE"
            else:
                ib_deep_status = "none"
        else:
            ib_deep_status = j_st

        setup_name = (s_match["setup_lane"] if s_match and s_match["setup_lane"] else None) or local.get("setup") or ""
        lane_map = inbox_row_mapping(setup_name)
        inbox.append({
            "ticker": local.get("ticker") or sym.upper(),
            "date": local.get("date") or today_str,
            "setup": setup_name,
            "lane": lane_map.get("lane"),
            "setup_tag": lane_map.get("tag"),
            "setup_tag_label": lane_map.get("label"),
            "setup_prior_win": lane_map.get("prior_win"),
            "setup_prior_ev": lane_map.get("prior_ev"),
            "measured": lane_map.get("tag") == "measured",
            "llm_decision": local.get("llm_decision") or "",
            "local_score": score_val,
            "llm_playbook": (local.get("llm_playbook") or "")[:120],
            "deep_status": ib_deep_status,
            "gate_status": s_match["gate_status"] if s_match else None,
            "suggestion_id": s_match["id"] if s_match else None,
            "last_price": (s_match["last_price"] if s_match else None) or local.get("alert_price") or local.get("market_price"),
        })

    _no_px = [r["ticker"] for r in inbox if not r.get("last_price") and r.get("ticker")]
    if _no_px:
        try:
            from src.clients.price_client import get_current_prices_batch
            _live = get_current_prices_batch(sorted(set(_no_px)), context="surveillance") or {}
            for r in inbox:
                if not r.get("last_price"):
                    px = _live.get(r.get("ticker"))
                    if px:
                        r["last_price"] = px
        except Exception as e_px:
            logger.warning(f"[today] inbox price fill failed: {type(e_px).__name__} {e_px}")

    inbox_measured = [r for r in inbox if r.get("measured")]
    inbox_unmeasured = [r for r in inbox if not r.get("measured")]

    actionable.sort(key=lambda x: (
        1 if x.get("status") == "IN_TRADE" else 2 if x.get("status") == "IN_ZONE" else 3,
        -(x.get("sort_rr") if x.get("sort_rr") is not None else -9999.0),
    ))
    unmeasured.sort(key=lambda x: (x.get("ticker") or ""))

    def _sort_closest_key(x):
        in_z = 1 if (x.get("in_zone") or (x.get("dist") is not None and abs(float(x.get("dist"))) <= 1.5)) else 0
        d = abs(float(x.get("dist"))) if x.get("dist") is not None else 999.0
        rr = float(x.get("rr_at_market") or 0.0)
        return (-in_z, d, -rr)

    closest_to_gate.sort(key=_sort_closest_key)

    missing_all = sorted(missing_symbols)
    missing_test = sorted(s for s in missing_all if is_test_ticker(s))
    missing_real = [s for s in missing_all if not is_test_ticker(s)]

    _BUILD_TIMINGS["row_loop_end"] = round(time.time() - _t_build, 2)
    core_probe_error = None
    try:
        core_gaps, core_covered = _timed_call("core_coverage_gaps", core_coverage_gaps)
    except Exception as exc:
        core_gaps, core_covered = [], list(CORE_SYMBOLS)
        core_probe_error = str(exc)[:200]

    # Synthesize dynamic deep research opportunities & actionable streams
    from src.ui.services.opportunity_service import (
        get_live_market_pulse,
        collect_active_deep_research_opportunities,
        get_actionable_alerts_stream,
        get_market_regime,
    )
    from src.ui.services.action_engine import build_action_panel
    from src.ui.services.feedback_service import get_feedback_loop_data

    # Multi-threaded sub-task pool for external fetches (bounded by shared time budget)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    try:
        futs = (
            ("market_pulse", ex.submit(_timed_call, "market_pulse", get_live_market_pulse), 12.0, {}),
            ("market_regime", ex.submit(_timed_call, "market_regime", get_market_regime), 25.0, {}),
            ("deep_research_opps", ex.submit(_timed_call, "deep_research_opps", collect_active_deep_research_opportunities, 3), 20.0, []),
        )
        got = {}
        subtasks_deadline = time.time() + 35.0
        for label, fut, max_wait, empty in futs:
            rem = max(0.1, subtasks_deadline - time.time())
            wait_time = min(max_wait, rem)
            try:
                got[label] = _LAST_GOOD[label] = fut.result(timeout=wait_time)
            except (concurrent.futures.TimeoutError, TimeoutError):
                logger.warning(f"[today] {label} exceeded time budget ({wait_time:.1f}s); serving last good value")
                got[label] = _LAST_GOOD.get(label, empty)
            except Exception as e_sub:
                logger.warning(f"[today] {label} failed: {e_sub}; serving last good value")
                got[label] = _LAST_GOOD.get(label, empty)
    finally:
        ex.shutdown(wait=False)
    market_pulse = got.get("market_pulse") or _LAST_GOOD.get("market_pulse", {})
    market_regime = got.get("market_regime") or _LAST_GOOD.get("market_regime", {})
    top_opportunities = got.get("deep_research_opps") or _LAST_GOOD.get("deep_research_opps", [])

    actionable_alerts = _timed_call("actionable_alerts", lambda: get_actionable_alerts_stream(limit=30))
    feedback_data = _timed_call("feedback_loop", get_feedback_loop_data)
    action_panel = _timed_call("action_panel", lambda: build_action_panel(
        opportunities=top_opportunities,
        regime=market_regime,
        open_positions=feedback_data.get("open_positions") or [],
    ))
    _BUILD_TIMINGS["total"] = round(time.time() - _t_build, 2)

    return {
        "build_timings": dict(_BUILD_TIMINGS),
        "market_pulse": market_pulse,
        "market_regime": market_regime,
        "action_panel": action_panel,
        "top_opportunities": top_opportunities,
        "actionable_alerts": actionable_alerts,
        "feedback_loop": feedback_data,
        "found": found,
        "inbox": inbox,
        "measured_count": len(actionable) + len(stalking) + len(needs_you),
        "inbox_measured_count": len(inbox_measured),
        "inbox_unmeasured_count": len(inbox_unmeasured),
        "actionable": actionable,
        "stalking": stalking,
        "watch": watch_list,
        "done": done_list,
        "cut": cut_list,
        "needs_you": needs_you,
        "unmeasured": unmeasured,
        "closest_to_gate": closest_to_gate,
        "missing_symbols": missing_real,
        "missing_symbols_excluded_test_rows": missing_test,
        "missing_symbols_core_gaps": core_gaps,
        "core_symbols_covered": core_covered,
        "core_coverage_error": core_probe_error,
        "rr_gates": rr_config.as_ui_payload(),
        "below_bar_count": sum(1 for r in unmeasured if r.get("unmeasured_reason") == "below_bar"),
        "funnel": {
            "alerts": len(alert_map),
            "local_done": sum(1 for a in alert_map.values() if a.get("llm_decision")),
            "local_pass": sum(1 for a in alert_map.values() if "PASS" in (a.get("llm_decision") or "").upper()),
            "measured": len(actionable) + len(stalking) + len(needs_you),
            "deep_queued": sum(1 for j in job_map.values() if j.get("job_status") in ("QUEUED", "RUNNING")),
            "deep_done": sum(1 for sym in alert_map.keys() if _has_deep_report(sym, today_str)),
            "actionable": len(actionable),
            "screener_actionable": sum(1 for o in top_opportunities if o.get("is_actionable")),
        },
        "coverage": {
            "found_count": len(found_rows) if found_rows else 0,
            "inbox_count": len(inbox),
            "actionable_count": len(actionable),
            "stalking_count": len(stalking),
            "watch_count": len(watch_list),
            "done_count": len(done_list),
            "cut_count": len(cut_list),
            "needs_you_count": len(needs_you),
            "unmeasured_count": len(unmeasured),
            "closest_to_gate_count": len(closest_to_gate),
            "measured_count": len(actionable) + len(stalking) + len(needs_you),
            "missing_symbols_count": len(missing_real),
            "missing_symbols_test_rows_excluded": len(missing_test),
            "missing_symbols_core_gaps": len(core_gaps),
            "core_symbols_covered": len(core_covered),
        },
    }


def _build_coverage_payload_sync() -> Dict[str, Any]:
    """Calculate desk coverage stats via read-only WAL connection without locks."""
    today_str = get_eastern_date_str()
    alerts_db_path = config.alerts_db_path()
    watch_db_path = config.research_watch_db_path()

    alerts_count = 0
    local_done_count = 0
    local_pass_symbols = set()
    missing_alert_syms = set()
    queue_syms = set()
    evaluated_syms = set()

    try:
        with get_readonly_connection(alerts_db_path, timeout=2.0) as alert_conn:
            ac = alert_conn.cursor()
            ac.execute("SELECT COUNT(*) as total FROM alerts WHERE date = ?", (today_str,))
            r = ac.fetchone()
            alerts_count = r["total"] if r else 0

            ac.execute("""
                SELECT COUNT(*) as done FROM alerts
                WHERE date = ? AND llm_decision IS NOT NULL AND llm_decision != ''
            """, (today_str,))
            r = ac.fetchone()
            local_done_count = r["done"] if r else 0

            ac.execute("""
                SELECT DISTINCT UPPER(symbol) as sym FROM alerts
                WHERE date = ? AND (llm_decision LIKE '%PASS%')
            """, (today_str,))
            local_pass_symbols = set(r["sym"] for r in ac.fetchall() if r["sym"])

            ac.execute("""
                SELECT DISTINCT UPPER(symbol) as sym FROM alerts
                WHERE date = ? AND (llm_decision IS NULL OR llm_decision = '')
            """, (today_str,))
            missing_alert_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])

            ac.execute("SELECT DISTINCT UPPER(symbol) as sym FROM research_queue WHERE date = ?", (today_str,))
            queue_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])

            ac.execute("""
                SELECT DISTINCT UPPER(symbol) as sym FROM alerts
                WHERE date = ? AND llm_decision IS NOT NULL AND llm_decision != ''
            """, (today_str,))
            evaluated_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])
    except Exception as e:
        logger.warning(f"[_build_coverage_payload] Read-only alerts query exception: {e}")

    missing_alert_syms = missing_alert_syms - evaluated_syms
    un_evaluated_queue = queue_syms - evaluated_syms
    local_missing = sorted(list(missing_alert_syms | un_evaluated_queue))

    done_job_syms = set()
    deep_queued = []
    try:
        with get_readonly_connection(watch_db_path, timeout=2.0) as watch_conn:
            cursor = watch_conn.cursor()
            cursor.execute("""
                SELECT DISTINCT UPPER(ticker) as sym FROM active_research_jobs
                WHERE target_date = ? AND status = 'COMPLETED'
            """, (today_str,))
            done_job_syms = set(r["sym"] for r in cursor.fetchall() if r["sym"])

            cursor.execute("""
                SELECT DISTINCT UPPER(ticker) as sym FROM active_research_jobs
                WHERE target_date = ? AND status = 'QUEUED'
            """, (today_str,))
            deep_queued = [r["sym"] for r in cursor.fetchall() if r["sym"]]
    except Exception as e:
        logger.warning(f"[_build_coverage_payload] Read-only watch query exception: {e}")

    deep_done_symbols = set()
    for sym in local_pass_symbols:
        if sym in done_job_syms or _has_deep_report(sym, today_str):
            deep_done_symbols.add(sym)

    deep_missing = sorted(list(local_pass_symbols - deep_done_symbols))
    deep_done_count = len(deep_done_symbols)
    local_pass_count = len(local_pass_symbols)

    deep_cap_deferred = []
    watch_no_deep = []
    try:
        with get_readonly_connection(alerts_db_path, timeout=2.0) as alert_conn:
            ac = alert_conn.cursor()
            ac.execute("SELECT symbol, llm_decision, llm_playbook FROM alerts WHERE date = ?", (today_str,))
            for row in ac.fetchall():
                s = (row["symbol"] or "").upper()
                dec = (row["llm_decision"] or "").upper()
                reas = (row["llm_playbook"] or "").strip()
                if len(reas) > 120:
                    reas = reas[:117] + "..."
                has_report = _has_deep_report(s, today_str) or s in done_job_syms
                if "PASS" in dec and not has_report and s not in deep_queued:
                    deep_cap_deferred.append({"ticker": s, "reason": reas or "Deferred by daily research cap"})
                elif "WATCH" in dec and not has_report:
                    watch_no_deep.append({"ticker": s, "reason": reas or "Stalking watch - awaiting zone trigger"})
    except Exception as e:
        logger.warning(f"[_build_coverage_payload] Deferred/Watch alerts query exception: {e}")

    return {
        "alerts": alerts_count,
        "local_done": local_done_count,
        "local_pass": local_pass_count,
        "deep_done": deep_done_count,
        "deep_queued": deep_queued,
        "deep_missing": deep_missing,
        "local_missing": local_missing,
        "deep_cap_deferred": deep_cap_deferred,
        "watch_no_deep": watch_no_deep,
    }


def _build_missed_moves_sync() -> Dict[str, Any]:
    try:
        from src.tracking.missed_moves import get_missed_moves
        return get_missed_moves(min_move_pct=8.0, lookback_days=5, include_backfill=True)
    except Exception as e:
        return {"error": str(e), "setups": [], "summary": {}}


def _build_morning_briefing_sync() -> Dict[str, Any]:
    today_str = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    briefing_file = config.BASE_DIR / "data" / "briefings" / f"briefing_{today_str}.json"
    if briefing_file.exists():
        try:
            return json.loads(briefing_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from src.logic.morning_briefing import generate_morning_briefing
        return generate_morning_briefing(target_date=today_str, force_live_quotes=False)
    except Exception as e:
        return {"error": str(e), "tier1_actionable": [], "regime": "NEUTRAL"}


def _build_feedback_loop_sync() -> Dict[str, Any]:
    try:
        from src.ui.services.feedback_service import get_feedback_loop_data
        return {"status": "ok", **get_feedback_loop_data()}
    except Exception as e:
        return {"status": "error", "error": str(e), "open_positions": [], "closed_positions": []}


# ─────────────────────────────────────────────────────────────────────────────
# 2. CENTRAL BACKGROUND ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _cached_snapshot(name: str) -> Tuple[Optional[Dict[str, Any]], float]:
    with _CACHE_LOCK:
        return _CACHE.get(name), float(_CACHE.get(f"{name}_ts", 0.0) or 0.0)


def _data_fingerprint() -> Tuple[Any, ...]:
    """Identity of the data a desk payload was built from: the session date, the live
    R:R dial, and the alerts and watch DBs (path, mtime, size).

    A cached payload is only served while this fingerprint is unchanged. A swapped
    or rewritten DB (a fresh isolated instance, a restore, a new trading day)
    invalidates the cache instead of serving the previous day's numbers — and so does
    a dial change: moving the R:R floor must re-bucket the very next request.
    """
    try:
        rr_cfg = rr_config.get_rr_config()
        parts: List[Any] = [
            get_eastern_date_str(),
            ("rr_config", tuple(sorted(((k, round(float(v), 6)) for k, v in rr_cfg.items())))),
        ]
        for path in (config.alerts_db_path(), config.research_watch_db_path()):
            try:
                st = os.stat(path)
                parts.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                parts.append((str(path), None, None))
        return tuple(parts)
    except Exception as e_fp:
        # A fingerprint failure must be LOUD, not silent: the constant fallback would
        # freeze every cached payload behind a fingerprint that never changes, and a
        # DB or dial change would stop invalidating the cache.
        logger.error(f"[serve_payload] data fingerprint failed ({e_fp}); cache invalidation is degraded")
        return ("unknown",)


_WARM_PATH = config.BASE_DIR / "data" / "desk_warm_cache.json"
_WARM_MAX_AGE_S = 900.0
_WARM_WRITE_EVERY_S = 60.0
_WARM_LAST_WRITE = 0.0


def _warm_key() -> Any:
    """Session date and R:R dial: the part of the fingerprint a restart does not change."""
    return json.loads(json.dumps(_data_fingerprint()[:2], default=str))


def _persist_warm_start(payload: Dict[str, Any]) -> None:
    """Save the last good Today payload so a restart can serve it while the first build runs."""
    global _WARM_LAST_WRITE
    now_ts = time.time()
    if now_ts - _WARM_LAST_WRITE < _WARM_WRITE_EVERY_S:
        return
    _WARM_LAST_WRITE = now_ts
    try:
        _WARM_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _WARM_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"ts": now_ts, "key": _warm_key(), "payload": payload}, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, _WARM_PATH)
    except Exception as e:
        logger.warning(f"[today_service] warm-start save failed: {e}")


def _load_warm_start() -> None:
    """Load the saved Today payload as a stale cache entry, only for the same session date and dial."""
    try:
        if not _WARM_PATH.exists():
            return
        saved = json.loads(_WARM_PATH.read_text(encoding="utf-8"))
        ts = float(saved.get("ts") or 0.0)
        payload = saved.get("payload")
        if not isinstance(payload, dict) or time.time() - ts > _WARM_MAX_AGE_S:
            return
        if saved.get("key") != _warm_key():
            return
        with _CACHE_LOCK:
            if _CACHE.get("today") is not None:
                return
            _CACHE["today"] = payload
            _CACHE["today_ts"] = ts
            _CACHE["today_fp"] = None
            _CACHE["today_warm"] = True
    except Exception as e:
        logger.warning(f"[today_service] warm-start load failed: {e}")


def _serve_warm_start(name: str) -> Optional[Dict[str, Any]]:
    """The saved payload, until the first real build replaces it (bounded by _WARM_MAX_AGE_S)."""
    with _CACHE_LOCK:
        if not _CACHE.get(f"{name}_warm"):
            return None
        payload = _CACHE.get(name)
        ts = float(_CACHE.get(f"{name}_ts", 0.0) or 0.0)
    if payload is None or time.time() - ts > _WARM_MAX_AGE_S:
        return None
    return {**payload, "stale_s": round(time.time() - ts, 1), "from_cache": True, "warm_start": True}


def _store_payload(name: str, payload: Optional[Dict[str, Any]], t0: float, fingerprint=None) -> None:
    if payload is None:
        return
    build_s = round(time.time() - t0, 2)
    with _CACHE_LOCK:
        _CACHE[name] = payload
        _CACHE[f"{name}_ts"] = time.time()
        _CACHE[f"{name}_build_s"] = build_s
        _CACHE[f"{name}_fp"] = fingerprint if fingerprint is not None else _data_fingerprint()
        _CACHE[f"{name}_warm"] = False
    if name == "today" and not payload.get("error"):
        _persist_warm_start(payload)


def _due(name: str, now_ts: float, force: bool = False) -> bool:
    """True when the payload's TTL says it is time to rebuild.

    An empty cache is always due: a poll skips a refresh only while one for the
    same payload is already in flight (guarded by the per-payload lock), never
    because the cycle decided to stop rebuilding a cached payload. A payload whose
    data fingerprint changed (different DB / new day) is due however fresh it is.
    """
    if force:
        return True
    _cached, ts = _cached_snapshot(name)
    if _cached is None:
        return True
    with _CACHE_LOCK:
        fp = _CACHE.get(f"{name}_fp")
    if fp != _data_fingerprint():
        return True
    return (now_ts - ts) >= _PAYLOAD_TTL_S.get(name, 15.0)


def _run_build(name: str, builder) -> Optional[Dict[str, Any]]:
    """Build one payload under its own lock; an in-flight build is never duplicated."""
    lock = _PAYLOAD_LOCKS.get(name)
    if lock is None:
        return builder()
    # The background cycle never blocks waiting for another in-flight build:
    # if a build is already in flight, let that thread finish and store the result.
    if not lock.acquire(timeout=0.25):
        logger.info(
            f"[TodayBackgroundService] {name} build skipped: another build holds it "
            f"(in flight, not queued)."
        )
        return None
    try:
        t0 = time.time()
        payload = builder()
        _store_payload(name, payload, t0)
        return payload
    except Exception as e:
        logger.error(f"[TodayBackgroundService] Error computing {name}: {e}")
        return None
    finally:
        lock.release()


def _serve_cached(name: str, fingerprint) -> Optional[Dict[str, Any]]:
    """The cached payload when it is still valid for this fingerprint."""
    cached, cache_ts = _cached_snapshot(name)
    if cached is None:
        return None
    with _CACHE_LOCK:
        fp = _CACHE.get(f"{name}_fp")
    if fp != fingerprint:
        return None
    payload = {**cached, "stale_s": round(time.time() - cache_ts, 1), "from_cache": True}
    payload["build_s"] = _CACHE.get(f"{name}_build_s")
    return payload


def _build_and_store(name: str, builder, fingerprint) -> Dict[str, Any]:
    """Run the builder once, store it under this fingerprint, and serve it fresh."""
    t0 = time.time()
    payload = builder()
    _store_payload(name, payload, t0, fingerprint)
    return {**payload, "stale_s": 0.0, "from_cache": False, "build_s": round(time.time() - t0, 2)}


def _wait_for_inflight(name: str, fingerprint) -> Optional[Dict[str, Any]]:
    """Wait (bounded) for the build another thread is running, then serve its result.

    Never duplicates the build: the payload is whatever that thread stored, and only
    when it was stored under this same fingerprint.
    """
    lock = _PAYLOAD_LOCKS.get(name)
    if lock is None:
        return None
    if not lock.acquire(blocking=False):
        if not lock.acquire(timeout=_COLD_GET_WAIT_S):
            return None
        try:
            return _serve_cached(name, fingerprint)
        finally:
            lock.release()
    try:
        return None
    finally:
        lock.release()


def serve_payload(name: str, builder, force_refresh: bool = False) -> Dict[str, Any]:
    """Serve a desk payload: cache first, and build exactly once on a cold cache.

    A cold GET waits (bounded) for an in-flight background build instead of
    duplicating it; if none is running it builds itself and caches. The response
    always carries `stale_s` so the UI can show the payload's age, and `from_cache`
    so a repeat is diagnosable. The cache is fingerprint-checked: a payload built
    from a different DB, a different dial, or a different session is rebuilt, never
    served. ``force_refresh`` means THIS response is rebuilt now — a caller that
    asked for fresh data is never handed the previous payload.
    """
    fingerprint = _data_fingerprint()

    if not force_refresh:
        cached = _serve_cached(name, fingerprint)
        if cached is not None:
            return cached
        warm = _serve_warm_start(name)
        if warm is not None:
            return warm
        if _PAYLOAD_LOCKS.get(name) is not None and _PAYLOAD_LOCKS[name].locked():
            logger.debug(
                f"[serve_payload] {name} cache miss; a build is in flight, waiting for it."
            )
    elif _PAYLOAD_LOCKS.get(name) is not None:
        # force_refresh still does not queue a second build behind an in-flight one:
        # it waits for that build and serves what it just stored.
        pass

    lock = _PAYLOAD_LOCKS.get(name)

    if force_refresh:
        # Still wake the worker: it may have a broader refresh to run next cycle.
        trigger_today_refresh()

    if lock is not None and not lock.acquire(blocking=False):
        served = _wait_for_inflight(name, fingerprint)
        if served is not None:
            return served
        if force_refresh:
            # The in-flight build belongs to another fingerprint (stale): build now.
            if not lock.acquire(timeout=_COLD_GET_WAIT_S):
                return {
                    "error": f"{name} build in flight; retry shortly",
                    "stale_s": None,
                    "from_cache": False,
                }
            try:
                return _build_and_store(name, builder, fingerprint)
            finally:
                lock.release()
        return {
            "error": f"{name} build in flight; retry shortly",
            "stale_s": None,
            "from_cache": False,
        }

    if lock is not None:
        try:
            return _build_and_store(name, builder, fingerprint)
        finally:
            lock.release()

    return _build_and_store(name, builder, fingerprint)


def get_cache_stats() -> Dict[str, Any]:
    """Diagnosability surface: every payload's cache age, last build time and TTL."""
    now_ts = time.time()
    out: Dict[str, Any] = {"payloads": {}, "updating": _IS_UPDATING}
    for name in _PAYLOAD_TTL_S:
        cached, ts = _cached_snapshot(name)
        out["payloads"][name] = {
            "cached": cached is not None,
            "stale_s": round(now_ts - ts, 1) if ts else None,
            "ttl_s": _PAYLOAD_TTL_S.get(name),
            "build_s": _CACHE.get(f"{name}_build_s"),
            "locked": _PAYLOAD_LOCKS[name].locked(),
        }
    return out


def _background_cycle(force: bool = False):
    """Run one synthesis cycle across the desk payloads that are due.

    today / coverage / missed-moves run every cycle; briefing and feedback follow
    their own lazy TTL. A payload already building elsewhere (in-flight) is skipped,
    not queued behind it.

    This cycle is the desk's WRITER half: the read path (GET endpoints) never spawns a
    DB write, so the scheduled writers that a payload's data depends on are triggered
    from here instead.
    """
    global _IS_UPDATING
    _IS_UPDATING = True
    start_ts = time.time()
    now_ts = start_ts

    # Scheduled writer: resolve paper theses on a 5-minute throttle, off the read path.
    try:
        from src.ui.services.action_engine import _maybe_refresh_paper

        _maybe_refresh_paper()
    except Exception as e_paper:
        logger.debug(f"[TodayBackgroundService] paper refresh dispatch skipped: {e_paper}")

    builders = {
        "today": _build_today_payload_sync,
        "coverage": _build_coverage_payload_sync,
        "missed_moves": _build_missed_moves_sync,
        "morning_briefing": _build_morning_briefing_sync,
        "feedback_loop": _build_feedback_loop_sync,
    }
    due = [name for name, builder in builders.items() if _due(name, now_ts, force=force)]
    if not due:
        _IS_UPDATING = False
        return

    try:
        # Run primary desk payloads first (today, coverage) so user sees UI instantly
        # and quotes cache is primed, followed by secondary background analyses.
        primary = [name for name in ("today", "coverage") if name in due]
        secondary = [name for name in due if name not in primary]
        ordered_stages = [s for s in (primary, secondary) if s]

        for stage_names in ordered_stages:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(stage_names)))
            try:
                futures = {
                    name: executor.submit(_run_build, name, builders[name])
                    for name in stage_names
                }
                for name, fut in futures.items():
                    try:
                        fut.result(timeout=_PAYLOAD_TIMEOUT_S.get(name, 30.0))
                    except Exception as e:
                        logger.error(f"[TodayBackgroundService] {name} cycle error: {type(e).__name__} {e}")
            finally:
                executor.shutdown(wait=False)

        logger.info(
            f"⚡ [TodayBackgroundService] Cycle refreshed {','.join(due)} "
            f"in {round(time.time() - start_ts, 2)}s."
        )
    except Exception as exc:
        logger.error(f"❌ [TodayBackgroundService] Background cycle failed: {exc}", exc_info=True)
    finally:
        _IS_UPDATING = False


def _worker_loop():
    """Main daemon loop running every 15-20 seconds."""
    global _FORCE_NEXT_CYCLE
    logger.info("🚀 [TodayBackgroundService] Daemon thread started.")
    # Run initial cycle immediately
    _background_cycle(force=True)

    while not _STOP_EVENT.is_set():
        # Sleep for 15s or wake up on trigger
        triggered = _TRIGGER_EVENT.wait(timeout=15.0)
        if _STOP_EVENT.is_set():
            break
        if triggered:
            _TRIGGER_EVENT.clear()
        force_next = _FORCE_NEXT_CYCLE
        _FORCE_NEXT_CYCLE = False
        _background_cycle(force=force_next)

    logger.info("🛑 [TodayBackgroundService] Daemon thread terminated.")


def start_today_service():
    """Start the background refresh thread if not already running."""
    global _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _TRIGGER_EVENT.clear()
    _load_warm_start()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, daemon=True, name="TodayBackgroundService")
    _WORKER_THREAD.start()


def stop_today_service():
    """Signal background thread to exit cleanly."""
    _STOP_EVENT.set()
    _TRIGGER_EVENT.set()


def trigger_today_refresh():
    """Wake background worker for an immediate recalculation of every payload."""
    global _FORCE_NEXT_CYCLE
    _FORCE_NEXT_CYCLE = True
    _TRIGGER_EVENT.set()


rebuild_today_cache = _background_cycle


# ─────────────────────────────────────────────────────────────────────────────
# 3. HIGH-SPEED PUBLIC GETTERS (ZERO-WAIT CACHE LOOKUPS)
# ─────────────────────────────────────────────────────────────────────────────

def get_today_payload(force: bool = False) -> Dict[str, Any]:
    """Return Today desk payload instantly from in-memory cache with stale_s telemetry.

    A cold cache builds exactly once (serve_payload's per-payload lock); a cached
    payload is returned without any DB work, so the GET stays sub-second even while
    deep research holds the write lock.
    """
    return serve_payload("today", _build_today_payload_sync, force_refresh=force)


def get_coverage_payload(force: bool = False) -> Dict[str, Any]:
    """Return Coverage payload instantly from in-memory cache with stale_s."""
    return serve_payload("coverage", _build_coverage_payload_sync, force_refresh=force)


def get_missed_moves_payload(force: bool = False) -> Dict[str, Any]:
    """Return Missed Moves scorecard instantly from in-memory cache with stale_s."""
    return serve_payload("missed_moves", _build_missed_moves_sync, force_refresh=force)


def get_morning_briefing_payload(date: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """Return Morning Executive Briefing from in-memory cache (lazy: 5-minute TTL).

    A dated briefing bypasses the cache: it is a different day's file, not a refresh.
    """
    if force_refresh and not date:
        return serve_payload(
            "morning_briefing", _build_morning_briefing_sync, force_refresh=True
        )
    if date:
        payload = _build_morning_briefing_sync()
        return {**payload, "stale_s": None, "from_cache": False}
    return serve_payload("morning_briefing", _build_morning_briefing_sync)


def get_feedback_loop_payload(force: bool = False) -> Dict[str, Any]:
    """Return Trade Feedback Loop and surveillance from in-memory cache (lazy: 2-minute TTL)."""
    return serve_payload("feedback_loop", _build_feedback_loop_sync, force_refresh=force)
