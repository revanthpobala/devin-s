"""
Single source of truth for OPEN positions.

Everything else (Google Sheets, the LLM playbook, the monitor threads) reads
from / writes to this file. An "open position" is created when an ENTRY alert
is processed and managed autonomously by the PositionMonitor and Local LLM.
TradingView EXIT alerts are secondary telemetry; our autonomous engine owns
trade management, Target 1 50% scaling, Break-Even runner trailing, and
hard stop/invalidation exits.

Schema (data/positions.json):
{
  "AAPL": {
    "ticker": "AAPL",
    "side": "LONG",            # LONG | SHORT
    "strategy": "Intraday",    # Intraday | Swing | Daily
    "entry_price": 212.4,
    "stop": 209.1,
    "target": 218.0,
    "alert_price": 212.4,
    "opened_at": "2026-07-17T10:32:00-04:00",
    "last_price": 212.4,
    "last_eval": "...playbook text...",
    "last_eval_at": "2026-07-17T10:45:00-04:00",
    "scaled_at_t1": false,
    "runner_stop": null,
    "realized_pnl": 0.0,
    "breached_stop": false,
    "raw_alert": { ... }       # original alert payload, for context
  }
}

The file is written atomically (temp file + rename) so a crash mid-write can
never leave a half-corrupted state that would break the monitor on next boot.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger(__name__)

# Serializes all state mutations: the router thread, monitor threads, and the
# rehydrate-on-startup path all touch this file; without the lock a concurrent
# write could clobber another (last-writer-wins on the whole file).
_state_lock = threading.Lock()

POSITIONS_FILE = config.BASE_DIR / "data" / "positions.json"


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")


def load_state() -> dict:
    """Return the full positions dict (ticker -> position). Empty if missing."""
    if not POSITIONS_FILE.exists():
        return {}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Failed to load positions state from {POSITIONS_FILE}: {e}")
        return {}


def _save_state(state: dict) -> None:
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POSITIONS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    try:
        tmp.replace(POSITIONS_FILE)  # atomic on Windows + POSIX
    except OSError:
        tmp.unlink()
        raise


def open_position(
    ticker: str,
    *,
    side: str,
    strategy: str,
    entry_price: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    alert_price: float | None = None,
    scaled_at_t1: bool = False,
    peak_price: float | None = None,
    be_locked: bool = False,
    raw_alert: dict | None = None,
    **extra,
) -> dict:
    """Create or replace the open position for `ticker`.

    Returns the new position record. Re-opening an already-open ticker replaces
    it (latest alert wins) — the monitor thread for the old one is expected to
    be stopped by the caller."""
    ticker = ticker.strip().upper()
    now = _now_iso()
    qty = float(extra.get("quantity") or 100.0)
    rem_qty = float(extra.get("remaining_quantity") or qty)
    mult = int(extra.get("multiplier") or (100 if extra.get("instrument_type") == "OPTION" else 1))

    with _state_lock:
        state = load_state()
        rec = state.get(ticker, {})
        rec.update(
            {
                "ticker": ticker,
                "side": side,
                "strategy": strategy,
                "entry_price": entry_price if entry_price is not None else rec.get("entry_price"),
                "stop": stop if stop is not None else rec.get("stop"),
                "target": target if target is not None else rec.get("target"),
                "initial_stop": stop if stop is not None else rec.get("initial_stop", stop),
                "initial_target": target if target is not None else rec.get("initial_target", target),
                "alert_price": alert_price if alert_price is not None else rec.get("alert_price"),
                "opened_at": rec.get("opened_at", now),
                "last_price": rec.get("last_price", entry_price),
                "last_eval": rec.get("last_eval", ""),
                "last_eval_at": rec.get("last_eval_at", ""),
                "scaled_at_t1": scaled_at_t1 or rec.get("scaled_at_t1", False),
                "be_locked": be_locked or rec.get("be_locked", False),
                "peak_price": peak_price if peak_price is not None else rec.get("peak_price", entry_price),
                "runner_stop": rec.get("runner_stop", None),
                "realized_pnl": rec.get("realized_pnl", 0.0),
                "realized_broker_pnl": rec.get("realized_broker_pnl", 0.0),
                "breached_stop": False,
                "quantity": qty,
                "remaining_quantity": rem_qty,
                "multiplier": mult,
                "instrument_type": extra.get("instrument_type", "EQUITY"),
                "mode": extra.get("mode", "MODEL"),
                "trade_id": extra.get("trade_id") or rec.get("trade_id") or f"{ticker}_{int(time.time())}",
                "raw_alert": raw_alert or rec.get("raw_alert", {}),
            }
        )
        rec.update(extra)
        state[ticker] = rec
        _save_state(state)
    logger.info(f"[state] OPEN {ticker} {side} @ {entry_price} (stop={stop}, target={target}, strategy={strategy})")
    try:
        from src.tracking.alert_db import sync_position, record_trade_event
        sync_position(ticker, rec)
        record_trade_event({
            "trade_id": rec.get("trade_id"),
            "setup_id": rec.get("setup_id", rec.get("trade_id")),
            "strategy_id": strategy,
            "mode": rec.get("mode", "MODEL"),
            "event_type": "ENTRY",
            "symbol": ticker,
            "price": entry_price,
            "quantity": qty,
            "remaining_quantity": rem_qty,
            "instrument_type": rec.get("instrument_type", "EQUITY"),
            "multiplier": mult,
            "stop_level": stop,
            "target_1": target,
            "details": {"strategy": strategy, "side": side},
        })
    except Exception as e:
        logger.debug(f"Failed syncing position / event to alert_db: {e}")
    return rec


def scale_position(ticker: str, scale_pct: float = 0.5, fill_price: float | None = None, reason: str = "T1_SCALE", atr: float | None = None) -> dict | None:
    """Scale out a portion (default 50%) of the position at Target 1, realizing partial profit
    and ratcheting runner stop to Break-Even + max($0.05, 0.1×ATR) buffer (Rule 4.1.1 Golden Lock).

    If atr is provided, the BE+ buffer scales with volatility; otherwise falls back to $0.05 flat.
    """
    ticker = ticker.strip().upper()
    now = _now_iso()
    with _state_lock:
        state = load_state()
        rec = state.get(ticker)
        if rec is None:
            return None

        entry = rec.get("entry_price") or 0.0
        side = str(rec.get("side", "LONG")).upper()
        px = fill_price if fill_price is not None else (rec.get("last_price") or entry)

        # Quantity-aware scaling
        cur_qty = float(rec.get("remaining_quantity") or rec.get("quantity") or 100.0)
        scale_qty = round(cur_qty * scale_pct, 2)
        rem_qty = round(cur_qty - scale_qty, 2)
        mult = int(rec.get("multiplier") or 1)

        # Calculate realized P&L on scaled portion
        pts = (px - entry) if "LONG" in side else (entry - px)
        realized_add = round(pts * scale_qty * mult, 2)
        prior_pnl = rec.get("realized_pnl", 0.0) or 0.0
        total_realized = round(prior_pnl + realized_add, 2)

        # Move runner stop to BE+ max($0.05, 0.1×ATR) (Golden Lock)
        be_buffer = max(0.05, 0.1 * atr) if atr and atr > 0 else 0.05
        runner_stop = round(entry + be_buffer, 2) if "LONG" in side else round(entry - be_buffer, 2)

        rec["scaled_at_t1"] = True
        rec["remaining_quantity"] = rem_qty
        rec["runner_stop"] = runner_stop
        rec["stop"] = runner_stop
        rec["realized_pnl"] = total_realized
        rec["last_price"] = px
        rec["last_eval"] = (
            f"🎯 SCALED {int(scale_pct*100)}% ({scale_qty} units) at ${px:.2f} (+${realized_add:.2f}). "
            f"Runner stop locked at BE+ (${runner_stop:.2f}). Total Realized: ${total_realized:+.2f}"
        )
        rec["last_eval_at"] = now

        state[ticker] = rec
        _save_state(state)

    logger.info(
        f"[state] 🎯 SCALED {ticker} {int(scale_pct*100)}% @ ${px:.2f} (+${realized_add:.2f}) — "
        f"runner stop ratcheted to BE+ (${runner_stop:.2f})."
    )
    try:
        from src.tracking.alert_db import sync_position, record_trade_event
        sync_position(ticker, rec)
        record_trade_event({
            "trade_id": rec.get("trade_id") or f"{ticker}_trade",
            "setup_id": rec.get("setup_id", rec.get("trade_id")),
            "strategy_id": rec.get("strategy", "Intraday"),
            "mode": rec.get("mode", "MODEL"),
            "event_type": "SCALE_OUT",
            "symbol": ticker,
            "price": px,
            "quantity": scale_qty,
            "remaining_quantity": rem_qty,
            "instrument_type": rec.get("instrument_type", "EQUITY"),
            "multiplier": mult,
            "stop_level": runner_stop,
            "details": {"scale_pct": scale_pct, "realized_add": realized_add, "reason": reason},
        })
    except Exception as e:
        logger.debug(f"Failed syncing scaled position / event to alert_db: {e}")
    return rec


def close_position(ticker: str, exit_price: float | None = None, exit_reason: str | None = None) -> dict | None:
    """Remove `ticker` from open positions and record final execution metrics.
    Returns the closed record or None if it wasn't open."""
    ticker = ticker.strip().upper()
    now = _now_iso()
    with _state_lock:
        state = load_state()
        rec = state.pop(ticker, None)
        if rec is None:
            logger.info(f"[state] close requested for {ticker} but not in open state.")
            return None

        # Capture exit telemetry
        entry = rec.get("entry_price") or 0.0
        side = str(rec.get("side", "LONG")).upper()
        px = exit_price if exit_price is not None else rec.get("last_price") or entry

        rec["closed_at"] = now
        rec["exit_price"] = px
        rec["exit_reason"] = exit_reason or "MANUAL_CLOSE"

        # Calculate final net P&L with quantity, multiplier, fees, and slippage
        rem_qty = float(rec.get("remaining_quantity") or rec.get("quantity") or 100.0)
        mult = int(rec.get("multiplier") or 1)
        fees = float(rec.get("fees") or 0.0)
        slippage = float(rec.get("slippage") or 0.0)

        pts = (px - entry) if "LONG" in side else (entry - px)
        runner_pnl = round(pts * rem_qty * mult, 2)
        realized_prior = rec.get("realized_pnl", 0.0) or 0.0
        total_pnl = round(realized_prior + runner_pnl - fees - slippage, 2)
        rec["remaining_quantity"] = 0.0
        # Calculate exit_r on underlying
        initial_stop = rec.get("initial_stop") or rec.get("stop") or 0.0
        risk_dist = abs(entry - initial_stop) if entry and initial_stop else 0.0
        exit_r = round(pts / risk_dist, 4) if risk_dist > 0 else 0.0
        rec["exit_r"] = exit_r

        _save_state(state)

    try:
        from src.tracking.alert_db import sync_position, record_trade_event, record_intraday_exit
        sync_position(ticker, rec)
        record_trade_event({
            "trade_id": rec.get("trade_id") or f"{ticker}_trade",
            "setup_id": rec.get("setup_id", rec.get("trade_id")),
            "strategy_id": rec.get("strategy", "Intraday"),
            "mode": rec.get("mode", "MODEL"),
            "event_type": "EXIT",
            "symbol": ticker,
            "price": px,
            "quantity": rem_qty,
            "remaining_quantity": 0.0,
            "instrument_type": rec.get("instrument_type", "EQUITY"),
            "multiplier": mult,
            "stop_level": rec.get("stop"),
            "target_level": rec.get("target"),
            "details": {"exit_reason": rec["exit_reason"], "net_pnl": total_pnl, "runner_pnl": runner_pnl, "fees": fees, "slippage": slippage, "exit_r": exit_r},
        })
        trade_id = rec.get("trade_id")
        record_intraday_exit(
            trade_id=trade_id,
            exit_r=exit_r,
            exit_why=rec["exit_reason"],
            ticker=ticker,
            date=str(rec.get("opened_at", ""))[:10],
        )
        try:
            from src.tracking.alert_db import get_day_session_r
            from src.tracking.alert_evaluator import format_intraday_exit_push, notify_push
            date_today = str(rec.get("opened_at", ""))[:10]
            session_r = get_day_session_r(date_today)
            exit_push = format_intraday_exit_push(ticker, exit_r=exit_r, session_r=session_r, reason=rec.get("exit_reason", ""))
            notify_push(exit_push)
        except Exception as e_epush:
            logger.debug(f"Failed to emit exit push for {ticker}: {e_epush}")
    except Exception as e:
        logger.debug(f"Failed syncing closed position / event to alert_db: {e}")

    logger.info(
        f"[state] CLOSED {ticker} @ ${px} (Net P&L: ${rec.get('net_pnl', 0.0):+.2f}, "
        f"Reason: {rec.get('exit_reason')}, was open since {rec.get('opened_at')})"
    )
    return rec


def update_position(ticker: str, **fields) -> dict | None:
    """Patch arbitrary fields (last_price, last_eval, last_eval_at, breached_stop)."""
    ticker = ticker.strip().upper()
    with _state_lock:
        state = load_state()
        rec = state.get(ticker)
        if rec is None:
            return None
        rec.update(fields)
        state[ticker] = rec
        _save_state(state)
    try:
        from src.tracking.alert_db import sync_position
        sync_position(ticker, rec)
    except Exception as e:
        logger.debug(f"Failed syncing updated position to alert_db: {e}")
    return rec


def get_position(ticker: str) -> dict | None:
    return load_state().get(ticker.strip().upper())


def flatten_eod_intraday_positions(force: bool = False) -> list[dict]:
    """Close all intraday positions for EOD flattening (or older than today if force=False)."""
    closed_list = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    with _state_lock:
        state = load_state()
        tickers_to_close = []
        for t, p in state.items():
            if not isinstance(p, dict):
                continue
            strat = (p.get("strategy") or "").lower()
            opened_at = p.get("opened_at") or ""
            is_old = opened_at and not opened_at.startswith(today_str)
            if strat == "intraday" and (force or is_old):
                tickers_to_close.append(t)

        now_iso = _now_iso()
        for t in tickers_to_close:
            rec = state.pop(t, None)
            if rec:
                entry = rec.get("entry_price") or 0.0
                side = str(rec.get("side", "LONG")).upper()
                px = rec.get("last_price") or entry

                rec["closed_at"] = now_iso
                rec["exit_price"] = px
                rec["exit_reason"] = "EOD Flatten"

                rem_qty = float(rec.get("remaining_quantity") or rec.get("quantity") or 100.0)
                mult = int(rec.get("multiplier") or 1)
                fees = float(rec.get("fees") or 0.0)
                slippage = float(rec.get("slippage") or 0.0)
                pts = (px - entry) if "LONG" in side else (entry - px)
                runner_pnl = round(pts * rem_qty * mult, 2)
                realized_prior = rec.get("realized_pnl", 0.0) or 0.0
                total_pnl = round(realized_prior + runner_pnl - fees - slippage, 2)
                rec["remaining_quantity"] = 0.0
                rec["net_pnl"] = total_pnl

                closed_list.append(rec)
                try:
                    from src.tracking.alert_db import sync_position, record_trade_event
                    sync_position(t, rec)
                    record_trade_event({
                        "trade_id": rec.get("trade_id") or f"{t}_trade",
                        "setup_id": rec.get("setup_id", rec.get("trade_id")),
                        "strategy_id": rec.get("strategy", "Intraday"),
                        "mode": rec.get("mode", "MODEL"),
                        "event_type": "EOD_FLATTEN",
                        "symbol": t,
                        "price": px,
                        "quantity": rem_qty,
                        "remaining_quantity": 0.0,
                        "instrument_type": rec.get("instrument_type", "EQUITY"),
                        "multiplier": mult,
                        "details": {"exit_reason": "EOD Flatten", "net_pnl": total_pnl},
                    })
                except Exception as e:
                    logger.debug(f"Failed syncing EOD flattened position / event to alert_db: {e}")
        if tickers_to_close:
            _save_state(state)
            logger.info(f"[state] EOD Flattened {len(closed_list)} intraday position(s): {tickers_to_close}")
    return closed_list


def list_open() -> dict:
    return load_state()
