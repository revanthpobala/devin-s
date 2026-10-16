from src.logic.trade_state import fill_odds_10d, trade_state


def test_in_zone_with_room_is_actionable():
    s = trade_state(100, 98, 101, 95, 115, 3)
    assert s["state"] == "ACTIONABLE" and "R:R 3.0" in s["label"]


def test_close_limit_is_armed_with_fill_odds():
    s = trade_state(104, 98, 101, 95, 115, 3)
    assert s["state"] == "ARMED" and s["label"].startswith("ARMED 101.67") and "% fill" in s["label"]


def test_far_limit_and_short_and_gate_are_no_trade():
    assert trade_state(110, 98, 101, 95, 115, 2)["state"] == "NO_TRADE"
    assert trade_state(100, 98, 101, 95, 115, 3, side="SHORT")["state"] == "NO_TRADE"
    g = trade_state(100, 98, 101, 95, 115, 3, gate_rejected=True, reasons=["earnings inside 21 bars"])
    assert g["state"] == "NO_TRADE" and "earnings" in g["detail"]


def test_fill_odds_interpolate_measured_table():
    assert fill_odds_10d(0) == 100 and fill_odds_10d(1.0) == 58 and fill_odds_10d(1.25) == 50 and fill_odds_10d(3) == 31
