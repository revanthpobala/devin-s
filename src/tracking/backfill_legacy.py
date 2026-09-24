"""Backfill legacy suggested_trades_audit rows into the suggestions table as source=legacy."""

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone

from typing import Any, Dict

from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger(__name__)


def _row_hash(ticker: str, date: str, trade_type: str, trade_structure: str) -> str:
    raw = f"{ticker}:{date}:{trade_type}:{trade_structure}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def backfill_legacy_suggestions() -> Dict[str, Any]:
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            audit_rows = cursor.execute(
                "SELECT * FROM suggested_trades_audit ORDER BY date ASC, ticker ASC"
            ).fetchall()

            inserted = 0
            skipped = 0
            errors = []

            for row in audit_rows:
                t = dict(row)
                ticker = (t.get("ticker") or "").strip().upper()
                date_val = t.get("date") or ""
                trade_type = (t.get("trade_type") or "").upper()
                trade_structure = t.get("trade_structure") or ""

                if not ticker or not date_val:
                    continue

                # P3: No options rows
                if trade_type == "OPTIONS":
                    continue

                is_modeled = 0
                report_hash = _row_hash(
                    ticker, date_val, trade_type, trade_structure
                )

                entry_type = t.get("entry_type") or "LIMIT"
                entry_low = t.get("entry_zone_low") or None
                entry_high = t.get("entry_zone_high") or None
                breakout_level = t.get("entry_price") or None
                stop = t.get("tactical_stop") or None
                target_1 = t.get("target_1") or None
                target_2 = t.get("target_2") or None
                side = (t.get("side") or "LONG").upper()
                your_fill = None

                try:
                    cursor.execute(
                        """
                        INSERT INTO suggestions (
                            ticker, date, source, report_hash, side, entry_type,
                            entry_low, entry_high, breakout_level, stop,
                            target_1, target_2, planned_rr, atr_at_signal,
                            taken, your_fill, notes, is_modeled, gate_status,
                            kind, verdict, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(ticker, date, source) DO NOTHING
                        """,
                        (
                            ticker,
                            date_val,
                            "legacy",
                            report_hash,
                            side,
                            entry_type,
                            entry_low,
                            entry_high,
                            breakout_level,
                            stop,
                            target_1,
                            target_2,
                            None,
                            None,
                            0,
                            None,
                            f"Backfill from suggested_trades_audit: {trade_type} {trade_structure}",
                            is_modeled,
                            "PASS",
                            "NEW",
                            "STALK",
                            _now_iso(),
                        ),
                    )
                    if cursor.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors.append(f"{ticker}/{date_val}: {e}")
                    logger.warning(f"Backfill failed for {ticker}/{date_val}: {e}")

            conn.commit()

            return {
                "inserted": inserted,
                "skipped": skipped,
                "errors": errors,
                "total_audit_rows": len(audit_rows),
            }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import json
    result = backfill_legacy_suggestions()
    print(json.dumps(result, indent=2, default=str))
