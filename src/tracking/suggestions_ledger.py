"""
suggestions_ledger.py — Append-only suggestions ledger + honest 21-bar R scorer.

Phase 1 & 4: Append-only suggestions table in data/research_watch.db,
levels validated before persist, honest 21-bar R scoring, per-source attribution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.logic.level_validation import validate_levels
from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger(__name__)


def _compute_hash(ticker: str, date: str, source: str, report_source: str = "") -> str:
    raw = f"{ticker.upper()}:{date}:{source}:{report_source}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def append_suggestion(data: Dict[str, Any]) -> int:
    """Append a suggestion row to data/research_watch.db suggestions table.
    
    Levels are frozen at insert. Validation runs first.
    If level gate fails, marks verdict=REJECTED_BY_GATE and source=judge so gate is measurable.
    """
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    date_str = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    side = str(data.get("side", "LONG")).upper()
    source = data.get("source", "research")
    report_source = data.get("report_source", "pine")
    report_hash = data.get("report_hash") or _compute_hash(ticker, date_str, source, report_source)

    # Run level validation gate
    dw = data.get("_datawindow", {})
    ok, reasons = validate_levels(data, dw, side)
    if not ok:
        logger.warning(
            f"[ledger] Level gate FAILED for {ticker}: {'; '.join(reasons)} — logging as REJECTED_BY_GATE"
        )
        source = "judge"
        notes = f"REJECTED_BY_GATE: {'; '.join(reasons)}"
    else:
        notes = data.get("notes") or data.get("triage_reason") or ""

    entry_type = data.get("entry_type") or "LIMIT"
    entry_low = float(data.get("entry_low") or data.get("entry_zone_low") or 0.0)
    entry_high = float(data.get("entry_high") or data.get("entry_zone_high") or 0.0)
    breakout_level = float(data.get("breakout_level") or 0.0)
    stop = float(data.get("stop") or data.get("tactical_stop") or 0.0)
    target_1 = float(data.get("target_1") or 0.0)
    target_2 = float(data.get("target_2") or 0.0)
    planned_rr = float(data.get("planned_rr") or 0.0)
    atr_at_signal = float(data.get("atr_at_signal") or 0.0)
    taken = 1 if data.get("taken") else 0
    your_fill = data.get("your_fill")
    is_modeled = 1 if data.get("is_modeled") else 0

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
                        ticker, date_str, source, report_hash, side, entry_type,
                        entry_low, entry_high, breakout_level, stop,
                        target_1, target_2, planned_rr if planned_rr > 0 else None,
                        atr_at_signal if atr_at_signal > 0 else None,
                        taken, your_fill, notes, is_modeled, _now_iso(),
                    ),
                )
                conn.commit()
                row_id = cursor.lastrowid
                if cursor.rowcount > 0:
                    logger.info(f"[ledger] Appended suggestion {row_id}: {ticker} {date_str} source={source}")
                    return row_id
                else:
                    logger.debug(f"[ledger] Duplicate suggestion skipped: {ticker} {date_str} {source}")
                    return -1
            except Exception as e:
                logger.error(f"[ledger] Failed to insert suggestion: {e}")
                return -1


def update_suggestion_user_input(
    suggestion_id: int,
    taken: bool,
    your_fill: Optional[float] = None,
    notes: Optional[str] = None,
) -> bool:
    """Update human inputs (taken, your_fill, notes) for a suggestion row."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            updates = ["taken = ?"]
            params: List[Any] = [1 if taken else 0]
            if your_fill is not None:
                updates.append("your_fill = ?")
                params.append(your_fill)
            if notes is not None:
                updates.append("notes = ?")
                params.append(notes)
            params.append(suggestion_id)
            cursor.execute(f"UPDATE suggestions SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            return cursor.rowcount > 0


def get_ledger_summary(ticker: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    """Query suggestions ledger for rows and summary stats."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            where = ""
            params: List[Any] = []
            if ticker:
                where = "WHERE ticker = ?"
                params.append(ticker.strip().upper())

            total = cursor.execute(f"SELECT COUNT(*) as cnt FROM suggestions {where}", params).fetchone()["cnt"]
            rows = cursor.execute(
                f"SELECT * FROM suggestions {where} ORDER BY date DESC, id DESC LIMIT ?",
                params + [limit],
            ).fetchall()

            resolved = [dict(r) for r in rows if r["r_net"] is not None]
            if resolved:
                r_vals = [float(r["r_net"]) for r in resolved]
                avg_r = round(statistics.mean(r_vals), 4)
                win_count = sum(1 for r in r_vals if r > 0)
                win_rate = round(win_count / len(resolved) * 100, 1)
            else:
                avg_r = 0.0
                win_rate = 0.0

            return {
                "total": total,
                "avg_r": avg_r,
                "win_rate_pct": win_rate,
                "rows": [dict(r) for r in rows],
            }


def get_per_source_stats() -> List[Dict[str, Any]]:
    """Return performance aggregated per research source."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sources = [r["source"] for r in cursor.execute("SELECT DISTINCT source FROM suggestions").fetchall()]
            stats = []
            for src in sources:
                rows = cursor.execute(
                    "SELECT * FROM suggestions WHERE source = ?", (src,)
                ).fetchall()
                total = len(rows)
                taken_count = sum(1 for r in rows if r["taken"])
                scored_rows = [r for r in rows if r["r_net"] is not None]
                if scored_rows:
                    r_vals = [float(r["r_net"]) for r in scored_rows]
                    mean_r = round(statistics.mean(r_vals), 4)
                    median_r = round(statistics.median(r_vals), 4)
                    win_pct = round(sum(1 for r in r_vals if r > 0) / len(r_vals) * 100, 1)
                    stopped_pct = round(sum(1 for r in scored_rows if r["exit_reason"] == "STOP_BREACHED") / len(scored_rows) * 100, 1)
                else:
                    mean_r = None
                    median_r = None
                    win_pct = 0.0
                    stopped_pct = 0.0

                stats.append({
                    "source": src,
                    "total": total,
                    "scored_count": len(scored_rows),
                    "mean_r": mean_r,
                    "median_r": median_r,
                    "win_rate_pct": win_pct,
                    "stop_out_pct": stopped_pct,
                    "taken_count": taken_count,
                    "not_taken_count": total - taken_count,
                })
            return stats
