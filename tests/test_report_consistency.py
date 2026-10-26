from src.logic import report_consistency as rc
from src.logic.level_validation import validate_levels
from src.logic.pine_levels import apply_rsi2_order

WD = {
    "shares_plan": {"entry_zone_low": 241.63, "entry_zone_high": 243.29, "tactical_stop": 238.3, "target_1": 266.4},
    "options_plan": {"structure": "COVERED_CALL", "short_strike": 265.0, "expiration": "2026-11-27"},
    "next_earnings_date": "2026-10-29", "atr14": 6.9, "levels_source": "pine",
}
DW = {"Exp Move Pct 21b": "9.2"}
POS = "• Options on this name: +1 AMZN  270716C00285000 ($2,005)"


def test_flags_stop_t1_option_and_cc():
    text = "HARD STOP $241.50 on the rest. Stop $238.30 (Pine). T1 $255.40. Sell 1 $265C."
    issues = rc.check(text, WD, DW, 248.04, POS)
    assert any("$241.5" in i for i in issues) and any("T1 $255.4" in i for i in issues)
    assert any("$285 option" in i for i in issues) and any("covered call $265" in i for i in issues)


def test_consistent_report_is_clean():
    wd = {**WD, "options_plan": {"structure": "COVERED_CALL", "short_strike": 285.0, "expiration": "2026-11-27"}}
    text = "Stop $238.30. T1 $266.40. Keep the 285 call; sell the $285C."
    assert rc.check(text, wd, DW, 248.04, POS) == []


def test_stamp_replaces_previous_block(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("# AMZN\nbody\n")
    rc.stamp(f, rc.binding_block(WD, ["x"]))
    rc.stamp(f, rc.binding_block(WD, []))
    t = f.read_text()
    assert t.count(rc._MARK) == 2 and "x" not in t.split("body")[0].replace("AMZN", "") and t.endswith("body\n")


def test_rsi2_order_overrides_judge_levels_and_validates():
    dw = {"_rsi2_state_code": 2, "RSI2 Fixed Stop": "316.79", "RSI2 Fixed Target": "359.41",
          "RSI2 Entry Or Opening Ceiling": "331", "RSI2 ATR14": 7.1029, "close": "331"}
    wd = {"shares_plan": {"entry_zone_low": 321.33, "entry_zone_high": 323.1, "tactical_stop": 317.77, "target_1": 359.67}}
    assert apply_rsi2_order(wd, dw)
    sp = wd["shares_plan"]
    assert (sp["entry_zone_high"], sp["tactical_stop"], sp["target_1"], sp["entry_type"]) == (331.0, 316.79, 359.41, "NEXT_OPEN")
    plan = {**sp, "ticker": "COR", "date": "2026-10-24", "side": "LONG", "setup_lane": "RSI2", "source": "rsi2",
            "is_judge": False, "spot": 331.0, "options_plan": {}}
    assert validate_levels(plan, dw, "LONG", ticker="COR", date_str="2026-10-24")[0]


def test_rsi2_order_needs_pending_state():
    assert not apply_rsi2_order({"shares_plan": {}}, {"_rsi2_state_code": 0, "RSI2 Fixed Stop": "1", "RSI2 Fixed Target": "3",
                                                      "RSI2 Entry Or Opening Ceiling": "2"})
