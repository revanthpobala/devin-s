"""Material-change gate: re-run deep research only when the prior plan is stale or the picture changed."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional, Tuple

from src import config

MAX_AGE_SESSIONS = 5
MOVE_ATR_TRIGGER = 1.0


def _sessions_between(d0: date, d1: date) -> int:
    n, d = 0, d0
    while d < d1:
        d = date.fromordinal(d.toordinal() + 1)
        if d.weekday() < 5:
            n += 1
    return n


def last_report_date(ticker: str, before: str) -> Optional[str]:
    root = config.BASE_DIR / "reports"
    if not root.exists():
        return None
    for d in sorted((p.name for p in root.iterdir() if p.is_dir() and p.name < before), reverse=True):
        if (root / d / f"{ticker}_watch_levels.json").exists():
            return d
    return None


def _live_price(ticker: str) -> Optional[float]:
    try:
        from src.clients.price_client import get_current_prices_batch

        px = (get_current_prices_batch([ticker], context="surveillance") or {}).get(ticker)
        return float(px) if px else None
    except Exception:
        return None


def _prior_close_and_daily_atr(ticker: str, d: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        from src.data.datawindow_loader import load_datawindow

        dw = load_datawindow(ticker, d) or {}
        close = float(dw.get("close") or 0) or None
        em = float(dw.get("Exp Move Pct 21b") or 0)
        atr = close * em / 100.0 / math.sqrt(21) if close and em else None
        return close, atr
    except Exception:
        return None, None


def needs_fresh_research(ticker: str, today: str, held: bool = False) -> Tuple[bool, str]:
    """True when no recent plan exists, the plan is >= 5 sessions old, or price moved >= 1 ATR since it. Held names always refresh."""
    ticker = ticker.upper()
    if held:
        return True, "held position"
    prior = last_report_date(ticker, today)
    if not prior:
        return True, "no prior plan"
    age = _sessions_between(datetime.strptime(prior, "%Y-%m-%d").date(), datetime.strptime(today, "%Y-%m-%d").date())
    if age >= MAX_AGE_SESSIONS:
        return True, f"plan {age} sessions old"
    close, atr = _prior_close_and_daily_atr(ticker, prior)
    px = _live_price(ticker)
    if close and atr and px and abs(px - close) >= MOVE_ATR_TRIGGER * atr:
        return True, f"moved {abs(px - close) / atr:.1f} ATR since {prior}"
    return False, f"plan from {prior} still valid"
