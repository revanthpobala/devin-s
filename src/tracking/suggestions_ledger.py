"""
suggestions_ledger.py — Append-only suggestions ledger + honest 21-bar R scorer.

Phase 1: Append-only suggestions table + honest 21-bar R scorer with fees;
backfill legacy audit rows.

The ledger is append-only: rows are never updated, only inserted.
The R scorer computes realized R over 21 bars including fees and slippage,
not the modeled option P/L that the auditor previously used.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config
from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger(__name__)

LEDGER_DB_PATH = config.BASE_DIR / "data" / "suggestions_ledger.db"


def init_ledger_db():
    """Create the append-only suggestions ledger tables."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'LONG',
                    verdict TEXT NOT NULL,
                    triage_reason TEXT,
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    zone_midpoint REAL,
                    stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    breakout_level REAL,
                    options_structure TEXT,
                    options_actionable BOOLEAN DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'research',
                    rule_triage TEXT,
                    report_source TEXT DEFAULT 'pine',
                    taken BOOLEAN DEFAULT 0,
                    fill_price REAL,
                    fill_at TEXT,
                    exit_price REAL,
                    exit_at TEXT,
                    exit_reason TEXT,
                    realized_r REAL DEFAULT 0.0,
                    realized_pnl REAL DEFAULT 0.0,
                    fees REAL DEFAULT 0.0,
                    slippage REAL DEFAULT 0.0,
                    bars_to_resolution INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, date, source, rule_triage)
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()


init_ledger_db()


def append_suggestion(data: Dict[str, Any]) -> int:
    """Append a suggestion row. Never updates — insert only.

    PHASE 4: rule-only triage, each report source, taken/fill fields.
    """
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    side = data.get("side", "LONG").upper()
    verdict = data.get("verdict", "STALK")
    source = data.get("source", "research")
    rule_triage = data.get("rule_triage", "")
    report_source = data.get("report_source", "pine")

    created = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO suggestions (
                        ticker, date, side, verdict, triage_reason,
                        entry_zone_low, entry_zone_high, zone_midpoint,
                        stop, target_1, target_2, breakout_level,
                        options_structure, options_actionable,
                        source, rule_triage, report_source,
                        taken, fill_price, fill_at, exit_price, exit_at,
                        exit_reason, realized_r, realized_pnl, fees, slippage,
                        bars_to_resolution, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker, date, side, verdict, data.get("triage_reason", ""),
                        data.get("entry_zone_low"), data.get("entry_zone_high"),
                        data.get("zone_midpoint"), data.get("stop"),
                        data.get("target_1"), data.get("target_2"),
                        data.get("breakout_level"), data.get("options_structure"),
                        1 if data.get("options_actionable") else 0,
                        source, rule_triage, report_source,
                        1 if data.get("taken") else 0,
                        data.get("fill_price"), data.get("fill_at"),
                        data.get("exit_price"), data.get("exit_at"),
                        data.get("exit_reason"), data.get("realized_r", 0.0),
                        data.get("realized_pnl", 0.0), data.get("fees", 0.0),
                        data.get("slippage", 0.0), data.get("bars_to_resolution"),
                        created,
                    ),
                )
                conn.commit()
                row_id = cursor.lastrowid
                logger.info(f"[ledger] Appended suggestion {row_id}: {ticker} {date} {verdict}")
                return row_id
            except sqlite3.IntegrityError as e:
                logger.debug(f"[ledger] Duplicate suggestion skipped: {e}")
                return -1


def score_21bar_r(
    entry_price: float,
    exit_price: float,
    stop: float,
    target_1: float,
    side: str = "LONG",
    fees: float = 0.0,
    slippage: float = 0.0,
    bars: int = 21,
) -> Dict[str, Any]:
    """Honest 21-bar R scorer with fees.

    Computes realized R relative to the entry price, including fees and
    slippage. If the trade hasn't resolved by 21 bars, returns the
    unrealized R at the current price with bars_to_resolution = 21.

    Returns dict with: realized_r, realized_pnl, fees, slippage,
    bars_to_resolution, resolved (bool).
    """
    if entry_price <= 0:
        return {
            "realized_r": 0.0,
            "realized_pnl": 0.0,
            "fees": fees,
            "slippage": slippage,
            "bars_to_resolution": bars,
            "resolved": False,
        }

    if side == "LONG":
        pnl_per_share = exit_price - entry_price - fees - slippage
        risk_per_share = abs(entry_price - stop) if stop > 0 else entry_price * 0.01
    else:
        pnl_per_share = entry_price - exit_price - fees - slippage
        risk_per_share = abs(stop - entry_price) if stop > 0 else entry_price * 0.01

    realized_pnl = round(pnl_per_share, 4)
    realized_r = round(pnl_per_share / risk_per_share, 4) if risk_per_share > 0 else 0.0

    return {
        "realized_r": realized_r,
        "realized_pnl": round(realized_pnl, 2),
        "fees": fees,
        "slippage": slippage,
        "bars_to_resolution": bars,
        "resolved": exit_price is not None and exit_price != 0.0,
    }


def backfill_legacy_audit_rows() -> int:
    """Backfill legacy audit rows from suggested_trades_audit into the ledger.

    Reads from the SQLite watch DB, computes honest R for each row,
    and inserts into the append-only ledger.
    """
    backfilled = 0
    try:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT * FROM suggested_trades_audit ORDER BY date DESC, ticker ASC"
            ).fetchall()

            for row in rows:
                t = dict(row)
                ticker = (t.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                side = (t.get("side") or "LONG").upper()
                status = (t.get("status") or "STALKING").upper()
                entry_price = float(t.get("entry_price") or 0.0)
                stop = float(t.get("tactical_stop") or 0.0)
                target_1 = float(t.get("target_1") or 0.0)
                last_price = float(t.get("last_price") or 0.0)
                dist_pct = float(t.get("distance_to_entry_pct") or 0.0)
                fees = float(t.get("fees", 0.0) or 0.0)
                slippage = float(t.get("slippage", 0.0) or 0.0)

                if status in ("TARGET_HIT", "COMPLETED"):
                    exit_price = target_1 if target_1 > 0 else last_price
                    exit_at = t.get("evaluated_at") or _now_iso()
                elif status in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
                    exit_price = stop if stop > 0 else last_price
                    exit_at = t.get("evaluated_at") or _now_iso()
                else:
                    exit_price = 0.0
                    exit_at = None

                r_scorer = score_21bar_r(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop=stop,
                    target_1=target_1,
                    side=side,
                    fees=fees,
                    slippage=slippage,
                )

                append_suggestion(
                    {
                        "ticker": ticker,
                        "date": t.get("date") or datetime.now().strftime("%Y-%m-%d"),
                        "side": side,
                        "verdict": status,
                        "triage_reason": f"status={status}",
                        "entry_zone_low": float(t.get("entry_zone_low") or 0.0),
                        "entry_zone_high": float(t.get("entry_zone_high") or 0.0),
                        "zone_midpoint": (float(t.get("entry_zone_low") or 0.0) + float(t.get("entry_zone_high") or 0.0)) / 2.0 if (t.get("entry_zone_low") or t.get("entry_zone_high")) else 0.0,
                        "stop": stop,
                        "target_1": target_1,
                        "target_2": float(t.get("target_2") or 0.0),
                        "breakout_level": float(t.get("breakout_level") or 0.0),
                        "options_structure": t.get("trade_structure", "NONE"),
                        "options_actionable": bool(t.get("is_primary", 0)),
                        "source": "audit_backfill",
                        "report_source": "legacy",
                        "taken": status in ("TARGET_HIT", "COMPLETED", "IN_TRADE", "IN_ZONE"),
                        "fill_price": entry_price if entry_price > 0 else last_price,
                        "exit_price": exit_price,
                        "exit_at": exit_at,
                        "exit_reason": t.get("outcome_notes", ""),
                        "realized_r": r_scorer["realized_r"],
                        "realized_pnl": r_scorer["realized_pnl"],
                        "fees": fees,
                        "slippage": slippage,
                    }
                )
                backfilled += 1

    except Exception as e:
        logger.error(f"[ledger] Backfill failed: {e}")

    logger.info(f"[ledger] Backfilled {backfilled} legacy audit rows.")
    return backfilled


def get_ledger_summary(ticker: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    """Query the ledger for summary stats and rows."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            where = ""
            params = []
            if ticker:
                where = "WHERE ticker = ?"
                params = [ticker.strip().upper()]

            total = cursor.execute(
                f"SELECT COUNT(*) as cnt FROM suggestions {where}", params
            ).fetchone()["cnt"]

            rows = cursor.execute(
                f"""
                SELECT * FROM suggestions {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                params + [limit],
            ).fetchall()

            resolved = [dict(r) for r in rows if r.get("exit_price") and r.get("exit_price") != 0.0]
            if resolved:
                avg_r = round(sum(r["realized_r"] for r in resolved) / len(resolved), 4)
                win_count = sum(1 for r in resolved if r["realized_r"] > 0)
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
