"""Executable ticket from a gated long plan: repair to the rulebook, build, persist and notify, then the pre-open GO / STAND DOWN check."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config
from src.tracking import rr_config, trade_rules
from src.utils.market_calendar import NYSE_HOLIDAYS, is_trading_day

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


def spread_math(structure: str, long_k: float, short_k: float, mid_long: float, mid_short: float, t1: float, stop: float):
    """(numbers, None) from leg mids, or (None, reason). Per-share price, per-contract dollars."""
    s, width = str(structure).upper(), abs(long_k - short_k)
    if s == "BULL_CALL_SPREAD":
        debit = mid_long - mid_short
        if not (long_k < short_k and 0 < debit < width):
            return None, f"bad call spread: debit {debit:.2f}, width {width:.2f}"
        if short_k > t1:
            return None, f"short strike {short_k:g} above target {t1:.2f}"
        pnl = min(width, max(0.0, t1 - long_k)) - debit
        return {"mid_debit": round(debit, 2), "max_loss": round(debit * 100, 2), "max_profit": round((width - debit) * 100, 2),
                "breakeven": round(long_k + debit, 2), "r_at_t1": round(pnl / debit, 2)}, None
    if s == "BULL_PUT_SPREAD":
        credit = mid_short - mid_long
        if not (long_k < short_k and 0 < credit < width):
            return None, f"bad put spread: credit {credit:.2f}, width {width:.2f}"
        if credit / width < 0.25:
            return None, f"credit/width {credit / width:.0%} below 25%"
        if short_k >= stop:
            return None, f"short strike {short_k:g} at or above stop {stop:.2f}"
        return {"mid_credit": round(credit, 2), "max_loss": round((width - credit) * 100, 2), "max_profit": round(credit * 100, 2),
                "breakeven": round(short_k - credit, 2), "r_at_t1": round(credit / (width - credit), 2)}, None
    if s == "LONG_CALL":
        if mid_long <= 0:
            return None, "no call price"
        return {"mid_debit": round(mid_long, 2), "max_loss": round(mid_long * 100, 2), "max_profit": None,
                "breakeven": round(long_k + mid_long, 2), "r_at_t1": round((t1 - long_k - mid_long) / mid_long, 2)}, None
    return None, f"{s} not priced"


def _leg_mid(chain_side, strike: float):
    rows = chain_side[chain_side["strike"] == strike]
    if rows.empty:
        return None
    b, a = float(rows["bid"].iloc[0]), float(rows["ask"].iloc[0])
    mid = (a + b) / 2
    return mid if a > 0 and (a - b) / mid <= 0.25 else None


def price_option(ticker: str, opt: Dict[str, Any], t1: float, stop: float):
    """Replace the LLM's option numbers with chain mids: (alternative dict, None) or (None, reason)."""
    structure = str(opt.get("structure") or "").upper()
    exp, lk, sk = str(opt.get("expiration") or ""), _f(opt.get("long_strike")), _f(opt.get("short_strike"))
    try:
        import yfinance as yf

        ch = yf.Ticker(ticker).option_chain(exp)
    except Exception as e:
        return None, f"no chain for {exp}: {e}"
    side = ch.puts if structure == "BULL_PUT_SPREAD" else ch.calls
    ml, ms = _leg_mid(side, lk), (_leg_mid(side, sk) if sk else 0.0)
    if ml is None or ms is None:
        return None, "leg missing or quote wider than 25% of mid"
    nums, why = spread_math(structure, lk, sk, ml, ms, t1, stop)
    if nums is None:
        return None, why
    return {"structure": structure, "expiration": exp, "long_strike": lk, "short_strike": sk or None, **nums,
            "cost_usd": nums["max_loss"]}, None


def _expiries(ticker: str) -> List[str]:
    import yfinance as yf

    return list(yf.Ticker(ticker).options)


def _calls(ticker: str, exp: str):
    import yfinance as yf

    return yf.Ticker(ticker).option_chain(exp).calls


MEASURED_SOURCES = ("pine_alert", "rsi2")
EARNINGS_BLOCK_DAYS = 7


def measured_lane(dw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Measured long lane on this Data Window bar, mirroring the Pine definitions, or None.

    ZONE_RR: in long zone, at-market R:R >= 2, fade off, not Code 18. CODE20: Action Long Code 20 with
    entry R:R >= 2. OVERSOLD: Ext Z <= -2 (no toxic suppression, measured unconditionally).
    """
    from src.logic.actionable_gate import dw_long_levels, dw_rr_at_market, gate_inputs_from_datawindow

    lv, g = dw_long_levels(dw), gate_inputs_from_datawindow(dw)
    close, stop, t1, entry = lv["price"], lv["stop"], lv["target"], lv["entry"]
    if not (close and stop and t1 and stop < close < t1):
        return None
    rr_mkt = dw_rr_at_market(lv) or 0.0
    code = int(g["action_long"]) if g.get("action_long") is not None else None
    lane = None
    if g.get("long_in_zone") == 1.0 and rr_mkt >= 2.0 and g.get("fade_long") == 0.0 and code != 18:
        lane = "ZONE_RR"
    elif code == 20 and entry and stop < entry < t1 and (t1 - entry) / (entry - stop) >= 2.0:
        lane = "CODE20"
    elif g.get("ext_z_self") is not None and g["ext_z_self"] <= -2.0:
        lane = "OVERSOLD"
    if not lane:
        return None
    return {"lane": lane, "close": close, "stop": stop, "target": t1, "rr_at_market": round(rr_mkt, 2)}


def _earnings_within(ticker: str, start: date, days: int) -> Optional[str]:
    try:
        from src.clients.earnings_client import resolve_next_earnings_date

        d = resolve_next_earnings_date(ticker)[0]
    except Exception:
        return None
    return str(d) if d and start <= d <= start + timedelta(days=days) else None


def build_measured_ticket(ticker: str, dw: Dict[str, Any], date_str: str) -> Optional[Dict[str, Any]]:
    """NEXT_OPEN ticket from the Data Window's own levels when the bar is on a measured lane.

    Blocked only by hard facts: held, earnings within 7 days, an unmatured paper trade in the same lane.
    """
    ticker = ticker.upper()
    m = measured_lane(dw)
    if not m:
        return None
    if ticket_blocker(ticker, {}, date_str, skip_superseded=True):
        return None
    d = date.fromisoformat(date_str)
    if _earnings_within(ticker, d, EARNINGS_BLOCK_DAYS):
        return None
    try:
        from src.tracking.ticket_paper import has_open

        if has_open(ticker, m["lane"]):
            return None
    except Exception:
        pass
    close, stop, t1, qty = round(m["close"], 2), round(m["stop"], 2), round(m["target"], 2), trade_rules.SHARES_PER_TRADE
    return {
        "ticker": ticker,
        "date": date_str,
        "plan_date": date_str,
        "side": "BUY",
        "entry_method": "NEXT_OPEN",
        "order_type": f"NEXT OPEN (skip if open <= {stop} or >= {t1})",
        "limit_price": close,
        "max_chase_price": t1,
        "qty": qty,
        "stop": stop,
        "target_1": t1,
        "target_2": None,
        "signal_close": close,
        "rr_at_market": m["rr_at_market"],
        "rr_at_limit": m["rr_at_market"],
        "risk_usd": round(trade_rules.risk_usd(close, stop, qty), 2),
        "option_alternative": None,
        "option_dropped": None,
        "play": "SHARES",
        "exit_by": str(_add_sessions(d, 21)),
        "horizon_bars": 21,
        "good_til": str(_add_sessions(d, 1)),
        "cancel_if": f"next open at or below {stop} or at or above {t1}",
        "earnings_stance": None,
        "lane": m["lane"],
        "levels_source": "pine_alert",
        "verdict": "MEASURED",
        "paper": True,
    }


def maybe_measured_ticket(ticker: str, date_str: str, dw: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Load the Data Window, build a measured ticket, persist + notify (PAPER) and record it in the paper ledger."""
    if dw is None:
        from src.data.datawindow_loader import load_datawindow

        dw = load_datawindow(ticker, date_str)
    if not dw:
        return None
    tk = build_measured_ticket(ticker, dw, date_str)
    if not tk:
        return None
    from src.tracking.ticket_paper import record_ticket

    if record_ticket(tk):
        persist_ticket(tk)
        try:
            from src.tracking.notify import notify

            notify("ENTRY", f"{tk['ticker']} PAPER ticket ({tk['lane']})", ticket_text(tk),
                   dedupe_key=f"ticket:{tk['ticker']}:{date_str}:{tk['lane']}")
        except Exception as e:
            logger.warning(f"[{tk['ticker']}] ticket notify failed: {e}")
    return tk


def bounce_play(ticker: str, entry: float, stop: float, t1: float, atr: float, date_str: str,
                stance: Optional[str] = None, report_date: Optional[str] = None) -> Dict[str, Any]:
    """Time-boxed bounce to entry + BOUNCE_ATR*ATR (capped at T1): the option expiring just after the exit-by date, or shares only."""
    target = min(t1, entry + trade_rules.BOUNCE_ATR * atr)
    exit_by = _add_sessions(date.fromisoformat(date_str), trade_rules.BOUNCE_BARS)
    out = {"bounce_target": round(target, 2), "exit_by": str(exit_by), "horizon_bars": trade_rules.BOUNCE_BARS,
           "play": "SHARES", "option": None, "option_note": None}
    if target <= entry:
        out["option_note"] = "no room to bounce target"
        return out
    try:
        exp = next((e for e in sorted(_expiries(ticker)) if e >= str(exit_by + timedelta(days=3))), None)
        if not exp:
            out["option_note"] = "no expiry after exit-by"
            return out
        bad = options_span_earnings_ok({"expiration": exp, "actionable": True}, report_date, stance)
        if bad:
            out["option_note"] = bad
            return out
        calls = _calls(ticker, exp)
        strikes = sorted(float(k) for k in calls["strike"].unique())
        lk = max((k for k in strikes if k <= entry), default=None)
        sk = max((k for k in strikes if lk and lk < k <= target), default=None)
        if not lk:
            out["option_note"] = "no strike at or below entry"
            return out
        ml = _leg_mid(calls, lk)
        tries = []
        if sk:
            tries.append(("BULL_CALL_SPREAD", sk, _leg_mid(calls, sk)))
        tries.append(("LONG_CALL", 0.0, 0.0))
        why = "leg missing or quote wider than 25% of mid"
        for structure, short_k, ms in tries:
            if ml is None or ms is None:
                continue
            nums, why = spread_math(structure, lk, short_k, ml, ms, target, stop)
            if nums and nums["r_at_t1"] >= 1.0:
                out["option"] = {"structure": structure, "expiration": exp, "long_strike": lk, "short_strike": short_k or None,
                                 **nums, "cost_usd": nums["max_loss"]}
                out["play"] = "SHARES+SPREAD" if structure == "BULL_CALL_SPREAD" else "SHARES+CALL"
                return out
            why = why or f"R at bounce target {nums['r_at_t1']} below 1"
        out["option_note"] = why
    except Exception as e:
        out["option_note"] = f"no chain: {e}"
    return out


def _latest_research_date(ticker: str, today: str) -> Optional[str]:
    root = config.BASE_DIR / "reports"
    if not root.exists():
        return None
    for d in sorted((p.name for p in root.iterdir() if p.is_dir() and p.name <= today), reverse=True)[:30]:
        if any((root / d / f"{ticker}_{s}.md").exists() for s in ("arbitration", "summary")):
            return d
    return None


def held_symbols() -> set:
    """Underlying symbols held at the broker (all accounts) or tracked open; empty when unreadable."""
    out: set = set()
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions

        out |= {str(p.get("underlying_symbol") or p.get("symbol") or "").upper() for p in (get_portfolio_positions() or [])}
    except Exception as e:
        logger.debug(f"holdings read failed: {e}")
    try:
        from src.tracking.position_state import list_open

        out |= {t.upper() for t in list_open()}
    except Exception:
        pass
    out.discard("")
    return out


def ticket_blocker(ticker: str, plan: Dict[str, Any], today: str, held: Optional[set] = None, skip_superseded: bool = False) -> Optional[str]:
    """Why a fresh BUY ticket must not exist for this plan, or None. Single gate for build, touch and pre-open.
    skip_superseded: measured lanes are not voided by newer research, only by hard facts."""
    if plan.get("void"):
        return f"voided: {plan['void']}"
    if plan.get("level_gate_rejected"):
        return "levels failed the gate"
    v = str(plan.get("verdict") or "").upper()
    if any(w in v for w in ("CASH_SKIP", "HOLD", "MANAGE", "NO_LEVELS")):
        return f"verdict {v}"
    if not skip_superseded:
        pd, latest = str(plan.get("plan_date") or plan.get("date") or "")[:10], _latest_research_date(ticker, today)
        if pd and latest and latest > pd:
            return f"plan of {pd} superseded by research of {latest}"
    if ticker.upper() in (held_symbols() if held is None else held):
        return "already held; add plans are not auto-ticketed"
    return None


def void_ticket(ticker: str, date_str: str, reason: str) -> None:
    fp = config.BASE_DIR / "reports" / date_str / f"{ticker}_ticket.json"
    try:
        if fp.exists():
            t = json.loads(fp.read_text(encoding="utf-8"))
            if not t.get("void"):
                fp.write_text(json.dumps({**t, "void": reason}, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[{ticker}] ticket void failed: {e}")


def build_ticket(watch_data: Dict[str, Any], spot: float, date_str: str, skip_superseded: bool = False) -> Optional[Dict[str, Any]]:
    sp = watch_data.get("shares_plan") or {}
    if str(watch_data.get("side") or "LONG").upper() != "LONG":
        return None
    if not watch_data.get("actionable"):
        return None
    if ticket_blocker(str(watch_data.get("ticker") or ""), watch_data, date_str, skip_superseded=skip_superseded):
        return None
    stop, t1, t2 = _f(sp.get("tactical_stop")), _f(sp.get("target_1")), _f(sp.get("target_2"))
    hi, lo = _f(sp.get("entry_zone_high")), _f(sp.get("entry_zone_low"))
    if not (stop > 0 and t1 > stop and (hi > 0 or spot > 0)):
        return None
    entry_max = _f(sp.get("entry_max")) or trade_rules.entry_max_for_rr(t1, stop)
    limit = min(hi, entry_max) if hi > 0 else min(spot, entry_max)
    if limit <= stop:
        return None
    raw_qty = sp.get("recommended_shares")
    try:
        qty = int(raw_qty) if (raw_qty and int(raw_qty) > 0) else trade_rules.SHARES_PER_TRADE
    except (ValueError, TypeError):
        qty = trade_rules.SHARES_PER_TRADE
    opt = watch_data.get("options_plan") or {}
    alt, opt_note = None, None
    if opt.get("actionable") and str(opt.get("structure") or "NONE").upper() != "NONE":
        alt, opt_note = price_option(str(watch_data.get("ticker") or ""), opt, t1, stop)
    bounce = None
    atr = _f(watch_data.get("atr14"))
    if atr > 0:
        rep = watch_data.get("next_earnings_date")
        if not rep:
            try:
                from src.clients.earnings_client import resolve_next_earnings_date

                rep = str(resolve_next_earnings_date(str(watch_data.get("ticker") or ""))[0] or "") or None
            except Exception:
                rep = None
        bounce = bounce_play(str(watch_data.get("ticker") or ""), limit, stop, t1, atr, date_str,
                             watch_data.get("earnings_stance"), rep)
        if bounce["option"]:
            alt, opt_note = bounce["option"], None
        else:
            opt_note = bounce["option_note"] if alt is None else opt_note
    inv = watch_data.get("invalidation") or {}
    layers = sp.get("layers") if isinstance(sp.get("layers"), dict) else None
    order_type = "LIMIT" if limit < spot or not spot else "LIMIT (marketable)"
    risk = trade_rules.risk_usd(limit, stop, qty)
    if layers and layers.get("tranches"):
        qty = int(layers["total_shares"])
        risk = float(layers["risk_usd_if_all_fill"])
        order_type = "LAYERED"
    return {
        "layers": layers,
        "ticker": watch_data.get("ticker"),
        "date": date_str,
        "plan_date": str(watch_data.get("date") or date_str)[:10],
        "side": "BUY",
        "order_type": order_type,
        "limit_price": round(limit, 2),
        "max_chase_price": round(entry_max, 2),
        "qty": qty,
        "stop": round(stop, 2),
        "target_1": round(t1, 2),
        "target_2": round(t2, 2) if t2 > t1 else None,
        "risk_usd": round(risk, 2),
        "rr_at_limit": round((t1 - limit) / (limit - stop), 2),
        "option_alternative": alt,
        "option_dropped": opt_note,
        "play": bounce["play"] if bounce else "SHARES",
        "bounce_target": bounce["bounce_target"] if bounce else None,
        "exit_by": bounce["exit_by"] if bounce else None,
        "horizon_bars": bounce["horizon_bars"] if bounce else None,
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
    if t.get("layers"):
        from src.logic.layered_entry import layers_text

        return (
            f"{t['ticker']} {layers_text(t['layers'])} | {t['layers']['why']} | {t['lane']} ({t['levels_source']}) "
            f"[{t['layers']['label']}]" + (f" | earnings {t['earnings_stance']}" if t.get("earnings_stance") else "")
            + f" | good til {t['good_til']}"
        )
    return (
        f"{t['ticker']} BUY {t['qty']} {t['order_type']} {t['limit_price']} (chase cap {t['max_chase_price']}) | "
        f"stop {t['stop']} T1 {t['target_1']}" + (f" T2 {t['target_2']}" if t.get("target_2") else "")
        + f" | R:R {t['rr_at_limit']} risk ${t['risk_usd']:.0f} | {t['lane']} ({t['levels_source']})"
        + (f" | earnings {t['earnings_stance']}" if t.get("earnings_stance") else "")
        + (f" | opt {alt['structure']} {alt['expiration']} {alt['long_strike']:g}/{alt['short_strike'] or 0:g} max loss ${alt['cost_usd']:.0f} BE {alt['breakeven']} R@T1 {alt['r_at_t1']}" if alt else "")
        + (f" | {t['play']}: bounce to {t['bounce_target']} by {t['exit_by']} ({t['horizon_bars']} bars)" if t.get("bounce_target") else "")
        + f" | good til {t['good_til']}"
    )


def persist_ticket(ticket: Dict[str, Any]) -> None:
    tk, d = ticket["ticker"], ticket["date"]
    try:
        kind = "measured_ticket" if ticket.get("levels_source") == "pine_alert" else "ticket"
        out = config.BASE_DIR / "reports" / d / f"{tk}_{kind}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[{tk}] ticket write failed: {e}")


def persist_and_notify(ticket: Dict[str, Any]) -> None:
    persist_ticket(ticket)
    tk, d = ticket["ticker"], ticket["date"]
    try:
        from src.tracking.notify import notify

        notify("ENTRY", f"{tk} ticket", ticket_text(ticket), dedupe_key=f"ticket:{tk}:{d}:{ticket['limit_price']}")
    except Exception as e:
        logger.warning(f"[{tk}] ticket notify failed: {e}")


def _watch_paths(ticker: str, d: str) -> List[Path]:
    """Persisted plans live in data/raw/<date>/<T>/; gate-rejected display copies in reports/<date>/."""
    safe = ticker.replace(":", "_")
    return [config.BASE_DIR / "data" / "raw" / d / safe / f"{safe}_watch_levels.json",
            config.BASE_DIR / "reports" / d / f"{safe}_watch_levels.json"]


def _plan_on_disk(ticker: str, today: str, plan_date: Optional[str] = None) -> Optional[Tuple[str, Path]]:
    """(date, file) of the plan on `plan_date`, else the newest plan up to `today`."""
    if plan_date:
        return next(((plan_date, fp) for fp in _watch_paths(ticker, plan_date) if fp.exists()), None)
    reports, raw = config.BASE_DIR / "reports", config.BASE_DIR / "data" / "raw"
    days = {p.name for root in (reports, raw) if root.exists() for p in root.iterdir() if p.is_dir() and p.name <= today}
    for d in sorted(days, reverse=True)[:30]:
        hit = next(((d, fp) for fp in _watch_paths(ticker, d) if fp.exists()), None)
        if hit:
            return hit
    return None


def _latest_watch_levels(ticker: str, today: str) -> Optional[Dict[str, Any]]:
    hit = _plan_on_disk(ticker, today)
    if not hit:
        return None
    try:
        return json.loads(hit[1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _watch_levels_on(ticker: str, d: str) -> Optional[Dict[str, Any]]:
    hit = _plan_on_disk(ticker, d, plan_date=d)
    try:
        return json.loads(hit[1].read_text(encoding="utf-8")) if hit else None
    except Exception:
        return None


def void_plan(ticker: str, today: str, cls: str, why: str, last: float, plan_date: Optional[str] = None) -> None:
    """Void the plan everywhere a touch can read it: plan files (raw + reports), tickets of its day and today, watch DB row.
    plan_date: the plan that was checked; today's ticket and the watch DB row follow only the newest plan."""
    ticker, reason = ticker.upper(), f"{cls}: {why}"
    newest = _plan_on_disk(ticker, today)
    hit = (_plan_on_disk(ticker, today, plan_date) if plan_date else None) or newest
    if hit:
        d = hit[0]
        for fp in _watch_paths(ticker, d):
            if fp.exists():
                try:
                    w = json.loads(fp.read_text(encoding="utf-8"))
                    if not w.get("void"):
                        fp.write_text(json.dumps({**w, "void": reason}, indent=2), encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[{ticker}] plan void failed: {e}")
        void_ticket(ticker, d, reason)
    if hit and newest and hit[0] != newest[0]:
        return
    void_ticket(ticker, today, reason)
    try:
        from src.tracking.watch_manager import update_target_live_state

        update_target_live_state(ticker, last, "STOP_BREACHED" if cls == "VOID_STOP" else "INVALIDATED")
    except Exception as e:
        logger.warning(f"[{ticker}] watch DB void failed: {e}")


def _void_on_disk(ticker: str, today: str, plan_date: Optional[str] = None) -> Optional[str]:
    hit = _plan_on_disk(ticker, today, plan_date)
    try:
        return (json.loads(hit[1].read_text(encoding="utf-8")) or {}).get("void") if hit else None
    except Exception:
        return None


def _ticket_today(ticker: str, today: str) -> bool:
    """A live (non-void) ticket already exists for this ticker today: idempotency key for pre-open and touch."""
    d = config.BASE_DIR / "reports" / today
    for kind in ("ticket", "measured_ticket"):
        fp = d / f"{ticker}_{kind}.json"
        if fp.exists():
            try:
                if not json.loads(fp.read_text(encoding="utf-8")).get("void"):
                    return True
            except Exception:
                return True
    return False


CONFIRM_BARS_OFF_LOW = 2


def confirm_from_bars(bars: List[Dict[str, Any]], zone_lo: float, zone_hi: float) -> Tuple[str, str]:
    """CONFIRMED, WAIT or FAILED from today's completed 5m bars (oldest first).
    Confirmed: after the first bar that reached the zone top, the lowest bar since is the pivot; at least
    CONFIRM_BARS_OFF_LOW bars have closed after it and the last close is above the pivot bar's high.
    Failed: a 5m close below the zone low after the touch (price accepted below the zone)."""
    touch = next((i for i, b in enumerate(bars) if b["low"] <= zone_hi), None)
    if touch is None:
        return "WAIT", f"no completed 5m bar has reached the zone top {zone_hi:.2f}"
    after = bars[touch:]
    if zone_lo > 0:
        bad = next((b for b in after if b["close"] < zone_lo), None)
        if bad:
            return "FAILED", f"5m close {bad['close']:.2f} below zone low {zone_lo:.2f}: accepted below the zone"
    k = min(range(len(after)), key=lambda i: after[i]["low"])
    pivot, later = after[k], after[k + 1:]
    if len(later) < CONFIRM_BARS_OFF_LOW:
        return "WAIT", f"low {pivot['low']:.2f} is {len(later)} bar(s) old, need {CONFIRM_BARS_OFF_LOW} closed bars off it"
    if later[-1]["close"] <= pivot["high"]:
        return "WAIT", f"last 5m close {later[-1]['close']:.2f} not above the low bar's high {pivot['high']:.2f}"
    return "CONFIRMED", f"held {pivot['low']:.2f}, last 5m close {later[-1]['close']:.2f} above the low bar's high {pivot['high']:.2f}"


def confirm_touch(ticker: str, today: str, zone_lo: float, zone_hi: float, now_ts: Optional[float] = None) -> Tuple[str, str]:
    """NO_DATA when confirmation cannot be measured (disabled, no bars, fetch error): the touch then stands as before."""
    if os.getenv("TOUCH_CONFIRM", "1") == "0":
        return "NO_DATA", "confirmation disabled"
    try:
        from src.clients.schwab_client import get_intraday_candles
        from src.tracking.candle_service import _normalize_intraday

        raw = _normalize_intraday(get_intraday_candles(ticker, frequency=5, lookback_bars=100))
    except Exception as e:
        return "NO_DATA", f"5m bars unavailable ({type(e).__name__})"
    cut = now_ts if now_ts is not None else time.time()
    bars = [b for b in raw if b["date"] == today and b["session"] == "RTH" and b["datetime"] and b["datetime"] / 1000 + 300 <= cut]
    if not bars:
        return "NO_DATA", "no completed 5m bars today"
    return confirm_from_bars(bars, zone_lo, zone_hi)


def act_on_touch(ticker: str, price: float, today: str, mode: str = "touch", wd: Optional[Dict[str, Any]] = None,
                 plan_date: Optional[str] = None) -> Dict[str, str]:
    """A zone touch (mode 'touch') or approach (mode 'approach') becomes one of: TICKET, RESEARCH (stale dossier, MID queued), NO TRADE.
    plan_date: the plan that named the zone; without it the newest stored plan is used."""
    ticker = ticker.upper()
    base = _watch_levels_on(ticker, plan_date) if plan_date else None
    wd = dict(base or wd or _latest_watch_levels(ticker, today) or {})
    if not wd.get("shares_plan") and wd.get("raw_json"):
        try:
            raw = wd["raw_json"]
            wd = {**(json.loads(raw) if isinstance(raw, str) else raw), **{k: v for k, v in wd.items() if k != "raw_json"}}
        except Exception:
            pass
    if not wd.get("shares_plan"):
        return {"action": "NO TRADE", "text": f"{ticker}: no stored plan"}
    void = wd.get("void") or _void_on_disk(ticker, today, plan_date)
    if void:
        return {"action": "NO TRADE", "text": f"{ticker}: plan voided ({void})"}
    why_not = ticket_blocker(ticker, wd, today, skip_superseded=wd.get("levels_source") in MEASURED_SOURCES)
    if why_not:
        return {"action": "NO TRADE", "text": f"{ticker}: no ticket, {why_not}"}
    if _ticket_today(ticker, today):
        return {"action": "NO TRADE", "text": f"{ticker}: ticket already issued today"}
    atr = _f(wd.get("atr14"))
    if not atr:
        try:
            from src.logic.level_validation import _compute_atr14_from_bars

            atr = wd["atr14"] = _compute_atr14_from_bars(ticker, today)
        except Exception:
            pass
    plan = plan_levels(wd)
    if not plan:
        return {"action": "NO TRADE", "text": f"{ticker}: stored levels invalid"}
    q = _quotes([ticker]).get(ticker) or {}
    c = classify_plan(plan, price, q.get("low", 0.0), q.get("date", today), today, atr, is_etf(ticker), datetime.now(ET),
                      cap_risk=wd.get("levels_source") in MEASURED_SOURCES)
    if c["cls"].startswith("VOID"):
        _void_and_notify(ticker, today, c, price, plan_date)
        return {"action": "NO TRADE", "text": f"{ticker}: plan voided ({c['cls']}): {c['why']}"}
    if c["cls"] == "STALE":
        return {"action": "NO TRADE", "text": f"{ticker}: {c['why']}"}
    blocked = _veto_gate(ticker, str(wd.get("date") or today), today, price)
    if blocked:
        return {"action": "NO TRADE", "text": f"{ticker}: {blocked}"}
    from src.tracking.auto_triage_daemon import enqueue_touch_mid, needs_fresh_research

    stale, why = needs_fresh_research(ticker, today)
    if mode == "touch" and not stale and wd.get("scenarios") and (_latest_research_date(ticker, today) or "") < today:
        stale, why = True, "scenario trigger reached; arrival check"
    if stale:
        if mode == "approach":
            return {"action": "NO TRADE", "text": f"{ticker}: dossier stale ({why})"}
        enqueue_touch_mid(ticker, today)
        return {"action": "RESEARCH", "text": f"{ticker} at {price:.2f} in its zone; dossier stale ({why}), priority MID research queued, ticket follows"}
    sp = wd.get("shares_plan") or {}
    zone_hi = _f(sp.get("entry_zone_high"))
    state, why_c = "NO_DATA", ""
    if mode == "touch" and 0 < zone_hi and price <= zone_hi:
        state, why_c = confirm_touch(ticker, today, _f(sp.get("entry_zone_low")), zone_hi)
        if state == "FAILED":
            return {"action": "NO TRADE", "text": f"{ticker}: zone failed today, {why_c}"}
        if state == "WAIT":
            return {"action": "NO TRADE", "text": f"{ticker} at {price:.2f} in its zone; waiting for confirmation: {why_c}"}
    wd["actionable"] = 1
    tk = build_ticket(wd, price, today)
    if not tk:
        return {"action": "NO TRADE", "text": f"{ticker}: plan no longer builds a valid ticket at {price:.2f}"}
    if state == "CONFIRMED":
        persist_and_notify(tk)
        return {"action": "TICKET", "text": f"confirmed ({why_c}). " + ticket_text(tk)}
    persist_ticket(tk)
    return {"action": "TICKET", "text": ticket_text(tk)}


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

    # The stop floor is measured from the LOWEST planned fill (zone low), the same fill
    # level_validation's gate uses. Measuring from the zone top let a stop pass here and fail
    # the gate (GOOGL 2026-10-29: 260.30 ok vs 268.31, rejected vs 265.64 -> 258.17), and the
    # layered entry fills down to the zone low anyway.
    floor_fill = lo if lo > 0 else fill
    cap = trade_rules.max_allowed_stop(floor_fill, atr, pine_stop=stop if pine_stop else 0.0)
    if stop > cap:
        sp["tactical_stop"] = stop = round(cap, 2)
        notes.append(f"stop widened to {trade_rules.STOP_ATR_MIN:g} ATR below the lowest fill {floor_fill:.2f}: {stop}")

    sp["recommended_shares"] = trade_rules.compute_position_shares(fill, stop)

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
        # The conversion can lower the zone low; keep the stop 0.7 ATR under the new lowest fill.
        cap2 = trade_rules.max_allowed_stop(sp["entry_zone_low"], atr, pine_stop=stop if pine_stop else 0.0)
        if stop > cap2:
            sp["tactical_stop"] = round(cap2, 2)
            notes.append(f"stop widened to {trade_rules.STOP_ATR_MIN:g} ATR below the new zone low: {sp['tactical_stop']}")
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
    stop, t1 = float(t["stop"]), float(t["target_1"])
    risk = max(price - stop, 0.01)
    if t.get("entry_method") == "NEXT_OPEN":
        if price <= stop:
            verdict, why = "STAND DOWN", f"open {price:.2f} at or through stop {stop:.2f}; no entry"
        elif price >= t1:
            verdict, why = "STAND DOWN", f"open {price:.2f} at or above target {t1:.2f}; no entry"
        else:
            verdict, why = "GO", f"buy at the open {price:.2f}, inside stop {stop:.2f} / target {t1:.2f}"
        return {"verdict": verdict, "why": why, "rr_now": round((t1 - price) / risk, 2)}
    limit, cap = float(t["limit_price"]), float(t["max_chase_price"])
    if price <= stop:
        verdict, why = "STAND DOWN", f"{price:.2f} at or through stop {stop:.2f}"
    elif price > cap:
        verdict, why = "STAND DOWN", f"{price:.2f} above chase cap {cap:.2f}; do not chase, limit {limit:.2f} stays resting"
    elif price <= limit:
        verdict, why = "GO", f"{price:.2f} at or below limit {limit:.2f}, fills"
    else:
        verdict, why = "RESTING", f"{price:.2f} above limit {limit:.2f}, inside chase cap {cap:.2f}; limit rests"
    return {"verdict": verdict, "why": why, "rr_now": round((t1 - price) / risk, 2)}


def _news_delta(ticker: str, since: str) -> List[str]:
    try:
        from src.clients.news_client import get_fresh_news

        txt = get_fresh_news(ticker, since, days=2)
        return [l.strip() for l in txt.splitlines() if l.strip()][:3]
    except Exception:
        return []


def open_tickets(days: int = 14) -> List[Dict[str, Any]]:
    """Newest valid ticket per ticker, inside good-til, not voided, superseded, gate-rejected or held."""
    root, today, latest = config.BASE_DIR / "reports", datetime.now().strftime("%Y-%m-%d"), {}
    held = held_symbols()
    for i in range(max(1, min(days, 21))):
        dname = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        d = root / dname
        for f in sorted(d.glob("*_ticket.json")) if d.exists() else []:
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            tk = str(t.get("ticker") or "")
            if tk in latest or str(t.get("good_til") or "") < today:
                continue
            if not t.get("plan_date"):
                try:
                    w = json.loads((d / f"{tk}_watch_levels.json").read_text(encoding="utf-8"))
                    ok = abs(float(w["shares_plan"]["tactical_stop"]) - float(t["stop"])) < 0.01
                    t["level_gate_rejected"] = w.get("level_gate_rejected")
                except Exception:
                    ok = False
                if not ok:
                    continue
                t["plan_date"] = dname
            latest[tk] = t if not ticket_blocker(tk, t, today, held, skip_superseded=t.get("levels_source") in MEASURED_SOURCES) else None
    return [t for t in latest.values() if t]


PREOPEN_WINDOWS = {"0800": ((8, 0), (8, 59)), "0925": ((9, 25), (9, 35))}
RISK_CAP_PCT = {"stock": 0.05, "etf": 0.03}
RISK_CAP_ATR = 2.0
VETO_SYSTEM = (
    "You are a veto-only reviewer of a long swing plan. Reply with one line: OK, or VETO followed by a short reason "
    "and the exact sentence copied in double quotes from the news that proves it. Veto only a hard, dated fact that "
    "breaks the long thesis (guidance cut, downgrade, fraud, halt, lost major contract). Otherwise OK. Never invent facts."
)


def is_etf(ticker: str) -> bool:
    from src.tracking.entry_risk_alerts import ETFS

    return ticker.upper() in ETFS


def plan_levels(wd: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Stop, T1, fill limit and chase cap of a stored long plan, the same numbers build_ticket would use."""
    sp = wd.get("shares_plan") or {}
    stop, t1 = _f(sp.get("tactical_stop")), _f(sp.get("target_1"))
    if not (stop > 0 and t1 > stop):
        return None
    hi = _f(sp.get("entry_zone_high"))
    entry_max = _f(sp.get("entry_max")) or trade_rules.entry_max_for_rr(t1, stop)
    return {"stop": stop, "target_1": t1, "limit": min(hi, entry_max) if hi > 0 else entry_max, "entry_max": entry_max}


def classify_plan(plan: Dict[str, Any], last: float, day_low: float, quote_date: str, today: str, atr: float,
                  etf: bool, now_et: datetime, cap_risk: bool = False) -> Dict[str, Any]:
    """Code-only re-check of a stored long plan against one quote. cls: STALE, VOID_STOP, VOID_TARGET, VOID_RISK,
    STAND_ASIDE, RESTING or FILL. The day low counts only in the regular session, when it is today's low.
    cap_risk: apply the 5% (3% ETF) and 2 ATR risk caps at the fill. Only Pine-sourced plans; rulebook LLM plans carry dollar risk as information."""
    stop, t1 = _f(plan.get("stop")), _f(plan.get("target_1"))
    limit, cap, last = _f(plan.get("limit")), _f(plan.get("entry_max")), _f(last)

    def out(cls: str, why: str, fill: Optional[float] = None) -> Dict[str, Any]:
        return {"cls": cls, "why": why, "fill": fill}

    if quote_date != today or last <= 0:
        return out("STALE", f"quote dated {quote_date or 'none'}, not today {today}")
    regular = (9, 30) <= (now_et.hour, now_et.minute) < (16, 0)
    if last <= stop:
        return out("VOID_STOP", f"{last:.2f} at or through stop {stop:.2f}")
    if regular and 0 < _f(day_low) <= stop:
        return out("VOID_STOP", f"today's low {_f(day_low):.2f} traded through stop {stop:.2f}")
    if t1 > 0 and last >= t1:
        return out("VOID_TARGET", f"{last:.2f} at or above target {t1:.2f}")
    if cap > 0 and last > cap:
        return out("STAND_ASIDE", f"{last:.2f} above chase cap {cap:.2f}")
    fill = min(last, limit) if limit > 0 else last
    risk = fill - stop
    if cap_risk and (risk > RISK_CAP_PCT["etf" if etf else "stock"] * fill or (atr > 0 and risk > RISK_CAP_ATR * atr)):
        return out("VOID_RISK", f"risk {risk:.2f} at fill {fill:.2f} over cap", fill)
    if limit > 0 and last > limit:
        return out("RESTING", f"{last:.2f} above limit {limit:.2f}, limit rests", fill)
    rr = (t1 - fill) / risk if risk > 0 else 0.0
    if rr < rr_config.min_rr():
        return out("STAND_ASIDE", f"R:R at fill {rr:.2f} below floor {rr_config.min_rr():.1f}", fill)
    return out("FILL", f"fills at {fill:.2f}, R:R {rr:.2f}", fill)


def _quotes(syms: List[str]) -> Dict[str, Dict[str, Any]]:
    from src.clients.quote_router import quote_router

    out: Dict[str, Dict[str, Any]] = {}
    try:
        batch = quote_router.get_watchlist_quotes_batch(list(syms)) or {}
    except Exception as e:
        logger.warning(f"quote batch failed: {e}")
        return out
    for k, q in batch.items():
        if q and q.last_price > 0:
            out[k.upper()] = {"last": float(q.last_price), "low": float(q.low or 0.0),
                              "date": datetime.fromtimestamp(q.timestamp, ET).strftime("%Y-%m-%d")}
    return out


def _notify_risk(ticker: str, today: str, tag: str, body: str) -> None:
    try:
        from src.tracking.notify import notify

        notify("RISK", f"{ticker} pre-open {tag}", body, dedupe_key=f"preopen:{ticker}:{today}:{tag}")
    except Exception as e:
        logger.warning(f"[{ticker}] risk notify failed: {e}")


def _void_and_notify(ticker: str, today: str, c: Dict[str, Any], last: float, plan_date: Optional[str] = None) -> None:
    void_plan(ticker, today, c["cls"], c["why"], last, plan_date)
    _notify_risk(ticker, today, c["cls"], c["why"])


def _load_json(fp: Path) -> Dict[str, Any]:
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(fp: Path, obj: Dict[str, Any]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, fp)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _veto_llm(ticker: str, plan_date: str, news: str) -> List[str]:
    from src.clients.llm_client import query_local_llm

    raw = query_local_llm(system_prompt=VETO_SYSTEM, user_prompt=f"Ticker {ticker}. Plan dated {plan_date}.\nNEWS:\n{news}",
                          use_openrouter=True, model=os.getenv("CHAT_MODEL") or None, use_tools=False, max_tokens=600,
                          reasoning_effort=os.getenv("JUDGE_REASONING", "high")) or ""
    line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
    up = line.upper()
    if up.startswith("OK"):
        return ["OK", ""]
    if not up.startswith("VETO"):
        return ["UNTRACED", f"unparseable judge reply: {line[:200]}"]
    m = re.search(r'"([^"]{10,})"', line)
    if m and _norm(m.group(1)) in _norm(news):
        return ["VETO", line[:300]]
    return ["UNTRACED", line[:300]]


def _veto_verdict(ticker: str, plan_date: str, today: str) -> Optional[Tuple[str, str]]:
    """None when news is clear. ('VETO', text) only when the quoted news sentence is verbatim in the news; ('UNTRACED', text) otherwise."""
    from src.clients.news_client import get_fresh_news

    try:
        days = max(1, min(10, (date.fromisoformat(today) - date.fromisoformat(str(plan_date)[:10])).days + 1))
        news = get_fresh_news(ticker, str(plan_date)[:10], days=days) or ""
    except Exception as e:
        return ("UNTRACED", f"news unavailable: {e}")
    if not news.strip():
        return None
    key = hashlib.sha1(f"{ticker}|{today}|{news}".encode("utf-8")).hexdigest()
    fp = config.BASE_DIR / "data" / "veto_cache.json"
    cache = _load_json(fp)
    if key not in cache:
        try:
            cache[key] = _veto_llm(ticker, str(plan_date)[:10], news)
        except Exception as e:
            return ("UNTRACED", f"judge unavailable: {e}")
        _save_json(fp, dict(list(cache.items())[-1000:]))
    cls, text = cache[key]
    return None if cls == "OK" else (cls, text)


def _veto_gate(ticker: str, plan_date: str, today: str, last: float) -> Optional[str]:
    """None when clear. Traced news VETO voids the plan; untraced VETO or judge failure blocks the ticket (fail closed)."""
    v = _veto_verdict(ticker, plan_date, today)
    if v is None:
        return None
    cls, text = v
    if cls == "VETO":
        void_plan(ticker, today, "VETO", f"news: {text}", last, str(plan_date)[:10])
        _notify_risk(ticker, today, "VETO", text)
        return f"news veto, plan voided: {text}"
    _notify_risk(ticker, today, "UNVERIFIED_VETO", text)
    return f"unverified news veto, no ticket: {text}"


def _armed_tickers(today: str) -> List[str]:
    """Tickers with a stored plan (raw or reports) in the zone-ledger lookback."""
    from src.tracking.zone_ledger import LOOKBACK_DAYS

    cutoff = (date.fromisoformat(today) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    out = set()
    for root, pat in ((config.BASE_DIR / "data" / "raw", "*/*_watch_levels.json"), (config.BASE_DIR / "reports", "*_watch_levels.json")):
        if not root.exists():
            continue
        for d in root.iterdir():
            if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name) and cutoff <= d.name <= today:
                out.update(fp.name[: -len("_watch_levels.json")].upper() for fp in d.glob(pat))
    return sorted(out)


def run_preopen(quote_fn=None, max_measured: int = 3, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Code re-check against the live quote. Open tickets: VOID on a breach, else GO / RESTING / STAND DOWN (measured tickets labelled PAPER).
    Armed plans with no ticket today: VOID on a breach, ticket at FILL."""
    from src.tracking.notify import notify

    now = now or datetime.now(ET)
    today = now.strftime("%Y-%m-%d")
    tickets = open_tickets()
    measured = sorted((t for t in tickets if t.get("levels_source") == "pine_alert"),
                      key=lambda t: float(t.get("rr_at_market") or 0), reverse=True)[:max_measured]
    tickets = measured + [t for t in tickets if t.get("levels_source") != "pine_alert"]
    names = {t["ticker"] for t in tickets}
    armed = [tk for tk in _armed_tickers(today) if tk not in names]
    syms = list(dict.fromkeys([t["ticker"] for t in tickets] + armed))
    if not syms:
        return []
    quotes = (quote_fn or _quotes)(syms) or {}
    out: List[Dict[str, Any]] = []
    for t in tickets:
        tk = t["ticker"]
        q = quotes.get(tk)
        if not q:
            continue
        last = q["last"]
        if t.get("entry_method") != "NEXT_OPEN":
            plan = {"stop": t["stop"], "target_1": t["target_1"], "limit": t["limit_price"], "entry_max": t["max_chase_price"]}
            c = classify_plan(plan, last, q.get("low", 0.0), q.get("date", today), today, _f(t.get("atr14")), is_etf(tk), now,
                              cap_risk=t.get("levels_source") in MEASURED_SOURCES)
            if c["cls"].startswith("VOID"):
                _void_and_notify(tk, today, c, last)
                out.append({"ticker": tk, "verdict": "VOID", "why": c["why"], "paper": bool(t.get("paper"))})
                continue
            if c["cls"] == "STALE":
                continue
        res = {"ticker": tk, **evaluate_ticket(t, last), "paper": bool(t.get("paper"))}
        if res["verdict"] == "GO":
            blocked = _veto_gate(tk, str(t.get("plan_date") or t["date"]), today, last)
            if blocked:
                res.update(verdict="STAND DOWN", why=blocked)
        res["news"] = _news_delta(tk, t["date"])
        out.append(res)
        label = ("PAPER " if res["paper"] else "") + res["verdict"]
        body = f"{label}: {res['why']} | R:R now {res['rr_now']}" + (f" | news: {res['news'][0]}" if res["news"] else "")
        notify("ENTRY" if res["verdict"] == "GO" else "RISK", f"{tk} pre-open {label}", body,
               dedupe_key=f"preopen:{tk}:{today}:{res['verdict']}")
    held = held_symbols()
    for tk in armed:
        q, hit = quotes.get(tk), _plan_on_disk(tk, today)
        wd = _load_json(hit[1]) if hit else {}
        if not q or plan_levels(wd) is None or ticket_blocker(tk, wd, today, held):
            continue
        plan_date = hit[0]
        c = classify_plan(plan_levels(wd), q["last"], q.get("low", 0.0), q.get("date", today), today,
                          _f(wd.get("atr14")), is_etf(tk), now, cap_risk=wd.get("levels_source") in MEASURED_SOURCES)
        if c["cls"].startswith("VOID"):
            _void_and_notify(tk, today, c, q["last"], plan_date)
            continue
        if c["cls"] != "FILL" or not wd.get("actionable") or _ticket_today(tk, today):
            continue
        if _veto_gate(tk, plan_date, today, q["last"]):
            continue
        ticket = build_ticket(wd, q["last"], today)
        if ticket:
            persist_and_notify(ticket)
            out.append({"ticker": tk, "verdict": "TICKET", "why": c["why"], "paper": False})
    return out


def _preopen_state_fp() -> Path:
    return config.BASE_DIR / "data" / "preopen_state.json"


def maybe_run_preopen(now: Optional[datetime] = None) -> None:
    """Pre-open windows 08:00-08:59 and 09:25-09:35 ET, each run once per trading day. Called from the watch loop."""
    now = now or datetime.now(ET)
    day = now.strftime("%Y-%m-%d")
    if not is_trading_day(now.date()):
        return
    win = next((k for k, (a, b) in PREOPEN_WINDOWS.items() if a <= (now.hour, now.minute) <= b), None)
    if not win:
        return
    done = _load_json(_preopen_state_fp()).get(day, [])
    if win in done:
        return
    try:
        run_preopen(now=now)
    except Exception as e:
        logger.warning(f"preopen run failed: {e}")
        return
    _save_json(_preopen_state_fp(), {day: done + [win]})
