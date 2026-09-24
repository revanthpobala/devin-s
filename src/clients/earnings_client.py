from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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
                    return b_days

        cal = t.calendar
        dates = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
        elif hasattr(cal, "get"):
            dates = cal.get("Earnings Date")
        elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
            dates = cal.loc["Earnings Date"]

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
    days = get_next_earnings_days(ticker)
    lines = ["--- DETERMINISTIC EARNINGS DATE & CATALYST HISTORY (yfinance) ---"]

    if days is not None:
        est_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        gate_status = "FAIL (<3d)" if days < 3 else "WARNING (<7d)" if days < 7 else "CLEAR (>=7d)"
        lines.append(
            f"Next earnings in {days} day(s) (~{est_date}). "
            f"Earnings Gate: {gate_status}.\n"
            f"Trust this over anything found via search/grounding for this ticker's earnings date."
        )
    else:
        lines.append(
            "Next earnings date: UNAVAILABLE via yfinance calendar (verify manually). "
            "If unverified, state 'earnings date unverified'."
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
