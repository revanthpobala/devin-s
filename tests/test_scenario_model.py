import json
from src.logic.scenario_model import build_scenario

def test_build_scenario_reversal():
    dw_row = {
        "ma50": "100.0",
        "ma200": "150.0",
        "long_target": "160.0",
        "long_stop_loss": "90.0"
    }
    candidate_prices = [120.0]
    scenarios = build_scenario(dw_row, None, candidate_prices)
    assert len(scenarios) == 1
    s = scenarios[0]
    # Price is below ma200, should be reversal
    assert s["lane"] == "reversal"
    assert "measured long edge" in s["lane_edge"]
    assert s["zone_top"] == 160.0
    assert s["zone_bot"] == 90.0
    # ensure no fabricated probability/return
    assert "probability" not in s
    assert "return" not in s

def test_build_scenario_breakout():
    dw_row = {
        "ma50": "100.0",
        "ma200": "80.0",
    }
    candidate_prices = [115.0] # > ma50 * 1.1
    scenarios = build_scenario(dw_row, None, candidate_prices)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s["lane"] == "breakout-chase"
    assert s["lane_edge"] == "flat/negative"

def test_build_scenario_pullback():
    dw_row = {
        "ma50": "100.0",
        "ma200": "80.0",
    }
    candidate_prices = [95.0] # ma200 <= P <= ma50 * 1.1
    scenarios = build_scenario(dw_row, None, candidate_prices)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s["lane"] == "pullback-flat"
    assert "pullback/breakout lanes are exclusion" in s["lane_edge"]

if __name__ == "__main__":
    test_build_scenario_reversal()
    test_build_scenario_breakout()
    test_build_scenario_pullback()
    print("All scenario tests passed.")
