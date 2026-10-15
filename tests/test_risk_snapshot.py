from src.tracking.risk_snapshot import compute_snapshot, regime_dial


def _held():
    return [
        {"underlying_symbol": "AAA", "asset_type": "EQUITY", "market_value": 40000, "day_profit_loss_pct": -1},
        {"underlying_symbol": "BBB", "asset_type": "EQUITY", "market_value": 30000},
        {"underlying_symbol": "CCC", "asset_type": "EQUITY", "market_value": 10000},
        {"underlying_symbol": "SWVXX", "asset_type": "MUTUAL_FUND", "market_value": 10000},
        {"underlying_symbol": "$NDX", "asset_type": "OPTION", "market_value": 10000},
    ]


STATS = {"beta": {"AAA": 1.5, "BBB": 1.0}, "sector": {"AAA": "Tech", "BBB": "Tech", "CCC": "Energy"},
         "corr": {"AAA|BBB": 0.85, "AAA|CCC": 0.2}}


def test_exposure_beta_clusters_and_hedge():
    s = compute_snapshot(_held(), 100000, {}, STATS)
    assert s["cash_like_pct"] == 10.0 and s["options_pct"] == 10.0
    assert s["beta_weighted_pct"] == 90.0  # 40k*1.5 + 30k*1.0
    assert s["unknown_beta"] == ["CCC"]
    assert s["clusters"] == [["AAA", "BBB"]] or s["clusters"] == [["BBB", "AAA"]]
    assert s["hedge"]["spy_notional_to_neutralize"] == 90000
    assert any("AAA is 40.0% of the book" in f for f in s["flags"])


def test_open_risk_cap_and_missing_stop():
    tracked = {
        "AAA": {"side": "LONG", "entry_price": 100.0, "stop": 90.0, "quantity": 400},
        "BBB": {"side": "SHORT", "entry_price": 50.0, "stop": 55.0, "quantity": 200},
        "CCC": {"side": "LONG", "entry_price": 10.0, "stop": None, "quantity": 100},
    }
    s = compute_snapshot(_held(), 100000, tracked, STATS, earnings_days={"AAA": 3, "BBB": 40})
    assert s["open_risk"] == 5000 and s["open_risk_pct"] == 5.0
    assert s["missing_stop"] == ["CCC"]
    assert not any("exceeds" in f for f in s["flags"])
    tracked["AAA"]["quantity"] = 500
    s = compute_snapshot(_held(), 100000, tracked, STATS, earnings_days={"AAA": 3})
    assert any("exceeds the 5.0% cap" in f for f in s["flags"])
    assert any("AAA reports in 3 days" in f for f in s["flags"])


def test_stop_locked_in_profit_is_zero_risk():
    tracked = {"AAA": {"side": "LONG", "entry_price": 100.0, "stop": 120.0, "quantity": 100}}
    assert compute_snapshot(_held(), 100000, tracked, STATS)["open_risk"] == 0


def test_regime_dial():
    up = {"available": True, "tide": {"is_bullish": True}, "vix": {"price": 15}}
    assert regime_dial(up)["multiplier"] == 1.0
    assert regime_dial({**up, "vix": {"price": 24}})["multiplier"] == 0.5
    assert regime_dial({"available": True, "tide": {"is_bullish": False}, "vix": {"price": 35}})["multiplier"] == 0.25
    assert regime_dial(None)["multiplier"] == 0.5
