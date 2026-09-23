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

SCORER_VERSION = 2
MAX_HOLDING_BARS = 21
ROUND_TRIP_FEES_BPS = 10  # 10bps = 0.10%


def _load_suggestions(
    ticker: Optional[str] = None,
    source: Optional[str] = None,
    scored: bool = False,
) -> List[Dict[str, Any]]:
    from src.tracking.suggestions_ledger import ensure_suggestions_schema
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            ensure_suggestions_schema(conn)
            cursor = conn.cursor()

            # P3: Score only verdict IN ('ENTER', 'STALK'), complete levels, kind='NEW', gate_status='PASS'
            sql = """
                SELECT * FROM suggestions
                WHERE (verdict IS NULL OR verdict IN ('ENTER', 'STALK'))
                  AND (kind IS NULL OR kind = 'NEW')
                  AND (entry_low IS NOT NULL AND stop IS NOT NULL AND target_1 IS NOT NULL)
                  AND (gate_status IS NULL OR gate_status = 'PASS')
            """
            params: List[Any] = []
            if ticker:
                sql += " AND ticker = ?"
                params.append(ticker.upper())
            if source:
                sql += " AND source = ?"
                params.append(source)
            if scored:
                sql += " AND scored_at IS NOT NULL AND scorer_version = ?"
                params.append(SCORER_VERSION)
            else:
                sql += " AND (scored_at IS NULL OR scorer_version IS NULL OR scorer_version != ?)"
                params.append(SCORER_VERSION)
            sql += " ORDER BY date ASC, ticker ASC"
            rows = cursor.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


def score_suggestion(row: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single suggestion row according to honest lifecycle validation."""
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
        return {**row, "r_net": None, "mae_r": None, "scored_at": _now_iso(), "scorer_version": SCORER_VERSION}

    from src.tracking.execution_validator import get_bars_since_date

    bars = get_bars_since_date(ticker, date_str)
    if bars is None or bars.empty:
        logger.debug(f"No bars for {ticker} since {date_str}; cannot score.")
        return row

    # P2: RSI2 lane scored with its own rules (next-open entry, EMA5 recovery exit, 10-bar max)
    setup_lane = row.get("setup_lane") or ""
    is_rsi2 = (setup_lane == "RSI2") or (entry_type in ("NEXT_OPEN", "RSI2")) or (row.get("notes") or "").startswith("rsi2")
    holding_bars = 10 if is_rsi2 else MAX_HOLDING_BARS
    e_type = "NEXT_OPEN" if is_rsi2 else entry_type
    strat_id = "RSI2" if is_rsi2 else None

    # P2: no fake live bar; pass live_price=0.0
    eval_res = evaluate_setup_lifecycle_bars(
        bars=bars,
        setup_date=date_str,
        side=side,
        entry_type=e_type,
        strategy_id=strat_id,
        entry_low=entry_low,
        entry_high=entry_high,
        breakout_level=breakout_level,
        stop_loss=stop,
        target_1=target_1,
        target_2=target_2,
        live_price=0.0,
        max_holding_bars=holding_bars,
        skip_setup_bar=True,
    )

    was_filled = eval_res.get("was_filled", False)
    bars_count = len(bars)

    # 1. Unfilled: if setup never filled within 5 bars, mark NOT_FILLED with r_net=NULL
    if not was_filled:
        if bars_count >= 5:
            return {
                **row,
                "fill_date": None,
                "fill_price": None,
                "exit_date": str(bars.iloc[-1].name)[:10] if hasattr(bars.iloc[-1], "name") else None,
                "exit_price": None,
                "exit_reason": "NOT_FILLED",
                "bars_held": 0,
                "gross_r": None,
                "r_net": None,
                "mae_r": None,
                "scored_at": _now_iso(),
                "scorer_version": SCORER_VERSION,
            }
        else:
            # Fewer than 5 bars passed; leave scored_at NULL so it can fill later
            return row

    # 2. Setup filled: ignore your_fill; breakout uses validator gap fill
    if entry_type == "BREAKOUT":
        fill_price = float(eval_res.get("fill_price") or breakout_level)
    else:
        fill_price = float(eval_res.get("fill_price") or (entry_high if side == "LONG" else entry_low))

    fill_date = eval_res.get("fill_date") or date_str

    # Risk = planned entry_high - stop; stop >= fill means INVALID_GEOMETRY
    if side == "LONG":
        risk = (entry_high - stop) if entry_high > 0 else (fill_price - stop)
        if stop >= fill_price or risk <= 0:
            return {
                **row,
                "fill_date": fill_date,
                "fill_price": fill_price,
                "exit_reason": "INVALID_GEOMETRY",
                "gross_r": None,
                "r_net": None,
                "mae_r": None,
                "scored_at": _now_iso(),
                "scorer_version": SCORER_VERSION,
            }
    else:
        risk = (stop - entry_low) if entry_low > 0 else (stop - fill_price)
        if stop <= fill_price or risk <= 0:
            return {
                **row,
                "fill_date": fill_date,
                "fill_price": fill_price,
                "exit_reason": "INVALID_GEOMETRY",
                "gross_r": None,
                "r_net": None,
                "mae_r": None,
                "scored_at": _now_iso(),
                "scorer_version": SCORER_VERSION,
            }

    is_terminal = eval_res.get("is_terminal", False)
    bars_held = eval_res.get("bars_held", 0)

    # 3. Immature: if fewer than max holding bars have passed since fill and has not exited, leave scored_at NULL
    if not is_terminal and bars_held < holding_bars:
        return {
            **row,
            "fill_date": fill_date,
            "fill_price": fill_price,
            "bars_held": bars_held,
            "scored_at": None,
        }

    # 4. Finalize exit
    exit_date = eval_res.get("exit_date") or (str(bars.iloc[-1].name)[:10] if hasattr(bars.iloc[-1], "name") else "")
    exit_price = eval_res.get("exit_price")
    if exit_price is None or exit_price <= 0:
        exit_price = float(bars.iloc[-1].get("Close", bars.iloc[-1].get("close", fill_price)))
    exit_reason = eval_res.get("exit_reason") or eval_res.get("status") or "MAX_HOLDING_EXPIRED"

    # 5. Compute gross R, fee (Cost 0.0005*(fill+exit)/risk), and net R
    if side == "LONG":
        gross_r = (exit_price - fill_price) / risk
    else:
        gross_r = (fill_price - exit_price) / risk

    # Cost 0.0005*(fill+exit)/risk
    cost_r = (0.0005 * (fill_price + exit_price)) / risk
    r_net = gross_r - cost_r

    # 6. Compute MAE as worst adverse excursion between fill and exit divided by risk
    held_bars = bars
    if fill_date:
        held_bars = held_bars[held_bars.index >= fill_date]
    if exit_date:
        held_bars = held_bars[held_bars.index <= exit_date]
    if held_bars.empty:
        held_bars = bars

    low_col = "Low" if "Low" in held_bars.columns else "low"
    high_col = "High" if "High" in held_bars.columns else "high"

    if side == "LONG":
        worst_price = float(held_bars[low_col].min())
        adverse_excursion = max(0.0, fill_price - worst_price)
    else:
        worst_price = float(held_bars[high_col].max())
        adverse_excursion = max(0.0, worst_price - fill_price)

    mae_r = adverse_excursion / risk

    return {
        **row,
        "fill_date": fill_date,
        "fill_price": fill_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "gross_r": round(gross_r, 4),
        "r_net": round(r_net, 4),
        "mae_r": round(mae_r, 4),
        "scored_at": _now_iso(),
        "scorer_version": SCORER_VERSION,
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
            if scored.get("scored_at"):
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
            try:
                cursor.execute("ALTER TABLE suggestions ADD COLUMN scorer_version INTEGER DEFAULT 1")
            except Exception:
                pass
            if row.get("id"):
                cursor.execute(
                    """
                    UPDATE suggestions
                    SET fill_date=?, fill_price=?, exit_date=?, exit_price=?,
                        exit_reason=?, bars_held=?, gross_r=?, r_net=?, mae_r=?, scored_at=?, scorer_version=?
                    WHERE id=?
                    """,
                    (
                        row.get("fill_date"),
                        row.get("fill_price"),
                        row.get("exit_date"),
                        row.get("exit_price"),
                        row.get("exit_reason"),
                        row.get("bars_held"),
                        row.get("gross_r"),
                        row.get("r_net"),
                        row.get("mae_r"),
                        row.get("scored_at"),
                        row.get("scorer_version", SCORER_VERSION),
                        row["id"],
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE suggestions
                    SET fill_date=?, fill_price=?, exit_date=?, exit_price=?,
                        exit_reason=?, bars_held=?, gross_r=?, r_net=?, mae_r=?, scored_at=?, scorer_version=?
                    WHERE ticker=? AND date=? AND source=?
                    """,
                    (
                        row.get("fill_date"),
                        row.get("fill_price"),
                        row.get("exit_date"),
                        row.get("exit_price"),
                        row.get("exit_reason"),
                        row.get("bars_held"),
                        row.get("gross_r"),
                        row.get("r_net"),
                        row.get("mae_r"),
                        row.get("scored_at"),
                        row.get("scorer_version", SCORER_VERSION),
                        row["ticker"],
                        row["date"],
                        row["source"],
                    ),
                )
            conn.commit()


def _compute_stats() -> Dict[str, Any]:
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # P2: filter gate_status='PASS'
            rows = cursor.execute(
                "SELECT * FROM suggestions WHERE r_net IS NOT NULL AND exit_reason != 'NOT_FILLED' AND (gate_status = 'PASS' OR gate_status IS NULL)"
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
