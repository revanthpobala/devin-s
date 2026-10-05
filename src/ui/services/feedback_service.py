"""
src/ui/services/feedback_service.py

Position Tracking & Post-Mortem Feedback Loop Engine.
Provides active position surveillance, trade management (Scale 50%, BE Trail, Flatten),
and post-trade analytical evaluation ("What Needs Improving") to drive continuous
playbook improvement.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.clients.price_client import get_current_prices_batch
from src.tracking.alert_db import DB_PATH
from src.tracking.position_state import (
    load_state,
    open_position,
    scale_position,
    close_position,
    POSITIONS_FILE,
)

logger = logging.getLogger(__name__)

FEEDBACK_NOTES_FILE = config.BASE_DIR / "data" / "feedback_notes.json"
_feedback_lock = threading.Lock()


def _load_feedback_notes() -> Dict[str, Dict[str, Any]]:
    """Load persistent post-mortem feedback notes."""
    if not FEEDBACK_NOTES_FILE.exists():
        return {}
    try:
        with open(FEEDBACK_NOTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"Failed loading feedback notes: {e}")
        return {}


def _save_feedback_notes(data: Dict[str, Dict[str, Any]]) -> None:
    """Save persistent post-mortem feedback notes atomically."""
    with _feedback_lock:
        FEEDBACK_NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FEEDBACK_NOTES_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        try:
            tmp.replace(FEEDBACK_NOTES_FILE)
        except OSError:
            if tmp.exists():
                tmp.unlink()


def save_trade_feedback(
    trade_id: str,
    ticker: str,
    grade: Optional[str] = None,
    notes: Optional[str] = None,
    lesson: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a trader post-mortem critique & improvement lesson."""
    key = str(trade_id or ticker).strip()
    notes_store = _load_feedback_notes()
    entry = notes_store.get(key, {})
    entry["trade_id"] = trade_id
    entry["ticker"] = ticker.upper()
    if grade:
        entry["grade"] = grade.upper()
    if notes is not None:
        entry["notes"] = notes
    if lesson is not None:
        entry["lesson"] = lesson
    entry["updated_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat()

    notes_store[key] = entry
    _save_feedback_notes(notes_store)
    return entry


def get_feedback_loop_data() -> Dict[str, Any]:
    """
    Fetch active positions and evaluated closed positions with automated
    diagnostic critique ("What Needs Improving") and overall scorecard.
    """
    notes_store = _load_feedback_notes()

    # 1. Active Open Positions STRICTLY from data/positions.json (Single Source of Truth)
    active_state = load_state()
    active_tickers = list(active_state.keys())


    # Open positions come EXCLUSIVELY from active_state (data/positions.json)
    open_positions: List[Dict[str, Any]] = []
    all_open_syms = set(active_tickers)

    # Batch quotes for genuine open positions
    live_quotes: Dict[str, float] = {}
    if all_open_syms:
        try:
            live_quotes = get_current_prices_batch(list(all_open_syms), context="positions")
        except Exception:
            pass

    for sym in sorted(list(all_open_syms)):
        rec = active_state.get(sym, {})
        spot = live_quotes.get(sym) or float(rec.get("last_price") or rec.get("entry_price") or 0.0)
        entry = float(rec.get("entry_price") or rec.get("alert_price") or 0.0)
        stop = float(rec.get("stop") or 0.0)
        target = float(rec.get("target") or 0.0)
        side = str(rec.get("side", "LONG")).upper()
        trade_id = rec.get("trade_id") or f"{sym}_open"

        # Calculate unrealized metrics
        unrealized_pnl = 0.0
        pnl_pct = 0.0
        unrealized_r = 0.0
        risk = abs(entry - stop) if (entry and stop) else 0.0

        if entry > 0 and spot > 0:
            if "LONG" in side:
                unrealized_pnl = (spot - entry) * float(rec.get("remaining_quantity") or 100.0)
                pnl_pct = ((spot - entry) / entry) * 100.0
                if risk > 0:
                    unrealized_r = round((spot - entry) / risk, 2)
            else:
                unrealized_pnl = (entry - spot) * float(rec.get("remaining_quantity") or 100.0)
                pnl_pct = ((entry - spot) / entry) * 100.0
                if risk > 0:
                    unrealized_r = round((entry - spot) / risk, 2)

        # Trail & Target 1 status
        scaled_at_t1 = bool(rec.get("scaled_at_t1", False))
        be_locked = bool(rec.get("be_locked", False))

        pos_item = {
            "trade_id": trade_id,
            "ticker": sym,
            "side": side,
            "strategy": rec.get("strategy", "Intraday"),
            "entry_price": round(entry, 2),
            "spot_price": round(spot, 2),
            "stop_loss": round(stop, 2),
            "target": round(target, 2),
            "initial_stop": round(float(rec.get("initial_stop") or stop), 2),
            "quantity": float(rec.get("quantity") or 100.0),
            "remaining_quantity": float(rec.get("remaining_quantity") or 100.0),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "unrealized_r": unrealized_r,
            "scaled_at_t1": scaled_at_t1,
            "be_locked": be_locked,
            "runner_stop": rec.get("runner_stop"),
            "opened_at": rec.get("opened_at", ""),
            "status": "OPEN",
        }
        open_positions.append(pos_item)

    # 2. Closed Positions from trading_alerts.db & suggestions
    closed_positions: List[Dict[str, Any]] = []
    if DB_PATH.exists():
        try:
            with sqlite3.connect(str(DB_PATH), timeout=15.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT trade_id, symbol as ticker, side, strategy, entry_price, exit_price,
                           stop, target, initial_stop, initial_target, opened_at, closed_at,
                           exit_reason, realized_broker_pnl, raw_alert
                    FROM positions
                    WHERE status = 'CLOSED'
                      AND (exit_reason IS NULL OR (exit_reason != 'TEST_FIXTURE_RECONCILED' AND exit_reason != 'RECONCILED_CLOSED'))
                    ORDER BY closed_at DESC
                    LIMIT 50
                """)
                for r in cur.fetchall():
                    closed_positions.append(dict(r))
        except Exception as e:
            logger.debug(f"Error querying closed positions: {e}")

    # Also pull evaluated suggestions from research_watch.db
    rw_db = config.research_watch_db_path()
    if rw_db.exists():
        try:
            with sqlite3.connect(str(rw_db), timeout=15.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT id as suggestion_id, ticker, side, entry_low, entry_high, stop, target_1,
                           fill_price as entry_price, exit_price, fill_date as opened_at,
                           exit_date as closed_at, exit_reason, r_net, gross_r, notes
                    FROM suggestions
                    WHERE exit_date IS NOT NULL
                    ORDER BY exit_date DESC
                    LIMIT 30
                """)
                for r in cur.fetchall():
                    item = dict(r)
                    item["trade_id"] = f"sugg_{item.get('suggestion_id')}"
                    item["strategy"] = "Swing"
                    closed_positions.append(item)
        except Exception as e:
            logger.debug(f"Error querying closed suggestions: {e}")

    # Process and evaluate each closed position
    evaluated_closed: List[Dict[str, Any]] = []
    total_r = 0.0
    wins = 0
    losses = 0
    scratches = 0
    win_r_sum = 0.0
    loss_r_sum = 0.0

    from src.tracking.r_calculator import compute_r

    for c in closed_positions:
        trade_id = str(c.get("trade_id") or c.get("ticker", "trade"))
        ticker = str(c.get("ticker", "")).upper()
        side = str(c.get("side", "LONG")).upper()
        entry_px = float(c.get("entry_price") or 0.0)
        exit_px = float(c.get("exit_price") or entry_px)
        stop_px = float(c.get("initial_stop") or c.get("stop") or 0.0)
        t1_px = float(c.get("target") or c.get("target_1") or 0.0)
        exit_why = c.get("exit_reason")  # Keep NULL exit_reason as None

        # Determine realized R via unified compute_r
        unit = "option" if c.get("instrument_type") == "OPTION" or "option" in str(c.get("strategy", "")).lower() else "share"
        r_val = c.get("r_net") if c.get("r_net") is not None else c.get("gross_r")
        if r_val is not None and abs(float(r_val)) <= 20.0:
            realized_r = round(float(r_val), 4)
        else:
            realized_r = compute_r(
                unit=unit,
                entry=entry_px,
                exit_px=exit_px,
                stop=stop_px if stop_px > 0 else None,
                side=side,
                atr=c.get("atr"),
                debit=c.get("debit"),
                dollar_pnl=c.get("realized_broker_pnl"),
            )

        # Outcome bucketing: scratch (|R| < 0.1) is its own bucket
        if realized_r is not None:
            total_r += realized_r
            if abs(realized_r) < 0.1:
                outcome = "SCRATCH"
                scratches += 1
                auto_grade = "B"
            elif realized_r >= 0.1:
                outcome = "WIN"
                wins += 1
                win_r_sum += realized_r
                auto_grade = "A" if realized_r >= 1.5 else "B"
            else:
                outcome = "LOSS"
                losses += 1
                loss_r_sum += abs(realized_r)
                auto_grade = "C" if realized_r >= -1.1 else "D"
        else:
            outcome = "UNMEASURED"
            auto_grade = "N/A"

        # Automated Diagnostic ("What Needs Improving")
        improvements: List[str] = []
        if outcome == "LOSS":
            if realized_r is not None and realized_r < -1.15:
                improvements.append("⚠️ Stop Slippage: Loss exceeded 1.0R (-{:.2f}R). Exit was delayed or stop was widened past initial invalidation.".format(abs(realized_r)))
            elif realized_r is not None:
                improvements.append("🛡️ Clean Invalidation: Hard stop honored at planned risk level ({:.2f}R). Standard trade variance.".format(realized_r))
            if exit_why and "EOD" in str(exit_why):
                improvements.append("⏳ Time Invalidation: Trade closed due to EOD Flatten rule to eliminate overnight binary risk.")
        elif outcome == "WIN":
            if realized_r is not None and realized_r >= 1.5:
                improvements.append("🏆 High Expectancy: Reached Target 1 with favorable R:R. Captured +{:.2f}R profit.".format(realized_r))
            elif realized_r is not None:
                improvements.append("✅ Profit Secured: Partial scale or early tactical exit (+{:.2f}R).".format(realized_r))
        elif outcome == "SCRATCH":
            improvements.append("⚖️ Breakeven Defense: Position scratched ({:.2f}R) to prevent turning a winning trigger into a loss.".format(realized_r if realized_r is not None else 0.0))

        saved_notes = notes_store.get(trade_id) or notes_store.get(ticker) or {}
        user_grade = saved_notes.get("grade") or auto_grade
        user_notes = saved_notes.get("notes") or ""
        user_lesson = saved_notes.get("lesson") or ""

        evaluated_closed.append({
            "trade_id": trade_id,
            "ticker": ticker,
            "side": side,
            "strategy": c.get("strategy", "Intraday"),
            "entry_price": round(entry_px, 2),
            "exit_price": round(exit_px, 2),
            "stop_loss": round(stop_px, 2),
            "target": round(t1_px, 2),
            "realized_r": realized_r,
            "outcome": outcome,
            "execution_grade": user_grade,
            "auto_grade": auto_grade,
            "exit_reason": exit_why,
            "diagnostic": " ".join(improvements),
            "user_notes": user_notes,
            "user_lesson": user_lesson,
            "opened_at": c.get("opened_at", ""),
            "closed_at": c.get("closed_at", ""),
        })

    # Sort closed trades newest first
    evaluated_closed.sort(key=lambda x: str(x.get("closed_at") or ""), reverse=True)

    # Aggregate Scorecard
    total_evaluated = len(evaluated_closed)
    win_rate = round((wins / total_evaluated * 100.0), 1) if total_evaluated > 0 else 0.0
    avg_win_r = round(win_r_sum / wins, 2) if wins > 0 else 0.0
    avg_loss_r = round(loss_r_sum / losses, 2) if losses > 0 else 0.0
    profit_factor = round(win_r_sum / loss_r_sum, 2) if loss_r_sum > 0 else (99.0 if win_r_sum > 0 else 1.0)
    expectancy = round((win_rate / 100.0 * avg_win_r) - ((1.0 - win_rate / 100.0) * avg_loss_r), 2)

    return {
        "open_positions": open_positions,
        "closed_positions": evaluated_closed[:40],
        "scorecard": {
            "total_trades": total_evaluated,
            "open_count": len(open_positions),
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
            "win_rate_pct": win_rate,
            "total_realized_r": round(total_r, 2),
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
        },
    }


def execute_actionable_position_from_alert(
    ticker: str,
    side: str = "LONG",
    strategy: str = "Swing",
    entry_price: Optional[float] = None,
    stop: Optional[float] = None,
    target: Optional[float] = None,
    quantity: float = 100.0,
    instrument_type: str = "EQUITY",
    trade_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """1-Click execution from alert or opportunity card into tracked position."""
    rec = open_position(
        ticker=ticker,
        side=side.upper(),
        strategy=strategy,
        entry_price=entry_price,
        stop=stop,
        target=target,
        alert_price=entry_price,
        quantity=quantity,
        remaining_quantity=quantity,
        instrument_type=instrument_type,
        trade_id=trade_id or f"{ticker.upper()}_{int(datetime.now().timestamp())}",
        last_eval=notes or "Opened directly from Actionable Opportunity Cockpit",
    )
    return {"status": "ok", "message": f"Opened position for {ticker.upper()}", "position": rec}


def manage_position_action(
    ticker: str,
    action: str,
    price: Optional[float] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute live position management action (Scale 50%, Trail BE, Close)."""
    sym = ticker.strip().upper()
    act = action.strip().upper()

    if act in ("SCALE_50", "SCALE", "TRIM"):
        scaled = scale_position(
            ticker=sym,
            scale_pct=0.5,
            fill_price=price,
            reason=reason or "Target 1 Scale Trim (50% profit lock)",
        )
        if not scaled:
            return {"status": "error", "message": f"Could not find open position for {sym} to scale"}
        return {"status": "ok", "message": f"Scaled 50% profit on {sym} and locked BE+ runner stop", "position": scaled}

    elif act in ("TRAIL_BE", "LOCK_BE", "RATCHET_BE"):
        # Update runner stop to entry + buffer
        state = load_state()
        rec = state.get(sym)
        if not rec:
            return {"status": "error", "message": f"Position {sym} not found"}
        entry = float(rec.get("entry_price") or 0.0)
        side = str(rec.get("side", "LONG")).upper()
        buf = 0.05
        be_stop = (entry + buf) if "LONG" in side else (entry - buf)
        rec["stop"] = be_stop
        rec["be_locked"] = True
        rec["runner_stop"] = be_stop
        state[sym] = rec
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        return {"status": "ok", "message": f"Ratcheted stop to Break-Even (${be_stop:.2f}) for {sym}", "position": rec}

    elif act in ("CLOSE", "FLATTEN", "EXIT"):
        closed = close_position(
            ticker=sym,
            exit_price=price,
            exit_reason=reason or "Manual Exit via Cockpit",
        )
        if not closed:
            return {"status": "error", "message": f"Position {sym} was not open"}
        return {"status": "ok", "message": f"Closed position on {sym}", "closed_record": closed}

    return {"status": "error", "message": f"Unknown action: {act}"}


def reconcile_open_positions() -> Dict[str, Any]:
    """Reconcile orphaned OPEN rows in SQLite against data/positions.json.
    Refuses when positions.json is empty or unreadable, and keys by row id.
    """
    active_state = load_state()
    if not active_state or not isinstance(active_state, dict):
        return {"status": "refused", "reason": "positions.json is empty or unreadable", "reconciled": 0}

    active_tickers = set(k.upper() for k in active_state.keys())
    if not active_tickers:
        return {"status": "refused", "reason": "No active positions found in positions.json", "reconciled": 0}

    reconciled_count = 0
    if DB_PATH.exists():
        with sqlite3.connect(str(DB_PATH), timeout=15.0) as conn:
            cur = conn.cursor()
            rows = cur.execute("SELECT rowid, symbol FROM positions WHERE status = 'OPEN'").fetchall()
            for rowid, sym in rows:
                if (sym or "").upper() not in active_tickers:
                    cur.execute(
                        "UPDATE positions SET status = 'CLOSED', exit_reason = 'RECONCILED_CLOSED', closed_at = datetime('now') WHERE rowid = ?",
                        (rowid,),
                    )
                    reconciled_count += 1
            conn.commit()

    return {"status": "success", "reconciled": reconciled_count}
