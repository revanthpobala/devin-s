"""
Watchlist targets, price polling, trigger alerts, and Tastytrade cloud quote alert endpoints.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import config
from src.ui.state import append_log, get_db

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["watchlist"])


class CreateAlertRequest(BaseModel):
    symbol: str
    threshold: float
    operator: str = "<"
    expires_at: Optional[str] = None


class ModifyAlertRequest(BaseModel):
    alert_id: str
    symbol: str
    threshold: float
    operator: str = "<"
    expires_at: Optional[str] = None


class SyncTickerAlertsRequest(BaseModel):
    ticker: str
    date: Optional[str] = None


@router.get("/api/watch-targets")
def get_watch_targets():
    """Fetch active stalking targets and enrich with tactical trade ideas and real-time Schwab quotes."""
    try:
        from src.logic.trade_ideas_generator import generate_trade_ideas_for_target

        rep_root = config.BASE_DIR / "reports"
        raw_root = config.BASE_DIR / "data" / "raw"

        with get_db() as conn:
            c = conn.cursor()
            rows = c.execute("SELECT * FROM watch_targets").fetchall()
            targets = []

            for r in rows:
                item = dict(r)
                if item.get("raw_json"):
                    try:
                        item["parsed_json"] = json.loads(item["raw_json"])
                    except Exception:
                        item["parsed_json"] = {}

                sym = item.get("ticker", "").upper()
                d_str = item.get("date", "")
                target_mtime = None

                rep_dir = rep_root / d_str
                for cand_name in [f"{sym}_arbitration.md", f"{sym}_summary.md", f"{sym}_independent.md"]:
                    cand = rep_dir / cand_name
                    if cand.exists():
                        target_mtime = datetime.fromtimestamp(cand.stat().st_mtime).isoformat()
                        break

                if not target_mtime and raw_root.exists():
                    raw_dir = raw_root / d_str / sym
                    for cand_name in [f"{sym}_arbitration.md", f"{sym}_independent_thesis.md", f"{sym}_gemini_thesis.md"]:
                        cand = raw_dir / cand_name
                        if cand.exists():
                            target_mtime = datetime.fromtimestamp(cand.stat().st_mtime).isoformat()
                            break

                if not target_mtime:
                    continue

                item["research_timestamp"] = target_mtime
                item["is_open_position"] = False
                item["trade_ideas"] = generate_trade_ideas_for_target(item)
                targets.append(item)

            all_symbols = [t.get("ticker", "").upper() for t in targets if t.get("ticker")]
            try:
                from src.clients.schwab_client import get_realtime_quotes_batch
                schwab_quotes = get_realtime_quotes_batch(all_symbols)
                for item in targets:
                    sym = item.get("ticker", "").upper()
                    q = schwab_quotes.get(sym)
                    if q and q.get("last_price"):
                        live_px = float(q["last_price"])
                        item["last_price"] = live_px
                        item["net_change"] = q.get("net_change", 0.0)
                        item["net_percent_change"] = q.get("net_percent_change", 0.0)
                        item["quote_source"] = "SCHWAB"

                        entry_low = item.get("entry_zone_low")
                        entry_high = item.get("entry_zone_high")
                        side = str(item.get("side") or "LONG").upper()
                        inv_price = item.get("invalidation_price")
                        inv_cond = str(item.get("invalidation_condition") or "DAILY_CLOSE_BELOW").upper()
                        target_1 = item.get("target_1")
                        target_2 = item.get("target_2")
                        old_status = str(item.get("status") or "STALKING").upper()

                        from src.tracking.execution_validator import evaluate_setup_lifecycle

                        session_low = float(q.get("low") or 0.0)
                        session_high = float(q.get("high") or 0.0)
                        item["session_low"] = session_low
                        item["session_high"] = session_high

                        entry_low = float(item.get("entry_zone_low") or 0.0)
                        entry_high = float(item.get("entry_zone_high") or 0.0)
                        breakout_lvl = float(item.get("breakout_level") or 0.0)
                        side = str(item.get("side") or "LONG").upper()
                        inv_price = float(item.get("invalidation_price") or item.get("tactical_stop") or 0.0)
                        target_1 = float(item.get("target_1") or 0.0)
                        target_2 = float(item.get("target_2") or 0.0)
                        old_status = str(item.get("status") or "STALKING").upper()
                        setup_date = str(item.get("date") or "")

                        # Calculate distance to entry zone %
                        dist_pct = 0.0
                        if entry_high > 0 and side == "LONG":
                            if entry_low <= live_px <= entry_high:
                                dist_pct = 0.0
                            elif live_px > entry_high:
                                dist_pct = round(((live_px - entry_high) / entry_high) * 100, 2)
                            else:
                                dist_pct = round(((live_px - entry_low) / entry_low) * 100, 2)
                        elif entry_low > 0 and side == "SHORT":
                            if entry_low <= live_px <= entry_high:
                                dist_pct = 0.0
                            elif live_px < entry_low:
                                dist_pct = round(((entry_low - live_px) / entry_low) * 100, 2)
                            else:
                                dist_pct = round(((entry_high - live_px) / entry_high) * 100, 2)

                        item["distance_to_entry_pct"] = dist_pct

                        # Deterministic Bar-Based Lifecycle Evaluation
                        eval_res = evaluate_setup_lifecycle(
                            ticker=sym,
                            setup_date=setup_date,
                            side=side,
                            entry_type=item.get("entry_type", "LIMIT"),
                            entry_low=entry_low,
                            entry_high=entry_high,
                            breakout_level=breakout_lvl,
                            stop_loss=inv_price,
                            target_1=target_1,
                            target_2=target_2,
                            live_price=live_px,
                            session_low=session_low,
                            session_high=session_high,
                            current_status=old_status,
                            proximity_tolerance_pct=0.5,
                        )

                        item["status"] = eval_res["status"]
                        item["was_filled"] = eval_res["was_filled"]
                        item["fill_price"] = eval_res["fill_price"]
                        item["unrealized_pnl_pct"] = eval_res["unrealized_pnl_pct"]
                        item["reclaimed"] = (old_status in ("INVALIDATED", "STOP_BREACHED") and eval_res["status"] in ("IN_TRADE", "IN_ZONE"))
            except Exception as q_err:
                logger.debug(f"Error enriching watch targets with Schwab quotes: {q_err}")

            # Calculate Suggested Trade P&L, ROC %, and performance summary
            won_dollars = []
            lost_dollars = []
            active_dollars = []
            actionable_count = 0

            for item in targets:
                entry_low = item.get("entry_zone_low")
                entry_high = item.get("entry_zone_high")
                side = str(item.get("side") or "LONG").upper()
                tactical_stop = item.get("tactical_stop") or item.get("invalidation_price")
                target_1 = item.get("target_1")
                target_2 = item.get("target_2")
                live_px = item.get("last_price")
                status = (item.get("status") or "STALKING").upper()
                dist_pct = item.get("distance_to_entry_pct")
                opt_act = bool(item.get("options_actionable"))

                raw = item.get("parsed_json") or {}
                op = raw.get("options_plan") or {}
                sp = raw.get("shares_plan") or {}

                struct = op.get("structure") or item.get("options_structure") or "NONE"
                max_prof = float(op.get("max_profit") or 0.0)
                max_loss = float(op.get("max_loss") or 0.0)
                long_k = float(op.get("long_strike") or 0.0)
                short_k = float(op.get("short_strike") or 0.0)
                debit = float(op.get("target_debit") or 0.0)
                is_options = bool(struct and struct != "NONE" and (max_prof > 0 or max_loss > 0))

                trade_type = "OPTIONS" if is_options else "SHARES"
                trade_label = struct.replace("_", " ").title() if is_options else f"Shares ({sp.get('entry_type', 'Limit')})"

                entry_mid = None
                if entry_low is not None and entry_high is not None and (entry_low > 0 or entry_high > 0):
                    entry_mid = round((entry_low + entry_high) / 2.0, 2)
                elif entry_low and entry_low > 0:
                    entry_mid = entry_low
                elif entry_high and entry_high > 0:
                    entry_mid = entry_high
                item["entry_midpoint"] = entry_mid

                # 1. Underlying Stock Price Delta %
                pnl_pct = None
                if entry_mid and live_px and entry_mid > 0:
                    if side == "SHORT":
                        pnl_pct = round(((entry_mid - live_px) / entry_mid) * 100.0, 2)
                    else:
                        pnl_pct = round(((live_px - entry_mid) / entry_mid) * 100.0, 2)
                item["pnl_pct"] = pnl_pct

                # 2. SUGGESTED TRADE DOLLAR P&L & ROC %
                trade_dollar_pnl = 0.0
                trade_roc_pct = 0.0

                if status in ("TARGET_HIT", "COMPLETED"):
                    if is_options and max_prof > 0:
                        trade_dollar_pnl = max_prof
                        trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                    else:
                        t_exit = target_2 if (target_2 and target_2 > 0) else target_1
                        eff_entry = item.get("fill_price") or entry_mid
                        if t_exit and eff_entry and eff_entry > 0:
                            sh_gain = (t_exit - eff_entry) if side == "LONG" else (eff_entry - t_exit)
                            trade_dollar_pnl = round(sh_gain * 100, 2)
                            trade_roc_pct = round((sh_gain / eff_entry * 100), 2)
                    won_dollars.append(trade_dollar_pnl)

                elif status in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
                    if is_options and max_loss > 0:
                        trade_dollar_pnl = -max_loss
                        trade_roc_pct = -100.0
                    else:
                        eff_entry = item.get("fill_price") or entry_mid
                        if tactical_stop and eff_entry and eff_entry > 0:
                            sh_loss = (tactical_stop - eff_entry) if side == "LONG" else (eff_entry - tactical_stop)
                            trade_dollar_pnl = round(sh_loss * 100, 2)
                            trade_roc_pct = round((sh_loss / eff_entry * 100), 2)
                    lost_dollars.append(trade_dollar_pnl)

                elif status in ("IN_TRADE", "IN_ZONE") and live_px and live_px > 0:
                    if is_options and (max_prof > 0 or max_loss > 0):
                        if "PUT" in struct:
                            if live_px >= short_k:
                                trade_dollar_pnl = max_prof
                                trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif live_px <= long_k:
                                trade_dollar_pnl = -max_loss
                                trade_roc_pct = -100.0
                            elif short_k > long_k:
                                ratio = (live_px - long_k) / (short_k - long_k)
                                trade_dollar_pnl = round(max_prof * ratio - max_loss * (1.0 - ratio), 2)
                                trade_roc_pct = round((trade_dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                        else:
                            if live_px >= short_k:
                                trade_dollar_pnl = max_prof
                                trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif live_px <= long_k:
                                trade_dollar_pnl = -max_loss
                                trade_roc_pct = -100.0
                            else:
                                spread_val = (live_px - long_k) * 100.0
                                trade_dollar_pnl = round(spread_val - (debit * 100.0), 2)
                                trade_roc_pct = round((trade_dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                    else:
                        eff_entry = item.get("fill_price") or entry_mid
                        if eff_entry and eff_entry > 0:
                            sh_gain = (live_px - eff_entry) if side == "LONG" else (eff_entry - live_px)
                            trade_dollar_pnl = round(sh_gain * 100, 2)
                            trade_roc_pct = round((sh_gain / eff_entry * 100), 2)
                    active_dollars.append(trade_dollar_pnl)

                item["trade_type"] = trade_type
                item["trade_label"] = trade_label
                item["trade_dollar_pnl"] = trade_dollar_pnl
                item["trade_roc_pct"] = trade_roc_pct
                item["modeled_dollar_pnl"] = trade_dollar_pnl
                item["modeled_roc_pct"] = trade_roc_pct
                item["is_modeled"] = True
                item["accounting_mode"] = "HYPOTHETICAL_100_SHARES_OR_SINGLE_SPREAD"
                item["trade_max_profit"] = max_prof
                item["trade_max_loss"] = max_loss
                item["realized_pnl_pct"] = trade_roc_pct

                # Risk to Reward ratio
                rr_ratio = None
                if is_options and max_loss > 0:
                    rr_ratio = round(max_prof / max_loss, 2)
                elif entry_mid and target_1 and tactical_stop and entry_mid > 0:
                    r_risk = abs(entry_mid - tactical_stop)
                    r_rew = abs(target_1 - entry_mid)
                    if r_risk > 0:
                        rr_ratio = round(r_rew / r_risk, 2)
                item["rr_ratio"] = rr_ratio

                # Actionable flag: IN_ZONE, IN_TRADE, or distance <= 1.0%
                is_actionable = (status in ("IN_ZONE", "IN_TRADE")) or (dist_pct is not None and abs(dist_pct) <= 1.0) or opt_act
                item["is_actionable"] = is_actionable
                if is_actionable:
                    actionable_count += 1

            total_resolved = len(won_dollars) + len(lost_dollars)
            win_rate = round((len(won_dollars) / total_resolved) * 100.0, 1) if total_resolved > 0 else 0.0

            total_won_dollars = round(sum(won_dollars), 2)
            total_lost_dollars = round(sum(lost_dollars), 2)
            total_active_dollars = round(sum(active_dollars), 2)
            net_dollar_profit = round(total_won_dollars + total_lost_dollars + total_active_dollars, 2)

            performance_summary = {
                "total_targets": len(targets),
                "won_count": len(won_dollars),
                "lost_count": len(lost_dollars),
                "active_count": len(active_dollars),
                "actionable_count": actionable_count,
                "win_rate": win_rate,
                "total_won_dollars": total_won_dollars,
                "total_lost_dollars": total_lost_dollars,
                "total_active_dollars": total_active_dollars,
                "net_dollar_profit": net_dollar_profit,
                "is_modeled": True,
                "accounting_mode": "HYPOTHETICAL_100_SHARES_OR_SINGLE_SPREAD",
            }

            return {"targets": targets, "count": len(targets), "performance": performance_summary}
    except Exception as e:
        logger.error(f"Error fetching watch targets: {e}")
        return {"targets": [], "count": 0, "error": str(e)}


@router.post("/api/watch-targets/delete")
def delete_watch_target_post(data: dict):
    """Delete / Untrack a stalking target from the SQLite watch database."""
    try:
        ticker = (data.get("ticker") or "").upper().strip()
        date = (data.get("date") or "").strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker symbol required")

        with get_db() as conn:
            c = conn.cursor()
            if date:
                c.execute("DELETE FROM watch_targets WHERE ticker = ? AND date = ?", (ticker, date))
            else:
                c.execute("DELETE FROM watch_targets WHERE ticker = ?", (ticker,))
            conn.commit()

        append_log(f"🗑️ Untracked and removed {ticker} ({date or 'all dates'}) from Watchlist.")
        return {"status": "ok", "deleted": ticker}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed deleting watch target: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/watch-targets/poll")
def poll_watch_targets():
    """Trigger an immediate real-time price polling & trigger evaluation cycle."""
    try:
        from run_watch_alerts import evaluate_watch_cycle
        updated = evaluate_watch_cycle(sync_sheets=False)
        return {"status": "ok", "updated_count": len(updated)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/watch-targets/status")
def set_watch_target_status_endpoint(data: dict):
    """Manually update the status of a watch target (e.g. IN_TRADE, STALKING, TARGET_HIT)."""
    try:
        ticker = (data.get("ticker") or "").upper().strip()
        new_status = (data.get("status") or "").upper().strip()
        if not ticker or not new_status:
            raise HTTPException(status_code=400, detail="Ticker and status are required")
        if new_status not in ("STALKING", "IN_ZONE", "IN_TRADE", "TARGET_HIT", "INVALIDATED", "MISSED_RUNAWAY"):
            raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

        from src.tracking.watch_manager import update_target_live_state, _get_connection, _db_lock, _now_iso
        with _db_lock:
            with _get_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE watch_targets SET status = ?, updated_at = ? WHERE ticker = ?", (new_status, _now_iso(), ticker))
                conn.commit()

        append_log(f"⚡ Watchlist target {ticker} status manually updated to {new_status}.")
        return {"status": "ok", "ticker": ticker, "new_status": new_status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed updating watch target status: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/api/watch-alerts")
def get_watch_alerts(limit: int = 50, ticker: Optional[str] = None):
    """Retrieve recent trigger alerts and daily alert events from SQLite."""
    with get_db() as conn:
        cursor = conn.cursor()
        if ticker:
            rows = cursor.execute(
                "SELECT id, ticker, date, trigger_type, message, spot_price, triggered_at FROM watch_alerts WHERE ticker = ? ORDER BY id DESC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = cursor.execute(
                "SELECT id, ticker, date, trigger_type, message, spot_price, triggered_at FROM watch_alerts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "alerts": [
                {
                    "id": r["id"],
                    "ticker": r["ticker"],
                    "date": r["date"],
                    "trigger_type": r["trigger_type"],
                    "message": r["message"],
                    "spot_price": r["spot_price"],
                    "triggered_at": r["triggered_at"],
                }
                for r in rows
            ]
        }


@router.post("/api/alerts/sync")
def trigger_alerts_sync():
    """Trigger 1-click sync of all reports into SQLite and Tastytrade."""
    def _sync():
        append_log("🔄 Syncing all research levels into SQLite, Google Sheets, and Tastytrade...")
        cmd = [sys.executable, "run_watch_alerts.py", "--sync", "--once"]
        proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                append_log(line_str)
        proc.wait()
        append_log("✅ Sync complete.")

    threading.Thread(target=_sync, daemon=True).start()
    return {"status": "sync_triggered"}


@router.get("/api/tastytrade-alerts")
def get_tastytrade_alerts():
    """Fetch live quote alerts from Tastytrade account."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alerts = client.get_quote_alerts()
        return {"alerts": alerts, "count": len(alerts)}
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e)}


@router.post("/api/tastytrade-alerts/sync-ticker")
def sync_ticker_tastytrade_alerts(req: SyncTickerAlertsRequest):
    """Sync a researched ticker's structured watch levels directly into Tastytrade cloud quote alerts on demand."""
    ticker_clean = req.ticker.strip().upper()
    if not ticker_clean:
        raise HTTPException(status_code=400, detail="Ticker is required")
    try:
        from run_watch_alerts import sync_reports_to_watchlist
        count = sync_reports_to_watchlist(
            target_date=req.date,
            target_ticker=ticker_clean,
            sync_tastytrade=True,
        )
        return {
            "success": True,
            "ticker": ticker_clean,
            "date": req.date,
            "indexed_count": count,
            "message": f"Successfully synced Tastytrade cloud quote alerts for {ticker_clean}",
        }
    except Exception as e:
        logger.error(f"Error syncing Tastytrade alerts for {ticker_clean}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tastytrade-alerts/create")
def create_tastytrade_alert(req: CreateAlertRequest):
    """Create a new cloud price alert on Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alert = client.create_quote_alert(
            symbol=req.symbol.strip().upper(),
            threshold=req.threshold,
            operator=req.operator,
            expires_at=req.expires_at,
        )
        if not alert:
            raise HTTPException(status_code=400, detail="Failed to create alert on Tastytrade")
        return {"success": True, "alert": alert}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tastytrade-alerts/modify")
def modify_tastytrade_alert(req: ModifyAlertRequest):
    """Modify an existing cloud price alert on Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alert = client.modify_quote_alert(
            alert_id=req.alert_id,
            symbol=req.symbol.strip().upper(),
            threshold=req.threshold,
            operator=req.operator,
            expires_at=req.expires_at,
        )
        if not alert:
            raise HTTPException(status_code=400, detail="Failed to modify alert on Tastytrade")
        return {"success": True, "alert": alert}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/tastytrade-alerts/all")
def delete_all_tastytrade_alerts():
    """Delete all quote alerts across all tickers from Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        deleted_count = client.delete_all_quote_alerts()
        return {"success": True, "deleted_count": deleted_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/tastytrade-alerts/{alert_id}")
def delete_tastytrade_alert(alert_id: str):
    """Delete a cloud alert by external ID."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        ok = client.delete_quote_alert(alert_id)
        return {"success": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
