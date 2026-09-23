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


def _compute_hash(
    ticker: str,
    date: str,
    source: str,
    entry_low: float = 0.0,
    entry_high: float = 0.0,
    stop: float = 0.0,
    target_1: float = 0.0,
    target_2: float = 0.0,
) -> str:
    raw = f"{ticker.upper()}:{date}:{source}:{entry_low:.4f}:{entry_high:.4f}:{stop:.4f}:{target_1:.4f}:{target_2:.4f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def log_rejected_plan(ticker: str, date_str: str, plan: Dict[str, Any], reasons: List[str]) -> None:
    """Log level validation failures to the rejected_plans audit table."""
    try:
        with _db_lock:
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS rejected_plans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        side TEXT,
                        plan_json TEXT,
                        reasons TEXT,
                        logged_at TEXT
                    )
                """)
                cursor.execute(
                    "INSERT INTO rejected_plans (ticker, date, side, plan_json, reasons, logged_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ticker.upper(),
                        date_str,
                        plan.get("side", "LONG"),
                        json.dumps(plan),
                        "; ".join(reasons),
                        _now_iso(),
                    ),
                )
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to log rejected plan for {ticker}: {e}")


def ensure_suggestions_schema(conn: sqlite3.Connection) -> None:
    """Ensure suggestions table and all schema migration columns exist."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            report_hash TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'LONG',
            entry_type TEXT DEFAULT 'LIMIT',
            entry_low REAL,
            entry_high REAL,
            breakout_level REAL,
            stop REAL,
            target_1 REAL,
            target_2 REAL,
            planned_rr REAL,
            atr_at_signal REAL,
            taken INTEGER DEFAULT 0,
            your_fill REAL,
            notes TEXT,
            is_modeled INTEGER DEFAULT 0,
            gate_status TEXT DEFAULT 'PASS',
            gate_reasons TEXT,
            verdict TEXT DEFAULT 'STALK',
            setup_lane TEXT,
            kind TEXT DEFAULT 'NEW',
            rr_at_market_at_signal REAL,
            spot_at_signal REAL,
            lane_prior_win REAL,
            lane_prior_ev REAL,
            scorer_version INTEGER DEFAULT 1,
            fill_date TEXT,
            fill_price REAL,
            exit_date TEXT,
            exit_price REAL,
            exit_reason TEXT,
            bars_held INTEGER,
            gross_r REAL,
            r_net REAL,
            mae_r REAL,
            scored_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    for col_def in [
        ("gate_status", "TEXT DEFAULT 'PASS'"),
        ("gate_reasons", "TEXT"),
        ("verdict", "TEXT DEFAULT 'STALK'"),
        ("setup_lane", "TEXT"),
        ("kind", "TEXT DEFAULT 'NEW'"),
        ("atr_at_signal", "REAL"),
        ("rr_at_market_at_signal", "REAL"),
        ("spot_at_signal", "REAL"),
        ("lane_prior_win", "REAL"),
        ("lane_prior_ev", "REAL"),
        ("scorer_version", "INTEGER DEFAULT 1"),
        ("fill_date", "TEXT"),
        ("fill_price", "REAL"),
        ("exit_date", "TEXT"),
        ("exit_price", "REAL"),
        ("exit_reason", "TEXT"),
        ("bars_held", "INTEGER"),
        ("gross_r", "REAL"),
        ("r_net", "REAL"),
        ("mae_r", "REAL"),
        ("scored_at", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE suggestions ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass


def append_suggestion(data: Dict[str, Any]) -> int:
    """Append a suggestion row to data/research_watch.db suggestions table.
    
    Levels are frozen at insert.
    Deduplicates on (ticker, date, source) with last-write-wins semantics.
    """
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    date_str = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    side = str(data.get("side", "LONG")).upper()
    source = data.get("source", "research")

    shares_plan = data.get("shares_plan") or {}
    entry_type = data.get("entry_type") or shares_plan.get("entry_type") or "LIMIT"
    entry_low = float(data.get("entry_low") or data.get("entry_zone_low") or shares_plan.get("entry_low") or shares_plan.get("entry_zone_low") or 0.0)
    entry_high = float(data.get("entry_high") or data.get("entry_zone_high") or shares_plan.get("entry_high") or shares_plan.get("entry_zone_high") or 0.0)
    breakout_level = float(data.get("breakout_level") or shares_plan.get("breakout_level") or 0.0)
    stop = float(data.get("stop") or data.get("tactical_stop") or shares_plan.get("stop") or shares_plan.get("tactical_stop") or 0.0)
    target_1 = float(data.get("target_1") or shares_plan.get("target_1") or 0.0)
    target_2 = float(data.get("target_2") or shares_plan.get("target_2") or 0.0)
    planned_rr = float(data.get("planned_rr") or shares_plan.get("rr_ratio") or 0.0)
    atr_at_signal = float(data.get("atr_at_signal") or 0.0)
    taken = 1 if data.get("taken") else 0
    your_fill = data.get("your_fill")
    is_modeled = 1 if data.get("is_modeled") else 0

    verdict = data.get("verdict") or "STALK"
    setup_lane = data.get("setup_lane")
    kind = data.get("kind") or "NEW"
    spot_at_signal = float(data.get("spot_at_signal") or data.get("spot") or data.get("price") or 0.0)
    rr_at_market_at_signal = float(data.get("rr_at_market_at_signal") or data.get("rr_at_market") or data.get("long_rr_at_market") or 0.0)
    lane_prior_win = float(data.get("lane_prior_win")) if data.get("lane_prior_win") is not None else None
    lane_prior_ev = float(data.get("lane_prior_ev")) if data.get("lane_prior_ev") is not None else None
    gate_status = data.get("gate_status") or "PASS"
    gate_reasons = data.get("gate_reasons")
    notes = data.get("notes") or data.get("triage_reason") or ""

    report_hash = data.get("report_hash") or _compute_hash(
        ticker, date_str, source, entry_low, entry_high, stop, target_1, target_2
    )

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                ensure_suggestions_schema(conn)

                # Dedupe (ticker, date, source) with last write wins
                existing = cursor.execute(
                    "SELECT id FROM suggestions WHERE ticker = ? AND date = ? AND source = ?",
                    (ticker, date_str, source),
                ).fetchone()

                if existing:
                    row_id = existing[0]
                    cursor.execute(
                        """
                        UPDATE suggestions
                        SET report_hash=?, side=?, entry_type=?, entry_low=?, entry_high=?,
                            breakout_level=?, stop=?, target_1=?, target_2=?, planned_rr=?,
                            atr_at_signal=?, taken=?, your_fill=?, notes=?, is_modeled=?,
                            gate_status=?, gate_reasons=?, verdict=?, setup_lane=?, kind=?,
                            rr_at_market_at_signal=?, spot_at_signal=?, lane_prior_win=?, lane_prior_ev=?,
                            scored_at=NULL, r_net=NULL, gross_r=NULL, exit_reason=NULL
                        WHERE id = ?
                        """,
                        (
                            report_hash, side, entry_type, entry_low, entry_high,
                            breakout_level, stop, target_1, target_2, planned_rr if planned_rr > 0 else None,
                            atr_at_signal if atr_at_signal > 0 else None, taken, your_fill, notes, is_modeled,
                            gate_status, gate_reasons, verdict, setup_lane, kind,
                            rr_at_market_at_signal if rr_at_market_at_signal > 0 else None,
                            spot_at_signal if spot_at_signal > 0 else None,
                            lane_prior_win, lane_prior_ev,
                            row_id,
                        ),
                    )
                    conn.commit()
                    logger.info(f"[ledger] Updated existing suggestion {row_id}: {ticker} {date_str} source={source}")
                    return row_id
                else:
                    cursor.execute(
                        """
                        INSERT INTO suggestions (
                            ticker, date, source, report_hash, side, entry_type,
                            entry_low, entry_high, breakout_level, stop,
                            target_1, target_2, planned_rr, atr_at_signal,
                            taken, your_fill, notes, is_modeled, gate_status, gate_reasons,
                            verdict, setup_lane, kind, rr_at_market_at_signal, spot_at_signal,
                            lane_prior_win, lane_prior_ev, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ticker, date_str, source, report_hash, side, entry_type,
                            entry_low, entry_high, breakout_level, stop,
                            target_1, target_2, planned_rr if planned_rr > 0 else None,
                            atr_at_signal if atr_at_signal > 0 else None,
                            taken, your_fill, notes, is_modeled, gate_status, gate_reasons,
                            verdict, setup_lane, kind,
                            rr_at_market_at_signal if rr_at_market_at_signal > 0 else None,
                            spot_at_signal if spot_at_signal > 0 else None,
                            lane_prior_win, lane_prior_ev, _now_iso(),
                        ),
                    )
                    conn.commit()
                    row_id = cursor.lastrowid
                    logger.info(f"[ledger] Appended suggestion {row_id}: {ticker} {date_str} source={source}")
                    return row_id
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


def get_per_source_stats(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return performance aggregated per (source, setup_lane, gate_status)."""
    priors_map = {
        "RR_SETUP_STRONG": (25.0, 0.13),
        "RR_SETUP": (30.0, 0.08),
        "CODE20": (45.0, 0.08),
        "OVERSOLD": (51.0, 0.06),
        "RSI2": (61.0, 0.06),
    }

    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                where_clause = "WHERE date >= ?" if since else ""
                params = [since] if since else []
                group_rows = cursor.execute(
                    f"""
                    SELECT DISTINCT 
                        source, 
                        COALESCE(setup_lane, 'UNKNOWN') as setup_lane,
                        COALESCE(gate_status, 'PASS') as gate_status 
                    FROM suggestions 
                    {where_clause}
                    ORDER BY source, setup_lane, gate_status
                    """,
                    params,
                ).fetchall()
            except Exception:
                return []

            stats = []
            for g in group_rows:
                src = g["source"]
                lane = g["setup_lane"]
                gate_st = g["gate_status"]

                sql = "SELECT * FROM suggestions WHERE source = ? AND COALESCE(setup_lane, 'UNKNOWN') = ? AND COALESCE(gate_status, 'PASS') = ?"
                q_params = [src, lane, gate_st]
                if since:
                    sql += " AND date >= ?"
                    q_params.append(since)

                rows = cursor.execute(sql, q_params).fetchall()
                total = len(rows)
                taken_count = sum(1 for r in rows if r["taken"])
                filled_count = sum(1 for r in rows if (r["fill_price"] is not None and r["fill_price"] > 0) or (r["exit_reason"] and r["exit_reason"] != "NOT_FILLED"))
                fill_rate = round(filled_count / total * 100, 1) if total > 0 else 0.0

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

                prior_win, prior_ev = priors_map.get(lane, (None, None))
                # Check if row recorded its own lane prior
                if prior_win is None and rows and rows[0]["lane_prior_win"] is not None:
                    prior_win = rows[0]["lane_prior_win"]
                    prior_ev = rows[0]["lane_prior_ev"]

                stats.append({
                    "source": src,
                    "setup_lane": lane,
                    "lane": lane,
                    "gate_status": gate_st,
                    "n": total,
                    "total": total,
                    "filled_count": filled_count,
                    "fill_rate": fill_rate,
                    "scored": len(scored_rows),
                    "scored_count": len(scored_rows),
                    "mean_r": mean_r,
                    "median_r": median_r,
                    "win": win_pct,
                    "win_rate_pct": win_pct,
                    "stop_out_pct": stopped_pct,
                    "lane_prior_win": prior_win,
                    "lane_prior_ev": prior_ev,
                    "taken_count": taken_count,
                    "not_taken_count": total - taken_count,
                    "flag_n30": len(scored_rows) >= 30,
                    "read": len(scored_rows) >= 30,
                })
            return stats
