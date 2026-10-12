"""
scripts/run_arbitration_pass.py

Fast-path standalone Senior PM Ponytail Arbitration runner.
Runs Stage 5 (Senior PM Judicial Arbitration) for any ticker where Model A & Model B
reports already exist, without needing to re-run the expensive tool-calling passes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Set up project root
BASE_DIR = Path(r"D:\My-Projects\Stock")
sys.path.insert(0, str(BASE_DIR))

from src import config
from src.logic.deep_research.arbitration import run_arbitration
from src.plugins.schwab_plugin import format_active_position_block

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (FastArbitration) %(message)s",
)
logger = logging.getLogger(__name__)


def run_ticker_arbitration(ticker: str, date_str: str | None = None) -> bool:
    ticker_u = ticker.strip().upper()
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")

    tdir = config.BASE_DIR / "data" / "triage" / date_str
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str
    reports_dir = config.BASE_DIR / "reports" / date_str
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 1. Locate Model A (summary) and Model B (independent)
    sum_candidates = [
        reports_dir / f"{ticker_u}_summary.md",
        tdir / "force" / ticker_u / f"{ticker_u}_gemini_thesis.md",
        tdir / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_gemini_thesis.md",
        raw_dir / ticker_u / f"{ticker_u}_summary.md",
    ]
    ind_candidates = [
        reports_dir / f"{ticker_u}_independent.md",
        tdir / "force" / ticker_u / f"{ticker_u}_independent_thesis.md",
        tdir / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_independent_thesis.md",
        raw_dir / ticker_u / f"{ticker_u}_independent_thesis.md",
    ]

    clean_response = None
    for c in sum_candidates:
        if c.exists() and c.stat().st_size > 500:
            clean_response = c.read_text(encoding="utf-8")
            logger.info(f"[{ticker_u}] Found Model A report at {c} ({len(clean_response)} chars)")
            break

    clean_ind_response = None
    for c in ind_candidates:
        if c.exists() and c.stat().st_size > 500:
            clean_ind_response = c.read_text(encoding="utf-8")
            logger.info(f"[{ticker_u}] Found Model B report at {c} ({len(clean_ind_response)} chars)")
            break

    if not clean_response:
        logger.error(f"[{ticker_u}] Model A summary report not found in candidates: {sum_candidates}")
        return False
    if not clean_ind_response:
        logger.error(f"[{ticker_u}] Model B independent report not found in candidates: {ind_candidates}")
        return False

    # Ensure reports directory has the canonical copies
    canon_sum = reports_dir / f"{ticker_u}_summary.md"
    if not canon_sum.exists():
        canon_sum.write_text(clean_response, encoding="utf-8")
    canon_ind = reports_dir / f"{ticker_u}_independent.md"
    if not canon_ind.exists():
        canon_ind.write_text(clean_ind_response, encoding="utf-8")

    # 2. Locate Data Window JSON
    dw_candidates = [
        tdir / "force" / ticker_u / f"{ticker_u}_datawindow.json",
        tdir / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_datawindow.json",
        raw_dir / ticker_u / f"{ticker_u}_datawindow.json",
        config.BASE_DIR / "data" / "artifacts" / date_str / ticker_u / f"{ticker_u}_datawindow.json",
    ]
    dw_dict = {}
    for c in dw_candidates:
        if c.exists():
            try:
                dw_dict = json.loads(c.read_text(encoding="utf-8"))
                if dw_dict:
                    logger.info(f"[{ticker_u}] Loaded Data Window from {c}")
                    break
            except Exception as e:
                logger.debug(f"Failed parsing {c}: {e}")

    # 3. Locate thesis / triage record
    thesis_candidates = [
        tdir / "force" / ticker_u / f"{ticker_u}_thesis.json",
        tdir / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_thesis.json",
        raw_dir / ticker_u / f"{ticker_u}_thesis.json",
    ]
    thesis_data = {}
    for c in thesis_candidates:
        if c.exists():
            try:
                thesis_data = json.loads(c.read_text(encoding="utf-8"))
                if thesis_data:
                    break
            except Exception:
                pass

    triage_record = thesis_data.get("triage", {}) if isinstance(thesis_data, dict) else {}
    f_parsed = triage_record if isinstance(triage_record, dict) else {}

    # 4. Deep context JSON
    deep_ctx_candidates = [
        tdir / "force" / ticker_u / f"{ticker_u}_deep_context.json",
        tdir / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_deep_context.json",
        raw_dir / ticker_u / f"{ticker_u}_deep_context.json",
    ]
    deep_ctx = {}
    for c in deep_ctx_candidates:
        if c.exists():
            try:
                deep_ctx = json.loads(c.read_text(encoding="utf-8"))
                if deep_ctx:
                    break
            except Exception:
                pass

    live_quote_block = deep_ctx.get("live_quote_block", "")
    earnings_fact_block = deep_ctx.get("earnings_fact_block", "")
    macro_news = deep_ctx.get("macro_news", "")
    macro_grounded_block = deep_ctx.get("macro_grounded_block", "")
    alerts_list = deep_ctx.get("alerts_list", [])

    # Active broker position
    active_pos_block = format_active_position_block(ticker_u)
    is_held = bool(active_pos_block and "ACTIVE BROKER POSITION" in active_pos_block)
    kind = "MANAGE" if is_held else "NEW"

    resolved_lane = f_parsed.get("setup_lane", "RR_SETUP") if isinstance(f_parsed, dict) else "RR_SETUP"
    triage_reason = f_parsed.get("reason", "no_setup") if isinstance(f_parsed, dict) else "no_setup"
    lane_prior_win = f_parsed.get("lane_prior_win") if isinstance(f_parsed, dict) else None
    lane_prior_ev = f_parsed.get("lane_prior_ev") if isinstance(f_parsed, dict) else None

    # Track in SQLite
    db_path = config.research_watch_db_path()
    job_id = f"arb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker_u}"
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path), timeout=10.0) as conn:
                conn.cursor().execute("""
                    INSERT OR REPLACE INTO active_research_jobs
                    (job_id, ticker, mode, pid, stage, status, started_at, target_date)
                    VALUES (?, ?, 'arbitration', ?, 'ARBITRATION', 'RUNNING', ?, ?)
                """, (job_id, ticker_u, os.getpid(), datetime.now(timezone.utc).isoformat(), date_str))
                conn.commit()
        except Exception as e:
            logger.debug(f"SQLite tracking error: {e}")

    logger.info(f"⚖️ [{ticker_u}] Running Senior PM Ponytail Arbitration...")
    t0 = time.time()
    res = run_arbitration(
        ticker=ticker_u,
        date_str=date_str,
        clean_response=clean_response,
        clean_ind_response=clean_ind_response,
        f_parsed=f_parsed,
        dw_dict=dw_dict,
        live_quote_block=live_quote_block,
        earnings_fact_block=earnings_fact_block,
        alerts_list=alerts_list,
        macro_news=macro_news,
        macro_grounded_block=macro_grounded_block,
        active_pos_block=active_pos_block,
        tdir=tdir,
        raw_dir=raw_dir,
        reports_dir=reports_dir,
        kind=kind,
        setup_lane=resolved_lane,
        triage_reason=triage_reason,
        lane_prior_win=lane_prior_win,
        lane_prior_ev=lane_prior_ev,
    )
    elapsed = round(time.time() - t0, 2)
    logger.info(f"✅ [{ticker_u}] Arbitration completed in {elapsed}s (length: {len(res)} chars)")

    # Sync watchlist
    try:
        from run_watch_alerts import sync_reports_to_watchlist
        sync_res = sync_reports_to_watchlist(target_date=date_str, target_ticker=ticker_u, sync_tastytrade=True)
        logger.info(f"[{ticker_u}] Watchlist sync: {sync_res.count} targets indexed, {sync_res.tt_alerts_count} TT alerts registered.")
    except Exception as e:
        logger.warning(f"[{ticker_u}] Watchlist sync error: {e}")

    # Mark complete in SQLite
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path), timeout=10.0) as conn:
                conn.cursor().execute("""
                    UPDATE active_research_jobs
                    SET status = 'COMPLETED', stage = 'DONE', stage_detail = 'Senior PM Arbitration Complete', completed_at = ?
                    WHERE ticker = ? AND status = 'RUNNING'
                """, (datetime.now(timezone.utc).isoformat(), ticker_u))
                conn.commit()
        except Exception as e:
            logger.debug(f"SQLite completion update error: {e}")

    return bool(res)


def main():
    parser = argparse.ArgumentParser(description="Run Senior PM Ponytail Arbitration for candidates with Model A & B reports.")
    parser.add_argument("--ticker", "-t", required=True, help="Ticker symbol (e.g. PEP)")
    parser.add_argument("--date", "-d", default=None, help="Target date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()

    success = run_ticker_arbitration(ticker=args.ticker, date_str=args.date)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
