"""
TradingView Alerts & Research Queue State Manager (SQLite-backed).
Provides durable, idempotent alert ingestion, persistent audit trail,
and automated research queueing with Eastern Time session date validation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config

from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = config.BASE_DIR / "data" / "trading_alerts.db"
_db_lock = threading.Lock()


def get_eastern_now() -> datetime:
    """Return current datetime in US Eastern Time (America/New_York)."""
    return datetime.now(ZoneInfo("America/New_York"))


def get_eastern_date_str(ts_str: Optional[str] = None) -> str:
    """Extract or return YYYY-MM-DD strictly anchored in US Eastern Time.

    If ts_str is provided and starts with YYYY-MM-DD, returns that date.
    Otherwise, returns current Eastern Time date.
    """
    if ts_str and len(ts_str) >= 10:
        date_part = ts_str[:10].replace("/", "-")
        if len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
            return date_part
    return get_eastern_now().strftime("%Y-%m-%d")


def compute_alert_hash(
    timestamp: str, symbol: str, action: str, price: Optional[float] = None
) -> str:
    """Compute a deterministic hash for an alert when RFC Message-ID is missing."""
    key = f"{timestamp}|{symbol.upper()}|{action.upper()}|{price or 0.0}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@contextmanager
def _get_connection():
    """Thread-safe context manager that opens SQLite WAL connection and guarantees closing."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_alert_db():
    """Initialize SQLite tables for alerts, research queue, and positions."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    message_id TEXT PRIMARY KEY,
                    email_id TEXT,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    alert_price REAL,
                    market_price REAL,
                    event TEXT,
                    setup TEXT,
                    verdict TEXT,
                    act_now TEXT,
                    plan TEXT,
                    score TEXT,
                    grade TEXT,
                    align TEXT,
                    wrong_if TEXT,
                    raw_payload TEXT NOT NULL,
                    status TEXT DEFAULT 'INGESTED',
                    llm_decision TEXT,
                    llm_playbook TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts(date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_action ON alerts(action);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_email_id ON alerts(email_id);")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS research_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL,
                    setup TEXT,
                    status TEXT DEFAULT 'QUEUED',
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(date, symbol)
                );
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rq_date_status ON research_queue(date, status);")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    side TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    entry_price REAL,
                    stop REAL,
                    target REAL,
                    alert_price REAL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    status TEXT DEFAULT 'OPEN',
                    exit_price REAL,
                    exit_reason TEXT,
                    last_price REAL,
                    last_eval TEXT,
                    raw_alert TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()


# Auto-initialize tables on module import
init_alert_db()


def is_alert_processed(
    message_id: Optional[str] = None,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    timestamp: Optional[str] = None,
    email_id: Optional[str] = None,
    require_completed: bool = False,
) -> bool:
    """Check if an alert has already been processed in the database.

    Checks by message_id, email_id, or natural key (symbol, action, timestamp).
    If require_completed is True, only considers alerts with status 'PROCESSED'.
    Defaults to False so newly ingested alerts (status 'INGESTED') are recognized
    as already handled and never duplicated or re-opened.
    """
    status_clause = " AND status = 'PROCESSED'" if require_completed else ""

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            if message_id:
                cursor.execute(
                    f"SELECT 1 FROM alerts WHERE message_id = ?{status_clause}",
                    (message_id,),
                )
                if cursor.fetchone():
                    return True

            if email_id and str(email_id).strip():
                cursor.execute(
                    f"SELECT 1 FROM alerts WHERE email_id = ?{status_clause}",
                    (str(email_id).strip(),),
                )
                if cursor.fetchone():
                    return True

            if symbol and action and timestamp:
                cursor.execute(
                    f"SELECT 1 FROM alerts WHERE symbol = ? AND action = ? AND timestamp = ?{status_clause}",
                    (symbol.strip().upper(), action.strip().upper(), timestamp.strip()),
                )
                if cursor.fetchone():
                    return True

            return False


def get_processed_email_ids(email_ids: List[str]) -> set[str]:
    """Return the subset of email_ids that are already recorded in the SQLite database.
    
    Fast vectorized query using IN clause with index on email_id. Runs in <1ms.
    """
    if not email_ids:
        return set()
    clean_ids = [str(eid).strip() for eid in email_ids if str(eid).strip()]
    if not clean_ids:
        return set()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            found = set()
            chunk_size = 500
            for i in range(0, len(clean_ids), chunk_size):
                chunk = clean_ids[i:i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(f"SELECT email_id FROM alerts WHERE email_id IN ({placeholders})", chunk)
                for row in cursor.fetchall():
                    if row[0]:
                        found.add(str(row[0]).strip())
            return found


def record_alert(alert: Dict[str, Any], raw_payload: Optional[str] = None) -> bool:
    """Record an alert into SQLite.

    Returns True if successfully inserted, or False if already existed (idempotent).
    """
    symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
    if not symbol:
        return False

    ts = alert.get("timestamp") or get_eastern_now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = get_eastern_date_str(ts)
    action = str(alert.get("action") or alert.get("event") or "ALERT").strip().upper()
    strategy = str(alert.get("strategy") or "Intraday").strip()

    message_id = alert.get("message_id")
    if not message_id:
        message_id = compute_alert_hash(
            ts, symbol, action, alert.get("alert_price")
        )
        alert["message_id"] = message_id

    email_id = str(alert.get("email_id") or "")
    payload_str = raw_payload or alert.get("body") or json.dumps(alert, default=str)
    created_at = get_eastern_now().isoformat(timespec="seconds")

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO alerts (
                        message_id, email_id, date, timestamp, symbol, action, strategy,
                        alert_price, market_price, event, setup, verdict, act_now, plan,
                        score, grade, align, wrong_if, raw_payload, status, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'INGESTED', ?
                    )
                    """,
                    (
                        message_id,
                        email_id,
                        date_str,
                        ts,
                        symbol,
                        action,
                        strategy,
                        alert.get("alert_price"),
                        alert.get("market_price"),
                        alert.get("event"),
                        alert.get("setup"),
                        alert.get("verdict"),
                        alert.get("act_now"),
                        alert.get("plan"),
                        alert.get("score"),
                        alert.get("grade"),
                        alert.get("align"),
                        alert.get("wrong_if"),
                        payload_str,
                        created_at,
                    ),
                )
                conn.commit()
                logger.info(f"[alert_db] Recorded new alert {message_id[:16]} for {symbol} ({action}) on {date_str}.")
                return True
            except sqlite3.IntegrityError:
                # Already exists
                logger.debug(f"[alert_db] Alert {message_id[:16]} already recorded. Skipping duplicate.")
                return False


def update_alert_llm(
    message_id: str,
    llm_decision: str,
    llm_playbook: str,
    status: str = "PROCESSED",
):
    """Update an alert record with LLM decision and playbook."""
    if not message_id:
        return
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE alerts
                SET llm_decision = ?, llm_playbook = ?, status = ?
                WHERE message_id = ?
                """,
                (llm_decision, llm_playbook, status, message_id),
            )
            conn.commit()


def queue_for_research(
    symbol: str,
    date_str: Optional[str] = None,
    setup: Optional[str] = None,
    source: str = "screener_alert",
    reason: str = "Screener setup trigger",
) -> bool:
    """Add a ticker to the SQLite research queue AND sync to survivors.json.

    Deduplicates by (date, symbol). Returns True if newly queued, False if already present.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return False

    date_str = get_eastern_date_str(date_str)
    created_at = get_eastern_now().isoformat(timespec="seconds")

    newly_added = False
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at)
                    VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)
                    """,
                    (date_str, symbol, source, setup or "", reason, created_at),
                )
                conn.commit()
                newly_added = True
                logger.info(f"[research_queue] Queued {symbol} for date {date_str} (setup={setup}).")
            except sqlite3.IntegrityError:
                logger.debug(f"[research_queue] {symbol} already queued for date {date_str}.")

    # Sync to survivors.json file system manifest so existing scripts (run_local_research, run_deep_research) pick it up
    if newly_added:
        _sync_to_survivors_file(symbol, date_str, setup, source)

    return newly_added


def _sync_to_survivors_file(symbol: str, date_str: str, setup: Optional[str], source: str):
    """Safely append the symbol to data/raw/<date_str>/survivors.json."""
    try:
        out_dir = config.BASE_DIR / "data" / "raw" / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "survivors.json"

        survivors = []
        if manifest_path.exists():
            try:
                raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(raw_data, list):
                    survivors = raw_data
            except Exception as e:
                logger.warning(f"Failed reading survivors.json for {date_str}: {e}")

        existing = {
            (s.get("Ticker") or s.get("ticker") or s.get("Symbol") or "").upper()
            for s in survivors
        }
        if symbol.upper() not in existing:
            survivors.append({
                "Ticker": symbol.upper(),
                "source": source,
                "screener_setup": setup or "",
                "queued_at": get_eastern_now().isoformat(timespec="seconds"),
            })
            manifest_path.write_text(json.dumps(survivors, indent=2), encoding="utf-8")
            logger.info(f"[research_queue] Appended {symbol} to {manifest_path}.")
    except Exception as e:
        logger.error(f"Failed syncing {symbol} to survivors.json for {date_str}: {e}")


def get_research_queue(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all queued research candidates for a given date (default today ET)."""
    date_str = get_eastern_date_str(date_str)
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, date, symbol, source, setup, status, reason, created_at
                FROM research_queue
                WHERE date = ?
                ORDER BY id ASC
                """,
                (date_str,),
            )
            return [dict(row) for row in cursor.fetchall()]


def update_research_status(symbol: str, date_str: str, status: str):
    """Update research candidate status (e.g. 'SCRAPED', 'TRIAGED', 'COMPLETED')."""
    symbol = symbol.strip().upper()
    date_str = get_eastern_date_str(date_str)
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE research_queue
                SET status = ?
                WHERE symbol = ? AND date = ?
                """,
                (status, symbol, date_str),
            )
            conn.commit()


def get_alerts_for_date(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all alerts logged for a given date (default today ET)."""
    date_str = get_eastern_date_str(date_str)
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM alerts
                WHERE date = ?
                ORDER BY timestamp DESC
                """,
                (date_str,),
            )
            return [dict(row) for row in cursor.fetchall()]


def get_recent_alerts(limit: int = 1000) -> List[Dict[str, Any]]:
    """Retrieve the most recent N alerts across all dates."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM alerts
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]


def sync_position(symbol: str, position_data: Dict[str, Any]):
    """Sync an open/closed position from position_state into SQLite."""
    symbol = symbol.strip().upper()
    if not symbol:
        return
    now_iso = get_eastern_now().isoformat(timespec="seconds")
    status = "OPEN" if position_data.get("opened_at") and not position_data.get("closed_at") else "CLOSED"

    raw_alert = position_data.get("raw_alert")
    raw_str = json.dumps(raw_alert, default=str) if isinstance(raw_alert, dict) else str(raw_alert or "")

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO positions (
                    symbol, side, strategy, entry_price, stop, target, alert_price,
                    opened_at, closed_at, status, exit_price, exit_reason, last_price,
                    last_eval, raw_alert, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    side = excluded.side,
                    strategy = excluded.strategy,
                    entry_price = excluded.entry_price,
                    stop = excluded.stop,
                    target = excluded.target,
                    alert_price = excluded.alert_price,
                    opened_at = excluded.opened_at,
                    closed_at = excluded.closed_at,
                    status = excluded.status,
                    exit_price = excluded.exit_price,
                    exit_reason = excluded.exit_reason,
                    last_price = excluded.last_price,
                    last_eval = excluded.last_eval,
                    raw_alert = excluded.raw_alert,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    position_data.get("side", "LONG"),
                    position_data.get("strategy", "Intraday"),
                    position_data.get("entry_price"),
                    position_data.get("stop"),
                    position_data.get("target"),
                    position_data.get("alert_price"),
                    position_data.get("opened_at", now_iso),
                    position_data.get("closed_at"),
                    status,
                    position_data.get("exit_price"),
                    position_data.get("exit_reason"),
                    position_data.get("last_price"),
                    position_data.get("last_eval"),
                    raw_str,
                    now_iso,
                ),
            )
            conn.commit()
