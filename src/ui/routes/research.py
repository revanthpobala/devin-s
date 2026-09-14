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
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import psutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src import config
from src.ui.services.research_queue import (
    dispatch_next_queued_job,
    find_live_research_pid,
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
    if not levels_data and not db_target:
        for md_cand in [rep_dir / f"{ticker_u}_arbitration.md", rep_dir / f"{ticker_u}_summary.md"]:
            if md_cand.exists():
                try:
                    txt = md_cand.read_text(encoding="utf-8")
                    m = re.search(r'```json:watch_levels\s*(\{.*?\})\s*```', txt, re.DOTALL)
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

        # 1. Check target_date raw/ folders for scraped candidates
        if raw_root.exists():
            for item in raw_root.iterdir():
                if item.is_dir():
                    sym = item.name.upper()
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
                            "reason": "Chart & Data Window ready on disk",
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
                            "date": now_date,
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

        # 3. Desk priority candidates if queue is small (<6)
        priority_candidates = ["UBER", "NVDA", "META", "TSLA", "NFLX", "PLTR", "AMD"]
        for p_sym in priority_candidates:
            if p_sym not in seen and len(queue) < 6:
                if rep_root.exists() and (rep_root / f"{p_sym}_arbitration.md").exists():
                    continue
                reports_base = config.BASE_DIR / "reports"
                already_has_recent_report = False
                if reports_base.exists():
                    latest_rep_dirs = sorted(
                        [d for d in reports_base.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
                        reverse=True,
                    )[:2]
                    for d in latest_rep_dirs:
                        if (d / f"{p_sym}_arbitration.md").exists():
                            already_has_recent_report = True
                            break
                if already_has_recent_report:
                    continue

                queue.append({
                    "ticker": p_sym,
                    "date": now_date,
                    "status": "READY_TO_LAUNCH",
                    "action": "full",
                    "action_label": "🚀 Scrape & Run",
                    "reason": "Desk Priority",
                    "has_chart": False,
                    "has_report": False,
                })
                seen.add(p_sym)

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


@router.get("/api/report/{ticker}")
def get_report_bundle_single(ticker: str):
    """Convenience alias resolving the latest available report bundle for a ticker."""
    return get_report_bundle("latest", ticker)


@router.get("/api/report/{date}/{ticker}")
def get_report_bundle(date: str, ticker: str):
    """Return all 3 model markdown reports, available dates, and historical timeline for a ticker."""
    ticker_u = ticker.upper().strip()
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

    report_dates_set = set()
    scrape_dates_set = set()

    if rep_root.exists():
        for d in rep_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                if (d / f"{ticker_u}_arbitration.md").exists() or (d / f"{ticker_u}_summary.md").exists() or (d / f"{ticker_u}_independent.md").exists():
                    report_dates_set.add(d.name)

    if raw_root.exists():
        for d in raw_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                sym_dir = d / ticker_u
                if sym_dir.exists():
                    if (
                        (sym_dir / f"{ticker_u}_arbitration.md").exists()
                        or (sym_dir / f"{ticker_u}_gemini_thesis.md").exists()
                        or (sym_dir / f"{ticker_u}_thesis.md").exists()
                        or (sym_dir / f"{ticker_u}_watch_levels.json").exists()
                    ):
                        report_dates_set.add(d.name)
                    elif (sym_dir / f"{ticker_u}_datawindow.json").exists():
                        scrape_dates_set.add(d.name)

    try:
        with get_db() as conn:
            rows = conn.cursor().execute(
                "SELECT DISTINCT date FROM watch_targets WHERE ticker = ? ORDER BY date DESC",
                (ticker_u,)
            ).fetchall()
            for r in rows:
                if r["date"] and r["date"].startswith("202"):
                    report_dates_set.add(r["date"][:10])
    except Exception:
        pass

    all_sorted_dates = sorted(list(report_dates_set if report_dates_set else scrape_dates_set), reverse=True)

    req_date = (date or "").strip()
    target_date = req_date
    if all_sorted_dates and (target_date not in all_sorted_dates or target_date in ("latest", "today", "now", "", "undefined", "null")):
        target_date = all_sorted_dates[0]
    elif not target_date and all_sorted_dates:
        target_date = all_sorted_dates[0]
    elif not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    ref_dt = datetime.strptime(target_date, "%Y-%m-%d") if re.match(r"^\d{4}-\d{2}-\d{2}$", target_date) else datetime.now()
    cutoff_dt_str = (ref_dt - timedelta(days=35)).strftime("%Y-%m-%d")

    available_dates = [d for d in all_sorted_dates if d >= cutoff_dt_str or d == target_date]
    if not available_dates:
        available_dates = all_sorted_dates[:10] if all_sorted_dates else [target_date]
    if target_date not in available_dates:
        available_dates.insert(0, target_date)

    rep_dir = rep_root / target_date
    raw_dir = raw_root / target_date / ticker_u

    summary_file = rep_dir / f"{ticker_u}_summary.md"
    if not summary_file.exists() and raw_dir.exists():
        for cand_name in (f"{ticker_u}_gemini_thesis.md", f"{ticker_u}_thesis.md"):
            cand = raw_dir / cand_name
            if cand.exists():
                summary_file = cand
                break

    ind_file = rep_dir / f"{ticker_u}_independent.md"
    if not ind_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_independent_thesis.md"
        if cand.exists():
            ind_file = cand

    arb_file = rep_dir / f"{ticker_u}_arbitration.md"
    if not arb_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_arbitration.md"
        if cand.exists():
            arb_file = cand

    summary_md = summary_file.read_text(encoding="utf-8") if summary_file.exists() else None
    independent_md = ind_file.read_text(encoding="utf-8") if ind_file.exists() else None
    arbitration_md = arb_file.read_text(encoding="utf-8") if arb_file.exists() else None

    if not arbitration_md and (summary_md or independent_md):
        arbitration_md = f"# {ticker_u} | ARBITRATION & EXECUTIVE RESEARCH DOSSIER ({target_date})\n\n*(Displaying primary research report for {target_date})*\n\n" + (independent_md or summary_md)

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
            continue

        txt = chosen_doc.read_text(encoding="utf-8")

        spot_val = None
        for dw_cand in [t_raw / f"{ticker_u}_datawindow.json", t_raw_date / f"{ticker_u}_datawindow.json"]:
            if dw_cand.exists():
                try:
                    dw = json.loads(dw_cand.read_text(encoding="utf-8"))
                    for k in ["close", "Close", "last", "Last", "bar_close"]:
                        if k in dw and dw[k]:
                            spot_val = float(dw[k])
                            break
                except Exception:
                    pass
            if spot_val:
                break

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

        preview_text = None
        m_th = re.search(r'\*\*(?:The\s+)?Thesis in 2 Sentences:\*\*\s*([^\n\r]+(?:\n[^\n\r#]+)?)', txt)
        if m_th:
            preview_text = m_th.group(1).replace("**", "").strip()[:240]
        else:
            paragraphs = [p.strip() for p in txt.split('\n\n') if p.strip() and not p.strip().startswith('#') and not p.strip().startswith('<tool_call>')]
            if paragraphs:
                preview_text = paragraphs[0][:220]

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
    if levels_file.exists():
        try:
            watch_levels = json.loads(levels_file.read_text(encoding="utf-8"))
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
        if watch_levels:
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
            lines.append("---\n*Detailed 3-model deep research narrative pending. Live quotes, options chain, and interactive Copilot are active in the right-hand panel.*")
            arbitration_md = "\n".join(lines)
        else:
            arbitration_md = (
                f"# {ticker_u} | RESEARCH DOSSIER ({target_date})\n\n"
                f"No archived deep-research report found on disk for **{ticker_u}**.\n\n"
                f"👉 Use the interactive **AI Dossier Copilot** on the right to fetch live quotes, inspect options chains, or trigger fresh analysis."
            )

    result_payload = {
        "ticker": ticker_u,
        "date": target_date,
        "live_price": live_price,
        "user_position": user_position,
        "trade_ideas": trade_ideas,
        "available_dates": available_dates,
        "historical_timeline": timeline,
        "summary_md": summary_md,
        "independent_md": independent_md,
        "arbitration_md": arbitration_md,
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
    """Fetch live real-time price and day stats for a ticker directly from Schwab."""
    from src.clients.price_client import get_current_price

    sym = ticker.upper().strip()
    price = None
    net_change = 0.0
    net_pct = 0.0
    bid = 0.0
    ask = 0.0
    volume = 0
    source = "SCHWAB"

    try:
        from src.clients.schwab_client import get_realtime_quote

        sq = get_realtime_quote(sym)
        if sq and sq.get("last_price"):
            price = float(sq["last_price"])
            net_change = float(sq.get("net_change") or 0.0)
            net_pct = float(sq.get("net_percent_change") or 0.0)
            bid = float(sq.get("bid") or 0.0)
            ask = float(sq.get("ask") or 0.0)
            volume = int(sq.get("volume") or 0)
    except Exception as e:
        logger.debug(f"Schwab quote error for {sym}: {e}")

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
                    "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
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
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/options-flow/{ticker}")
def get_options_flow_endpoint(ticker: str, refresh: bool = False):
    """Fetch unusual options flow anomalies and institutional sweep metrics from Schwab API."""
    from src.clients.schwab_client import get_unusual_options_flow_data

    sym = ticker.upper().strip()
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
    img_path = find_chart_path(date, ticker, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail="Chart image not found")
    return FileResponse(str(img_path), media_type="image/png")


@router.get("/api/charts/latest/{ticker}/{chart_type}")
def get_latest_chart_image(ticker: str, chart_type: str):
    """Serve most recent chart PNG image for a ticker without requiring a date."""
    img_path = find_chart_path("", ticker, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Chart image not found for {ticker}")
    return FileResponse(str(img_path), media_type="image/png")


@router.get("/api/jobs")
def get_research_jobs():
    """Fetch all research jobs from SQLite database with accurate master-thread liveness tracking."""
    with get_db() as conn:
        c = conn.cursor()
        jobs = c.execute("""
            SELECT * FROM active_research_jobs 
            ORDER BY 
                CASE status 
                    WHEN 'RUNNING' THEN 1 
                    WHEN 'QUEUED' THEN 2 
                    ELSE 3 
                END, 
                started_at DESC 
            LIMIT 40
        """).fetchall()
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
                            c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, job_id))

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
                        c.execute(
                            "UPDATE active_research_jobs SET status = ?, stage = ?, error_message = ? WHERE job_id = ?",
                            (item["status"], item["stage"], item.get("error_message"), job_id),
                        )
                else:
                    is_alive = False

            item["is_alive"] = is_alive
            jobs_list.append(item)
        conn.commit()
        return {"jobs": jobs_list, "max_concurrent": MAX_CONCURRENT_RESEARCH}


class ResearchRequest(BaseModel):
    ticker: str
    mode: str = "full"  # "full" | "scrape_only" | "deep_only"
    date: Optional[str] = None
    force: bool = False


@router.post("/api/research/run")
def trigger_research(req: ResearchRequest):
    """Trigger research pipeline asynchronously with automatic slot queueing and SQLite tracking."""
    init_db()

    ticker_raw = req.ticker.strip()
    if not ticker_raw:
        raise HTTPException(status_code=400, detail="Ticker is required")

    tickers = [t.strip().upper() for t in re.split(r"[,;\s]+", ticker_raw) if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Ticker is required")

    if len(tickers) > 1:
        results = []
        for t in tickers:
            sub_req = ResearchRequest(ticker=t, mode=req.mode, date=req.date, force=req.force)
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
        existing = c.execute(
            "SELECT job_id, status, pid FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING', 'QUEUED')",
            (ticker_u,)
        ).fetchone()
        if existing:
            jid = existing["job_id"]
            thread = ACTIVE_RESEARCH_WORKERS.get(jid)
            is_alive = thread.is_alive() if thread else False
            if not is_alive and existing.get("pid"):
                try:
                    is_alive = psutil.pid_exists(existing["pid"])
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
                "target_date": target_date,
                "message": f"{ticker_u} already researched for {target_date}",
            }

    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker_u}"
    log_file = str(LOGS_DIR / f"{job_id}.log")

    active_count = get_active_research_count()

    if active_count < MAX_CONCURRENT_RESEARCH:
        with get_db() as conn:
            conn.cursor().execute("""
                INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date)
                VALUES (?, ?, ?, ?, 'STARTING', 'RUNNING', ?, ?, ?)
            """, (job_id, ticker_u, req.mode, os.getpid(), datetime.now(timezone.utc).isoformat(), log_file, req.date))
            conn.commit()

        worker_thread = threading.Thread(target=run_research_worker, args=(job_id, ticker_u, req.mode, req.date, req.force), daemon=True)
        ACTIVE_RESEARCH_WORKERS[job_id] = worker_thread
        worker_thread.start()
        append_log(f"🚀 Started research for {ticker_u} in open slot (Active: {active_count + 1}/{MAX_CONCURRENT_RESEARCH}).")
        return {"status": "started", "job_id": job_id, "ticker": ticker_u, "mode": req.mode}
    else:
        with get_db() as conn:
            conn.cursor().execute("""
                INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date)
                VALUES (?, ?, ?, ?, 'QUEUED', 'QUEUED', ?, ?, ?)
            """, (job_id, ticker_u, req.mode, None, datetime.now(timezone.utc).isoformat(), log_file, req.date))
            conn.commit()
        append_log(f"📥 [Queue] Concurrency slots full ({active_count}/{MAX_CONCURRENT_RESEARCH}). Queued {ticker_u} for research.")
        return {"status": "queued", "job_id": job_id, "ticker": ticker_u, "mode": req.mode}


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
    append_log(f"🛑 Terminated Research Job {job_id} ({ticker_name}) to free VRAM slot.")
    dispatch_next_queued_job()
    return {"status": "killed", "job_id": job_id}
