"""Executable ticket from a gated long plan: what to place, the chase limit, when it dies, the stance and the lane prior."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

from src import config
from src.tracking import trade_rules

logger = logging.getLogger(__name__)


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _add_sessions(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def build_ticket(watch_data: Dict[str, Any], spot: float, date_str: str) -> Optional[Dict[str, Any]]:
    sp = watch_data.get("shares_plan") or {}
    if str(watch_data.get("side") or "LONG").upper() != "LONG":
        return None
    if "CASH_SKIP" in str(watch_data.get("verdict", "")).upper() or not watch_data.get("actionable"):
        return None
    stop, t1, t2 = _f(sp.get("tactical_stop")), _f(sp.get("target_1")), _f(sp.get("target_2"))
    hi, lo = _f(sp.get("entry_zone_high")), _f(sp.get("entry_zone_low"))
    if not (stop > 0 and t1 > stop and (hi > 0 or spot > 0)):
        return None
    entry_max = _f(sp.get("entry_max")) or trade_rules.entry_max_for_rr(t1, stop)
    limit = min(hi, entry_max) if hi > 0 else min(spot, entry_max)
    if limit <= stop:
        return None
    qty = trade_rules.SHARES_PER_TRADE
    opt = watch_data.get("options_plan") or {}
    alt = None
    if opt.get("actionable") and str(opt.get("structure") or "NONE").upper() != "NONE":
        debit = _f(opt.get("target_debit"))
        alt = {
            "structure": opt.get("structure"), "expiration": opt.get("expiration"),
            "long_strike": opt.get("long_strike"), "short_strike": opt.get("short_strike"),
            "mid_debit": debit or None, "cost_usd": round(debit * 100, 2) if debit else None,
            "max_loss": opt.get("max_loss"),
        }
    inv = watch_data.get("invalidation") or {}
    return {
        "ticker": watch_data.get("ticker"),
        "date": date_str,
        "side": "BUY",
        "order_type": "LIMIT" if limit < spot or not spot else "LIMIT (marketable)",
        "limit_price": round(limit, 2),
        "max_chase_price": round(entry_max, 2),
        "qty": qty,
        "stop": round(stop, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2) if t2 > t1 else None,
        "risk_usd": round(trade_rules.risk_usd(limit, stop, qty), 2),
        "rr_at_limit": round((t1 - limit) / (limit - stop), 2),
        "option_alternative": alt,
        "good_til": str(_add_sessions(date.fromisoformat(date_str), trade_rules.TICKET_GOOD_TIL_SESSIONS)),
        "cancel_if": f"daily close below {_f(inv.get('price_level')) or round(stop, 2):.2f}, or price trades above {entry_max:.2f} before fill",
        "earnings_stance": trade_rules.normalize_stance(watch_data.get("earnings_stance")),
        "earnings_rationale": watch_data.get("earnings_rationale"),
        "lane": watch_data.get("setup_lane"),
        "lane_prior_win": watch_data.get("lane_prior_win"),
        "lane_prior_ev": watch_data.get("lane_prior_ev"),
        "levels_source": watch_data.get("levels_source"),
        "verdict": watch_data.get("verdict"),
    }


def ticket_text(t: Dict[str, Any]) -> str:
    alt = t.get("option_alternative")
    return (
        f"{t['ticker']} BUY {t['qty']} {t['order_type']} {t['limit_price']} (chase cap {t['max_chase_price']}) | "
        f"stop {t['stop']} T1 {t['target_1']}" + (f" T2 {t['target_2']}" if t.get("target_2") else "")
        + f" | R:R {t['rr_at_limit']} risk ${t['risk_usd']:.0f} | {t['lane']} ({t['levels_source']})"
        + (f" | earnings {t['earnings_stance']}" if t.get("earnings_stance") else "")
        + (f" | opt {alt['structure']} {alt['expiration']} ${alt['cost_usd']}" if alt and alt.get("cost_usd") else "")
        + f" | good til {t['good_til']}"
    )


def persist_and_notify(ticket: Dict[str, Any]) -> None:
    tk, d = ticket["ticker"], ticket["date"]
    try:
        out = config.BASE_DIR / "reports" / d / f"{tk}_ticket.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[{tk}] ticket write failed: {e}")
    try:
        from src.tracking.notify import notify

        notify("ENTRY", f"{tk} ticket", ticket_text(ticket), dedupe_key=f"ticket:{tk}:{d}:{ticket['limit_price']}")
    except Exception as e:
        logger.warning(f"[{tk}] ticket notify failed: {e}")
