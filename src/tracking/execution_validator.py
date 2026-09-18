"""
Execution Validator & Bar-Based Fill Simulator
=============================================
Provides deterministic evaluation of trade setups against historical and live bar extremes (OHLC).
Ensures that dip buys and breakout trades are accurately audited for fills, target hits,
and invalidations across the lifetime of the setup, preventing false "MISSED RUNAWAY" alarms.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory cache for daily OHLC bars: {symbol: (fetch_timestamp, DataFrame)}
_BARS_CACHE: Dict[str, Tuple[float, Any]] = {}
_BARS_CACHE_TTL = 60.0  # seconds


def get_bars_since_date(symbol: str, start_date: str) -> Optional[Any]:
    """
    Fetch daily OHLC bars for a symbol from start_date to today (inclusive).
    Uses in-memory cache with 60s TTL.
    """
    sym = symbol.upper().strip()
    if not sym or sym.startswith("^"):
        return None

    now = time.time()
    cached = _BARS_CACHE.get(sym)
    if cached and (now - cached[0] < _BARS_CACHE_TTL):
        df = cached[1]
        if df is not None and not df.empty:
            try:
                sub = df[df.index >= start_date]
                if not sub.empty:
                    return sub
            except Exception:
                pass

    try:
        import yfinance as yf
        df = yf.download(sym, start=start_date, progress=False)
        if df is not None and not df.empty:
            _BARS_CACHE[sym] = (now, df)
            return df
    except Exception as e:
        logger.debug(f"Failed fetching historical bars for {sym} via yfinance: {e}")

    return None


def evaluate_setup_lifecycle(
    ticker: str,
    setup_date: str,
    side: str = "LONG",
    entry_type: str = "LIMIT",
    entry_low: float = 0.0,
    entry_high: float = 0.0,
    breakout_level: float = 0.0,
    stop_loss: float = 0.0,
    target_1: float = 0.0,
    target_2: float = 0.0,
    live_price: float = 0.0,
    session_low: Optional[float] = None,
    session_high: Optional[float] = None,
    current_status: str = "STALKING",
    proximity_tolerance_pct: float = 0.5,
) -> Dict[str, Any]:
    """
    Deterministically evaluates a setup's lifecycle from setup_date through current moment.

    Returns:
        {
            "status": "TARGET_HIT" | "IN_TRADE" | "IN_ZONE" | "STOP_BREACHED" | "MISSED_RUNAWAY" | "STALKING",
            "was_filled": bool,
            "fill_price": float,
            "min_low": float,
            "max_high": float,
            "hit_t1": bool,
            "hit_t2": bool,
            "hit_stop": bool,
            "unrealized_pnl_pct": float,
            "notes": str
        }
    """
    sym = ticker.upper().strip()
    side = (side or "LONG").upper()
    entry_type = (entry_type or "LIMIT").upper()
    curr_st = (current_status or "STALKING").upper()

    entry_low = float(entry_low or 0.0)
    entry_high = float(entry_high or 0.0)
    breakout_lvl = float(breakout_level or 0.0)
    stop_loss = float(stop_loss or 0.0)
    target_1 = float(target_1 or 0.0)
    target_2 = float(target_2 or 0.0)
    live_px = float(live_price or 0.0)

    # 1. Gather historical bar extremes from setup_date
    historical_lows: List[float] = []
    historical_highs: List[float] = []

    if setup_date:
        bars = get_bars_since_date(sym, setup_date)
        if bars is not None and not bars.empty:
            try:
                if "Low" in bars.columns:
                    historical_lows.extend([float(x) for x in bars["Low"].dropna().to_numpy().flatten()])
                if "High" in bars.columns:
                    historical_highs.extend([float(x) for x in bars["High"].dropna().to_numpy().flatten()])
            except Exception as e:
                logger.debug(f"Error parsing historical bars for {sym}: {e}")

    # Inject today's session extremes from live quote if available
    if session_low is not None and session_low > 0:
        historical_lows.append(float(session_low))
    if session_high is not None and session_high > 0:
        historical_highs.append(float(session_high))
    if live_px > 0:
        historical_lows.append(live_px)
        historical_highs.append(live_px)

    min_low = min(historical_lows) if historical_lows else live_px
    max_high = max(historical_highs) if historical_highs else live_px

    # Tolerance calculation (default 0.5% buffer)
    tol = proximity_tolerance_pct / 100.0

    # 2. Determine if Fill Occurred
    was_filled = False
    fill_price = 0.0

    # If already confirmed IN_TRADE, maintain filled state
    if curr_st == "IN_TRADE":
        was_filled = True
        fill_price = entry_high if side == "LONG" else entry_low

    if entry_type == "BREAKOUT" and breakout_lvl > 0:
        if side == "LONG":
            if max_high >= breakout_lvl:
                was_filled = True
                fill_price = breakout_lvl
        else:
            if min_low <= breakout_lvl:
                was_filled = True
                fill_price = breakout_lvl
    else:
        # Limit Order / Dip Buy Evaluation
        if side == "LONG":
            limit_ceiling = round(entry_high * (1.0 + tol), 2) if entry_high > 0 else 0.0
            if limit_ceiling > 0 and min_low <= limit_ceiling:
                was_filled = True
                fill_price = entry_high if entry_high > 0 else min_low
        else:
            limit_floor = round(entry_low * (1.0 - tol), 2) if entry_low > 0 else 0.0
            if limit_floor > 0 and max_high >= limit_floor:
                was_filled = True
                fill_price = entry_low if entry_low > 0 else max_high

    # If live spot is directly inside entry zone right now
    if not was_filled and entry_low > 0 and entry_high > 0:
        if entry_low <= live_px <= entry_high:
            was_filled = True
            fill_price = live_px

    # 3. Determine Stop and Target Hits
    hit_stop = False
    if stop_loss > 0:
        if side == "LONG" and min_low <= stop_loss:
            hit_stop = True
        elif side == "SHORT" and max_high >= stop_loss:
            hit_stop = True

    hit_t1 = False
    if target_1 > 0:
        if side == "LONG" and max_high >= target_1:
            hit_t1 = True
        elif side == "SHORT" and min_low <= target_1:
            hit_t1 = True

    hit_t2 = False
    if target_2 > 0:
        if side == "LONG" and max_high >= target_2:
            hit_t2 = True
        elif side == "SHORT" and min_low <= target_2:
            hit_t2 = True

    # 4. Resolve Deterministic Status
    status = curr_st
    notes = ""

    if was_filled:
        if hit_t2:
            status = "TARGET_HIT"
            notes = f"Target 2 reached (${target_2:.2f}) after fill"
        elif hit_t1:
            status = "TARGET_HIT"
            notes = f"Target 1 reached (${target_1:.2f}) after fill"
        elif hit_stop:
            status = "STOP_BREACHED"
            notes = f"Stop breached (${stop_loss:.2f}) after fill"
        else:
            # Active in trade
            # If spot is currently inside entry zone
            if entry_low > 0 and entry_high > 0 and entry_low <= live_px <= entry_high:
                status = "IN_ZONE"
                notes = f"Spot in entry zone [${entry_low:.2f} - ${entry_high:.2f}]"
            else:
                status = "IN_TRADE"
                notes = f"Active position holding above stop (${stop_loss:.2f})"
    else:
        # Order never filled
        if hit_t1 or hit_t2:
            # Reached target without filling limit
            status = "MISSED_RUNAWAY"
            notes = f"Runaway: price reached target (${target_1:.2f}) without filling limit"
        elif hit_stop:
            status = "INVALIDATED"
            notes = f"Invalidated: breached stop (${stop_loss:.2f}) prior to entry fill"
        else:
            status = "STALKING"
            dist_pct = round(((live_px - entry_high) / entry_high) * 100, 2) if entry_high > 0 and live_px > 0 else 0.0
            notes = f"Stalking: {dist_pct:+.1f}% from limit zone"

    # Compute unrealized PnL % if filled
    unrealized_pnl_pct = 0.0
    if was_filled and fill_price > 0 and live_px > 0:
        if side == "LONG":
            unrealized_pnl_pct = round(((live_px - fill_price) / fill_price) * 100, 2)
        else:
            unrealized_pnl_pct = round(((fill_price - live_px) / fill_price) * 100, 2)

    return {
        "status": status,
        "was_filled": was_filled,
        "fill_price": fill_price,
        "min_low": min_low,
        "max_high": max_high,
        "hit_t1": hit_t1,
        "hit_t2": hit_t2,
        "hit_stop": hit_stop,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "notes": notes,
    }
