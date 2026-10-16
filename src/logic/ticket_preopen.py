"""Pre-open check of open tickets: GO / STAND DOWN from the live quote against the ticket's own limits, plus the news delta."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def evaluate_ticket(t: Dict[str, Any], price: float) -> Dict[str, Any]:
    stop, limit, cap = float(t["stop"]), float(t["limit_price"]), float(t["max_chase_price"])
    if price <= stop:
        verdict, why = "STAND DOWN", f"{price:.2f} at or through stop {stop:.2f}"
    elif price > cap:
        verdict, why = "STAND DOWN", f"{price:.2f} above chase cap {cap:.2f}; do not chase, limit {limit:.2f} stays resting"
    elif price <= limit:
        verdict, why = "GO", f"{price:.2f} at or below limit {limit:.2f}, fills"
    else:
        verdict, why = "RESTING", f"{price:.2f} above limit {limit:.2f}, inside chase cap {cap:.2f}; limit rests"
    risk = max(price - stop, 0.01)
    return {"verdict": verdict, "why": why, "rr_now": round((float(t["target_1"]) - price) / risk, 2)}


def _news_delta(ticker: str, since: str) -> List[str]:
    try:
        from src.clients.news_client import get_fresh_news

        txt = get_fresh_news(ticker, since, days=2)
        return [l.strip() for l in txt.splitlines() if l.strip()][:3]
    except Exception:
        return []


def _open_tickets() -> List[Dict[str, Any]]:
    from src.ui.routes.desk import get_tickets

    return get_tickets(days=14)["tickets"]


def run_preopen(price_fn=None) -> List[Dict[str, Any]]:
    from src.tracking.notify import notify

    if price_fn is None:
        from src.clients.price_client import get_current_prices_batch

        price_fn = lambda syms: get_current_prices_batch(syms, context="surveillance") or {}
    tickets = _open_tickets()
    if not tickets:
        return []
    prices = price_fn([t["ticker"] for t in tickets])
    out = []
    for t in tickets:
        px = prices.get(t["ticker"])
        if not px:
            continue
        res = {"ticker": t["ticker"], **evaluate_ticket(t, float(px))}
        res["news"] = _news_delta(t["ticker"], t["date"])
        out.append(res)
        body = f"{res['verdict']}: {res['why']} | R:R now {res['rr_now']}" + (f" | news: {res['news'][0]}" if res["news"] else "")
        notify("ENTRY" if res["verdict"] == "GO" else "RISK", f"{t['ticker']} pre-open {res['verdict']}", body,
               dedupe_key=f"preopen:{t['ticker']}:{datetime.now(ET):%Y-%m-%d}:{res['verdict']}")
    return out


_last_run: Optional[str] = None


def maybe_run_preopen() -> None:
    """Once per weekday between 09:25 and 09:35 ET."""
    global _last_run
    now = datetime.now(ET)
    day = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5 or _last_run == day or not (now.replace(hour=9, minute=25, second=0) <= now <= now.replace(hour=9, minute=35, second=0)):
        return
    _last_run = day
    try:
        run_preopen()
    except Exception as e:
        logger.warning(f"preopen run failed: {e}")
