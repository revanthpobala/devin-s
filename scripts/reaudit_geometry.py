"""
scripts/reaudit_geometry.py
===========================
Idempotent legacy cleanup pass for suggested_trades_audit and watch_targets.

Iterates every watch target, runs the shared `check_geometry` gate, and
writes failures back as:
  - watch_targets.status = 'REJECTED_BY_GATE' (preserves the row for audit)
  - suggested_trades_audit rows for that ticker/date/trade_type/structure
      status = 'INVALID_GEOMETRY', r_multiple = NULL, is_primary = 0,
      outcome_notes = joined geometry reasons

Run on Windows, then POST /api/trades/audit/evaluate?window=0 to recompute
KPI buckets without legacy bad rows.

Safe to re-run: re-auditing an already-marked row is a no-op.
"""

import json
import logging
import time
from typing import Any, Dict, List

from src.tracking.watch_manager import _get_connection, _db_lock, init_watch_db
from src.tracking.suggested_trades_auditor import get_audit_summary
from src.logic.level_validation import check_geometry

logger = logging.getLogger(__name__)
_AUDIT_CACHE_TTL = 0

_INVALID_STATUSES = {"INVALID_GEOMETRY", "NO_QUOTE", "INCOME"}
_VALID_LANE_STATUSES = {"TARGET_HIT", "COMPLETED", "STOP_BREACHED", "STOPPED", "GAP_STOP", "NOT_FILLED", "IN_TRADE", "IN_ZONE", "RECOVERY_EXIT", "TIME_EXIT"}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_float(val: Any) -> float:
    try:
        return float(val or 0.0)
    except (ValueError, TypeError):
        return 0.0


def run() -> Dict[str, Any]:
    init_watch_db()
    scanned = 0
    invalid_targets = 0
    invalid_audit_rows = 0

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            targets = cursor.execute("SELECT * FROM watch_targets").fetchall()

            for row in targets:
                scanned += 1
                raw: Dict[str, Any] = {}
                try:
                    raw = json.loads(row["raw_json"] or "{}")
                except Exception:
                    pass

                sp = raw.get("shares_plan", {}) or {}
                side = (row["side"] or sp.get("side") or "LONG").upper()
                entry_type = str(row["entry_type"] or sp.get("entry_type") or "LIMIT").upper()
                entry_low = _clean_float(row["entry_zone_low"] or sp.get("entry_zone_low"))
                entry_high = _clean_float(row["entry_zone_high"] or sp.get("entry_zone_high"))
                breakout = _clean_float(row["breakout_level"] or sp.get("breakout_level"))
                stop = _clean_float(row["tactical_stop"] or sp.get("tactical_stop"))
                t1 = _clean_float(row["target_1"] or sp.get("target_1"))
                t2 = _clean_float(row["target_2"] or sp.get("target_2"))

                reasons = check_geometry(
                    side=side,
                    entry_type=entry_type,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    breakout_level=breakout,
                    stop=stop,
                    t1=t1,
                    t2=t2,
                )
                if reasons:
                    invalid_targets += 1
                    ticker = row["ticker"]
                    date = row["date"]
                    joined = "; ".join(reasons)

                    cursor.execute(
                        "UPDATE watch_targets SET status = ?, verdict = ?, updated_at = ? WHERE ticker = ?",
                        ("REJECTED_BY_GATE", "REJECTED_BY_GATE", _now_iso(), ticker),
                    )

                    cursor.execute(
                        """
                        UPDATE suggested_trades_audit
                        SET status = ?, r_multiple = NULL, outcome_notes = ?, evaluated_at = ?, is_primary = 0
                        WHERE ticker = ? AND date = ? AND is_primary = 1
                        """,
                        ("INVALID_GEOMETRY", joined, _now_iso(), ticker, date),
                    )
                    invalid_audit_rows += cursor.rowcount

                    cursor.execute(
                        """
                        UPDATE suggested_trades_audit
                        SET status = ?, r_multiple = NULL, outcome_notes = ?, evaluated_at = ?, is_primary = 0
                        WHERE ticker = ? AND date = ? AND status NOT IN ('TARGET_HIT', 'COMPLETED', 'STOP_BREACHED', 'STOPPED', 'GAP_STOP', 'NOT_FILLED', 'IN_TRADE', 'IN_ZONE', 'RECOVERY_EXIT', 'TIME_EXIT')
                        """,
                        ("INVALID_GEOMETRY", joined, _now_iso(), ticker, date),
                    )
                    invalid_audit_rows += cursor.rowcount

            conn.commit()

    return {
        "scanned": scanned,
        "invalid_targets": invalid_targets,
        "invalid_audit_rows": invalid_audit_rows,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    res = run()
    print(f"[reaudit_geometry] scanned={res['scanned']} invalid_targets={res['invalid_targets']} invalid_audit_rows={res['invalid_audit_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
