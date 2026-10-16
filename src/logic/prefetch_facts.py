"""Deterministic facts handed to Pass 2 so the models spend tool calls on judgement, not lookups: option-chain summary, regime strip, sector relative strength."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Dict, Optional, Tuple

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
