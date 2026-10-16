"""Deterministic facts handed to the models so tool calls go to judgement, not lookups: option-chain summary, regime strip, sector RS, earnings packet."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Dict, Optional, Tuple

from src.tracking import trade_rules

logger = logging.getLogger(__name__)

SECTOR_ETF = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Energy": "XLE", "Industrials": "XLI", "Utilities": "XLU",
    "Real Estate": "XLRE", "Basic Materials": "XLB", "Communication Services": "XLC",
}
_regime_cache: Tuple[float, str] = (0.0, "")


def _ret(closes, n: int) -> Optional[float]:
    return (float(closes.iloc[-1]) / float(closes.iloc[-1 - n]) - 1) * 100 if len(closes) > n else None


def regime_strip() -> str:
    global _regime_cache
    if time.time() - _regime_cache[0] < 3600 and _regime_cache[1]:
        return _regime_cache[1]
    try:
        import yfinance as yf

        spy = yf.Ticker("SPY").history(period="14mo")["Close"]
        vix = yf.Ticker("^VIX").history(period="3mo")["Close"]
        sma200 = float(spy.tail(200).mean())
        txt = (
            f"REGIME STRIP: SPY {float(spy.iloc[-1]):.2f} {'above' if spy.iloc[-1] > sma200 else 'below'} 200SMA {sma200:.2f}; "
            f"SPY 5d {_ret(spy, 5):+.1f}% 21d {_ret(spy, 21):+.1f}%; VIX {float(vix.iloc[-1]):.1f} (21d avg {float(vix.tail(21).mean()):.1f})"
        )
        _regime_cache = (time.time(), txt)
        return txt
    except Exception as e:
        logger.debug(f"regime strip failed: {e}")
        return ""


def sector_relative_strength(ticker: str) -> str:
    try:
        import yfinance as yf

        sector = (yf.Ticker(ticker).info or {}).get("sector")
        etf = SECTOR_ETF.get(sector or "")
        if not etf:
            return ""
        hist = yf.download([ticker, etf, "SPY"], period="3mo", progress=False, auto_adjust=True)["Close"]
        r = {s: _ret(hist[s].dropna(), 21) for s in (ticker, etf, "SPY")}
        if any(v is None for v in r.values()):
            return ""
        return (f"SECTOR RS (21d): {ticker} {r[ticker]:+.1f}% vs {sector} {etf} {r[etf]:+.1f}% vs SPY {r['SPY']:+.1f}% "
                f"=> vs sector {r[ticker] - r[etf]:+.1f}pp, sector vs SPY {r[etf] - r['SPY']:+.1f}pp")
    except Exception as e:
        logger.debug(f"[{ticker}] sector RS failed: {e}")
        return ""


def chain_summary(ticker: str, spot: Optional[float]) -> str:
    """ATM call/put mid and IV for the expiries nearest 30-45 DTE and nearest 6+ months."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        px = spot or float(t.fast_info.get("last_price") or 0)
        if px <= 0 or not t.options:
            return ""
        today = date.today()
        dte = {e: (date.fromisoformat(e) - today).days for e in t.options}
        picks = []
        for lo, hi in ((30, 45), (180, 400)):
            c = [e for e, d in dte.items() if lo <= d <= hi]
            if c:
                picks.append(c[0])
        lines = []
        for e in picks:
            ch = t.option_chain(e)
            k = float(ch.calls.iloc[(ch.calls["strike"] - px).abs().argsort()[:1]]["strike"].iloc[0])
            c = ch.calls[ch.calls["strike"] == k].iloc[0]
            p = ch.puts[ch.puts["strike"] == k].iloc[0]
            mid = lambda r: (float(r["bid"]) + float(r["ask"])) / 2 if float(r["ask"]) > 0 else float(r["lastPrice"])
            lines.append(f"{e} ({dte[e]} DTE) ATM {k:g}: call mid {mid(c):.2f} (IV {float(c['impliedVolatility']) * 100:.0f}%), put mid {mid(p):.2f} (IV {float(p['impliedVolatility']) * 100:.0f}%)")
        return "OPTION CHAIN SUMMARY: " + " | ".join(lines) if lines else ""
    except Exception as e:
        logger.debug(f"[{ticker}] chain summary failed: {e}")
        return ""


def build_prefetch_block(ticker: str, spot: Optional[float]) -> str:
    parts: Dict[str, str] = {"regime": regime_strip(), "sector": sector_relative_strength(ticker), "chain": chain_summary(ticker, spot)}
    body = "\n".join(v for v in parts.values() if v)
    return f"[PRE-FETCHED FACTS, do not re-fetch]:\n{body}" if body else ""


# --- earnings packet: date and source, reaction history, implied move, Kalshi. Context for the stance, never a veto. ---

def _historical_reaction(ticker: str, n: int = 8) -> Dict[str, Any]:
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        ed = t.get_earnings_dates(limit=n + 4)
        if ed is None or ed.empty:
            return {}
        hist = t.history(period="3y", auto_adjust=True)
        if hist.empty:
            return {}
        hist.index = hist.index.tz_localize(None).normalize()
        rows = []
        today = date.today()
        for ts in ed.index:
            d = ts.date()
            if d >= today:
                continue
            dt = hist.index.searchsorted(ts.tz_localize(None).normalize())
            after_hours = ts.hour >= 12
            i0 = dt + 1 if after_hours else dt
            if i0 < 1 or i0 + 3 >= len(hist):
                continue
            ref = float(hist["Close"].iloc[i0 - 1])
            rows.append({
                "date": str(d),
                "day1_pct": round((float(hist["Close"].iloc[i0]) / ref - 1) * 100, 1),
                "drift_3d_pct": round((float(hist["Close"].iloc[i0 + 3]) / float(hist["Close"].iloc[i0]) - 1) * 100, 1),
            })
            if len(rows) >= n:
                break
        if not rows:
            return {}
        avg_abs = sum(abs(r["day1_pct"]) for r in rows) / len(rows)
        ups = sum(1 for r in rows if r["day1_pct"] > 0)
        return {"avg_abs_day1_pct": round(avg_abs, 1), "up_count": ups, "n": len(rows), "last": rows[:4]}
    except Exception as e:
        logger.debug(f"[{ticker}] reaction history failed: {e}")
        return {}


def _implied_move(ticker: str, report: date, spot: Optional[float]) -> Dict[str, Any]:
    """ATM straddle mid on the first expiry on or after the report date, as % of spot."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        exps = [e for e in (t.options or []) if date.fromisoformat(e) >= report]
        if not exps:
            return {}
        exp = exps[0]
        chain = t.option_chain(exp)
        px = spot or float(t.fast_info.get("last_price") or 0)
        if px <= 0:
            return {}
        c = chain.calls.iloc[(chain.calls["strike"] - px).abs().argsort()[:1]]
        p = chain.puts.iloc[(chain.puts["strike"] - px).abs().argsort()[:1]]

        def mid(row):
            b, a = float(row["bid"].iloc[0]), float(row["ask"].iloc[0])
            return (b + a) / 2 if a > 0 else float(row["lastPrice"].iloc[0])

        straddle = mid(c) + mid(p)
        return {"expiry": exp, "strike": float(c["strike"].iloc[0]), "straddle": round(straddle, 2), "implied_move_pct": round(straddle / px * 100, 1)}
    except Exception as e:
        logger.debug(f"[{ticker}] implied move failed: {e}")
        return {}


def _kalshi(ticker: str) -> str:
    try:
        from src.clients.kalshi_client import fetch_prediction_market

        out = fetch_prediction_market(f"{ticker} earnings")
        return out if out.startswith("KALSHI PREDICTION MARKETS") else ""
    except Exception:
        return ""


def build_earnings_packet(ticker: str, spot: Optional[float] = None) -> Dict[str, Any]:
    from src.clients.earnings_client import get_next_earnings_days, resolve_next_earnings_date

    ticker = ticker.upper()
    report, source = resolve_next_earnings_date(ticker)
    days = get_next_earnings_days(ticker) if report else None
    pkt: Dict[str, Any] = {
        "ticker": ticker,
        "report_date": str(report) if report else None,
        "source": source,
        "trading_days": days,
        "in_window": trade_rules.earnings_in_window(days),
    }
    if report and days is not None and days <= 60:
        pkt["reaction"] = _historical_reaction(ticker)
        pkt["implied"] = _implied_move(ticker, report, spot)
        pkt["kalshi"] = _kalshi(ticker)
    return pkt


def format_earnings_packet(pkt: Dict[str, Any]) -> str:
    if not pkt.get("report_date"):
        return f"EARNINGS PACKET: next report date unknown ({pkt.get('source') or 'no source'})."
    lines = [
        "EARNINGS PACKET (context, not a veto; the plan declares HOLD_THROUGH / EXIT_BEFORE / EARNINGS_PLAY):",
        f"- Report {pkt['report_date']} ({pkt['trading_days']} trading days, source {pkt['source']}); inside 21-bar window: {pkt['in_window']}",
    ]
    r = pkt.get("reaction") or {}
    if r:
        lines.append(
            f"- Last {r['n']} reports: avg |day-1 move| {r['avg_abs_day1_pct']}%, up {r['up_count']}/{r['n']}; recent "
            + ", ".join(f"{x['date']} {x['day1_pct']:+.1f}% (3d drift {x['drift_3d_pct']:+.1f}%)" for x in r["last"])
        )
    i = pkt.get("implied") or {}
    if i:
        lines.append(f"- Options implied move: {i['implied_move_pct']}% (ATM {i['strike']:g} straddle ${i['straddle']} exp {i['expiry']})")
        if r:
            hist = r["avg_abs_day1_pct"]
            tag = "rich" if i["implied_move_pct"] > hist * 1.15 else "cheap" if i["implied_move_pct"] < hist * 0.85 else "fair"
            lines.append(f"- Implied vs historical: {i['implied_move_pct']}% vs {hist}% ({tag})")
    if pkt.get("kalshi"):
        lines.append(pkt["kalshi"])
    return "\n".join(lines)
