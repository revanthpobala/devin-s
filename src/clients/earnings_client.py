from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 12 * 3600
_NEG_TTL_S = 1800
_cache_lock = threading.Lock()


def _cache_path():
    from src import config

    return config.BASE_DIR / "data" / "cache" / "earnings_dates.json"


def _cache_read() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_write(sym: str, entry: dict) -> None:
    with _cache_lock:
        data = _cache_read()
        data[sym] = entry
        try:
            p = _cache_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.debug(f"earnings cache write failed: {e}")


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            return None
    if isinstance(v, str) and len(v) >= 10:
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def _tasty_date(sym: str) -> Optional[date]:
    from src.clients.tastytrade_client import get_tastytrade_client

    for m in get_tastytrade_client().get_market_metrics(sym) or []:
        if str(m.get("symbol", "")).upper() == sym:
            return _to_date((m.get("earnings") or {}).get("expected-report-date"))
    return None


def _yf_dates(sym: str) -> tuple[Optional[date], Optional[date]]:
    """(next future earnings date, last reported date) from yfinance."""
    import yfinance as yf

    t = yf.Ticker(sym)
    today = date.today()
    nxt = last = None
    try:
        cal = t.calendar
        raw = cal.get("Earnings Date") if hasattr(cal, "get") else None
        for v in raw or []:
            d = _to_date(v)
            if d and d >= today and (nxt is None or d < nxt):
                nxt = d
    except Exception:
        pass
    try:
        ed = t.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            for idx, row in ed.iterrows():
                d = _to_date(idx)
                if d is None:
                    continue
                if d >= today and nxt is None:
                    nxt = d
                elif d < today and str(row.get("Reported EPS")) not in ("nan", "None") and (last is None or d > last):
                    last = d
    except Exception:
        pass
    return nxt, last


def _grounded_date(sym: str, last_report: Optional[date]) -> Optional[date]:
    """Last resort. Accepts a Google-grounded date only if it fits the ~quarterly cadence."""
    from src.clients.google_grounding_client import ask_grounded

    res = ask_grounded(
        f"What is the next quarterly earnings report date for {sym}? "
        f"Answer with only the date as YYYY-MM-DD, or UNKNOWN if not announced or confirmed."
    )
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", res.get("text") or "")
    d = _to_date(m.group(1)) if m else None
    today = date.today()
    if d is None or d < today or (d - today).days > 120:
        return None
    if last_report is not None and not 60 <= (d - last_report).days <= 125:
        logger.warning(f"[{sym}] grounded earnings date {d} rejected: breaks quarterly cadence after {last_report}")
        return None
    return d


def resolve_next_earnings_date(ticker: str) -> tuple[Optional[date], str]:
    """Next earnings date and the source that supplied it, cached 12h on disk.

    Order: Tastytrade expected-report-date, yfinance, Google grounding (cadence-checked).
    """
    sym = (ticker or "").strip().upper()
    if not sym or sym in ("UNKNOWN", "NONE"):
        return None, "none"
    cached = _cache_read().get(sym)
    if cached:
        try:
            age = (datetime.now() - datetime.fromisoformat(cached["fetched"])).total_seconds()
            d = _to_date(cached.get("date"))
            if d is None and age < _NEG_TTL_S:
                return None, "none"
            if d is not None and d >= date.today() and age < _CACHE_TTL_S:
                return d, cached.get("source", "cache")
        except Exception:
            pass

    last_report: Optional[date] = None
    found: Optional[date] = None
    source = "none"

    def _try(name, fn):
        nonlocal found, source
        if found is not None:
            return
        try:
            d = fn()
            if d and d >= date.today():
                found, source = d, name
        except Exception as e:
            logger.debug(f"[{sym}] earnings via {name} failed: {e}")

    _try("tastytrade", lambda: _tasty_date(sym))
    if found is None:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_yf_dates, sym)
        try:
            nxt, last_report = fut.result(timeout=8.0)
            if nxt:
                found, source = nxt, "yfinance"
        except Exception as e:
            logger.debug(f"[{sym}] earnings via yfinance failed: {e}")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    _try("google-grounding (cadence-checked)", lambda: _grounded_date(sym, last_report))

    _cache_write(sym, {
        "date": found.isoformat() if found else None,
        "source": source,
        "fetched": datetime.now().isoformat(timespec="seconds"),
    })
    return found, source


def _bdays_between(start: date, end: date) -> int:
    import pandas as pd

    return max(len(pd.bdate_range(start=start, end=end)) - 1, 0)



def get_next_earnings_days(ticker: str, as_of_date: Optional[Any] = None) -> int | None:
    """Deterministic next-earnings-date lookup via yfinance. None if unavailable -
    should be the rare exception for S&P 500 large-caps, not the common case, but
    callers MUST handle it gracefully (see format_earnings_fact_block)."""
    try:
        from datetime import date, datetime
        import pandas as pd
        import yfinance as yf

        ref_date = date.today()
        is_past = False
        if as_of_date is not None:
            if isinstance(as_of_date, datetime):
                ref_date = as_of_date.date()
            elif isinstance(as_of_date, date):
                ref_date = as_of_date
            elif isinstance(as_of_date, str) and len(as_of_date) >= 10:
                try:
                    ref_date = datetime.strptime(as_of_date[:10], "%Y-%m-%d").date()
                except Exception:
                    ref_date = date.today()
            if ref_date < date.today():
                is_past = True

        if not is_past:
            nd, _src = resolve_next_earnings_date(ticker)
            return _bdays_between(ref_date, nd) if nd else None

        import concurrent.futures

        def _fetch_yf_dates():
            t = yf.Ticker(ticker)
            if is_past:
                ed = t.get_earnings_dates(limit=12)
                if ed is not None and not ed.empty:
                    ref_str = ref_date.strftime("%Y-%m-%d")
                    future_dates = []
                    for dt_idx in ed.index:
                        d_str = str(dt_idx)[:10]
                        if d_str >= ref_str:
                            future_dates.append(d_str)
                    future_dates.sort()
                    if future_dates:
                        next_earn_str = future_dates[0]
                        b_range = pd.bdate_range(start=ref_str, end=next_earn_str)
                        b_days = len(b_range) - 1
                        return b_days, None
            cal = t.calendar
            dates = None
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
            elif hasattr(cal, "get"):
                dates = cal.get("Earnings Date")
            elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                dates = cal.loc["Earnings Date"]
            return None, dates

        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_fetch_yf_dates)
        try:
            past_days, dates = fut.result(timeout=2.0)
            if past_days is not None:
                ex.shutdown(wait=False)
                return past_days
        except Exception:
            dates = None
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

        if dates is not None:
            if hasattr(dates, "tolist"):
                dates = dates.tolist()
            nxt = dates[0] if isinstance(dates, (list, tuple)) and len(dates) > 0 else dates

            nxt_date = None
            if isinstance(nxt, datetime):
                nxt_date = nxt.date()
            elif hasattr(nxt, "date"):
                nxt_date = nxt.date()
            elif isinstance(nxt, date):
                nxt_date = nxt
            elif isinstance(nxt, str) and len(nxt) >= 10:
                try:
                    nxt_date = datetime.strptime(nxt[:10], "%Y-%m-%d").date()
                except Exception:
                    nxt_date = None

            if nxt_date:
                ref_str = ref_date.strftime("%Y-%m-%d")
                nxt_str = nxt_date.strftime("%Y-%m-%d")
                b_range = pd.bdate_range(start=ref_str, end=nxt_str)
                b_days = len(b_range) - 1
                return b_days if b_days >= 0 else None
    except Exception as e:
        logger.warning(f"[{ticker}] earnings date lookup failed: {e}")
        return None
    return None


def format_earnings_fact_block(ticker: str, dw: Optional[Dict[str, Any]] = None) -> str:
    """Formats deterministic earnings date, gate status, and historical reactions/PEAD table."""
    nd, src = resolve_next_earnings_date(ticker)
    days = _bdays_between(date.today(), nd) if nd else None
    lines = [f"--- DETERMINISTIC EARNINGS DATE & CATALYST HISTORY ({src}) ---"]

    if nd is not None:
        gate_status = "FAIL (<3d)" if days < 3 else "WARNING (<7d)" if days < 7 else "CLEAR (>=7d)"
        trust = (
            "Trust this over anything found via search for this ticker's earnings date."
            if not src.startswith("google")
            else "Date comes from Google grounding and passed a quarterly-cadence check; call it 'unconfirmed'."
        )
        lines.append(
            f"Next earnings: {nd:%Y-%m-%d} ({days} trading day(s) away, {(nd - date.today()).days} calendar day(s); source: {src}). "
            f"Earnings Gate: {gate_status}.\n{trust}"
        )
    else:
        lines.append(
            "Next earnings date: UNAVAILABLE (Tastytrade, yfinance, Finnhub and grounding all failed). "
            "State 'earnings date unverified'; do NOT infer a date from the gate's trading-bar count."
        )

    # 1. Pull historical quarterly reaction events from dw if enriched
    events = (dw.get("_earnings_reaction_events") if isinstance(dw, dict) else None)
    median_move = (dw.get("_historical_median_catalyst_move_pct") if isinstance(dw, dict) else None)
    summary = (dw.get("_catalyst_move_summary") if isinstance(dw, dict) else None)

    # 2. Fallback: query yfinance directly if events not present in dw
    if not events and ticker and ticker.upper() not in ("UNKNOWN", "NONE", ""):
        try:
            import pandas as pd
            import yfinance as yf

            t = yf.Ticker(ticker)
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and not ed.empty:
                reported = ed.dropna(subset=["Reported EPS"]).head(4)
                events = []
                for dt_idx, row in reported.iterrows():
                    ed_str = str(dt_idx)[:10]
                    surp = round(float(row["Surprise(%)"]), 2) if pd.notna(row.get("Surprise(%)")) else None
                    est = round(float(row["EPS Estimate"]), 2) if pd.notna(row.get("EPS Estimate")) else None
                    act = round(float(row["Reported EPS"]), 2) if pd.notna(row.get("Reported EPS")) else None
                    events.append({
                        "date": ed_str,
                        "eps_reported": act,
                        "eps_estimate": est,
                        "surprise_pct": surp,
                    })
        except Exception:
            pass

    # 3. Format historical reaction table
    if events:
        lines.append("\n📊 HISTORICAL EARNINGS REACTIONS & POST-EARNINGS DRIFT (Trailing Quarters):")
        has_drift = any("fwd_5d_drift_pct" in e for e in events)
        if has_drift:
            lines.append("| Date | EPS Est | Reported EPS | Surprise (%) | 1-Day Reaction | 5-Day PEAD |")
            lines.append("|---|---|---|---|---|---|")
            # If events were ordered chronologically in plugin, show latest first
            display_events = list(reversed(events)) if len(events) >= 2 and events[0].get("date", "") < events[-1].get("date", "") else events
            for ev in display_events:
                d_str = ev.get("date", "N/A")
                est_str = f"${ev['eps_estimate']:.2f}" if ev.get("eps_estimate") is not None else "—"
                act_str = f"${ev['eps_reported']:.2f}" if ev.get("eps_reported") is not None else "—"
                surp = ev.get("surprise_pct")
                surp_str = (
                    f"{'+' if surp > 0 else ''}{surp:.1f}% {'🟢' if surp > 0 else '🔴'}"
                    if surp is not None
                    else "—"
                )
                day_ret = ev.get("day_ret_pct")
                day_str = f"{'+' if day_ret > 0 else ''}{day_ret:.2f}%" if day_ret is not None else "—"
                drift = ev.get("fwd_5d_drift_pct")
                drift_str = f"{'+' if drift > 0 else ''}{drift:.2f}%" if drift is not None else "—"
                lines.append(f"| {d_str} | {est_str} | {act_str} | {surp_str} | {day_str} | {drift_str} |")
        else:
            lines.append("| Date | EPS Est | Reported EPS | Surprise (%) |")
            lines.append("|---|---|---|---|")
            for ev in events:
                d_str = ev.get("date", "N/A")
                est_str = f"${ev['eps_estimate']:.2f}" if ev.get("eps_estimate") is not None else "—"
                act_str = f"${ev['eps_reported']:.2f}" if ev.get("eps_reported") is not None else "—"
                surp = ev.get("surprise_pct")
                surp_str = (
                    f"{'+' if surp > 0 else ''}{surp:.1f}% {'🟢' if surp > 0 else '🔴'}"
                    if surp is not None
                    else "—"
                )
                lines.append(f"| {d_str} | {est_str} | {act_str} | {surp_str} |")

        if summary:
            lines.append(f"\n{summary}")
        elif median_move:
            lines.append(f"\nHistorical median 1-day earnings reaction: ±{median_move}%")

    return "\n".join(lines)


def check_earnings_safety(
    ticker: str,
    min_days: int = 14,
    as_of_date: Optional[Any] = None,
) -> tuple[bool, Optional[int], str]:
    """Check if ticker has sufficient earnings clearance.

    Returns (is_safe, days_to_earnings, reason).
    Fails closed if earnings date cannot be verified.
    """
    days = get_next_earnings_days(ticker, as_of_date=as_of_date)
    sym = (ticker or "").upper()
    if days is None:
        return False, None, f"Earnings date unknown for {sym} — failed closed"
    if days < min_days:
        return False, days, f"Earnings in {days} day(s) (< {min_days} day minimum safety window for {sym})"
    return True, days, f"Earnings clear in {days} day(s) (>= {min_days} days for {sym})"

