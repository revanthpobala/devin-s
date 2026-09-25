from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desk", tags=["desk"])


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


def _has_deep_report(ticker: str, date_str: str) -> bool:
    """Check if a deep research report already exists for ticker on date."""
    from pathlib import Path
    from src import config
    ticker = ticker.upper()
    base = config.BASE_DIR / "reports" / date_str
    if not base.exists():
        return False
    return (base / f"{ticker}_summary.md").exists() or (base / f"{ticker}_arbitration.md").exists()


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
                    atr_at_signal = r.get("atr_at_signal")
                    if atr_at_signal and atr_at_signal > 0 and entry_h and stop:
                        r["stop_width_atr"] = round(abs(entry_h - stop) / atr_at_signal, 2)
                    else:
                        r["stop_width_atr"] = None

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

                    status = r.get("status") or "STALKING"
                    dist = r.get("dist")
                    near = dist is not None and abs(float(dist)) <= 1.5

                    if not r.get("llm_decision"):
                        continue

                    local_dec = (local.get("llm_decision") or "").upper()
                    deep_status = r.get("deep_status") or "none"
                    r["setup"] = local.get("setup") or r.get("setup") or ""
                    r["llm_playbook"] = local.get("llm_playbook") or r.get("llm_playbook") or ""

                    if "PASS" in local_dec:
                        if has_real_deep and deep_status == "COMPLETED":
                            if status in ("IN_ZONE", "IN_TRADE") or near:
                                actionable.append(r)
                            else:
                                stalking.append(r)
                        else:
                            needs_you.append(r)
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
                    }

                    if "PASS" in local_dec:
                        needs_you.append(item)
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
                    inbox.append({
                        "ticker": local.get("ticker") or sym.upper(),
                        "date": local.get("date") or today_str,
                        "setup": local.get("setup") or "",
                        "llm_decision": local.get("llm_decision") or "",
                        "local_score": score_val,
                        "llm_playbook": (local.get("llm_playbook") or "")[:120],
                        "deep_status": ib_deep_status,
                        "gate_status": s_match["gate_status"] if s_match else None,
                        "suggestion_id": s_match["id"] if s_match else None,
                        "last_price": (s_match["last_price"] if s_match else None) or local.get("alert_price") or local.get("market_price"),
                    })

                actionable.sort(key=lambda x: (
                    1 if x.get("status") == "IN_TRADE" else
                    2 if x.get("status") == "IN_ZONE" else 3,
                    -(x.get("live_rr") if x.get("live_rr") is not None else -9999.0)
                ))

                return {
                    "found": found,
                    "inbox": inbox,
                    "actionable": actionable,
                    "stalking": stalking,
                    "watch": watch_list,
                    "cut": cut_list,
                    "needs_you": needs_you,
                    "missing_symbols": list(missing_symbols),
                    "coverage": {
                        "found_count": len(found_rows) if found_rows else 0,
                        "inbox_count": len(inbox),
                        "actionable_count": len(actionable),
                        "stalking_count": len(stalking),
                        "watch_count": len(watch_list),
                        "cut_count": len(cut_list),
                        "needs_you_count": len(needs_you),
                        "missing_symbols_count": len(missing_symbols),
                    },
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


PRIORS_MAP = {
    "RR_SETUP_STRONG": (25.0, 0.13),
    "RR_SETUP": (30.0, 0.08),
    "CODE20": (45.0, 0.08),
    "OVERSOLD": (51.0, 0.06),
    "RSI2": (61.0, 0.06),
}


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
                    " WHEN exit_date IS NOT NULL THEN 'CLOSED'"
                    " WHEN fill_date IS NOT NULL THEN 'FILLED'"
                    " WHEN date >= date('now', '-30 days') THEN 'OPEN'"
                    " ELSE 'EXPIRED'"
                    " END"
                )

                # Build SQL conditions for status filter
                sugg_status_cond = "1=1"
                include_rejected_in_query = False

                if status_upper == "CLOSED":
                    sugg_status_cond = "exit_date IS NOT NULL"
                elif status_upper == "FILLED":
                    sugg_status_cond = "fill_date IS NOT NULL AND exit_date IS NULL"
                elif status_upper == "OPEN":
                    sugg_status_cond = "fill_date IS NULL AND exit_date IS NULL AND date >= date('now', '-30 days')"
                elif status_upper == "EXPIRED":
                    sugg_status_cond = "fill_date IS NULL AND exit_date IS NULL AND date < date('now', '-30 days')"
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
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND r_net > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND r_net IS NOT NULL THEN r_net ELSE 0 END) as sum_r,
                        AVG(CASE WHEN {derived_status_sql} = 'CLOSED' AND r_net IS NOT NULL THEN r_net ELSE NULL END) as mean_r
                    FROM suggestions
                    WHERE 1=1
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        summary_q += " AND (setup_lane IS NULL OR setup_lane = '')"
                    else:
                        summary_q += " AND setup_lane = ?"
                        summary_params.append(lane)
                if ticker:
                    summary_q += " AND LOWER(ticker) = LOWER(?)"
                    summary_params.append(ticker)
                if from_date:
                    summary_q += " AND date >= ?"
                    summary_params.append(from_date)
                if to_date:
                    summary_q += " AND date <= ?"
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
                    SELECT r_net FROM suggestions
                    WHERE {derived_status_sql} = 'CLOSED' AND r_net IS NOT NULL
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        med_q += " AND (setup_lane IS NULL OR setup_lane = '')"
                    else:
                        med_q += " AND setup_lane = ?"
                        med_params.append(lane)
                if ticker:
                    med_q += " AND LOWER(ticker) = LOWER(?)"
                    med_params.append(ticker)
                if from_date:
                    med_q += " AND date >= ?"
                    med_params.append(from_date)
                if to_date:
                    med_q += " AND date <= ?"
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

                data_params = []
                main_q = f"""
                    SELECT
                        'suggestion' as row_type,
                        id, date, ticker, setup_lane as lane,
                        gate_status as verdict,
                        entry_low, entry_high, stop, target_1,
                        rr_at_market_at_signal,
                        fill_date, fill_price,
                        exit_date, exit_price,
                        exit_reason, bars_held,
                        r_net, mae_r,
                        lane_prior_win, lane_prior_ev,
                        notes, entry_type, breakout_level, source,
                        {derived_status_sql} as derived_status
                    FROM suggestions
                    WHERE {sugg_status_cond}
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        main_q += " AND (setup_lane IS NULL OR setup_lane = '')"
                    else:
                        main_q += " AND setup_lane = ?"
                        data_params.append(lane)
                if ticker:
                    main_q += " AND LOWER(ticker) = LOWER(?)"
                    data_params.append(ticker)
                if from_date:
                    main_q += " AND date >= ?"
                    data_params.append(from_date)
                if to_date:
                    main_q += " AND date <= ?"
                    data_params.append(to_date)

                if include_rejected_in_query:
                    main_q += """
                        UNION ALL
                        SELECT
                            'rejected' as row_type,
                            id, date, ticker, '' as lane,
                            'REJECTED' as verdict,
                            NULL as entry_low, NULL as entry_high,
                            NULL as stop, NULL as target_1,
                            NULL as rr_at_market_at_signal,
                            NULL as fill_date, NULL as fill_price,
                            NULL as exit_date, NULL as exit_price,
                            reasons as exit_reason, 0 as bars_held,
                            NULL as r_net, NULL as mae_r,
                            NULL as lane_prior_win, NULL as lane_prior_ev,
                            '' as notes, '' as entry_type, NULL as breakout_level, '' as source,
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
                    ORDER BY date DESC, ticker ASC
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

                if scope == "all":
                    # All history: every closed suggestion, labelled legacy
                    scored_rows = cursor.execute("""
                        SELECT * FROM suggestions
                        WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                        ORDER BY exit_date ASC
                    """).fetchall()

                    lane_rows = cursor.execute("""
                        SELECT setup_lane as lane, r_net, lane_prior_win, lane_prior_ev
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

                    lane_rows = cursor.execute(
                        f"""
                        SELECT setup_lane as lane, r_net, lane_prior_win, lane_prior_ev
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
                        p_win, p_ev = PRIORS_MAP.get(l, (None, None))
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
                        }
                    bl = by_lane[l]
                    bl["total"] += 1
                    if rv is not None and rv > 0:
                        bl["wins"] += 1
                    elif rv is not None:
                        bl["losses"] += 1
                    if rv is not None:
                        bl["sum_r"] = round(bl.get("sum_r", 0.0) + float(rv), 4)
                        bl["mean_r"] = round(
                            (bl.get("mean_r", 0.0) * (bl["total"] - 1) + float(rv)) / bl["total"],
                            4,
                        )
                        bl["win_rate"] = round(bl["wins"] / bl["total"] * 100, 1) if bl["total"] > 0 else 0.0

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
                          OR (
                              llm_decision NOT LIKE '%PASS%'
                              AND llm_decision NOT LIKE '%WATCH%'
                              AND llm_decision NOT LIKE '%CUT%'
                          )
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
