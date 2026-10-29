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
    return {
        "ticker": watch_data.get("ticker"),
        "date": date_str,
        "plan_date": str(watch_data.get("date") or date_str)[:10],
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


def _latest_watch_levels(ticker: str, today: str) -> Optional[Dict[str, Any]]:
    """Persisted plans live in data/raw/<date>/<T>/; gate-rejected display copies in reports/<date>/."""
    reports, raw = config.BASE_DIR / "reports", config.BASE_DIR / "data" / "raw"
    safe = ticker.replace(":", "_")
    days = {p.name for root in (reports, raw) if root.exists() for p in root.iterdir() if p.is_dir() and p.name <= today}
    for d in sorted(days, reverse=True)[:30]:
        for fp in (raw / d / safe / f"{safe}_watch_levels.json", reports / d / f"{safe}_watch_levels.json"):
            if fp.exists():
                try:
                    return json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    return None
    return None


def act_on_touch(ticker: str, price: float, today: str, mode: str = "touch", wd: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """A zone touch (mode 'touch') or approach (mode 'approach') becomes one of: TICKET, RESEARCH (stale dossier, MID queued), NO TRADE."""
    ticker = ticker.upper()
    wd = dict(wd or _latest_watch_levels(ticker, today) or {})
    if not wd.get("shares_plan") and wd.get("raw_json"):
        try:
            raw = wd["raw_json"]
            wd = {**(json.loads(raw) if isinstance(raw, str) else raw), **{k: v for k, v in wd.items() if k != "raw_json"}}
        except Exception:
            pass
    if not wd.get("shares_plan"):
        return {"action": "NO TRADE", "text": f"{ticker}: no stored plan"}
    why_not = ticket_blocker(ticker, wd, today, skip_superseded=wd.get("levels_source") in MEASURED_SOURCES)
    if why_not:
        return {"action": "NO TRADE", "text": f"{ticker}: no ticket, {why_not}"}
    from src.tracking.auto_triage_daemon import enqueue_touch_mid, needs_fresh_research

    stale, why = needs_fresh_research(ticker, today)
    if mode == "touch" and not stale and wd.get("scenarios") and (_latest_research_date(ticker, today) or "") < today:
        stale, why = True, "scenario trigger reached; arrival check"
    if stale:
        if mode == "approach":
            return {"action": "NO TRADE", "text": f"{ticker}: dossier stale ({why})"}
        enqueue_touch_mid(ticker, today)
        return {"action": "RESEARCH", "text": f"{ticker} at {price:.2f} in its zone; dossier stale ({why}), priority MID research queued, ticket follows"}
    wd["actionable"] = 1
    if not _f(wd.get("atr14")):
        try:
            from src.logic.level_validation import _compute_atr14_from_bars

            wd["atr14"] = _compute_atr14_from_bars(ticker, today)
        except Exception:
            pass
    tk = build_ticket(wd, price, today)
    if not tk:
        return {"action": "NO TRADE", "text": f"{ticker}: plan no longer builds a valid ticket at {price:.2f}"}
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

    cap = trade_rules.max_allowed_stop(fill, atr, pine_stop=stop if pine_stop else 0.0)
    if stop > cap:
        sp["tactical_stop"] = stop = round(cap, 2)
        notes.append(f"stop widened to {trade_rules.STOP_ATR_MIN:g} ATR below the fill: {stop}")

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


def run_preopen(price_fn=None, max_measured: int = 3) -> List[Dict[str, Any]]:
    """GO / STAND DOWN per open ticket. Measured tickets: top `max_measured` by R:R at market, labelled PAPER."""
    from src.tracking.notify import notify

    if price_fn is None:
        from src.clients.price_client import get_current_prices_batch

        price_fn = lambda syms: get_current_prices_batch(syms, context="surveillance") or {}
    tickets = open_tickets()
    measured = sorted((t for t in tickets if t.get("levels_source") == "pine_alert"),
                      key=lambda t: float(t.get("rr_at_market") or 0), reverse=True)[:max_measured]
    tickets = measured + [t for t in tickets if t.get("levels_source") != "pine_alert"]
    if not tickets:
        return []
    prices = price_fn([t["ticker"] for t in tickets])
    out = []
    for t in tickets:
        px = prices.get(t["ticker"])
        if not px:
            continue
        res = {"ticker": t["ticker"], **evaluate_ticket(t, float(px)), "paper": bool(t.get("paper"))}
        res["news"] = _news_delta(t["ticker"], t["date"])
        out.append(res)
        label = ("PAPER " if res["paper"] else "") + res["verdict"]
        body = f"{label}: {res['why']} | R:R now {res['rr_now']}" + (f" | news: {res['news'][0]}" if res["news"] else "")
        notify("ENTRY" if res["verdict"] == "GO" else "RISK", f"{t['ticker']} pre-open {label}", body,
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
