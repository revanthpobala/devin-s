"""
src/logic/buy_precedent.py

Historical Buy-Precedent Lookup

When a Code 20 (REVERSAL BUY) state is active or nearly active, we need to know
how this specific ticker behaved during its LAST Code 20 state (if any).
Did it rally? Did it trap? Did the expected edge materialize?

This module searches the local `data/positions.json` (open trades) and
historical data to extract the nearest buy precedent for the ticker.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional
import os

from src import config

def get_latest_buy_precedent(ticker: str) -> Optional[Dict[str, Any]]:
    """Finds the most recent Code 20 (or ACTION) long trade for this ticker
    and returns a summary of its performance.

    Returns None if no precedent is found.
    """
    ticker = ticker.upper()
    positions_file = config.BASE_DIR / "data" / "positions.json"
    
    if not positions_file.exists():
        return None
        
    try:
        with open(positions_file, "r", encoding="utf-8") as f:
            positions = json.load(f)
    except Exception:
        return None
        
    # Search for an open long position first
    for pos_id, pos in positions.get("open_positions", {}).items():
        if pos.get("ticker") == ticker and pos.get("side") == "LONG":
            return {
                "date": pos.get("entry_date", "unknown"),
                "status": "OPEN",
                "entry": pos.get("entry_price"),
                "current": pos.get("last_eval_price"),
                "pnl_pct": pos.get("unrealized_pnl_pct"),
            }
            
    # Search historical (closed) positions (if stored in a similar format)
    # The current positions.json mainly tracks open trades, but this provides
    # the hook for full attribution lookup once the schema supports it.
    for pos_id, pos in positions.get("closed_positions", {}).items():
        if pos.get("ticker") == ticker and pos.get("side") == "LONG":
            # For now, return the most recent closed long
            return {
                "date": pos.get("entry_date", "unknown"),
                "status": "CLOSED",
                "entry": pos.get("entry_price"),
                "exit": pos.get("exit_price"),
                "pnl_pct": pos.get("realized_pnl_pct"),
            }

    return None

def format_buy_precedent(ticker: str, precedent: Optional[Dict[str, Any]]) -> str:
    """Formats the precedent dict into a string for the LLM prompt."""
    if not precedent:
        return "(No recent long precedent found in local tracker)"
        
    status = precedent.get("status")
    date = precedent.get("date")
    pnl = precedent.get("pnl_pct")
    
    if pnl is not None:
        pnl_str = f"{pnl:+.2f}%"
    else:
        pnl_str = "unknown %"
        
    if status == "OPEN":
        return f"OPEN long position from {date} (Currently {pnl_str})"
    else:
        return f"CLOSED long position from {date} (Result: {pnl_str})"
