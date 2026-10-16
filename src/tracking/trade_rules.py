"""The single rulebook. The desk, the paper agent and the deep-research gate all read these; the LLM trades by the same rules as the user.

Earnings is not a blackout. Entries are taken through earnings, so a plan must state a stance and the evidence for it.
Stops follow Pine when Pine has a valid plan, otherwise the plan's own structural stop no tighter than STOP_ATR_MIN.
Size is 100 shares, or an option at the chain mid; dollar risk is information, not a cap.
"""

from __future__ import annotations

from typing import Optional

from src.tracking.rr_config import GREEN_RR, STOP_ATR_MIN, min_rr  # noqa: F401  (single copy, re-exported)

SHARES_PER_TRADE = 100
T1_MAX_EXPECTED_MOVE_MULT = 1.5
EARNINGS_WINDOW_BARS = 21
EARNINGS_STANCES = ("HOLD_THROUGH", "EXIT_BEFORE", "EARNINGS_PLAY")
TICKET_GOOD_TIL_SESSIONS = 10


def earnings_blocks_entry(days_to_earnings: Optional[int]) -> bool:
    return False


def earnings_in_window(days_to_earnings: Optional[int]) -> bool:
    return days_to_earnings is not None and 0 <= days_to_earnings <= EARNINGS_WINDOW_BARS


def normalize_stance(raw: object) -> Optional[str]:
    s = str(raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    return s if s in EARNINGS_STANCES else None


def max_allowed_stop(fill: float, atr: float, pine_stop: float = 0.0) -> float:
    """Highest stop (tightest) the rulebook allows: Pine's stop when present, else fill - STOP_ATR_MIN * ATR."""
    return pine_stop if pine_stop > 0 else fill - STOP_ATR_MIN * atr


def risk_usd(entry: float, stop: float, qty: int = SHARES_PER_TRADE) -> float:
    return round(max(entry - stop, 0.0) * qty, 2)


def entry_max_for_rr(target: float, stop: float, rr: Optional[float] = None) -> float:
    """Highest entry price at which (target - entry) / (entry - stop) still meets the green R:R (2.0)."""
    r = float(rr if rr is not None else GREEN_RR)
    return (target + r * stop) / (1.0 + r)
