"""
30-Day Historical Options Data & Institutional Flow Manager.
Stores daily snapshots of options flow, institutional sweeps, and volatility metrics (IV Rank, HV30).
Enforces a rolling 30-day retention policy with automatic pruning.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

_DB_LOCK = threading.RLock()


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_options_history_db_path() -> Path:
    return config.BASE_DIR / "data" / "options_history.db"


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS options_flow_daily (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            spot_price REAL,
            sentiment TEXT,
            sentiment_label TEXT,
            total_sweeps INTEGER DEFAULT 0,
            call_sweeps INTEGER DEFAULT 0,
            put_sweeps INTEGER DEFAULT 0,
            total_call_volume INTEGER DEFAULT 0,
            total_put_volume INTEGER DEFAULT 0,
            total_call_premium REAL DEFAULT 0.0,
            total_put_premium REAL DEFAULT 0.0,
            put_call_volume_ratio REAL,
            put_call_premium_ratio REAL,
            iv_rank REAL,
            iv_percentile REAL,
            hv_30 REAL,
            iv_30 REAL,
            iv_hv_spread REAL,
            beta REAL,
            data_source TEXT,
            anomalies_json TEXT,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS options_sweeps_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            type TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,
            dte INTEGER,
            volume INTEGER,
            open_interest INTEGER,
            vol_to_oi REAL,
            delta REAL,
            mid REAL,
            notional_premium REAL,
            in_the_money INTEGER,
            distance_from_spot_pct REAL,
            UNIQUE (ticker, date, type, strike, expiry)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_daily_ticker ON options_flow_daily(ticker, date DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sweeps_hist_ticker ON options_sweeps_history(ticker, date DESC)")


def record_options_snapshot(
    flow_data: Dict[str, Any],
    target_date: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """
    Persist or upsert a daily options flow and institutional sweeps snapshot for a ticker.
    Automatically prunes entries older than 30 days.
    """
    if not flow_data or not isinstance(flow_data, dict):
        return False

    ticker = (flow_data.get("ticker") or "").upper().strip()
    if not ticker:
        return False

    now = datetime.now()
    day = target_date or flow_data.get("date") or now.strftime("%Y-%m-%d")
    iso_ts = now.isoformat(timespec="seconds")

    anomalies = flow_data.get("anomalies") or []
    anomalies_json = json.dumps(anomalies[:40]) if anomalies else "[]"

    target_db = Path(db_path) if db_path else get_options_history_db_path()
    target_db.parent.mkdir(parents=True, exist_ok=True)

    with _DB_LOCK:
        try:
            with sqlite3.connect(str(target_db), timeout=15) as conn:
                _init_db(conn)

                # Upsert daily summary record
                conn.execute(
                    """
                    INSERT INTO options_flow_daily (
                        ticker, date, timestamp, spot_price, sentiment, sentiment_label,
                        total_sweeps, call_sweeps, put_sweeps,
                        total_call_volume, total_put_volume, total_call_premium, total_put_premium,
                        put_call_volume_ratio, put_call_premium_ratio,
                        iv_rank, iv_percentile, hv_30, iv_30, iv_hv_spread, beta,
                        data_source, anomalies_json
                    ) VALUES (
                        :ticker, :date, :timestamp, :spot_price, :sentiment, :sentiment_label,
                        :total_sweeps, :call_sweeps, :put_sweeps,
                        :total_call_volume, :total_put_volume, :total_call_premium, :total_put_premium,
                        :put_call_volume_ratio, :put_call_premium_ratio,
                        :iv_rank, :iv_percentile, :hv_30, :iv_30, :iv_hv_spread, :beta,
                        :data_source, :anomalies_json
                    )
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        timestamp = excluded.timestamp,
                        spot_price = COALESCE(excluded.spot_price, spot_price),
                        sentiment = COALESCE(excluded.sentiment, sentiment),
                        sentiment_label = COALESCE(excluded.sentiment_label, sentiment_label),
                        total_sweeps = excluded.total_sweeps,
                        call_sweeps = excluded.call_sweeps,
                        put_sweeps = excluded.put_sweeps,
                        total_call_volume = excluded.total_call_volume,
                        total_put_volume = excluded.total_put_volume,
                        total_call_premium = excluded.total_call_premium,
                        total_put_premium = excluded.total_put_premium,
                        put_call_volume_ratio = excluded.put_call_volume_ratio,
                        put_call_premium_ratio = excluded.put_call_premium_ratio,
                        iv_rank = COALESCE(excluded.iv_rank, iv_rank),
                        iv_percentile = COALESCE(excluded.iv_percentile, iv_percentile),
                        hv_30 = COALESCE(excluded.hv_30, hv_30),
                        iv_30 = COALESCE(excluded.iv_30, iv_30),
                        iv_hv_spread = COALESCE(excluded.iv_hv_spread, iv_hv_spread),
                        beta = COALESCE(excluded.beta, beta),
                        data_source = excluded.data_source,
                        anomalies_json = excluded.anomalies_json
                    """,
                    {
                        "ticker": ticker,
                        "date": day,
                        "timestamp": iso_ts,
                        "spot_price": _safe_float(flow_data.get("underlying_price")),
                        "sentiment": flow_data.get("sentiment"),
                        "sentiment_label": flow_data.get("sentiment_label"),
                        "total_sweeps": _safe_int(flow_data.get("total_anomalies_count"), len(anomalies)),
                        "call_sweeps": _safe_int(flow_data.get("call_sweeps_count"), 0),
                        "put_sweeps": _safe_int(flow_data.get("put_sweeps_count"), 0),
                        "total_call_volume": _safe_int(flow_data.get("total_call_volume"), 0),
                        "total_put_volume": _safe_int(flow_data.get("total_put_volume"), 0),
                        "total_call_premium": _safe_float(flow_data.get("total_call_premium"), 0.0),
                        "total_put_premium": _safe_float(flow_data.get("total_put_premium"), 0.0),
                        "put_call_volume_ratio": _safe_float(flow_data.get("put_call_volume_ratio")),
                        "put_call_premium_ratio": _safe_float(flow_data.get("put_call_premium_ratio")),
                        "iv_rank": _safe_float(flow_data.get("iv_rank")),
                        "iv_percentile": _safe_float(flow_data.get("iv_percentile")),
                        "hv_30": _safe_float(flow_data.get("hv_30")),
                        "iv_30": _safe_float(flow_data.get("iv_30")),
                        "iv_hv_spread": _safe_float(flow_data.get("iv_hv_spread")),
                        "beta": _safe_float(flow_data.get("beta")),
                        "data_source": flow_data.get("data_source") or "Multi-Broker Feed",
                        "anomalies_json": anomalies_json,
                    },
                )

                # Insert individual sweeps into history table
                for a in anomalies:
                    try:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO options_sweeps_history (
                                ticker, date, timestamp, type, strike, expiry, dte,
                                volume, open_interest, vol_to_oi, delta, mid,
                                notional_premium, in_the_money, distance_from_spot_pct
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                ticker,
                                day,
                                iso_ts,
                                a.get("type", "CALL"),
                                _safe_float(a.get("strike"), 0.0),
                                str(a.get("expiry", "")),
                                _safe_int(a.get("dte"), 0),
                                _safe_int(a.get("volume"), 0),
                                _safe_int(a.get("open_interest"), 0),
                                _safe_float(a.get("vol_to_oi"), 0.0),
                                _safe_float(a.get("delta")),
                                _safe_float(a.get("mid")),
                                _safe_float(a.get("notional_premium"), 0.0),
                                int(bool(a.get("in_the_money"))),
                                _safe_float(a.get("distance_from_spot_pct"), 0.0),
                            ),
                        )
                    except Exception as e_row:
                        logger.debug("Failed to record individual sweep row: %s", e_row)

                # Prune records older than 30 days
                cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
                conn.execute("DELETE FROM options_flow_daily WHERE date < ?", (cutoff,))
                conn.execute("DELETE FROM options_sweeps_history WHERE date < ?", (cutoff,))

            return True
        except Exception as e:
            logger.error(f"Failed to record options snapshot for {ticker}: {e}")
            return False


def get_options_history(
    ticker: str,
    days: int = 30,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve daily options history and volatility metrics for a ticker over the last N days (default 30).
    Returns list of daily summary dicts sorted chronologically (newest first).
    """
    sym = ticker.upper().strip()
    target_db = Path(db_path) if db_path else get_options_history_db_path()
    if not target_db.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with _DB_LOCK:
        try:
            with sqlite3.connect(str(target_db), timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                _init_db(conn)
                rows = conn.execute(
                    """
                    SELECT * FROM options_flow_daily 
                    WHERE ticker = ? AND date >= ?
                    ORDER BY date DESC
                    """,
                    (sym, cutoff),
                ).fetchall()

                result = []
                for r in rows:
                    item = dict(r)
                    raw_anomalies = item.pop("anomalies_json", None)
                    try:
                        item["anomalies"] = json.loads(raw_anomalies) if raw_anomalies else []
                    except Exception:
                        item["anomalies"] = []
                    result.append(item)
                return result
        except Exception as e:
            logger.error(f"Failed to fetch options history for {sym}: {e}")
            return []


def get_sweeps_history(
    ticker: str,
    date: Optional[str] = None,
    days: int = 30,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve individual institutional sweeps history for a ticker.
    Can filter by specific date or last N days.
    """
    sym = ticker.upper().strip()
    target_db = Path(db_path) if db_path else get_options_history_db_path()
    if not target_db.exists():
        return []

    with _DB_LOCK:
        try:
            with sqlite3.connect(str(target_db), timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                _init_db(conn)

                if date:
                    rows = conn.execute(
                        """
                        SELECT * FROM options_sweeps_history
                        WHERE ticker = ? AND date = ?
                        ORDER BY vol_to_oi DESC, volume DESC
                        """,
                        (sym, date),
                    ).fetchall()
                else:
                    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
                    rows = conn.execute(
                        """
                        SELECT * FROM options_sweeps_history
                        WHERE ticker = ? AND date >= ?
                        ORDER BY date DESC, vol_to_oi DESC
                        """,
                        (sym, cutoff),
                    ).fetchall()

                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to fetch sweeps history for {sym}: {e}")
            return []


def prune_options_history(days: int = 30, db_path: Optional[Path | str] = None) -> int:
    """Explicitly prune all options history records older than N days (default 30)."""
    target_db = Path(db_path) if db_path else get_options_history_db_path()
    if not target_db.exists():
        return 0

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _DB_LOCK:
        try:
            with sqlite3.connect(str(target_db), timeout=15) as conn:
                _init_db(conn)
                c1 = conn.execute("DELETE FROM options_flow_daily WHERE date < ?", (cutoff,)).rowcount
                c2 = conn.execute("DELETE FROM options_sweeps_history WHERE date < ?", (cutoff,)).rowcount
                return c1 + c2
        except Exception as e:
            logger.error(f"Failed to prune options history: {e}")
            return 0


class OptionsHistoryManager:
    """Class wrapper for 30-day options history management."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path else get_options_history_db_path()

    def record_options_snapshot(self, flow_data: Dict[str, Any], target_date: Optional[str] = None) -> bool:
        return record_options_snapshot(flow_data, target_date=target_date, db_path=self.db_path)

    def get_options_history(self, ticker: str, days: int = 30) -> List[Dict[str, Any]]:
        return get_options_history(ticker, days=days, db_path=self.db_path)

    def get_sweeps_history(self, ticker: str, date: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        return get_sweeps_history(ticker, date=date, days=days, db_path=self.db_path)

    def prune_options_history(self, days: int = 30) -> int:
        return prune_options_history(days=days, db_path=self.db_path)
