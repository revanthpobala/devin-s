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

import os
import re
from src import config

from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("ALERT_DB_PATH", str(config.BASE_DIR / "data" / "trading_alerts.db")))
_db_lock = threading.Lock()


def set_db_path(new_path: Path | str):
    """Override database file path dynamically for testing or alternate stores."""
    global DB_PATH
    DB_PATH = Path(new_path)


def get_eastern_now() -> datetime:
    """Return current datetime in US Eastern Time (America/New_York)."""
    return datetime.now(ZoneInfo("America/New_York"))


def get_eastern_date_str(ts_str: Optional[str] = None) -> str:
    """Extract or return YYYY-MM-DD strictly converted to US Eastern Time (America/New_York).

    Parses ISO timestamps (aware or naive) and converts to Eastern Time rather than
    blindly slicing UTC strings.
    """
    if ts_str:
        try:
            s = str(ts_str).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                # If naive, format date directly if in YYYY-MM-DD form
                return dt.strftime("%Y-%m-%d")
            eastern_dt = dt.astimezone(ZoneInfo("America/New_York"))
            return eastern_dt.strftime("%Y-%m-%d")
        except Exception:
            if len(ts_str) >= 10 and ts_str[4] == "-" and ts_str[7] == "-":
                return ts_str[:10]
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
    """Initialize SQLite tables for alerts, research queue, positions, and Schema v2 trade events."""
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
                    updated_at TEXT NOT NULL,
                    quantity REAL DEFAULT 100.0,
                    remaining_quantity REAL DEFAULT 100.0,
                    multiplier INTEGER DEFAULT 1,
                    fees REAL DEFAULT 0.0,
                    slippage REAL DEFAULT 0.0,
                    trade_id TEXT,
                    strategy_id TEXT DEFAULT 'Intraday',
                    mode TEXT DEFAULT 'MODEL',
                    initial_stop REAL,
                    initial_target REAL,
                    realized_broker_pnl REAL DEFAULT 0.0
                );
                """
            )

            # Schema metadata
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '2');")

            # Schema v2: Immutable Trade Events Log
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL,
                    setup_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT 'legacy',
                    strategy_version TEXT DEFAULT 'v1.0',
                    strategy_hash TEXT,
                    source_hash TEXT,
                    mode TEXT NOT NULL DEFAULT 'MODEL',
                    event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    price REAL,
                    quantity REAL DEFAULT 0.0,
                    remaining_quantity REAL DEFAULT 0.0,
                    instrument_type TEXT DEFAULT 'EQUITY',
                    multiplier INTEGER DEFAULT 1,
                    fees REAL DEFAULT 0.0,
                    slippage REAL DEFAULT 0.0,
                    stop_level REAL,
                    target_1 REAL,
                    target_2 REAL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_trade_id ON trade_events(trade_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_setup_id ON trade_events(setup_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_symbol ON trade_events(symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_timestamp ON trade_events(timestamp);")

            # Additive migration for alerts table
            cursor.execute("PRAGMA table_info(alerts);")
            alert_cols = {r[1] for r in cursor.fetchall()}
            if "routing_stage" not in alert_cols:
                cursor.execute("ALTER TABLE alerts ADD COLUMN routing_stage TEXT DEFAULT 'RECORDED';")
            if "strategy_id" not in alert_cols:
                cursor.execute("ALTER TABLE alerts ADD COLUMN strategy_id TEXT DEFAULT 'Intraday';")
            if "mode" not in alert_cols:
                cursor.execute("ALTER TABLE alerts ADD COLUMN mode TEXT DEFAULT 'MODEL';")
            if "trade_id" not in alert_cols:
                cursor.execute("ALTER TABLE alerts ADD COLUMN trade_id TEXT;")

            # intraday_signals shadow ledger table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS intraday_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    entry_ts TEXT,
                    hour INTEGER,
                    grade TEXT,
                    score REAL,
                    align TEXT,
                    side TEXT DEFAULT 'LONG',
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_price REAL,
                    entry_px REAL,
                    stop REAL,
                    entry_stop REAL,
                    target_1 REAL,
                    entry_t1 REAL,
                    target_2 REAL,
                    veto_reason TEXT,
                    llm_verdict TEXT,
                    exit_r REAL,
                    exit_why TEXT,
                    pine_exit_r REAL,
                    replay_r REAL,
                    taken BOOLEAN DEFAULT 0,
                    your_fill REAL,
                    is_modeled INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    UNIQUE(trade_id, ticker, date)
                )
                """
            )

            # Additive migration for intraday_signals table
            cursor.execute("PRAGMA table_info(intraday_signals);")
            sig_cols = {r[1] for r in cursor.fetchall()}
            for col_name, col_type in [
                ("hour", "INTEGER"),
                ("entry_px", "REAL"),
                ("entry_stop", "REAL"),
                ("entry_t1", "REAL"),
                ("llm_verdict", "TEXT"),
                ("exit_r", "REAL"),
                ("exit_why", "TEXT"),
                ("updated_at", "TEXT"),
            ]:
                if col_name not in sig_cols:
                    cursor.execute(f"ALTER TABLE intraday_signals ADD COLUMN {col_name} {col_type};")

            # Additive migration for positions table
            cursor.execute("PRAGMA table_info(positions);")
            pos_cols = {r[1] for r in cursor.fetchall()}
            for col_name, col_type in [
                ("trade_id", "TEXT"),
                ("strategy_id", "TEXT DEFAULT 'Intraday'"),
                ("strategy_version", "TEXT DEFAULT 'v1.0'"),
                ("mode", "TEXT DEFAULT 'MODEL'"),
                ("quantity", "REAL DEFAULT 100.0"),
                ("remaining_quantity", "REAL DEFAULT 100.0"),
                ("multiplier", "INTEGER DEFAULT 1"),
                ("fees", "REAL DEFAULT 0.0"),
                ("slippage", "REAL DEFAULT 0.0"),
                ("initial_stop", "REAL"),
                ("initial_target", "REAL"),
                ("realized_broker_pnl", "REAL DEFAULT 0.0"),
                ("quoted_option_mark", "REAL"),
                ("modeled_option_payoff", "REAL"),
            ]:
                if col_name not in pos_cols:
                    cursor.execute(f"ALTER TABLE positions ADD COLUMN {col_name} {col_type};")

            conn.commit()


init_db = init_alert_db

# Auto-initialize tables on module import, unless disabled or running in test runner
if os.getenv("ALERT_DB_DISABLE_AUTO_INIT") != "1" and not os.getenv("PYTEST_CURRENT_TEST"):
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
                    last_eval, raw_alert, updated_at,
                    quantity, remaining_quantity, multiplier, fees, slippage,
                    trade_id, strategy_id, mode, initial_stop, initial_target,
                    realized_broker_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at = excluded.updated_at,
                    quantity = excluded.quantity,
                    remaining_quantity = excluded.remaining_quantity,
                    multiplier = excluded.multiplier,
                    fees = excluded.fees,
                    slippage = excluded.slippage,
                    trade_id = excluded.trade_id,
                    strategy_id = excluded.strategy_id,
                    mode = excluded.mode,
                    initial_stop = excluded.initial_stop,
                    initial_target = excluded.initial_target,
                    realized_broker_pnl = excluded.realized_broker_pnl
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
                    position_data.get("quantity"),
                    position_data.get("remaining_quantity"),
                    position_data.get("multiplier"),
                    position_data.get("fees"),
                    position_data.get("slippage"),
                    position_data.get("trade_id"),
                    position_data.get("strategy_id"),
                    position_data.get("mode"),
                    position_data.get("initial_stop"),
                    position_data.get("initial_target"),
                    position_data.get("realized_broker_pnl"),
                ),
            )
            conn.commit()


def record_trade_event(event: Dict[str, Any]) -> int:
    """Record an immutable trade event into trade_events log."""
    trade_id = event.get("trade_id") or ""
    setup_id = event.get("setup_id") or ""
    strategy_id = event.get("strategy_id") or "legacy"
    strategy_version = event.get("strategy_version") or "v1.0"
    strategy_hash = event.get("strategy_hash") or ""
    source_hash = event.get("source_hash") or ""
    mode = event.get("mode") or "MODEL"
    event_type = event.get("event_type") or "SETUP"
    symbol = (event.get("symbol") or event.get("ticker") or "").strip().upper()
    ts = event.get("timestamp") or get_eastern_now().isoformat()
    price = event.get("price")
    quantity = float(event.get("quantity") or 0.0)
    remaining_qty = float(event.get("remaining_quantity") or quantity)
    inst_type = event.get("instrument_type") or "EQUITY"
    multiplier = int(event.get("multiplier") or (100 if inst_type == "OPTION" else 1))
    fees = float(event.get("fees") or 0.0)
    slippage = float(event.get("slippage") or 0.0)
    stop_level = event.get("stop_level")
    target_1 = event.get("target_1")
    target_2 = event.get("target_2")
    details = json.dumps(event.get("details")) if isinstance(event.get("details"), (dict, list)) else event.get("details")
    created_at = get_eastern_now().isoformat()

    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO trade_events (
                    trade_id, setup_id, strategy_id, strategy_version, strategy_hash,
                    source_hash, mode, event_type, symbol, timestamp, price,
                    quantity, remaining_quantity, instrument_type, multiplier,
                    fees, slippage, stop_level, target_1, target_2, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, setup_id, strategy_id, strategy_version, strategy_hash,
                    source_hash, mode, event_type, symbol, ts, price,
                    quantity, remaining_qty, inst_type, multiplier,
                    fees, slippage, stop_level, target_1, target_2, details, created_at
                ),
            )
            conn.commit()
            return cur.lastrowid or 0


def get_trade_events(trade_id: str) -> List[Dict[str, Any]]:
    """Retrieve immutable chronological trade events for a trade_id."""
    if not trade_id:
        return []
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM trade_events
                WHERE trade_id = ?
                ORDER BY timestamp ASC, id ASC
                """,
                (trade_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def update_routing_stage(message_id: str, stage: str):
    """Update durable routing stage ('RECORDED', 'ENQUEUED', 'ROUTED', 'COMPLETED')."""
    if not message_id:
        return
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE alerts SET routing_stage = ? WHERE message_id = ?",
                (stage, message_id),
            )
            conn.commit()


def get_unrouted_alerts() -> List[Dict[str, Any]]:
    """Retrieve alerts that were durably recorded but not yet marked ROUTED or COMPLETED.

    Covers both RECORDED (crash before routing began) and ENQUEUED (crash between
    handoff to PositionManager and the ROUTED commit) so replay is idempotent.
    """
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM alerts
                WHERE (routing_stage IN ('RECORDED', 'ENQUEUED')
                       OR (routing_stage IS NULL AND status = 'INGESTED'))
                ORDER BY created_at ASC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_canonical_trade_id(alert: Dict[str, Any]) -> str:
    """Return Pine trade_id if present; otherwise fallback to {symbol}_{date}_{entryTs}."""
    raw_p = alert.get("raw_payload") or alert.get("body") or ""
    payload = {}
    if raw_p:
        try:
            payload = json.loads(raw_p) if isinstance(raw_p, str) else (raw_p if isinstance(raw_p, dict) else {})
        except Exception:
            payload = {}

    tid = alert.get("trade_id") or payload.get("trade_id")
    if tid:
        return str(tid).strip()

    sym = str(alert.get("symbol") or alert.get("ticker") or payload.get("symbol") or payload.get("ticker") or "UNKNOWN").strip().upper()
    d_str = str(alert.get("date") or payload.get("date") or "").strip()
    ts_str = str(alert.get("entry_ts") or alert.get("timestamp") or payload.get("timestamp") or payload.get("time") or "").strip()

    if not d_str:
        if ts_str and len(ts_str) >= 10 and ts_str[4] == "-" and ts_str[7] == "-":
            d_str = ts_str[:10]
        else:
            d_str = get_eastern_now().strftime("%Y-%m-%d")

    if not ts_str:
        ts_str = get_eastern_now().strftime("%H:%M:%S")

    ts_clean = ts_str.replace(" ", "_")
    return f"{sym}_{d_str}_{ts_clean}"


def upsert_intraday_signal(signal: Dict[str, Any]) -> bool:
    """Insert or update an intraday signal in the shadow ledger."""
    trade_id = signal.get("trade_id") or ""
    ticker = (signal.get("ticker") or signal.get("symbol") or "").strip().upper()
    date = signal.get("date") or ""
    if not ticker or not date:
        return False

    entry_px = signal.get("entry_px") if signal.get("entry_px") is not None else signal.get("entry_price")
    entry_stop = signal.get("entry_stop") if signal.get("entry_stop") is not None else signal.get("stop")
    entry_t1 = signal.get("entry_t1") if signal.get("entry_t1") is not None else signal.get("target_1")
    exit_r = signal.get("exit_r") if signal.get("exit_r") is not None else signal.get("pine_exit_r")
    exit_why = signal.get("exit_why")

    # Resolve hour in ET
    hour = signal.get("hour")
    if hour is None:
        entry_ts = str(signal.get("entry_ts") or "")
        m_h = re.search(r"(\d{1,2}):\d{2}", entry_ts)
        if m_h:
            try:
                hour = int(m_h.group(1))
            except Exception:
                hour = get_eastern_now().hour
        else:
            hour = get_eastern_now().hour
    else:
        try:
            hour = int(hour)
        except Exception:
            hour = get_eastern_now().hour

    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            try:
                now_iso = get_eastern_now().isoformat(timespec="seconds")
                cur.execute(
                    """
                    INSERT INTO intraday_signals (
                        trade_id, ticker, date, entry_ts, hour, grade, score, align,
                        side, entry_type, entry_price, entry_px, stop, entry_stop,
                        target_1, entry_t1, target_2, veto_reason, llm_verdict,
                        exit_r, exit_why, pine_exit_r, replay_r, taken, your_fill,
                        is_modeled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_id, ticker, date) DO UPDATE SET
                        hour=COALESCE(intraday_signals.hour, excluded.hour),
                        grade=COALESCE(intraday_signals.grade, excluded.grade),
                        score=COALESCE(intraday_signals.score, excluded.score),
                        align=COALESCE(intraday_signals.align, excluded.align),
                        side=COALESCE(intraday_signals.side, excluded.side),
                        entry_type=COALESCE(intraday_signals.entry_type, excluded.entry_type),
                        entry_price=COALESCE(intraday_signals.entry_price, excluded.entry_price),
                        entry_px=COALESCE(intraday_signals.entry_px, excluded.entry_px),
                        stop=COALESCE(intraday_signals.stop, excluded.stop),
                        entry_stop=COALESCE(intraday_signals.entry_stop, excluded.entry_stop),
                        target_1=COALESCE(intraday_signals.target_1, excluded.target_1),
                        entry_t1=COALESCE(intraday_signals.entry_t1, excluded.entry_t1),
                        target_2=COALESCE(intraday_signals.target_2, excluded.target_2),
                        veto_reason=COALESCE(excluded.veto_reason, intraday_signals.veto_reason),
                        llm_verdict=CASE
                            WHEN excluded.llm_verdict IS NOT NULL AND TRIM(excluded.llm_verdict) != '' THEN excluded.llm_verdict
                            ELSE intraday_signals.llm_verdict
                        END,
                        exit_r=COALESCE(excluded.exit_r, intraday_signals.exit_r),
                        exit_why=COALESCE(excluded.exit_why, intraday_signals.exit_why),
                        pine_exit_r=COALESCE(excluded.pine_exit_r, intraday_signals.pine_exit_r),
                        replay_r=COALESCE(excluded.replay_r, intraday_signals.replay_r),
                        taken=MAX(intraday_signals.taken, excluded.taken),
                        your_fill=COALESCE(intraday_signals.your_fill, excluded.your_fill),
                        is_modeled=excluded.is_modeled,
                        updated_at=excluded.updated_at
                    """,
                    (
                        trade_id,
                        ticker,
                        date,
                        signal.get("entry_ts"),
                        hour,
                        signal.get("grade"),
                        signal.get("score"),
                        signal.get("align"),
                        signal.get("side", "LONG"),
                        signal.get("entry_type", "LIMIT"),
                        entry_px,
                        entry_px,
                        entry_stop,
                        entry_stop,
                        entry_t1,
                        entry_t1,
                        signal.get("target_2"),
                        signal.get("veto_reason"),
                        str(signal.get("llm_verdict")).strip() if signal.get("llm_verdict") not in (None, "") else None,
                        exit_r,
                        exit_why,
                        signal.get("pine_exit_r"),
                        signal.get("replay_r"),
                        1 if signal.get("taken") else 0,
                        signal.get("your_fill"),
                        signal.get("is_modeled", 0),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return True
            except Exception as e:
                logger.warning(f"Failed to upsert intraday signal: {e}")
                return False


def record_intraday_exit(
    trade_id: Optional[str] = None,
    exit_r: Optional[float] = None,
    pine_exit_r: Optional[float] = None,
    exit_why: str = "",
    ticker: Optional[str] = None,
    date: Optional[str] = None,
) -> bool:
    """Update an intraday signal row with exit performance metrics."""
    if not trade_id and not ticker:
        return False
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            try:
                now_iso = get_eastern_now().isoformat(timespec="seconds")
                updated = False
                if trade_id:
                    cur.execute(
                        """
                        UPDATE intraday_signals
                        SET exit_r = COALESCE(?, exit_r),
                            pine_exit_r = COALESCE(?, pine_exit_r),
                            exit_why = CASE WHEN ? != '' THEN ? ELSE exit_why END,
                            updated_at = ?
                        WHERE trade_id = ?
                        """,
                        (exit_r, pine_exit_r, exit_why, exit_why, now_iso, trade_id),
                    )
                    if cur.rowcount > 0:
                        updated = True

                if not updated and ticker:
                    if exit_r is not None:
                        # Pair with the latest open ENTRY row for that symbol and date (where exit_r is NULL)
                        cur.execute(
                            """
                            UPDATE intraday_signals
                            SET exit_r = ?,
                                exit_why = CASE WHEN ? != '' THEN ? ELSE exit_why END,
                                updated_at = ?
                            WHERE id = (
                                SELECT id FROM intraday_signals
                                WHERE ticker = ?
                                  AND (date = ? OR ? IS NULL)
                                  AND exit_r IS NULL
                                ORDER BY id DESC LIMIT 1
                            )
                            """,
                            (exit_r, exit_why, exit_why, now_iso, ticker.upper(), date, date),
                        )
                        if cur.rowcount > 0:
                            updated = True
                    elif pine_exit_r is not None:
                        # Pair with the latest ENTRY row for that symbol and date where pine_exit_r is NULL
                        cur.execute(
                            """
                            UPDATE intraday_signals
                            SET pine_exit_r = ?,
                                exit_why = CASE WHEN ? != '' THEN ? ELSE exit_why END,
                                updated_at = ?
                            WHERE id = (
                                SELECT id FROM intraday_signals
                                WHERE ticker = ?
                                  AND (date = ? OR ? IS NULL)
                                  AND pine_exit_r IS NULL
                                ORDER BY id DESC LIMIT 1
                            )
                            """,
                            (pine_exit_r, exit_why, exit_why, now_iso, ticker.upper(), date, date),
                        )
                        if cur.rowcount > 0:
                            updated = True

                conn.commit()
                return updated
            except Exception as e:
                logger.warning(f"Failed to record intraday exit for {trade_id or ticker}: {e}")
                return False


def get_day_session_r(date: Optional[str] = None) -> float:
    """Calculate the cumulative realized R for the session today from intraday_signals."""
    d_str = date or get_eastern_now().strftime("%Y-%m-%d")
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT SUM(exit_r)
                FROM intraday_signals
                WHERE date = ? AND exit_r IS NOT NULL
                """,
                (d_str,),
            )
            row = cur.fetchone()
            val = row[0] if row and row[0] is not None else 0.0
            return round(float(val), 2)


def get_day_session_record(date: Optional[str] = None) -> tuple[int, int]:
    """Return (wins, losses) for completed intraday exits on date."""
    d_str = date or get_eastern_now().strftime("%Y-%m-%d")
    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    SUM(CASE WHEN exit_r > 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN exit_r <= 0 THEN 1 ELSE 0 END)
                FROM intraday_signals
                WHERE date = ? AND exit_r IS NOT NULL
                """,
                (d_str,),
            )
            row = cur.fetchone()
            wins = int(row[0] or 0) if row else 0
            losses = int(row[1] or 0) if row else 0
            return wins, losses


def format_llm_verdict(decision: Optional[str] = None, risk_veto_header: Optional[str] = None) -> str:
    """Format verdict as TAKE, VETO:<first 80 chars of reason>, or GATE:<rule>."""
    if risk_veto_header:
        hdr_up = str(risk_veto_header).upper()
        if "GRADE" in hdr_up:
            return "GATE:grade"
        if "EXHAUSTION" in hdr_up or "10:30-11:30" in hdr_up:
            return "GATE:time"
        if "LUNCH" in hdr_up:
            return "GATE:time"
        if "COUNTER-STAGE" in hdr_up or "STAGE 4" in hdr_up or "STAGE 2" in hdr_up or "REGIME" in hdr_up:
            return "GATE:stage"
        if "MAX EXPOSURE" in hdr_up or "MAX_CONCURRENT" in hdr_up:
            return "GATE:max_exposure"
        if "DAY PAUSE" in hdr_up:
            return "GATE:day_pause"
        if "EXCLUDED" in hdr_up:
            return "GATE:excluded"
        return f"GATE:{risk_veto_header[:40].strip()}"

    text = str(decision or "").strip()
    if not text:
        return "TAKE"

    if text.startswith("GATE:"):
        return text
    if text.startswith("VETO:"):
        return text[:85]
    if text.upper() == "TAKE" or text.upper().startswith("TAKE"):
        return "TAKE"

    t_up = text.upper()
    if "GRADE" in t_up:
        return "GATE:grade"
    if "EXHAUSTION" in t_up or "10:30-11:30" in t_up:
        return "GATE:time"
    if "LUNCH" in t_up:
        return "GATE:time"
    if "COUNTER-STAGE" in t_up or "STAGE 4" in t_up or "STAGE 2" in t_up:
        return "GATE:stage"
    if "MAX EXPOSURE" in t_up:
        return "GATE:max_exposure"
    if "DAY PAUSE" in t_up:
        return "GATE:day_pause"
    if "EXCLUDED" in t_up:
        return "GATE:excluded"

    if "STAND ASIDE" in t_up or "VETO" in t_up or "WAIT" in t_up:
        clean = text
        for prefix in ["🛡️", "⛔", "—", "-", "[", "]"]:
            clean = clean.replace(prefix, " ")
        clean = " ".join(clean.split()).strip()
        if "STAND ASIDE" in clean.upper():
            idx = clean.upper().find("STAND ASIDE")
            clean = clean[idx + len("STAND ASIDE"):].strip(" :—-( )")
        return f"VETO:{clean[:80].strip() or 'AI triage stand aside'}"

    return "TAKE"


def get_intraday_signals(
    ticker: Optional[str] = None,
    date: Optional[str] = None,
    vetoed: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Query intraday signals with optional filters."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            sql = "SELECT * FROM intraday_signals WHERE 1=1"
            params: List[Any] = []
            if ticker:
                sql += " AND ticker = ?"
                params.append(ticker.upper())
            if date:
                sql += " AND date = ?"
                params.append(date)
            if vetoed is True:
                sql += " AND veto_reason IS NOT NULL"
            elif vetoed is False:
                sql += " AND veto_reason IS NULL"
            sql += " ORDER BY date ASC, id ASC"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def get_intraday_signal_stats() -> Dict[str, Any]:
    """Compute shadow ledger stats: R by grade, veto counts, etc."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            result = {}

            # Total signals
            cur.execute("SELECT COUNT(*) FROM intraday_signals")
            result["total"] = cur.fetchone()[0]

            # Vetoed vs non-vetoed
            cur.execute("SELECT COUNT(*) FROM intraday_signals WHERE veto_reason IS NOT NULL")
            result["vetoed"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM intraday_signals WHERE veto_reason IS NULL")
            result["non_vetoed"] = cur.fetchone()[0]

            # R stats by grade (non-vetoed only)
            cur.execute(
                """
                SELECT grade,
                    COUNT(*) as n,
                    AVG(replay_r) as mean_r,
                    AVG(CASE WHEN replay_r > 0 THEN 1.0 ELSE 0.0 END) as win_rate
                FROM intraday_signals
                WHERE veto_reason IS NULL AND replay_r IS NOT NULL
                GROUP BY grade
                """
            )
            result["r_by_grade"] = [
                {
                    "grade": r["grade"],
                    "n": r["n"],
                    "mean_r": round(r["mean_r"], 4) if r["mean_r"] is not None else None,
                    "win_rate": round(r["win_rate"] * 100, 1) if r["win_rate"] is not None else None,
                }
                for r in cur.fetchall()
            ]

            # Veto reasons
            cur.execute(
                """
                SELECT veto_reason, COUNT(*) as n
                FROM intraday_signals
                WHERE veto_reason IS NOT NULL
                GROUP BY veto_reason
                ORDER BY n DESC
                """
            )
            result["veto_reasons"] = [
                {"reason": r["veto_reason"], "n": r["n"]}
                for r in cur.fetchall()
            ]

            return result

