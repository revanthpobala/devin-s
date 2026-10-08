"""
Research dossier viewing, report listing, chart streaming, live quotes, options flow, and asynchronous research queue execution.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import sqlite3

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from src import config
from src.data.datawindow_loader import load_datawindow, find_datawindow_paths
from src.ui.services.research_queue import (
    MAX_CONCURRENT_DEEP,
    MAX_CONCURRENT_FULL,
    MAX_CONCURRENT_MID,
    MAX_CONCURRENT_LOCAL,
    dispatch_next_queued_job,
    find_live_research_pid,
    get_active_deep_research_count,
    get_active_full_research_count,
    get_active_mid_research_count,
    get_active_local_research_count,
    get_active_research_count,
    run_research_worker,
)
from src.ui.state import (
    ACTIVE_RESEARCH_SUBPROCS,
    ACTIVE_RESEARCH_WORKERS,
    LOGS_DIR,
    MAX_CONCURRENT_RESEARCH,
    append_log,
    get_db,
    init_db,
)

logger = logging.getLogger("ui_server")

router = APIRouter(tags=["research"])

_REPORT_BUNDLE_CACHE: Dict[str, tuple[float, dict]] = {}
_REPORT_CACHE_TTL = 300  # 5 minutes TTL

TICKER_REGEX = re.compile(r"^[A-Z0-9]{1,6}(?:[./-][A-Z0-9]{1,2})?$")


def validate_ticker(ticker: str) -> str:
    ticker_u = (ticker or "").strip().upper()
    if not TICKER_REGEX.match(ticker_u):
        raise HTTPException(status_code=400, detail=f"Invalid ticker symbol: {ticker!r}")
    return ticker_u


def extract_report_card(date: str, ticker: str) -> Dict[str, Any]:
    """Extract structured levels and PM verdict metadata for a research report."""
    ticker_u = ticker.upper()
    rep_dir = config.BASE_DIR / "reports" / date

    # 1. Search for watch_levels.json across all known locations (raw, triage, reports)
    levels_candidates = [
        config.BASE_DIR / "data" / "raw" / date / ticker_u / f"{ticker_u}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / f"{ticker_u}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_watch_levels.json",
        rep_dir / f"{ticker_u}_watch_levels.json",
    ]

    levels_data = {}
    for cand in levels_candidates:
        if cand.exists():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    levels_data = json.load(f)
                    if levels_data:
                        break
            except Exception:
                pass

    # 2. Check SQLite watch_targets database as fallback or complement
    db_target = None
    try:
        with get_db() as conn:
            row = conn.cursor().execute(
                "SELECT * FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?) ORDER BY date DESC LIMIT 1",
                (ticker_u, date, f"{date}%")
            ).fetchone()
            if not row:
                row = conn.cursor().execute(
                    "SELECT * FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (ticker_u,)
                ).fetchone()
            if row:
                db_target = dict(row)
    except Exception:
        pass

    # 3. Fallback scan markdown files for embedded json:watch_levels if still missing
    if not levels_data:
        for md_cand in [rep_dir / f"{ticker_u}_arbitration.md", rep_dir / f"{ticker_u}_summary.md"]:
            if md_cand.exists():
                try:
                    txt = md_cand.read_text(encoding="utf-8")
                    m = re.search(r'```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?"shares_plan"[\s\S]*?\})\s*```', txt)
                    if m:
                        levels_data = json.loads(m.group(1))
                        break
                except Exception:
                    pass

    # 4. Extract structured fields robustly from nested plans or DB row
    shares_plan = levels_data.get("shares_plan") or {}
    options_plan = levels_data.get("options_plan") or {}
    invalidation = levels_data.get("invalidation") or {}

    # Entry zone [low, high]
    ez_low = shares_plan.get("entry_zone_low")
    ez_high = shares_plan.get("entry_zone_high")
    if ez_low is None and db_target:
        ez_low = db_target.get("entry_zone_low")
    if ez_high is None and db_target:
        ez_high = db_target.get("entry_zone_high")

    if ez_low is not None and ez_high is not None:
        try:
            entry_zone = [float(ez_low), float(ez_high)]
        except (ValueError, TypeError):
            entry_zone = []
    elif levels_data.get("entry_zone"):
        entry_zone = levels_data["entry_zone"]
    else:
        entry_zone = []

    # Tactical stop
    stop = (
        shares_plan.get("tactical_stop")
        or levels_data.get("tactical_stop")
        or (db_target.get("tactical_stop") if db_target else None)
        or invalidation.get("price_level")
    )
    if stop is not None:
        try:
            stop = float(stop)
        except (ValueError, TypeError):
            stop = None

    # Targets
    t1 = shares_plan.get("target_1") or levels_data.get("target_1") or (db_target.get("target_1") if db_target else None)
    if t1 is not None:
        try:
            t1 = float(t1)
        except (ValueError, TypeError):
            t1 = None

    t2 = shares_plan.get("target_2") or levels_data.get("target_2") or (db_target.get("target_2") if db_target else None)
    if t2 is not None:
        try:
            t2 = float(t2)
        except (ValueError, TypeError):
            t2 = None

    # Options summary
    options_summary = (
        options_plan.get("summary")
        or levels_data.get("options_summary")
        or (db_target.get("options_summary") if db_target else "")
        or ""
    )
    if not options_summary and options_plan.get("structure"):
        options_summary = f"{options_plan.get('structure')} (Exp: {options_plan.get('expiration', 'N/A')})"

    # Verdict & conviction
    verdict = levels_data.get("verdict") or (db_target.get("verdict") if db_target else None)
    if not verdict or verdict == "ANALYZED":
        arb_file = rep_dir / f"{ticker_u}_arbitration.md"
        if arb_file.exists():
            try:
                txt = arb_file.read_text(encoding="utf-8")
                if "ENTER (Options Credit)" in txt:
                    verdict = "ENTER (Credit Spread)"
                elif "ENTER (Long Call)" in txt:
                    verdict = "ENTER (Call Debit)"
                elif "ENTER" in txt:
                    verdict = "ENTER"
                elif "WATCH" in txt:
                    verdict = "WATCH"
                elif "AVOID" in txt:
                    verdict = "AVOID"
            except Exception:
                pass
    if not verdict:
        verdict = "ANALYZED"

    conviction = levels_data.get("conviction") or (db_target.get("conviction") if db_target else None) or 5

    # Researched timestamp (mtime of report file)
    researched_at = None
    for cand_f in [rep_dir / f"{ticker_u}_summary.md", rep_dir / f"{ticker_u}_arbitration.md", rep_dir / f"{ticker_u}_independent.md"]:
        if cand_f.exists():
            try:
                researched_at = datetime.fromtimestamp(cand_f.stat().st_mtime).isoformat()
                break
            except Exception:
                pass

    return {
        "ticker": ticker_u,
        "date": date,
        "verdict": verdict,
        "conviction": conviction,
        "options_summary": options_summary,
        "entry_zone": entry_zone,
        "tactical_stop": stop,
        "target_1": t1,
        "target_2": t2,
        "researched_at": researched_at,
        "has_summary": (rep_dir / f"{ticker_u}_summary.md").exists(),
        "has_arbitration": (rep_dir / f"{ticker_u}_arbitration.md").exists(),
        "has_independent": (rep_dir / f"{ticker_u}_independent.md").exists(),
    }


def find_chart_path(date: str, ticker: str, chart_type: str) -> Optional[Path]:
    """Find chart image across raw and triage directories with automatic fallback to most recent date."""
    ticker_u = ticker.upper()
    fname = f"{ticker_u}_chart_zoom.png" if chart_type == "zoom" else f"{ticker_u}_chart_plain.png"
    fallback_fname = f"{ticker_u}_chart.png"

    # 1. Check specified date first
    if date:
        candidates = [
            config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / fname,
            config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / fname,
            config.BASE_DIR / "data" / "raw" / date / ticker_u / fname,
            config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / fallback_fname,
            config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / fallback_fname,
            config.BASE_DIR / "data" / "raw" / date / ticker_u / fallback_fname,
        ]
        for p in candidates:
            if p.exists():
                return p

    # 2. Search across all recent dates for this ticker (newest first)
    raw_root = config.BASE_DIR / "data" / "raw"
    if raw_root.exists():
        matches = sorted(list(raw_root.glob(f"202*/{ticker_u}/{fname}")), reverse=True)
        if matches:
            return matches[0]
        fallback_matches = sorted(list(raw_root.glob(f"202*/{ticker_u}/{fallback_fname}")), reverse=True)
        if fallback_matches:
            return fallback_matches[0]

    return None


@router.get("/api/research/queue")
def get_research_queue(date: Optional[str] = None):
    """
    Returns candidate tickers that need scraping or are ready for deep research:
    - Scraped candidates (charts & datawindow present, no deep research report yet).
    - Screener survivors from today's survivors.json.
    - Desk priority mega-caps ready to launch.
    """
    try:
        now_date = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")

        target_date = date.strip() if (date and date.strip()) else now_date
        raw_root = config.BASE_DIR / "data" / "raw" / target_date
        rep_root = config.BASE_DIR / "reports" / target_date

        # If date wasn't explicitly requested and now_date has no folder on disk, find latest date
        if not (date and date.strip()) and not raw_root.exists():
            raw_base = config.BASE_DIR / "data" / "raw"
            if raw_base.exists():
                avail_dates = sorted(
                    [d.name for d in raw_base.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
                    reverse=True,
                )
                if avail_dates:
                    target_date = avail_dates[0]
                    raw_root = config.BASE_DIR / "data" / "raw" / target_date
                    rep_root = config.BASE_DIR / "reports" / target_date

        queue = []
        seen = set()

        # 1. Check target_date triage PASS or force candidates
        triage_dir = config.BASE_DIR / "data" / "triage" / target_date
        triage_pass_or_force = set()
        for sub in ("_DEEP_RESEARCH", "force"):
            s_dir = triage_dir / sub
            if s_dir.exists():
                for item in s_dir.iterdir():
                    if item.is_dir():
                        triage_pass_or_force.add(item.name.upper())

        # Also inspect raw_root thesis files if available
        if raw_root.exists():
            for item in raw_root.iterdir():
                if item.is_dir():
                    sym = item.name.upper()
                    th_file = item / f"{sym}_thesis.json"
                    if th_file.exists():
                        try:
                            th_data = json.loads(th_file.read_text(encoding="utf-8"))
                            triage_dict = th_data.get("triage", {})
                            send_flag = (
                                th_data.get("send_for_deep_research") is True
                                or th_data.get("llm_data", {}).get("send_for_deep_research") is True
                                or (isinstance(triage_dict, dict) and triage_dict.get("send_for_deep_research") is True)
                            )
                            is_pass = (isinstance(triage_dict, dict) and triage_dict.get("triage") == "PASS") or th_data.get("triage") == "PASS"
                            if is_pass and send_flag:
                                triage_pass_or_force.add(sym)
                        except Exception:
                            pass

        if raw_root.exists():
            for item in raw_root.iterdir():
                if item.is_dir():
                    sym = item.name.upper()
                    has_dw = (item / f"{sym}_datawindow.json").exists() or (item / f"{sym}_datawindow.csv").exists()
                    has_chart = (item / f"{sym}_chart.png").exists() or (item / f"{sym}_chart_zoom.png").exists()
                    has_report = rep_root.exists() and (rep_root / f"{sym}_arbitration.md").exists()

                    if has_dw and not has_report and sym in triage_pass_or_force:
                        queue.append({
                            "ticker": sym,
                            "date": target_date,
                            "status": "READY_FOR_RESEARCH",
                            "action": "deep_only",
                            "action_label": "⚡ Run Deep Research",
                            "reason": "Triage PASS / Force candidate",
                            "has_chart": has_chart,
                            "has_report": False,
                        })
                        seen.add(sym)
                    elif has_report:
                        seen.add(sym)

        # Also inspect triage subdirectories (_DEEP_RESEARCH and force)
        for sub_name in ["_DEEP_RESEARCH", "force"]:
            t_sub = triage_dir / sub_name
            if t_sub.exists():
                for item in t_sub.iterdir():
                    if item.is_dir():
                        sym = item.name.upper()
                        if sym in seen:
                            continue
                        has_dw = (item / f"{sym}_datawindow.json").exists() or (item / f"{sym}_datawindow.csv").exists()
                        has_chart = (item / f"{sym}_chart.png").exists() or (item / f"{sym}_chart_zoom.png").exists()
                        has_report = rep_root.exists() and (rep_root / f"{sym}_arbitration.md").exists()
                        if has_dw and not has_report:
                            queue.append({
                                "ticker": sym,
                                "date": target_date,
                                "status": "READY_FOR_RESEARCH",
                                "action": "deep_only",
                                "action_label": "⚡ Run Deep Research",
                                "reason": f"Triage {sub_name} candidate",
                                "has_chart": has_chart,
                                "has_report": False,
                            })
                            seen.add(sym)
                        elif has_report:
                            seen.add(sym)

        # 2. Check survivors.json for target_date
        surv_file = raw_root / "survivors.json" if raw_root.exists() else None
        if surv_file and surv_file.exists():
            try:
                survs = json.loads(surv_file.read_text(encoding="utf-8"))
                for s in survs:
                    sym = (s.get("Symbol") or s.get("Ticker") or "").upper()
                    if sym and sym not in seen:
                        queue.append({
                            "ticker": sym,
                            # target_date, not now_date: asking for a past session must label its
                            # candidates with that session, or the per-date view mixes in today.
                            "date": target_date,
                            "status": "NEEDS_SCRAPE",
                            "action": "full",
                            "action_label": "📸 Scrape & Research",
                            "reason": "Screener Survivor",
                            "has_chart": False,
                            "has_report": False,
                        })
                        seen.add(sym)
            except Exception:
                pass

        return {"date": target_date, "queue": queue}
    except Exception as e:
        logger.error(f"Error in get_research_queue: {e}")
        return {"date": "", "queue": [], "error": str(e)}


@router.get("/api/reports")
def list_available_reports(date: Optional[str] = None):
    """List all available research reports grouped by date with rich summary metadata."""
    reports_dir = config.BASE_DIR / "reports"
    if not reports_dir.exists():
        return {"dates": [], "reports_by_date": {}, "date_labels": {}}

    all_dates = []
    reports_by_date = {}
    date_labels = {}
    today_str = datetime.now().strftime("%Y-%m-%d")

    for d in sorted(reports_dir.iterdir(), reverse=True):
        if d.is_dir():
            date_str = d.name
            tickers = set()
            for f in d.glob("*_summary.md"):
                t = f.name.replace("_summary.md", "")
                tickers.add(t)
            if tickers:
                all_dates.append(date_str)
                if not date or date == date_str or len(reports_by_date) < 5:
                    cards = [extract_report_card(date_str, t) for t in sorted(list(tickers))]
                    reports_by_date[date_str] = cards

    if all_dates:
        latest = all_dates[0]
        rep_d = reports_dir / latest
        has_today = any(
            datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d") == today_str
            for f in rep_d.glob("*.md")
        )
        for d_str in all_dates:
            if d_str == latest:
                if has_today and latest != today_str:
                    date_labels[d_str] = f"📅 {d_str} (Latest Session · Run Today)"
                else:
                    date_labels[d_str] = f"📅 {d_str} (Latest Session)"
            elif d_str == today_str:
                date_labels[d_str] = f"📅 {d_str} (Today)"
            else:
                date_labels[d_str] = f"📅 {d_str}"

    return {
        "dates": all_dates,
        "selected_date": date or (all_dates[0] if all_dates else None),
        "reports_by_date": reports_by_date,
        "date_labels": date_labels,
    }


def _build_local_research_dossier(target_date: str, ticker_u: str) -> tuple[Optional[str], Optional[dict]]:
    """Synthesize a complete Local Research Dossier from alerts, triage thesis, and suggestions."""
    thesis_data = None
    triage_base = config.BASE_DIR / "data" / "triage" / target_date
    raw_base = config.BASE_DIR / "data" / "raw" / target_date / ticker_u
    candidates = [
        triage_base / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / "force" / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / "pass" / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / "watch" / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / "cut" / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / ticker_u / f"{ticker_u}_thesis.json",
        triage_base / f"{ticker_u}_thesis.json",
        raw_base / f"{ticker_u}_thesis.json",
        raw_base / f"{ticker_u}_triage.json",
        config.BASE_DIR / "data" / "raw" / target_date / f"{ticker_u}_triage.json",
        triage_base / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_triage.json",
        triage_base / "force" / ticker_u / f"{ticker_u}_triage.json",
        triage_base / f"{ticker_u}_triage.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    thesis_data = json.load(f)
                    if thesis_data:
                        break
            except Exception:
                pass

    if not thesis_data:
        # Check consolidated results
        cons_p = config.BASE_DIR / "data" / "raw" / target_date / "consolidate" / "consolidated_results.json"
        if cons_p.exists():
            try:
                with open(cons_p, "r", encoding="utf-8") as f:
                    c_all = json.load(f)
                    if isinstance(c_all, dict) and ticker_u in c_all:
                        thesis_data = c_all[ticker_u]
            except Exception:
                pass

    if not thesis_data:
        try:
            for p in sorted(config.BASE_DIR.glob(f"data/triage/*/**/{ticker_u}_thesis.json"), reverse=True):
                with open(p, "r", encoding="utf-8") as f:
                    thesis_data = json.load(f)
                    if thesis_data:
                        break
        except Exception:
            pass

    if not thesis_data:
        try:
            for p in sorted(config.BASE_DIR.glob(f"data/raw/*/{ticker_u}/{ticker_u}_triage.json"), reverse=True):
                with open(p, "r", encoding="utf-8") as f:
                    thesis_data = json.load(f)
                    if thesis_data:
                        break
        except Exception:
            pass

    alert_row = None
    suggestion_row = None
    try:
        from src.tracking.alert_db import _get_connection as _get_alert_conn, _db_lock as _alert_db_lock
        with _alert_db_lock:
            with _get_alert_conn() as acon:
                ac = acon.cursor()
                ac.execute("""
                    SELECT * FROM alerts
                    WHERE (UPPER(symbol) = ? OR LOWER(symbol) = ?)
                      AND (date = ? OR ? = '' OR ? = 'latest')
                      AND llm_decision IS NOT NULL
                      AND llm_decision != ''
                    ORDER BY date DESC, rowid DESC
                    LIMIT 1
                """, (ticker_u, ticker_u.lower(), target_date, target_date, target_date))
                r = ac.fetchone()
                if not r:
                    ac.execute("""
                        SELECT * FROM alerts
                        WHERE (UPPER(symbol) = ? OR LOWER(symbol) = ?)
                          AND llm_decision IS NOT NULL
                          AND llm_decision != ''
                        ORDER BY date DESC, rowid DESC
                        LIMIT 1
                    """, (ticker_u, ticker_u.lower()))
                    r = ac.fetchone()
                if r:
                    alert_row = dict(r)

                ac.execute("""
                    SELECT * FROM suggestions
                    WHERE (UPPER(ticker) = ? OR LOWER(ticker) = ?)
                    ORDER BY date DESC, id DESC
                    LIMIT 1
                """, (ticker_u, ticker_u.lower()))
                sr = ac.fetchone()
                if sr:
                    suggestion_row = dict(sr)
    except Exception as e:
        logger.debug(f"Alert DB query error for {ticker_u}: {e}")

    if not thesis_data and not alert_row and not suggestion_row:
        return None, None

    triage_info = {}
    llm_data = {}
    if thesis_data:
        raw_triage = thesis_data.get("triage")
        if isinstance(raw_triage, dict):
            triage_info = raw_triage
            llm_data = thesis_data.get("llm_data") or {}
        else:
            triage_info = thesis_data
            llm_data = thesis_data.get("llm_data") or {}

    raw_dec = (alert_row.get("llm_decision") if alert_row else None) or triage_info.get("triage") or (suggestion_row.get("gate_status") if suggestion_row else None) or "WATCH"
    clean_verdict = "PASS" if "PASS" in str(raw_dec).upper() else ("CUT" if "CUT" in str(raw_dec).upper() else "WATCH")

    conv_val = None
    if alert_row and alert_row.get("llm_decision"):
        m = re.search(r"\((\d+(?:\.\d+)?(?:/\d+)?)\)", alert_row["llm_decision"])
        if m:
            conv_val = m.group(1)
    if not conv_val:
        conv_val = triage_info.get("conviction") or llm_data.get("conviction") or (alert_row.get("score") if alert_row else None)
    if conv_val is not None:
        try:
            if isinstance(conv_val, (int, float)):
                conv_str = f"{float(conv_val):.1f}"
            else:
                conv_str = str(conv_val)
        except Exception:
            conv_str = str(conv_val)
    else:
        conv_str = "--"

    setup = (alert_row.get("setup") if alert_row else None) or triage_info.get("mode") or (suggestion_row.get("setup_lane") if suggestion_row else None) or "Technical Coiling"
    mode = triage_info.get("mode") or (suggestion_row.get("setup_lane") if suggestion_row else None) or "REVERSAL / MOMENTUM"

    def _fmt(val):
        try:
            return f"{float(val):.2f}"
        except (ValueError, TypeError):
            return str(val) if val is not None else "--"

    playbook = (alert_row.get("llm_playbook") if alert_row else None) or triage_info.get("reason") or llm_data.get("sentiment_summary") or "Local technical triage completed."

    side = str(triage_info.get("chosen_side") or (alert_row.get("side") if alert_row else "") or "long").lower()
    plan = triage_info.get(f"{side}_plan") or triage_info.get("long_plan") or {}
    zone = plan.get("zone") or [None, None]
    entry_low = zone[0] if (len(zone) > 0 and zone[0] is not None) else (suggestion_row.get("entry_low") if suggestion_row else None)
    entry_high = zone[1] if (len(zone) > 1 and zone[1] is not None) else (suggestion_row.get("entry_high") if suggestion_row else None)
    stop = plan.get("stop") or (suggestion_row.get("stop") if suggestion_row else None) or triage_info.get("tight_stop")
    target_1 = plan.get("target") or (suggestion_row.get("target_1") if suggestion_row else None)
    target_2 = suggestion_row.get("target_2") if suggestion_row else None

    # Parse levels from playbook text if missing
    if stop is None and playbook:
        m_stop = re.search(r'Stop\s+\$?([0-9]+\.?[0-9]*)', playbook, re.IGNORECASE)
        if m_stop:
            try:
                stop = float(m_stop.group(1))
            except Exception:
                pass
    if target_1 is None and playbook:
        m_target = re.search(r'Target\s+\$?([0-9]+\.?[0-9]*)', playbook, re.IGNORECASE)
        if m_target:
            try:
                target_1 = float(m_target.group(1))
            except Exception:
                pass
    if entry_low is None and playbook:
        m_entry = re.search(r'Entry\s+(?:Zone\s+)?\$?([0-9]+\.?[0-9]*)(?:\s*[-–]\s*\$?([0-9]+\.?[0-9]*))?', playbook, re.IGNORECASE)
        if m_entry:
            try:
                entry_low = float(m_entry.group(1))
                entry_high = float(m_entry.group(2)) if m_entry.group(2) else entry_low
            except Exception:
                pass

    spot_price = (alert_row.get("alert_price") if alert_row else None) or (alert_row.get("market_price") if alert_row else None) or (suggestion_row.get("last_price") if suggestion_row else None)
    if not spot_price:
        # Check datawindow in raw/triage artifacts
        dw = load_datawindow(ticker_u, target_date)
        if dw:
            for k in ["close", "Close", "last", "Last", "bar_close", "price"]:
                if k in dw and dw[k] is not None:
                    try:
                        spot_price = float(dw[k])
                        break
                    except Exception:
                        pass
    if not spot_price and entry_low and entry_high:
        try:
            spot_price = (float(entry_low) + float(entry_high)) / 2.0
        except Exception:
            pass
    elif not spot_price and entry_low:
        try:
            spot_price = float(entry_low)
        except Exception:
            pass

    # If entry zone was not explicitly given, compute tactical zone from spot & stop
    if entry_low is None and spot_price:
        if stop and spot_price > stop:
            entry_low = round(spot_price * 0.99, 2)
            entry_high = round(spot_price, 2)
        else:
            entry_low = None
            entry_high = None

    # A3: Reject degenerate zones: if entry_low == entry_high == spot, set levels to None and tag no_levels
    no_levels = False
    if entry_low is not None and entry_high is not None and spot_price is not None:
        if abs(float(entry_low) - float(entry_high)) < 1e-4 and abs(float(entry_low) - float(spot_price)) < 0.01:
            entry_low = None
            entry_high = None
            stop = None
            target_1 = None
            target_2 = None
            no_levels = True

    # A1/A2: Live spot, move_since_bar_pct, and staleness
    dw = load_datawindow(ticker_u, target_date) or {}
    dw_bar_date = triage_info.get("dw_bar_date") or dw.get("dw_bar_date") or target_date
    spot_age = triage_info.get("spot_age") or dw.get("spot_age") or 0
    stale_data = bool(triage_info.get("stale_data") or (isinstance(spot_age, (int, float)) and spot_age > 1))

    live_spot = None
    try:
        from src.clients.price_client import get_current_price
        live_spot = get_current_price(ticker_u)
    except Exception:
        pass

    move_since_bar_pct = 0.0
    if spot_price and live_spot:
        move_since_bar_pct = round(((float(live_spot) - float(spot_price)) / float(spot_price)) * 100, 2)

    # Mathematical R:R computation if missing
    rr = triage_info.get("rr") or (suggestion_row.get("rr_at_market_at_signal") if suggestion_row else None)
    if rr is None and entry_high and stop and target_1:
        try:
            risk = float(entry_high) - float(stop)
            reward = float(target_1) - float(entry_high)
            if risk > 0 and reward > 0:
                rr = reward / risk
        except Exception:
            pass
    rr_str = f"{float(rr):.2f}" if rr is not None else "--"
    win_prob = triage_info.get("win_prob") or (suggestion_row.get("lane_prior_win") if suggestion_row else None)
    win_prob_str = f"{float(win_prob):.1f}%" if win_prob is not None else "--"
    ev_r = triage_info.get("ev_r") or (suggestion_row.get("lane_prior_ev") if suggestion_row else None)
    ev_str = f"{float(ev_r):.2f} R" if ev_r is not None else "--"

    # Extract recommended options vehicle from playbook
    vehicle = "EQUITY_SHARES"
    if playbook:
        m_veh = re.search(r'Vehicle:\s*([A-Za-z0-9_]+)', playbook)
        if m_veh:
            vehicle = m_veh.group(1).upper()

    flags = triage_info.get("flags") or llm_data.get("flags") or []
    flags_str = ", ".join(f"`{f}`" for f in flags) if flags else "None detected"
    recency = triage_info.get("recency") or {}
    warnings = recency.get("warnings_fresh") or []
    warnings_str = ", ".join(warnings) if warnings else "Clean (No active warnings)"
    reversals = recency.get("reversals_fresh") or []
    reversals_str = ", ".join(reversals) if reversals else "None"

    sentiment = triage_info.get("sentiment") or {}
    sentiment_label = sentiment.get("label") or llm_data.get("sentiment") or "neutral"
    sentiment_summary = sentiment.get("summary") or llm_data.get("sentiment_summary") or "No negative news contradictions detected."
    headlines = sentiment.get("headlines") or []
    headlines_formatted = "\n".join(f"- {h}" for h in headlines[:5]) if headlines else "- No recent breaking headlines flagged."

    spot_header = f"> **Dossier Spot**: ${_fmt(spot_price)} (Bar: {dw_bar_date}, Age: {spot_age}d)" if spot_price else ""
    if live_spot is not None:
        sign = "+" if move_since_bar_pct >= 0 else ""
        spot_header += f" | **Live Spot**: **${live_spot:.2f}** ({sign}{move_since_bar_pct:.1f}% since bar)"
    if stale_data:
        spot_header += f" | ⚠️ **STALE DATA** (not scored)"

    lines = [
        f"# {ticker_u} | LOCAL RESEARCH DOSSIER ({target_date})\n",
        f"> **System Decision**: **{raw_dec}** | **Score / Conviction**: **{conv_str}** | **Setup**: **{setup}**\n",
        f"> **Lane / Mode**: `{mode}`\n",
    ]
    if spot_header:
        lines.append(f"{spot_header}\n")
    lines.extend([
        "\n---\n",
        "### 🛡️ Tactical Playbook & Evaluation\n",
        playbook.strip(),
        "\n\n---\n",
        "### 🎯 Structured Levels & Mathematical Plan\n",
    ])
    if no_levels:
        lines.append("- **Entry Zone**: None (degenerate zone rejected — entry equals spot)")
        lines.append("- **Tactical Invalidation Stop**: None")
        lines.append("- **Target 1**: None")
    else:
        if entry_low is not None and entry_high is not None:
            lines.append(f"- **Entry Zone**: ${_fmt(entry_low)} – ${_fmt(entry_high)}")
        if stop is not None:
            lines.append(f"- **Tactical Invalidation Stop**: ${_fmt(stop)}")
        if target_1 is not None:
            lines.append(f"- **Target 1 (Scale Out)**: ${_fmt(target_1)}")
        if target_2 is not None:
            lines.append(f"- **Target 2 (Runner)**: ${_fmt(target_2)}")
    lines.append(f"- **Risk / Reward**: {rr_str} | **Win Probability**: {win_prob_str} | **Expected Value**: {ev_str}")

    lines.extend([
        "\n### ⚠️ Technical Warnings & Flags\n",
        f"- **Active Flags**: {flags_str}",
        f"- **Recency Warnings**: {warnings_str}",
        f"- **Reversal Triggers**: {reversals_str}",
        "\n### 📰 News & Sentiment Dossier\n",
        f"- **Sentiment Stance**: **{sentiment_label.upper()}**",
        f"- **Synthesis**: {sentiment_summary}",
        "- **Recent Catalysts & Headlines**:\n" + headlines_formatted,
        "\n---\n",
        f"*Note: This Local Research Dossier was generated via the deterministic pre-filter and local Qwen LLM. Click **🔬 Run deep** to trigger multi-model quantitative arbitration.*"
    ])
    dossier_md = "\n".join(lines)

    is_cut_or_skip = clean_verdict in ("CUT", "CASH_SKIP", "NO_TRADE")
    conv_derived = 0.0 if is_cut_or_skip else (float(conv_val) if isinstance(conv_val, (int, float)) else (5.0 if clean_verdict == "WATCH" else 8.0))
    opt_actionable = False if is_cut_or_skip else (clean_verdict == "PASS")

    opt_summary = f"Local triage: {clean_verdict}. Vehicle: {vehicle}. Stalk entry zone ${float(entry_low):.2f}-${float(entry_high):.2f} with tactical stop at ${float(stop):.2f}." if (entry_low and stop and not is_cut_or_skip) else (f"Local triage: {clean_verdict}. Execution inactive." if is_cut_or_skip else f"Local triage: {vehicle} plan recommended.")

    watch_levels = {
        "ticker": ticker_u,
        "date": target_date,
        "verdict": clean_verdict,
        "conviction": conv_derived,
        "status": "CUT" if is_cut_or_skip else ("IN_ZONE" if (suggestion_row and suggestion_row.get("status") in ("IN_ZONE", "IN_TRADE")) else "STALKING"),
        "spot_price": spot_price,
        "spot_price_date": target_date,
        "shares_plan": {
            "entry_zone_low": entry_low if not is_cut_or_skip else None,
            "entry_zone_high": entry_high if not is_cut_or_skip else None,
            "tactical_stop": stop,
            "target_1": target_1 if not is_cut_or_skip else None,
            "target_2": target_2 if not is_cut_or_skip else None,
        },
        "options_plan": {
            "structure": vehicle,
            "actionable": opt_actionable,
            "summary": opt_summary,
        },
        "invalidation": {
            "condition": "DAILY CLOSE BELOW",
            "price_level": stop,
            "rationale": f"Thesis invalidated on candle close below ${float(stop):.2f}." if stop else "Invalidation below structural stop level.",
        }
    }

    return dossier_md, watch_levels


@router.get("/api/report/{ticker}")
def get_report_bundle_single(ticker: str):
    """Convenience alias resolving the latest available report bundle for a ticker."""
    return get_report_bundle("latest", ticker)


@router.get("/api/report/{date}/{ticker}")
def get_report_bundle(date: str, ticker: str):
    """Return all 3 model markdown reports, available dates, and historical timeline for a ticker."""
    ticker_u = validate_ticker(ticker)
    cache_key = f"{date}_{ticker_u}"
    now_ts = time.time()

    cached = _REPORT_BUNDLE_CACHE.get(cache_key)
    if cached and (now_ts - cached[0] < _REPORT_CACHE_TTL):
        res = dict(cached[1])
        try:
            with get_db() as conn:
                r = conn.cursor().execute(
                    "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (ticker_u,)
                ).fetchone()
                if r and r["last_price"]:
                    res["live_price"] = float(r["last_price"])
        except Exception:
            pass
        return res

    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    triage_root = config.BASE_DIR / "data" / "triage"

    md_dates_set = set()
    other_dates_set = set()
    scrape_dates_set = set()

    # 1. Reports root: check if any markdown file for ticker_u exists in reports/{date}
    if rep_root.exists():
        for d in rep_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                if (
                    (d / f"{ticker_u}_arbitration.md").exists()
                    or (d / f"{ticker_u}_summary.md").exists()
                    or (d / f"{ticker_u}_independent.md").exists()
                    or (d / f"{ticker_u}_gemini_dr.md").exists()
                    or (d / f"{ticker_u}_thesis.md").exists()
                ):
                    md_dates_set.add(d.name)
                else:
                    for f in d.iterdir():
                        if f.is_file() and f.suffix.lower() == ".md" and f.name.upper().startswith(ticker_u):
                            md_dates_set.add(d.name)
                            break

    # 2. Raw root: check if ticker_u folder has markdown files or datawindow
    if raw_root.exists():
        for d in raw_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                sym_dir = d / ticker_u
                if sym_dir.exists():
                    has_md = False
                    for f in sym_dir.iterdir():
                        if f.is_file() and f.suffix.lower() == ".md":
                            md_dates_set.add(d.name)
                            has_md = True
                            break
                    if not has_md:
                        if (sym_dir / f"{ticker_u}_datawindow.json").exists() or (sym_dir / f"{ticker_u}_datawindow.csv").exists():
                            scrape_dates_set.add(d.name)
                        if (
                            (sym_dir / f"{ticker_u}_thesis.json").exists()
                            or (sym_dir / f"{ticker_u}_triage.json").exists()
                            or (sym_dir / f"{ticker_u}_watch_levels.json").exists()
                        ):
                            other_dates_set.add(d.name)

                if (d / f"{ticker_u}_triage.json").exists():
                    other_dates_set.add(d.name)

                if (d / "consolidate" / "consolidated_results.json").exists():
                    try:
                        with open(d / "consolidate" / "consolidated_results.json", "r", encoding="utf-8") as cr_f:
                            cr_data = json.load(cr_f)
                            if isinstance(cr_data, dict) and ticker_u in cr_data:
                                other_dates_set.add(d.name)
                    except Exception:
                        pass

    # 3. Triage root
    if triage_root.exists():
        for d in triage_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                if list(d.glob(f"**/{ticker_u}*.md")):
                    md_dates_set.add(d.name)
                elif list(d.glob(f"**/{ticker_u}_thesis.json")) or list(d.glob(f"**/{ticker_u}_triage.json")):
                    other_dates_set.add(d.name)
                if list(d.glob(f"**/{ticker_u}*datawindow*")):
                    scrape_dates_set.add(d.name)

    # 4. Alert & suggestions DB
    try:
        from src.tracking.alert_db import _get_connection as _get_alert_conn, _db_lock as _alert_db_lock
        with _alert_db_lock:
            with _get_alert_conn() as acon:
                rows = acon.cursor().execute(
                    "SELECT DISTINCT date FROM alerts WHERE (UPPER(symbol) = ? OR LOWER(symbol) = ?) AND llm_decision IS NOT NULL AND llm_decision != '' ORDER BY date DESC",
                    (ticker_u, ticker_u.lower())
                ).fetchall()
                for r in rows:
                    if r["date"] and str(r["date"]).startswith("202"):
                        other_dates_set.add(str(r["date"])[:10])
                s_rows = acon.cursor().execute(
                    "SELECT DISTINCT date FROM suggestions WHERE (UPPER(ticker) = ? OR LOWER(ticker) = ?) ORDER BY date DESC",
                    (ticker_u, ticker_u.lower())
                ).fetchall()
                for r in s_rows:
                    if r["date"] and str(r["date"]).startswith("202"):
                        other_dates_set.add(str(r["date"])[:10])
    except Exception as e:
        logger.debug(f"Could not load alert dates for {ticker_u}: {e}")

    try:
        with get_db() as conn:
            rows = conn.cursor().execute(
                "SELECT DISTINCT date FROM watch_targets WHERE ticker = ? ORDER BY date DESC",
                (ticker_u,)
            ).fetchall()
            for r in rows:
                if r["date"] and r["date"].startswith("202"):
                    other_dates_set.add(r["date"][:10])
    except Exception:
        pass

    all_sorted_dates = sorted(list(md_dates_set | other_dates_set | scrape_dates_set), reverse=True)
    if not all_sorted_dates:
        all_sorted_dates = [datetime.now().strftime("%Y-%m-%d")]

    req_date = (date or "").strip()
    target_date = req_date
    if not target_date or target_date in ("latest", "today", "now", "", "undefined", "null"):
        # Prioritize newest date with actual markdown dossier files on disk!
        if md_dates_set:
            target_date = sorted(list(md_dates_set), reverse=True)[0]
        else:
            target_date = all_sorted_dates[0]
    elif target_date not in all_sorted_dates:
        # Pinned date not in list, keep target_date as requested
        pass

    available_dates = all_sorted_dates[:50]
    if target_date not in available_dates:
        available_dates.insert(0, target_date)

    def _read_dossier_files(d_str: str):
        r_d = rep_root / d_str
        w_d = raw_root / d_str / ticker_u
        s_md, i_md, a_md = None, None, None

        # 1. Senior-PM Arbitration
        a_cand = r_d / f"{ticker_u}_arbitration.md"
        if not a_cand.exists() and w_d.exists():
            c_p = w_d / f"{ticker_u}_arbitration.md"
            if c_p.exists():
                a_cand = c_p
        if a_cand and a_cand.exists():
            try:
                a_md = a_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 2. Summary / Model A
        s_cand = r_d / f"{ticker_u}_summary.md"
        if not s_cand.exists() and r_d.exists():
            for alt_name in (
                f"{ticker_u}_gemini_dr.md",
                f"{ticker_u}_gemini_thesis.md",
                f"{ticker_u}_minimax_summary.md",
                f"{ticker_u}_mimo_summary.md",
                f"{ticker_u}_qwen3.7_summary.md",
                f"{ticker_u}_thesis.md",
            ):
                cand = r_d / alt_name
                if cand.exists():
                    s_cand = cand
                    break
        if not s_cand.exists() and w_d.exists():
            for c_name in (
                f"{ticker_u}_gemini_thesis.md",
                f"{ticker_u}_summary.md",
                f"{ticker_u}_thesis.md",
                f"{ticker_u}_news_research.md",
            ):
                c_p = w_d / c_name
                if c_p.exists():
                    s_cand = c_p
                    break
            if not s_cand.exists():
                for c_p in w_d.glob(f"{ticker_u}*.md"):
                    if c_p.is_file() and not c_p.name.endswith("_arbitration.md") and not c_p.name.endswith("_independent.md"):
                        s_cand = c_p
                        break
        if s_cand and s_cand.exists():
            try:
                s_md = s_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 3. Independent / Model B
        i_cand = r_d / f"{ticker_u}_independent.md"
        if not i_cand.exists() and w_d.exists():
            c_p = w_d / f"{ticker_u}_independent_thesis.md"
            if c_p.exists():
                i_cand = c_p
        if i_cand and i_cand.exists():
            try:
                i_md = i_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 4. Triage fallback if no reports in rep or raw
        if not s_md and not i_md and not a_md and triage_root.exists():
            triage_cands = [
                triage_root / d_str / "_DEEP_RESEARCH" / f"{ticker_u}_gemini_thesis.md",
                triage_root / d_str / "force" / f"{ticker_u}_gemini_thesis.md",
                triage_root / d_str / f"{ticker_u}_gemini_thesis.md",
                triage_root / d_str / f"{ticker_u}_thesis.md",
            ]
            for tc in triage_cands:
                if tc.exists():
                    try:
                        s_md = tc.read_text(encoding="utf-8")
                        break
                    except Exception:
                        pass
            if not s_md and not i_md and not a_md:
                t_sub = triage_root / d_str
                if t_sub.exists():
                    for tc in t_sub.glob(f"**/{ticker_u}*.md"):
                        if tc.is_file():
                            try:
                                s_md = tc.read_text(encoding="utf-8")
                                break
                            except Exception:
                                pass

        return s_md, i_md, a_md

    summary_md, independent_md, arbitration_md = _read_dossier_files(target_date)

    # Check local research dossier for target date
    local_dossier_md, local_wl = _build_local_research_dossier(target_date, ticker_u)

    # Fallback to an older date if target_date has no deep research reports
    if not summary_md and not independent_md and not arbitration_md:
        for alt_d in available_dates:
            if alt_d == target_date:
                continue
            alt_s, alt_i, alt_a = _read_dossier_files(alt_d)
            if alt_s or alt_i or alt_a:
                summary_md, independent_md, arbitration_md = alt_s, alt_i, alt_a
                target_date = alt_d
                local_dossier_md, local_wl = _build_local_research_dossier(target_date, ticker_u)
                break

    # If arbitration is missing but a real deep summary/independent exists, use it as primary display
    if not arbitration_md and (summary_md or independent_md):
        cand_md = summary_md or independent_md
        if cand_md and not cand_md.startswith(f"# {ticker_u} | LOCAL RESEARCH DOSSIER"):
            arbitration_md = cand_md

    # If summary is missing but real arbitration exists, mirror it
    if not summary_md and arbitration_md:
        if not arbitration_md.startswith(f"# {ticker_u} | LOCAL RESEARCH DOSSIER"):
            summary_md = arbitration_md

    # Note: If deep reports are missing and only local_dossier_md exists, do NOT mirror it into
    # arbitration_md or summary_md. Each tab maintains its true content so pending states render cleanly.

    zoom_path = find_chart_path(target_date, ticker_u, "zoom")
    plain_path = find_chart_path(target_date, ticker_u, "plain")

    timeline = []
    for d_str in available_dates:
        t_raw = raw_root / d_str / ticker_u
        t_raw_date = raw_root / d_str
        t_rep = rep_root / d_str

        doc_candidates = [
            t_rep / f"{ticker_u}_arbitration.md",
            t_raw / f"{ticker_u}_arbitration.md",
            t_rep / f"{ticker_u}_summary.md",
            t_raw / f"{ticker_u}_summary.md",
            t_rep / f"{ticker_u}_independent.md",
            t_raw / f"{ticker_u}_independent_thesis.md",
            t_raw / f"{ticker_u}_gemini_thesis.md",
        ]

        chosen_doc = None
        for cand in doc_candidates:
            if cand.exists() and cand.stat().st_size > 250:
                txt_head = cand.read_text(encoding="utf-8")[:400]
                if not txt_head.strip().startswith("<tool_call>"):
                    chosen_doc = cand
                    break

        if not chosen_doc:
            loc_md, loc_wl = _build_local_research_dossier(d_str, ticker_u)
            if loc_md and loc_wl:
                v = loc_wl.get("verdict", "WATCH")
                c = loc_wl.get("conviction")
                conv_str = f" (Conviction: {c}/10)" if c else ""
                timeline.append({
                    "date": d_str,
                    "spot": loc_wl.get("spot_price"),
                    "verdict": f"{v}{conv_str}",
                    "summary": f"Local triage: {v}.",
                    "has_doc": True,
                })
            continue

        txt = chosen_doc.read_text(encoding="utf-8")

        spot_val = None
        dw_val = load_datawindow(ticker_u, d_str)
        if dw_val:
            for k in ["close", "Close", "last", "Last", "bar_close", "price"]:
                if k in dw_val and dw_val[k]:
                    try:
                        spot_val = float(dw_val[k])
                        break
                    except Exception:
                        pass

        if not spot_val:
            m_spot = re.search(r'(?:Spot Price|Bar close|\bSpot\b|\bClose\b)[\s\*:]+\$?([0-9]+\.[0-9]+)', txt, re.IGNORECASE)
            if m_spot:
                spot_val = float(m_spot.group(1))
            else:
                m_header_spot = re.search(rf'#\s*{ticker_u}\s*\|\s*\$([0-9]+\.[0-9]+)', txt)
                if m_header_spot:
                    spot_val = float(m_header_spot.group(1))

        if not spot_val:
            for other_cand in doc_candidates:
                if other_cand.exists() and other_cand != chosen_doc:
                    o_txt = other_cand.read_text(encoding="utf-8")[:800]
                    m_o_spot = re.search(r'(?:Spot Price|Bar close|\bSpot\b|\bClose\b)[\s\*:]+\$?([0-9]+\.[0-9]+)', o_txt, re.IGNORECASE)
                    if m_o_spot:
                        spot_val = float(m_o_spot.group(1))
                        break
                    m_o_hspot = re.search(rf'#\s*{ticker_u}\s*\|\s*\$([0-9]+\.[0-9]+)', o_txt)
                    if m_o_hspot:
                        spot_val = float(m_o_hspot.group(1))
                        break

        verdict_str = None
        m_json = re.search(r'"verdict":\s*"([^"]+)"', txt)
        if m_json:
            v_raw = m_json.group(1).strip()
            m_conv = re.search(r'"conviction":\s*([0-9]+(?:\.[0-9]+)?)', txt)
            conv_str = f" (Conviction: {m_conv.group(1)}/10)" if m_conv else ""
            verdict_str = f"{v_raw}{conv_str}"

        if not verdict_str:
            m_v = re.search(r'\*\*(?:Final\s+)?Verdict:\*\*\s*([^\n\r]+)', txt)
            if m_v:
                v_line = m_v.group(1).replace("**", "").strip()
                if not v_line.startswith("|") and "Vehicle" not in v_line:
                    v_clean = re.split(r'[\xb7\u2022]', v_line)[0].strip()
                    verdict_str = v_clean[:50]

        if not verdict_str:
            m_tr = re.search(r'\*\*Technical Rating:\*\*\s*([^\n\r·*]+)', txt)
            if m_tr:
                v_tr = m_tr.group(1).strip()
                if not v_tr.startswith("|") and "Vehicle" not in v_tr:
                    verdict_str = v_tr[:40]

        if not verdict_str or verdict_str == "ANALYZED":
            m_eq = re.search(r'\|\s*\*\*Equity(?:\s*\(Shares\))?\*\*\s*\|\s*\*\*?([^\*\|]+)\*\*?\s*\|', txt)
            if m_eq:
                v_eq = m_eq.group(1).strip()
                if "Vehicle" not in v_eq and "Verdict" not in v_eq:
                    verdict_str = v_eq[:40]

        if not verdict_str or verdict_str == "ANALYZED":
            try:
                with get_db() as conn:
                    row = conn.cursor().execute(
                        "SELECT verdict, conviction FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?)",
                        (ticker_u, d_str, f"{d_str}%")
                    ).fetchone()
                    if row and row["verdict"]:
                        c_str = f" (Conviction: {row['conviction']}/10)" if row["conviction"] else ""
                        verdict_str = f"{row['verdict']}{c_str}"
            except Exception:
                pass

        if not verdict_str:
            verdict_str = "ANALYZED"

        verdict_str = verdict_str.replace("·", "·").strip(" |*·-")
        if "Vehicle" in verdict_str or "Actionable Setup" in verdict_str:
            verdict_str = "STALK"

        # Look for the executive 2-sentence thesis across summary, gemini, or arbitration docs
        summary_cand = None
        for s_cand in [t_rep / f"{ticker_u}_summary.md", t_raw / f"{ticker_u}_summary.md", t_raw / f"{ticker_u}_gemini_thesis.md"]:
            if s_cand.exists() and s_cand.stat().st_size > 200:
                summary_cand = s_cand
                break

        preview_text = None
        text_sources = [txt]
        if summary_cand and summary_cand != chosen_doc:
            try:
                text_sources.insert(0, summary_cand.read_text(encoding="utf-8"))
            except Exception:
                pass

        for src in text_sources:
            m_th = re.search(r'\*\*(?:The\s+)?Thesis in 2 Sentences:\*\*\s*([^\n\r]+(?:\n[^\n\r#]+)?)', src)
            if m_th:
                cand_th = m_th.group(1)
                cand_th = re.sub(r'```[a-zA-Z0-9_:]*[\s\S]*?```', '', cand_th)
                cand_th = cand_th.replace("**", "").replace("__", "").replace("`", "").strip()
                cand_th = re.sub(r'^\s*[\*\-\•]\s*', '', cand_th)
                if len(cand_th) > 20:
                    preview_text = cand_th[:260] + ('...' if len(cand_th) > 260 else '')
                    break

        if not preview_text:
            for src in text_sources:
                src_no_code = re.sub(r'```[a-zA-Z0-9_:]*[\s\S]*?```', '', src)
                m_judge = re.search(r'##\s*[^\n]*?(?:JUDGE|RULING|EXECUTIVE|DIRECTIVE|CASE FOR)[^\n]*\n([\s\S]+?)(?=\n##|\Z)', src_no_code, re.IGNORECASE)
                if m_judge:
                    raw_block = m_judge.group(1)
                    raw_block = re.sub(r'^\s*#+\s*.*$', '', raw_block, flags=re.MULTILINE)
                    raw_block = re.sub(r'^\s*[\*\-\•]\s+', '', raw_block, flags=re.MULTILINE)
                    raw_block = raw_block.replace("**", "").replace("__", "").replace("`", "")
                    lines = [l.strip() for l in raw_block.split('\n') if l.strip() and not l.strip().startswith('{') and not l.strip().startswith('}') and not l.strip().startswith('|')]
                    if lines:
                        cand_block = ' '.join(lines)
                        if len(cand_block) > 20:
                            preview_text = cand_block[:260] + ('...' if len(cand_block) > 260 else '')
                            break

        if not preview_text:
            for src in text_sources:
                src_clean = re.sub(r'```[a-zA-Z0-9_:]*[\s\S]*?```', '', src)
                src_clean = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', src_clean)
                src_clean = re.sub(r'\{[^\}]+\}', '', src_clean)
                paras = [p.strip() for p in src_clean.split('\n\n') if p.strip()]
                for p in paras:
                    if p.startswith('#') or p.startswith('|') or p.startswith('{'):
                        continue
                    p_clean = re.sub(r'^\s*[\*\-\•]\s+', '', p, flags=re.MULTILINE)
                    p_clean = p_clean.replace("**", "").replace("__", "").replace("`", "").strip()
                    lines = [l.strip() for l in p_clean.split('\n') if l.strip() and not l.strip().startswith('#') and not l.strip().startswith('|')]
                    cand = ' '.join(lines)
                    if len(cand) > 25:
                        preview_text = cand[:260] + ('...' if len(cand) > 260 else '')
                        break
                if preview_text:
                    break

        if preview_text:
            preview_text = preview_text.replace("\ufffd", "–").replace("Â", "").strip()

        timeline.append({
            "date": d_str,
            "spot": spot_val,
            "verdict": verdict_str,
            "preview": preview_text or f"Full research dossier archived for {d_str}.",
            "is_current": (d_str == date),
        })

    live_price = None
    try:
        with get_db() as conn:
            r = conn.cursor().execute(
                "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                (ticker_u,)
            ).fetchone()
            if r and r["last_price"]:
                live_price = float(r["last_price"])
    except Exception:
        pass

    watch_levels = None
    levels_file = raw_root / target_date / ticker_u / f"{ticker_u}_watch_levels.json"
    if not levels_file.exists():
        levels_file = raw_root / target_date / f"{ticker_u}_watch_levels.json"
    if not levels_file.exists():
        levels_file = rep_root / target_date / f"{ticker_u}_watch_levels.json"
    if levels_file.exists():
        try:
            watch_levels = json.loads(levels_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Extract directly from embedded json:watch_levels in arbitration_md or summary_md
    if not watch_levels:
        for md_txt in [arbitration_md, summary_md]:
            if md_txt:
                m_block = re.search(
                    r"```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?\"shares_plan\"[\s\S]*?\})\s*```",
                    md_txt,
                )
                if m_block:
                    try:
                        watch_levels = json.loads(m_block.group(1))
                        # Persist to raw folder so future lookups are instant
                        save_p = raw_root / target_date / ticker_u / f"{ticker_u}_watch_levels.json"
                        save_p.parent.mkdir(parents=True, exist_ok=True)
                        save_p.write_text(json.dumps(watch_levels, indent=2), encoding="utf-8")
                        break
                    except Exception:
                        pass

    if not watch_levels:
        try:
            with get_db() as conn:
                row = conn.cursor().execute(
                    "SELECT * FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?) ORDER BY date DESC LIMIT 1",
                    (ticker_u, target_date, f"{target_date}%")
                ).fetchone()
                if not row:
                    row = conn.cursor().execute(
                        "SELECT * FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                        (ticker_u,)
                    ).fetchone()
                if row:
                    watch_levels = {
                        "ticker": ticker_u,
                        "date": row["date"] or target_date,
                        "verdict": row["verdict"],
                        "conviction": row["conviction"],
                        "shares_plan": {
                            "entry_zone_low": row["entry_zone_low"],
                            "entry_zone_high": row["entry_zone_high"],
                            "tactical_stop": row["tactical_stop"],
                            "target_1": row["target_1"],
                            "target_2": row["target_2"],
                        },
                        "options_plan": {
                            "actionable": bool(row["options_actionable"]),
                            "structure": row["options_structure"],
                            "summary": row["options_summary"],
                            "max_loss": row["options_max_loss"],
                            "max_profit": row["options_max_profit"],
                        },
                        "invalidation": {
                            "condition": row["invalidation_rule"],
                            "price_level": row["tactical_stop"],
                        },
                        "status": row["status"],
                    }
        except Exception:
            pass

    user_position = None
    trade_ideas = []
    try:
        from src.logic.trade_ideas_generator import generate_trade_ideas_for_target
        if watch_levels:
            sp = watch_levels.get("shares_plan") or {}
            target_dict = {
                "ticker": ticker_u,
                "last_price": live_price,
                "entry_zone_low": sp.get("entry_zone_low"),
                "entry_zone_high": sp.get("entry_zone_high"),
                "tactical_stop": sp.get("tactical_stop"),
                "target_1": sp.get("target_1"),
                "target_2": sp.get("target_2"),
                "options_summary": (watch_levels.get("options_plan") or {}).get("summary"),
            }
            trade_ideas = generate_trade_ideas_for_target(target_dict)
    except Exception as te:
        logger.debug(f"Could not generate trade ideas for {ticker_u}: {te}")

    if not arbitration_md and not summary_md and not independent_md:
        if not local_dossier_md:
            local_dossier_md, local_wl = _build_local_research_dossier(target_date, ticker_u)
        if local_dossier_md:
            if not watch_levels and local_wl:
                watch_levels = local_wl
        elif watch_levels:
            sp = watch_levels.get("shares_plan") or {}
            op = watch_levels.get("options_plan") or {}
            v = watch_levels.get("verdict") or "WATCH"
            c = watch_levels.get("conviction") or "--"
            ez_l = sp.get("entry_zone_low")
            ez_h = sp.get("entry_zone_high")
            stop = sp.get("tactical_stop")
            t1 = sp.get("target_1")
            t2 = sp.get("target_2")
            opt_str = op.get("summary") or op.get("structure") or "Pullback trade only"
            lines = [
                f"# {ticker_u} | TACTICAL RADAR & WATCH DOSSIER ({target_date})\n",
                f"> **System Verdict**: **{v}** | **Conviction Score**: **{c}/10**\n",
                "### 🎯 Structured Levels",
            ]
            if ez_l is not None and ez_h is not None:
                lines.append(f"- **Entry Zone**: ${float(ez_l):.2f} – ${float(ez_h):.2f}")
            if stop is not None:
                lines.append(f"- **Tactical Invalidation Floor (Stop)**: ${float(stop):.2f}")
            if t1 is not None:
                lines.append(f"- **Target 1**: ${float(t1):.2f}")
            if t2 is not None:
                lines.append(f"- **Target 2**: ${float(t2):.2f}")
            lines.append(f"\n### 🛡️ Recommended Strategy\n{opt_str}\n")
            lines.append("---\n*Multi-model deep research narrative pending. Local triage and interactive Copilot are active.*")
            local_dossier_md = "\n".join(lines)
        else:
            local_dossier_md = (
                f"# {ticker_u} | LOCAL RESEARCH DOSSIER ({target_date})\n\n"
                f"No archived report or local research found for **{ticker_u}**.\n\n"
                f"👉 Use the interactive **AI Dossier Copilot** on the right to fetch live quotes, inspect options chains, or trigger fresh analysis."
            )
    else:
        # Deep research reports exist on disk! Also ensure local_dossier_md is populated
        if not local_dossier_md:
            local_dossier_md, local_wl = _build_local_research_dossier(target_date, ticker_u)

    if not watch_levels:
        if local_wl:
            watch_levels = local_wl
        else:
            _, local_wl = _build_local_research_dossier(target_date, ticker_u)
            if local_wl:
                watch_levels = local_wl

    # Extract spot_price at time of report for the Suggested Position header
    spot_price = None
    if watch_levels:
        spot_price = watch_levels.get("spot_price")
        if not spot_price:
            sp_plan = watch_levels.get("shares_plan") or {}
            # Use entry_zone midpoint as proxy if spot not recorded
            ez_l = sp_plan.get("entry_zone_low")
            ez_h = sp_plan.get("entry_zone_high")
            if ez_l and ez_h:
                try:
                    spot_price = (float(ez_l) + float(ez_h)) / 2.0
                except (ValueError, TypeError):
                    pass

    # Check if real multi-model deep research exists for target_date
    has_deep = bool(
        (summary_md and not summary_md.startswith(f"# {ticker_u} | LOCAL RESEARCH DOSSIER"))
        or (independent_md and not independent_md.startswith(f"# {ticker_u} | LOCAL RESEARCH DOSSIER"))
        or (arbitration_md and not arbitration_md.startswith(f"# {ticker_u} | LOCAL RESEARCH DOSSIER") and not arbitration_md.startswith(f"# {ticker_u} | TACTICAL RADAR"))
    )

    result_payload = {
        "ticker": ticker_u,
        "date": target_date,
        "live_price": live_price,
        "spot_price": spot_price,
        "user_position": user_position,
        "trade_ideas": trade_ideas,
        "available_dates": available_dates,
        "historical_timeline": timeline,
        "summary_md": summary_md,
        "independent_md": independent_md,
        "arbitration_md": arbitration_md,
        "local_dossier_md": local_dossier_md,
        "has_local_dossier": bool(local_dossier_md),
        "has_deep_research": has_deep,
        "default_tab": "local" if (local_dossier_md and not has_deep) else "arb",
        "watch_levels": watch_levels,
        "has_zoom_chart": zoom_path is not None,
        "has_plain_chart": plain_path is not None,
        "zoom_chart_url": f"/api/charts/{target_date}/{ticker_u}/zoom" if zoom_path else None,
        "plain_chart_url": f"/api/charts/{target_date}/{ticker_u}/plain" if plain_path else None,
    }

    _REPORT_BUNDLE_CACHE[cache_key] = (now_ts, result_payload)
    return result_payload


@router.get("/api/quote/{ticker}")
def get_ticker_quote(ticker: str):
    """Fetch live real-time price and day stats for a ticker directly from Schwab or QuoteRouter."""
    from src.clients.price_client import get_current_price, get_realtime_quote_data

    sym = validate_ticker(ticker)
    price = None
    net_change = 0.0
    net_pct = 0.0
    bid = 0.0
    ask = 0.0
    volume = 0
    source = "SCHWAB"
    quote_age = 0.0

    try:
        from src.clients.schwab_client import get_realtime_quote

        sq = get_realtime_quote(sym)
        if sq and sq.get("last_price") and float(sq["last_price"]) > 0:
            price = float(sq["last_price"])
            net_change = float(sq.get("net_change") or 0.0)
            net_pct = float(sq.get("net_percent_change") or 0.0)
            bid = float(sq.get("bid") or 0.0)
            ask = float(sq.get("ask") or 0.0)
            volume = int(sq.get("volume") or 0)
            source = "SCHWAB_REALTIME"
    except Exception as e:
        logger.debug(f"Schwab quote error for {sym}: {e}")

    if price is None:
        try:
            qd = get_realtime_quote_data(sym)
            if qd and qd.get("last_price") and float(qd["last_price"]) > 0:
                price = float(qd["last_price"])
                net_change = float(qd.get("net_change") or 0.0)
                net_pct = float(qd.get("net_percent_change") or 0.0)
                bid = float(qd.get("bid") or 0.0)
                ask = float(qd.get("ask") or 0.0)
                volume = int(qd.get("volume") or 0)
                source = qd.get("source") or "FALLBACK"
                if qd.get("timestamp"):
                    quote_age = max(0.0, round(time.time() - float(qd["timestamp"]), 1))
        except Exception:
            pass

    if price is None:
        try:
            price = get_current_price(sym)
            source = "FALLBACK"
        except Exception:
            pass

    if price is None:
        try:
            with get_db() as conn:
                r = conn.cursor().execute(
                    "SELECT last_price, updated_at FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (sym,)
                ).fetchone()
                if r and r["last_price"]:
                    price = float(r["last_price"])
                    source = "DATABASE"
        except Exception:
            pass

    return {
        "ticker": sym,
        "price": price,
        "net_change": net_change,
        "net_percent_change": net_pct,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "source": source,
        "quote_age_seconds": quote_age,
        "time": datetime.now(timezone.utc).isoformat(),
    }



@router.get("/api/options-flow/{ticker}")
def get_options_flow_endpoint(ticker: str, refresh: bool = False):
    """Fetch unusual options flow anomalies and institutional sweep metrics from Schwab API."""
    from src.clients.schwab_client import get_unusual_options_flow_data

    sym = validate_ticker(ticker)
    try:
        data = get_unusual_options_flow_data(sym, force_refresh=refresh)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch Schwab options flow for {sym}: {e}")
        return {
            "ticker": sym,
            "status": "error",
            "error": str(e),
            "anomalies": [],
        }


@router.get("/api/charts/{date}/{ticker}/{chart_type}")
def get_chart_image(date: str, ticker: str, chart_type: str):
    """Serve chart PNG images."""
    ticker_u = validate_ticker(ticker)
    img_path = find_chart_path(date, ticker_u, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail="Chart image not found")
    return FileResponse(str(img_path), media_type="image/png")


@router.get("/api/charts/latest/{ticker}/{chart_type}")
def get_latest_chart_image(ticker: str, chart_type: str):
    """Serve most recent chart PNG image for a ticker without requiring a date."""
    ticker_u = validate_ticker(ticker)
    img_path = find_chart_path("", ticker_u, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Chart image not found for {ticker}")
    return FileResponse(str(img_path), media_type="image/png")


@router.get("/api/research/{ticker}/trades")
def get_ticker_trades_endpoint(ticker: str):
    """
    Returns all trades taken under this ticker by name from:
    1. suggestions (user-taken or filled swing trades)
    2. positions (alert ingestor live/closed positions)
    3. schwab_positions (live Schwab broker executions)
    """
    sym = validate_ticker(ticker)
    trades = []

    def _val(row, key, default=None):
        return row[key] if key in row.keys() else default

    # 1. suggestions from research_watch.db (complete suggested trade history & performance)
    try:
        with get_db() as conn:
            c = conn.cursor()
            table_check = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='suggestions'").fetchone()
            if table_check:
                rows = c.execute(
                    "SELECT * FROM suggestions WHERE UPPER(ticker) = ? ORDER BY date DESC, id DESC",
                    (sym,)
                ).fetchall()
                for r in rows:
                    f_price = _val(r, "your_fill") if _val(r, "your_fill") is not None else _val(r, "fill_price")
                    gate_st = _val(r, "gate_status") or "PASS"
                    exit_d = _val(r, "exit_date")
                    exit_p = _val(r, "exit_price")
                    r_net = _val(r, "r_net")
                    gross_r = _val(r, "gross_r")

                    if exit_d or exit_p:
                        if r_net is not None and float(r_net) > 0:
                            status = "WIN"
                        elif r_net is not None and float(r_net) < 0:
                            status = "LOSS"
                        else:
                            status = "CLOSED"
                    elif f_price:
                        status = "ACTIVE"
                    elif gate_st == "PASS":
                        status = "PENDING"
                    else:
                        status = gate_st

                    trades.append({
                        "id": f"sugg_{r['id']}",
                        "name": _val(r, "setup_lane") or _val(r, "kind") or "Swing Setup",
                        "source": "Swing Suggestion",
                        "side": _val(r, "side") or "LONG",
                        "date": _val(r, "date"),
                        "status": status,
                        "gate_status": gate_st,
                        "gate_reasons": _val(r, "gate_reasons"),
                        "entry_type": _val(r, "entry_type") or "LIMIT",
                        "entry_low": _val(r, "entry_low"),
                        "entry_high": _val(r, "entry_high"),
                        "breakout_level": _val(r, "breakout_level"),
                        "stop_loss": _val(r, "stop"),
                        "target_1": _val(r, "target_1"),
                        "target_2": _val(r, "target_2"),
                        "planned_rr": _val(r, "planned_rr"),
                        "fill_date": _val(r, "fill_date") or _val(r, "date"),
                        "fill_price": f_price,
                        "exit_date": exit_d,
                        "exit_price": exit_p,
                        "exit_reason": _val(r, "exit_reason"),
                        "bars_held": _val(r, "bars_held"),
                        "gross_r": gross_r,
                        "r_net": r_net,
                        "mae_r": _val(r, "mae_r"),
                        "taken": bool(_val(r, "taken")),
                        "verdict": _val(r, "verdict"),
                        "notes": _val(r, "notes"),
                    })
    except Exception as e:
        logger.warning(f"Error querying suggestions for ticker trades {sym}: {e}")

    # 2. positions from trading_alerts.db
    try:
        alerts_db = config.alerts_db_path()
        if alerts_db.exists():
            with sqlite3.connect(str(alerts_db), timeout=5.0) as aconn:
                aconn.row_factory = sqlite3.Row
                c = aconn.cursor()
                table_check = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'").fetchone()
                if table_check:
                    rows = c.execute(
                        "SELECT * FROM positions WHERE UPPER(symbol) = ? ORDER BY opened_at DESC",
                        (sym,)
                    ).fetchall()
                    for r in rows:
                        strat = _val(r, "strategy") or _val(r, "strategy_id") or "Alert Ingestor Trade"
                        trades.append({
                            "id": _val(r, "trade_id") or f"pos_{sym}_{_val(r, 'opened_at')}",
                            "name": strat,
                            "source": "Alert Ingestor",
                            "side": _val(r, "side") or "LONG",
                            "status": _val(r, "status") or "CLOSED",
                            "fill_date": _val(r, "opened_at"),
                            "fill_price": _val(r, "entry_price"),
                            "stop_loss": _val(r, "stop"),
                            "target_1": _val(r, "target"),
                            "target_2": None,
                            "exit_date": _val(r, "closed_at"),
                            "exit_price": _val(r, "exit_price"),
                            "exit_reason": _val(r, "exit_reason"),
                            "quantity": _val(r, "quantity"),
                            "pnl_dollars": _val(r, "realized_broker_pnl"),
                            "notes": f"Quantity: {_val(r, 'quantity')}" if _val(r, "quantity") else "",
                        })
    except Exception as e:
        logger.warning(f"Error querying positions for ticker trades {sym}: {e}")

    # 3. schwab_positions from schwab_portfolio.db
    try:
        schwab_db = config.BASE_DIR / "data" / "schwab_portfolio.db"
        if schwab_db.exists():
            with sqlite3.connect(str(schwab_db), timeout=5.0) as sconn:
                sconn.row_factory = sqlite3.Row
                c = sconn.cursor()
                table_check = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schwab_positions'").fetchone()
                if table_check:
                    rows = c.execute(
                        "SELECT * FROM schwab_positions WHERE UPPER(symbol) = ? OR UPPER(underlying_symbol) = ? ORDER BY id DESC",
                        (sym, sym)
                    ).fetchall()
                    for r in rows:
                        desc = _val(r, "description") or _val(r, "symbol")
                        asset_type = _val(r, "asset_type", "")
                        name_str = f"Schwab {asset_type} - {desc}".strip()
                        qty = _val(r, "quantity", 0) or 0
                        trades.append({
                            "id": f"schwab_{r['id']}",
                            "name": name_str,
                            "source": "Schwab Broker",
                            "side": "LONG" if qty >= 0 else "SHORT",
                            "status": "OPEN",
                            "fill_date": (_val(r, "last_synced") or "")[:10],
                            "fill_price": _val(r, "average_price"),
                            "stop_loss": None,
                            "target_1": None,
                            "target_2": None,
                            "exit_date": None,
                            "exit_price": None,
                            "exit_reason": None,
                            "quantity": qty,
                            "pnl_dollars": _val(r, "unrealized_profit_loss"),
                            "notes": f"Market Value: ${_val(r, 'market_value', '')}, Cost Basis: ${_val(r, 'cost_basis', '')}",
                        })
    except Exception as e:
        logger.warning(f"Error querying schwab positions for ticker trades {sym}: {e}")

    total = len(trades)
    closed = sum(1 for t in trades if t.get("exit_price") is not None or t.get("status") in ["WIN", "LOSS", "CLOSED"])
    wins = sum(1 for t in trades if (t.get("r_net") is not None and float(t["r_net"]) > 0) or t.get("status") == "WIN")
    losses = sum(1 for t in trades if (t.get("r_net") is not None and float(t["r_net"]) < 0) or t.get("status") == "LOSS")
    active = sum(1 for t in trades if t.get("status") in ["ACTIVE", "FILLED", "OPEN"])
    pending = sum(1 for t in trades if t.get("status") == "PENDING")

    valid_r = [float(t["r_net"]) for t in trades if t.get("r_net") is not None]
    total_r = sum(valid_r)
    avg_r = (total_r / len(valid_r)) if valid_r else 0.0
    win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

    summary = {
        "total_suggestions": total,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "active_trades": active,
        "pending_trades": pending,
        "win_rate_pct": round(win_rate, 1),
        "total_r_net": round(total_r, 2),
        "avg_r_net": round(avg_r, 2),
    }

    return {
        "success": True,
        "ticker": sym,
        "total_trades": total,
        "summary": summary,
        "trades": trades,
    }


@router.get("/api/research/sessions")
def get_session_history(limit: int = 30):
    """Per-session summary: what arrived, what was triaged, what went to deep research.

    The queue panels answer "what is running now". This answers "what happened on the 29th",
    which is a different question and needs a different read: it joins the alerts ledger by date
    rather than looking at job rows.
    """
    try:
        # The two halves live in different files. `alerts` is in the alerts/intraday DB
        # (trading_alerts.db); research jobs are in the cockpit watch DB (research_watch.db).
        # get_db() is only the latter, so a single-connection join here fails with
        # "no such table: alerts".
        from src.tracking.alert_db import _get_connection as _alerts_conn

        with _alerts_conn() as ac:
            sessions = ac.execute("""
                SELECT date AS session,
                       COUNT(*) AS alerts,
                       SUM(CASE WHEN strategy = 'Intraday' THEN 1 ELSE 0 END) AS intraday,
                       SUM(CASE WHEN llm_decision IS NOT NULL AND TRIM(llm_decision) != ''
                                THEN 1 ELSE 0 END) AS graded,
                       SUM(CASE WHEN COALESCE(routing_stage, '') = 'ARCHIVED'
                                THEN 1 ELSE 0 END) AS archived,
                       SUM(CASE WHEN COALESCE(setup, '') != '' THEN 1 ELSE 0 END) AS with_setup
                FROM alerts
                WHERE date IS NOT NULL AND TRIM(date) != ''
                  -- A session date in the future is a fixture, not a session. data/raw carries a
                  -- 2029-01-01 test directory and it was sorting to the top of the picker.
                  AND date <= ?
                GROUP BY date
                ORDER BY date DESC
                LIMIT ?
            """, (datetime.now().strftime("%Y-%m-%d"), int(limit))).fetchall()

        with get_db() as conn:
            c = conn.cursor()
            jobs_by_date = {}
            for r in c.execute("""
                SELECT target_date AS session,
                       mode,
                       status,
                       COUNT(*) AS n
                FROM active_research_jobs
                WHERE target_date IS NOT NULL AND TRIM(target_date) != ''
                  AND target_date <= ?
                GROUP BY target_date, mode, status
            """, (datetime.now().strftime("%Y-%m-%d"),)).fetchall():
                d = dict(r)
                bucket = jobs_by_date.setdefault(d["session"], {
                    "local_running": 0, "local_queued": 0, "local_other": 0,
                    "deep_running": 0, "deep_queued": 0, "deep_done": 0, "deep_failed": 0,
                })
                is_local = d["mode"] == "local_only"
                st = (d["status"] or "").upper()
                if is_local:
                    bucket["local_running" if st == "RUNNING"
                           else "local_queued" if st == "QUEUED" else "local_other"] += d["n"]
                else:
                    if st == "RUNNING":
                        bucket["deep_running"] += d["n"]
                    elif st == "QUEUED":
                        bucket["deep_queued"] += d["n"]
                    elif st in ("COMPLETED", "DONE"):
                        bucket["deep_done"] += d["n"]
                    else:
                        bucket["deep_failed"] += d["n"]

            # Sessions that only have jobs (a run with no surviving alerts) still belong here.
            known = {dict(r)["session"] for r in sessions}
            extra = [d for d in jobs_by_date if d not in known]

        rows = []
        for r in sessions:
            d = dict(r)
            d.update(jobs_by_date.get(d["session"], {}))
            rows.append(d)
        for d in extra:
            rows.append({
                "session": d, "alerts": 0, "intraday": 0, "graded": 0, "archived": 0,
                "with_setup": 0, **jobs_by_date[d],
            })
        rows.sort(key=lambda x: x["session"], reverse=True)

        # Drop dates with neither alerts nor jobs. A weekend (or a day the poller was down) has no
        # alerts and no jobs, and an empty row in this table reads as "we lost something".
        def _is_empty(r):
            job_total = sum(
                v for k, v in r.items()
                if k.startswith(("local_", "deep_")) and isinstance(v, int)
            )
            return not r.get("alerts") and not job_total

        dropped = [r["session"] for r in rows if _is_empty(r)]
        rows = [r for r in rows if not _is_empty(r)]

        return {"sessions": rows, "limit": int(limit), "empty_dates_hidden": dropped}
    except Exception as e:
        logger.error(f"Error in get_session_history: {e}")
        return {"sessions": [], "error": str(e)}


@router.get("/api/jobs")
def get_research_jobs(date: Optional[str] = None):
    """Fetch research jobs from SQLite with accurate master-thread liveness tracking.

    `date` filters to a single session via active_research_jobs.target_date. Without it the
    panels mixed every date together, so a job that failed 21 hours ago sat in the same list as
    one that had been running for 51 seconds. `available_dates` is always returned so the UI can
    offer the picker.
    """
    with get_db() as conn:
        c = conn.cursor()
        where = ""
        params: tuple = ()
        if date and date.strip():
            where = "WHERE target_date = ?"
            params = (date.strip(),)

        available_dates = [
            r[0] for r in c.execute(
                "SELECT DISTINCT target_date FROM active_research_jobs "
                "WHERE target_date IS NOT NULL AND TRIM(target_date) != '' "
                "AND target_date <= ? "
                "ORDER BY target_date DESC LIMIT 60",
                (datetime.now().strftime("%Y-%m-%d"),),
            ).fetchall()
        ]

        jobs = c.execute(f"""
            SELECT * FROM active_research_jobs
            {where}
            ORDER BY
                CASE status
                    WHEN 'RUNNING' THEN 1
                    WHEN 'QUEUED' THEN 2
                    ELSE 3
                END,
                started_at DESC
            LIMIT 40
        """, params).fetchall()
        jobs_list = []
        for r in jobs:
            item = dict(r)
            job_id = item.get("job_id")
            thread = ACTIVE_RESEARCH_WORKERS.get(job_id)
            subproc = ACTIVE_RESEARCH_SUBPROCS.get(job_id)

            if thread is not None and thread.is_alive():
                is_alive = True
            elif subproc is not None and subproc.poll() is None:
                is_alive = True
            else:
                if item["status"] == "RUNNING":
                    pid = item.get("pid")
                    is_alive = bool(pid and psutil.pid_exists(pid))
                    if not is_alive:
                        ticker_sym = item.get("ticker", "")
                        live_pid = find_live_research_pid(ticker_sym, job_id)
                        if live_pid:
                            is_alive = True
                            item["pid"] = live_pid

                    if not is_alive and item.get("stage") not in ("SCRAPING", "STARTING"):
                        t_date = item.get("target_date") or datetime.now().strftime("%Y-%m-%d")
                        ticker_sym = item.get("ticker", "")
                        rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_summary.md"
                        arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_arbitration.md"
                        if rep_file.exists() or arb_file.exists():
                            item["status"] = "COMPLETED"
                            item["stage"] = "DONE"
                        else:
                            item["status"] = "FAILED"
                            item["stage"] = "ERROR"
                            item["error_message"] = "Process terminated before generating report"
                else:
                    is_alive = False

            item["is_alive"] = is_alive
            if job_id:
                item["log_url"] = f"/api/jobs/{job_id}/logs"
                item["raw_log_url"] = f"/api/jobs/{job_id}/logs?raw=true"
            jobs_list.append(item)
        local_queue = [j for j in jobs_list if j.get("mode") == "local_only"]
        deep_queue = [j for j in jobs_list if j.get("mode") != "local_only"]
        active_local_count = get_active_local_research_count()
        active_full_count = get_active_full_research_count()
        active_mid_count = get_active_mid_research_count()
        active_deep_count = get_active_deep_research_count()
        return {
            "jobs": jobs_list,
            "local_queue": local_queue,
            "deep_queue": deep_queue,
            # Slot counts stay GLOBAL, not date-scoped: you cannot free a GPU slot by filtering
            # to another day, so showing a per-date count would under-report real occupancy.
            "local_slots_used": active_local_count,
            "local_slots_max": MAX_CONCURRENT_LOCAL,
            "full_slots_used": active_full_count,
            "full_slots_max": MAX_CONCURRENT_FULL,
            "mid_slots_used": active_mid_count,
            "mid_slots_max": MAX_CONCURRENT_MID,
            "deep_slots_used": active_deep_count,
            "deep_slots_max": MAX_CONCURRENT_DEEP,
            "max_concurrent": MAX_CONCURRENT_RESEARCH,
            "date": (date or "").strip(),
            "available_dates": available_dates,
        }


class ResearchRequest(BaseModel):
    ticker: Optional[str] = None
    tickers: Optional[List[str]] = None
    mode: str = "full"  # "full" | "scrape_only" | "deep_only" | "local_only"
    date: Optional[str] = None
    force: bool = False
    tier: str = "MID"  # "LITE" | "MID" | "FULL"


@router.post("/api/research/run")
def trigger_research(req: ResearchRequest):
    """Trigger research pipeline asynchronously with automatic slot queueing and SQLite tracking."""
    init_db()

    ticker_raw = (req.ticker or "").strip()
    if not ticker_raw and req.tickers:
        ticker_raw = ",".join(str(t) for t in req.tickers if t).strip()
    if not ticker_raw:
        raise HTTPException(status_code=400, detail="Ticker is required")

    tickers = [t.strip().upper() for t in re.split(r"[,;\s]+", ticker_raw) if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Ticker is required")
    for t in tickers:
        if not re.match(r"^[A-Z0-9]{1,6}(?:/[A-Z0-9]{1,2})?$", t):
            raise HTTPException(status_code=400, detail=f"Invalid ticker format: {t}")
    if req.date and req.date.strip():
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', req.date.strip()):
            raise HTTPException(status_code=400, detail=f"Invalid date format: {req.date}")

    req_tier = str(req.tier or "MID").upper().strip()
    if req_tier not in ("LITE", "MID", "FULL"):
        req_tier = "MID"

    if len(tickers) > 1:
        results = []
        for t in tickers:
            sub_req = ResearchRequest(ticker=t, mode=req.mode, date=req.date, force=req.force, tier=req_tier)
            results.append(trigger_research(sub_req))
        started = sum(1 for r in results if r.get("status") == "started")
        queued = sum(1 for r in results if r.get("status") == "queued")
        return {
            "status": "batch_dispatched",
            "count": len(results),
            "started": started,
            "queued": queued,
            "jobs": results,
        }

    ticker_u = tickers[0]

    # Prevent duplicate active or queued jobs for the same ticker
    with get_db() as conn:
        c = conn.cursor()
        # Ensure tier column exists
        try:
            c.execute("ALTER TABLE active_research_jobs ADD COLUMN tier TEXT DEFAULT 'MID'")
        except Exception:
            pass
        existing = c.execute(
            "SELECT job_id, status, pid FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING', 'QUEUED')",
            (ticker_u,)
        ).fetchone()
        if existing:
            jid = existing["job_id"]
            thread = ACTIVE_RESEARCH_WORKERS.get(jid)
            is_alive = thread.is_alive() if thread else False
            existing_pid = existing["pid"] if "pid" in existing.keys() else None
            if not is_alive and existing_pid:
                try:
                    is_alive = psutil.pid_exists(existing_pid)
                except Exception:
                    is_alive = False

            if not is_alive:
                live_pid = find_live_research_pid(ticker_u, jid)
                if live_pid:
                    is_alive = True
                    c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, jid))
                    conn.commit()

            if existing["status"] == "QUEUED" or is_alive:
                return {
                    "status": existing["status"].lower(),
                    "job_id": jid,
                    "ticker": ticker_u,
                    "mode": req.mode,
                    "tier": req_tier,
                }
            else:
                c.execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = 'Process died unexpectedly', completed_at = ? WHERE job_id = ?",
                    (datetime.now(timezone.utc).isoformat(), jid),
                )
                conn.commit()

    if not req.force and req.mode in ("full", "deep_only"):
        target_date = req.date.strip() if (req.date and req.date.strip()) else datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
        report_file = config.BASE_DIR / "reports" / target_date / f"{ticker_u}_summary.md"
        arbitration_file = config.BASE_DIR / "reports" / target_date / f"{ticker_u}_arbitration.md"
        if report_file.exists() or arbitration_file.exists():
            append_log(f"⏭️ [Skip] {ticker_u} already has a completed deep research report for {target_date}. Skipping to preserve slots.")
            return {
                "status": "skipped",
                "job_id": None,
                "ticker": ticker_u,
                "mode": req.mode,
                "tier": req_tier,
                "target_date": target_date,
                "message": f"{ticker_u} already researched for {target_date}",
            }

    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker_u}"
    log_file = str(LOGS_DIR / f"{job_id}.log")

    is_local_job = (req.mode == "local_only")
    if is_local_job:
        active_slot_count = get_active_local_research_count()
        max_slots = MAX_CONCURRENT_LOCAL
        q_label = "Local Queue"
    elif req_tier == "FULL":
        active_slot_count = get_active_full_research_count()
        max_slots = MAX_CONCURRENT_FULL
        q_label = "Full Deep Slot"
    else:
        active_slot_count = get_active_mid_research_count()
        max_slots = MAX_CONCURRENT_MID
        q_label = "Mid Deep Slot"

    if active_slot_count < max_slots:
        try:
            with get_db() as conn:
                start_stage = "LOCAL_TRIAGE" if is_local_job else "STARTING"
                start_detail = "Starting Local Triage" if is_local_job else f"Starting Deep Research ({req_tier})"
                conn.cursor().execute("""
                    INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date, stage_detail, tier)
                    VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?)
                """, (job_id, ticker_u, req.mode, os.getpid(), start_stage, datetime.now(timezone.utc).isoformat(), log_file, req.date, start_detail, req_tier))
                conn.commit()
        except sqlite3.IntegrityError:
            # Duplicate active job for this ticker — return the existing one
            with get_db() as conn:
                row = conn.cursor().execute(
                    "SELECT job_id FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING','QUEUED')",
                    (ticker_u,),
                ).fetchone()
            if row:
                return {"status": "running", "job_id": row["job_id"], "ticker": ticker_u, "mode": req.mode, "tier": req_tier}
            raise HTTPException(status_code=409, detail="Duplicate job conflict for ticker")

        worker_thread = threading.Thread(target=run_research_worker, args=(job_id, ticker_u, req.mode, req.date, req.force, req_tier), daemon=True)
        ACTIVE_RESEARCH_WORKERS[job_id] = worker_thread
        worker_thread.start()
        append_log(f"🚀 Started research [{req_tier}] for {ticker_u} in open slot ({q_label}: {active_slot_count + 1}/{max_slots}).")
        return {"status": "started", "job_id": job_id, "ticker": ticker_u, "mode": req.mode, "tier": req_tier, "stage": start_stage, "log_file": log_file, "started_at": datetime.now(timezone.utc).isoformat()}
    else:
        try:
            with get_db() as conn:
                q_detail = "Queued for Local Triage" if is_local_job else f"Queued for Deep Research ({req_tier})"
                conn.cursor().execute("""
                    INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date, stage_detail, tier)
                    VALUES (?, ?, ?, ?, 'QUEUED', 'QUEUED', ?, ?, ?, ?, ?)
                """, (job_id, ticker_u, req.mode, None, datetime.now(timezone.utc).isoformat(), log_file, req.date, q_detail, req_tier))
                conn.commit()
        except sqlite3.IntegrityError:
            with get_db() as conn:
                row = conn.cursor().execute(
                    "SELECT job_id FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING','QUEUED')",
                    (ticker_u,),
                ).fetchone()
            if row:
                return {"status": "queued", "job_id": row["job_id"], "ticker": ticker_u, "mode": req.mode, "tier": req_tier}
            raise HTTPException(status_code=409, detail="Duplicate job conflict for ticker")

        q_label = "Local Queue" if is_local_job else "Deep Queue"
        append_log(f"📥 [{q_label}] Concurrency slots full ({active_slot_count}/{max_slots}). Queued {ticker_u} [{req_tier}] for research.")
        return {"status": "queued", "job_id": job_id, "ticker": ticker_u, "mode": req.mode, "tier": req_tier, "stage": "QUEUED", "log_file": log_file, "started_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/research/timings")
def get_research_timings_endpoint(
    ticker: Optional[str] = None,
    job_id: Optional[str] = None,
    target_date: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 100,
):
    """Retrieve stage timings recorded for deep research jobs."""
    from src.tracking.watch_manager import get_research_timings
    d = target_date or date
    timings = get_research_timings(ticker=ticker, job_id=job_id, target_date=d, limit=limit)
    return {"status": "ok", "count": len(timings), "timings": timings}


@router.post("/api/jobs/{job_id}/kill")
def kill_research_job_endpoint(job_id: str):
    """Terminate a specific research job and its entire subprocess tree, freeing slot for queue."""
    subproc = ACTIVE_RESEARCH_SUBPROCS.get(job_id)
    if subproc:
        try:
            subproc.kill()
        except Exception:
            pass

    with get_db() as conn:
        c = conn.cursor()
        job = c.execute("SELECT * FROM active_research_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job:
            pid = job["pid"]
            if pid and psutil.pid_exists(pid) and pid != os.getpid():
                try:
                    parent = psutil.Process(pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except Exception:
                    pass

            c.execute(
                "UPDATE active_research_jobs SET status = 'KILLED', stage = 'TERMINATED', completed_at = ? WHERE job_id = ?",
                (datetime.now(timezone.utc).isoformat(), job_id),
            )
            conn.commit()

    ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
    ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)

    ticker_name = job["ticker"] if job else job_id
    kill_msg = f"🛑 Terminated Research Job {job_id} ({ticker_name}) to free VRAM slot."
    append_log(kill_msg)

    # Write kill marker to the job log file so the log viewer shows why it stopped
    try:
        log_file = LOGS_DIR / f"{job_id}.log"
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Job killed by user via UI. Process tree terminated.\n")
    except Exception:
        pass

    dispatch_next_queued_job()
    return {"status": "killed", "job_id": job_id}


def _resolve_job_record_and_log(job_id: str) -> tuple[Optional[dict], Optional[Path]]:
    """Resolve database job record and log file path for a given job_id or ticker symbol."""
    raw_str = (job_id or "").strip()
    if not raw_str:
        return None, None
    clean_id = Path(raw_str).name
    if clean_id.endswith(".log"):
        clean_id = clean_id[:-4]

    job_row = None
    with get_db() as conn:
        c = conn.cursor()
        r = c.execute("SELECT * FROM active_research_jobs WHERE job_id = ?", (clean_id,)).fetchone()
        if r:
            job_row = dict(r)
        else:
            r = c.execute(
                "SELECT * FROM active_research_jobs WHERE UPPER(ticker) = ? ORDER BY started_at DESC LIMIT 1",
                (clean_id.upper(),),
            ).fetchone()
            if r:
                job_row = dict(r)

    candidates: list[Path] = []
    if job_row and job_row.get("log_file"):
        candidates.append(Path(job_row["log_file"]))
    candidates.append(LOGS_DIR / f"{clean_id}.log")
    if job_row:
        candidates.append(LOGS_DIR / f"{job_row['job_id']}.log")
        ticker = job_row.get("ticker")
        if ticker:
            for p in sorted(LOGS_DIR.glob(f"job_*_{ticker}.log"), reverse=True):
                candidates.append(p)
            for p in sorted(LOGS_DIR.glob(f"*{ticker}*.log"), reverse=True):
                candidates.append(p)

    resolved_path = None
    for cand in candidates:
        if cand and cand.exists():
            resolved_path = cand.resolve()
            break

    # Security check: prevent directory traversal outside workspace
    if resolved_path:
        base_res = str(config.BASE_DIR.resolve())
        logs_res = str(LOGS_DIR.resolve())
        p_res = str(resolved_path)
        if not (p_res.startswith(logs_res) or p_res.startswith(base_res)):
            return job_row, None

    if not resolved_path:
        target_id = job_row["job_id"] if job_row else clean_id
        resolved_path = (LOGS_DIR / f"{target_id}.log").resolve()

    return job_row, resolved_path


@router.get("/api/jobs/{job_id}")
def get_research_job_endpoint(job_id: str):
    """Retrieve metadata, execution status, and log endpoints for a specific research job or ticker."""
    job_row, log_path = _resolve_job_record_and_log(job_id)
    if not job_row and (not log_path or not log_path.exists()):
        raise HTTPException(status_code=404, detail=f"No job or log file found for '{job_id}'")

    jid = job_row["job_id"] if job_row else Path(job_id).name
    is_alive = False
    if job_row:
        thread = ACTIVE_RESEARCH_WORKERS.get(jid)
        subproc = ACTIVE_RESEARCH_SUBPROCS.get(jid)
        if thread is not None and thread.is_alive():
            is_alive = True
        elif subproc is not None and subproc.poll() is None:
            is_alive = True
        elif job_row.get("status") == "RUNNING":
            pid = job_row.get("pid")
            is_alive = bool(pid and psutil.pid_exists(pid))
            if not is_alive:
                ticker_sym = job_row.get("ticker", "")
                live_pid = find_live_research_pid(ticker_sym, jid)
                if live_pid:
                    is_alive = True
                    job_row["pid"] = live_pid

    has_log = bool(log_path and log_path.exists())
    log_size = log_path.stat().st_size if has_log else 0

    return {
        "status": "ok",
        "job_id": jid,
        "ticker": job_row.get("ticker") if job_row else None,
        "is_alive": is_alive,
        "has_log": has_log,
        "log_file": str(log_path) if log_path else None,
        "log_size_bytes": log_size,
        "log_url": f"/api/jobs/{jid}/logs",
        "raw_log_url": f"/api/jobs/{jid}/logs?raw=true",
        "job": job_row,
    }


@router.get("/api/jobs/{job_id}/logs")
@router.get("/api/jobs/{job_id}/log")
def get_job_logs_endpoint(
    job_id: str,
    lines: int = 500,
    tail: Optional[int] = None,
    offset: int = 0,
    raw: bool = False,
    format: Optional[str] = None,
    download: bool = False,
):
    """Expose research job logs via the API (JSON lines by default, or plain text / download)."""
    job_row, log_path = _resolve_job_record_and_log(job_id)
    jid = job_row["job_id"] if job_row else Path(job_id).name
    t_sym = job_row.get("ticker") if job_row else None

    # Handle QUEUED state before log file is created
    if job_row and job_row.get("status") == "QUEUED" and (not log_path or not log_path.exists()):
        msg = f"Job {jid} ({t_sym or 'unknown'}) is currently QUEUED. Log file will be generated once execution begins."
        if raw or (format and format.lower() in ("raw", "text", "plain")):
            return PlainTextResponse(msg)
        return {
            "status": "queued",
            "job_id": jid,
            "ticker": t_sym,
            "job_status": "QUEUED",
            "stage": job_row.get("stage", "QUEUED"),
            "stage_detail": job_row.get("stage_detail"),
            "tier": job_row.get("tier"),
            "mode": job_row.get("mode"),
            "is_alive": False,
            "message": msg,
            "total_lines": 0,
            "returned_lines": 0,
            "lines": [],
            "logs": [],
            "log_file": str(log_path) if log_path else None,
            "raw_url": f"/api/jobs/{jid}/logs?raw=true",
        }

    if not log_path or not log_path.exists():
        if job_row:
            err_msg = job_row.get("error_message") or f"No log content recorded yet for job {jid} (status: {job_row.get('status')})."
            if raw or (format and format.lower() in ("raw", "text", "plain")):
                return PlainTextResponse(err_msg)
            return {
                "status": "ok",
                "job_id": jid,
                "ticker": t_sym,
                "job_status": job_row.get("status", "UNKNOWN"),
                "stage": job_row.get("stage"),
                "stage_detail": job_row.get("stage_detail"),
                "tier": job_row.get("tier"),
                "mode": job_row.get("mode"),
                "is_alive": False,
                "message": err_msg,
                "total_lines": 0,
                "returned_lines": 0,
                "lines": [],
                "logs": [],
                "log_file": str(log_path) if log_path else None,
                "raw_url": f"/api/jobs/{jid}/logs?raw=true",
            }
        raise HTTPException(status_code=404, detail=f"No log file found for job '{job_id}'")

    if download:
        return FileResponse(
            path=str(log_path),
            filename=f"{jid}.log",
            media_type="text/plain",
        )

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")

    if raw or (format and format.lower() in ("raw", "text", "plain")):
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    all_lines = content.splitlines()
    limit = tail if tail is not None else lines

    if limit and limit > 0:
        sliced = all_lines[-limit:]
    else:
        sliced = all_lines

    if offset and offset > 0:
        sliced = sliced[offset:]

    is_alive = False
    if job_row:
        thread = ACTIVE_RESEARCH_WORKERS.get(jid)
        subproc = ACTIVE_RESEARCH_SUBPROCS.get(jid)
        if thread is not None and thread.is_alive():
            is_alive = True
        elif subproc is not None and subproc.poll() is None:
            is_alive = True
        elif job_row.get("status") == "RUNNING":
            pid = job_row.get("pid")
            is_alive = bool(pid and psutil.pid_exists(pid))

    return {
        "status": "ok",
        "job_id": jid,
        "ticker": t_sym,
        "job_status": job_row.get("status") if job_row else "COMPLETED",
        "stage": job_row.get("stage") if job_row else "DONE",
        "stage_detail": job_row.get("stage_detail") if job_row else None,
        "tier": job_row.get("tier") if job_row else None,
        "mode": job_row.get("mode") if job_row else None,
        "is_alive": is_alive,
        "error_message": job_row.get("error_message") if job_row else None,
        "started_at": job_row.get("started_at") if job_row else None,
        "completed_at": job_row.get("completed_at") if job_row else None,
        "target_date": job_row.get("target_date") if job_row else None,
        "log_file": str(log_path),
        "total_lines": len(all_lines),
        "returned_lines": len(sliced),
        "lines": sliced,
        "logs": sliced,
        "raw_url": f"/api/jobs/{jid}/logs?raw=true",
    }


@router.get("/api/jobs/{job_id}/raw")
def get_job_raw_log_endpoint(job_id: str):
    """Direct plain-text endpoint for a job log."""
    return get_job_logs_endpoint(job_id=job_id, raw=True)


