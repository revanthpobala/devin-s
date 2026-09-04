"""
tests/test_conformal_prediction_plugin.py
Unit tests for Conformal Prediction Quantile Envelopes plugin.
"""

import numpy as np
import pandas as pd
import pytest
from src.plugins.conformal_prediction_plugin import ConformalPredictionPlugin


def test_conformal_prediction_short_df():
    plugin = ConformalPredictionPlugin()
    df = pd.DataFrame({"close": [10.0, 11.0]})
    res = plugin.run("TEST", df, {"price": 11.0})
    assert res == {}


def test_conformal_prediction_quantiles():
    plugin = ConformalPredictionPlugin()
    # Generate 120 bars of realistic price data with moderate volatility
    np.random.seed(42)
    base_price = 100.0
    returns = np.random.normal(loc=0.0005, scale=0.015, size=120)
    closes = base_price * np.cumprod(1 + returns)
    highs = closes * (1 + np.random.uniform(0.002, 0.015, size=120))
    lows = closes * (1 - np.random.uniform(0.002, 0.015, size=120))
    opens = (highs + lows) / 2

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.random.randint(500000, 2000000, size=120),
    })

    spot = float(closes[-1])
    res = plugin.run("TEST", df, {"price": spot})

    assert "conformal_90_floor_5d" in res
    assert "conformal_90_ceiling_5d" in res
    assert "conformal_90_floor_14d" in res
    assert "conformal_90_ceiling_14d" in res
    assert "conformal_90_floor_21d" in res
    assert "conformal_90_ceiling_21d" in res
    assert "conformal_certified_stop_14d" in res
    assert "conformal_14d_envelope" in res
    assert "conformal_empirical_coverage_score" in res

    # Check that floor is strictly below ceiling
    assert res["conformal_90_floor_14d"] < res["conformal_90_ceiling_14d"]
    # Check that certified stop is below current spot
    assert res["conformal_certified_stop_14d"] < spot
