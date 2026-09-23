import json
import logging
from typing import Any, Dict

from src.logic.trigger_gaps import compute_triggers

logging.basicConfig(level=logging.INFO)

def test_code20_trigger():
    f = {
        "action_long": 8.0,
        "price": 100.0,
        "ma200": 110.0,
        "buy": 25.0,
        "rev_l": 5.0,
        "rvol": 1.0,
        "ext_pct": 0.0,
        "stage": 4.0,
    }
    res = compute_triggers(f)
    assert res is not None
    code20 = res["code20_reversal"]
    assert code20["all_passed"] == False

    # We should have 2 open gates: rev_l (5 < 7) and rvol (1.0 < 1.5)
    # Passed: buy < 30, price < ma200
    assert code20["open_count"] == 2
    open_names = [g["name"] for g in code20["open_gates"]]
    assert "rev_zone_l_gte_7" in open_names
    assert "rvol_above_1_5" in open_names
    passed_names = code20["passed_gates"]
    assert "buy_score_lt_30" in passed_names
    assert "close_below_ma200" in passed_names

    # Check lane_edge and blocked_now
    assert res["blocked_now"] == False
    assert code20["lane_edge"].startswith("+0.85%")
    assert res["stage2_prime"]["lane_edge"].startswith("flat -")

    # edge_rank is dynamic: total_count - open_count (Fix #16)
    assert code20["edge_rank"] == code20["total_count"] - code20["open_count"]
    assert res["stage2_prime"]["edge_rank"] == res["stage2_prime"]["total_count"] - res["stage2_prime"]["open_count"]

    # weighted_open_count exists and is a float (Fix #17)
    assert isinstance(code20["weighted_open_count"], float)
    assert code20["weighted_open_count"] > 0

    # nearest_actionable_state is determined by edge_rank desc, weighted_open asc
    assert res["nearest_actionable_state"] in ("code20_reversal", "stage2_prime")


def test_edge_rank_dynamic_readiness():
    """edge_rank should reflect real readiness: a state closer to all-pass
    should rank higher than one with more open gates."""
    # code20: 4 gates, 3 open → edge_rank=1
    # stage2: 7 gates, 6 open → edge_rank=1
    # Same edge_rank → tiebreak by weighted_open_count (code20 wins, lower)
    f = {
        "action_long": 8.0,
        "price": 100.0,
        "ma200": 110.0,
        "buy": 25.0,
        "rev_l": 5.0,
        "rvol": 1.0,
        "ext_pct": 0.0,
        "stage": 4.0,
        "long_in_zone": None,
        "long_rr_valid": None,
        "ext_z_self": None,
    }
    res = compute_triggers(f)
    code20 = res["code20_reversal"]
    stage2 = res["stage2_prime"]
    assert code20["edge_rank"] == code20["total_count"] - code20["open_count"]
    assert stage2["edge_rank"] == stage2["total_count"] - stage2["open_count"]
    assert isinstance(code20["weighted_open_count"], float)
    assert isinstance(stage2["weighted_open_count"], float)


def test_all_passed_max_edge_rank():
    """When all gates pass, edge_rank should equal total_count (maximum)."""
    f = {
        "action_long": 20.0,  # Code 20 (REVERSAL BUY)
        "price": 100.0,
        "ma200": 110.0,  # price < ma200 (close_below_ma200 passes)
        "buy": 25.0,
        "rev_l": 8.0,  # rev_zone >= 7
        "rvol": 2.0,  # rvol > 1.5
        "ext_pct": 0.0,
        "stage": 4.0,
    }
    res = compute_triggers(f)
    code20 = res["code20_reversal"]
    # All required gates should pass
    assert code20["all_passed"] is True
    assert code20["open_count"] == 0
    assert code20["edge_rank"] == code20["total_count"]

def test_hard_exclusions():
    f = {
        "action_long": 16.0, # BLOW-OFF
        "ext_pct": 35.0,     # ext 25-60
        "stage": 0.0,        # warmup
    }
    res = compute_triggers(f)
    assert res is not None
    assert "action_code_16_forbids_entry" in res["hard_exclusions"]
    assert "ext_25_60_no_fresh_long" in res["hard_exclusions"]
    assert "stage_0_not_actionable" in res["hard_exclusions"]
    assert res["blocked_now"] == True

if __name__ == "__main__":
    test_code20_trigger()
    test_hard_exclusions()
    print("All tests passed.")
