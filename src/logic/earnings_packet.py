"""Earnings packet: date and source, historical reaction, options implied move, Kalshi odds. Context for the stance, never a veto."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from src.tracking import trade_rules

logger = logging.getLogger(__name__)


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
