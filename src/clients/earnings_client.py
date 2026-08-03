import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_next_earnings_days(ticker: str) -> int | None:
    """Deterministic next-earnings-date lookup via yfinance. None if unavailable -
    should be the rare exception for S&P 500 large-caps, not the common case, but
    callers MUST handle it gracefully (see format_earnings_fact_block)."""
    try:
        from datetime import date, datetime

        import yfinance as yf

        cal = yf.Ticker(ticker).calendar
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
                d = (nxt_date - date.today()).days
                return d if d >= 0 else None
    except Exception as e:
        logger.warning(f"[{ticker}] earnings date lookup failed: {e}")
        return None
    return None


def format_earnings_fact_block(ticker: str) -> str:
    days = get_next_earnings_days(ticker)
    if days is not None:
        est_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return (
            "--- DETERMINISTIC EARNINGS DATE (yfinance, ground truth) ---\n"
            f"Next earnings in {days} day(s) (~{est_date}).\n"
            "Trust this over anything found via search/grounding for this ticker's earnings date.\n"
        )
    return (
        "--- DETERMINISTIC EARNINGS DATE: UNAVAILABLE ---\n"
        "No reliable yfinance earnings-date data for this ticker. You MUST verify the earnings\n"
        "date yourself via search/grounding. If you cannot confirm it cleanly, explicitly state\n"
        "'earnings date unverified' rather than asserting a specific date with confidence.\n"
    )
