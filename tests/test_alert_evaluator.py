import json
import sqlite3
from unittest.mock import patch, MagicMock
import pytest

from src.tracking import alert_db
from src.tracking.alert_evaluator import (
    cap_conviction,
    evaluate_alert_payload,
    build_live_market_context,
)


@pytest.fixture
def temp_db(tmp_path):
    test_db_path = tmp_path / "test_trading_alerts.db"
    with patch.object(alert_db, "DB_PATH", test_db_path):
        alert_db.init_alert_db()
        yield test_db_path


def test_build_live_market_context_mocked():
    with patch("src.tracking.alert_evaluator.TastytradeClient") as mock_tt, \
         patch("src.tracking.alert_evaluator.search_web") as mock_search, \
         patch("src.plugins.order_flow_plugin.read_tape", return_value={"verdict": "bullish"}), \
         patch("src.data.darkpool.fetch_darkpool_data", return_value={
             "status": "ok", "dp_trades_count": 10, "window_minutes": 7.5, "dp_share_of_tape_pct": 50.0,
             "dp_buy_pct": 55.0, "dp_classified_pct": 80.0, "direction_method": "quote",
             "major_shelves": [{"price": 150.0}]}), \
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
        assert "top volume cluster 150.0" in ctx["offexchange"]


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
         patch("src.tracking.alert_evaluator.build_live_market_context", return_value={"volatility": {}, "news": [], "options_snippet": "", "expected_earnings": None}), \
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


# =====================================================================================
# Conviction caps: the badge may never claim an edge the verdict's own flags deny.
# Regression for 2026-10-12, when the model printed 6/10 for four no-edge drift rows.
# =====================================================================================

def test_no_edge_flag_caps_conviction_at_4():
    assert cap_conviction(6, "WATCH", ["news_confirmed", "no_edge"], entry_mode="NONE", has_plan=False) == 4


def test_earnings_imminent_hard_gate_caps_conviction():
    assert cap_conviction(6, "WATCH", ["earnings_imminent", "cheap_iv_debit"]) == 4


def test_news_contradicts_and_stage_4_cap_conviction():
    assert cap_conviction(8, "WATCH", ["news_contradicts"]) == 4
    assert cap_conviction(9, "WATCH", ["stage_4_markdown"]) == 4


def test_cut_verdict_caps_conviction():
    assert cap_conviction(7, "CUT", []) == 4


def test_watch_with_nothing_to_stalk_caps_conviction():
    """No entry mode and no stop/target levels is not the 6-7 'measurable edge' band."""
    assert cap_conviction(6, "WATCH", [], entry_mode="NONE", has_plan=False) == 4


def test_measured_watch_keeps_its_conviction():
    """Entry mode + levels + no defect flags: an actionable stalk stays a 6."""
    assert cap_conviction(6, "WATCH", [], entry_mode="MEAN_REVERSION_LONG", has_plan=True) == 6


def test_high_conviction_survives_when_the_setup_is_real():
    assert cap_conviction(9, "WATCH", ["news_confirmed"], entry_mode="MOMENTUM_LONG", has_plan=True) == 9


def test_cap_never_inflates():
    """A low number on a flagged row is already honest; the cap is a ceiling only."""
    assert cap_conviction(2, "WATCH", ["no_edge"], entry_mode="NONE", has_plan=False) == 2


def test_garbage_conviction_falls_back_to_default():
    assert cap_conviction("high", "WATCH", []) == 4
    assert cap_conviction(None, "WATCH", []) == 4
    assert cap_conviction(99, "WATCH", [], entry_mode="TREND_LONG", has_plan=True) == 10


# =====================================================================================
# The 0DTE card playbook: conviction must land on the same 1-10 band the swing triage uses.
# The card's own score stays 0-100 -- it is the card's scale, printed as "Card: A(63)".
# =====================================================================================

def _0dte_playbook(verdict: str, score: str):
    from src.tracking.alert_evaluator import synthesize_deterministic_triage

    return synthesize_deterministic_triage(
        "AAPL", "ALERT_CALLS", "Intraday", "10:07 AM ET", 100.0, 15.0,
        {"verdict": verdict, "grade": "A", "score": score, "why_now": "breakout"},
        {},
    )[1]


def test_0dte_conviction_uses_the_110_band():
    pb = _0dte_playbook("BUY CALLS", "63")
    # Bands are integers: cap_conviction truncates toward the lower band, never rounds up.
    assert "Conviction: 6/10 (A)" in pb
    assert "Conviction: 63/100" not in pb
    # The card score itself stays on its own scale, labelled as the card's.
    assert "Card: BUY CALLS A(63)" in pb


def test_0dte_stand_aside_carries_no_deployment_conviction():
    pb = _0dte_playbook("STAND ASIDE", "63")
    assert "Conviction: 4/10" in pb


def test_0dte_unparseable_card_score_is_unmeasured_not_75():
    """No fabricated default: an absent "75" used to print a made-up 7.5/10 TAKE."""
    pb = _0dte_playbook("BUY CALLS", "")
    assert "Conviction: N/A (unmeasured card score)" in pb
    assert "7.5/10" not in pb
    assert "/100" not in pb


def test_0dte_missing_card_score_is_unmeasured_not_75():
    from src.tracking.alert_evaluator import synthesize_deterministic_triage

    pb = synthesize_deterministic_triage(
        "AAPL", "ALERT_CALLS", "Intraday", "10:07 AM ET", 100.0, 15.0,
        {"verdict": "BUY CALLS", "grade": "A"},
        {},
    )[1]
    assert "Conviction: N/A (unmeasured card score)" in pb
    assert "7.5/10" not in pb
