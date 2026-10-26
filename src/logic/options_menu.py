"""Three-tier options menu built from the live chain and the plan's own levels, whatever the verdict.

Tier 1: 30-45 DTE bull call spread (long ATM, short at or below T1). Tier 2: deep-ITM LEAPS call near 0.80 delta.
Tier 3: cash-secured put at or below the stop. Real mids, real R:R, earnings flag. These are PAPER ideas with no
measured edge: never actionable, never a ticket.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

TAG = "PAPER idea, no measured edge"


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _bs_call(spot: float, k: float, iv: float, years: float) -> float:
    d1 = (math.log(spot / k) + 0.5 * iv * iv * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return spot * n(d1) - k * n(d2)


def _implied_vol(spot: float, k: float, mid: float, years: float) -> Optional[float]:
    if mid <= max(0.0, spot - k) or years <= 0:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(40):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if _bs_call(spot, k, m, years) < mid else (lo, m)
    return (lo + hi) / 2


def _row_delta(row, spot: float, years: float, mid: float) -> Optional[float]:
    """Broker delta when quoted, else implied from the mid via Black-Scholes."""
    d = row.get("delta")
    if d is not None and not pd.isna(d) and 0 < _f(d) <= 1:
        return _f(d)
    iv = _f(row.get("impliedVolatility")) or _implied_vol(spot, _f(row.get("strike")), mid, years)
    return _delta(spot, _f(row.get("strike")), iv or 0.0, years)


def _delta(spot: float, k: float, iv: float, years: float) -> Optional[float]:
    if spot <= 0 or k <= 0 or iv <= 0 or years <= 0:
        return None
    d1 = (math.log(spot / k) + 0.5 * iv * iv * years) / (iv * math.sqrt(years))
    return 0.5 * (1 + math.erf(d1 / math.sqrt(2)))


def _mid(row) -> Optional[float]:
    b, a = _f(row.get("bid")), _f(row.get("ask"))
    if a <= 0:
        return None
    mid = (a + b) / 2
    return mid if (a - b) / mid <= 0.40 else None


def _pick_exp(exps: List[str], today: date, lo: int, hi: int, want: int, fetch=None, spot: float = 0.0) -> Optional[str]:
    """Expiry in the DTE window with the most strikes near spot (thin weeklies give no usable spread), then closest to `want`."""
    c = [e for e in exps if lo <= (date.fromisoformat(e) - today).days <= hi]
    if not c:
        return None

    def depth(e):
        try:
            calls = fetch(e)[0]
            return int(((calls["strike"] >= spot * 0.85) & (calls["strike"] <= spot * 1.2)).sum())
        except Exception:
            return 0

    return max(c, key=lambda e: (depth(e) if fetch and spot else 0, -abs((date.fromisoformat(e) - today).days - want)))


def _flag(exp: str, earnings: Optional[str]) -> str:
    return f" ⚠ expiry spans earnings {earnings}" if earnings and exp >= earnings else ""


def _levels(plan: Dict[str, Any], spot: float, em_pct: float) -> Dict[str, float]:
    stop, t1 = _f(plan.get("tactical_stop")), _f(plan.get("target_1"))
    if not (stop and t1 and stop < spot < t1):
        em = (em_pct or 8.0) / 100
        stop, t1 = round(spot * (1 - em / 2), 2), round(spot * (1 + em), 2)
    return {"stop": stop, "t1": t1}


def _tier1(calls, exp: str, spot: float, t1: float, stop: float, earnings: Optional[str]) -> Dict[str, Any]:
    strikes = sorted(float(k) for k in calls["strike"].unique())
    lk = max((k for k in strikes if k <= spot), default=None)
    if lk is None:
        return {"structure": "NONE", "summary": "no call strike at or below spot"}
    ml = _mid(calls[calls["strike"] == lk].iloc[0])
    best = None
    for sk in (k for k in strikes if lk < k <= t1):
        ms = _mid(calls[calls["strike"] == sk].iloc[0])
        if ml is None or ms is None:
            continue
        debit, width = ml - ms, sk - lk
        if not 0 < debit < width:
            continue
        r = (min(width, max(0.0, t1 - lk)) - debit) / debit
        if best is None or r > best["r_at_t1"]:
            best = {"structure": "BULL_CALL_SPREAD", "expiration": exp, "long_strike": lk, "short_strike": sk,
                    "mid_debit": round(debit, 2), "max_loss": round(debit * 100, 2),
                    "max_profit": round((width - debit) * 100, 2), "breakeven": round(lk + debit, 2),
                    "r_at_t1": round(r, 2)}
    if not best:
        return {"structure": "NONE", "summary": f"no liquid call spread {exp} with short strike <= T1 {t1:.2f}"}
    best["summary"] = (f"{exp} {best['long_strike']:g}/{best['short_strike']:g} call spread, debit {best['mid_debit']:.2f} "
                       f"(max loss ${best['max_loss']:.0f}, max gain ${best['max_profit']:.0f}), BE {best['breakeven']}, "
                       f"R at T1 {best['r_at_t1']}:1{' - thin' if best['r_at_t1'] < 1.5 else ''}{_flag(exp, earnings)} · {TAG}")
    return best


def _tier2(calls, exp: str, spot: float, today: date, earnings: Optional[str]) -> Dict[str, Any]:
    years = (date.fromisoformat(exp) - today).days / 365
    best = None
    for _, r in calls.iterrows():
        mid = _mid(r)
        d = _row_delta(r, spot, years, mid) if mid else None
        if mid is None or d is None:
            continue
        if best is None or abs(d - 0.80) < abs(best[1] - 0.80):
            best = (r, d, mid)
    if not best:
        return {"structure": "NONE", "summary": f"no liquid LEAPS call {exp}"}
    r, d, mid = best
    k = _f(r["strike"])
    extrinsic = max(0.0, mid - max(0.0, spot - k))
    out = {"structure": "LONG_CALL", "expiration": exp, "long_strike": k, "short_strike": None,
           "mid_debit": round(mid, 2), "max_loss": round(mid * 100, 2), "max_profit": None,
           "breakeven": round(k + mid, 2), "delta": round(d, 2), "extrinsic_pct": round(100 * extrinsic / spot, 1)}
    out["summary"] = (f"{exp} {k:g} call ~{d:.2f} delta, debit {mid:.2f} (${out['max_loss']:.0f}), BE {out['breakeven']}, "
                      f"time value {out['extrinsic_pct']}% of spot{_flag(exp, earnings)} · {TAG}")
    return out


def _tier3(puts, exp: str, spot: float, stop: float, today: date, earnings: Optional[str]) -> Dict[str, Any]:
    strikes = sorted(float(k) for k in puts["strike"].unique())
    for k in sorted((k for k in strikes if k <= stop), reverse=True):
        mid = _mid(puts[puts["strike"] == k].iloc[0])
        if mid and mid > 0:
            days = max((date.fromisoformat(exp) - today).days, 1)
            out = {"structure": "CASH_SECURED_PUT", "expiration": exp, "short_strike": k, "long_strike": None,
                   "mid_credit": round(mid, 2), "max_loss": round((k - mid) * 100, 2), "max_profit": round(mid * 100, 2),
                   "breakeven": round(k - mid, 2), "yield_pct": round(100 * mid / k, 2),
                   "annualized_pct": round(100 * mid / k * 365 / days, 1), "below_spot_pct": round(100 * (spot - k) / spot, 1)}
            out["summary"] = (f"{exp} {k:g} put, credit {mid:.2f}, collateral ${k * 100:,.0f}, BE {out['breakeven']}, "
                              f"{out['yield_pct']}% in {days}d (~{out['annualized_pct']}% ann), {out['below_spot_pct']}% below spot"
                              f"{_flag(exp, earnings)} · {TAG}")
            return out
    return {"structure": "NONE", "summary": f"no liquid put at or below stop {stop:.2f} for {exp}"}


def _broker_chain(ticker: str):
    """(expiries, fetch(exp)->(calls, puts)) from the Schwab > Tastytrade > Alpaca chain, or None."""
    try:
        from src.clients.options_client import fetch_chain_grid

        res = fetch_chain_grid(ticker)
    except Exception as e:
        logger.warning(f"[{ticker}] broker chain failed: {e}")
        return None
    if not res:
        return None
    by_exp: Dict[str, Dict[str, list]] = {}
    for (exp, strike), row in res["grid"].items():
        for side in ("calls", "puts"):
            q = row.get(side)
            if q and q.get("ask"):
                by_exp.setdefault(exp, {"calls": [], "puts": []})[side].append(
                    {"strike": strike, "bid": q.get("bid") or 0.0, "ask": q["ask"], "delta": q.get("delta")})
    if not by_exp:
        return None
    logger.info(f"[{ticker}] options menu chain from {res['source']}")
    frames = {e: (pd.DataFrame(v["calls"], columns=["strike", "bid", "ask", "delta"]),
                  pd.DataFrame(v["puts"], columns=["strike", "bid", "ask", "delta"])) for e, v in by_exp.items()}
    _broker_chain.last_source = res["source"]
    return sorted(frames), (lambda e: frames[e])


def load_chain(ticker: str):
    """(expiries, fetch(exp)->(calls, puts), source) from the broker routing, else yfinance."""
    chain = _broker_chain(ticker)
    if chain is not None:
        return chain[0], chain[1], getattr(_broker_chain, "last_source", "broker")
    import yfinance as yf

    t = yf.Ticker(ticker.replace(".", "-"))
    return list(t.options), (lambda e: (lambda c: (c.calls, c.puts))(t.option_chain(e))), "yfinance"


def shifted_chain(chain, spot_now: float, spot_at: float, today: date, entry_day: date):
    """Reprice every quoted strike as if spot were spot_at on entry_day: each strike keeps its own implied vol
    (from today's mid), time shrinks to entry_day. Puts via put-call parity, zero rates."""
    exps, fetch = chain[0], chain[1]
    cache: Dict[str, Any] = {}

    def conv(df, put: bool, y_now: float, y_at: float):
        rows = []
        for _, r in df.iterrows():
            m, k = _mid(r), _f(r.get("strike"))
            if m is None or k <= 0:
                continue
            iv = _implied_vol(spot_now, k, m + spot_now - k if put else m, y_now)
            if not iv:
                continue
            c = _bs_call(spot_at, k, iv, y_at)
            px = c - spot_at + k if put else c
            if px >= 0.05:
                px = round(px, 2)
                rows.append({"strike": k, "bid": px, "ask": px, "delta": _delta(spot_at, k, iv, y_at)})
        return pd.DataFrame(rows, columns=["strike", "bid", "ask", "delta"])

    def f(e: str):
        if e not in cache:
            exp = date.fromisoformat(e)
            y_now, y_at = max((exp - today).days, 1) / 365, (exp - entry_day).days / 365
            if y_at <= 0:
                cache[e] = (pd.DataFrame(columns=["strike", "bid", "ask", "delta"]),) * 2
            else:
                calls, puts = fetch(e)
                cache[e] = (conv(calls, False, y_now, y_at), conv(puts, True, y_now, y_at))
        return cache[e]

    return [e for e in exps if date.fromisoformat(e) > entry_day], f


def build_options_menu(ticker: str, spot: float, plan: Dict[str, Any], em_pct: float = 0.0,
                       earnings: Optional[str] = None, today: Optional[date] = None, chain=None,
                       tactical_lo: int = 28) -> Dict[str, Any]:
    """chain = (expiries, fetch(exp)->(calls_df, puts_df)[, source]). Defaults to broker routing, then yfinance."""
    today = today or date.today()
    if chain is None:
        chain = load_chain(ticker)
    exps, fetch = chain[0], chain[1]
    source = chain[2] if len(chain) > 2 else "custom"
    lv = _levels(plan, spot, em_pct)
    menu: Dict[str, Any] = {"levels_used": lv, "source": source}
    for key, lo, hi, want in (("tactical_spread", tactical_lo, 50, 37), ("leaps", 150, 800, 270), ("income_or_csp", 20, 50, 33)):
        exp = _pick_exp(exps, today, lo, hi, want, fetch, spot)
        if not exp:
            menu[key] = {"structure": "NONE", "summary": f"no expiry in {lo}-{hi} DTE"}
            continue
        try:
            calls, puts = fetch(exp)
            if key == "tactical_spread":
                menu[key] = _tier1(calls, exp, spot, lv["t1"], lv["stop"], earnings)
            elif key == "leaps":
                menu[key] = _tier2(calls, exp, spot, today, earnings)
            else:
                menu[key] = _tier3(puts, exp, spot, lv["stop"], today, earnings)
        except Exception as e:
            menu[key] = {"structure": "NONE", "summary": f"chain error {exp}: {type(e).__name__}"}
    return menu


def attach_options_menu(watch_data: Dict[str, Any], ticker: str, spot: float, em_pct: float = 0.0,
                        earnings: Optional[str] = None) -> None:
    """Overwrite watch_data['options_menu'] with chain-built tiers. LONG only; never sets actionable."""
    if str(watch_data.get("side") or "LONG").upper() != "LONG" or spot <= 0:
        return
    try:
        menu = build_options_menu(ticker, spot, watch_data.get("shares_plan") or {}, em_pct, earnings)
    except Exception as e:
        logger.warning(f"[{ticker}] options menu skipped: {e}")
        return
    if any(menu[k].get("structure") not in (None, "NONE") for k in ("tactical_spread", "leaps", "income_or_csp")):
        watch_data["options_menu"] = menu
