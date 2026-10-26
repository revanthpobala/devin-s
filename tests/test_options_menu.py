from datetime import date

import pandas as pd

from src.logic.options_menu import attach_options_menu, build_options_menu


def _df(strikes, spot, put=False):
    rows = []
    for k in strikes:
        intrinsic = max(0.0, (k - spot) if put else (spot - k))
        mid = intrinsic + max(0.5, 4.0 - abs(k - spot) * 0.15)
        rows.append({"strike": k, "bid": mid - 0.2, "ask": mid + 0.2, "impliedVolatility": 0.4})
    return pd.DataFrame(rows)


def _chain(spot):
    strikes = [spot + d for d in range(-40, 45, 5)]
    exps = ["2026-11-27", "2027-06-17"]
    return exps, (lambda e: (_df(strikes, spot), _df(strikes, spot, put=True)))


PLAN = {"tactical_stop": 95.0, "target_1": 115.0}


def test_three_tiers_built_regardless_of_edge():
    m = build_options_menu("X", 100.0, PLAN, today=date(2026, 10, 25), chain=_chain(100.0), earnings="2026-11-12")
    assert m["tactical_spread"]["structure"] == "BULL_CALL_SPREAD"
    assert m["tactical_spread"]["short_strike"] <= 115.0 and m["tactical_spread"]["long_strike"] <= 100.0
    assert m["leaps"]["structure"] == "LONG_CALL" and 0.7 < m["leaps"]["delta"] < 0.9
    assert m["income_or_csp"]["structure"] == "CASH_SECURED_PUT" and m["income_or_csp"]["short_strike"] <= 95.0
    assert all("PAPER idea" in m[k]["summary"] for k in ("tactical_spread", "leaps", "income_or_csp"))
    assert "spans earnings" in m["tactical_spread"]["summary"]


def test_levels_fallback_when_plan_empty():
    m = build_options_menu("X", 100.0, {}, em_pct=10.0, today=date(2026, 10, 25), chain=_chain(100.0))
    assert m["levels_used"] == {"stop": 95.0, "t1": 110.0}


def test_no_expiry_gives_none_not_crash():
    m = build_options_menu("X", 100.0, PLAN, today=date(2026, 10, 25), chain=([], lambda e: None))
    assert m["tactical_spread"]["structure"] == "NONE"


def test_attach_skips_short_and_keeps_menu_when_empty(monkeypatch):
    wd = {"side": "SHORT", "options_menu": {"keep": 1}}
    attach_options_menu(wd, "X", 100.0)
    assert wd["options_menu"] == {"keep": 1}


def test_broker_grid_feeds_menu_and_beats_yfinance(monkeypatch):
    from src.clients import options_client as oc

    spot = 100.0
    grid = {}
    for exp, dte in (("2026-11-27", 33), ("2027-06-17", 235)):
        for k in range(60, 145, 5):
            tv = max(0.5, 4.0 - abs(k - spot) * 0.15)
            c = max(0.0, spot - k) + tv
            p = max(0.0, k - spot) + tv
            grid[(exp, float(k))] = {
                "exp": exp, "dte": dte, "strike": float(k),
                "calls": {"bid": c - 0.2, "ask": c + 0.2, "mid": c, "delta": None, "oi": 10},
                "puts": {"bid": p - 0.2, "ask": p + 0.2, "mid": p, "delta": None, "oi": 10}}

    def fake_schwab(ticker, intent):
        return oc._render_chain(ticker, grid, {"2026-11-27", "2027-06-17"}, "BOTH", None, spot, "Schwab")

    monkeypatch.setattr(oc, "_fetch_schwab_chain", fake_schwab)
    monkeypatch.setattr(oc, "_fetch_tastytrade_chain", lambda *a: None)
    monkeypatch.setattr(oc, "_fetch_alpaca_chain", lambda *a: None)
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", lambda *a: (_ for _ in ()).throw(AssertionError("yfinance used")))
    m = build_options_menu("X", spot, PLAN, today=date(2026, 10, 25))
    assert m["source"] == "Schwab"
    assert m["tactical_spread"]["structure"] == "BULL_CALL_SPREAD"
    assert m["leaps"]["structure"] == "LONG_CALL" and 0.7 < m["leaps"]["delta"] < 0.9
    assert m["income_or_csp"]["structure"] == "CASH_SECURED_PUT"


def test_no_broker_returns_none(monkeypatch):
    from src.clients import options_client as oc

    for n in ("_fetch_schwab_chain", "_fetch_tastytrade_chain", "_fetch_alpaca_chain"):
        monkeypatch.setattr(oc, n, lambda *a: None)
    assert oc.fetch_chain_grid("X") is None
