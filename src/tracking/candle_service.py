"""
src/tracking/candle_service.py — Deterministic candles + level evaluation for chat.

The chat answers "did it cross 355?" with numbers, not model arithmetic. This
service fetches real candles and evaluates named levels against them:

  * daily, 5m and 1m candles — intraday candles reuse
    ``schwab_client.get_intraday_candles`` (Schwab, yfinance fallback); daily bars
    come from Schwab price history, then the shared daily-bars helper, never from a
    stale scrape CSV while a live source answers;
  * the day's high / low / close and the live quote;
  * per named level: crossed or not, the FIRST TOUCH timestamp (or "never touched"),
    and whether it HELD on close;
  * R:R at market from the stored plan's own stop / T1 / T2 — the plan's numbers,
    measured at the live price, never the report's spot.

Absent means absent: a series that cannot be fetched is reported as such instead of
being fabricated from an older artifact.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _num(val: Any) -> float:
    if val is None or val == "" or val == "N/A":
        return 0.0
    try:
        f = float(val)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _schwab_daily_candles(sym: str, days: int) -> List[Dict[str, Any]]:
    """Daily bars from Schwab price history (primary source). [] when unavailable."""
    try:
        from datetime import datetime as _dt

        from src.clients.schwab_client import get_schwab_client

        client = get_schwab_client()
        client.session.timeout = 6.0
        if days <= 10:
            period_type, period = client.PriceHistory.PeriodType.DAY, max(1, days)
        else:
            period_type, period = client.PriceHistory.PeriodType.MONTH, 6
        resp = client.get_price_history(
            sym,
            period_type=period_type,
            period=period,
            frequency_type=client.PriceHistory.FrequencyType.DAILY,
            frequency=client.PriceHistory.Frequency.EVERY_DAY,
        )
        if getattr(resp, "status_code", 0) != 200:
            return []
        out: List[Dict[str, Any]] = []
        for c in resp.json().get("candles", []):
            ts = c.get("datetime")
            out.append({
                "date": _dt.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else "",
                "open": _num(c.get("open")),
                "high": _num(c.get("high")),
                "low": _num(c.get("low")),
                "close": _num(c.get("close")),
                "volume": int(_num(c.get("volume"))),
            })
        return out
    except Exception as e:
        logger.debug(f"[candle_service] Schwab daily candles note for {sym}: {e}")
        return []


def _daily_bars_fallback(sym: str, days: int) -> List[Dict[str, Any]]:
    """Daily bars from the shared helper (fresh local CSV, else Yahoo)."""
    try:
        from datetime import timedelta

        from src.tracking.execution_validator import get_bars_since_date

        start = (_dt_today() - timedelta(days=max(days * 2, 30))).strftime("%Y-%m-%d")
        bars = get_bars_since_date(sym, start)
        if bars is None or len(bars) == 0:
            return []
        out: List[Dict[str, Any]] = []
        for i in range(len(bars)):
            row = bars.iloc[i]
            out.append({
                "date": str(bars.index[i])[:10],
                "open": _num(_row_get(row, "Open", "open")),
                "high": _num(_row_get(row, "High", "high")),
                "low": _num(_row_get(row, "Low", "low")),
                "close": _num(_row_get(row, "Close", "close")),
                "volume": int(_num(_row_get(row, "Volume", "volume"))),
            })
        return out
    except Exception as e:
        logger.debug(f"[candle_service] daily bars fallback failed for {sym}: {e}")
        return []


def _dt_today():
    from datetime import datetime as _dt

    return _dt.now().date()


def _row_get(row: Any, *keys: str) -> Any:
    if hasattr(row, "get"):
        for k in keys:
            v = row.get(k)
            if v is not None:
                return v
    for k in keys:
        if isinstance(row, dict) and row.get(k) is not None:
            return row.get(k)
    return None


def _normalize_intraday(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Schwab/yfinance candle dicts -> a uniform {datetime, ts, time_et, date, open, high, low, close, volume}."""
    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    out: List[Dict[str, Any]] = []
    for c in candles or []:
        ts = c.get("datetime")
        if ts:
            try:
                dt_et = _dt.fromtimestamp(ts / 1000, tz=_tz.utc).astimezone(ET)
                ts_str = dt_et.strftime("%Y-%m-%d %H:%M")
                time_et_str = dt_et.strftime("%I:%M %p ET")
                date_str = dt_et.strftime("%Y-%m-%d")
                minutes = dt_et.hour * 60 + dt_et.minute
                session = "PRE" if minutes < 570 else ("POST" if minutes >= 960 else "RTH")
            except Exception:
                ts_str, time_et_str, date_str, session = str(ts), "", "", "RTH"
        else:
            ts_str, time_et_str, date_str, session = "", "", "", "RTH"
        out.append({
            "datetime": ts,
            "ts": ts_str,
            "time_et": time_et_str,
            "date": date_str,
            "session": session,
            "open": _num(c.get("open")),
            "high": _num(c.get("high")),
            "low": _num(c.get("low")),
            "close": _num(c.get("close")),
            "volume": int(_num(c.get("volume"))),
        })
    return out


def fetch_candles(
    ticker: str,
    interval: str = "5m",
    days: int = 5,
) -> Dict[str, Any]:
    """Daily, 5m and 1m candles + the day's high/low/close and the live quote.

    Returns the candle lists (``daily_candles``, ``m5_candles``, ``m1_candles``),
    session stats (``day_open/high/low/close``, ``live_price``, ``volume``) and the
    preloaded DataFrames (``df_daily``, ``df_intraday``) for a sandbox.
    """
    sym = (ticker or "").strip().upper().replace(".", "/")
    if not sym:
        return {"error": "Missing ticker symbol"}
    days = max(1, min(int(days or 5), 60))

    from src.clients.schwab_client import get_intraday_candles, get_realtime_quote

    quote: Dict[str, Any] = {}
    try:
        q = get_realtime_quote(sym)
        if q and _num(q.get("last_price")) > 0:
            quote = q
    except Exception as e_q:
        logger.debug(f"[candle_service] Schwab quote note for {sym}: {e_q}")

    if not quote:
        try:
            from src.clients.quote_router import quote_router

            qd = quote_router.get_live_execution_quote(sym) or quote_router.get_watchlist_quote(sym)
            if qd and qd.last_price > 0:
                quote = qd.to_dict() if hasattr(qd, "to_dict") else {}
        except Exception:
            pass

    # Intraday: the shared Schwab helper (5m and 1m), one call each.
    m5_candles = _normalize_intraday(get_intraday_candles(sym, frequency=5, lookback_bars=288))
    m1_candles = _normalize_intraday(get_intraday_candles(sym, frequency=1, lookback_bars=1440))
    if not m1_candles and m5_candles:
        m1_candles = m5_candles  # touch timing falls back to 5m resolution

    # Daily: Schwab first, then the shared daily-bars helper. Never a stale scrape CSV.
    daily_candles = _schwab_daily_candles(sym, days) or _daily_bars_fallback(sym, days)

    latest_daily = daily_candles[-1] if daily_candles else {}
    live_price = _num(quote.get("last_price")) or _num(latest_daily.get("close"))
    day_open = _num(quote.get("open")) or _num(latest_daily.get("open"))
    # The session's own extremes: the live quote's high/low cover the running session,
    # the last daily bar covers a closed one.
    day_high = max(_num(quote.get("high")), _num(latest_daily.get("high")))
    day_low_candidates = [v for v in (_num(quote.get("low")), _num(latest_daily.get("low"))) if v > 0]
    day_low = min(day_low_candidates) if day_low_candidates else 0.0
    day_close = live_price or _num(latest_daily.get("close"))
    volume = int(_num(quote.get("volume")) or _num(latest_daily.get("volume")))

    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    session_date = _dt.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    prior_close = _num(latest_daily.get("close"))

    import pandas as pd

    return {
        "ticker": sym,
        "interval": interval,
        "days": days,
        "session_date": session_date,
        "prior_close": round(prior_close, 4),
        "day_open": round(day_open, 4),
        "day_high": round(day_high, 4),
        "day_low": round(day_low, 4),
        "day_close": round(day_close, 4),
        "live_price": round(live_price, 4),
        "volume": volume,
        "daily_candles": daily_candles[-days:],
        "m5_candles": m5_candles,
        "m1_candles": m1_candles,
        "df_daily": pd.DataFrame(daily_candles) if daily_candles else pd.DataFrame(),
        "df_intraday": pd.DataFrame(m5_candles) if m5_candles else pd.DataFrame(),
        "source": quote.get("source", "SCHWAB"),
    }


def evaluate_levels_against_candles(
    candles_data: Dict[str, Any],
    levels: List[float],
    side: str = "LONG",
) -> List[Dict[str, Any]]:
    """Deterministically evaluate price levels against the session's candles.

    For each level: crossed or not, the FIRST TOUCH timestamp (or "never touched"),
    whether it HELD on close, the position at the live price, and the plain outcome.
    A level above prior close / open is treated as resistance / breakout (cross = day high traded through
    it); a level below as support (cross = day low traded through it).
    """
    results: List[Dict[str, Any]] = []
    if not levels:
        return results

    day_high = _num(candles_data.get("day_high"))
    day_low = _num(candles_data.get("day_low"))
    day_close = _num(candles_data.get("day_close"))
    live_price = _num(candles_data.get("live_price")) or day_close
    day_open = _num(candles_data.get("day_open"))
    prior_close = _num(candles_data.get("prior_close"))
    ref_price = prior_close if prior_close > 0 else (day_open if day_open > 0 else live_price)

    # The requested interval drives the touch-time resolution: 1m when asked for,
    # else the 5m series (1m remains the default when no 5m bars exist).
    interval = str(candles_data.get("interval") or "5m").lower()
    if interval in ("1m", "1min", "60s"):
        timeline = candles_data.get("m1_candles") or candles_data.get("m5_candles") or []
    else:
        timeline = candles_data.get("m5_candles") or candles_data.get("m1_candles") or []
    session_date = candles_data.get("session_date") or ""
    session_bars = [b for b in timeline if b.get("date") == session_date or b.get("ts", "").startswith(session_date)]
    if not session_bars and timeline:
        # No bars dated "today" (weekend, pre-open, or past midnight ET): the session is the last one with bars.
        last_date = timeline[-1].get("date")
        session_bars = [b for b in timeline if b.get("date") == last_date] or timeline

    for raw_lvl in levels:
        lvl = _num(raw_lvl)
        if lvl <= 0:
            continue

        is_up = lvl >= ref_price if ref_price > 0 else (lvl >= live_price)
        crossed = (day_high >= lvl) if is_up else (day_low <= lvl)

        def _touched(bar):
            return (_num(bar.get("high")) >= lvl) if is_up else (_num(bar.get("low")) <= lvl)

        def _label(bar):
            return bar.get("time_et") or bar.get("ts") or ""

        # Day high/low are regular-session figures, so the headline touch is the first REGULAR-session
        # touch. An earlier pre/post-market print is reported separately, never as the cross.
        rth_touch = next((b for b in session_bars if b.get("session", "RTH") == "RTH" and _touched(b)), None)
        ext_touch = next((b for b in session_bars if b.get("session", "RTH") != "RTH" and _touched(b)), None)

        first_touch_time: Optional[str] = None
        if crossed:
            if rth_touch:
                first_touch_time = _label(rth_touch)
            elif ext_touch:
                first_touch_time = f"{_label(ext_touch)} ({ext_touch.get('session')}-market print; regular-session bar unresolved)"
            else:
                first_touch_time = "touched (session high/low; bar time unavailable)"
            if rth_touch and ext_touch and ext_touch is not rth_touch and (ext_touch.get("datetime") or 0) < (rth_touch.get("datetime") or 0):
                first_touch_time += f"; first extended-hours print {_label(ext_touch)} ({ext_touch.get('session')})"
        elif ext_touch:
            first_touch_time = f"never touched in regular session (extended-hours print only: {_label(ext_touch)} {ext_touch.get('session')})"
        else:
            first_touch_time = "never touched"

        held_on_close: Optional[bool] = None
        outcome = "never touched"
        if crossed:
            if is_up:
                held_on_close = (day_close >= lvl)
                outcome = "broke_and_holding" if held_on_close else "failed_breakout_closed_back_below"
            else:
                held_on_close = (day_close >= lvl)
                outcome = "held_support_on_close" if held_on_close else "lost_support_closed_below"

        dist_dollars = round(live_price - lvl, 4)
        dist_pct = round((dist_dollars / lvl) * 100, 2) if lvl else 0.0
        if abs(dist_dollars) < 0.01:
            pos_str = "AT_LEVEL"
        elif dist_dollars > 0:
            pos_str = f"ABOVE by ${dist_dollars:.2f} (+{dist_pct:.2f}%)"
        else:
            pos_str = f"BELOW by ${abs(dist_dollars):.2f} ({dist_pct:.2f}%)"

        results.append({
            "level": round(lvl, 4),
            "crossed": crossed,
            "first_touch_time": first_touch_time,
            "held_on_close": held_on_close,
            "outcome": outcome,
            "day_high": day_high,
            "day_low": day_low,
            "day_close": day_close,
            "live_price": live_price,
            "distance_dollars": dist_dollars,
            "distance_percent": dist_pct,
            "position": pos_str,
        })

    return results


def rr_at_market_for(ticker: str, live_price: float) -> Optional[Dict[str, Any]]:
    """R:R at market from the ticker's stored plan: stop / T1 / T2 at the live price,
    plus the lane's MEASURED / UNMEASURED tag. None when no plan is stored."""
    try:
        from src.tracking.plan_outcome import judge_at_market, load_prior_plan

        loaded = load_prior_plan(ticker)
        if not loaded or not loaded.get("plan"):
            return None
        side = str(loaded.get("side") or "LONG").upper()
        j = judge_at_market(loaded["plan"], live_price, side)
        lane_val = str(loaded["plan"].get("setup_lane") or loaded.get("setup_lane") or "").upper()
        return {
            "position": j.get("position"),
            "outcome": j.get("outcome"),
            "rr_at_market_to_t1": j.get("rr_at_market_to_t1"),
            "rr_at_market_to_t2": j.get("rr_at_market_to_t2"),
            "best_rr": j.get("best_rr_at_market") or j.get("best_rr"),
            "stop": loaded["plan"].get("tactical_stop"),
            "target_1": loaded["plan"].get("target_1"),
            "target_2": loaded["plan"].get("target_2"),
            "lane": lane_val or None,
            "lane_label": (
                "MEASURED" if lane_val in ("CODE20", "RR_SETUP", "RSI2") else "UNMEASURED"
            ),
        }
    except Exception as e:
        logger.debug(f"[candle_service] R:R at market for {ticker} unavailable: {e}")
        return None


def get_candles_tool(
    ticker: str,
    interval: str = "5m",
    days: int = 5,
    levels: Optional[List[float]] = None,
) -> str:
    """The chat tool's entry point: deterministic numbers for a level question.

    Returns the day's high/low/close, the live quote, and per named level: crossed
    or not, the first-touch time (or "never touched"), held on close, and the
    position at the live price — plus R:R at market from the stored plan. The model
    reports these numbers; it does not compute them.
    """
    data = fetch_candles(ticker, interval=interval, days=days)
    if "error" in data:
        return f"Error: {data['error']}"

    sym = data["ticker"]
    dh, dl, do, dc, lp = (
        data["day_high"], data["day_low"], data["day_open"], data["day_close"], data["live_price"],
    )
    vol = data["volume"]

    lines = [
        f"=== DETERMINISTIC CANDLES & LEVELS: {sym} ({interval}, {data['days']}d) ===",
        f"- Day Open: ${do:.2f}",
        f"- Day High: ${dh:.2f}",
        f"- Day Low: ${dl:.2f}",
        f"- Day Close: ${dc:.2f}",
        f"- Live Quote: ${lp:.2f} (source {data.get('source', 'SCHWAB')})",
        f"- Session Volume: {vol:,}",
    ]

    eval_results = evaluate_levels_against_candles(data, levels or []) if levels else []
    if eval_results:
        lines.append("")
        lines.append("### Level Evaluations (this session)")
        lines.append("| Level | Crossed? | First Touch Time | Held on Close? | Outcome | Position at Live |")
        lines.append("|---|---|---|---|---|---|")
        for ev in eval_results:
            crossed_str = "YES" if ev["crossed"] else "NO"
            held_str = "YES" if ev["held_on_close"] is True else ("NO" if ev["held_on_close"] is False else "N/A")
            lines.append(
                f"| ${ev['level']:.2f} | {crossed_str} | {ev['first_touch_time']} | {held_str} "
                f"| {ev['outcome']} | {ev['position']} |"
            )

    rr = rr_at_market_for(sym, lp)
    if rr:
        lines.append("")
        lines.append(
            f"### R:R at market (stored plan vs live ${lp:.2f}): position {rr['position']}, "
            f"outcome {rr['outcome']}, R:R to T1 {rr['rr_at_market_to_t1']}, "
            f"R:R to T2 {rr['rr_at_market_to_t2']}, "
            f"stop {rr['stop']}, T1 {rr['target_1']}, T2 {rr['target_2']}, "
            f"lane {rr['lane']} [{rr['lane_label']}]"
        )

    daily = data.get("daily_candles") or []
    if daily:
        lines.append("")
        lines.append("### Recent Daily Candles")
        lines.append("| Date | Open | High | Low | Close | Volume |")
        lines.append("|---|---|---|---|---|---|")
        for c in daily[-10:]:
            lines.append(
                f"| {c['date']} | ${c['open']:.2f} | ${c['high']:.2f} | ${c['low']:.2f} "
                f"| ${c['close']:.2f} | {c.get('volume', 0):,} |"
            )

    m5 = data.get("m5_candles") or []
    if m5:
        lines.append("")
        lines.append("### Latest 5m Candles")
        lines.append("| Time | Open | High | Low | Close | Volume |")
        lines.append("|---|---|---|---|---|---|")
        for c in m5[-8:]:
            lines.append(
                f"| {c.get('ts') or c.get('datetime')} | ${c['open']:.2f} | ${c['high']:.2f} "
                f"| ${c['low']:.2f} | ${c['close']:.2f} | {c.get('volume', 0):,} |"
            )

    machine_payload: Dict[str, Any] = {
        "ticker": sym,
        "day_high": dh,
        "day_low": dl,
        "day_open": do,
        "day_close": dc,
        "live_price": lp,
        "volume": vol,
        "level_evaluations": eval_results,
    }
    if rr:
        machine_payload["rr_at_market"] = rr
    lines.append(f"\nSTRUCTURED_CANDLES: {json.dumps(machine_payload, default=str)}")

    return "\n".join(lines)
