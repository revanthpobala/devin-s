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

