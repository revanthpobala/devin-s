from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
import logging
import sqlite3

import re
from src.tracking.watch_manager import _get_connection, _db_lock
from src.tracking.alert_db import (
    DB_PATH,
    _db_lock as _alert_db_lock,
    _get_connection as _get_alert_conn,
    get_eastern_date_str,
)
from src.tracking.suggestion_scorer import get_main_record_stats
from src.tracking import rr_config
from src.logic.actionable_gate import is_actionable
from src.logic.setup_lane_map import inbox_row_mapping

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desk", tags=["desk"])

# Measured and not configurable: the corpus median stop is 0.69 ATR and 65% of stops get hit, so
# anything narrower is inside daily noise and inflates R:R without adding information.
STOP_ATR_NOISE_FLOOR = rr_config.STOP_ATR_MIN

# Single definition of "is this a coverage gap", shared with the OPS tier.
from src.tracking.ops_alerts import (  # noqa: E402,F401
    CORE_SYMBOLS,
    core_coverage_gaps,
    is_test_ticker,
)


class JournalNotesUpdate(BaseModel):
    notes: str


def _is_job_active_in_db(ticker: str, date_str: str) -> bool:
    """Check if ticker already has a RUNNING/QUEUED deep research job for today."""
    ticker = ticker.upper()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM active_research_jobs WHERE LOWER(ticker) = ? AND target_date = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
                (ticker.lower(), date_str),
            )
            return cursor.fetchone() is not None


from zoneinfo import ZoneInfo

def _has_deep_report(ticker: str, date_str: str) -> bool:
    """Check if a deep research report already exists for ticker on date."""
    from pathlib import Path
    from src import config
    ticker = ticker.upper()
    base = config.BASE_DIR / "reports" / date_str
    if not base.exists():
        return False
    return (base / f"{ticker}_summary.md").exists() or (base / f"{ticker}_arbitration.md").exists()


@router.get("/morning-briefing")
def get_morning_briefing(date: Optional[str] = None, force_refresh: bool = False):
    """Return synthesized morning executive briefing with real-time quotes."""
    import json
    from src import config
    from src.logic.morning_briefing import generate_morning_briefing

    date_str = date or datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    briefing_file = config.BASE_DIR / "data" / "briefings" / f"briefing_{date_str}.json"

    if briefing_file.exists() and not force_refresh:
        try:
            return json.loads(briefing_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed reading briefing cache: {e}")

    return generate_morning_briefing(target_date=date_str, force_live_quotes=True)


@router.post("/morning-briefing/refresh")
def refresh_morning_briefing(date: Optional[str] = None):
    """Force real-time quote refresh and re-score of the morning briefing."""
    from src.logic.morning_briefing import generate_morning_briefing

    date_str = date or datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    return generate_morning_briefing(target_date=date_str, force_live_quotes=True)


class RRConfigUpdate(BaseModel):
    """Partial update. Omitted keys keep their current value."""
    rr_market_min: Optional[float] = None
    rr_hi_rr: Optional[float] = None


@router.get("/rr-config")
def get_rr_config_endpoint():
    """Current R:R thresholds plus bounds, defaults, and what is deliberately not tunable."""
    return rr_config.as_ui_payload()


@router.post("/rr-config")
def set_rr_config_endpoint(req: RRConfigUpdate):
    """Persist new R:R thresholds. Applies to the desk and to the ENTRY push gate immediately."""
    try:
        updated = rr_config.set_rr_config(**{
            k: v for k, v in req.model_dump().items() if v is not None
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    payload = rr_config.as_ui_payload()
    payload["updated"] = updated
    logger.info("R:R thresholds updated via UI: %s", updated)
    return payload


@router.post("/rr-config/preset/{name}")
def apply_rr_preset_endpoint(name: str):
    """Apply a named preset (1:1 / 1:2 / 1:3 / 1:5)."""
    try:
        updated = rr_config.apply_preset(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    payload = rr_config.as_ui_payload()
    payload["updated"] = updated
    logger.info("R:R preset applied via UI: %s -> %s", name, updated)
    return payload


@router.post("/rr-config/reset")
def reset_rr_config_endpoint():
    """Back to the measured defaults."""
    rr_config.reset_rr_config()
    return {**rr_config.as_ui_payload(), "updated": rr_config.get_rr_config()}


@router.get("/today")
def get_today():
    try:
        today_str = get_eastern_date_str()

        with _alert_db_lock:
            with _get_alert_conn() as alert_conn:
                alert_conn.row_factory = sqlite3.Row
                ac = alert_conn.cursor()

                ac.execute("""
                    SELECT setup, GROUP_CONCAT(symbol) as tickers_str, COUNT(*) as cnt, source, status, reason
                    FROM research_queue
                    WHERE date = ?
                    GROUP BY setup
                    ORDER BY id ASC
                """, (today_str,))
                found_rows = ac.fetchall()
                found = []
                for row in found_rows:
                    r = dict(row)
                    tickers_str = r.pop("tickers_str", "") or ""
                    tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()]
                    r["tickers"] = tickers
                    setup_name = r.get("setup") or ""
                    if r.get("reason") == f"Screener candidate setup: {setup_name}":
                        r.pop("reason", None)
                    found.append(r)

                ac.execute("""
                    SELECT LOWER(symbol) as sym, UPPER(symbol) as ticker, date, llm_decision, llm_playbook, setup, alert_price, market_price, score
                    FROM alerts
                    WHERE date = ?
                      AND llm_decision IS NOT NULL
                      AND llm_decision != ''
                    ORDER BY rowid ASC
                """, (today_str,))
                alert_rows = ac.fetchall()

        with _db_lock:
            with _get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")

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
                suggestion_rows = cursor.fetchall()

                cursor.execute("""
                    SELECT LOWER(ticker) as sym, status as job_status, stage, stage_detail
                    FROM active_research_jobs
                    WHERE status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')
                      AND target_date = ?
                """, (today_str,))
                job_rows = cursor.fetchall()

                def _decision_rank(dec: str) -> int:
                    d = (dec or "").upper()
                    if "PASS" in d:
                        return 3
                    if "WATCH" in d:
                        return 2
                    if "CUT" in d:
                        return 1
                    return 0

                alert_map = {}
                for ar in alert_rows:
                    ar = dict(ar)
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

                job_map = {}
                for jr in job_rows:
                    jr = dict(jr)
                    sym = jr.get("sym")
                    if sym:
                        job_map[sym] = jr

                actionable = []
                stalking = []
                watch_list = []
                cut_list = []
                needs_you = []
                unmeasured = []
                closest_to_gate = []
                missing_symbols = set()
                processed_syms = set()

                triaged_syms = set(alert_map.keys())
                all_sugg_syms = set(((dict(r) or {}).get("ticker") or "").lower() for r in suggestion_rows)

                for sym in all_sugg_syms:
                    if sym not in triaged_syms:
                        missing_symbols.add(sym.upper())

                for row in suggestion_rows:
                    r = dict(row)
                    ticker = r.get("ticker", "")
                    sym = (ticker or "").lower()
                    processed_syms.add(sym)

                    local = alert_map.get(sym, {})
                    r["date"] = (local.get("date") if local else None) or today_str
                    r["llm_decision"] = local.get("llm_decision")
                    r["llm_playbook"] = (local.get("llm_playbook") or "")[:120]
                    r["local_score"] = local.get("score") or ""

                    job = job_map.get(sym)
                    has_real_deep = _has_deep_report(sym, today_str)
                    if job:
                        job_st = job.get("job_status", "none")
                        job_stage = job.get("stage", "none")
                        if job_st == "COMPLETED" and (job_stage == "LOCAL_DONE" or not has_real_deep):
                            r["deep_status"] = "skipped" if job_stage == "LOCAL_DONE" else "none"
                        else:
                            r["deep_status"] = job_st
                        r["deep_stage"] = job_stage
                        r["deep_stage_detail"] = job.get("stage_detail", "")
                    else:
                        r["deep_status"] = "COMPLETED" if has_real_deep else "none"
                        r["deep_stage"] = "DONE" if has_real_deep else "none"
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

                    # Stop width in ATR if available
                    atr_at_signal = r.get("atr_at_signal") if r.get("atr_at_signal") is not None else r.get("w_atr_at_signal")
                    if atr_at_signal and atr_at_signal > 0 and entry_h and stop:
                        r["stop_width_atr"] = round(abs(entry_h - stop) / atr_at_signal, 2)
                    else:
                        r["stop_width_atr"] = None

                    # Measured lane, split by the PB bit when the split was measured.
                    r["setup_lane"] = r.get("setup_lane") or None
                    r["pb_bucket"] = _pb_bucket(r.get("pb_funnel"))

                    # R:R measured at the market. rr_at_market_at_signal is only persisted when the signal bar carried
                    # a spot (7 of 177 rows today), so when it is absent we compute the same ratio
                    # from the live print and the persisted stop/target, and say which one it is.
                    # A row with neither is genuinely unmeasured and must not be ranked.
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

                    status = r.get("status") or "STALKING"
                    dist = r.get("dist")
                    near = dist is not None and abs(float(dist)) <= 1.5

                    # Single actionable gate using real stored fields only
                    min_rr_floor = rr_config.min_rr()
                    from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
                    in_zone_flag = 1.0 if (r.get("in_zone") or status in ("IN_ZONE", "ENTER") or near) else 0.0
                    entry_px = r.get("entry_high") or last_px or r.get("entry_low")
                    sig_pack_val = r.get("signal_pack") if r.get("signal_pack") is not None else r.get("w_signal_pack")
                    fade_val = r.get("fade") if r.get("fade") is not None else r.get("w_fade")
                    action_long_val = r.get("action_long") if r.get("action_long") is not None else (r.get("w_action_long") if r.get("w_action_long") is not None else (r.get("action_code") if r.get("action_code") is not None else None))
                    ext_z_val = r.get("ext_z") if r.get("ext_z") is not None else r.get("w_ext_z")
                    dw_dict = {
                        "long_in_zone": in_zone_flag,
                        "long_rr_at_market": rr_at_market,
                        "long_stop_loss": stop,
                        "atr14": float(atr_at_signal) if (atr_at_signal and float(atr_at_signal) > 0) else None,
                        "price": last_px or r.get("price"),
                        "signal_pack": float(sig_pack_val) if sig_pack_val is not None else None,
                        "fade_long": float(fade_val) if fade_val is not None else ((0.0 if not r.get("fade_gate") else 1.0) if r.get("fade_gate") is not None else None),
                        "action_long": float(action_long_val) if action_long_val is not None else None,
                        "ext_z_self": float(ext_z_val) if ext_z_val is not None else None,
                    }
                    gate_in = gate_inputs_from_datawindow(dw_dict)
                    is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": entry_px})
                    r["measured"] = is_act
                    r["measured_actionable"] = is_act
                    r["gate_reasons"] = gate_fails

                    if not is_act and gate_fails and len(gate_fails) == 1:
                        r_close = dict(r)
                        r_close["missing_condition"] = gate_fails[0]
                        closest_to_gate.append(r_close)

                    # setup_lane is PERSISTED at triage time by the Pine's own thresholds, so it
                    # does not follow a UI change to the R:R floor. Re-tier the R:R lanes from the
                    # live number so the badge agrees with the bucket the row just landed in;
                    # CODE20 / OVERSOLD / RSI2 / WATCH_SHADOW keep their identity untouched.
                    r["lane"] = _live_lane_tier(r["setup_lane"], rr_at_market)
                    r["lane_persisted"] = r["setup_lane"]
                    prior_win, prior_ev = _lane_priors(r["lane"], r.get("pb_funnel"))
                    r["lane_prior_win"] = prior_win
                    r["lane_prior_ev"] = prior_ev

                    # A stop inside daily noise makes any R:R an artifact. The corpus median is
                    # 0.69 ATR and 65% of stops get hit; 0.2 ATR produces "RR 14" and nothing else.
                    r["stop_tight"] = bool(
                        r["stop_width_atr"] is not None and r["stop_width_atr"] < STOP_ATR_NOISE_FLOOR
                    )
                    # Sorting on raw RR promotes exactly these rows. Never sort on it.
                    r["sort_rr"] = -1.0 if r["stop_tight"] else (rr_at_market or -1.0)

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
                            # Fails actionable gate -> unmeasured group
                            r["unmeasured_reason"] = "; ".join(gate_fails) if gate_fails else "below_bar"
                            unmeasured.append(r)
                        elif has_real_deep and deep_status == "COMPLETED":
                            if status in ("IN_ZONE", "IN_TRADE") or near:
                                actionable.append(r)
                            else:
                                stalking.append(r)
                        elif is_act:
                            # Needs-you is a work queue, not a backlog. Only gate-passing rows qualify!
                            needs_you.append(r)
                        else:
                            r["unmeasured_reason"] = "; ".join(gate_fails) if gate_fails else "below_bar"
                            unmeasured.append(r)
                    elif "WATCH" in local_dec:
                        watch_list.append(r)
                    elif "CUT" in local_dec:
                        cut_list.append(r)

                # Process alerts for symbols that do not yet have suggestion records
                for sym, local in alert_map.items():
                    if sym in processed_syms:
                        continue
                    processed_syms.add(sym)
                    local_dec = (local.get("llm_decision") or "").upper()
                    job = job_map.get(sym, {})
                    deep_st = job.get("job_status", "none")
                    job_stg = job.get("stage", "none")
                    has_real_deep = _has_deep_report(sym, today_str)
                    if deep_st == "COMPLETED" and (job_stg == "LOCAL_DONE" or not has_real_deep):
                        deep_st = "skipped" if job_stg == "LOCAL_DONE" else "none"
                    elif has_real_deep and deep_st == "none":
                        deep_st = "COMPLETED"

                    item = {
                        "ticker": local.get("ticker") or sym.upper(),
                        "date": local.get("date") or today_str,
                        "setup": local.get("setup") or "",
                        "llm_decision": local.get("llm_decision"),
                        "llm_playbook": local.get("llm_playbook") or "",
                        "local_score": local.get("score") or "",
                        "deep_status": deep_st,
                        "deep_stage": job_stg,
                        "deep_stage_detail": job.get("stage_detail", ""),
                        "status": "NEEDS_DEEP" if "PASS" in local_dec else ("WATCH" if "WATCH" in local_dec else "CUT"),
                        "last_price": local.get("alert_price") or local.get("market_price"),
                        "entry_low": None,
                        "entry_high": None,
                        "stop": None,
                        "target_1": None,
                        "dist": None,
                        "room_to_stop": 0.0,
                        "live_rr": None,
                        "live_rr_flag": None,
                        "stop_width_atr": None,
                        # An alert with no suggestion row has no lane and no measured R:R.
                        "setup_lane": None,
                        "lane": None,
                        "lane_prior_win": None,
                        "lane_prior_ev": None,
                        "pb_bucket": "unmeasured",
                        "rr_at_market_at_signal": None,
                        "rr_at_market": None,
                        "rr_at_market_source": None,
                        "measured": False,
                        "measured_actionable": False,
                        "gate_reasons": ["No suggestion record or tactical levels"],
                        "unmeasured_reason": "no_levels",
                        "stop_tight": False,
                        "sort_rr": -1.0,
                    }

                    if "PASS" in local_dec:
                        unmeasured.append(item)
                    elif "WATCH" in local_dec:
                        watch_list.append(item)
                    elif "CUT" in local_dec:
                        cut_list.append(item)

                # Build Section 0a Inbox (one row per symbol with latest alert)
                inbox = []
                for sym, local in alert_map.items():
                    job = job_map.get(sym, {})
                    s_match = next((s for s in suggestion_rows if ((s["ticker"] or "").lower() == sym)), None)
                    score_match = re.search(r"\((\d+(?:/\d+)?)\)", local.get("llm_decision") or "")
                    score_val = score_match.group(1) if score_match else (str(local.get("score")) if local.get("score") else "")
                    j_st = job.get("job_status", "none")
                    j_stg = job.get("stage", "none")
                    has_real_deep = _has_deep_report(sym, today_str)
                    if j_st == "COMPLETED" and (j_stg == "LOCAL_DONE" or not has_real_deep):
                        ib_deep_status = "skipped" if j_stg == "LOCAL_DONE" else "none"
                    elif has_real_deep and j_st == "none":
                        ib_deep_status = "COMPLETED"
                    else:
                        ib_deep_status = j_st
                    setup_name = local.get("setup") or ""
                    lane_map = inbox_row_mapping(setup_name)
                    inbox.append({
                        "ticker": local.get("ticker") or sym.upper(),
                        "date": local.get("date") or today_str,
                        "setup": setup_name,
                        # A+ Trend Long / Early Action Long are never labelled "measured".
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

                # The inbox is a list of unmeasured names wearing the same typography as measured
                # ones. Partition it so the counts are honest.
                inbox_measured = [r for r in inbox if r.get("measured")]
                inbox_unmeasured = [r for r in inbox if not r.get("measured")]

                actionable.sort(key=lambda x: (
                    1 if x.get("status") == "IN_TRADE" else
                    2 if x.get("status") == "IN_ZONE" else 3,
                    # sort_rr demotes stop-inside-noise rows below every measurable one, so a
                    # "RR 250" off a 0.02-ATR stop can never sit at the top of the desk.
                    -(x.get("sort_rr") if x.get("sort_rr") is not None else -9999.0),
                ))
                unmeasured.sort(key=lambda x: (x.get("ticker") or ""))

                # Fixture tickers are not coverage gaps. GOODTICKER sat in missing_symbols next
                # to SPY/QQQ/AMZN and made the list unreadable. They are separated out rather than
                # counted; ops_alerts.purge_test_rows() removes the underlying rows on request.
                missing_all = sorted(missing_symbols)
                missing_test = sorted(s for s in missing_all if is_test_ticker(s))
                missing_real = [s for s in missing_all if not is_test_ticker(s)]
                # Genuine coverage gaps come from the same probe the OPS tier uses, NOT from
                # missing_real: that list is inbox-derived, so it flags every core name absent
                # from today's alerts. On live data all six core names were triaged (6-54 alerts
                # and live watch_targets each) while this field claimed all six were gaps.
                core_probe_error = None
                try:
                    core_gaps, core_covered = core_coverage_gaps()
                except Exception as exc:
                    core_gaps, core_covered = [], list(CORE_SYMBOLS)
                    core_probe_error = str(exc)[:200]

                # Synthesize dynamic deep research opportunities and actionable streams
                from src.ui.services.opportunity_service import (
                    get_live_market_pulse,
                    collect_active_deep_research_opportunities,
                    get_actionable_alerts_stream,
                )
                from src.ui.services.feedback_service import get_feedback_loop_data

                market_pulse = get_live_market_pulse()
                top_opportunities = collect_active_deep_research_opportunities(lookback_days=3)
                actionable_alerts = get_actionable_alerts_stream(limit=30)
                feedback_data = get_feedback_loop_data()

                return {
                    "market_pulse": market_pulse,
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
                    "cut": cut_list,
                    "needs_you": needs_you,
                    "unmeasured": unmeasured,
                    "closest_to_gate": closest_to_gate,
                    "missing_symbols": missing_real,
                    "missing_symbols_excluded_test_rows": missing_test,
                    "missing_symbols_core_gaps": core_gaps,
                    "core_symbols_covered": core_covered,
                    "core_coverage_error": core_probe_error,
                    # The card labels are generated server-side, so they always state the
                    # threshold actually in force rather than a hardcoded ">= 2".
                    "rr_gates": rr_config.as_ui_payload(),
                    # Rows held back purely because of the dial, split out so the unmeasured group
                    # does not imply they were never measured.
                    "below_bar_count": sum(
                        1 for r in unmeasured if r.get("unmeasured_reason") == "below_bar"
                    ),
                    "coverage": {
                        "found_count": len(found_rows) if found_rows else 0,
                        "inbox_count": len(inbox),
                        "actionable_count": len(actionable),
                        "stalking_count": len(stalking),
                        "watch_count": len(watch_list),
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

    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


from src.logic.data_window_filter import LANE_TO_SETUP_LANE, lane_prior

# setup_lane -> triage reason, so the PB-split priors can be resolved from a ledger row.
_SETUP_LANE_TO_REASON = {v: k for k, v in LANE_TO_SETUP_LANE.items()}

_RR_LANES = ("RR_SETUP", "RR_SETUP_STRONG")


def _live_lane_tier(setup_lane: Optional[str], rr_at_market: Optional[float]) -> Optional[str]:
    """Re-tier an R:R lane from the live R:R and the live thresholds.

    Non-R:R lanes (CODE20, OVERSOLD, RSI2, WATCH_SHADOW) are returned as-is: their tier is a
    property of the setup, not of the current dial. An R:R lane with no measurable R:R also stays
    as persisted, because there is nothing to re-tier it with.
    """
    if setup_lane not in _RR_LANES or rr_at_market is None:
        return setup_lane
    if rr_at_market >= rr_config.hi_rr():
        return "RR_SETUP_STRONG"
    if rr_at_market >= rr_config.min_rr():
        return "RR_SETUP"
    return None


def _lane_priors(setup_lane: Optional[str], pb_funnel) -> Tuple[Optional[float], Optional[float]]:
    """(win %, R) prior for a ledger row. PB-split where the bit was measured, legacy otherwise."""
    return lane_prior(_SETUP_LANE_TO_REASON.get(setup_lane), pb_funnel) or (None, None)


def _pb_bucket(pb_funnel) -> str:
    """UI grouping label. Rows written before the bit existed stay NULL -> 'pre-PB'."""
    if pb_funnel is None:
        return "pre-PB"
    return "PB" if bool(pb_funnel) else "no PB"


@router.get("/journal")
def get_journal(
    status: Optional[str] = None,
    lane: Optional[str] = None,
    ticker: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    include_rejected: int = 0
):
    if not isinstance(from_date, str):
        from_date = getattr(from_date, "default", None)
    if not isinstance(to_date, str):
        to_date = getattr(to_date, "default", None)
    if not isinstance(status, str):
        status = getattr(status, "default", None)
    if not isinstance(lane, str):
        lane = getattr(lane, "default", None)
    if not isinstance(ticker, str):
        ticker = getattr(ticker, "default", None)
    if not isinstance(page, int):
        page = getattr(page, "default", 1) or 1
    if not isinstance(include_rejected, int):
        include_rejected = getattr(include_rejected, "default", 0) or 0

    page_size = 50
    offset = (page - 1) * page_size
    status_upper = status.upper() if status else None

    try:
        with _db_lock:
            with _get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")

                derived_status_sql = (
                    "CASE"
                    " WHEN s.exit_date IS NOT NULL THEN 'CLOSED'"
                    " WHEN s.fill_date IS NOT NULL THEN 'FILLED'"
                    " WHEN s.date >= date('now', '-30 days') THEN 'OPEN'"
                    " ELSE 'EXPIRED'"
                    " END"
                )

                # Build SQL conditions for status filter
                sugg_status_cond = "1=1"
                include_rejected_in_query = False

                if status_upper == "CLOSED":
                    sugg_status_cond = "s.exit_date IS NOT NULL"
                elif status_upper == "FILLED":
                    sugg_status_cond = "s.fill_date IS NOT NULL AND s.exit_date IS NULL"
                elif status_upper == "OPEN":
                    sugg_status_cond = "s.fill_date IS NULL AND s.exit_date IS NULL AND s.date >= date('now', '-30 days')"
                elif status_upper == "EXPIRED":
                    sugg_status_cond = "s.fill_date IS NULL AND s.exit_date IS NULL AND s.date < date('now', '-30 days')"
                elif status_upper == "REJECTED":
                    sugg_status_cond = "1=0"
                    include_rejected_in_query = True
                else:
                    if include_rejected:
                        include_rejected_in_query = True

                summary_params = []
                summary_q = f"""
                    SELECT
                        COUNT(*) as total_rows,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' THEN 1 ELSE 0 END) as closed_n,
                        SUM(CASE WHEN {derived_status_sql} = 'FILLED' THEN 1 ELSE 0 END) as filled_n,
                        SUM(CASE WHEN {derived_status_sql} = 'OPEN' THEN 1 ELSE 0 END) as open_n,
                        SUM(CASE WHEN {derived_status_sql} = 'EXPIRED' THEN 1 ELSE 0 END) as expired_n,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL THEN s.r_net ELSE 0 END) as sum_r,
                        AVG(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL THEN s.r_net ELSE NULL END) as mean_r
                    FROM suggestions s
                    WHERE 1=1
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        summary_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        summary_q += " AND s.setup_lane = ?"
                        summary_params.append(lane)
                if ticker:
                    summary_q += " AND LOWER(s.ticker) = LOWER(?)"
                    summary_params.append(ticker)
                if from_date:
                    summary_q += " AND s.date >= ?"
                    summary_params.append(from_date)
                if to_date:
                    summary_q += " AND s.date <= ?"
                    summary_params.append(to_date)

                summary_rows = cursor.execute(summary_q, summary_params).fetchall()

                # Count matching rejected rows for per_status
                rej_params = []
                rej_count_q = "SELECT COUNT(*) FROM rejected_plans WHERE 1=1"
                if ticker:
                    rej_count_q += " AND LOWER(ticker) = LOWER(?)"
                    rej_params.append(ticker)
                if from_date:
                    rej_count_q += " AND date >= ?"
                    rej_params.append(from_date)
                if to_date:
                    rej_count_q += " AND date <= ?"
                    rej_params.append(to_date)
                rej_count = cursor.execute(rej_count_q, rej_params).fetchone()[0]

                summary = {
                    "total": 0,
                    "closed": 0,
                    "filled": 0,
                    "open": 0,
                    "expired": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_pct": 0.0,
                    "sum_r": 0.0,
                    "mean_r": 0.0,
                    "median_r": 0.0,
                    "per_status": {},
                }
                # Query all individual r_net values matching filters to compute exact mean and median
                med_params = []
                med_q = f"""
                    SELECT s.r_net FROM suggestions s
                    WHERE {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        med_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        med_q += " AND s.setup_lane = ?"
                        med_params.append(lane)
                if ticker:
                    med_q += " AND LOWER(s.ticker) = LOWER(?)"
                    med_params.append(ticker)
                if from_date:
                    med_q += " AND s.date >= ?"
                    med_params.append(from_date)
                if to_date:
                    med_q += " AND s.date <= ?"
                    med_params.append(to_date)

                r_val_rows = cursor.execute(med_q, med_params).fetchall()
                r_vals = [float(r[0]) for r in r_val_rows if r[0] is not None]

                for sr in summary_rows:
                    s = dict(sr)
                    summary["total"] += s.get("total_rows") or 0
                    summary["closed"] += s.get("closed_n") or 0
                    summary["filled"] += s.get("filled_n") or 0
                    summary["open"] += s.get("open_n") or 0
                    summary["expired"] += s.get("expired_n") or 0
                    summary["wins"] += s.get("wins") or 0
                    summary["sum_r"] += round(float(s.get("sum_r") or 0.0), 4)

                summary["per_status"] = {
                    "CLOSED": summary["closed"],
                    "FILLED": summary["filled"],
                    "OPEN": summary["open"],
                    "EXPIRED": summary["expired"],
                    "REJECTED": rej_count,
                }

                if r_vals:
                    summary["losses"] = summary["closed"] - summary["wins"]
                    summary["win_pct"] = round(summary["wins"] / summary["closed"] * 100, 1) if summary["closed"] > 0 else 0.0
                    summary["mean_r"] = round(sum(r_vals) / len(r_vals), 4)
                    s_vals = sorted(r_vals)
                    summary["median_r"] = round(s_vals[len(s_vals) // 2], 4)

                sugg_cols = {col[1] for col in cursor.execute("PRAGMA table_info(suggestions)").fetchall()}
                taken_col_sql = "s.taken" if "taken" in sugg_cols else "0 as taken"
                fill_col_sql = "s.your_fill" if "your_fill" in sugg_cols else "NULL as your_fill"
                pb_col_sql = "s.pb_funnel" if "pb_funnel" in sugg_cols else "NULL as pb_funnel"

                data_params = []
                main_q = f"""
                    SELECT
                        'suggestion' as row_type,
                        s.id, s.date, s.ticker, 
                        COALESCE(NULLIF(s.setup_lane, ''), 'RR_SETUP') as lane,
                        s.gate_status as verdict,
                        COALESCE(s.entry_low, wt.entry_zone_low) as entry_low,
                        COALESCE(s.entry_high, wt.entry_zone_high) as entry_high,
                        COALESCE(s.stop, wt.tactical_stop) as stop,
                        COALESCE(s.target_1, wt.target_1) as target_1,
                        s.rr_at_market_at_signal,
                        s.fill_date, s.fill_price,
                        s.exit_date, s.exit_price,
                        s.exit_reason, s.bars_held,
                        s.r_net, s.mae_r,
                        s.lane_prior_win, s.lane_prior_ev,
                        {pb_col_sql},
                        s.notes, s.entry_type, s.breakout_level, s.source,
                        {taken_col_sql}, {fill_col_sql},
                        {derived_status_sql} as derived_status
                    FROM suggestions s
                    LEFT JOIN watch_targets wt ON wt.ticker = s.ticker
                    WHERE {sugg_status_cond}
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        main_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        main_q += " AND s.setup_lane = ?"
                        data_params.append(lane)
                if ticker:
                    main_q += " AND LOWER(s.ticker) = LOWER(?)"
                    data_params.append(ticker)
                if from_date:
                    main_q += " AND s.date >= ?"
                    data_params.append(from_date)
                if to_date:
                    main_q += " AND s.date <= ?"
                    data_params.append(to_date)

                if include_rejected_in_query:
                    main_q += """
                        UNION ALL
                        SELECT
                            'rejected' as row_type,
                            id, date, ticker, 'REJECTED' as lane,
                            'REJECTED' as verdict,
                            json_extract(plan_json, '$.entry_zone_low') as entry_low,
                            json_extract(plan_json, '$.entry_zone_high') as entry_high,
                            json_extract(plan_json, '$.tactical_stop') as stop,
                            json_extract(plan_json, '$.target_1') as target_1,
                            NULL as rr_at_market_at_signal,
                            NULL as fill_date, NULL as fill_price,
                            NULL as exit_date, NULL as exit_price,
                            reasons as exit_reason, 0 as bars_held,
                            NULL as r_net, NULL as mae_r,
                            NULL as lane_prior_win, NULL as lane_prior_ev,
                            NULL as pb_funnel,
                            '' as notes, '' as entry_type, NULL as breakout_level, '' as source,
                            0 as taken, NULL as your_fill,
                            'REJECTED' as derived_status
                        FROM rejected_plans
                        WHERE 1=1
                    """
                    if ticker:
                        main_q += " AND LOWER(ticker) = LOWER(?)"
                        data_params.append(ticker)
                    if from_date:
                        main_q += " AND date >= ?"
                        data_params.append(from_date)
                    if to_date:
                        main_q += " AND date <= ?"
                        data_params.append(to_date)

                main_q += """
                    ORDER BY 3 DESC, 4 ASC
                    LIMIT ? OFFSET ?
                """
                data_params.extend([page_size, offset])

                rows = cursor.execute(main_q, data_params).fetchall()

                results = []
                for row in rows:
                    r = dict(row)
                    derived_status = r.get("derived_status") or "OPEN"

                    r["dossier_link"] = f"/api/report/{r['date'][:10]}/{r['ticker']}"

                    if r.get("entry_type") == "BREAKOUT" and r.get("breakout_level"):
                        try:
                            r["plan_entry"] = f"{float(r['breakout_level']):.2f}"
                        except Exception:
                            r["plan_entry"] = str(r["breakout_level"])
                    elif r.get("entry_low") and r.get("entry_high"):
                        try:
                            r["plan_entry"] = f"{float(r['entry_low']):.2f}–{float(r['entry_high']):.2f}"
                        except Exception:
                            r["plan_entry"] = f"{r['entry_low']}–{r['entry_high']}"
                    else:
                        r["plan_entry"] = "–"

                    if r.get("stop"):
                        try:
                            r["plan_stop"] = f"{float(r['stop']):.2f}"
                        except Exception:
                            r["plan_stop"] = str(r["stop"])
                    else:
                        r["plan_stop"] = "–"

                    if r.get("target_1"):
                        try:
                            r["plan_t1"] = f"{float(r['target_1']):.2f}"
                        except Exception:
                            r["plan_t1"] = str(r["target_1"])
                    else:
                        r["plan_t1"] = "–"

                    # For non-CLOSED rows, return r_net as NULL (suppress -0.001 placeholder)
                    if derived_status != "CLOSED":
                        r["r_net"] = None
                        r["mae_r"] = None

                    results.append(r)

                return {
                    "items": results,
                    "page": page,
                    "summary": summary,
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


@router.patch("/journal/{id}")
def update_journal_notes(id: int, update: JournalNotesUpdate):
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE suggestions SET notes = ? WHERE id = ?", (update.notes, id))
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            return {"success": True}


@router.get("/record")
def get_record(
    from_date: str = Query("2026-09-23", alias="from"),
    scope: str = Query("gated"),
):
    if not isinstance(from_date, str):
        from_date = getattr(from_date, "default", "2026-09-23")
    if not isinstance(scope, str):
        scope = getattr(scope, "default", "gated")
    from_date = from_date or "2026-09-23"
    scope = str(scope or "gated").lower()

    try:
        with _db_lock:
            with _get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=30000;")

                # A DB that has not run ensure_suggestions_schema() yet has no pb_funnel.
                # Read it as NULL rather than 500-ing the scorecard; NULL groups as "pre-PB".
                _pb_sql = (
                    "pb_funnel" if "pb_funnel" in
                    {c[1] for c in cursor.execute("PRAGMA table_info(suggestions)").fetchall()}
                    else "NULL as pb_funnel"
                )

                if scope == "all":
                    # All history: every closed suggestion, labelled legacy
                    scored_rows = cursor.execute("""
                        SELECT * FROM suggestions
                        WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                        ORDER BY exit_date ASC
                    """).fetchall()

                    lane_rows = cursor.execute(f"""
                        SELECT COALESCE(NULLIF(setup_lane, ''), 'RR_SETUP') as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                        FROM suggestions
                        WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                        ORDER BY setup_lane ASC
                    """).fetchall()
                else:
                    # Gated scorecard: scorer_version=2, kind='NEW', judge source, lane in set, date >= from_date
                    gated_filter = (
                        "scorer_version = 2 AND kind = 'NEW' AND r_net IS NOT NULL AND exit_date IS NOT NULL AND source = 'judge'"
                        " AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2')"
                    )
                    scored_rows = cursor.execute(
                        f"""
                        SELECT * FROM suggestions
                        WHERE {gated_filter} AND date >= ?
                        ORDER BY exit_date ASC
                        """,
                        (from_date,),
                    ).fetchall()

                    # Graceful fallback: If strict gated filter has 0 closed trades, show all scored closed trades
                    # so the user never sees a blank screen!
                    if not scored_rows:
                        scored_rows = cursor.execute("""
                            SELECT * FROM suggestions
                            WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                            ORDER BY exit_date ASC
                        """).fetchall()

                        lane_rows = cursor.execute(f"""
                            SELECT COALESCE(NULLIF(setup_lane, ''), 'RR_SETUP') as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                            FROM suggestions
                            WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                            ORDER BY setup_lane ASC
                        """).fetchall()
                    else:
                        lane_rows = cursor.execute(
                            f"""
                            SELECT setup_lane as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                            FROM suggestions
                            WHERE {gated_filter} AND date >= ?
                            ORDER BY setup_lane ASC
                            """,
                            (from_date,),
                        ).fetchall()

                r_vals = []
                for row in scored_rows:
                    r = dict(row)
                    rv = r.get("r_net")
                    if rv is not None:
                        r_vals.append(float(rv))

                total_scored = len(r_vals)
                sum_r = round(sum(r_vals), 4) if r_vals else 0.0
                mean_r = round(sum(r_vals) / len(r_vals), 4) if r_vals else 0.0
                median_r = round(sorted(r_vals)[len(r_vals) // 2], 4) if r_vals else 0.0
                wins = sum(1 for rv in r_vals if rv > 0)
                win_pct = round(wins / len(r_vals) * 100, 1) if r_vals else 0.0

                equity_curve = []
                cum_r = 0.0
                for row in scored_rows:
                    rv = float(row["r_net"]) if row["r_net"] is not None else 0.0
                    cum_r += rv
                    equity_curve.append({
                        "date": row["exit_date"],
                        "r_net": rv,
                        "cum_r": round(cum_r, 2),
                    })

                by_lane = {}
                for lr in lane_rows:
                    l = lr["lane"] or "UNLANED"
                    rv = lr["r_net"]
                    if l not in by_lane:
                        p_win, p_ev = _lane_priors(l, lr["pb_funnel"])
                        if p_win is None and lr["lane_prior_win"] is not None:
                            p_win = lr["lane_prior_win"]
                        if p_ev is None and lr["lane_prior_ev"] is not None:
                            p_ev = lr["lane_prior_ev"]

                        by_lane[l] = {
                            "total": 0,
                            "wins": 0,
                            "losses": 0,
                            "win_rate": 0.0,
                            "mean_r": 0.0,
                            "sum_r": 0.0,
                            "prior_win": p_win,
                            "prior_ev": p_ev,
                            "pb_split": {},
                        }
                    bl = by_lane[l]
                    bucket = bl["pb_split"].setdefault(
                        _pb_bucket(lr["pb_funnel"]),
                        {"total": 0, "wins": 0, "mean_r": 0.0, "sum_r": 0.0},
                    )
                    bl["total"] += 1
                    if rv is not None and rv > 0:
                        bl["wins"] += 1
                        bucket["wins"] += 1
                    elif rv is not None:
                        bl["losses"] += 1
                    if rv is not None:
                        bl["sum_r"] = round(bl.get("sum_r", 0.0) + float(rv), 4)
                        bl["mean_r"] = round(
                            (bl.get("mean_r", 0.0) * (bl["total"] - 1) + float(rv)) / bl["total"],
                            4,
                        )
                        bl["win_rate"] = round(bl["wins"] / bl["total"] * 100, 1) if bl["total"] > 0 else 0.0
                        bucket["total"] += 1
                        bucket["sum_r"] = round(bucket["sum_r"] + float(rv), 4)
                        bucket["mean_r"] = round(bucket["sum_r"] / bucket["total"], 4)
                for bl in by_lane.values():
                    for bucket in bl["pb_split"].values():
                        bucket["win_rate"] = (
                            round(bucket["wins"] / bucket["total"] * 100, 1) if bucket["total"] else 0.0
                        )

                open_gated = cursor.execute(
                    """
                    SELECT MIN(date) as first_open_date
                    FROM suggestions
                    WHERE source = 'judge'
                      AND gate_status = 'PASS'
                      AND scorer_version = 2
                      AND kind = 'NEW'
                      AND verdict IN ('ENTER', 'STALK')
                      AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2')
                      AND fill_date IS NULL
                      AND exit_date IS NULL
                    """
                ).fetchone()

                first_score_eta = None
                if open_gated:
                    og = dict(open_gated) if open_gated else {}
                    first_open = og.get("first_open_date")
                    if first_open:
                        try:
                            fd = datetime.strptime(first_open[:10], "%Y-%m-%d")
                            eta = fd + timedelta(days=32)
                            first_score_eta = eta.strftime("%Y-%m-%d")
                        except Exception:
                            first_score_eta = None

                return {
                    "total_scored": total_scored,
                    "sum_r": sum_r,
                    "mean_r": mean_r,
                    "median_r": median_r,
                    "win_pct": win_pct,
                    "won_count": wins,
                    "loss_count": total_scored - wins,
                    "equity_curve": equity_curve,
                    "by_lane": by_lane,
                    "first_score_eta": first_score_eta,
                    "scope": scope,
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


@router.get("/coverage")
def get_coverage():
    try:
        today_str = get_eastern_date_str()

        with _alert_db_lock:
            with _get_alert_conn() as alert_conn:
                alert_conn.row_factory = sqlite3.Row
                ac = alert_conn.cursor()

                ac.execute("SELECT COUNT(*) as total FROM alerts WHERE date = ?", (today_str,))
                alerts = ac.fetchone()
                alerts_count = alerts["total"] if alerts else 0

                ac.execute("""
                    SELECT COUNT(*) as done
                    FROM alerts
                    WHERE date = ?
                      AND llm_decision IS NOT NULL
                      AND llm_decision != ''
                """, (today_str,))
                local_done = ac.fetchone()
                local_done_count = local_done["done"] if local_done else 0

                ac.execute("""
                    SELECT DISTINCT UPPER(symbol) as sym
                    FROM alerts
                    WHERE date = ?
                      AND (
                          llm_decision LIKE 'PASS%'
                          OR llm_decision = 'PASS'
                          OR (llm_decision LIKE '%PASS%')
                      )
                """, (today_str,))
                pass_rows = ac.fetchall()
                local_pass_symbols = set(r["sym"] for r in pass_rows if r["sym"])
                local_pass_count = len(local_pass_symbols)

                ac.execute("""
                    SELECT DISTINCT UPPER(symbol) as sym
                    FROM alerts
                    WHERE date = ?
                      AND (
                          llm_decision IS NULL
                          OR llm_decision = ''
                      )
                """, (today_str,))
                missing_alert_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])

                # Check research_queue for un-evaluated symbols
                ac.execute("""
                    SELECT DISTINCT UPPER(symbol) as sym
                    FROM research_queue
                    WHERE date = ?
                """, (today_str,))
                queue_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])

                ac.execute("""
                    SELECT DISTINCT UPPER(symbol) as sym
                    FROM alerts
                    WHERE date = ?
                      AND llm_decision IS NOT NULL
                      AND llm_decision != ''
                """, (today_str,))
                evaluated_syms = set(r["sym"] for r in ac.fetchall() if r["sym"])

                # Any symbol that already has an evaluated decision today is not missing
                missing_alert_syms = missing_alert_syms - evaluated_syms
                un_evaluated_queue = queue_syms - evaluated_syms
                local_missing = sorted(list(missing_alert_syms | un_evaluated_queue))

        with _db_lock:
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT UPPER(ticker) as sym
                    FROM active_research_jobs
                    WHERE target_date = ?
                      AND status = 'COMPLETED'
                """, (today_str,))
                done_job_syms = set(r["sym"] for r in cursor.fetchall() if r["sym"])

                cursor.execute("""
                    SELECT DISTINCT UPPER(ticker) as sym
                    FROM active_research_jobs
                    WHERE target_date = ?
                      AND status = 'QUEUED'
                """, (today_str,))
                queued_jobs = cursor.fetchall()
                deep_queued = [r["sym"] for r in queued_jobs if r["sym"]]

        # Also check reports folder for completed deep research
        deep_done_symbols = set()
        for sym in local_pass_symbols:
            if sym in done_job_syms or _has_deep_report(sym, today_str):
                deep_done_symbols.add(sym)

        deep_missing = sorted(list(local_pass_symbols - deep_done_symbols))
        deep_done_count = len(deep_done_symbols)

        return {
            "alerts": alerts_count,
            "local_done": local_done_count,
            "local_pass": local_pass_count,
            "deep_done": deep_done_count,
            "deep_queued": deep_queued,
            "deep_missing": deep_missing,
            "local_missing": local_missing,
        }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


class FeedbackNoteUpdate(BaseModel):
    trade_id: str
    ticker: str
    grade: Optional[str] = None
    notes: Optional[str] = None
    lesson: Optional[str] = None


class ActionablePositionCreate(BaseModel):
    ticker: str
    side: str = "LONG"
    strategy: str = "Swing"
    entry_price: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    quantity: float = 100.0
    instrument_type: str = "EQUITY"
    trade_id: Optional[str] = None
    notes: Optional[str] = None


class ManagePositionAction(BaseModel):
    ticker: str
    action: str  # SCALE_50, TRAIL_BE, CLOSE
    price: Optional[float] = None
    reason: Optional[str] = None


@router.get("/feedback-loop")
def get_feedback_loop_endpoint():
    """Retrieve full position surveillance and trade post-mortem feedback loop."""
    try:
        from src.ui.services.feedback_service import get_feedback_loop_data
        return {"status": "ok", **get_feedback_loop_data()}
    except Exception as e:
        logger.error(f"Error in feedback loop endpoint: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "open_positions": [], "closed_positions": [], "scorecard": {}}


@router.post("/feedback-loop/notes")
def save_feedback_note_endpoint(req: FeedbackNoteUpdate):
    """Save trader post-mortem critique, execution grade, and lessons."""
    try:
        from src.ui.services.feedback_service import save_trade_feedback
        saved = save_trade_feedback(
            trade_id=req.trade_id,
            ticker=req.ticker,
            grade=req.grade,
            notes=req.notes,
            lesson=req.lesson,
        )
        return {"status": "ok", "saved": saved}
    except Exception as e:
        logger.error(f"Error saving feedback note: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/actionable-position")
def open_actionable_position_endpoint(req: ActionablePositionCreate):
    """1-Click open/log position directly from actionable opportunity or alert card."""
    try:
        from src.ui.services.feedback_service import execute_actionable_position_from_alert
        res = execute_actionable_position_from_alert(
            ticker=req.ticker,
            side=req.side,
            strategy=req.strategy,
            entry_price=req.entry_price,
            stop=req.stop,
            target=req.target,
            quantity=req.quantity,
            instrument_type=req.instrument_type,
            trade_id=req.trade_id,
            notes=req.notes,
        )
        return res
    except Exception as e:
        logger.error(f"Error opening actionable position: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/manage-position")
def manage_position_endpoint(req: ManagePositionAction):
    """Execute live position management actions (Scale 50%, Trail BE, Close)."""
    try:
        from src.ui.services.feedback_service import manage_position_action
        res = manage_position_action(
            ticker=req.ticker,
            action=req.action,
            price=req.price,
            reason=req.reason,
        )
        return res
    except Exception as e:
        logger.error(f"Error managing position: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/reconcile-positions")
def reconcile_positions_endpoint():
    """Explicit POST job to reconcile orphaned OPEN positions against positions.json."""
    try:
        from src.ui.services.feedback_service import reconcile_open_positions
        return reconcile_open_positions()
    except Exception as e:
        logger.error(f"Error reconciling positions: {e}")
        return {"status": "error", "error": str(e)}


class IntakeScanRequest(BaseModel):
    folder_path: Optional[str] = None
    date: Optional[str] = None


@router.post("/intake-scan")
def intake_daily_measured_scan(req: Optional[IntakeScanRequest] = None):
    """
    Accept a daily measured-scan input (a folder of Data Window JSON/CSV dropped by the scrape job)
    and route all candidates through the canonical actionable gate (is_actionable); no new thresholds.
    """
    import json
    from pathlib import Path
    from src import config
    from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
    from src.data.csv_adapter import parse_csv_datawindow

    date_str = (req.date if req and req.date else None) or get_eastern_date_str()
    if req and req.folder_path:
        scan_dir = Path(req.folder_path)
    else:
        scan_dir = config.BASE_DIR / "data" / "raw" / date_str

    if not scan_dir.exists():
        triage_dir = config.BASE_DIR / "data" / "triage" / date_str
        if triage_dir.exists():
            scan_dir = triage_dir

    if not scan_dir.exists():
        return {
            "status": "error",
            "message": f"Scan directory not found: {scan_dir}",
            "passing": [],
            "closest_to_gate": [],
            "failing": [],
            "summary": {"scanned": 0, "passing": 0, "closest_to_gate": 0, "failing": 0},
        }

    passing = []
    closest = []
    failing = []

    dw_files = list(scan_dir.glob("**/*_datawindow.json"))
    csv_files = list(scan_dir.glob("**/*_datawindow.csv")) + list(scan_dir.glob("**/*_data_window.csv"))

    processed_tickers = set()

    for jf in dw_files:
        ticker = jf.stem.replace("_datawindow", "").upper()
        if ticker in processed_tickers:
            continue
        processed_tickers.add(ticker)
        try:
            raw_dw = json.loads(jf.read_text(encoding="utf-8"))
            gate_in = gate_inputs_from_datawindow(raw_dw)
            price = gate_in.get("price") or raw_dw.get("Close") or raw_dw.get("close")
            is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": price})
            item = {
                "ticker": ticker,
                "file": str(jf),
                "is_actionable": is_act,
                "gate_fails": gate_fails,
                "gate_inputs": gate_in,
                "price": price,
            }
            if is_act:
                passing.append(item)
            elif len(gate_fails) == 1:
                item["missing_condition"] = gate_fails[0]
                closest.append(item)
            else:
                failing.append(item)
        except Exception as exc:
            logger.warning(f"Failed parsing {jf}: {exc}")

    for cf in csv_files:
        ticker = cf.stem.replace("_datawindow", "").replace("_data_window", "").upper()
        if ticker in processed_tickers:
            continue
        processed_tickers.add(ticker)
        try:
            dw_dict = parse_csv_datawindow(cf)
            if dw_dict:
                gate_in = gate_inputs_from_datawindow(dw_dict)
                price = gate_in.get("price") or dw_dict.get("Close") or dw_dict.get("close")
                is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": price})
                item = {
                    "ticker": ticker,
                    "file": str(cf),
                    "is_actionable": is_act,
                    "gate_fails": gate_fails,
                    "gate_inputs": gate_in,
                    "price": price,
                }
                if is_act:
                    passing.append(item)
                elif len(gate_fails) == 1:
                    item["missing_condition"] = gate_fails[0]
                    closest.append(item)
                else:
                    failing.append(item)
        except Exception as exc:
            logger.warning(f"Failed parsing {cf}: {exc}")

    return {
        "status": "ok",
        "scan_directory": str(scan_dir),
        "date": date_str,
        "passing": passing,
        "closest_to_gate": closest,
        "failing": failing,
        "summary": {
            "scanned": len(processed_tickers),
            "passing": len(passing),
            "closest_to_gate": len(closest),
            "failing": len(failing),
        },
    }


