"""
TradingView setup-name -> measured-lane mapping for the Inbox.

The Inbox was 81 rows of unmeasured names, dominated by two alert families:
"A+ Trend Long" (41) and "Early Action Long" (27). Neither had ever been measured, and both were
rendered in the same type as a PASS that could actually be traded -- which is how a list where
nothing is actionable still looks like a full pipeline.

The mapping is deliberately conservative:
  * a name maps to a lane ONLY when it corresponds to an action code with a measurement behind it;
  * Early Action Long maps to ACTION (code 2), which measures flat/unstable, so it is tagged
    "no edge" rather than dressed up as a lane;
  * anything unrecognised is "unmeasured" -- never a silent blank.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from src.logic.data_window_filter import LANE_PRIORS, _ACTION_CODES

# (substring to match, action code, lane, tag, prior)
#
# `prior` is (win %, R). None means there is no measurement, and the row is tagged accordingly
# rather than inheriting an unrelated lane's numbers.
_NAME_MAP: Tuple[Tuple[str, Optional[int], Optional[str], str, Optional[Tuple[float, float]]], ...] = (
    ("rr_setup_strong", None, "RR_SETUP_STRONG", "measured", LANE_PRIORS.get("rr_at_market_lane_strong")),
    ("rr_setup", None, "RR_SETUP", "measured", LANE_PRIORS.get("rr_at_market_lane")),
    ("rr_no_pb", None, "RR_NO_PB", "watch only", None),
    ("rr_tight_stop", None, "RR_TIGHT_STOP", "watch only", None),
    ("code20", 20, "CODE20", "measured", LANE_PRIORS.get("reversal_buy_lane")),
    ("momentum_breakout", None, "MOMENTUM_BREAKOUT", "unmeasured", None),
    ("coil", None, "COIL", "unmeasured", None),
    ("a+ trend", None, None, "unmeasured", None),
    ("early action", 2, None, "no edge", None),
    ("prime", 1, None, "unmeasured", None),
    ("power move", 3, None, "unmeasured", None),
    ("power (ext)", 4, None, "unmeasured", None),
    ("reversal buy", 20, "CODE20", "measured", LANE_PRIORS.get("reversal_buy_lane")),
    ("rsi2", None, "RSI2", "measured", LANE_PRIORS.get("rsi2_setup_lane")),
    ("oversold", None, "OVERSOLD", "measured", LANE_PRIORS.get("oversold_lane")),
    ("rr at market", None, "RR_SETUP", "measured", LANE_PRIORS.get("rr_at_market_lane")),
    ("watch shadow", 8, None, "watch only", None),
    ("early", 7, None, "unmeasured", None),
    ("forming", 9, None, "unmeasured", None),
    ("wait", 10, None, "unmeasured", None),
    ("blow-off", 16, None, "excluded", None),
    ("blowoff", 16, None, "excluded", None),
    ("extended", 11, None, "excluded", None),
    ("stretched", 12, None, "excluded", None),
    ("chase", 21, None, "excluded", None),
    ("parabolic", 17, None, "hard cut", None),
    ("toxic", 18, None, "hard cut", None),
    ("screen block", 19, None, "excluded", None),
    ("counter-trend", 14, None, "excluded", None),
)


# Codes 1-4 are CONFIRMED actionable entries, but only ACTION (2) has been measured and it
# measured flat. The badge says so instead of implying an edge.
NO_EDGE_ACTION_CODES = {2}
EXCLUDED_ACTION_CODES = {11, 12, 13, 16, 19, 21, 14}
HARD_CUT_ACTION_CODES = {17, 18}

UNMEASURED = "unmeasured"


def classify_setup_name(setup: str) -> Dict[str, object]:
    """Resolve a TradingView setup name to a lane, a tag, and (where measured) a prior."""
    raw = (setup or "").strip()
    low = raw.lower()

    if not low:
        return {
            "setup": raw, "lane": None, "action_code": None, "tag": UNMEASURED,
            "prior_win": None, "prior_ev": None, "label": "unmeasured",
        }

    for needle, code, lane, tag, prior in _NAME_MAP:
        if needle in low:
            return _result(raw, code, lane, tag, prior)

    return {
        "setup": raw, "lane": None, "action_code": None, "tag": UNMEASURED,
        "prior_win": None, "prior_ev": None, "label": f"{UNMEASURED}: {raw}",
    }


def classify_action_code(code) -> Dict[str, object]:
    """Resolve a numeric action code to the same shape."""
    if code is None:
        return {
            "setup": "", "lane": None, "action_code": None, "tag": UNMEASURED,
            "prior_win": None, "prior_ev": None, "label": UNMEASURED,
        }
    try:
        code = int(code)
    except (TypeError, ValueError):
        return {
            "setup": "", "lane": None, "action_code": None, "tag": UNMEASURED,
            "prior_win": None, "prior_ev": None, "label": UNMEASURED,
        }

    name = _ACTION_CODES.get(code, f"code {code}")
    if code in HARD_CUT_ACTION_CODES:
        tag = "hard cut"
    elif code in EXCLUDED_ACTION_CODES:
        tag = "excluded"
    elif code in NO_EDGE_ACTION_CODES:
        tag = "no edge"
    else:
        tag = UNMEASURED
    return _result(name, code, None, tag, None)


def _result(setup, code, lane, tag, prior) -> Dict[str, object]:
    # A tag of "measured" is only honest if a prior actually exists.
    if tag == "measured" and prior is None:
        tag = UNMEASURED
    return {
        "setup": setup,
        "lane": lane,
        "action_code": code,
        "tag": tag,
        "prior_win": prior[0] if prior else None,
        "prior_ev": prior[1] if prior else None,
        "label": tag,
    }


def inbox_row_mapping(setup: str, action_code=None) -> Dict[str, object]:
    """Prefer the numeric code when we have one; fall back to the name."""
    if action_code is not None:
        by_code = classify_action_code(action_code)
        if by_code["tag"] != UNMEASURED or not (setup or "").strip():
            by_code["setup"] = setup or by_code["setup"]
            return by_code
    return classify_setup_name(setup)