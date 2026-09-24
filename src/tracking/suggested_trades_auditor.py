"""
Suggested Trades Auditor & Attribution Engine
============================================
Maintains a persistent, on-demand SQLite audit table (`suggested_trades_audit`)
tracking every trade recommendation emitted by the research pipeline
(both Options Spreads and Equity Shares).

Provides on-demand evaluation of win/loss metrics, dollar PnL, ROC %,
and historical attribution without running on-the-fly calculations on every request.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger("suggested_trades_auditor")

_AUDIT_CACHE: Dict[str, tuple[float, dict]] = {}
_AUDIT_CACHE_TTL = 30  # 30 seconds


def sync_suggested_trades_from_watch_targets() -> int:
    """
    Ingest and sync all suggested trade recommendations from `watch_targets`
    into the persistent `suggested_trades_audit` table.
    Determines primary suggested trade vehicle vs secondary alternative.
    Returns the number of synced trade recommendations.
    """
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            targets = cursor.execute("SELECT * FROM watch_targets").fetchall()

            synced_count = 0
            for row in targets:
                t = dict(row)
                ticker = (t.get("ticker") or "").strip().upper()
                if not ticker:
                    continue

                date_val = t.get("date") or datetime.now().strftime("%Y-%m-%d")
                side = (t.get("side") or "LONG").upper()
                last_price = float(t.get("last_price") or 0.0)
                dist_pct = float(t.get("distance_to_entry_pct") or 0.0)
                status = (t.get("status") or "STALKING").upper()

                raw = {}
                if t.get("raw_json"):
                    try:
                        raw = json.loads(t["raw_json"])
                    except Exception:
                        pass

                sp = raw.get("shares_plan", {})
                op = raw.get("options_plan", {})

                entry_low = float(t.get("entry_zone_low") or sp.get("entry_zone_low") or 0.0)
                entry_high = float(t.get("entry_zone_high") or sp.get("entry_zone_high") or 0.0)
                breakout_lvl = float(t.get("breakout_level") or sp.get("breakout_level") or 0.0)
                tactical_stop = float(t.get("tactical_stop") or sp.get("tactical_stop") or 0.0)
                target_1 = float(t.get("target_1") or sp.get("target_1") or 0.0)
                target_2 = float(t.get("target_2") or sp.get("target_2") or 0.0)

                # Determine mid entry price (honor entry_type; do not use breakout_lvl for LIMIT entries)
                entry_mode = str(t.get("entry_type") or sp.get("entry_type") or "LIMIT").upper()
                if entry_mode == "BREAKOUT" and breakout_lvl > 0:
                    entry_price = breakout_lvl
                elif entry_low > 0 and entry_high > 0:
                    entry_price = round((entry_low + entry_high) / 2.0, 2)
                elif entry_low > 0:
                    entry_price = entry_low
                elif breakout_lvl > 0:
                    entry_price = breakout_lvl
                else:
                    entry_price = last_price

                # Check for Suggested Options Trade
                opt_struct = (op.get("structure") or t.get("options_structure") or "").strip()
                max_profit = float(op.get("max_profit") or 0.0)
                max_loss = float(op.get("max_loss") or 0.0)
                target_debit = float(op.get("target_debit") or 0.0)
                long_strike = float(op.get("long_strike") or 0.0)
                short_strike = float(op.get("short_strike") or 0.0)
                opt_exp = str(op.get("expiration") or "")

                is_income = opt_struct.upper() in ("COVERED_CALL", "CASH_SECURED_PUT")
                has_actionable_options = not is_income and bool(
                    opt_struct
                    and opt_struct.upper() != "NONE"
                    and (max_profit > 0 or max_loss > 0 or op.get("actionable") is True)
                )

                # Check for Suggested Equity Shares Trade
                sh_entry_type = (sp.get("entry_type") or t.get("entry_type") or "LIMIT").upper()
                is_shares_actionable = bool(
                    sh_entry_type != "NO_ENTRY"
                    and (entry_low > 0 or breakout_lvl > 0 or tactical_stop > 0 or target_1 > 0)
                )

                # Determine Primary Vehicle
                if has_actionable_options and is_shares_actionable:
                    if op.get("actionable") is True or sh_entry_type == "NO_ENTRY":
                        opt_primary = 1
                        sh_primary = 0
                    elif op.get("actionable") is False:
                        opt_primary = 0
                        sh_primary = 1
                    elif 0 < last_price < 20:
                        opt_primary = 0
                        sh_primary = 1
                    else:
                        opt_primary = 1
                        sh_primary = 0
                elif has_actionable_options:
                    opt_primary = 1
                    sh_primary = 0
                elif is_shares_actionable:
                    opt_primary = 0
                    sh_primary = 1
                else:
                    opt_primary = 0
                    sh_primary = 0

                from src.logic.level_validation import check_geometry
                geo_reasons = check_geometry(
                    side=side,
                    entry_type=entry_mode,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    breakout_level=breakout_lvl,
                    stop=tactical_stop,
                    t1=target_1,
                    t2=target_2,
                )
                if geo_reasons:
                    status = "INVALID_GEOMETRY"
                    opt_primary = 0
                    sh_primary = 0

                # 1. Insert/Update Options / Income Trade
                if is_income or has_actionable_options:
                    struct_clean = opt_struct.replace("_", " ").upper()
                    strike_label = f" ({long_strike:g}/{short_strike:g})" if (long_strike and short_strike) else ""
                    trade_label = f"{struct_clean}{strike_label}"
                    trade_type_val = "INCOME" if is_income else "OPTIONS"
                    entry_type_val = "INCOME_ENTRY" if is_income else "OPTIONS_ENTRY"
                    status_val = "INCOME" if is_income else status

                    cursor.execute(
                        """
                        INSERT INTO suggested_trades_audit (
                            ticker, date, side, trade_type, trade_structure, trade_label,
                            entry_type, entry_price, entry_zone_low, entry_zone_high,
                            tactical_stop, target_1, target_2, options_expiration,
                            long_strike, short_strike, target_debit, max_profit, max_loss,
                            last_price, distance_to_entry_pct, status, is_primary, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(ticker, date, trade_type, trade_structure) DO UPDATE SET
                            side = excluded.side,
                            trade_label = excluded.trade_label,
                            entry_type = excluded.entry_type,
                            entry_price = excluded.entry_price,
                            entry_zone_low = excluded.entry_zone_low,
                            entry_zone_high = excluded.entry_zone_high,
                            tactical_stop = excluded.tactical_stop,
                            target_1 = excluded.target_1,
                            target_2 = excluded.target_2,
                            options_expiration = excluded.options_expiration,
                            long_strike = excluded.long_strike,
                            short_strike = excluded.short_strike,
                            target_debit = excluded.target_debit,
                            max_profit = excluded.max_profit,
                            max_loss = excluded.max_loss,
                            last_price = excluded.last_price,
                            distance_to_entry_pct = excluded.distance_to_entry_pct,
                            status = excluded.status,
                            is_primary = excluded.is_primary
                        """,
                        (
                            ticker, date_val, side, trade_type_val, opt_struct.upper(), trade_label,
                            entry_type_val, entry_price, entry_low, entry_high,
                            tactical_stop, target_1, target_2, opt_exp,
                            long_strike, short_strike, target_debit, max_profit, max_loss,
                            last_price, dist_pct, status_val, opt_primary, _now_iso(),
                        ),
                    )
                    synced_count += 1

                # 2. Insert/Update Equity Shares Trade
                if is_shares_actionable:
                    sh_label = f"EQUITY SHARES ({sh_entry_type.replace('_', ' ')})"
                    cursor.execute(
                        """
                        INSERT INTO suggested_trades_audit (
                            ticker, date, side, trade_type, trade_structure, trade_label,
                            entry_type, entry_price, entry_zone_low, entry_zone_high,
                            tactical_stop, target_1, target_2, options_expiration,
                            long_strike, short_strike, target_debit, max_profit, max_loss,
                            last_price, distance_to_entry_pct, status, is_primary, created_at
                        ) VALUES (
                            ?, ?, ?, 'EQUITY', 'SHARES', ?,
                            ?, ?, ?, ?,
                            ?, ?, ?, NULL,
                            NULL, NULL, NULL, NULL, NULL,
                            ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(ticker, date, trade_type, trade_structure) DO UPDATE SET
                            side = excluded.side,
                            trade_label = excluded.trade_label,
                            entry_type = excluded.entry_type,
                            entry_price = excluded.entry_price,
                            entry_zone_low = excluded.entry_zone_low,
                            entry_zone_high = excluded.entry_zone_high,
                            tactical_stop = excluded.tactical_stop,
                            target_1 = excluded.target_1,
                            target_2 = excluded.target_2,
                            last_price = excluded.last_price,
                            distance_to_entry_pct = excluded.distance_to_entry_pct,
                            status = excluded.status,
                            is_primary = excluded.is_primary
                        """,
                        (
                            ticker, date_val, side, sh_label,
                            sh_entry_type, entry_price, entry_low, entry_high,
                            tactical_stop, target_1, target_2,
                            last_price, dist_pct, status, sh_primary, _now_iso(),
                        ),
                    )
                    synced_count += 1

            conn.commit()
            return synced_count


def evaluate_all_suggested_trades(refresh_quotes: bool = False, window: int = 100) -> Dict[str, Any]:
    """
    On-demand calculation and persistent DB update for all suggested trades.
    Computes exact dollar PnL, ROC %, and outcome notes for each trade,
    persisting results to SQLite.
    """
    # 1. Sync from watch targets
    sync_suggested_trades_from_watch_targets()

    # Also evaluate append-only suggestions ledger
    try:
        from src.tracking.suggestion_scorer import evaluate_all_suggestions
        evaluate_all_suggestions()
    except Exception as e_sugg:
        logger.debug(f"evaluate_all_suggestions notice: {e_sugg}")

    eval_time = _now_iso()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT * FROM suggested_trades_audit ORDER BY date DESC, ticker ASC").fetchall()

            for r in rows:
                t = dict(r)
                row_id = t["id"]
                trade_type = (t.get("trade_type") or "EQUITY").upper()
                struct = (t.get("trade_structure") or "SHARES").upper()
                side = (t.get("side") or "LONG").upper()
                status = (t.get("status") or "STALKING").upper()
                if status == "INVALID_GEOMETRY":
                    continue
                spot = float(t.get("last_price") or 0.0)

                entry = float(t.get("entry_price") or 0.0)
                entry_low = float(t.get("entry_zone_low") or 0.0)
                entry_high = float(t.get("entry_zone_high") or 0.0)
                stop = float(t.get("tactical_stop") or 0.0)
                t1 = float(t.get("target_1") or 0.0)
                dist_pct = float(t.get("distance_to_entry_pct") or 0.0)

                max_prof = float(t.get("max_profit") or 0.0)
                max_loss = float(t.get("max_loss") or 0.0)
                long_k = float(t.get("long_strike") or 0.0)
                short_k = float(t.get("short_strike") or 0.0)
                debit = float(t.get("target_debit") or 0.0)

                dollar_pnl = 0.0
                roc_pct = 0.0
                notes = ""

                is_options = (trade_type == "OPTIONS")
                is_income = (trade_type == "INCOME")

                from src.tracking.execution_validator import evaluate_setup_lifecycle
                e_type = str(t.get("entry_type") or "LIMIT").upper()
                bo_lvl = float(t.get("breakout_level") or 0.0)
                eval_res = evaluate_setup_lifecycle(
                    ticker=t["ticker"],
                    setup_date=t.get("date") or "",
                    side=side,
                    entry_type=e_type,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    breakout_level=bo_lvl,
                    stop_loss=stop,
                    target_1=t1,
                    target_2=float(t.get("target_2") or 0.0),
                    live_price=spot,
                    current_status=status,
                    max_holding_bars=21,
                    skip_setup_bar=True,
                )
                status = eval_res["status"]
                if eval_res["was_filled"] and entry <= 0:
                    entry = eval_res["fill_price"]

                # Evaluate by status with honest R-multiples
                r_mult: Optional[float] = None
                notes = ""

                fill_val = float(eval_res.get("fill_price") or entry or 0.0)
                if fill_val > 0 and stop > 0:
                    risk_amt = (fill_val - stop) if side == "LONG" else (stop - fill_val)
                    if risk_amt <= 0:
                        risk_amt = None
                else:
                    risk_amt = None

                was_filled = bool(eval_res.get("was_filled"))
                exit_px = eval_res.get("exit_price")
                if exit_px is None or exit_px <= 0:
                    exit_px = t1 if status in ("TARGET_HIT", "COMPLETED") else stop

                if is_income:
                    r_mult = None
                    notes = "Income structure — not R-scored"
                    dollar_pnl = 0.0
                elif was_filled:
                    if risk_amt is not None and risk_amt > 0:
                        if status in ("TARGET_HIT", "COMPLETED", "STOP_BREACHED", "STOPPED", "GAP_STOP", "TIME_EXIT", "RECOVERY_EXIT"):
                            gain = (exit_px - fill_val) if side == "LONG" else (fill_val - exit_px)
                            r_mult = round(gain / risk_amt, 2)
                            notes = f"{status}: {r_mult:+.2f}R at ${exit_px:.2f} vs fill ${fill_val:.2f}"
                        elif status in ("IN_TRADE", "IN_ZONE"):
                            gain = (spot - fill_val) if side == "LONG" else (fill_val - spot)
                            r_mult = round(gain / risk_amt, 2)
                            notes = f"Active trade: {r_mult:+.2f}R at ${spot:.2f} vs fill ${fill_val:.2f}"
                        else:
                            r_mult = None
                            notes = f"{status}: in trade"

                        if r_mult is not None:
                            if is_options:
                                clamped = max(-max_loss, min(max_prof, r_mult * max_loss if max_loss > 0 else 0.0))
                                dollar_pnl = clamped
                                notes += " (est)"
                            else:
                                dollar_pnl = r_mult * risk_amt * 100.0
                    else:
                        r_mult = None
                        notes = f"{status} with invalid geometry (risk <= 0)"
                else:
                    r_mult = None
                    if status in ("INVALIDATED", "GAP_STOP", "NOT_FILLED", "MISSED_RUNAWAY"):
                        notes = f"{status} before fill: no R (never filled)"
                    else:
                        notes = f"Stalking: {dist_pct:+.1f}% from entry zone"

                if spot <= 0 and status not in ("TARGET_HIT", "COMPLETED", "STOP_BREACHED", "STOPPED", "TIME_EXIT", "RECOVERY_EXIT", "INCOME", "NO_QUOTE"):
                    status = "NO_QUOTE"
                    notes = f"{status}: no live quote available"
                    r_mult = None

                # Update row in DB
                cursor.execute(
                    """
                    UPDATE suggested_trades_audit
                    SET status = ?, r_multiple = ?, outcome_notes = ?, evaluated_at = ?, dollar_pnl = ?
                    WHERE id = ?
                    """,
                    (status, r_mult, notes, eval_time, dollar_pnl, row_id),
                )

            conn.commit()

    return get_audit_summary(tab="ALL", window=window, _from_evaluate=True)


def get_audit_summary(
    tab: str = "ALL",
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    window: int = 100,
    force_sync: bool = False,
    _from_evaluate: bool = False,
) -> Dict[str, Any]:
    """
    Returns pre-computed summary metrics, tab counts, pagination, and trade audit records
    directly from SQLite. Runs instantaneously on demand.
    """
    if force_sync:
        sync_suggested_trades_from_watch_targets()
        _AUDIT_CACHE.clear()

    cache_key = f"{tab}:{search or ''}:{page}:{page_size}:{window}"
    now = time.time()
    if cache_key in _AUDIT_CACHE:
        cached_ts, cached_result = _AUDIT_CACHE[cache_key]
        if now - cached_ts < _AUDIT_CACHE_TTL:
            return cached_result

    has_trades = False
    with _db_lock:
        with _get_connection() as conn:
            cnt = conn.cursor().execute("SELECT COUNT(*) FROM suggested_trades_audit WHERE is_primary = 1").fetchone()[0]
            has_trades = (cnt > 0)

    if not has_trades and not _from_evaluate:
        evaluate_all_suggested_trades(window=window)

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()

            # Base query: evaluate window on primary suggested trades
            try:
                w_val = int(window) if window is not None else 100
            except Exception:
                w_val = 100
            window_clause = f"LIMIT {w_val}" if w_val > 0 else ""
            
            # Fetch all rows within window (primary suggested trades for metrics)
            all_rows = cursor.execute(
                f"""
                SELECT * FROM suggested_trades_audit
                WHERE is_primary = 1
                ORDER BY date DESC, ticker ASC
                {window_clause}
                """
            ).fetchall()

            all_trades: List[Dict[str, Any]] = [dict(r) for r in all_rows]

            valid_statuses = {"TARGET_HIT", "COMPLETED", "STOP_BREACHED", "STOPPED", "GAP_STOP", "NOT_FILLED", "IN_TRADE", "IN_ZONE", "RECOVERY_EXIT", "TIME_EXIT", "STALKING", "MISSED_RUNAWAY", "INVALIDATED"}
            kpi_trades = [t for t in all_trades if t.get("status") in valid_statuses and t.get("trade_type") != "INCOME"]

            won_trades = [t for t in kpi_trades if t["status"] in ("TARGET_HIT", "COMPLETED")]
            lost_trades = [t for t in kpi_trades if t["status"] in ("STOP_BREACHED", "STOPPED") or (t["status"] in ("INVALIDATED", "GAP_STOP") and t.get("r_multiple") is not None and float(t.get("r_multiple") or 0.0) < 0)]
            active_trades = [t for t in kpi_trades if t["status"] in ("IN_TRADE", "IN_ZONE")]
            stalking_trades = [t for t in kpi_trades if t["status"] in ("STALKING", "MISSED_RUNAWAY", "NOT_FILLED") or (t["status"] == "INVALIDATED" and t.get("r_multiple") is None)]

            invalid_trades = [t for t in all_trades if t.get("status") in ("INVALID_GEOMETRY", "NO_QUOTE")]
            invalid_count = len(invalid_trades)

            won_count = len(won_trades)
            lost_count = len(lost_trades)
            active_count = len(active_trades)
            stalking_count = len(stalking_trades)
            resolved_count = won_count + lost_count

            won_r = sum(float(t["r_multiple"] or 0.0) for t in won_trades)
            lost_r = sum(float(t["r_multiple"] or 0.0) for t in lost_trades)
            floating_r = sum(float(t["r_multiple"] or 0.0) for t in active_trades)
            net_r = round(won_r + lost_r + floating_r, 2)

            net_dollar = round(sum(float(t.get("dollar_pnl") or 0.0) for t in kpi_trades), 2)

            resolved_win_rate = round((won_count / resolved_count * 100.0), 1) if resolved_count > 0 else 0.0
            abs_lost = abs(lost_r)
            profit_factor = round(won_r / abs_lost, 2) if abs_lost > 0 else (99.0 if won_r > 0 else 0.0)
            avg_win = round(won_r / won_count, 2) if won_count > 0 else 0.0
            avg_loss = round(lost_r / lost_count, 2) if lost_count > 0 else 0.0

            latest_eval = all_trades[0].get("evaluated_at") or _now_iso() if all_trades else _now_iso()

            summary = {
                "window": window,
                "total_trades": len(all_trades),
                "won_count": won_count,
                "lost_count": lost_count,
                "resolved_count": resolved_count,
                "active_count": active_count,
                "actionable_count": active_count,
                "stalking_count": stalking_count,
                "invalid_count": invalid_count,
                "total_won": round(won_r, 2),
                "total_lost": round(lost_r, 2),
                "total_floating": round(floating_r, 2),
                "net_profit": net_r,
                "net_dollar": net_dollar,
                "resolved_win_rate": resolved_win_rate,
                "win_rate_pct": resolved_win_rate,
                "profit_factor": profit_factor,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "last_evaluated_at": latest_eval,
            }

            # Calculate Tab Counts across ALL or Window
            tab_counts = {
                "all": len(all_trades),
                "options": sum(1 for t in all_trades if t.get("trade_type") == "OPTIONS"),
                "shares": sum(1 for t in all_trades if t.get("trade_type") == "EQUITY"),
                "income": sum(1 for t in all_trades if t.get("trade_type") == "INCOME"),
                "invalid": invalid_count,
                "won": won_count,
                "stopped": lost_count,
                "active": active_count,
                "stalking": stalking_count,
            }

            # Filter rows by tab & search query for pagination table
            filtered_trades = all_trades

            tab_upper = (tab or "ALL").upper()
            if tab_upper == "OPTIONS":
                filtered_trades = [t for t in filtered_trades if t.get("trade_type") == "OPTIONS"]
            elif tab_upper in ("SHARES", "EQUITY"):
                filtered_trades = [t for t in filtered_trades if t.get("trade_type") == "EQUITY"]
            elif tab_upper == "INCOME":
                filtered_trades = [t for t in filtered_trades if t.get("trade_type") == "INCOME"]
            elif tab_upper in ("INVALID", "INVALID_GEOMETRY"):
                filtered_trades = [t for t in filtered_trades if t.get("status") in ("INVALID_GEOMETRY", "NO_QUOTE")]
            elif tab_upper in ("WON", "TARGET_HIT"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("TARGET_HIT", "COMPLETED")]
            elif tab_upper in ("STOPPED", "INVALIDATED"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("INVALIDATED", "STOP_BREACHED", "STOPPED")]
            elif tab_upper in ("ACTIVE", "IN_TRADE", "IN_ZONE"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("IN_TRADE", "IN_ZONE")]
            elif tab_upper == "STALKING":
                filtered_trades = [t for t in filtered_trades if t["status"] not in ("TARGET_HIT", "COMPLETED", "STOP_BREACHED", "STOPPED", "IN_TRADE", "IN_ZONE", "RECOVERY_EXIT", "TIME_EXIT")]

            if search and search.strip():
                sq = search.strip().upper()
                filtered_trades = [
                    t for t in filtered_trades
                    if sq in (t.get("ticker") or "").upper()
                    or sq in (t.get("date") or "").upper()
                    or sq in (t.get("trade_label") or "").upper()
                    or sq in (t.get("outcome_notes") or "").upper()
                    or sq in (t.get("status") or "").upper()
                ]

            total_items = len(filtered_trades)
            page_size = max(1, min(page_size, 200))
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))

            start_idx = (page - 1) * page_size
            end_idx = min(start_idx + page_size, total_items)
            paged_trades = filtered_trades[start_idx:end_idx]

            pagination = {
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_items": total_items,
                "start_index": start_idx + 1 if total_items > 0 else 0,
                "end_index": end_idx,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "window": window,
            }

            result = {
                "success": True,
                "summary": summary,
                "tab_counts": tab_counts,
                "pagination": pagination,
                "trades": paged_trades,
            }
            _AUDIT_CACHE[cache_key] = (time.time(), result)
            return result
