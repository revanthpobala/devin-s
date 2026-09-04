"""
tests/test_options_skew_gex_plugin.py
Unit tests for Options Skew & Dealer Gamma (GEX) quantitative plugin.
"""

import pandas as pd
import pytest
from src.plugins.options_skew_gex_plugin import OptionsSkewGEXPlugin


def test_options_skew_gex_index_skip():
    plugin = OptionsSkewGEXPlugin()
    df = pd.DataFrame({"close": [5000.0, 5010.0]})
    res = plugin.run("SPX", df, {"price": 5010.0})
    assert res == {}


def test_options_skew_gex_heuristic_fallback():
    plugin = OptionsSkewGEXPlugin()
    df = pd.DataFrame({
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
        "volume": [10000, 12000, 15000],
    })
    dw = {
        "price": 103.0,
        "tastytrade_iv_rank": 72.0,
        "tastytrade_iv_hv_spread": 4.5,
        "atr": 2.5,
    }
    res = plugin.run("TEST", df, dw)
    assert "options_skew_25d" in res
    assert "options_call_wall" in res
    assert "options_put_wall" in res
    assert "options_zero_gamma_flip" in res
    assert "options_net_gex_regime" in res
    assert res["options_call_wall"] > dw["price"]
    assert res["options_put_wall"] < dw["price"]
    assert res["options_call_wall"] > res["options_put_wall"]


def test_options_skew_gex_mock_chain():
    plugin = OptionsSkewGEXPlugin()
    spot = 150.0
    mock_snapshots = {
        "TEST260918C00160000": {
            "dailyBar": {"v": 500},
            "greeks": {"delta": 0.26, "gamma": 0.025},
            "impliedVolatility": 0.28,
        },
        "TEST260918C00165000": {
            "dailyBar": {"v": 1500},
            "greeks": {"delta": 0.15, "gamma": 0.015},
            "impliedVolatility": 0.29,
        },
        "TEST260918P00140000": {
            "dailyBar": {"v": 1800},
            "greeks": {"delta": -0.24, "gamma": 0.028},
            "impliedVolatility": 0.34,
        },
    }
    res = plugin._process_chain_snapshots(mock_snapshots, spot)
    assert res["options_call_wall"] == 165.0
    assert res["options_put_wall"] == 140.0
    assert res["options_skew_25d"] == pytest.approx(6.0, abs=0.5)
    assert "options_net_gex_regime" in res
