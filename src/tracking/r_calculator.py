"""
src/tracking/r_calculator.py
============================
Unified R calculation with unit tagging (share vs option).
Enforces:
- R derived from initial_stop only (shares) or premium debit (options)
- Skips R (returns None) when risk < 0.25 ATR
- Clamps |R| > 20.0 to None (NULL)
- No 0.0 default — missing/invalid risk returns None
"""

from typing import Optional


def compute_r(
    unit: str = "share",
    entry: Optional[float] = None,
    exit_px: Optional[float] = None,
    stop: Optional[float] = None,
    side: str = "LONG",
    atr: Optional[float] = None,
    debit: Optional[float] = None,
    dollar_pnl: Optional[float] = None,
) -> Optional[float]:
    """Compute realized R multiple.

    Parameters:
    - unit: "share" (equity/underlying) or "option"
    - entry: Fill price of entry
    - exit_px: Fill price of exit
    - stop: Initial tactical stop (NEVER a trailed/ratcheted runner stop)
    - side: "LONG" or "SHORT"
    - atr: ATR(14) at signal/entry time
    - debit: Option premium paid per share (e.g. 3.25 for $325 risk)
    - dollar_pnl: Realized dollar P&L for options spread
    """
    unit_norm = (unit or "share").strip().lower()
    side_norm = (side or "LONG").strip().upper()

    if unit_norm in ("option", "options"):
        # Risk per contract is debit * 100
        if debit and float(debit) > 0:
            risk = float(debit) * 100.0
        elif entry and stop:
            risk = abs(float(entry) - float(stop)) * 100.0
        else:
            return None

        if risk <= 0:
            return None

        if dollar_pnl is not None:
            raw_r = float(dollar_pnl) / risk
        elif entry is not None and exit_px is not None:
            pts = (float(exit_px) - float(entry)) if "LONG" in side_norm else (float(entry) - float(exit_px))
            raw_r = (pts * 100.0) / risk
        else:
            return None

        if abs(raw_r) > 20.0:
            return None
        return round(raw_r, 4)

    # Underlying / share mode
    try:
        e = float(entry) if entry is not None else None
        x = float(exit_px) if exit_px is not None else None
        s = float(stop) if stop is not None else None
    except (ValueError, TypeError):
        return None

    if e is None or x is None or s is None or e <= 0 or s <= 0:
        return None

    risk_dist = abs(e - s)
    if risk_dist <= 0:
        return None

    # Skip when risk < 0.25 ATR
    if atr and float(atr) > 0:
        if risk_dist < (0.25 * float(atr)):
            return None

    pts = (x - e) if "LONG" in side_norm else (e - x)
    raw_r = pts / risk_dist

    # Clamp > 20R to None (NULL)
    if abs(raw_r) > 20.0:
        return None

    return round(raw_r, 4)
