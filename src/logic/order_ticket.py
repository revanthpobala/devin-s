"""Executable ticket from a gated long plan: repair to the rulebook, build, persist and notify, then the pre-open GO / STAND DOWN check."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

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


# --- repair: turn a good idea at a bad price into a resting limit before the gate ---

MAX_LIMIT_DISTANCE_ATR = 1.5


def repair_long_shares_plan(
    sp: Dict[str, Any], close: float, atr: float, pine_stop: bool = False, rr: Optional[float] = None
) -> List[str]:
    """Mutates sp in place. Returns notes; sets sp['repair_rejected'] when no fillable limit exists."""
    notes: List[str] = []
    stop, t1 = _f(sp.get("tactical_stop")), _f(sp.get("target_1"))
    if not (close > 0 and atr > 0 and t1 > close and stop > 0):
        return notes
    lo, hi = _f(sp.get("entry_zone_low")), _f(sp.get("entry_zone_high"))
    fill = hi if hi > 0 else close

    if not pine_stop:
        cap = trade_rules.max_allowed_stop(fill, atr)
        if stop > cap:
            sp["tactical_stop"] = stop = round(cap, 2)
            notes.append(f"stop widened to {trade_rules.STOP_ATR_MIN:g} ATR below the fill: {stop}")

    entry_max = trade_rules.entry_max_for_rr(t1, stop, rr)
    sp["entry_max"] = round(entry_max, 2)
    if entry_max <= stop:
        sp["repair_rejected"] = "no entry above the stop reaches the R:R floor"
        return notes + [sp["repair_rejected"]]

    if fill > entry_max:
        if (close - entry_max) / atr > MAX_LIMIT_DISTANCE_ATR:
            sp["repair_rejected"] = f"R:R floor needs a limit at {entry_max:.2f}, {(close - entry_max) / atr:.1f} ATR below spot; unlikely to fill"
            return notes + [sp["repair_rejected"]]
        sp["entry_zone_high"] = round(entry_max, 2)
        sp["entry_zone_low"] = round(max(stop + 0.01, min(lo, entry_max) if lo > 0 else entry_max - 0.25 * atr), 2)
        sp["entry_type"] = "LIMIT"
        sp["limit_price"] = sp["trigger_price"] = sp["entry_zone_high"]
        sp["wait_for"] = f"resting limit {entry_max:.2f} (R:R floor)"
        notes.append(f"converted to resting limit at {entry_max:.2f}")
    return notes


def options_span_earnings_ok(options_plan: Dict[str, Any], report_date: Optional[str], stance: Optional[str]) -> Optional[str]:
    """None when fine; else the reason the option leg must be dropped."""
    exp = str((options_plan or {}).get("expiration") or "")
    if not exp or not report_date or not (options_plan or {}).get("actionable"):
        return None
    try:
        spans = date.fromisoformat(exp) >= date.fromisoformat(report_date)
    except ValueError:
        return None
    if spans and trade_rules.normalize_stance(stance) not in ("HOLD_THROUGH", "EARNINGS_PLAY"):
        return f"option expiry {exp} spans earnings {report_date} but stance is {stance or 'missing'}"
    return None


# --- pre-open: GO / STAND DOWN from the live quote against the ticket's own limits ---

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


def open_tickets(days: int = 14) -> List[Dict[str, Any]]:
    """Tickets from the last N calendar days still inside good-til."""
    root, today, out = config.BASE_DIR / "reports", datetime.now().strftime("%Y-%m-%d"), []
    for i in range(max(1, min(days, 21))):
        d = root / (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        for f in sorted(d.glob("*_ticket.json")) if d.exists() else []:
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(t.get("good_til") or "") >= today:
                out.append(t)
    return out


def run_preopen(price_fn=None) -> List[Dict[str, Any]]:
    from src.tracking.notify import notify

    if price_fn is None:
        from src.clients.price_client import get_current_prices_batch

        price_fn = lambda syms: get_current_prices_batch(syms, context="surveillance") or {}
    tickets = open_tickets()
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
