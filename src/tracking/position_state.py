"""
Single source of truth for OPEN positions.

Everything else (Google Sheets, the LLM playbook, the monitor threads) reads
from / writes to this file. An "open position" is created when a TradingView
ENTRY alert is processed and removed when the matching EXIT alert arrives. The
LLM never authorizes an exit — the exit is always a TradingView alert, exactly
like the entry.

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
    "breached_stop": false,
    "raw_alert": { ... }       # original alert payload, for context
  }
}

The file is written atomically (temp file + rename) so a crash mid-write can
never leave a half-corrupted state that would break the monitor on next boot.
"""

import json
import logging
import threading
from datetime import datetime, timezone

from src import config

logger = logging.getLogger(__name__)

# Serializes all state mutations: the router thread, monitor threads, and the
# rehydrate-on-startup path all touch this file; without the lock a concurrent
# write could clobber another (last-writer-wins on the whole file).
_state_lock = threading.Lock()

POSITIONS_FILE = config.BASE_DIR / "data" / "positions.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    tmp.replace(POSITIONS_FILE)  # atomic on Windows + POSIX


def open_position(
    ticker: str,
    *,
    side: str,
    strategy: str,
    entry_price: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    alert_price: float | None = None,
    raw_alert: dict | None = None,
) -> dict:
    """Create or replace the open position for `ticker`.

    Returns the new position record. Re-opening an already-open ticker replaces
    it (latest alert wins) — the monitor thread for the old one is expected to
    be stopped by the caller."""
    ticker = ticker.strip().upper()
    now = _now_iso()
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
                "alert_price": alert_price if alert_price is not None else rec.get("alert_price"),
                "opened_at": rec.get("opened_at", now),
                "last_price": rec.get("last_price", entry_price),
                "last_eval": rec.get("last_eval", ""),
                "last_eval_at": rec.get("last_eval_at", ""),
                "breached_stop": False,
                "raw_alert": raw_alert or rec.get("raw_alert", {}),
            }
        )
        state[ticker] = rec
        _save_state(state)
    logger.info(f"[state] OPEN {ticker} {side} @ {entry_price} (strategy={strategy})")
    return rec


def close_position(ticker: str) -> dict | None:
    """Remove `ticker` from open positions. Returns the closed record (for the
    monitor to log to Sheets) or None if it wasn't open."""
    ticker = ticker.strip().upper()
    with _state_lock:
        state = load_state()
        rec = state.pop(ticker, None)
        if rec is None:
            logger.info(f"[state] close requested for {ticker} but not in open state.")
            return None
        _save_state(state)
    logger.info(f"[state] CLOSED {ticker} (was open since {rec.get('opened_at')})")
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
    return rec


def get_position(ticker: str) -> dict | None:
    return load_state().get(ticker.strip().upper())


def list_open() -> dict:
    return load_state()
