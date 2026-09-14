"""
tests/test_schwab_portfolio.py
Unit tests for the dedicated Schwab Portfolio and Positions Manager (data/schwab_portfolio.db).
"""

import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from src.tracking.schwab_portfolio_manager import (
    DB_PATH,
    init_portfolio_db,
    mask_account_number,
    parse_option_details,
    sync_schwab_positions,
    get_portfolio_summary,
    get_portfolio_positions,
)


def test_mask_account_number():
    assert mask_account_number("123456789") == "***6789"
    assert mask_account_number("96806929") == "***6929"
    assert mask_account_number("123") == "123"
    assert mask_account_number("") == "Unknown"
    assert mask_account_number(None) == "Unknown"


def test_parse_option_details():
    # OCC format
    opt1 = parse_option_details("NVO   270115C00070000", "NOVO-NORDISK A S 01/15/2027 $70 Call", "CALL")
    assert opt1["strike"] == 70.0
    assert opt1["option_type"] == "CALL"
    assert opt1["expiration"] == "2027-01-15"

    opt2 = parse_option_details("AMZN  270716C00285000", "AMAZON.COM INC 07/16/2027 $285 Call", "CALL")
    assert opt2["strike"] == 285.0
    assert opt2["option_type"] == "CALL"
    assert opt2["expiration"] == "2027-07-16"

    opt3 = parse_option_details("SPY   260918P00550000", "", "PUT")
    assert opt3["strike"] == 550.0
    assert opt3["option_type"] == "PUT"
    assert opt3["expiration"] == "2026-09-18"


def test_init_portfolio_db_creates_tables():
    init_portfolio_db()
    assert DB_PATH.exists()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "schwab_accounts" in tables
    assert "schwab_positions" in tables
    assert "portfolio_snapshots" in tables
    conn.close()


def test_sync_schwab_positions_with_mock():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "securitiesAccount": {
                "accountNumber": "11112222",
                "type": "MARGIN",
                "currentBalances": {
                    "liquidationValue": 50000.0,
                    "cashBalance": 5000.0,
                    "availableFunds": 25000.0,
                    "buyingPower": 50000.0,
                    "longMarketValue": 45000.0,
                    "mutualFundValue": 0.0,
                    "longOptionMarketValue": 0.0,
                    "shortOptionMarketValue": 0.0,
                },
                "positions": [
                    {
                        "longQuantity": 10.0,
                        "shortQuantity": 0.0,
                        "averagePrice": 150.0,
                        "marketValue": 2000.0,
                        "currentDayProfitLoss": 50.0,
                        "currentDayProfitLossPercentage": 2.5,
                        "longOpenProfitLoss": 500.0,
                        "instrument": {
                            "symbol": "AAPL",
                            "underlyingSymbol": "AAPL",
                            "assetType": "EQUITY",
                            "description": "APPLE INC",
                        },
                    },
                    {
                        "longQuantity": 1.0,
                        "shortQuantity": 0.0,
                        "averagePrice": 5.0,
                        "marketValue": 800.0,
                        "currentDayProfitLoss": 100.0,
                        "currentDayProfitLossPercentage": 14.2,
                        "longOpenProfitLoss": 300.0,
                        "instrument": {
                            "symbol": "NVDA  270115C00150000",
                            "underlyingSymbol": "NVDA",
                            "assetType": "OPTION",
                            "description": "NVIDIA 01/15/2027 $150 Call",
                            "putCall": "CALL",
                        },
                    }
                ]
            }
        }
    ]
    mock_client.get_accounts.return_value = mock_resp

    res = sync_schwab_positions(client=mock_client)
    assert res["success"] is True
    assert res["accounts_count"] == 1
    assert res["positions_count"] == 2
    assert res["total_liquidation_value"] == 50000.0
    assert res["total_cash_balance"] == 5000.0
    assert res["total_day_pnl"] == 150.0
    assert res["total_unrealized_pnl"] == 800.0

    # Test summary
    summary = get_portfolio_summary()
    assert summary["total_liquidation_value"] == 50000.0
    assert summary["total_day_pnl"] == 150.0
    assert len(summary["accounts"]) == 1
    assert summary["accounts"][0]["account_number_masked"] == "***2222"

    # Test positions querying and filters
    all_pos = get_portfolio_positions()
    assert len(all_pos) == 2

    equities = get_portfolio_positions(asset_type="EQUITY")
    assert len(equities) == 1
    assert equities[0]["symbol"] == "AAPL"

    options = get_portfolio_positions(asset_type="OPTION")
    assert len(options) == 1
    assert options[0]["underlying_symbol"] == "NVDA"
    assert options[0]["option_strike"] == 150.0
    assert options[0]["option_type"] == "CALL"

    # Test search filter
    search_nvda = get_portfolio_positions(search="NVDA")
    assert len(search_nvda) == 1
    assert search_nvda[0]["symbol"].startswith("NVDA")
