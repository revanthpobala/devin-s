from src.tracking import schwab_portfolio_manager as pm


def _pos(sym, asset, qty, mv, cost, day=0.0, under=None):
    return {"symbol": sym, "underlying_symbol": under or sym, "asset_type": asset, "quantity": qty,
            "current_price": mv / qty if qty else 0.0, "market_value": mv, "cost_basis": cost,
            "unrealized_profit_loss": mv - cost, "day_profit_loss": day, "account_number_masked": "***1"}


def test_narrative_and_gains_come_from_positions(monkeypatch):
    positions = [
        _pos("GOOGL", "EQUITY", 100, 40000.0, 20000.0, -800.0),
        _pos("AMZN", "EQUITY", 50, 10000.0, 5000.0, -100.0),
        _pos("SWVXX", "MUTUAL_FUND", 1000, 1000.0, 1000.0),
        _pos("PLTR", "EQUITY", 10, 3000.0, 300.0),
    ]
    summary = {"total_liquidation_value": 100000.0, "total_cash_balance": 2000.0, "equity_value": 53000.0,
               "mutual_fund_value": 1000.0, "option_value": 0.0, "total_day_pnl": -900.0,
               "total_day_pnl_pct": -0.9, "total_unrealized_pnl": 0.0, "total_unrealized_pnl_pct": 0.0}
    monkeypatch.setattr(pm, "sync_schwab_positions", lambda: None)
    monkeypatch.setattr(pm, "get_portfolio_summary", lambda: summary)
    monkeypatch.setattr(pm, "get_portfolio_positions", lambda: positions)
    monkeypatch.setattr(pm, "_ANALYSIS_CACHE", None)
    out = pm.get_portfolio_analysis(force_refresh=True)
    a = out["allocation"]
    assert a["top2_unrealized"] == 25000.0 and a["googl_gain_pct"] == 100.0
    assert a["top_gain_pct"][0] == {"symbol": "PLTR", "unrealized_pnl_pct": 900.0}
    txt = out["briefing"]["what_is_happening"]
    assert "$3,000" in txt and "3.0%" in txt and "$100k+" not in txt and "firmly positive" not in txt
