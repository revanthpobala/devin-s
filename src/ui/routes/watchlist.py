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

import time

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["watchlist"])

_WATCH_TARGETS_CACHE: Dict[str, Any] = {}
_WATCH_TARGETS_CACHE_TTL = 10.0  # 10 seconds


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


class CreateWatchTargetRequest(BaseModel):
    ticker: str
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    tactical_stop: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    side: str = "LONG"
    verdict: str = "PASS"
    conviction: int = 5
    notes: Optional[str] = None
    create_cloud_alert: bool = True


@router.get("/api/watch-targets")
def get_watch_targets():
    """Fetch active stalking targets and enrich with tactical trade ideas and real-time Schwab quotes."""
    now_ts = time.time()
    cached = _WATCH_TARGETS_CACHE.get("all_targets")
    if cached and (now_ts - cached[0] < _WATCH_TARGETS_CACHE_TTL):
        return cached[1]

    try:
        from src.logic.trade_ideas_generator import generate_trade_ideas_for_target

        rep_root = config.BASE_DIR / "reports"
        raw_root = config.BASE_DIR / "data" / "raw"

        with get_db() as conn:
            c = conn.cursor()
            rows = c.execute("SELECT rowid as id, * FROM watch_targets WHERE is_active IS NULL OR is_active = 1").fetchall()
            if len(rows) < 500:
                try:
                    from src.tracking.watch_manager import ensure_universe_prepopulated
                    ensure_universe_prepopulated()
                    rows = c.execute("SELECT rowid as id, * FROM watch_targets WHERE is_active IS NULL OR is_active = 1").fetchall()
                except Exception as ex:
                    logger.warning(f"Could not auto-prepopulate universe: {ex}")

            targets = []

            for r in rows:
                item = dict(r)
                if item.get("raw_json"):
                    try:
                        item["parsed_json"] = json.loads(item["raw_json"])
                    except Exception:
                        item["parsed_json"] = {}

                sym = item.get("ticker", "").upper()
                st_upper = str(item.get("status", "")).upper()
                if st_upper == "UNRESEARCHED":
                    item["research_timestamp"] = "1970-01-01T00:00:00"
                    item["is_open_position"] = False
                    item["trade_ideas"] = []
                    item["last_price"] = None
                    targets.append(item)
                    continue

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

                item["research_timestamp"] = target_mtime or item.get("updated_at") or d_str
                item["is_open_position"] = False
                item["trade_ideas"] = generate_trade_ideas_for_target(item)
                targets.append(item)

            # Only fetch live quotes for symbols that have actual research/setups (prevents blocking 983 HTTP batch queries)
            try:
                from src.clients.schwab_client import _QUOTE_CACHE
                for item in targets:
                    if str(item.get("status", "")).upper() == "UNRESEARCHED":
                        continue
                    sym = item.get("ticker", "").upper()
                    cached_q = _QUOTE_CACHE.get(sym)
                    if cached_q and len(cached_q) > 1 and cached_q[1]:
                        q = cached_q[1]
                        if q.get("last_price"):
                            item["last_price"] = float(q["last_price"])
                            item["net_change"] = q.get("net_change", 0.0)
                            item["net_percent_change"] = q.get("net_percent_change", 0.0)
                            item["quote_source"] = "SCHWAB"
            except Exception as q_err:
                logger.debug(f"Error enriching watch targets with cached quotes: {q_err}")

            # Calculate Suggested Trade R-Multiples and performance summary
            won_r = []
            lost_r = []
            active_r = []
            actionable_count = 0

            for item in targets:
                if str(item.get("status") or "").upper() == "UNRESEARCHED":
                    item["trade_type"] = "SHARES"
                    item["trade_label"] = "Constituent"
                    item["is_actionable"] = False
                    continue

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

                # 2. SUGGESTED TRADE R-MULTIPLE
                eff_entry = item.get("fill_price") or entry_mid
                trade_r = None
                risk_amt = abs(eff_entry - tactical_stop) if (eff_entry and tactical_stop) else 0.0

                if status in ("TARGET_HIT", "COMPLETED"):
                    if is_options:
                        # Options TARGET_HIT unscored until P7 chain pricing exists
                        trade_r = None
                    else:
                        t_exit = target_1
                        if t_exit and eff_entry and risk_amt > 0:
                            gain = (t_exit - eff_entry) if side == "LONG" else (eff_entry - t_exit)
                            trade_r = round(gain / risk_amt, 2)
                            won_r.append(trade_r)
                        else:
                            trade_r = None

                elif status in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
                    was_filled = bool(item.get("fill_price") or item.get("was_filled") or item.get("user_taken"))
                    if not was_filled:
                        trade_r = None
                    else:
                        trade_r = -1.0
                        lost_r.append(trade_r)

                elif status in ("IN_TRADE", "IN_ZONE") and live_px and live_px > 0:
                    if is_options:
                        trade_r = None
                    else:
                        if eff_entry and risk_amt > 0:
                            unrealized_gain = (live_px - eff_entry) if side == "LONG" else (eff_entry - live_px)
                            trade_r = round(unrealized_gain / risk_amt, 2)
                            active_r.append(trade_r)
                        else:
                            trade_r = None

                # Calculate real dollar P&L and targets (per 100 shares or options contract)
                trade_dollar_pnl = 0.0
                target_1_dollar = max_prof if (is_options and max_prof > 0) else None
                risk_dollar = max_loss if (is_options and max_loss > 0) else None
                if not is_options:
                    if entry_mid and target_1 and entry_mid > 0:
                        target_1_dollar = round(abs(target_1 - entry_mid) * 100.0, 2)
                    if entry_mid and tactical_stop and entry_mid > 0:
                        risk_dollar = round(abs(entry_mid - tactical_stop) * 100.0, 2)
                    if eff_entry and live_px and eff_entry > 0:
                        trade_dollar_pnl = round(((live_px - eff_entry) if side == "LONG" else (eff_entry - live_px)) * 100.0, 2)
                else:
                    if trade_r is not None and max_loss > 0:
                        trade_dollar_pnl = round(max(-max_loss, min(max_prof, trade_r * max_loss)), 2)

                item["trade_type"] = trade_type
                item["trade_label"] = trade_label
                item["r_multiple"] = trade_r
                item["trade_dollar_pnl"] = trade_dollar_pnl
                item["target_1_dollar"] = target_1_dollar
                item["risk_dollar"] = risk_dollar
                item["trade_roc_pct"] = pnl_pct or 0.0
                item["modeled_dollar_pnl"] = trade_dollar_pnl
                item["modeled_roc_pct"] = pnl_pct or 0.0
                item["is_modeled"] = False
                item["accounting_mode"] = "R_MULTIPLE"
                item["trade_max_profit"] = max_prof or target_1_dollar
                item["trade_max_loss"] = max_loss or risk_dollar

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

            total_resolved = len(won_r) + len(lost_r)
            win_rate = round((len(won_r) / total_resolved) * 100.0, 1) if total_resolved > 0 else 0.0
            total_r = round(sum(won_r) + sum(lost_r) + sum(active_r), 2)
            mean_r = round((sum(won_r) + sum(lost_r)) / total_resolved, 2) if total_resolved > 0 else 0.0

            performance_summary = {
                "total_targets": len(targets),
                "won_count": len(won_r),
                "lost_count": len(lost_r),
                "active_count": len(active_r),
                "actionable_count": actionable_count,
                "win_rate": win_rate,
                "total_r": total_r,
                "mean_r": mean_r,
                "net_dollar_profit": 0.0,
                "is_modeled": False,
                "accounting_mode": "R_MULTIPLE",
            }

            res = {"targets": targets, "count": len(targets), "performance": performance_summary}
            _WATCH_TARGETS_CACHE["all_targets"] = (now_ts, res)
            return res
    except Exception as e:
        logger.error(f"Error fetching watch targets: {e}")
        return {"targets": [], "count": 0, "error": str(e)}


@router.post("/api/watch-targets/sync-universe")
def sync_universe():
    """Explicitly pre-populate or refresh universe constituents and existing research."""
    from src.tracking.watch_manager import ensure_universe_prepopulated
    total = ensure_universe_prepopulated()
    _WATCH_TARGETS_CACHE.clear()
    return {"status": "ok", "total_constituents": total}


@router.post("/api/watch-targets/delete")
def delete_watch_target_post(data: dict):
    """Soft delete / Untrack a stalking target from the SQLite watch database."""
    _WATCH_TARGETS_CACHE.clear()
    try:
        row_id = data.get("id") or data.get("row_id")
        ticker = (data.get("ticker") or "").upper().strip()
        date = (data.get("date") or "").strip()
        if not ticker and not row_id:
            raise HTTPException(status_code=400, detail="Ticker symbol or row id required")

        with get_db() as conn:
            c = conn.cursor()
            if row_id:
                c.execute("UPDATE watch_targets SET is_active = 0 WHERE rowid = ?", (row_id,))
            elif date:
                c.execute("UPDATE watch_targets SET is_active = 0 WHERE ticker = ? AND date = ?", (ticker, date))
            else:
                c.execute("UPDATE watch_targets SET is_active = 0 WHERE ticker = ?", (ticker,))
            conn.commit()

        target_label = ticker or f"id {row_id}"
        append_log(f"🗑️ Soft deleted and untracked {target_label} ({date or 'all dates'}) from Watchlist.")
        return {"status": "ok", "deleted": target_label}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed soft-deleting watch target: {e}")
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
    """Manually update the status and user_taken of a watch target by row ID or ticker."""
    try:
        row_id = data.get("id") or data.get("row_id")
        ticker = (data.get("ticker") or "").upper().strip()
        new_status = (data.get("status") or "").upper().strip()
        user_taken = data.get("user_taken")
        if user_taken is None:
            if new_status == "IN_TRADE":
                user_taken = 1
            elif new_status == "STALKING":
                user_taken = 0

        if not ticker and not row_id:
            raise HTTPException(status_code=400, detail="Ticker or row ID required")
        if new_status and new_status not in ("STALKING", "IN_ZONE", "IN_TRADE", "TARGET_HIT", "INVALIDATED", "MISSED_RUNAWAY"):
            raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

        from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso
        with _db_lock:
            with _get_connection() as conn:
                c = conn.cursor()
                now_str = _now_iso()
                if row_id:
                    wt_row = c.execute("SELECT ticker, date, suggestion_id FROM watch_targets WHERE rowid = ?", (row_id,)).fetchone()
                    srow = dict(wt_row) if wt_row else {}
                    sugg_id = srow.get("suggestion_id")
                    t_sym = srow.get("ticker")
                    t_date = srow.get("date")

                    if new_status:
                        c.execute(
                            "UPDATE watch_targets SET status = ?, user_taken = COALESCE(?, user_taken), updated_at = ? WHERE rowid = ?",
                            (new_status, user_taken, now_str, row_id)
                        )
                    else:
                        c.execute(
                            "UPDATE watch_targets SET user_taken = ?, updated_at = ? WHERE rowid = ?",
                            (user_taken, now_str, row_id)
                        )
                    # Sync to suggestions ledger if exists and user_taken specified
                    if user_taken is not None:
                        try:
                            if sugg_id:
                                c.execute("UPDATE suggestions SET taken = ? WHERE id = ?", (user_taken, sugg_id))
                            elif t_sym and t_date:
                                c.execute(
                                    "UPDATE suggestions SET taken = ? WHERE ticker = ? AND date = ?",
                                    (user_taken, t_sym, t_date)
                                )
                        except Exception:
                            pass
                else:
                    wt_row = c.execute("SELECT date, suggestion_id FROM watch_targets WHERE ticker = ?", (ticker,)).fetchone()
                    srow = dict(wt_row) if wt_row else {}
                    sugg_id = srow.get("suggestion_id")
                    t_date = srow.get("date")

                    if new_status:
                        c.execute(
                            "UPDATE watch_targets SET status = ?, user_taken = COALESCE(?, user_taken), updated_at = ? WHERE ticker = ?",
                            (new_status, user_taken, now_str, ticker)
                        )
                    else:
                        c.execute(
                            "UPDATE watch_targets SET user_taken = ?, updated_at = ? WHERE ticker = ?",
                            (user_taken, now_str, ticker)
                        )
                    if user_taken is not None:
                        try:
                            if sugg_id:
                                c.execute("UPDATE suggestions SET taken = ? WHERE id = ?", (user_taken, sugg_id))
                            elif t_date:
                                c.execute("UPDATE suggestions SET taken = ? WHERE ticker = ? AND date = ?", (user_taken, ticker, t_date))
                        except Exception:
                            pass
                conn.commit()

        target_label = ticker or f"id {row_id}"
        append_log(f"⚡ Watchlist target {target_label} updated: status={new_status}, user_taken={user_taken}.")
        return {"status": "ok", "ticker": target_label, "new_status": new_status, "user_taken": user_taken}
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
    target_date = req.date.strip() if req.date and req.date.strip() else None
    try:
        from run_watch_alerts import sync_reports_to_watchlist
        result = sync_reports_to_watchlist(
            target_date=target_date,
            target_ticker=ticker_clean,
            sync_tastytrade=True,
        )
        count = int(result)
        if count == 0:
            return {
                "success": False,
                "ticker": ticker_clean,
                "date": target_date,
                "indexed_count": 0,
                "error": f"No research reports or watch levels found to index for {ticker_clean}",
                "message": f"No research reports or watch levels found to index for {ticker_clean}",
            }

        rejected_list = getattr(result, "rejected", [])
        ticker_rejected = next((r for r in rejected_list if r.get("ticker") == ticker_clean), None)
        if ticker_rejected:
            reasons = "; ".join(ticker_rejected.get("reasons", []))
            return {
                "success": False,
                "ticker": ticker_clean,
                "date": target_date,
                "indexed_count": count,
                "error": f"Levels rejected by validation gate: {reasons}. Tastytrade cloud alerts not registered.",
                "message": f"Levels rejected by validation gate: {reasons}. Tastytrade cloud alerts not registered.",
            }

        tt_count = getattr(result, "tt_alerts_count", 0)
        msg = (
            f"Successfully synced {tt_count} Tastytrade cloud quote alert(s) for {ticker_clean}"
            if tt_count > 0
            else f"Watch levels indexed for {ticker_clean}"
        )
        return {
            "success": True,
            "ticker": ticker_clean,
            "date": target_date,
            "indexed_count": count,
            "tt_alerts_count": tt_count,
            "message": msg,
        }
    except Exception as e:
        logger.error(f"Error syncing Tastytrade alerts for {ticker_clean}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/watchlist/sync-tt")
def sync_tt_alerts_endpoint(ticker: str, date: Optional[str] = None):
    """Sync a researched ticker's structured watch levels directly into Tastytrade cloud quote alerts."""
    req = SyncTickerAlertsRequest(ticker=ticker, date=date)
    return sync_ticker_tastytrade_alerts(req)


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


@router.post("/api/watch-targets/create")
def create_watch_target_endpoint(req: CreateWatchTargetRequest):
    """Create or upsert a tactical watch target into SQLite research_watch.db, and optionally register cloud quote alerts."""
    try:
        from src.tracking.watch_manager import upsert_watch_target
        sym = req.ticker.strip().upper()
        if not sym:
            raise HTTPException(status_code=400, detail="Ticker is required")

        today_str = datetime.now().strftime("%Y-%m-%d")
        ez_high = req.entry_zone_high
        ez_low = req.entry_zone_low if req.entry_zone_low is not None else (round(ez_high * 0.99, 2) if ez_high else None)
        stop = req.tactical_stop
        t1 = req.target_1
        t2 = req.target_2 if req.target_2 is not None else (round(t1 * 1.05, 2) if t1 else None)

        payload = {
            "ticker": sym,
            "date": today_str,
            "verdict": req.verdict,
            "conviction": req.conviction,
            "actionable": 1,
            "side": req.side.upper(),
            "shares_plan": {
                "side": req.side.upper(),
                "entry_type": "LIMIT",
                "entry_zone_low": ez_low,
                "entry_zone_high": ez_high,
                "tactical_stop": stop,
                "target_1": t1,
                "target_2": t2,
            },
            "status": "STALKING",
        }
        upsert_watch_target(payload)

        # Clear watch targets cache so dashboard updates immediately
        _WATCH_TARGETS_CACHE.clear()

        # If requested, also register Tastytrade cloud alert for entry
        cloud_alert = None
        if req.create_cloud_alert and ez_high:
            try:
                from src.clients.tastytrade_client import TastytradeClient
                tt = TastytradeClient()
                cloud_alert = tt.create_quote_alert(
                    symbol=sym,
                    threshold=ez_high,
                    operator="<=" if req.side.upper() == "LONG" else ">="
                )
            except Exception as te:
                logger.debug(f"Optional cloud alert creation note for {sym}: {te}")

        return {
            "success": True,
            "ticker": sym,
            "entry_zone_high": ez_high,
            "entry_zone_low": ez_low,
            "tactical_stop": stop,
            "target_1": t1,
            "cloud_alert_created": bool(cloud_alert),
            "message": f"Successfully registered watch target for {sym}"
        }
    except Exception as e:
        logger.error(f"Error creating watch target: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
