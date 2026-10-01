"""
src/logic/strike_validator.py

Deterministic validation for option trade structures before they enter prompts or reports.
Eliminates model rationalizations on naked legs, ITM credit spreads, or distorted R:R ratios.
"""

from typing import List, Optional, Tuple


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

    try:
        exp_move_num = float(exp_move_pct_21b) if exp_move_pct_21b is not None else None
    except (ValueError, TypeError):
        exp_move_num = None

    if exp_move_num is None or exp_move_num <= 0:
        return False, ["Missing or invalid ExpMove (Exp Move Pct 21b required for strike geometry validation)"]

    pct = exp_move_num / 100.0
    exp_move_dist = spot_price * pct
    max_allowed_dist = 1.5 * exp_move_dist

    # 1. Credit spread and naked short leg OTM checks
    if "BULL_PUT" in strat or "PUT_CREDIT" in strat:
        if short_strike is None or long_strike is None:
            defects.append("Malformed bull put credit spread: missing required strikes")
        elif short_strike >= spot_price:
            defects.append(
                f"Bull put credit spread short strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Credit spreads must be strictly OTM."
            )
        elif exp_move_dist and (spot_price - short_strike) < (1.25 * exp_move_dist - 0.05):
            defects.append(
                f"Bull put credit spread short strike (${short_strike:.2f}) is within 1.25x ExpMove "
                f"({(spot_price - short_strike)/exp_move_dist:.2f}x EM vs required >= 1.25x EM)."
            )
    elif "BEAR_CALL" in strat or "CALL_CREDIT" in strat:
        if short_strike is None or long_strike is None:
            defects.append("Malformed bear call credit spread: missing required strikes")
        elif short_strike <= spot_price:
            defects.append(
                f"Bear call credit spread short strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Credit spreads must be strictly OTM."
            )
        elif exp_move_dist and (short_strike - spot_price) < (1.25 * exp_move_dist - 0.05):
            defects.append(
                f"Bear call credit spread short strike (${short_strike:.2f}) is within 1.25x ExpMove "
                f"({(short_strike - spot_price)/exp_move_dist:.2f}x EM vs required >= 1.25x EM)."
            )
    elif "SHORT_PUT" in strat or "PUT_SALE" in strat or "CASH_SECURED" in strat or "CSP" in strat or "COVERED_PUT" in strat:
        if short_strike is None:
            defects.append("Missing short strike for income put sale")
        elif short_strike >= spot_price:
            defects.append(
                f"Cash-secured/short put strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Income put sales must be strictly OTM."
            )
        elif exp_move_dist and (spot_price - short_strike) < (1.25 * exp_move_dist - 0.05):
            defects.append(
                f"Cash-secured/short put strike (${short_strike:.2f}) is within 1.25x ExpMove "
                f"({(spot_price - short_strike)/exp_move_dist:.2f}x EM vs required >= 1.25x EM)."
            )
    elif "SHORT_CALL" in strat or "CALL_SALE" in strat or "COVERED_CALL" in strat:
        if short_strike is None:
            defects.append("Missing short strike for income call sale")
        elif short_strike <= spot_price:
            defects.append(
                f"Covered/short call strike (${short_strike:.2f}) is ITM/ATM vs spot (${spot_price:.2f}). "
                f"Income call sales must be strictly OTM."
            )
        elif exp_move_dist and (short_strike - spot_price) < (1.25 * exp_move_dist - 0.05):
            defects.append(
                f"Covered/short call strike (${short_strike:.2f}) is within 1.25x ExpMove "
                f"({(short_strike - spot_price)/exp_move_dist:.2f}x EM vs required >= 1.25x EM)."
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
            elif exp_move_dist and (spot_price - extra_short_strike) < (1.25 * exp_move_dist - 0.05):
                defects.append(
                    f"Jade Lizard short put (${extra_short_strike:.2f}) is within 1.25x ExpMove "
                    f"({(spot_price - extra_short_strike)/exp_move_dist:.2f}x EM vs required >= 1.25x EM)."
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


# --- creation-time gates for defined-risk verticals ------------------------------------------
#
# validate_strike_geometry above is prompt hygiene: it is advisory and only runs when the caller
# has an ExpMove. The gates below run on the INSERT path, where a bad row is unrecoverable -- it
# becomes a trade in the audit ledger and is scored for R against it.

# A vertical leg further than this from spot means the strikes were priced off a different quote
# than the entry. Not a market opinion: a strike list cannot be stale and still describe the trade.
MAX_STRIKE_SPOT_DRIFT_PCT = 15.0


def spread_intrinsic_value(
    spot_price: float,
    long_strike: float,
    short_strike: float,
    is_call: bool,
) -> float:
    """Lower bound on a 2-leg DEBIT vertical's value at spot: its intrinsic, per share.

    The spread is long one leg and short the other, so its intrinsic is the DIFFERENCE of the two
    legs' intrinsics -- not the smaller of them. A bull call spread whose spot is above both strikes
    is intrinsically worth the full width, which is the whole point: that state is a maximum gain,
    and reading it as the smaller leg's intrinsic understates the position by the width.

    Ordering is derived from direction, not from comparing the strike numbers. For a bull call the
    long strike is the lower one, but for a bull put it is the higher one, so a numeric comparison
    silently returns the wrong sign (and, clamped at zero, a flat 0.0) for half of all verticals.
    """
    if is_call:
        long_intr = max(0.0, spot_price - long_strike)
        short_intr = max(0.0, spot_price - short_strike)
    else:
        long_intr = max(0.0, long_strike - spot_price)
        short_intr = max(0.0, short_strike - spot_price)

    if (is_call and long_strike < short_strike) or (not is_call and long_strike > short_strike):
        return max(0.0, long_intr - short_intr)
    # Inverted pair: a credit spread, not the debit structure this prices. The long leg's own
    # intrinsic is the only meaningful number; a naive difference returns 0 and makes every check
    # downstream inert.
    return max(0.0, long_intr)


# Structures whose pay-off is not a simple 2-leg debit vertical. Pricing these with the
# single-spread formulas below produces nonsense, so they are refused rather than guessed at.
KNOWN_VERTICAL_TERMS = ("SPREAD", "VERTICAL", "BULL", "BEAR", "CALL", "PUT")
UNSUPPORTED_STRUCTURE_TERMS = ("CONDOR", "BUTTERFLY", "CALENDAR", "DIAGONAL", "STRADDLE", "STRANGLE")


def validate_spread_geometry(
    structure: str,
    spot_price: float,
    long_strike: Optional[float],
    short_strike: Optional[float],
    debit: Optional[float],
    contract_multiplier: int = 100,
) -> Tuple[bool, List[str]]:
    """Reject verticals that cannot exist at the quoted spot.

    Three independent defects, any one of which makes the row unusable:
      1. a leg more than 15% from spot -> the strikes came from a stale price;
      2. intrinsic > debit -> you cannot buy for less than the spread is already worth;
      3. debit >= width -> max_profit is zero or negative, so R is undefined.

    Defect 2 is the expensive one: it produces a trade that is simultaneously deeply in profit and
    scored at max loss, because the ledger marks P&L off the underlying while the position is the
    spread (see suggested_trades_auditor.evaluate_suggested_trades).
    """
    defects: List[str] = []

    strat = (structure or "").upper().replace(" ", "_")
    try:
        spot = float(spot_price or 0.0)
        long_k = float(long_strike or 0.0)
        short_k = float(short_strike or 0.0)
        dbt = float(debit or 0.0)
    except (TypeError, ValueError):
        return False, ["Non-numeric strike/debit/spot on a vertical structure"]

    # NaN and infinity must be rejected explicitly. Every comparison against NaN is False, so the
    # drift check, the intrinsic check and the width check all silently pass and a corrupt quote
    # is graded as a well-formed structure. -inf is caught by the `spot <= 0` test below; +inf and
    # NaN are not, so they are named here.
    non_finite = [n for n, v_ in (("spot", spot), ("long_strike", long_k),
                                   ("short_strike", short_k), ("debit", dbt))
                  if v_ != v_ or v_ in (float("inf"), float("-inf"))]
    if non_finite:
        return False, [f"Non-finite value(s) on a vertical structure: {', '.join(non_finite)}"]

    if spot <= 0:
        return False, ["Missing or invalid spot price; cannot price the structure"]
    if long_k <= 0 or short_k <= 0:
        return False, ["Missing strike(s) for vertical structure"]

    is_call = "CALL" in strat

    # 0. Structure type and strike ordering.
    #    A 2-leg debit vertical is a DEBIT spread, so the short leg must be FURTHER out of the
    #    money than the long leg: calls long-below-short, puts long-above-short. Getting this
    #    backwards makes the position a credit spread, and every formula below then reads the
    #    wrong sign.
    unsupported = [t for t in UNSUPPORTED_STRUCTURE_TERMS if t in strat]
    if unsupported:
        return False, [
            f"{strat} is not a 2-leg debit vertical ({', '.join(unsupported)}); this gate does not "
            f"price multi-leg structures"
        ]
    if not any(t in strat for t in KNOWN_VERTICAL_TERMS):
        return False, [
            f"Unrecognised structure {strat!r}; cannot determine call/put, so it cannot be priced"
        ]
    ordering_ok = (long_k < short_k) if is_call else (long_k > short_k)
    if not ordering_ok:
        rel = "long must be BELOW short" if is_call else "long must be ABOVE short"
        return False, [
            f"{strat} strike ordering is inverted: long ${long_k:.2f} vs short ${short_k:.2f}. "
            f"For a debit {('call' if is_call else 'put')} vertical, {rel}. As given it is a "
            f"credit spread, not the debit structure this gate prices."
        ]

    # 1. Strike drift vs spot
    band = spot * (MAX_STRIKE_SPOT_DRIFT_PCT / 100.0)
    for label, k in (("long", long_k), ("short", short_k)):
        if abs(k - spot) > band:
            drift = (k - spot) / spot * 100.0
            defects.append(
                f"{label} strike ${k:.2f} is {drift:+.1f}% from spot ${spot:.2f} "
                f"(>{MAX_STRIKE_SPOT_DRIFT_PCT:.0f}%). Strikes and entry are from different quotes."
            )

    width = abs(long_k - short_k)   # the ordering check above guarantees this is > 0

    # 2. intrinsic <= debit
    intrinsic = spread_intrinsic_value(spot, long_k, short_k, is_call)
    if dbt > 0 and intrinsic > dbt:
        defects.append(
            f"Debit ${dbt:.2f} is below the ${intrinsic:.2f} intrinsic of {long_k:.0f}/{short_k:.0f} "
            f"at spot ${spot:.2f}. The spread is already worth more than it was 'bought' for."
        )

    # 3. debit < width
    if dbt <= 0:
        defects.append("Missing or non-positive debit; R cannot be expressed")
    elif dbt >= width:
        defects.append(
            f"Debit ${dbt:.2f} >= spread width ${width:.2f}: max_profit is zero or negative."
        )

    return len(defects) == 0, defects


def _at_max_profit(spot_price: float, long_strike: float, short_strike: float, is_call: bool) -> bool:
    """True when both legs are ITM, which pins a debit vertical at its full width.

    A debit vertical's short leg is always the one further out of the money -- the lower strike
    for a call, the HIGHER strike for a put. Max profit is spot having passed the SHORT leg in the
    profitable direction (up for calls, down for puts). Deriving this from the strike NUMBERS
    instead gets it backwards for exactly one of the two structures, which is how a put vertical
    ends up booked at max profit while sitting at max loss.
    """
    return spot_price >= short_strike if is_call else spot_price <= short_strike


def vertical_mark_value(
    spot_price: float,
    long_strike: float,
    short_strike: float,
    debit: float,
    is_call: bool,
    contract_multiplier: int = 100,
) -> float:
    """Mark a 2-leg DEBIT vertical off its own value at spot, per contract.

    Without an options chain the only defensible mark is intrinsic: both legs ITM -> the full
    width, otherwise the intrinsic difference, floored at zero. Time value is real money but it is
    unknowable here, so it is not invented.

    The consequence is deliberately one-directional: an out-of-the-money vertical that gets stopped
    out marks at 0 and books the whole debit as a loss, even if some time value remained. That is
    the conservative direction. The opposite mistake -- flooring the mark at the debit paid -- books
    every stopped-out spread as exactly break-even, and an underlying stop almost always lands
    between the strikes.
    """
    if spot_price != spot_price or spot_price in (float("inf"), float("-inf")):
        return 0.0
    if long_strike <= 0 or short_strike <= 0 or debit <= 0 or long_strike == short_strike:
        return 0.0

    width = abs(long_strike - short_strike)
    if _at_max_profit(spot_price, long_strike, short_strike, is_call):
        per_share = width
    else:
        per_share = spread_intrinsic_value(spot_price, long_strike, short_strike, is_call)

    return round(min(width, max(0.0, per_share)) * contract_multiplier, 2)
