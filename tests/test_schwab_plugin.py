"""
tests/test_schwab_plugin.py
Unit tests for Schwab Analytics Plugin and active position formatting.
"""

from unittest.mock import patch
import pandas as pd
import pytest

from src.plugins.plugin_manager import plugin_manager
from src.plugins.schwab_plugin import SchwabPlugin, format_active_position_block


def test_plugin_registration():
    plugin = plugin_manager.get_plugin("schwab_analytics")
    assert plugin is not None
    assert isinstance(plugin, SchwabPlugin)
    assert plugin.name == "schwab_analytics"
    assert "Schwab" in plugin.description


def test_schwab_plugin_index_exclusions():
    plugin = SchwabPlugin()
    df = pd.DataFrame()
    dw = {}
    for idx in ["SPX", "VIX", "NDX", "RUT", "DJI"]:
        res = plugin.run(idx, df, dw)
        assert res == {}


def test_schwab_plugin_with_active_position():
    plugin = SchwabPlugin()
    df = pd.DataFrame()
    dw = {}

    mock_pos = {
        "has_position": True,
        "source": "SCHWAB",
        "ticker": "AMZN",
        "quantity": 100,
        "average_price": 185.20,
        "current_price": 248.39,
        "market_value": 24839.00,
        "day_pnl": -101.00,
        "day_pnl_pct": -0.40,
        "account": "***1234",
        "side": "LONG",
        "asset_type": "EQUITY",
    }

    mock_flow = {
        "status": "ok",
        "sentiment": "BULLISH_SWEEPS",
        "sentiment_label": "Bullish Institutional Call Accumulation",
        "call_sweeps_count": 5,
        "put_sweeps_count": 1,
        "total_call_premium": 2500000,
        "total_put_premium": 350000,
        "put_call_volume_ratio": 0.25,
        "put_call_premium_ratio": 0.14,
        "total_anomalies_count": 6,
        "anomalies": [
            {
                "type": "CALL",
                "strike": 250.0,
                "expiry": "2026-09-18",
                "volume": 12500,
                "open_interest": 4200,
                "notional_premium": 1500000,
            }
        ],
    }

    with patch("src.clients.schwab_client.get_active_position_for_ticker", return_value=mock_pos), \
         patch("src.clients.schwab_client.get_unusual_options_flow_data", return_value=mock_flow):
        res = plugin.run("AMZN", df, dw)

    assert res["schwab_has_position"] is True
    assert res["schwab_quantity"] == 100
    assert res["schwab_cost_basis"] == 185.20
    assert res["schwab_flow_sentiment"] == "BULLISH_SWEEPS"
    assert res["schwab_total_call_premium"] == 2500000
    assert len(res["schwab_top_sweeps"]) == 1
    assert "CALL $250.0" in res["schwab_top_sweeps"][0]


def test_format_active_position_block_flat():
    with patch("src.clients.schwab_client.get_active_position_for_ticker", return_value={"has_position": False}):
        block = format_active_position_block("NVDA")
        assert "USER ACTIVE BROKER POSITION: NONE" in block


def test_format_active_position_block_active():
    mock_pos = {
        "has_position": True,
        "source": "SCHWAB",
        "ticker": "AMZN",
        "quantity": 100,
        "average_price": 185.20,
        "last_price": 248.39,
        "market_value": 24839.00,
        "day_pnl": 6319.00,
        "day_pnl_pct": 34.12,
        "account": "***1234",
        "side": "LONG",
        "asset_type": "EQUITY",
    }
    with patch("src.clients.schwab_client.get_active_position_for_ticker", return_value=mock_pos):
        block = format_active_position_block("AMZN")
        assert "--- USER ACTIVE BROKER POSITION (SCHWAB) ---" in block
        assert "ACTIVE HOLDING (LONG 100 units / EQUITY)" in block
        assert "Cost Basis: $185.20" in block
        assert "MANDATORY SENIOR PM DIRECTIVE" in block
        assert "Active Holding Playbook" in block
