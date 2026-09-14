import json
import sqlite3
from unittest.mock import patch, MagicMock
import pytest

from src.tracking import alert_db
from src.tracking.alert_evaluator import evaluate_alert_payload, build_live_market_context


@pytest.fixture
def temp_db(tmp_path):
    test_db_path = tmp_path / "test_trading_alerts.db"
    with patch.object(alert_db, "DB_PATH", test_db_path):
        alert_db.init_alert_db()
        yield test_db_path


def test_build_live_market_context_mocked():
    with patch("src.tracking.alert_evaluator.TastytradeClient") as mock_tt, \
         patch("src.tracking.alert_evaluator.search_web") as mock_search, \
         patch("src.tracking.alert_evaluator.fetch_options_chain_tool") as mock_options:
        
        mock_tt.return_value.get_market_metrics.return_value = [{
            "implied-volatility-index-rank": "0.45",
            "historical-volatility-30-day": "35.2",
            "earnings": {"expected-report-date": "2026-11-15"}
        }]
        mock_search.return_value = [{"title": "Test Title", "body": "Test Body"}]
        mock_options.return_value = "| Expiry | Strike |\n| 2026-10-16 | 150 |"

        ctx = build_live_market_context("AAPL")
        assert ctx["volatility"]["iv_rank"] == 45.0
        assert ctx["expected_earnings"] == "2026-11-15"
        assert len(ctx["news"]) == 1
        assert "150" in ctx["options_snippet"]


def test_evaluate_alert_payload_daily_mocked(temp_db):
    alert = {
        "message_id": "test-daily-eval@tradingview.com",
        "timestamp": "2026-09-10 10:00:00",
        "symbol": "TPL",
        "action": "NEUTRAL",
        "strategy": "Daily",
        "alert_price": 365.97,
        "setup": "Stagnation (Research)",
        "raw_payload": json.dumps({
            "stage": 5,
            "buy": 73.7,
            "sell": 44.6,
            "proxy_rr": 9.8,
            "atrs_up": 0.52
        })
    }
    alert_db.record_alert(alert)

    mock_llm_resp = json.dumps({
        "ticker": "TPL",
        "dominant_side": "long",
        "entry_mode": "TREND_LONG",
        "triage": "WATCH",
        "conviction": 6,
        "ponytail_critique": "Stage 5 is a trap. Do not chase.",
        "tactical_stop": 340.0,
        "target_1": 400.0,
        "recommended_vehicle": "BULL_PUT_SPREAD"
    })

    with patch.object(alert_db, "DB_PATH", temp_db), \
         patch("src.tracking.alert_evaluator.query_local_llm", return_value=mock_llm_resp):
        
        res = evaluate_alert_payload(alert, use_tools=False)
        assert res["triage"] == "WATCH"
        assert res["conviction"] == 6
        assert "WATCH (6/10)" in res["llm_decision"]
        assert "Stage 5 is a trap" in res["llm_playbook"]

        # Check DB update
        alerts = alert_db.get_alerts_for_date("2026-09-10")
        assert len(alerts) == 1
        assert "WATCH (6/10)" in alerts[0]["llm_decision"]
        assert alerts[0]["status"] == "PROCESSED"
