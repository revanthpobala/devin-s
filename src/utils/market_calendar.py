"""NYSE trading-day calendar — the single source for holiday-aware day counts.

NYSE_HOLIDAYS lists full-day closures. Extend before each new year.
"""
from __future__ import annotations

from datetime import date, timedelta

NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def trading_days_between(start: date, end: date) -> int:
    """Trading days from *start* to *end*, exclusive of start (``bdate_range`` len - 1 semantics, holiday-aware)."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += 1 if is_trading_day(d) else 0
    return n
