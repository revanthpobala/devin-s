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
from src.tracking.alert_db import DB_PATH
from src.tracking.alert_evaluator import evaluate_alert_payload

logger = logging.getLogger(__name__)

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
