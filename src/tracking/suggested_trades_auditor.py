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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger("suggested_trades_auditor")


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

                # Determine mid entry price
                if breakout_lvl > 0:
                    entry_price = breakout_lvl
                elif entry_low > 0 and entry_high > 0:
                    entry_price = round((entry_low + entry_high) / 2.0, 2)
                elif entry_low > 0:
                    entry_price = entry_low
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

                has_actionable_options = bool(
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

                # 1. Insert/Update Options Trade
                if has_actionable_options:
                    struct_clean = opt_struct.replace("_", " ").upper()
                    strike_label = f" ({long_strike:g}/{short_strike:g})" if (long_strike and short_strike) else ""
                    trade_label = f"{struct_clean}{strike_label}"

                    cursor.execute(
                        """
                        INSERT INTO suggested_trades_audit (
                            ticker, date, side, trade_type, trade_structure, trade_label,
                            entry_type, entry_price, entry_zone_low, entry_zone_high,
                            tactical_stop, target_1, target_2, options_expiration,
                            long_strike, short_strike, target_debit, max_profit, max_loss,
                            last_price, distance_to_entry_pct, status, is_primary, created_at
                        ) VALUES (
                            ?, ?, ?, 'OPTIONS', ?, ?,
                            'OPTIONS_ENTRY', ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(ticker, date, trade_type, trade_structure) DO UPDATE SET
                            side = excluded.side,
                            trade_label = excluded.trade_label,
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
                            ticker, date_val, side, opt_struct.upper(), trade_label,
                            entry_price, entry_low, entry_high,
                            tactical_stop, target_1, target_2, opt_exp,
                            long_strike, short_strike, target_debit, max_profit, max_loss,
                            last_price, dist_pct, status, opt_primary, _now_iso(),
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

                from src.tracking.execution_validator import evaluate_setup_lifecycle
                eval_res = evaluate_setup_lifecycle(
                    ticker=t["ticker"],
                    setup_date=t.get("date") or "",
                    side=side,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop_loss=stop,
                    target_1=t1,
                    target_2=float(t.get("target_2") or 0.0),
                    live_price=spot,
                    current_status=status,
                )
                status = eval_res["status"]
                if eval_res["was_filled"] and entry <= 0:
                    entry = eval_res["fill_price"]

                # Evaluate by status
                if status in ("TARGET_HIT", "COMPLETED"):
                    if is_options and max_prof > 0:
                        dollar_pnl = max_prof
                        roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                        notes = f"Target reached: theoretical max payoff ${max_prof:.2f} on {struct} (modeled)"
                    else:
                        # 100 shares hypothetical standard
                        gain_per_share = (t1 - entry) if side == "LONG" else (entry - t1)
                        dollar_pnl = round(gain_per_share * 100, 2)
                        roc_pct = round((gain_per_share / entry * 100), 2) if entry > 0 else 0.0
                        notes = f"Hypothetical 100 shs hit Target 1 (${t1:.2f}) vs fill ${entry:.2f} (+${dollar_pnl:.2f})"

                elif status in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
                    if is_options and max_loss > 0:
                        dollar_pnl = -max_loss
                        roc_pct = -100.0
                        notes = f"Stop breached: theoretical max loss -${max_loss:.2f} on {struct} (modeled)"
                    else:
                        loss_per_share = (stop - entry) if side == "LONG" else (entry - stop)
                        dollar_pnl = round(loss_per_share * 100, 2)
                        roc_pct = round((loss_per_share / entry * 100), 2) if entry > 0 else 0.0
                        notes = f"Hypothetical 100 shs stopped at ${stop:.2f} vs entry ${entry:.2f}"

                elif status in ("IN_TRADE", "IN_ZONE"):
                    if is_options:
                        if "PUT" in struct:
                            # Credit put spread
                            if spot >= short_k:
                                dollar_pnl = max_prof
                                roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif spot <= long_k:
                                dollar_pnl = -max_loss
                                roc_pct = -100.0
                            else:
                                ratio = (spot - long_k) / (short_k - long_k) if (short_k > long_k) else 0.5
                                dollar_pnl = round(max_prof * ratio - max_loss * (1 - ratio), 2)
                                roc_pct = round((dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                            notes = f"Modeled: Spot ${spot:.2f} vs short {short_k} / long {long_k} (unquoted option mark)"
                        else:
                            # Debit call spread
                            if spot >= short_k:
                                dollar_pnl = max_prof
                                roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif spot <= long_k:
                                dollar_pnl = -max_loss
                                roc_pct = -100.0
                            else:
                                spread_val = (spot - long_k) * 100
                                dollar_pnl = round(spread_val - (debit * 100), 2)
                                roc_pct = round((dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                            notes = f"Modeled: Spot ${spot:.2f} vs strikes {long_k}/{short_k} (unquoted option mark)"
                    else:
                        float_gain = (spot - entry) if side == "LONG" else (entry - spot)
                        dollar_pnl = round(float_gain * 100, 2)
                        roc_pct = round((float_gain / entry * 100), 2) if entry > 0 else 0.0
                        notes = f"Hypothetical 100 shs active at ${spot:.2f} vs fill ${entry:.2f} ({eval_res['unrealized_pnl_pct']:+.1f}%)"

                else:
                    # STALKING / MISSED
                    dollar_pnl = 0.0
                    roc_pct = 0.0
                    notes = f"Stalking: {dist_pct:+.1f}% from entry zone"

                # Update row in DB
                cursor.execute(
                    """
                    UPDATE suggested_trades_audit
                    SET status = ?, dollar_pnl = ?, roc_pct = ?, outcome_notes = ?, evaluated_at = ?
                    WHERE id = ?
                    """,
                    (status, dollar_pnl, roc_pct, notes, eval_time, row_id),
                )

            conn.commit()

    return get_audit_summary(tab="ALL", window=window)


def get_audit_summary(
    tab: str = "ALL",
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    window: int = 100,
    force_sync: bool = False,
) -> Dict[str, Any]:
    """
    Returns pre-computed summary metrics, tab counts, pagination, and trade audit records
    directly from SQLite. Runs instantaneously on demand.
    """
    if force_sync:
        sync_suggested_trades_from_watch_targets()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()

            # Base query: evaluate window on primary suggested trades
            window_clause = f"LIMIT {int(window)}" if window and window > 0 else ""
            
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

            # If empty, run initial evaluation
            if not all_trades:
                evaluate_all_suggested_trades(window=window)
                all_rows = cursor.execute(
                    f"""
                    SELECT * FROM suggested_trades_audit
                    WHERE is_primary = 1
                    ORDER BY date DESC, ticker ASC
                    {window_clause}
                    """
                ).fetchall()
                all_trades = [dict(r) for r in all_rows]

            # Calculate KPI Metrics over the Primary Window
            won_trades = [t for t in all_trades if t["status"] in ("TARGET_HIT", "COMPLETED")]
            lost_trades = [t for t in all_trades if t["status"] in ("INVALIDATED", "STOP_BREACHED", "STOPPED")]
            active_trades = [t for t in all_trades if t["status"] in ("IN_TRADE", "IN_ZONE")]
            stalking_trades = [t for t in all_trades if t["status"] not in ("TARGET_HIT", "COMPLETED", "INVALIDATED", "STOP_BREACHED", "STOPPED", "IN_TRADE", "IN_ZONE", "RECOVERY_EXIT")]

            won_count = len(won_trades)
            lost_count = len(lost_trades)
            active_count = len(active_trades)
            stalking_count = len(stalking_trades)
            resolved_count = won_count + lost_count

            won_dollars = sum(float(t["dollar_pnl"] or 0.0) for t in won_trades)
            lost_dollars = sum(float(t["dollar_pnl"] or 0.0) for t in lost_trades)
            floating_dollars = sum(float(t["dollar_pnl"] or 0.0) for t in active_trades)
            net_profit = round(won_dollars + lost_dollars + floating_dollars, 2)

            resolved_win_rate = round((won_count / resolved_count * 100.0), 1) if resolved_count > 0 else 0.0
            abs_lost = abs(lost_dollars)
            profit_factor = round(won_dollars / abs_lost, 2) if abs_lost > 0 else (99.0 if won_dollars > 0 else 0.0)
            avg_win = round(won_dollars / won_count, 2) if won_count > 0 else 0.0
            avg_loss = round(lost_dollars / lost_count, 2) if lost_count > 0 else 0.0

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
                "total_won": round(won_dollars, 2),
                "total_lost": round(lost_dollars, 2),
                "total_floating": round(floating_dollars, 2),
                "net_profit": net_profit,
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
            elif tab_upper in ("WON", "TARGET_HIT"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("TARGET_HIT", "COMPLETED")]
            elif tab_upper in ("STOPPED", "INVALIDATED"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("INVALIDATED", "STOP_BREACHED", "STOPPED")]
            elif tab_upper in ("ACTIVE", "IN_TRADE", "IN_ZONE"):
                filtered_trades = [t for t in filtered_trades if t["status"] in ("IN_TRADE", "IN_ZONE")]
            elif tab_upper == "STALKING":
                filtered_trades = [t for t in filtered_trades if t["status"] not in ("TARGET_HIT", "COMPLETED", "INVALIDATED", "STOP_BREACHED", "STOPPED", "IN_TRADE", "IN_ZONE")]

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

            return {
                "success": True,
                "summary": summary,
                "tab_counts": tab_counts,
                "pagination": pagination,
                "trades": paged_trades,
            }
