"""Leg direction / premium sign must match the structure label (the NOW mislabelled 'bull put' bug)."""

from src.logic.strike_validator import validate_strike_geometry


def test_now_bull_put_that_is_really_a_bear_put_debit_is_rejected():
    # NOW report: "BULL_PUT_SPREAD" long $105P / short $95P, net debit $2.03, max profit $797.
    # That is a bear put debit; it profits when NOW falls. Must be flagged.
    ok, defects = validate_strike_geometry(
        "BULL_PUT_SPREAD", spot_price=112.0, exp_move_pct_21b=31.8,
        long_strike=105.0, short_strike=95.0, max_profit=797.0, max_loss=203.0)
    assert not ok
    assert any("bear put DEBIT" in d or "net debit" in d for d in defects)


def test_correct_bull_put_credit_passes_direction():
    # Proper bull put credit: sell the higher put, buy the lower, collect a credit.
    ok, defects = validate_strike_geometry(
        "BULL_PUT_SPREAD", spot_price=112.0, exp_move_pct_21b=10.0,
        long_strike=95.0, short_strike=100.0, max_profit=154.0, max_loss=346.0)
    assert not any("Mislabelled" in d or "bear put DEBIT" in d for d in defects)


def test_bull_call_debit_with_credit_pnl_is_flagged():
    ok, defects = validate_strike_geometry(
        "BULL_CALL_SPREAD", spot_price=100.0, exp_move_pct_21b=8.0,
        long_strike=105.0, short_strike=110.0, max_profit=200.0, max_loss=800.0)
    assert any("Mislabelled" in d and "credit" in d for d in defects)


def test_correct_bull_call_debit_passes_direction():
    ok, defects = validate_strike_geometry(
        "BULL_CALL_SPREAD", spot_price=100.0, exp_move_pct_21b=8.0,
        long_strike=100.0, short_strike=105.0, max_profit=300.0, max_loss=200.0)
    assert not any("Mislabelled" in d for d in defects)


def test_bear_call_wrong_ordering_is_flagged():
    ok, defects = validate_strike_geometry(
        "BEAR_CALL_SPREAD", spot_price=100.0, exp_move_pct_21b=8.0,
        long_strike=105.0, short_strike=110.0, max_profit=150.0, max_loss=350.0)
    assert any("Mislabelled" in d for d in defects)
