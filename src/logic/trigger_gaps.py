"""
src/logic/trigger_gaps.py

Buy-Trigger Gap Engine — deterministic distance-to-buy computation.

Given a parsed Data Window dict `f` (output of `parse_data_window`), encodes
the indicator's own actionable-state gate definitions as data and computes how
far the current bar is from satisfying each gate.

Design principles (from the plan):
  1. We do NOT manipulate indicator numbers. No forward-synthesised Buy Score /
     Stage / Dir Prob — those fields have no forward-selection edge (bible §16).
     The deterministic layer only names REAL thresholds and REAL levels (MA200,
     zone top, stop/target).
  2. Facts are deterministic; prediction is the agent's. Each open gate maps to
     a real price-event or a named catalyst, and is capped by the conviction
     ladder.

Feature-flagged via TRIGGERS_ENABLED env.
"""

import os
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
TRIGGERS_ENABLED = os.getenv("TRIGGERS_ENABLED", "1").lower() not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Gate definitions — each gate is a dict with:
#   name:        human-readable gate label
#   field:       key in the parsed Data Window dict `f`
#   comparator:  one of "<", "<=", ">", ">=", "==", "!="
#   ref_level:   the threshold value (a REAL exported level, never synthesised)
#   required:    if True, the gate MUST be satisfied for the parent state
#   change_type: "price_mechanical" (price moving to a level) or
#                "catalyst_dependent" (needs an external event/news)
#
# ref_level can be:
#   - a float literal (e.g. 7.0 for rev_zone >= 7)
#   - a string field name prefixed with "@" to reference another field in `f`
#     (e.g. "@ma200" means the ref_level is f["ma200"])
# ---------------------------------------------------------------------------

# Code 20 REVERSAL BUY gates (the ONLY measured-positive long lane)
# Pine source: ~line 5181 of revanth-enhanced-indicator.pine
# Conditions: Long Rev Zone >= 7 AND Buy Score < 30 AND close < MA200 AND RVOL > 1.5
# AND not already a stop state. The only measured-positive long lane (bible 16.1, +0.85%).
_CODE20_REVERSAL_GATES = [
    {
        "name": "rev_zone_l_gte_7",
        "field": "rev_l",
        "comparator": ">=",
        "ref_level": 7.0,
        "required": True,
        "change_type": "catalyst_dependent",
    },
    {
        "name": "buy_score_lt_30",
        "field": "buy",
        "comparator": "<",
        "ref_level": 30.0,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "close_below_ma200",
        "field": "price",
        "comparator": "<",
        "ref_level": "@ma200",
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "rvol_above_1_5",
        "field": "rvol",
        "comparator": ">",
        "ref_level": 1.5,
        "required": True,
        "change_type": "catalyst_dependent",
    },
]


# Stage-2 PRIME/ACTION long gates
# Pine source: calcActionState ~line 4934 + triple-screen gate ~line 4997
# The >= 98 hatch is the true operative gate per f_finalizeActionLong (~4997) —
# the plain 85 in calcActionState is vetoed 72.6% of the time.
# Conditions: In Zone + RR Valid + Stage 2 + Ext outside 25-60 + Ext Z < 1.5
#   + (triple-screen pass OR buyScore >= 98)
_STAGE2_PRIME_GATES = [
    {
        "name": "in_zone",
        "field": "long_in_zone",
        "comparator": "==",
        "ref_level": 1.0,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "rr_valid",
        "field": "long_rr_valid",
        "comparator": "==",
        "ref_level": 1.0,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "stage_2",
        "field": "stage",
        "comparator": "==",
        "ref_level": 2.0,
        "required": True,
        "change_type": "catalyst_dependent",
    },
    {
        "name": "ext_below_25",
        "field": "ext_pct",
        "comparator": "<",
        "ref_level": 25.0,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "ext_above_neg60",
        "field": "ext_pct",
        "comparator": ">",
        "ref_level": -60.0,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "ext_z_self_lt_1_5",
        "field": "ext_z_self",
        "comparator": "<",
        "ref_level": 1.5,
        "required": True,
        "change_type": "price_mechanical",
    },
    {
        "name": "triple_screen_or_buy_gte_98",
        "field": "buy",
        "comparator": ">=",
        "ref_level": 98.0,
        "required": False,  # OR with triple-screen (not individually required)
        "change_type": "catalyst_dependent",
    },
]


# Hard exclusion codes (never codes, cannot be long)
_HARD_EXCLUSION_CODES = {11, 12, 13, 14, 15, 16, 17, 18}

# Full demotion order from the plan:
# FORMING->SCREEN BLOCK->zone-WAIT; short PRIME/ACTION->WATCH;
# Ext-Z>=1.5->EXTENDED; room->CHASE; RVOL<0.75->EARLY;
# capitulation->REVERSAL BUY
_STOP_STATES = {0, 5}  # Stage 0/5 (warmup / recovery — no fresh long)


# ---------------------------------------------------------------------------
# Conviction ladder — capped at 6 for indicator-only, +1 per external pillar
# ---------------------------------------------------------------------------
_CONVICTION_BASE = 6        # max conviction from indicator alone
_CONVICTION_PER_PILLAR = 1  # each named external pillar adds 1


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------
def _resolve_ref(f: Dict[str, Any], ref_level) -> Optional[float]:
    """Resolve a ref_level: if it starts with '@', look up the field in f."""
    if isinstance(ref_level, str) and ref_level.startswith("@"):
        val = f.get(ref_level[1:])
        return float(val) if val is not None else None
    return float(ref_level)


def _check_gate(f: Dict[str, Any], gate: dict) -> dict:
    """Evaluate a single gate against parsed fields `f`.

    Returns a dict with:
        name, field, comparator, ref_level (resolved), ref_level_raw,
        current_value, passed (bool), gap (float or None),
        change_type, required
    """
    field_name = gate["field"]
    current = f.get(field_name)
    ref_raw = gate["ref_level"]
    ref = _resolve_ref(f, ref_raw)

    result = {
        "name": gate["name"],
        "field": field_name,
        "comparator": gate["comparator"],
        "ref_level": ref,
        "ref_level_raw": ref_raw,
        "current_value": current,
        "passed": False,
        "gap": None,
        "change_type": gate["change_type"],
        "required": gate["required"],
    }

    if current is None or ref is None:
        # Cannot evaluate — gate is open (not passed)
        return result

    current = float(current)
    comp = gate["comparator"]

    if comp == ">=":
        result["passed"] = current >= ref
        result["gap"] = max(0.0, ref - current) if not result["passed"] else 0.0
    elif comp == ">":
        result["passed"] = current > ref
        result["gap"] = max(0.0, ref - current) if not result["passed"] else 0.0
    elif comp == "<=":
        result["passed"] = current <= ref
        result["gap"] = max(0.0, current - ref) if not result["passed"] else 0.0
    elif comp == "<":
        result["passed"] = current < ref
        result["gap"] = max(0.0, current - ref) if not result["passed"] else 0.0
    elif comp == "==":
        result["passed"] = abs(current - ref) < 0.01
        result["gap"] = abs(current - ref) if not result["passed"] else 0.0
    elif comp == "!=":
        result["passed"] = abs(current - ref) >= 0.01
        result["gap"] = 0.0 if result["passed"] else abs(current - ref)

    return result


def _check_hard_exclusions(f: Dict[str, Any]) -> List[str]:
    """Return list of active hard exclusion reasons (empty = no exclusion)."""
    exclusions = []
    act_long = f.get("action_long")
    act_code = int(round(act_long)) if act_long is not None else 0
    if act_code in _HARD_EXCLUSION_CODES:
        exclusions.append(f"action_code_{act_code}_forbids_entry")
    stage = f.get("stage")
    if stage is not None:
        stage_int = int(round(stage))
        if stage_int in _STOP_STATES:
            exclusions.append(f"stage_{stage_int}_not_actionable")
    ext_pct = f.get("ext_pct")
    if ext_pct is not None:
        ext_val = float(ext_pct)
        if 25.0 <= ext_val <= 60.0:
            exclusions.append("ext_25_60_no_fresh_long")
    return exclusions

LANE_EDGE_MAP = {
    "code20_reversal": "+0.85% SIG - only measured long edge (bible 16.1); needs close<MA200 + RVOL>1.5 + Buy<30; ~60d hold",
    "stage2_prime": "flat - no measured edge (bible 16.9); pullback/breakout lanes are exclusion, not alpha",
}


def _evaluate_state(
    f: Dict[str, Any], gates: List[dict], state_name: str
) -> Dict[str, Any]:
    """Evaluate all gates for one actionable state.

    Returns dict with:
        state, gates (list of evaluated gate dicts),
        open_gates (failed gates), passed_gates (satisfied gates),
        all_passed (bool), open_count, total_count
    """
    evaluated = [_check_gate(f, g) for g in gates]
    open_gates = [g for g in evaluated if not g["passed"] and g["required"]]
    passed_gates = [g for g in evaluated if g["passed"]]

    # Weighted open count: sum of each gate's gap normalized by its
    # reference level — a gate one tick from triggering contributes far
    # less than one needing a full catalyst move. (Fix #17)
    weighted_open = 0.0
    for g in open_gates:
        if g["gap"] is None:
            continue
        ref = g["ref_level"] or 1.0
        weighted_open += min(g["gap"] / max(abs(ref), 0.01), 1.0)

    return {
        "state": state_name,
        "gates": evaluated,
        "open_gates": [
            {
                "name": g["name"],
                "field": g["field"],
                "comparator": g["comparator"],
                "ref_level": g["ref_level"],
                "current_value": g["current_value"],
                "gap": g["gap"],
                "change_type": g["change_type"],
                "required": g["required"],
            }
            for g in open_gates
        ],
        "passed_gates": [g["name"] for g in passed_gates],
        "all_passed": len(open_gates) == 0,
        "open_count": len(open_gates),
        "weighted_open_count": round(weighted_open, 4),
        "total_count": len(evaluated),
    }


def compute_triggers(f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compute trigger gaps for all actionable states.

    Args:
        f: parsed Data Window dict (output of parse_data_window).

    Returns:
        None if TRIGGERS_ENABLED is False, otherwise a dict with:
        - hard_exclusions: list of active hard exclusion reasons
        - code20_reversal: evaluated Code 20 state
        - stage2_prime: evaluated Stage-2 PRIME state
        - nearest_actionable_state: name of the nearest state (fewest open gates)
        - open_gates_count: number of open gates on the nearest state
        - conviction_ceiling: max conviction reachable without an external pillar
    """
    if not TRIGGERS_ENABLED:
        return None

    hard_exclusions = _check_hard_exclusions(f)

    code20 = _evaluate_state(f, _CODE20_REVERSAL_GATES, "code20_reversal")
    stage2 = _evaluate_state(f, _STAGE2_PRIME_GATES, "stage2_prime")
    
    code20["lane_edge"] = LANE_EDGE_MAP["code20_reversal"]
    stage2["lane_edge"] = LANE_EDGE_MAP["stage2_prime"]
    code20["edge_rank"] = code20["total_count"] - code20["open_count"]
    stage2["edge_rank"] = stage2["total_count"] - stage2["open_count"]

    # Determine nearest actionable state (edge_rank desc, weighted_open_count asc)
    states = [code20, stage2]
    states.sort(key=lambda s: (-s["edge_rank"], s["weighted_open_count"]))
    nearest = states[0]

    # Conviction ceiling: indicator-only = 6, no pillar bonus without catalyst
    conviction_ceiling = _CONVICTION_BASE
    
    blocked_now = len(hard_exclusions) > 0
    edge_note = f"Edge is in code20_reversal (open_gates={code20['open_count']})"

    return {
        "hard_exclusions": hard_exclusions,
        "blocked_now": blocked_now,
        "edge_note": edge_note,
        "code20_reversal": {
            "state": code20["state"],
            "all_passed": code20["all_passed"],
            "open_gates": code20["open_gates"],
            "passed_gates": code20["passed_gates"],
            "open_count": code20["open_count"],
            "weighted_open_count": code20["weighted_open_count"],
            "total_count": code20["total_count"],
            "edge_rank": code20["edge_rank"],
            "lane_edge": code20["lane_edge"],
        },
        "stage2_prime": {
            "state": stage2["state"],
            "all_passed": stage2["all_passed"],
            "open_gates": stage2["open_gates"],
            "passed_gates": stage2["passed_gates"],
            "open_count": stage2["open_count"],
            "weighted_open_count": stage2["weighted_open_count"],
            "total_count": stage2["total_count"],
            "edge_rank": stage2["edge_rank"],
            "lane_edge": stage2["lane_edge"],
        },
        "nearest_actionable_state": nearest["state"],
        "open_gates_count": nearest["open_count"],
        "conviction_ceiling": conviction_ceiling,
    }
