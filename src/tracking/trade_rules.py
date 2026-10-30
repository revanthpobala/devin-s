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
# Measured (near_zone_lane.py --bounce, full_v4_av2, in zone & R:R at market >= 2): reaching entry + 1.0 ATR before the
# stop within 10 bars beats break-even in all four eras and both ticker halves (43.5% vs 41.2%). Shorter windows do not.
BOUNCE_ATR = 1.0
BOUNCE_BARS = 10


def earnings_gate(days_to_earnings: Optional[int]) -> str:
    """Gate string for triage payloads. Earnings is context, never FAIL or CAUTION."""
    return "UNKNOWN" if days_to_earnings is None else "PASS"


def earnings_in_window(days_to_earnings: Optional[int]) -> bool:
    return days_to_earnings is not None and 0 <= days_to_earnings <= EARNINGS_WINDOW_BARS


def normalize_stance(raw: object) -> Optional[str]:
    s = str(raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    return s if s in EARNINGS_STANCES else None


DEFAULT_RISK_BUDGET_USD = 500.0


def compute_position_shares(
    entry: float,
    stop: float,
    risk_budget_usd: float = DEFAULT_RISK_BUDGET_USD,
    min_shares: int = 1,
    max_shares: int = 500,
) -> int:
    """Position sizing by dollar risk: Risk per trade is set by position size, not by squeezing the stop.
    If the natural stop is wide, cut share count until dollar risk is the same.
    """
    try:
        e = float(entry or 0.0)
        s = float(stop or 0.0)
        rb = float(risk_budget_usd or DEFAULT_RISK_BUDGET_USD)
    except (TypeError, ValueError):
        return SHARES_PER_TRADE
    if e <= s or s <= 0 or e <= 0:
        return SHARES_PER_TRADE
    risk_per_share = max(e - s, 0.01)
    shares = int(rb / risk_per_share)
    return max(min_shares, min(shares, max_shares))


def max_allowed_stop(fill: float, atr: float, pine_stop: float = 0.0) -> float:
    """Highest stop (tightest) the rulebook allows: never tighter than fill - STOP_ATR_MIN * ATR.
    If Pine stop is provided and is at or below this floor, respect it. If Pine stop is tighter than 0.7 ATR,
    clamp it down to the 0.7 ATR floor so the trade has statistical room to breathe.
    """
    try:
        fill_f = float(fill or 0.0)
        atr_f = float(atr or 0.0)
        pine_f = float(pine_stop or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if atr_f <= 0:
        return round(pine_f, 2) if pine_f > 0 else round(fill_f * 0.95, 2)
    atr_cap = round(fill_f - STOP_ATR_MIN * atr_f, 2)
    if pine_f > 0:
        return round(min(pine_f, atr_cap), 2)
    return atr_cap


def risk_usd(entry: float, stop: float, qty: int = SHARES_PER_TRADE) -> float:
    return round(max(entry - stop, 0.0) * qty, 2)


def entry_max_for_rr(target: float, stop: float, rr: Optional[float] = None) -> float:
    """Highest entry price at which (target - entry) / (entry - stop) still meets the green R:R (2.0)."""
    r = float(rr if rr is not None else GREEN_RR)
    return (target + r * stop) / (1.0 + r)


# --- account-level caps (sizing guardrails, measurement-independent) --------------------------
# The research edge is small (~+0.03R/trade relative to the market). Position size, concentration
# and earnings-gap exposure are risk limits, not alpha. Every layered plan and ticket is capped by
# these so a single name or a single gap cannot blow a hole in the account. Env-overridable.
import os as _os

ACCOUNT_EQUITY_USD = float(_os.getenv("ACCOUNT_EQUITY_USD", "170000"))
MAX_POSITION_NOTIONAL_PCT = float(_os.getenv("MAX_POSITION_NOTIONAL_PCT", "0.05"))  # 5% of equity per name
MAX_EARNINGS_GAP_LOSS_USD = float(_os.getenv("MAX_EARNINGS_GAP_LOSS_USD", "1000"))  # 2x implied move gap
MIN_STOP_ATR_FOR_SIZING = STOP_ATR_MIN  # never size off a stop tighter than the 0.7 ATR floor


def max_shares_by_notional(price: float, equity_usd: float = 0.0) -> int:
    """Largest whole-share count whose notional stays within MAX_POSITION_NOTIONAL_PCT of equity."""
    try:
        p = float(price or 0.0)
        eq = float(equity_usd or 0.0) or ACCOUNT_EQUITY_USD
    except (TypeError, ValueError):
        return 10_000_000
    if p <= 0:
        return 10_000_000
    return max(0, int((eq * MAX_POSITION_NOTIONAL_PCT) / p))


def max_shares_by_earnings_gap(
    price: float, implied_move_pct: Optional[float], days_to_earnings: Optional[int],
    window_bars: int = EARNINGS_WINDOW_BARS, max_gap_loss_usd: float = 0.0,
) -> Optional[int]:
    """Share cap so a down-gap of 2x the implied move loses at most max_gap_loss_usd.

    Returns None when earnings are outside the holding window or the implied move is unknown (no cap).
    A hard stop does NOT protect a gap, so this bounds exposure that the stop can't.
    """
    if not earnings_in_window(days_to_earnings):
        return None
    try:
        p = float(price or 0.0)
        im = float(implied_move_pct or 0.0)
    except (TypeError, ValueError):
        return None
    if p <= 0 or im <= 0:
        return None
    cap_usd = float(max_gap_loss_usd or MAX_EARNINGS_GAP_LOSS_USD)
    gap_loss_per_share = p * (2.0 * im / 100.0)
    if gap_loss_per_share <= 0:
        return None
    return max(0, int(cap_usd / gap_loss_per_share))
