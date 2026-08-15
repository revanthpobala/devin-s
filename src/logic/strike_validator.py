"""
src/logic/strike_validator.py

Deterministic validation for option trade structures before they enter prompts or reports.
Eliminates model rationalizations on naked legs, ITM credit spreads, or distorted R:R ratios.
"""

from typing import Any, Dict, List, Optional, Tuple


def validate_strike_geometry(
    strategy_type: str,
    spot_price: float,
    exp_move_pct_21b: Optional[float],
    long_strike: Optional[float] = None,
    short_strike: Optional[float] = None,
    extra_short_strike: Optional[float] = None,
    max_profit: Optional[float] = None,
    max_loss: Optional[float] = None,
    ask: Optional[float] = None,
    bid: Optional[float] = None,
) -> Tuple[bool, List[str]]:
    """
    Validates proposed option structure geometry deterministically.

    Returns:
        (is_valid, list_of_defects)
    """
    defects = []
    strat = (strategy_type or "").upper().replace(" ", "_")

    if not spot_price or spot_price <= 0:
        return False, ["Missing or invalid spot price"]

    exp_move_dist = (spot_price * (exp_move_pct_21b / 100.0)) if exp_move_pct_21b else None
    max_allowed_dist = (1.5 * exp_move_dist) if exp_move_dist else None

    # 1. Credit spread and naked short leg OTM checks
    if "BULL_PUT" in strat or "PUT_CREDIT" in strat:
        if short_strike is None or long_strike is None:
            defects.append("Malformed bull put credit spread: missing required strikes")
        elif short_strike >= spot_price:
            defects.append(
                f"Bull put credit spread short strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Credit spreads must be strictly OTM."
            )
    elif "BEAR_CALL" in strat or "CALL_CREDIT" in strat:
        if short_strike is None or long_strike is None:
            defects.append("Malformed bear call credit spread: missing required strikes")
        elif short_strike <= spot_price:
            defects.append(
                f"Bear call credit spread short strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Credit spreads must be strictly OTM."
            )
    elif "SHORT_PUT" in strat or "PUT_SALE" in strat or "CASH_SECURED" in strat or "CSP" in strat or "COVERED_PUT" in strat:
        if short_strike is None:
            defects.append("Missing short strike for income put sale")
        elif short_strike >= spot_price:
            defects.append(
                f"Cash-secured/short put strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Income put sales must be strictly OTM."
            )
    elif "SHORT_CALL" in strat or "CALL_SALE" in strat or "COVERED_CALL" in strat:
        if short_strike is None:
            defects.append("Missing short strike for income call sale")
        elif short_strike <= spot_price:
            defects.append(
                f"Covered/short call strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Income call sales must be strictly OTM."
            )
    elif "JADE_LIZARD" in strat or "JADE" in strat:
        # Jade Lizard = Short OTM Put + Bear Call Credit Spread (Short Call + Long Call)
        # short_strike = Short Call, long_strike = Long Call, extra_short_strike = Short Put
        if extra_short_strike is None or short_strike is None or long_strike is None:
            defects.append("Malformed Jade Lizard: requires 3 valid legs (short put, short call, long call)")
        else:
            if extra_short_strike >= spot_price:
                defects.append(
                    f"Jade Lizard short put (${extra_short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                    f"Short put must be strictly OTM."
                )
            if short_strike <= spot_price:
                defects.append(
                    f"Jade Lizard short call (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                    f"Call spread must be strictly OTM."
                )
            if long_strike <= short_strike:
                defects.append(
                    f"Jade Lizard long call (${long_strike:.2f}) must be higher than short call (${short_strike:.2f})."
                )

    # 2. Debit spread / long call / long put 1.5x ExpMove checks
    if "BULL_CALL" in strat or "CALL_DEBIT" in strat or "LONG_CALL" in strat:
        if long_strike is None:
            defects.append("Missing long strike for call structure")
        elif max_allowed_dist is not None:
            if (long_strike - spot_price) > max_allowed_dist:
                defects.append(
                    f"Long call strike (${long_strike:.2f}) sits > 1.5x ExpMove (${spot_price + max_allowed_dist:.2f}). "
                    f"Excessive distance produces near-zero delta and invalid R:R."
                )
            if short_strike is not None and (short_strike - spot_price) > max_allowed_dist:
                defects.append(
                    f"Bull call short strike (${short_strike:.2f}) sits > 1.5x ExpMove (${spot_price + max_allowed_dist:.2f}). "
                    f"Short leg contributes ~0 premium; structure is a naked long disguised as a spread."
                )
    elif "BEAR_PUT" in strat or "PUT_DEBIT" in strat or "LONG_PUT" in strat:
        if long_strike is None:
            defects.append("Missing long strike for put structure")
        elif max_allowed_dist is not None:
            if (spot_price - long_strike) > max_allowed_dist:
                defects.append(
                    f"Long put strike (${long_strike:.2f}) sits > 1.5x ExpMove (${spot_price - max_allowed_dist:.2f}). "
                    f"Excessive distance produces near-zero delta and invalid R:R."
                )
            if short_strike is not None and (spot_price - short_strike) > max_allowed_dist:
                defects.append(
                    f"Bear put short strike (${short_strike:.2f}) sits > 1.5x ExpMove (${spot_price - max_allowed_dist:.2f}). "
                    f"Short leg contributes ~0 premium; structure is a naked long disguised as a spread."
                )

    # 3. Spread Width & Accounting Reconciliation (2-leg vertical spreads only)
    if "JADE" not in strat and long_strike is not None and short_strike is not None:
        spread_width = abs(long_strike - short_strike)
        expected_total_per_share = spread_width
        if max_profit is not None and max_loss is not None and max_profit > 0 and max_loss != 0:
            # max_profit and max_loss are typically per contract (100 shares) or per share
            abs_loss = abs(max_loss)
            tot = (max_profit + abs_loss) / 100.0 if (max_profit + abs_loss) > spread_width * 5 else (max_profit + abs_loss)
            # Allow 25% tolerance for rounding / quote slippage
            if abs(tot - expected_total_per_share) > (expected_total_per_share * 0.25):
                defects.append(
                    f"Spread math reconciliation mismatch: max_profit ({max_profit}) + abs_loss ({abs_loss}) "
                    f"does not reconcile with spread width ${spread_width:.2f}."
                )

    is_valid = len(defects) == 0
    return is_valid, defects
