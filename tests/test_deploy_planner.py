from src.ui.services.deploy_planner import dry_powder, plan

SUMMARY = {"total_liquidation_value": 100000.0, "total_cash_balance": 5000.0}
POSITIONS = [
    {"symbol": "SWVXX", "asset_type": "MUTUAL_FUND", "market_value": 15000.0},
    {"symbol": "GOOGL", "underlying_symbol": "GOOGL", "asset_type": "EQUITY", "market_value": 30000.0},
]
REGIME_BULL = {"tide": {"is_bullish": True, "trend": "BULLISH"}}
REGIME_BEAR = {"tide": {"is_bullish": False, "trend": "DEFENSIVE"}}


def opp(t, **kw):
    base = {"ticker": t, "is_actionable": True, "opportunity_state": "TAKE_NOW", "spot_price": 100.0,
            "entry_high": 100.0, "tactical_stop": 95.0, "target_1": 115.0, "gate_reasons": []}
    base.update(kw)
    return base


def test_dry_powder_counts_cash_and_money_market():
    assert dry_powder(SUMMARY, POSITIONS)["total"] == 20000.0


def test_now_order_sized_by_risk_and_halved_when_tide_not_bullish():
    full = plan(REGIME_BULL, [opp("AAA")], SUMMARY, POSITIONS, min_rr=1.5)["tranches"][0]["orders"][0]
    half = plan(REGIME_BEAR, [opp("AAA")], SUMMARY, POSITIONS, min_rr=1.5)["tranches"][0]["orders"][0]
    assert full["risk"] <= 1000 and half["risk"] <= 500
    assert half["shares"] < full["shares"]


def test_held_concentration_vetoes_add_and_aliases_share_class():
    r = plan(REGIME_BULL, [opp("GOOG")], SUMMARY, POSITIONS, min_rr=1.5)
    assert r["tranches"][0]["orders"] == []
    assert "already" in r["vetoes"][0]["veto"]


def test_limit_only_when_sole_failure_is_zone_and_rr_clears_floor():
    ok = opp("BBB", is_actionable=False, opportunity_state="COILED_TRIGGER", gate_reasons=["not in the long zone"])
    pb = opp("CCC", is_actionable=False, opportunity_state="COILED_TRIGGER",
             gate_reasons=["not in the long zone", "not PB funnel"])
    r = plan(REGIME_BULL, [ok, pb], SUMMARY, POSITIONS, min_rr=1.5)
    assert [o["ticker"] for o in r["tranches"][1]["orders"]] == ["BBB"]


def test_low_rr_vetoed_and_blocked_and_earnings_excluded():
    low = opp("DDD", target_1=102.0)
    r = plan(REGIME_BULL, [low, opp("EEE"), opp("FFF")], SUMMARY, POSITIONS, min_rr=1.5,
             blocked={"EEE": "action code 18"}, days_to_earnings=lambda s: 2 if s == "FFF" else None)
    assert r["tranches"][0]["orders"] == []
    assert {v["ticker"] for v in r["vetoes"]} == {"DDD", "EEE", "FFF"}


def test_reserve_is_remainder_and_context_flags_unmeasured():
    r = plan(REGIME_BULL, [opp("AAA")], SUMMARY, POSITIONS, min_rr=1.5)
    committed = sum(o["notional"] for t in r["tranches"] for o in t["orders"])
    assert round(r["tranches"][2]["budget"] + committed, 2) == 20000.0
    assert "fear_greed" in r["context"]["unmeasured"]


def test_size_tiers_by_rr_band_and_breadth_halving(monkeypatch):
    from src.tracking import rr_config
    from src.ui.services.deploy_planner import rr_size_mult
    monkeypatch.setattr(rr_config, "hi_rr", lambda: 3.0)
    assert [rr_size_mult(r, 1.5) for r in (3.5, 3.0, 2.5, 2.0, 1.7, 1.5, 1.4)] == [1.5, 1.5, 1.0, 1.0, 0.5, 0.5, 0.0]
    assert rr_size_mult(1.2, 1.0) == 0.5 and rr_size_mult(1.49, 1.49) == 0.5 and rr_size_mult(1.48, 1.49) == 0.0
    big = {"total_liquidation_value": 1000000.0, "total_cash_balance": 500000.0}

    def order(regime, tgt):
        r = plan(regime, [opp("AAA", tactical_stop=70.0, target_1=tgt)], big, [], min_rr=1.5)
        return r["tranches"][0]["orders"][0]

    hi, mid, low = order(REGIME_BULL, 190.0), order(REGIME_BULL, 160.0), order(REGIME_BULL, 151.0)
    assert (hi["size_mult"], mid["size_mult"], low["size_mult"]) == (1.5, 1.0, 0.5)
    assert hi["risk"] > mid["risk"] > low["risk"]
    assert order(REGIME_BEAR, 160.0)["risk"] < mid["risk"]


def test_hi_band_still_bounded_by_name_cap():
    o = plan(REGIME_BULL, [opp("AAA", target_1=130.0)], SUMMARY, [], min_rr=1.5)["tranches"][0]["orders"][0]
    assert o["notional"] <= 100000.0 * 0.05


def test_vix_pace_bands():
    from src.ui.services.deploy_planner import vix_pace

    assert vix_pace(12)["range"] == "Below 15"
    assert vix_pace(16.87)["range"] == "15-25"
    assert vix_pace(25)["range"] == "25-35"
    assert vix_pace(40)["range"] == "Above 35"
    assert vix_pace(None) is None
