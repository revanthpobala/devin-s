"""
Autonomous Background Alert Triage Daemon.
Continuously monitors SQLite trading_alerts.db for pending or newly ingested alerts
and automatically runs local LLM triage without requiring manual user interaction.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from src import config
from src.tracking.alert_db import DB_PATH, update_research_status
from src.tracking.alert_evaluator import evaluate_alert_payload
from src.ui.services.research_queue import dispatch_next_queued_job
from src.tracking.watch_manager import _get_connection, _db_lock

logger = logging.getLogger(__name__)


def _ensure_queue_coverage(today_str: Optional[str] = None):
    """Coverage guarantee: ensure any research_queue symbol with no alert today gets a synthetic alert to evaluate."""
    today_str = today_str or datetime.now().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(DB_PATH), timeout=10.0) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, setup FROM research_queue
                WHERE date = ?
            """, (today_str,))
            items = cur.fetchall()
            if not items:
                return
            now_iso = datetime.now(timezone.utc).isoformat()
            for sym, setup in items:
                sym_u = (sym or "").strip().upper()
                if not sym_u:
                    continue
                msg_id = f"synth-{sym_u}-{today_str}"
                cur.execute("""
                    INSERT OR IGNORE INTO alerts (
                        message_id, date, timestamp, symbol, action, strategy, setup, status, created_at, raw_payload
                    ) VALUES (?, ?, ?, ?, 'LONG', 'Swing', ?, 'PENDING', ?, '{}')
                """, (msg_id, today_str, now_iso, sym_u, setup or 'Screener', now_iso))
            conn.commit()
    except Exception as e:
        logger.debug(f"Queue coverage sync note: {e}")


def _dispatch_deep_research_if_needed(ticker: str, date_str: str):
    ticker = ticker.upper()
    if not ticker:
        return
    report_dir = config.BASE_DIR / "reports" / date_str
    has_report = (
        (report_dir / f"{ticker}_summary.md").exists()
        or (report_dir / f"{ticker}_arbitration.md").exists()
    )
    if has_report:
        return
    db_path = config.BASE_DIR / "data" / "research_watch.db"
    if not db_path.exists():
        return
    try:
        with _db_lock:
            with _get_connection() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT job_id FROM active_research_jobs WHERE LOWER(ticker) = ? AND target_date = ? AND status IN ('QUEUED', 'RUNNING')",
                    (ticker.lower(), date_str),
                )
                if c.fetchone():
                    return
                job_id = f"auto-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                c.execute(
                    "INSERT INTO active_research_jobs (job_id, ticker, mode, stage, status, started_at, target_date) VALUES (?, ?, 'full', 'QUEUED', 'QUEUED', ?, ?)",
                    (job_id, ticker, datetime.now(timezone.utc).isoformat(), date_str),
                )
                conn.commit()
                dispatch_next_queued_job()
                logger.info(f"🤖 [AutoTriageDaemon] Enqueued deep research for {ticker}: {job_id}")
    except Exception as e:
        logger.warning(f"Failed to dispatch research for {ticker}: {e}")

_daemon_instance: Optional["AutoTriageDaemon"] = None
_daemon_lock = threading.Lock()


def is_llm_server_online(timeout: float = 2.0) -> bool:
    """Check if the local llama-server /health endpoint is responsive."""
    try:
        r = requests.get("http://127.0.0.1:8000/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def get_pending_alerts_count(date_str: Optional[str] = None) -> int:
    """Return the total number of alerts in SQLite that have not been triaged."""
    try:
        with sqlite3.connect(str(DB_PATH), timeout=10.0) as conn:
            cur = conn.cursor()
            query = """
                SELECT count(*) FROM alerts
                WHERE (llm_decision IS NULL OR llm_decision = '' OR llm_decision = '```' OR llm_decision = '...' OR llm_decision = 'AI EVALUATED')
            """
            params = []
            if date_str:
                query += " AND date = ?"
                params.append(date_str)
            cur.execute(query, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.debug(f"Error querying pending alerts count: {e}")
        return 0


class AutoTriageDaemon(threading.Thread):
    """Background worker daemon that polls for pending alerts and automatically triages them."""

    def __init__(self, poll_interval: int = 8, batch_size: int = 15):
        super().__init__(name="AutoTriageDaemon", daemon=True)
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._stop_event = threading.Event()
        self.total_triaged = 0
        self.last_run_time: Optional[str] = None
        self.is_processing = False

    def stop(self):
        self._stop_event.set()

    def run(self):
        logger.info(f"🤖 [AutoTriageDaemon] started (poll_interval={self.poll_interval}s, batch_size={self.batch_size}).")
        while not self._stop_event.is_set():
            try:
                # 0. Coverage guarantee: ensure queue candidates are staged as pending alerts
                _ensure_queue_coverage()

                # 1. Quick check: Are there any pending alerts in SQLite?
                pending = get_pending_alerts_count()
                if pending > 0:
                    # 2. Check if local LLM server is up
                    if is_llm_server_online():
                        self.is_processing = True
                        self._process_batch()
                        self.is_processing = False
                    else:
                        logger.debug("[AutoTriageDaemon] Local LLM server offline or busy. Waiting...")
            except Exception as e:
                self.is_processing = False
                logger.warning(f"[AutoTriageDaemon] cycle error: {e}")

            self._stop_event.wait(self.poll_interval)

        logger.info("🤖 [AutoTriageDaemon] stopped.")

    def _process_batch(self):
        """Fetch a batch of pending alerts and evaluate them."""
        try:
            with sqlite3.connect(str(DB_PATH), timeout=20.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                query = """
                    SELECT * FROM alerts
                    WHERE (llm_decision IS NULL OR llm_decision = '' OR llm_decision = '```' OR llm_decision = '...' OR llm_decision = 'AI EVALUATED')
                    ORDER BY CASE WHEN date = strftime('%Y-%m-%d', 'now') THEN 0 ELSE 1 END, timestamp DESC
                    LIMIT ?
                """
                cur.execute(query, (self.batch_size,))
                rows = cur.fetchall()

            if not rows:
                return

            logger.info(f"🤖 [AutoTriageDaemon] Autonomously triaging batch of {len(rows)} pending alert(s)...")
            batch_count = 0
            for row in rows:
                if self._stop_event.is_set():
                    break
                try:
                    alert_dict = dict(row)
                    res = evaluate_alert_payload(alert_dict, use_tools=False)
                    batch_count += 1
                    self.total_triaged += 1
                    logger.info(f"🤖 [AutoTriageDaemon] Triaged {alert_dict.get('symbol')}: {res.get('llm_decision')}")
                    decision = (res.get('llm_decision') or '').upper()
                    sym = (alert_dict.get('symbol') or '').strip().upper()
                    d_str = alert_dict.get('date') or datetime.now().strftime('%Y-%m-%d')
                    if 'PASS' in decision:
                        update_research_status(sym, d_str, 'LOCAL_PASS')
                        _dispatch_deep_research_if_needed(sym, d_str)
                    elif 'WATCH' in decision:
                        update_research_status(sym, d_str, 'LOCAL_WATCH')
                    elif 'CUT' in decision:
                        update_research_status(sym, d_str, 'LOCAL_CUT')
                except Exception as e:
                    logger.warning(f"[AutoTriageDaemon] Failed evaluating {row['symbol']}: {e}")

            self.last_run_time = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            logger.info(f"✅ [AutoTriageDaemon] Batch complete: {batch_count} alert(s) triaged.")
        except Exception as err:
            logger.error(f"[AutoTriageDaemon] Batch processing error: {err}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_alive": self.is_alive(),
            "is_processing": self.is_processing,
            "total_triaged": self.total_triaged,
            "pending_count": get_pending_alerts_count(),
            "last_run_time": self.last_run_time,
            "llm_online": is_llm_server_online(),
        }


def start_auto_triage_daemon(poll_interval: int = 8, batch_size: int = 15) -> AutoTriageDaemon:
    """Start the global AutoTriageDaemon singleton if not already running."""
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance is None or not _daemon_instance.is_alive():
            daemon = AutoTriageDaemon(poll_interval=poll_interval, batch_size=batch_size)
            daemon.start()
            _daemon_instance = daemon
            logger.info("🤖 [AutoTriageDaemon] Global instance launched.")
        return _daemon_instance


def get_auto_triage_status() -> Dict[str, Any]:
    """Retrieve current operational status of the auto-triage daemon."""
    global _daemon_instance
    if _daemon_instance and _daemon_instance.is_alive():
        return _daemon_instance.get_status()
    return {
        "is_alive": False,
        "is_processing": False,
        "total_triaged": 0,
        "pending_count": get_pending_alerts_count(),
        "last_run_time": None,
        "llm_online": is_llm_server_online(),
    }
