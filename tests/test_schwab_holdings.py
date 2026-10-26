import importlib.util
import sys
import types
from unittest.mock import patch

import pytest

DATA = {"status": "ok", "accounts": [
    {"account_number": "***9638", "positions": [
        {"symbol": "AMZN", "asset_type": "EQUITY", "quantity": 10.0, "average_price": 200.0, "market_value": 2480.0, "day_pnl": 1.0}]},
    {"account_number": "***6929", "positions": [
        {"symbol": "AMZN", "asset_type": "EQUITY", "quantity": 123.0, "average_price": 140.0, "market_value": 30504.0, "day_pnl": 2.0},
        {"symbol": "KO", "asset_type": "EQUITY", "quantity": 5.0, "average_price": 60.0, "market_value": 300.0}]},
    {"account_number": "***2422", "positions": [
        {"symbol": "AMZN  270716C00285000", "asset_type": "OPTION", "quantity": 1.0, "average_price": 30.0, "market_value": 2005.0}]},
]}


@pytest.fixture
def sc():
    for m in ("schwab", "schwab.auth", "schwab.client"):
        sys.modules.setdefault(m, types.ModuleType(m))
    sys.modules["schwab"].auth, sys.modules["schwab"].client = sys.modules["schwab.auth"], sys.modules["schwab.client"]
    from src.clients import schwab_client
    return schwab_client


def test_holding_sums_every_account_and_lists_options(sc):
    with patch.object(sc, "get_schwab_positions", return_value=DATA):
        p = sc.get_active_position_for_ticker("AMZN")
    assert p["quantity"] == 133 and p["market_value"] == 32984.0 and p["day_pnl"] == 3.0
    assert p["average_price"] == pytest.approx((10 * 200 + 123 * 140) / 133, abs=1e-3)
    assert [a["quantity"] for a in p["accounts"]] == [10.0, 123.0]
    assert p["options"][0]["quantity"] == 1.0 and "285C" not in p["options"][0]["symbol"]


def test_all_active_positions_one_aggregated_row_per_symbol(sc):
    with patch.object(sc, "get_schwab_positions", return_value=DATA), \
            patch("src.tracking.position_state.load_state", return_value={}):
        rows = {r["ticker"]: r for r in sc.get_all_active_positions()}
    assert rows["AMZN"]["quantity"] == 133 and rows["KO"]["quantity"] == 5


def test_position_block_states_total_and_covered_call_capacity(sc):
    spec = importlib.util.spec_from_file_location("schwab_plugin_t", "src/plugins/schwab_plugin.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("src.plugins", types.ModuleType("src.plugins")).__path__ = []
    base = types.ModuleType("src.plugins.base_plugin")
    base.BaseAnalyticsPlugin = type("BaseAnalyticsPlugin", (), {})
    sys.modules.setdefault("src.plugins.base_plugin", base)
    spec.loader.exec_module(mod)
    with patch.object(sc, "get_schwab_positions", return_value=DATA):
        txt = mod.format_active_position_block("AMZN")
    assert "133 units" in txt and "***9638: 10 @ $200.00" in txt and "***6929: 123 @ $140.00" in txt
    assert "1 covered-call contract(s) possible" in txt and "AMZN  270716C00285000".strip() in txt
