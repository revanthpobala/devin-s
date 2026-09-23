"""
Watchlist & Trigger Alert State Manager (SQLite-backed).
Manages active stalking targets, tactical levels, live price states, and alert logs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

DB_PATH = config.BASE_DIR / "data" / "research_watch.db"
_db_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    return conn


def init_watch_db():
    """Initialize database tables if they do not exist."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_targets (
                    ticker TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    conviction INTEGER DEFAULT 5,
                    actionable BOOLEAN DEFAULT 1,
                    side TEXT DEFAULT 'LONG',
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    breakout_level REAL,
                    breakout_stop REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_structure TEXT,
                    options_summary TEXT,
                    options_actionable BOOLEAN DEFAULT 0,
                    options_entry_trigger TEXT,
                    invalidation_price REAL,
                    invalidation_condition TEXT,
                    invalidation_rationale TEXT,
                    last_price REAL,
                    distance_to_entry_pct REAL,
                    status TEXT DEFAULT 'STALKING',
                    last_alert_type TEXT,
                    last_alert_at TEXT,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT
                )
                """
            )
            # Automatic column migrations for existing databases
            existing_cols = {col[1] for col in cursor.execute("PRAGMA table_info(watch_targets)").fetchall()}
            if "side" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN side TEXT DEFAULT 'LONG'")
            if "breakout_level" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN breakout_level REAL")
            if "breakout_stop" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN breakout_stop REAL")
            if "options_actionable" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN options_actionable BOOLEAN DEFAULT 0")
            if "options_entry_trigger" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN options_entry_trigger TEXT")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_targets_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    conviction INTEGER DEFAULT 5,
                    actionable BOOLEAN DEFAULT 1,
                    side TEXT DEFAULT 'LONG',
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    breakout_level REAL,
                    breakout_stop REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_structure TEXT,
                    options_summary TEXT,
                    options_actionable BOOLEAN DEFAULT 0,
                    options_entry_trigger TEXT,
                    invalidation_price REAL,
                    invalidation_condition TEXT,
                    invalidation_rationale TEXT,
                    status TEXT DEFAULT 'STALKING',
                    updated_at TEXT NOT NULL,
                    raw_json TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    spot_price REAL NOT NULL,
                    triggered_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS superforecasting_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    target_date TEXT NOT NULL,
                    event_description TEXT NOT NULL,
                    predicted_probability REAL NOT NULL,
                    actual_outcome INTEGER,
                    brier_score REAL,
                    evaluation_price REAL,
                    evaluated_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, report_date, model_type, horizon_days, event_description)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS suggested_trades_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'LONG',
                    trade_type TEXT NOT NULL,
                    trade_structure TEXT NOT NULL,
                    trade_label TEXT NOT NULL,
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_price REAL,
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_expiration TEXT,
                    long_strike REAL,
                    short_strike REAL,
                    target_debit REAL,
                    max_profit REAL,
                    max_loss REAL,
                    last_price REAL,
                    distance_to_entry_pct REAL,
                    status TEXT NOT NULL DEFAULT 'STALKING',
                    dollar_pnl REAL DEFAULT 0.0,
                    roc_pct REAL DEFAULT 0.0,
                    is_primary INTEGER DEFAULT 1,
                    outcome_notes TEXT,
                    evaluated_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, date, trade_type, trade_structure)
                )
                """
            )
            existing_audit_cols = {col[1] for col in cursor.execute("PRAGMA table_info(suggested_trades_audit)").fetchall()}
            if "is_primary" not in existing_audit_cols:
                cursor.execute("ALTER TABLE suggested_trades_audit ADD COLUMN is_primary INTEGER DEFAULT 1")
            conn.commit()


# Auto-initialize on import
init_watch_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def upsert_watch_target(data: Dict[str, Any]) -> None:
    """Insert or update a watch target from an extracted watch payload."""
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return

    shares_plan = data.get("shares_plan", {})
    options_plan = data.get("options_plan", {})
    invalidation = data.get("invalidation", {})

    side = str(data.get("side") or shares_plan.get("side") or "LONG").upper()
    breakout_level = shares_plan.get("breakout_level")
    breakout_stop = shares_plan.get("breakout_stop")

    now = _now_iso()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO watch_targets (
                    ticker, date, verdict, conviction, actionable, side,
                    entry_type, entry_zone_low, entry_zone_high, breakout_level, breakout_stop,
                    tactical_stop, target_1, target_2, options_structure, options_summary,
                    options_actionable, options_entry_trigger,
                    invalidation_price, invalidation_condition, invalidation_rationale,
                    status, updated_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    date=excluded.date,
                    verdict=excluded.verdict,
                    conviction=excluded.conviction,
                    actionable=excluded.actionable,
                    side=excluded.side,
                    entry_type=excluded.entry_type,
                    entry_zone_low=excluded.entry_zone_low,
                    entry_zone_high=excluded.entry_zone_high,
                    breakout_level=excluded.breakout_level,
                    breakout_stop=excluded.breakout_stop,
                    tactical_stop=excluded.tactical_stop,
                    target_1=excluded.target_1,
                    target_2=excluded.target_2,
                    options_structure=excluded.options_structure,
                    options_summary=excluded.options_summary,
                    options_actionable=excluded.options_actionable,
                    options_entry_trigger=excluded.options_entry_trigger,
                    invalidation_price=excluded.invalidation_price,
                    invalidation_condition=excluded.invalidation_condition,
                    invalidation_rationale=excluded.invalidation_rationale,
                    status=CASE 
                        WHEN watch_targets.status IN ('IN_TRADE', 'IN_ZONE') THEN watch_targets.status 
                        ELSE excluded.status 
                    END,
                    updated_at=excluded.updated_at,
                    raw_json=excluded.raw_json
                """,
                (
                    ticker,
                    data.get("date", datetime.now().strftime("%Y-%m-%d")),
                    data.get("verdict", "STALK"),
                    data.get("conviction", 5),
                    1 if data.get("actionable", True) else 0,
                    side,
                    shares_plan.get("entry_type", "LIMIT"),
                    shares_plan.get("entry_zone_low"),
                    shares_plan.get("entry_zone_high"),
                    breakout_level,
                    breakout_stop,
                    shares_plan.get("tactical_stop"),
                    shares_plan.get("target_1"),
                    shares_plan.get("target_2"),
                    options_plan.get("structure", "NONE"),
                    options_plan.get("summary", ""),
                    1 if (options_plan.get("actionable") or (options_plan.get("structure") not in ("NONE", "", None) and (options_plan.get("target_debit", 0) > 0 or options_plan.get("max_profit", 0) > 0))) else 0,
                    str(options_plan.get("entry_trigger") or "AT_FLOOR_LIMIT"),
                    invalidation.get("price_level"),
                    invalidation.get("condition", "DAILY_CLOSE_BELOW"),
                    invalidation.get("rationale", ""),
                    data.get("status", "STALKING"),
                    now,
                    json.dumps(data, default=str),
                ),
            )
            # Append to history table for immutable audit tracking
            cursor.execute(
                """
                INSERT INTO watch_targets_history (
                    ticker, date, verdict, conviction, actionable, side,
                    entry_type, entry_zone_low, entry_zone_high, breakout_level,
                    breakout_stop, tactical_stop, target_1, target_2,
                    options_structure, options_summary, options_actionable,
                    options_entry_trigger, invalidation_price, invalidation_condition,
                    invalidation_rationale, status, updated_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    data.get("date", datetime.now().strftime("%Y-%m-%d")),
                    data.get("verdict", "STALK"),
                    data.get("conviction", 5),
                    1 if data.get("actionable", True) else 0,
                    side,
                    shares_plan.get("entry_type", "LIMIT"),
                    shares_plan.get("entry_zone_low"),
                    shares_plan.get("entry_zone_high"),
                    breakout_level,
                    breakout_stop,
                    shares_plan.get("tactical_stop"),
                    shares_plan.get("target_1"),
                    shares_plan.get("target_2"),
                    options_plan.get("structure", "NONE"),
                    options_plan.get("summary", ""),
                    1 if (options_plan.get("actionable") or (options_plan.get("structure") not in ("NONE", "", None) and (options_plan.get("target_debit", 0) > 0 or options_plan.get("max_profit", 0) > 0))) else 0,
                    str(options_plan.get("entry_trigger") or "AT_FLOOR_LIMIT"),
                    invalidation.get("price_level"),
                    invalidation.get("condition", "DAILY_CLOSE_BELOW"),
                    invalidation.get("rationale", ""),
                    data.get("status", "STALKING"),
                    now,
                    json.dumps(data, default=str),
                ),
            )
            conn.commit()
            logger.info(f"[{ticker}] Registered in Watchlist DB (Side: {side}, Verdict: {data.get('verdict')}, Status: {data.get('status')})")


def get_active_watch_targets() -> List[Dict[str, Any]]:
    """Return all active watch targets that are not terminated."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM watch_targets 
                WHERE status IN ('STALKING', 'IN_ZONE', 'IN_TRADE', 'TESTING_SUPPORT')
                  AND (verdict IS NULL OR verdict != 'REJECTED_BY_GATE')
                ORDER BY date DESC, conviction DESC
                """
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def get_all_watch_targets() -> List[Dict[str, Any]]:
    """Return all watch targets."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM watch_targets ORDER BY date DESC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def update_target_live_state(
    ticker: str,
    live_price: float,
    status: str,
    distance_to_entry_pct: Optional[float] = None,
    alert_type: Optional[str] = None,
) -> None:
    """Update live price, distance, status, and alert state for a ticker."""
    now = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            if alert_type:
                cursor.execute(
                    """
                    UPDATE watch_targets 
                    SET last_price = ?, status = ?, distance_to_entry_pct = ?,
                        last_alert_type = ?, last_alert_at = ?, updated_at = ?
                    WHERE ticker = ?
                    """,
                    (live_price, status, distance_to_entry_pct, alert_type, now, now, ticker.upper()),
                )
            else:
                cursor.execute(
                    """
                    UPDATE watch_targets 
                    SET last_price = ?, status = ?, distance_to_entry_pct = ?, updated_at = ?
                    WHERE ticker = ?
                    """,
                    (live_price, status, distance_to_entry_pct, now, ticker.upper()),
                )
            conn.commit()


def log_trigger_alert(ticker: str, trigger_type: str, message: str, spot_price: float) -> None:
    """Log an alert event to the watch_alerts audit log."""
    now = _now_iso()
    date_str = datetime.now().strftime("%Y-%m-%d")
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO watch_alerts (ticker, date, trigger_type, message, spot_price, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ticker.upper(), date_str, trigger_type, message, spot_price, now),
            )
            conn.commit()
    logger.info(f"🚨 [WATCH ALERT] [{ticker}] {trigger_type}: {message} (Price: ${spot_price:.2f})")


def get_recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent alert events."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM watch_alerts ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def upsert_superforecasting_prediction(p: Dict[str, Any]) -> None:
    """Insert or update a superforecasting prediction."""
    now = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO superforecasting_audits (
                    ticker, report_date, model_type, horizon_days, target_date,
                    event_description, predicted_probability, actual_outcome,
                    brier_score, evaluation_price, evaluated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, report_date, model_type, horizon_days, event_description) DO UPDATE SET
                    predicted_probability = excluded.predicted_probability,
                    actual_outcome = COALESCE(superforecasting_audits.actual_outcome, excluded.actual_outcome),
                    brier_score = COALESCE(superforecasting_audits.brier_score, excluded.brier_score),
                    evaluation_price = COALESCE(superforecasting_audits.evaluation_price, excluded.evaluation_price),
                    evaluated_at = COALESCE(superforecasting_audits.evaluated_at, excluded.evaluated_at)
                """,
                (
                    p["ticker"].upper(),
                    p["report_date"],
                    p["model_type"],
                    int(p["horizon_days"]),
                    p["target_date"],
                    p["event_description"],
                    float(p["predicted_probability"]),
                    p.get("actual_outcome"),
                    p.get("brier_score"),
                    p.get("evaluation_price"),
                    p.get("evaluated_at"),
                    now,
                ),
            )
            conn.commit()


def get_superforecasting_stats() -> Dict[str, Any]:
    """Calculate aggregate calibration stats and Brier scores by model type."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()

            # Model A (Proprietary Pine) stats
            cursor.execute(
                """
                SELECT 
                    COUNT(*) as total_predictions,
                    COUNT(CASE WHEN actual_outcome IS NOT NULL THEN 1 END) as resolved_predictions,
                    AVG(brier_score) as mean_brier_score,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN (predicted_probability >= 0.5 AND actual_outcome = 1) OR (predicted_probability < 0.5 AND actual_outcome = 0) THEN 1.0 ELSE 0.0 END) ELSE NULL END) as directional_accuracy,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN actual_outcome = 1 THEN 1.0 ELSE 0.0 END) ELSE NULL END) as actual_hit_rate
                FROM superforecasting_audits
                WHERE model_type = 'MODEL_A_PINE'
                """
            )
            row_a = cursor.fetchone()
            model_a_stats = {
                "total": row_a["total_predictions"] if row_a else 0,
                "resolved": row_a["resolved_predictions"] if row_a else 0,
                "brier_score": round(row_a["mean_brier_score"], 4) if row_a and row_a["mean_brier_score"] is not None else 0.0,
                "accuracy_pct": round((row_a["actual_hit_rate"] if row_a and row_a["actual_hit_rate"] is not None else (row_a["directional_accuracy"] or 0.0)) * 100, 1) if row_a else 0.0,
                "directional_pct": round((row_a["directional_accuracy"] or 0.0) * 100, 1) if row_a else 0.0,
            }

            # Model B (Independent Quant) stats
            cursor.execute(
                """
                SELECT 
                    COUNT(*) as total_predictions,
                    COUNT(CASE WHEN actual_outcome IS NOT NULL THEN 1 END) as resolved_predictions,
                    AVG(brier_score) as mean_brier_score,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN (predicted_probability >= 0.5 AND actual_outcome = 1) OR (predicted_probability < 0.5 AND actual_outcome = 0) THEN 1.0 ELSE 0.0 END) ELSE NULL END) as directional_accuracy,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN actual_outcome = 1 THEN 1.0 ELSE 0.0 END) ELSE NULL END) as actual_hit_rate
                FROM superforecasting_audits
                WHERE model_type = 'MODEL_B_INDEPENDENT'
                """
            )
            row_b = cursor.fetchone()
            model_b_stats = {
                "total": row_b["total_predictions"] if row_b else 0,
                "resolved": row_b["resolved_predictions"] if row_b else 0,
                "brier_score": round(row_b["mean_brier_score"], 4) if row_b and row_b["mean_brier_score"] is not None else 0.0,
                "accuracy_pct": round((row_b["actual_hit_rate"] if row_b and row_b["actual_hit_rate"] is not None else (row_b["directional_accuracy"] or 0.0)) * 100, 1) if row_b else 0.0,
                "directional_pct": round((row_b["directional_accuracy"] or 0.0) * 100, 1) if row_b else 0.0,
            }

            # Recent predictions list
            cursor.execute(
                """
                SELECT id, ticker, report_date, model_type, horizon_days, target_date,
                       event_description, predicted_probability, actual_outcome, brier_score, evaluated_at
                FROM superforecasting_audits
                ORDER BY id DESC LIMIT 20
                """
            )
            recent_rows = [dict(r) for r in cursor.fetchall()]

            return {
                "model_a": model_a_stats,
                "model_b": model_b_stats,
                "recent_audits": recent_rows,
            }
