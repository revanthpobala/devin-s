import json
from unittest.mock import patch, MagicMock
import pytest
from src.clients.sec_edgar_client import get_cik_for_ticker, fetch_sec_filings_data, format_sec_report


def test_get_cik_for_ticker():
    # Test with mocked mapping
    mock_cik_map = {"AVGO": "0001730168", "AAPL": "0000320193"}
    with patch("src.clients.sec_edgar_client.CIK_MAP_PATH") as mock_path:
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = json.dumps(mock_cik_map)
        
        cik = get_cik_for_ticker("AVGO")
        assert cik == "0001730168"
        
        cik_aapl = get_cik_for_ticker("aapl")
        assert cik_aapl == "0000320193"


def test_format_sec_report_mock():
    sample_data = {
        "ticker": "AVGO",
        "company_name": "Broadcom Inc.",
        "cik": "0001730168",
        "sic": "Semiconductors",
        "fiscal_year_end": "1101",
        "recent_filings": [
            {
                "form": "10-Q",
                "filing_date": "2026-06-12",
                "report_date": "2026-05-03",
                "description": "Quarterly Report",
                "url": "https://www.sec.gov/filing/123",
            }
        ],
        "quarterly_financials": [
            {
                "end_date": "2026-05-03",
                "form": "10-Q",
                "revenue": 12490000000,
                "operating_income": 5320000000,
                "net_income": 2120000000,
                "cash": 9810000000,
                "long_term_debt": 71500000000,
            }
        ]
    }
    with patch("src.clients.sec_edgar_client.fetch_sec_filings_data", return_value=sample_data):
        report = format_sec_report("AVGO")
        assert "Broadcom Inc." in report
        assert "0001730168" in report
        assert "$12.49B" in report
        assert "$71.50B" in report
        assert "10-Q" in report
