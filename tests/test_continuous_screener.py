"""
tests/test_continuous_screener.py

Unit tests for the Continuous Schwab 1000 & Tastytrade Screener Daemon.
Verifies background lifecycle, Tastytrade volatility enrichment, 24/7 cloud price alert registration,
and API status endpoints.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.screener.continuous_screener_daemon import (
    ContinuousScreenerDaemon,
    get_continuous_screener_status,
    start_continuous_screener_daemon,
    stop_continuous_screener_daemon,
    trigger_continuous_scan_now,
)


def test_continuous_screener_initialization_and_status():
    daemon = ContinuousScreenerDaemon(poll_interval=300, top_n=5, auto_alerts=True)
    assert daemon.poll_interval == 300
    assert daemon.top_n == 5
    assert daemon.auto_alerts is True
    assert daemon.running is True

    status = daemon.get_status()
    assert status["running"] is True
    assert status["is_scanning"] is False
    assert status["poll_interval"] == 300
    assert status["scan_count"] == 0
    assert "market_hours" in status


def test_enrich_with_tastytrade_metrics_and_alerts():
    daemon = ContinuousScreenerDaemon(poll_interval=300, top_n=5, auto_alerts=True)

    long_picks = [
        {
            "symbol": "LYB",
            "side": "LONG",
            "price": 62.5,
            "support_level": 61.5,
            "target_level": 69.0,
            "priority_score": 75.0,
            "priority_tier": "HIGH_PRIORITY",
        }
    ]
    short_picks = [
        {
            "symbol": "XYZ",
            "side": "SHORT",
            "price": 100.0,
            "ceiling_level": 105.0,
            "target_level": 90.0,
            "priority_score": 70.0,
            "priority_tier": "HIGH_PRIORITY",
        }
    ]

    mock_metrics = [
        {
            "symbol": "LYB",
            "tos-implied-volatility-index-rank": "0.15",
            "implied-volatility-percentile": "0.10",
            "historical-volatility-30-day": "25.5",
            "iv-hv-30-day-difference": "5.0",
            "liquidity-rating": 4,
            "borrow-rate": "0.0",
            "lendability": "Easy To Borrow",
            "beta": "0.85",
        },
        {
            "symbol": "XYZ",
            "tos-implied-volatility-index-rank": "0.80",
            "implied-volatility-percentile": "0.75",
            "historical-volatility-30-day": "40.0",
            "iv-hv-30-day-difference": "-2.0",
            "liquidity-rating": 3,
            "borrow-rate": "1.5",
            "lendability": "Locate Required",
            "beta": "1.2",
        },
    ]

    with patch("src.screener.continuous_screener_daemon.TastytradeClient") as mock_tt_cls:
        mock_tt = MagicMock()
        mock_tt.get_auth_headers.return_value = {"Authorization": "Bearer fake"}
        mock_tt.get_market_metrics.return_value = mock_metrics
        mock_tt.create_quote_alert.return_value = {"alert-external-id": "test-alert-1"}
        mock_tt_cls.return_value = mock_tt

        alerts_created = daemon.enrich_with_tastytrade(long_picks, short_picks)

        # 2 alerts per pick (support/ceiling + target) = 4 alerts
        assert alerts_created == 4
        assert mock_tt.create_quote_alert.call_count == 4

        # Verify LYB enrichment
        lyb_tt = long_picks[0].get("tastytrade")
        assert lyb_tt is not None
        assert lyb_tt["connected"] is True
        assert lyb_tt["iv_rank"] == 15.0
        assert lyb_tt["iv_percentile"] == 10.0
        assert lyb_tt["hv30"] == 25.5
        assert lyb_tt["liquidity_rating"] == 4
        assert lyb_tt["lendability"] == "Easy To Borrow"
        assert long_picks[0]["tastytrade_alert_active"] is True

        # Verify XYZ enrichment
        xyz_tt = short_picks[0].get("tastytrade")
        assert xyz_tt is not None
        assert xyz_tt["iv_rank"] == 80.0
        assert xyz_tt["borrow_rate"] == 1.5
        assert short_picks[0]["tastytrade_alert_active"] is True


def test_continuous_screener_run_scan_cycle():
    daemon = ContinuousScreenerDaemon(poll_interval=300, top_n=2, auto_alerts=False)

    fake_long = [
        {"symbol": "AAPL", "price": 220.0, "support_level": 218.0, "target_level": 230.0, "priority_score": 60.0}
    ]
    fake_short = []

    with patch("src.screener.schwab_pre_move_scan.check_market_tide", return_value={"bullish": True}), \
         patch("src.screener.schwab_pre_move_scan.run_schwab_pre_move_scan", return_value={"long": fake_long, "short": fake_short}), \
         patch("src.screener.schwab_pre_move_scan.save_survivors_manifest") as mock_save_long, \
         patch("src.screener.schwab_pre_move_scan.save_short_manifest") as mock_save_short, \
         patch.object(daemon, "enrich_with_tastytrade", return_value=0):

        res = daemon.run_scan_cycle()

        assert res["success"] is True
        assert res["long_count"] == 1
        assert res["short_count"] == 0
        assert mock_save_long.called
        assert not mock_save_short.called

        status = daemon.get_status()
        assert status["scan_count"] == 1
        assert status["long_count"] == 1
        assert status["short_count"] == 0
        assert status["last_scan_time"] is not None


def test_continuous_screener_module_lifecycle():
    stop_continuous_screener_daemon()

    with patch("src.screener.continuous_screener_daemon.ContinuousScreenerDaemon.run_scan_cycle") as mock_cycle:
        daemon = start_continuous_screener_daemon(poll_interval=600)
        assert daemon is not None
        assert daemon.is_alive()

        status = get_continuous_screener_status()
        assert status["running"] is True

        triggered = trigger_continuous_scan_now()
        assert triggered is True

        stop_continuous_screener_daemon()
        assert daemon.running is False


def test_evaluate_and_dispatch_deep_research():
    daemon = ContinuousScreenerDaemon(poll_interval=300, auto_deep_research=True)
    daemon.max_auto_deep_per_day = 2
    daemon.min_conviction_score = 70.0

    candidates = [
        # Qualified candidate 1
        {"symbol": "LYB", "price": 62.5, "priority_score": 75.0, "priority_tier": "HIGH_PRIORITY", "long_rr": 3.5},
        # Disqualified (low score)
        {"symbol": "LOW", "price": 50.0, "priority_score": 45.0, "priority_tier": "MONITOR", "long_rr": 1.0},
        # Qualified candidate 2
        {"symbol": "MBGL", "price": 30.0, "priority_score": 72.0, "priority_tier": "HIGH_PRIORITY", "long_rr": 2.0},
        # Qualified candidate 3 (exceeds cap of 2)
        {"symbol": "EXCEED", "price": 80.0, "priority_score": 71.0, "priority_tier": "HIGH_PRIORITY", "long_rr": 1.5},
    ]

    with patch.object(daemon, "dispatch_candidate_research", return_value=True) as mock_dispatch, \
         patch.object(daemon, "_is_job_active_in_db", return_value=False):
        
        dispatched = daemon.evaluate_and_dispatch_deep_research(candidates, "2029-01-01")

        # Must dispatch top 2 (LYB, MBGL) up to cap of 2
        assert len(dispatched) == 2
        assert "LYB" in dispatched
        assert "MBGL" in dispatched
        assert "EXCEED" not in dispatched
        assert mock_dispatch.call_count == 2

        # Second call on same day should dispatch 0 because cap (2) is reached
        second_run = daemon.evaluate_and_dispatch_deep_research(candidates, "2029-01-01")
        assert len(second_run) == 0

        # Verify deduplication if candidate already in set
        status = daemon.get_status()
        assert status["auto_deep_count_today"] == 2
        assert "LYB" in status["auto_deep_dispatched_today"]
