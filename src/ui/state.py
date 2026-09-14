"""
Shared in-memory state, bounded ring buffers, SQLite helpers, and process registries.
Built with strict memory bounding (maxlen=2000) to prevent any memory leakage.
"""

from __future__ import annotations

import collections
import logging
import sqlite3
import subprocess
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from src import config

logger = logging.getLogger("ui_server")

# Paths
WEB_DIR = config.BASE_DIR / "web"
LOGS_DIR = config.BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS_FILE = config.BASE_DIR / "data" / "positions.json"

# Concurrency limits
MAX_CONCURRENT_RESEARCH = 2

# Bounded ring-buffer for server logs (prevents RAM leaks over long runtimes)
LOG_BUFFER: collections.deque[str] = collections.deque(maxlen=2000)
_LOG_LOCK = threading.Lock()

# Process & worker thread tracking
ACTIVE_RESEARCH_WORKERS: Dict[str, threading.Thread] = {}
ACTIVE_RESEARCH_SUBPROCS: Dict[str, subprocess.Popen] = {}
TRACKER_PROCESS: Optional[subprocess.Popen] = None

_QUEUE_DISPATCH_LOCK = threading.Lock()

# Scan & research telemetry
_SCHWAB_SCAN_STATE: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "completed_at": None,
    "error": None,
    "side": None,
}

RESEARCH_STATE: Dict[str, Any] = {
    "active": False,
    "ticker": None,
    "mode": None,
    "started_at": None,
    "log_file": None,
}


def append_log(msg: str):
    """Thread-safe append of a log line into the bounded ring buffer."""
    ts = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    with _LOG_LOCK:
        LOG_BUFFER.append(formatted)
    logger.info(msg)


def get_db():
    """Context manager for thread-safe SQLite connection with dictionary rows."""
    db_path = config.BASE_DIR / "data" / "research_watch.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Ensure active_research_jobs and necessary tables exist in SQLite database."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_research_jobs (
                job_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                mode TEXT NOT NULL,
                pid INTEGER,
                stage TEXT NOT NULL,
                status TEXT NOT NULL, -- 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'KILLED'
                started_at TEXT NOT NULL,
                completed_at TEXT,
                log_file TEXT,
                error_message TEXT,
                target_date TEXT
            )
        """)
        try:
            c.execute("ALTER TABLE active_research_jobs ADD COLUMN target_date TEXT")
        except Exception:
            pass
        conn.commit()
