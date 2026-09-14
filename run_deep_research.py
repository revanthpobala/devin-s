import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.logic.deep_research import run_deep_research

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (DeepResearch) %(message)s"
)


def _ensure_job_registered(ticker: str | None, date_str: str, job_id: str | None = None) -> str | None:
    """Ensure any standalone/CLI research job is tracked in SQLite for the Cockpit UI."""
    db_path = config.BASE_DIR / "data" / "research_watch.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            c = conn.cursor()
            my_pid = os.getpid()

            # If explicit job_id was passed (e.g. from UI or orchestrator)
            if job_id:
                row = c.execute("SELECT job_id FROM active_research_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if row:
                    c.execute(
                        "UPDATE active_research_jobs SET pid = ?, stage = 'DEEP_RESEARCH', status = 'RUNNING' WHERE job_id = ?",
                        (my_pid, job_id),
                    )
                    conn.commit()
                    return job_id

            # Check if an existing running job for this process PID already exists
            existing = c.execute(
                "SELECT job_id FROM active_research_jobs WHERE pid = ? AND status = 'RUNNING'", (my_pid,)
            ).fetchone()
            if existing:
                return existing[0]

            # Check if an existing running or queued job for this ticker already exists to avoid duplicate rows
            if ticker:
                ticker_label = ticker.strip().upper()
                existing_ticker = c.execute(
                    "SELECT job_id FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING', 'QUEUED') ORDER BY started_at DESC LIMIT 1",
                    (ticker_label,),
                ).fetchone()
                if existing_ticker:
                    c.execute(
                        "UPDATE active_research_jobs SET pid = ?, stage = 'DEEP_RESEARCH', status = 'RUNNING' WHERE job_id = ?",
                        (my_pid, existing_ticker[0]),
                    )
                    conn.commit()
                    return existing_ticker[0]
            else:
                ticker_label = "BATCH"

            final_job_id = job_id or f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker_label}"
            log_file = str(config.BASE_DIR / "data" / "logs" / f"{final_job_id}.log")
            c.execute("""
                INSERT OR REPLACE INTO active_research_jobs
                (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date)
                VALUES (?, ?, 'full', ?, 'DEEP_RESEARCH', 'RUNNING', ?, ?, ?)
            """, (final_job_id, ticker_label, my_pid, datetime.now(timezone.utc).isoformat(), log_file, date_str))
            conn.commit()
            return final_job_id
    except Exception as e:
        logger.debug(f"Failed registering research job in SQLite: {e}")
        return None


def _mark_job_complete(job_id: str | None, success: bool = True, error_msg: str | None = None):
    """Mark the tracked research job as COMPLETED or FAILED upon process exit."""
    if not job_id:
        return
    db_path = config.BASE_DIR / "data" / "research_watch.db"
    if not db_path.exists():
        return
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            c = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            if success:
                c.execute(
                    "UPDATE active_research_jobs SET status = 'COMPLETED', stage = 'DONE', completed_at = ? WHERE job_id = ?",
                    (now_iso, job_id),
                )
            else:
                c.execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ?, completed_at = ? WHERE job_id = ?",
                    (error_msg or "Execution failed", now_iso, job_id),
                )
            conn.commit()
    except Exception as e:
        logger.debug(f"Failed updating research job status: {e}")


def run_deep_research_pipeline(
    target_date: str = None, target_ticker: str = None, force_tickers: set = None, job_id: str = None
):
    logger.info("=" * 60)
    logger.info("STARTING DEEP RESEARCH PIPELINE (Paid Validation Phase)")
    logger.info("=" * 60)

    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    final_job_id = _ensure_job_registered(target_ticker, date_str, job_id=job_id)

    try:
        run_deep_research(date_str, target_ticker)
        _mark_job_complete(final_job_id, success=True)
        logger.info("=" * 60)
        logger.info("DEEP RESEARCH PHASE COMPLETE.")
        logger.info("=" * 60)
    except Exception as exc:
        _mark_job_complete(final_job_id, success=False, error_msg=str(exc))
        logger.error(f"DEEP RESEARCH PIPELINE FAILED: {exc}", exc_info=True)
        raise


if __name__ == "__main__":
    import argparse
    import re

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Run deep research validation phase")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD). If a non-date token is given, "
        "it is treated as --ticker using today's date.",
    )
    parser.add_argument("--ticker", type=str, help="Run only on a specific ticker")
    parser.add_argument(
        "--force",
        type=str,
        default="",
        help="Comma-separated tickers to force into deep research "
        "regardless of the local-LLM send_for_deep_research gate.",
    )
    parser.add_argument(
        "--job-id",
        type=str,
        default=None,
        help="Tracking job ID in SQLite active_research_jobs.",
    )

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker

    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    force_tickers = {t.strip().upper() for t in args.force.split(",") if t.strip()}

    # --force requires a local-research pass that routes the ticker into the
    # force subdir BEFORE deep research runs. If no explicit target/ticker is
    # given, force the listed tickers through local research first.
    if force_tickers:
        from run_local_research import run_local_research

        run_local_research(target_date, target_ticker, force_tickers)

    run_deep_research_pipeline(target_date, target_ticker, job_id=args.job_id)
