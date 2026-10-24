from datetime import date

from src.tracking.spread_check import spread_events, spread_flags

TODAY = date(2026, 10, 14)


def _leg(strike, qty, px, typ="CALL", exp="2026-10-15"):
    return {"asset_type": "OPTION", "underlying_symbol": "$NDX", "option_strike": strike, "option_type": typ, "quantity": qty,
            "current_price": px, "option_expiration": exp}


def test_deep_itm_debit_spread_is_flagged_with_downside_and_upside():
    f = spread_flags([_leg(29200, 1, 735.7), _leg(29225, -1, 711.8)], TODAY)
    assert len(f) == 1
    assert f[0]["downside"] == 2390.0 and f[0]["upside"] == 110.0 and f[0]["ratio"] == 21.7
    ev = spread_events(f, "2026-10-14")[0]
    assert ev["kind"] == "SPREAD_RISK" and "risk $2,390 to make $110" in ev["title"]


def test_balanced_spread_far_expiry_and_naked_legs_are_not_flagged():
    assert spread_flags([_leg(100, 1, 6.0), _leg(110, -1, 2.0)], TODAY) == []
    assert spread_flags([_leg(29200, 1, 735.7, exp="2026-12-18"), _leg(29225, -1, 711.8, exp="2026-12-18")], TODAY) == []
    assert spread_flags([_leg(29200, 1, 735.7)], TODAY) == []


def test_credit_put_spread_nearly_worthless_is_flagged_and_one_nearly_at_max_is_not():
    f = spread_flags([_leg(90, 1, 0.2, "PUT"), _leg(100, -1, 1.0, "PUT")], TODAY)
    assert len(f) == 1 and f[0]["upside"] == 80.0 and f[0]["downside"] == 920.0
    assert spread_flags([_leg(90, 1, 0.2, "PUT"), _leg(100, -1, 9.7, "PUT")], TODAY) == []


def test_context_block_carries_the_computed_numbers():
    from src.tracking.spread_check import context_text
    txt = context_text(spread_flags([_leg(29200, 1, 735.7), _leg(29225, -1, 711.8)], TODAY))
    assert "remaining loss $2,390, remaining gain $110 (21.7:1)" in txt and "not from the entry price" in txt
    assert context_text([]) == ""


def _vleg(strike, qty, avg, px, typ="CALL"):
    return {"asset_type": "OPTION", "underlying_symbol": "NDX", "option_expiration": "2026-10-16", "option_type": typ,
            "option_strike": strike, "quantity": qty, "average_price": avg, "current_price": px}


def test_open_vertical_debit_math_ndx():
    from src.tracking.spread_check import open_verticals, vertical_context
    v = open_verticals([_vleg(29140, 1, 390.41, 139.70), _vleg(29150, -1, 380.58, 132.55)])[0]
    assert v["kind"] == "debit" and v["net_per_share"] == 9.83
    assert v["max_loss"] == 983.0 and v["max_profit"] == 17.0 and v["breakeven"] == 29149.83
    assert v["mark_value"] == 715.0
    assert "max loss $983.00" in vertical_context([_vleg(29140, 1, 390.41, 139.70), _vleg(29150, -1, 380.58, 132.55)])


def test_open_vertical_credit_put_math():
    from src.tracking.spread_check import open_verticals
    v = open_verticals([_vleg(95, 2, 0.5, 0.2, "PUT"), _vleg(100, -2, 1.8, 0.6, "PUT")])[0]
    assert v["kind"] == "credit" and v["net_per_share"] == 1.3
    assert v["max_profit"] == 260.0 and v["max_loss"] == 740.0 and v["breakeven"] == 98.7


def test_vertical_context_flags_skewed_payoff():
    from src.tracking.spread_check import vertical_context
    legs = [_vleg(29140, 1, 390.41, 139.70), _vleg(29150, -1, 380.58, 132.55)]
    assert "RISK FLAG: risks $983 to make $17 (57.8:1)" in vertical_context(legs)
    ok = [_vleg(100, 1, 3.0, 3.0), _vleg(110, -1, 1.0, 1.0)]
    assert "RISK FLAG" not in vertical_context(ok)
