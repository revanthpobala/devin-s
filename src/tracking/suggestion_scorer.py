"""
Suggestion Scorer — honest 21-bar R scoring with fees.

Walks bars starting from the bar AFTER the signal, exits after 21 trading
days, charges 10bps round trip, reports R = (exit - fill) / (fill - stop).
Reuses the conventions in data-windows/scripts/honest_entry_replay.py:
next open, stop checked first, skip gaps through the stop.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.tracking.execution_validator import evaluate_setup_lifecycle_bars
from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger(__name__)

MAX_HOLDING_BARS = 21
ROUND_TRIP_FEES_BPS = 10  # 10bps = 0.10%


def _load_suggestions(
    ticker: Optional[str] = None,
    source: Optional[str] = None,
    scored: bool = False,
) -> List[Dict[str, Any]]:
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = "SELECT * FROM suggestions WHERE 1=1"
            params: List[Any] = []
            if ticker:
                sql += " AND ticker = ?"
                params.append(ticker.upper())
            if source:
                sql += " AND source = ?"
                params.append(source)
            if scored:
                sql += " AND r_net IS NOT NULL"
            sql += " ORDER BY date ASC"
            rows = cursor.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


def score_suggestion(row: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single suggestion row. Returns the row with scoring fields filled."""
    import sqlite3 as _sqlite3

    ticker = row["ticker"]
    date_str = row["date"]
    side = (row.get("side") or "LONG").upper()
    entry_low = float(row.get("entry_low") or 0.0)
    entry_high = float(row.get("entry_high") or 0.0)
    stop = float(row.get("stop") or 0.0)
    target_1 = float(row.get("target_1") or 0.0)
    target_2 = float(row.get("target_2") or 0.0)
    breakout_level = float(row.get("breakout_level") or 0.0)
    entry_type = (row.get("entry_type") or "LIMIT").upper()

    if not ticker or not date_str:
        return {**row, "r_net": None, "mae_r": None, "scored_at": _now_iso()}

    fill_price = float(row.get("your_fill") or 0.0)
    if fill_price <= 0:
        if breakout_level > 0:
            fill_price = breakout_level
        elif entry_low > 0 and entry_high > 0:
            fill_price = round((entry_low + entry_high) / 2.0, 2)
        elif entry_low > 0:
            fill_price = entry_low
        else:
            return {**row, "r_net": None, "mae_r": None, "scored_at": _now_iso()}

    from src.tracking.execution_validator import get_bars_since_date

    bars = get_bars_since_date(ticker, date_str)
    if bars is None or bars.empty:
        logger.debug(f"No bars for {ticker} since {date_str}; cannot score.")
        return {**row, "r_net": None, "mae_r": None, "scored_at": _now_iso()}

    eval_res = evaluate_setup_lifecycle_bars(
        bars=bars,
        setup_date=date_str,
        side=side,
        entry_type=entry_type,
        entry_low=entry_low,
        entry_high=entry_high,
        breakout_level=breakout_level,
        stop_loss=stop,
        target_1=target_1,
        target_2=target_2,
        live_price=float(bars.iloc[-1].get("Close", bars.iloc[-1].get("close", 0.0))),
        max_holding_bars=MAX_HOLDING_BARS,
        skip_setup_bar=True,
    )

    exit_price = eval_res.get("exit_price") or 0.0
    exit_date = eval_res.get("exit_date") or ""
    exit_reason = eval_res.get("exit_reason") or eval_res.get("status") or ""

    risk = fill_price - stop
    if risk <= 0:
        r_net = None
        mae_r = None
    else:
        gross_r = (exit_price - fill_price) / risk if exit_price > 0 else 0.0
        fee_cost = ROUND_TRIP_FEES_BPS / 10000.0
        r_net = gross_r - fee_cost
        mae_r = gross_r

    now = _now_iso()
    return {
        **row,
        "fill_price": fill_price,
        "exit_date": exit_date,
        "exit_price": exit_price if exit_price > 0 else None,
        "exit_reason": exit_reason,
        "r_net": round(r_net, 4) if r_net is not None else None,
        "mae_r": round(mae_r, 4) if mae_r is not None else None,
        "scored_at": now,
    }


def evaluate_all_suggestions() -> Dict[str, Any]:
    """Score all unscored suggestions. Returns summary stats."""
    rows = _load_suggestions(scored=False)
    scored_count = 0
    errors = []

    for row in rows:
        try:
            scored = score_suggestion(row)
            _upsert_scored(scored)
            scored_count += 1
        except Exception as e:
            errors.append(f"{row.get('ticker')}/{row.get('date')}: {e}")
            logger.warning(f"Failed to score {row.get('ticker')}/{row.get('date')}: {e}")

    stats = _compute_stats()
    return {
        "scored": scored_count,
        "errors": errors,
        "stats": stats,
    }


def _upsert_scored(row: Dict[str, Any]) -> None:
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE suggestions
                SET fill_date=?, fill_price=?, exit_date=?, exit_price=?,
                    exit_reason=?, r_net=?, mae_r=?, scored_at=?
                WHERE ticker=? AND date=? AND source=? AND report_hash=?
                """,
                (
                    row.get("fill_date"),
                    row.get("fill_price"),
                    row.get("exit_date"),
                    row.get("exit_price"),
                    row.get("exit_reason"),
                    row.get("r_net"),
                    row.get("mae_r"),
                    row.get("scored_at"),
                    row["ticker"],
                    row["date"],
                    row["source"],
                    row.get("report_hash"),
                ),
            )
            conn.commit()


def _compute_stats() -> Dict[str, Any]:
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT * FROM suggestions WHERE r_net IS NOT NULL"
            ).fetchall()
            if not rows:
                return {}
            r_vals = [float(r["r_net"]) for r in rows if r["r_net"] is not None]
            import statistics as _stats
            return {
                "total_scored": len(r_vals),
                "mean_r": round(_stats.mean(r_vals), 4) if r_vals else None,
                "median_r": round(_stats.median(r_vals), 4) if r_vals else None,
                "win_rate": round(sum(1 for r in r_vals if r > 0) / len(r_vals) * 100, 1),
                "min_r": round(min(r_vals), 4),
                "max_r": round(max(r_vals), 4),
            }


def insert_suggestion(row: Dict[str, Any]) -> bool:
    """Insert a suggestion with ON CONFLICT DO NOTHING. Returns whether inserted."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO suggestions (
                        ticker, date, source, report_hash, side, entry_type,
                        entry_low, entry_high, breakout_level, stop,
                        target_1, target_2, planned_rr, atr_at_signal,
                        taken, your_fill, notes, is_modeled, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date, source, report_hash) DO NOTHING
                    """,
                    (
                        row["ticker"],
                        row["date"],
                        row["source"],
                        row.get("report_hash"),
                        row.get("side", "LONG"),
                        row.get("entry_type", "LIMIT"),
                        row.get("entry_low"),
                        row.get("entry_high"),
                        row.get("breakout_level"),
                        row.get("stop"),
                        row.get("target_1"),
                        row.get("target_2"),
                        row.get("planned_rr"),
                        row.get("atr_at_signal"),
                        row.get("taken", 0),
                        row.get("your_fill"),
                        row.get("notes"),
                        row.get("is_modeled", 0),
                        _now_iso(),
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.warning(f"Failed to insert suggestion: {e}")
                return False
