import os

from src.logic.response_model import bucket_for, state_response

def test_bucket_for_reversal_deep_oops():
    f = {
        "price": 90.0,
        "ma200": 100.0,
        "buy": 20.0,
        "rvol": 2.0,
        "rev_l": 8.0,
        "ext_pct": -10.0,
        "rev_mask": 1024 # OOPS bit
    }
    assert bucket_for(f) == "reversal_deep_oops"

def test_bucket_for_strong_buy_mom():
    f = {
        "buy": 95.0,
    }
    assert bucket_for(f) == "strong_buy_mom"

def test_bucket_for_watch_code8():
    f = {
        "action_long": 8.0
    }
    assert bucket_for(f) == "watch_code8"

def test_bucket_for_empty():
    f = {}
    assert bucket_for(f) == "baseline_all"

def test_bucket_for_nans():
    f = {
        "price": None,
        "ma200": None,
        "buy": None,
        "rvol": None,
        "rev_l": None,
        "ext_pct": None,
        "rev_mask": None
    }
    assert bucket_for(f) == "baseline_all"

def test_state_response():
    f = {
        "buy": 95.0,
    }
    resp = state_response(f)
    if resp is not None:
        assert resp["bucket"] == "strong_buy_mom"
        assert "edge_vs_baseline" in resp

def test_disabled_flag():
    os.environ["RESPONSE_MODEL_ENABLED"] = "0"
    # Need to reload or it will use the cached ENABLED from module load
    import importlib
    import src.logic.response_model
    importlib.reload(src.logic.response_model)
    
    f = {"buy": 95.0}
    resp = src.logic.response_model.state_response(f)
    assert resp is None
    
    # Reset for other tests
    os.environ.pop("RESPONSE_MODEL_ENABLED", None)
    importlib.reload(src.logic.response_model)

if __name__ == "__main__":
    test_bucket_for_reversal_deep_oops()
    test_bucket_for_strong_buy_mom()
    test_bucket_for_watch_code8()
    test_bucket_for_empty()
    test_bucket_for_nans()
    test_state_response()
    test_disabled_flag()
    print("All tests passed!")
