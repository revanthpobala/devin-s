"""
Intraday trading, open positions state, flattening, and 0DTE simulation endpoints.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.ui.state import POSITIONS_FILE, append_log

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["intraday"])


class Simulate0DTERequest(BaseModel):
    ticker: str
    action: str = "ENTER_CALLS"
    price: float = 560.0
    why_now: str = "15m Squeeze Breakout + VWAP reclaim"


@router.get("/api/positions")
def get_positions():
    """Fetch open positions with live quotes and PnL."""
    if not POSITIONS_FILE.exists():
        return {"positions": []}
    try:
        from src.clients.price_client import get_current_price
        today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            pos_dict = raw_data.get("positions", raw_data) if isinstance(raw_data, dict) else {}
            positions = []
            for sym, p in pos_dict.items():
                if not isinstance(p, dict):
                    continue
                item = dict(p)
                opened_at = item.get("opened_at", "")
                if opened_at and opened_at[:10] != today_str:
                    continue
                if str(item.get("raw_alert", {}).get("subject", "")).lower() == "alert: screener":
                    continue

                entry = item.get("entry_price") or item.get("alert_price") or 0.0
                try:
                    spot = get_current_price(sym)
                    if isinstance(spot, (int, float)) and spot > 0:
                        item["current_price"] = spot
                        side = item.get("side", "LONG").upper()
                        if entry > 0:
                            if "LONG" in side or "BUY" in side or "CALL" in side:
                                item["pnl_pct"] = round(((spot - entry) / entry) * 100, 2)
                            else:
                                item["pnl_pct"] = round(((entry - spot) / entry) * 100, 2)
                except Exception:
                    pass
                positions.append(item)
            return {"positions": positions}
    except Exception as e:
        return {"positions": [], "error": str(e)}


@router.get("/api/schwab/positions")
def get_schwab_positions_endpoint():
    """Fetch live broker account balances and positions from Schwab Trader API."""
    try:
        from src.clients.schwab_client import get_schwab_positions
        return get_schwab_positions()
    except Exception as e:
        return {"status": "error", "error": str(e), "accounts": []}


@router.post("/api/positions/{ticker}/close")
def close_open_position(ticker: str):
    """Close an open position from data/positions.json."""
    try:
        from src.tracking.position_state import close_position
        rec = close_position(ticker)
        if rec:
            append_log(f"🛑 Closed open position for {ticker.upper()}.")
            return {"success": True, "closed": rec}
        else:
            return {"success": False, "message": f"{ticker} was not open."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/positions/flatten-intraday")
def flatten_intraday_endpoint():
    """Flatten and close all intraday positions for EOD."""
    try:
        from src.tracking.position_state import flatten_eod_intraday_positions
        closed = flatten_eod_intraday_positions(force=True)
        append_log(f"🧹 EOD Flattened {len(closed)} intraday position(s).")
        return {"success": True, "count": len(closed), "closed": closed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/intraday/simulate-eval")
def simulate_0dte_eval(req: Simulate0DTERequest):
    """Simulate and test 0DTE rules evaluation (revanth-0dte.md) on demand."""
    try:
        from main import query_local_llm_for_trade
        fake_alert = {
            "ticker": req.ticker.strip().upper(),
            "action": req.action.strip().upper(),
            "price": req.price,
            "why_now": req.why_now,
            "strategy": "Simulated 0DTE",
            "time": datetime.now().isoformat(),
        }
        res = query_local_llm_for_trade(fake_alert)
        return {"status": "ok", "evaluation": res}
    except Exception as e:
        return {"status": "error", "error": str(e)}
