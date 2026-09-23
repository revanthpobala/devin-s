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


def _get_last_closed_session_date() -> str:
    """Return YYYY-MM-DD of the most recent completed market session."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    try:
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now()

    # If today is Mon-Fri and past 16:15 ET, today's regular session has closed
    if now_et.weekday() < 5 and (now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 15)):
        return now_et.strftime("%Y-%m-%d")

    # Otherwise step back to previous weekday
    d = now_et.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def get_bars_since_date(symbol: str, start_date: str) -> Optional[Any]:
    """
    Fetch daily OHLC bars for a symbol from start_date to today (inclusive).
    Uses in-memory cache with 60s TTL, verifying that the cached range covers start_date.
    """
    sym = (symbol or "").upper().strip()
    if not sym or sym.startswith("^"):
        return None

    # If start_date is today or later, today's daily bar is not yet a closed historical bar in Yahoo Finance.
    # evaluate_setup_lifecycle_bars handles live intraday session extremes via session_low / session_high / live_price.
    today_str = datetime.now().strftime("%Y-%m-%d")
    if start_date >= today_str:
        return None

    now = time.time()
    cached = _BARS_CACHE.get(sym)
    if cached and (now - cached[0] < _BARS_CACHE_TTL):
        df = cached[1]
        if df is not None and not df.empty:
            try:
                # Only use cache if its earliest date is at or before start_date
                min_idx = df.index.min()
                min_date_str = str(min_idx)[:10]
                if min_date_str <= start_date:
                    sub = df[df.index >= start_date]
                    if not sub.empty:
                        return sub
            except Exception:
                pass
        elif df is None:
            # Negative cache hit — avoids hitting network every second for unavailable data
            return None

    # 1. Check local datawindow CSV first — only if it contains up to the last closed session
    try:
        from src import config
        import pandas as pd
        raw_root = config.BASE_DIR / "data" / "raw"
        if raw_root.exists():
            last_closed = _get_last_closed_session_date()
            for d in sorted(raw_root.glob("*/"), reverse=True):
                cand = d / sym / f"{sym}_datawindow.csv"
                if cand.exists():
                    df_csv = pd.read_csv(cand)
                    col_map = {c.lower(): c for c in df_csv.columns}
                    time_col = col_map.get("time") or col_map.get("date")
                    if time_col and "close" in col_map:
                        df_csv["date_str"] = df_csv[time_col].astype(str).str.slice(0, 10)
                        last_csv_date = str(df_csv["date_str"].max())[:10]
                        # A CSV scraped on signal day only holds the signal bar; require it to cover up to last closed session
                        if last_csv_date >= last_closed and (df_csv["date_str"] >= start_date).any():
                            sub = df_csv[df_csv["date_str"] >= start_date].copy()
                            sub.set_index("date_str", inplace=True)
                            rename_cols = {}
                            for orig, standard in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")]:
                                if orig in col_map:
                                    rename_cols[col_map[orig]] = standard
                            sub.rename(columns=rename_cols, inplace=True)
                            _BARS_CACHE[sym] = (now, sub)
                            return sub
    except Exception as e_csv:
        logger.debug(f"Local CSV check failed for {sym}: {e_csv}")

    # 2. Fallback to yfinance with silenced logger
    try:
        yf_l = logging.getLogger("yfinance")
        yf_l.setLevel(logging.CRITICAL)
        yf_l.propagate = False
        import yfinance as yf
        import pandas as pd
        df = yf.download(sym, start=start_date, progress=False)
        if df is not None and not df.empty:
            if hasattr(df, "columns") and isinstance(df.columns, pd.MultiIndex):
                df = df.copy()
                df.columns = [c[0] for c in df.columns]
            _BARS_CACHE[sym] = (now, df)
            return df
        else:
            _BARS_CACHE[sym] = (now, None)
    except Exception as e:
        logger.debug(f"Failed fetching historical bars for {sym} via yfinance: {e}")
        _BARS_CACHE[sym] = (now, None)

    return None


def evaluate_setup_lifecycle_bars(
    bars: Optional[Any],
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
    max_holding_bars: Optional[int] = None,
    strategy_id: Optional[str] = None,
    opening_ceiling: Optional[float] = None,
    skip_setup_bar: bool = False,
) -> Dict[str, Any]:
    """
    Pure chronological bar-walking evaluation helper.
    Evaluates order eligibility, fill, stop, targets, and time exits bar by bar.
    """
    side = (side or "LONG").upper().strip()
    entry_type = (entry_type or "LIMIT").upper().strip()
    curr_st = (current_status or "STALKING").upper().strip()

    entry_low = float(entry_low or 0.0)
    entry_high = float(entry_high or 0.0)
    breakout_lvl = float(breakout_level or 0.0)
    stop_loss = float(stop_loss or 0.0)
    target_1 = float(target_1 or 0.0)
    target_2 = float(target_2 or 0.0)
    live_px = float(live_price or 0.0)

    # 1. Parse bars into a chronological list of dicts
    bar_records: List[Dict[str, Any]] = []
    if bars is not None and hasattr(bars, "iterrows"):
        try:
            if hasattr(bars, "columns") and getattr(bars.columns, "nlevels", 1) > 1:
                bars = bars.copy()
                bars.columns = [c[0] for c in bars.columns]
            for idx, row in bars.iterrows():
                d_str = str(idx)[:10]
                if setup_date and d_str < setup_date:
                    continue
                if skip_setup_bar and setup_date and d_str == setup_date:
                    continue
                o = float(row.get("Open") if "Open" in row else row.get("open", 0.0))
                h = float(row.get("High") if "High" in row else row.get("high", 0.0))
                l = float(row.get("Low") if "Low" in row else row.get("low", 0.0))
                c = float(row.get("Close") if "Close" in row else row.get("close", 0.0))
                if h > 0 and l > 0:
                    bar_records.append({
                        "date": d_str,
                        "open": o if o > 0 else l,
                        "high": h,
                        "low": l,
                        "close": c if c > 0 else l,
                        "is_live_session": False,
                    })
        except Exception as e_parse:
            logger.debug(f"Error iterating bar rows: {e_parse}")

    # Sort chronological
    bar_records.sort(key=lambda r: r["date"])

    # If live intraday session extremes are provided, append as current partial bar
    if (session_low is not None and session_low > 0) or (session_high is not None and session_high > 0) or live_px > 0:
        cur_l = float(session_low) if (session_low is not None and session_low > 0) else live_px
        cur_h = float(session_high) if (session_high is not None and session_high > 0) else live_px
        cur_c = live_px if live_px > 0 else cur_h
        cur_o = cur_c if cur_c > 0 else ((cur_l + cur_h) / 2.0 if (cur_l > 0 and cur_h > 0) else cur_l)
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # If the last record is already today, update its bounds; otherwise append
        if bar_records and bar_records[-1]["date"] == today_str:
            bar_records[-1]["high"] = max(bar_records[-1]["high"], cur_h)
            bar_records[-1]["low"] = min(bar_records[-1]["low"], cur_l)
            bar_records[-1]["close"] = cur_c
            bar_records[-1]["is_live_session"] = True
        else:
            bar_records.append({
                "date": today_str,
                "open": cur_o,
                "high": cur_h,
                "low": cur_l,
                "close": cur_c,
                "is_live_session": True,
            })

    # Track extremes across all observed bars
    all_lows = [b["low"] for b in bar_records] if bar_records else ([live_px] if live_px > 0 else [0.0])
    all_highs = [b["high"] for b in bar_records] if bar_records else ([live_px] if live_px > 0 else [0.0])
    min_low = min(all_lows) if all_lows else live_px
    max_high = max(all_highs) if all_highs else live_px

    # State machine variables
    was_filled = (curr_st == "IN_TRADE")
    fill_price = (entry_high if side == "LONG" else entry_low) if was_filled else 0.0
    fill_date: Optional[str] = setup_date if was_filled else None
    activation_date: Optional[str] = setup_date if was_filled else None
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    hit_t1 = False
    hit_t2 = False
    hit_stop = False
    hit_recovery = False
    hit_target_level: Optional[str] = None
    is_terminal = False
    is_reclaimed = False
    episode_index = 1
    bars_held = 0
    status = curr_st
    notes = ""

    # Pre-compute EMA5 across bar records for RSI2 recovery detection
    multiplier_5 = 2.0 / (5.0 + 1.0)
    cur_ema5 = None
    for b in bar_records:
        c_val = b["close"]
        if cur_ema5 is None:
            cur_ema5 = c_val
        else:
            cur_ema5 = (c_val - cur_ema5) * multiplier_5 + cur_ema5
        b["ema5"] = cur_ema5

    is_next_open_model = entry_type in ("NEXT_OPEN", "RSI2") or (strategy_id or "").upper() in ("RSI2", "RSI2_PULLBACK")
    recovery_due = False

    # 2. Chronological bar walk
    for bar in bar_records:
        b_date = bar["date"]
        b_open = bar["open"]
        b_high = bar["high"]
        b_low = bar["low"]
        b_close = bar["close"]

        if is_terminal:
            # Check for possible setup reclaim (creates a new episode)
            if status in ("STOP_BREACHED", "INVALIDATED"):
                if side == "LONG" and entry_low > 0 and entry_high > 0:
                    if entry_low <= b_close <= entry_high or (b_low <= entry_high and b_high >= entry_low):
                        is_reclaimed = True
            continue

        if not was_filled:
            # Check eligibility for fill on this bar
            eligible_for_fill = False
            pot_fill = 0.0

            if is_next_open_model:
                if setup_date and b_date <= setup_date:
                    # Setup bar forming/closing; eligible opening is strictly the next session
                    continue
                # Single next-open execution: must fill at open, or expire if ceiling exceeded
                if opening_ceiling and opening_ceiling > 0 and b_open > opening_ceiling:
                    status = "EXPIRED_CEILING"
                    is_terminal = True
                    notes = f"Expired: Next open (${b_open:.2f}) on {b_date} exceeded opening ceiling (${opening_ceiling:.2f})"
                    continue
                else:
                    eligible_for_fill = True
                    pot_fill = b_open
            elif entry_type == "BREAKOUT" and breakout_lvl > 0:
                if side == "LONG" and b_high >= breakout_lvl:
                    eligible_for_fill = True
                    pot_fill = max(b_open, breakout_lvl)
                elif side == "SHORT" and b_low <= breakout_lvl:
                    eligible_for_fill = True
                    pot_fill = min(b_open, breakout_lvl)
            elif entry_type == "MARKET":
                eligible_for_fill = True
                pot_fill = b_open
            else:
                # LIMIT / Dip buy (strict eligibility: price must reach limit level)
                if side == "LONG":
                    limit_ceil = entry_high if entry_high > 0 else entry_low
                    if limit_ceil > 0 and b_low <= limit_ceil:
                        eligible_for_fill = True
                        pot_fill = b_open if b_open <= limit_ceil else limit_ceil
                else:
                    limit_flr = entry_low if entry_low > 0 else entry_high
                    if limit_flr > 0 and b_high >= limit_flr:
                        eligible_for_fill = True
                        pot_fill = b_open if b_open >= limit_flr else limit_flr

            # Check target and stop triggers on this bar
            bar_hit_t2 = target_2 > 0 and ((side == "LONG" and b_high >= target_2) or (side == "SHORT" and b_low <= target_2))
            bar_hit_t1 = target_1 > 0 and ((side == "LONG" and b_high >= target_1) or (side == "SHORT" and b_low <= target_1))
            bar_hit_target = bar_hit_t2 or bar_hit_t1
            target_hit_name = "T2" if bar_hit_t2 else ("T1" if bar_hit_t1 else "")

            bar_hit_stop = False
            if stop_loss > 0:
                if side == "LONG" and b_low <= stop_loss:
                    bar_hit_stop = True
                elif side == "SHORT" and b_high >= stop_loss:
                    bar_hit_stop = True

            if not eligible_for_fill:
                # Did not fill. If target was reached before fill, it is MISSED_RUNAWAY
                if bar_hit_target:
                    status = "MISSED_RUNAWAY"
                    is_terminal = True
                    hit_t1 = bar_hit_t1
                    hit_t2 = bar_hit_t2
                    hit_target_level = target_hit_name
                    notes = f"Runaway: price reached target {target_hit_name} on {b_date} without filling entry"
                    continue
                elif bar_hit_stop:
                    status = "INVALIDATED"
                    is_terminal = True
                    hit_stop = True
                    exit_date = b_date
                    exit_price = b_open if (side == "LONG" and b_open <= stop_loss) else stop_loss
                    notes = f"Invalidated: breached stop (${stop_loss:.2f}) on {b_date} prior to entry fill"
                    continue
            else:
                # Eligible for fill on this bar
                # If price opened already past target, target hit before fill could execute -> runaway
                if side == "LONG" and target_1 > 0 and b_open >= target_1:
                    status = "MISSED_RUNAWAY"
                    is_terminal = True
                    hit_t1 = True
                    hit_target_level = "T1"
                    notes = f"Runaway: opened above target (${target_1:.2f}) on {b_date} before limit fill"
                    continue
                elif side == "SHORT" and target_1 > 0 and b_open <= target_1:
                    status = "MISSED_RUNAWAY"
                    is_terminal = True
                    hit_t1 = True
                    hit_target_level = "T1"
                    notes = f"Runaway: opened below target (${target_1:.2f}) on {b_date} before limit fill"
                    continue

                # Order fills!
                was_filled = True
                fill_price = pot_fill
                fill_date = b_date
                activation_date = setup_date or b_date
                status = "IN_TRADE"
                bars_held = 1

                # Evaluate same-bar stop and target following fill
                if bar_hit_stop and bar_hit_target:
                    # Path-accurate frozen convention: stop checked before target
                    status = "STOP_BREACHED"
                    hit_stop = True
                    is_terminal = True
                    exit_date = b_date
                    exit_price = stop_loss
                    notes = f"Same-bar stop/target ambiguity on {b_date}: stop-first convention resolved to STOP_BREACHED"
                    continue
                elif bar_hit_stop:
                    status = "STOP_BREACHED"
                    hit_stop = True
                    is_terminal = True
                    exit_date = b_date
                    exit_price = stop_loss
                    notes = f"Stop breached (${stop_loss:.2f}) on entry bar {b_date}"
                    continue
                elif bar_hit_target:
                    status = "TARGET_HIT"
                    if bar_hit_t2:
                        hit_t2 = True
                        hit_t1 = True
                        hit_target_level = "T2"
                        exit_price = target_2
                    else:
                        hit_t1 = True
                        hit_target_level = "T1"
                        exit_price = target_1
                    is_terminal = True
                    exit_date = b_date
                    notes = f"Target {hit_target_level} reached on entry bar {b_date}"
                    continue

                if is_next_open_model and bar.get("ema5") is not None:
                    if side == "LONG" and b_close > bar["ema5"]:
                        recovery_due = True
                    elif side == "SHORT" and b_close < bar["ema5"]:
                        recovery_due = True

        else:
            # Active in trade from a prior bar
            # Check for queued recovery exit from prior bar's close > EMA5
            if recovery_due:
                if stop_loss > 0 and ((side == "LONG" and b_open <= stop_loss) or (side == "SHORT" and b_open >= stop_loss)):
                    status = "STOP_BREACHED"
                    hit_stop = True
                    is_terminal = True
                    exit_date = b_date
                    exit_price = b_open
                    notes = f"Stop breached on gap open (${b_open:.2f}) before recovery exit on {b_date}"
                    continue
                else:
                    status = "RECOVERY_EXIT"
                    hit_recovery = True
                    hit_target_level = "RECOVERY"
                    is_terminal = True
                    exit_date = b_date
                    exit_price = b_open
                    notes = f"Recovery exit on next open (${b_open:.2f}) following close above EMA5 on {b_date}"
                    continue

            bars_held += 1

            # Check stop loss (including gap-through-stop)
            bar_hit_stop = False
            gap_through_stop = False
            if stop_loss > 0:
                if side == "LONG":
                    if b_low <= stop_loss:
                        bar_hit_stop = True
                        if b_open <= stop_loss:
                            gap_through_stop = True
                else:
                    if b_high >= stop_loss:
                        bar_hit_stop = True
                        if b_open >= stop_loss:
                            gap_through_stop = True

            # Check targets
            bar_hit_t2 = target_2 > 0 and ((side == "LONG" and b_high >= target_2) or (side == "SHORT" and b_low <= target_2))
            bar_hit_t1 = target_1 > 0 and ((side == "LONG" and b_high >= target_1) or (side == "SHORT" and b_low <= target_1))

            if bar_hit_stop and (bar_hit_t1 or bar_hit_t2):
                # Path-accurate frozen convention: stop checked before target
                status = "STOP_BREACHED"
                hit_stop = True
                is_terminal = True
                exit_date = b_date
                exit_price = b_open if gap_through_stop else stop_loss
                notes = f"Same-bar stop/target ambiguity on {b_date}: stop-first convention resolved to STOP_BREACHED"
                continue
            elif bar_hit_stop:
                status = "STOP_BREACHED"
                hit_stop = True
                is_terminal = True
                exit_date = b_date
                exit_price = b_open if gap_through_stop else stop_loss
                notes = f"Stop breached on {b_date} ({'gap open $' + str(round(b_open, 2)) if gap_through_stop else '$' + str(round(stop_loss, 2))})"
                continue
            elif bar_hit_t2:
                status = "TARGET_HIT"
                hit_t2 = True
                hit_t1 = True
                hit_target_level = "T2"
                is_terminal = True
                exit_date = b_date
                exit_price = target_2
                notes = f"Target 2 reached (${target_2:.2f}) after fill"
                continue
            elif bar_hit_t1:
                status = "TARGET_HIT"
                hit_t1 = True
                hit_target_level = "T1"
                is_terminal = True
                exit_date = b_date
                exit_price = target_1
                notes = f"Target 1 reached (${target_1:.2f}) after fill"
                continue

            # Check max holding bars (e.g. 10-bar time exit for RSI2)
            if max_holding_bars and bars_held >= max_holding_bars:
                status = "TIME_EXIT"
                is_terminal = True
                exit_date = b_date
                exit_price = b_close
                notes = f"Time exit reached ({max_holding_bars} bars held) on {b_date}"
                continue

            # Queue recovery exit if active position closed above EMA5
            if is_next_open_model and bar.get("ema5") is not None:
                if side == "LONG" and b_close > bar["ema5"]:
                    recovery_due = True
                elif side == "SHORT" and b_close < bar["ema5"]:
                    recovery_due = True

    # 3. Post-walk status resolution if still non-terminal
    if not is_terminal:
        if was_filled:
            # Active in trade
            if entry_low > 0 and entry_high > 0 and entry_low <= live_px <= entry_high:
                status = "IN_ZONE"
                notes = f"Spot in entry zone [${entry_low:.2f} - ${entry_high:.2f}]"
            else:
                status = "IN_TRADE"
                notes = f"Active position holding above stop (${stop_loss:.2f})"
        else:
            # Order never filled
            # If live price is directly inside entry zone right now
            if entry_low > 0 and entry_high > 0 and entry_low <= live_px <= entry_high:
                status = "IN_ZONE"
                notes = f"Spot in entry zone [${entry_low:.2f} - ${entry_high:.2f}]"
            else:
                status = "STALKING"
                dist_pct = round(((live_px - entry_high) / entry_high) * 100, 2) if entry_high > 0 and live_px > 0 else 0.0
                notes = f"Stalking: {dist_pct:+.1f}% from limit zone"

    # Compute unrealized / realized PnL %
    unrealized_pnl_pct = 0.0
    if was_filled and fill_price > 0:
        eval_px = exit_price if (is_terminal and exit_price is not None) else live_px
        if eval_px > 0:
            if side == "LONG":
                unrealized_pnl_pct = round(((eval_px - fill_price) / fill_price) * 100, 2)
            else:
                unrealized_pnl_pct = round(((fill_price - eval_px) / fill_price) * 100, 2)

    return {
        "status": status,
        "was_filled": was_filled,
        "fill_price": fill_price,
        "fill_date": fill_date,
        "activation_date": activation_date,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "hit_target_level": hit_target_level,
        "min_low": min_low,
        "max_high": max_high,
        "hit_t1": hit_t1,
        "hit_t2": hit_t2,
        "hit_stop": hit_stop,
        "hit_recovery": hit_recovery,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "bars_held": bars_held,
        "notes": notes,
        "is_terminal": is_terminal,
        "is_reclaimed": is_reclaimed,
        "episode_index": 2 if is_reclaimed else episode_index,
        "modeled_vs_observed": "MODELED",
    }


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
    max_holding_bars: Optional[int] = None,
    strategy_id: Optional[str] = None,
    opening_ceiling: Optional[float] = None,
    skip_setup_bar: bool = False,
) -> Dict[str, Any]:
    """
    Public seam for setup lifecycle evaluation.
    Fetches historical bars and delegates to evaluate_setup_lifecycle_bars.
    """
    sym = ticker.upper().strip()
    bars = None
    if setup_date and sym:
        bars = get_bars_since_date(sym, setup_date)

    return evaluate_setup_lifecycle_bars(
        bars=bars,
        setup_date=setup_date,
        side=side,
        entry_type=entry_type,
        entry_low=entry_low,
        entry_high=entry_high,
        breakout_level=breakout_level,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        live_price=live_price,
        session_low=session_low,
        session_high=session_high,
        current_status=current_status,
        proximity_tolerance_pct=proximity_tolerance_pct,
        max_holding_bars=max_holding_bars,
        strategy_id=strategy_id,
        opening_ceiling=opening_ceiling,
        skip_setup_bar=skip_setup_bar,
    )

