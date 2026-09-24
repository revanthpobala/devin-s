from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

from src.tracking.watch_manager import _get_connection, _db_lock
from src.tracking.suggestion_scorer import get_main_record_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desk", tags=["desk"])


class JournalNotesUpdate(BaseModel):
    notes: str


@router.get("/today")
def get_today():
    try:
        with _db_lock:
            with _get_connection() as conn:
                conn.row_factory = sqlite3.Row if "sqlite3" in globals() else None
                if not conn.row_factory:
                    import sqlite3
                    conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                from src.tracking.alert_db import get_research_queue
                
                today_str = datetime.now().strftime("%Y-%m-%d")
                
                # found
                found_raw = get_research_queue(today_str)
                found = []
                for r in found_raw:
                    found.append({
                        "ticker": r["symbol"],
                        "source": r["source"],
                        "setup": r["setup"],
                        "status": r["status"],
                        "reason": r["reason"]
                    })
                
                # actionable & stalking
                suggestions = cursor.execute(
                    "SELECT s.id as suggestion_id, s.ticker, s.setup_lane as lane, s.entry_low, s.entry_high, s.stop, s.target_1 as target, s.lane_prior_win, s.lane_prior_ev, w.status, w.distance_to_entry_pct as dist, w.last_price, s.rr_at_market_at_signal FROM suggestions s LEFT JOIN watch_targets w ON s.ticker = w.ticker WHERE s.gate_status = 'PASS' AND s.date >= date('now', '-21 days')"
                ).fetchall()
                
                actionable = []
                stalking = []
                
                for row in suggestions:
                    r = dict(row)
                    status = r.get("status") or "STALKING"
                    dist = r.get("dist")
                    if status in ("IN_ZONE", "IN_TRADE") or (dist is not None and abs(dist) <= 1.5):
                        # Compute live RR
                        entry_h = r.get("entry_high") or 0.0
                        stop = r.get("stop") or 0.0
                        target = r.get("target") or 0.0
                        last_px = r.get("last_price") or 0.0
                        
                        live_rr = 0.0
                        if last_px > stop and target > last_px:
                            live_rr = round((target - last_px) / (last_px - stop), 2)
                        r["live_rr"] = live_rr
                        
                        actionable.append(r)
                    elif status == "STALKING":
                        stalking.append(r)
                
                actionable.sort(key=lambda x: x.get("live_rr", 0.0), reverse=True)
                
                return {
                    "found": found,
                    "actionable": actionable,
                    "stalking": stalking,
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}

@router.get("/journal")
def get_journal(
    status: Optional[str] = None,
    lane: Optional[str] = None,
    ticker: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    include_rejected: int = 0
):
    page_size = 50
    offset = (page - 1) * page_size
    
    with _db_lock:
        with _get_connection() as conn:
            import sqlite3
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = "SELECT 'suggestion' as row_type, id, date, ticker, setup_lane as lane, gate_status as verdict, entry_low, entry_high, stop, target_1, rr_at_market_at_signal, fill_date, fill_price, exit_date, exit_price, exit_reason, bars_held, r_net, mae_r, lane_prior_win, lane_prior_ev, notes FROM suggestions WHERE 1=1"
            params = []
            
            if lane:
                query += " AND setup_lane = ?"
                params.append(lane)
            if ticker:
                query += " AND ticker = ?"
                params.append(ticker)
            if from_date:
                query += " AND date >= ?"
                params.append(from_date)
            if to_date:
                query += " AND date <= ?"
                params.append(to_date)
                
            if include_rejected:
                rej_query = "SELECT 'rejected' as row_type, id, date, ticker, '' as lane, 'REJECTED' as verdict, 0 as entry_low, 0 as entry_high, 0 as stop, 0 as target_1, 0 as rr_at_market_at_signal, '' as fill_date, 0 as fill_price, '' as exit_date, 0 as exit_price, reasons as exit_reason, 0 as bars_held, 0 as r_net, 0 as mae_r, 0 as lane_prior_win, 0 as lane_prior_ev, '' as notes FROM rejected_plans WHERE 1=1"
                rej_params = []
                if ticker:
                    rej_query += " AND ticker = ?"
                    rej_params.append(ticker)
                if from_date:
                    rej_query += " AND date >= ?"
                    rej_params.append(from_date)
                if to_date:
                    rej_query += " AND date <= ?"
                    rej_params.append(to_date)
                query = f"({query}) UNION ALL ({rej_query})"
                params.extend(rej_params)
                
            query += " ORDER BY date DESC, ticker ASC LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            
            rows = cursor.execute(query, params).fetchall()
            
            results = []
            for row in rows:
                r = dict(row)
                # Status derived: OPEN (no fill), FILLED (fill, no exit), CLOSED (exit), EXPIRED (no fill in 21 bars).
                if r["row_type"] == "rejected":
                    derived_status = "REJECTED"
                else:
                    if r.get("exit_date"):
                        derived_status = "CLOSED"
                    elif r.get("fill_date"):
                        derived_status = "FILLED"
                    else:
                        date_obj = datetime.strptime(r["date"][:10], "%Y-%m-%d")
                        if (datetime.now() - date_obj).days > 30: # approx 21 bars
                            derived_status = "EXPIRED"
                        else:
                            derived_status = "OPEN"
                
                if status and status.upper() != derived_status:
                    continue
                    
                r["derived_status"] = derived_status
                r["dossier_link"] = f"/api/report/{r['date'][:10]}/{r['ticker']}"
                
                # Plan mapping
                if r.get("entry_type") == "BREAKOUT" and r.get("breakout_level"):
                    r["plan_entry"] = str(r["breakout_level"])
                elif r.get("entry_low") and r.get("entry_high"):
                    r["plan_entry"] = f"{r['entry_low']}-{r['entry_high']}"
                else:
                    r["plan_entry"] = "-"
                
                r["plan_stop"] = str(r.get("stop") or "-")
                r["plan_t1"] = str(r.get("target_1") or "-")
                
                results.append(r)
                
            return {"items": results, "page": page}

@router.patch("/journal/{id}")
def update_journal_notes(id: int, update: JournalNotesUpdate):
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE suggestions SET notes = ? WHERE id = ?", (update.notes, id))
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            return {"success": True}

@router.get("/record")
def get_record(from_date: str = Query("2026-09-23", alias="from")):
    # `get_main_record_stats` + per-lane n, win %, mean/median r_net, sum R vs lane prior, equity curve (cumulative r_net by exit_date), avg bars held, avg mae_r.
    stats = get_main_record_stats(min_date=from_date)
    
    with _db_lock:
        with _get_connection() as conn:
            import sqlite3
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # equity curve (cumulative r_net by exit_date)
            rows = cursor.execute(
                "SELECT exit_date, r_net FROM suggestions WHERE source = 'judge' AND gate_status = 'PASS' AND scorer_version = 2 AND kind = 'NEW' AND verdict IN ('ENTER', 'STALK') AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2') AND date >= ? AND r_net IS NOT NULL ORDER BY exit_date ASC",
                (from_date,)
            ).fetchall()
            
            equity_curve = []
            cum_r = 0.0
            for r in rows:
                if r["exit_date"]:
                    cum_r += float(r["r_net"])
                    equity_curve.append({"date": r["exit_date"], "r_net": float(r["r_net"]), "cum_r": round(cum_r, 2)})
            
            stats["equity_curve"] = equity_curve
            stats["win_pct"] = stats.get("win_rate", 0.0)
            stats["won_count"] = stats.get("wins", 0)
            stats["total_scored"] = stats.get("total_trades", 0)
            stats["lanes"] = stats.get("by_lane", {})
            
            # avg bars held, avg mae_r
            avg_rows = cursor.execute(
                "SELECT AVG(bars_held) as avg_bars, AVG(mae_r) as avg_mae FROM suggestions WHERE source = 'judge' AND gate_status = 'PASS' AND scorer_version = 2 AND kind = 'NEW' AND verdict IN ('ENTER', 'STALK') AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2') AND date >= ? AND r_net IS NOT NULL",
                (from_date,)
            ).fetchone()
            
            if avg_rows:
                stats["avg_bars_held"] = round(avg_rows["avg_bars"], 1) if avg_rows["avg_bars"] is not None else 0.0
                stats["avg_mae_r"] = round(avg_rows["avg_mae"], 2) if avg_rows["avg_mae"] is not None else 0.0
                
    return stats
