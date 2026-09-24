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
            if scored is True:
                sql += " AND scored_at IS NOT NULL AND scorer_version = ?"
                params.append(SCORER_VERSION)
            elif scored is False:
                sql += " AND (scored_at IS NULL OR scorer_version IS NULL OR scorer_version != ?)"
                params.append(SCORER_VERSION)
            sql += " ORDER BY date ASC, ticker ASC"
            rows = cursor.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


def score_suggestion(row: Dict[str, Any]) -> Dict[str, Any]:
    """Score a single suggestion row according to honest lifecycle validation."""
    # 6. Clear all outcome fields before scoring so a stale v1 row is never returned unchanged
    row = {
        **row,
        "fill_date": None,
        "fill_price": None,
        "exit_date": None,
        "exit_price": None,
        "exit_reason": None,
        "bars_held": 0,
        "gross_r": None,
        "r_net": None,
        "mae_r": None,
        "scored_at": None,
        "scorer_version": SCORER_VERSION,
    }

    ticker = row.get("ticker")
    date_str = row.get("date")
    side = (row.get("side") or "LONG").upper()
    entry_low = float(row.get("entry_low") or 0.0)
    entry_high = float(row.get("entry_high") or 0.0)
    stop = float(row.get("stop") or 0.0)
    target_1 = float(row.get("target_1") or 0.0)
    target_2 = float(row.get("target_2") or 0.0)
    breakout_level = float(row.get("breakout_level") or 0.0)
    entry_type = (row.get("entry_type") or "LIMIT").upper()

    if not ticker or not date_str:
        return {**row, "scored_at": _now_iso()}

    from src.tracking.execution_validator import get_bars_since_date

    # Fetch setup_date - 40 calendar days to warm EMA5 with at least 20 prior bars
    from datetime import datetime, timedelta
    try:
        d_obj = datetime.strptime(date_str[:10], "%Y-%m-%d")
        warm_start_date = (d_obj - timedelta(days=40)).strftime("%Y-%m-%d")
    except Exception:
        warm_start_date = date_str

    bars = get_bars_since_date(ticker, warm_start_date)
    if bars is None or bars.empty:
        logger.debug(f"No bars for {ticker} since {warm_start_date}; cannot score.")
        return row

    # Count bars strictly after the signal bar
    bars_after_signal = 0
    for idx in bars.index:
        if str(idx)[:10] > date_str:
            bars_after_signal += 1

    # 9. RSI2 detection by setup_lane == 'RSI2' only; pass opening_ceiling = min(stop + 2*ATR, stop/(1-0.05), target)
    setup_lane = row.get("setup_lane") or ""
    is_rsi2 = (setup_lane == "RSI2")
    holding_bars = 10 if is_rsi2 else MAX_HOLDING_BARS
    e_type = "NEXT_OPEN" if is_rsi2 else entry_type
    strat_id = "RSI2" if is_rsi2 else None

    opening_ceiling = None
    if is_rsi2:
        atr_val = float(row.get("atr_at_signal") or row.get("atr") or 0.0)
        ceil_candidates = []
        if stop > 0 and atr_val > 0:
            ceil_candidates.append(stop + 2.0 * atr_val)
        if stop > 0:
            ceil_candidates.append(stop / (1.0 - 0.05))
        if target_1 > 0:
            ceil_candidates.append(target_1)
        if ceil_candidates:
            opening_ceiling = min(ceil_candidates)

    eval_res = evaluate_setup_lifecycle_bars(
        bars=bars,
        setup_date=date_str,
        side=side,
        entry_type=e_type,
        strategy_id=strat_id,
        opening_ceiling=opening_ceiling,
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

    # 7. Unfilled: store validator's real status (INVALIDATED, MISSED_RUNAWAY, GAP_STOP, NOT_FILLED)
    if not was_filled:
        val_status = eval_res.get("status") or "NOT_FILLED"
        is_terminal = eval_res.get("is_terminal", False)
        if is_terminal or bars_after_signal >= 5:
            exit_d = eval_res.get("exit_date") or (str(bars.iloc[-1].name)[:10] if hasattr(bars.iloc[-1], "name") else date_str)
            return {
                **row,
                "fill_date": None,
                "fill_price": None,
                "exit_date": exit_d,
                "exit_price": None,
                "exit_reason": val_status,
                "bars_held": 0,
                "gross_r": None,
                "r_net": None,
                "mae_r": None,
                "scored_at": _now_iso(),
                "scorer_version": SCORER_VERSION,
            }
        else:
            # Fewer than 5 bars after signal and non-terminal; leave scored_at NULL so it can fill later
            return row

    # 8. Setup filled: breakout uses validator fill; limit uses fill or entry bound
    if entry_type == "BREAKOUT":
        fill_price = float(eval_res.get("fill_price") or breakout_level)
    else:
        fill_price = float(eval_res.get("fill_price") or (entry_high if side == "LONG" else entry_low))

    fill_date = eval_res.get("fill_date") or date_str

    # Risk = planned risk. Stop must be valid on the planned setup.
    if side == "LONG":
        risk = (entry_high - stop) if entry_high > 0 else (fill_price - stop)
        if risk <= 0:
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
        if risk <= 0:
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

    # Immature: if fewer than max holding bars have passed since fill and has not exited, leave scored_at NULL
    if not is_terminal and bars_held < holding_bars:
        return {
            **row,
            "fill_date": fill_date,
            "fill_price": fill_price,
            "bars_held": bars_held,
            "scored_at": None,
        }

    # Finalize exit
    exit_date = eval_res.get("exit_date") or (str(bars.iloc[-1].name)[:10] if hasattr(bars.iloc[-1], "name") else "")
    exit_price = eval_res.get("exit_price")
    if exit_price is None or exit_price <= 0:
        exit_price = float(bars.iloc[-1].get("Close", bars.iloc[-1].get("close", fill_price)))
    exit_reason = eval_res.get("exit_reason") or eval_res.get("status") or "MAX_HOLDING_EXPIRED"

    # Compute gross R, fee (Cost 0.0005*(fill+exit)/risk), and net R
    # Filled trade that gaps through the stop gets real R at the open
    if side == "LONG":
        gross_r = (exit_price - fill_price) / risk
    else:
        gross_r = (fill_price - exit_price) / risk

    cost_r = (0.0005 * (fill_price + exit_price)) / risk
    r_net = gross_r - cost_r

    # Compute MAE as worst adverse excursion between fill and exit divided by risk
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


def evaluate_all_suggestions(force: bool = False) -> Dict[str, Any]:
    """Score all unscored suggestions. If force=True, re-scores all suggestions."""
    rows = _load_suggestions(scored=None if force else False)
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
            rows = cursor.execute(
                """
                SELECT * FROM suggestions
                WHERE gate_status = 'PASS'
                  AND scorer_version = 2
                  AND kind = 'NEW'
                  AND verdict IN ('ENTER', 'STALK')
                  AND r_net IS NOT NULL
                """
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


def get_main_record_stats(min_date: str = "2026-09-23") -> Dict[str, Any]:
    """Compute stats for main record: source=judge, gate PASS, kind NEW, the 5 lanes, date >= min_date."""
    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT * FROM suggestions
                WHERE source = 'judge'
                  AND gate_status = 'PASS'
                  AND scorer_version = 2
                  AND kind = 'NEW'
                  AND verdict IN ('ENTER', 'STALK')
                  AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2')
                  AND date >= ?
                  AND r_net IS NOT NULL
                """,
                (min_date,),
            ).fetchall()
            if not rows:
                return {}
            r_vals = [float(r["r_net"]) for r in rows if r["r_net"] is not None]
            import statistics as _stats
            wins = sum(1 for r in r_vals if r > 0)
            losses = sum(1 for r in r_vals if r <= 0)

            # Breakdown by lane
            lanes_stats = {}
            for lane in ['RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2']:
                l_rows = [r for r in rows if r["setup_lane"] == lane]
                l_r_vals = [float(r["r_net"]) for r in l_rows if r["r_net"] is not None]
                l_wins = sum(1 for r in l_r_vals if r > 0)
                lanes_stats[lane] = {
                    "total": len(l_r_vals),
                    "wins": l_wins,
                    "losses": len(l_r_vals) - l_wins,
                    "win_rate": round(l_wins / len(l_r_vals) * 100, 1) if l_r_vals else 0.0,
                    "mean_r": round(_stats.mean(l_r_vals), 4) if l_r_vals else 0.0,
                    "sum_r": round(sum(l_r_vals), 4) if l_r_vals else 0.0,
                }

            return {
                "total_trades": len(r_vals),
                "wins": wins,
                "losses": losses,
                "mean_r": round(_stats.mean(r_vals), 4) if r_vals else None,
                "median_r": round(_stats.median(r_vals), 4) if r_vals else None,
                "win_rate": round(wins / len(r_vals) * 100, 1) if r_vals else 0.0,
                "sum_r": round(sum(r_vals), 4),
                "by_lane": lanes_stats,
            }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Re-score suggestions in DB")
    parser.add_argument("--force", action="store_true", help="Force re-score all suggestions")
    parser.add_argument("--date", type=str, default="2026-09-23", help="Min date for get_main_record_stats")
    args = parser.parse_args()

    print(f"Scoring suggestions (SCORER_VERSION={SCORER_VERSION}, force={args.force})...")
    res = evaluate_all_suggestions(force=args.force)
    print(f"Scored {res.get('scored')} records. Errors: {len(res.get('errors', []))}")
    if res.get("stats"):
        print("Scoring overall stats:", json.dumps(res["stats"], indent=2))

    print(f"\n--- Main Record Stats (date >= {args.date}) ---")
    main_stats = get_main_record_stats(min_date=args.date)
    if main_stats:
        print(json.dumps(main_stats, indent=2))
    else:
        print("No trades found matching criteria for main record stats.")
