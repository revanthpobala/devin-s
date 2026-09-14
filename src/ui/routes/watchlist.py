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
                        target_1 = item.get("target_1")
                        target_2 = item.get("target_2")

                        is_stop_breached = False
                        if inv_price and inv_price > 0:
                            if side == "LONG" and live_px <= inv_price:
                                is_stop_breached = True
                            elif side == "SHORT" and live_px >= inv_price:
                                is_stop_breached = True

                        hit_target = False
                        if target_2 and target_2 > 0:
                            if (side == "LONG" and live_px >= target_2) or (side == "SHORT" and live_px <= target_2):
                                hit_target = True
                        elif target_1 and target_1 > 0:
                            if (side == "LONG" and live_px >= target_1) or (side == "SHORT" and live_px <= target_1):
                                hit_target = True

                        if is_stop_breached:
                            item["status"] = "INVALIDATED"
                        elif hit_target and item.get("status") in ("IN_TRADE", "IN_ZONE"):
                            item["status"] = "TARGET_HIT"
                        elif hit_target and item.get("status") == "STALKING":
                            item["status"] = "MISSED_RUNAWAY"

                        if entry_low and entry_high and entry_low > 0 and entry_high > 0:
                            if side == "LONG":
                                if live_px > entry_high:
                                    item["distance_to_entry_pct"] = round(((live_px - entry_high) / entry_high) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                elif live_px < entry_low:
                                    item["distance_to_entry_pct"] = round(((live_px - entry_low) / entry_low) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                else:
                                    item["distance_to_entry_pct"] = 0.0
                                    if not is_stop_breached and not hit_target and item.get("status") == "STALKING":
                                        item["status"] = "IN_ZONE"
                            else:
                                if live_px < entry_low:
                                    item["distance_to_entry_pct"] = round(((entry_low - live_px) / entry_low) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                elif live_px > entry_high:
                                    item["distance_to_entry_pct"] = round(((entry_high - live_px) / entry_high) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                else:
                                    item["distance_to_entry_pct"] = 0.0
                                    if not is_stop_breached and not hit_target and item.get("status") == "STALKING":
                                        item["status"] = "IN_ZONE"
            except Exception as q_err:
                logger.debug(f"Error enriching watch targets with Schwab quotes: {q_err}")

            return {"targets": targets, "count": len(targets)}
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
