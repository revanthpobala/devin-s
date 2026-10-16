from src.logic.pine_levels import apply_pine_levels, pine_long_plan

DW = {"close": "100", "Long Stop Loss": "95", "Long Target": "112", "Long Entry Zone Bot": "97", "Long Entry Zone Top": "99"}


def test_pine_levels_overwrite_llm_and_keep_higher_t2():
    wd = {"side": "LONG", "shares_plan": {"entry_zone_low": 99.5, "entry_zone_high": 100, "tactical_stop": 98.8,
                                         "target_1": 104, "target_2": 120}, "invalidation": {"price_level": 98.8}}
    assert apply_pine_levels(wd, DW) == "pine"
    sp = wd["shares_plan"]
    assert (sp["entry_zone_low"], sp["entry_zone_high"], sp["tactical_stop"], sp["target_1"], sp["target_2"]) == (97, 99, 95, 112, 120)
    assert wd["invalidation"]["price_level"] == 95 and wd["llm_levels"]["tactical_stop"] == 98.8


def test_invalid_pine_plan_keeps_llm_levels():
    bad = {**DW, "Long Target": "50"}
    assert pine_long_plan(bad) is None
    wd = {"side": "LONG", "shares_plan": {"tactical_stop": 74.5}}
    assert apply_pine_levels(wd, bad) == "llm" and wd["shares_plan"]["tactical_stop"] == 74.5


def test_no_pine_zone_drops_llm_zone_below_pine_stop():
    dw = {k: v for k, v in DW.items() if "Zone" not in k}
    wd = {"side": "LONG", "shares_plan": {"entry_zone_low": 94, "entry_zone_high": 96, "entry_type": "LIMIT", "target_2": 105}}
    apply_pine_levels(wd, dw)
    assert wd["shares_plan"]["entry_type"] == "NO_ENTRY" and wd["shares_plan"]["target_2"] == 0.0
