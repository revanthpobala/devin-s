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

if __name__ == "__main__":
    test_code20_trigger()
    test_hard_exclusions()
    print("All tests passed.")
