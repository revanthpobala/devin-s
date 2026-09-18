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
    daemon = ContinuousScreenerDaemon(poll_interval=300, auto_deep_research=True, max_concurrent_slots=2)
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
         patch.object(daemon, "_is_job_active_in_db", return_value=False), \
         patch.object(daemon, "get_active_research_count", return_value=0):
        
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


def test_synthesize_datawindow_and_pipeline_sequence(tmp_path):
    from src.screener.schwab_pre_move_scan import (
        synthesize_datawindow_from_screener,
        run_autonomous_screener_pipeline,
    )
    from src.logic.data_window_filter import triage_ticker
    from src.logic.process_survivor import _deep_research_gate

    cand = {
        "symbol": "ACME",
        "side": "LONG",
        "price": 100.0,
        "ema20": 98.5,
        "sma50": 95.0,
        "sma200": 90.0,
        "stop_level": 96.0,
        "target_level": 110.0,
        "priority_score": 80.0,
        "priority_tier": "HIGH_PRIORITY",
        "weinstein_stage": 1,
        "long_rr": 2.5,
        "ext_200_pct": 11.1,
        "is_extreme_reversal": True,
        "squeeze_on": True,
        "nr7": True,
    }

    # 1. Verify synthesize_datawindow_from_screener creates a valid Data Window
    dw = synthesize_datawindow_from_screener("ACME", cand, rt_quote={"price": 100.0})
    assert dw["ticker"] == "ACME"
    assert float(dw["close"]) == 100.0
    assert dw["Action Long Code"] == "20"
    assert dw["Signal Pack"] == "5"

    # 2. Verify triage_ticker cleanly parses it with no bad_data
    triage = triage_ticker("ACME", dw, fetch_news=False)
    assert triage.get("bad_data") is not True
    assert triage.get("triage") == "PASS"
    assert triage.get("pursue") is True

    # 3. Verify gate qualification
    q_pass, send, rank_score, has_plan = _deep_research_gate(triage, earnings_gate="CAUTION")
    assert q_pass is True
    assert send is True
    assert has_plan is True

    # 4. Verify pipeline execution sequence:
    # If local research rejects the setup, Playwright scraping is NEVER called!
    with patch("subprocess.run") as mock_subproc, \
         patch("src.clients.schwab_client.get_realtime_quote", return_value={"price": 100.0}), \
         patch("src.clients.news_client.get_ticker_news", return_value={"raw_news": "ok"}):

        def mock_exists(self):
            # reports do not exist yet (no completed deep research)
            if "reports" in str(self):
                return False
            if "_chart.png" in str(self) or "_chart_zoom.png" in str(self):
                return False
            # thesis exists after local research
            if "_thesis.json" in str(self):
                return True
            return False

        with patch("pathlib.Path.exists", autospec=True, side_effect=mock_exists), \
             patch("pathlib.Path.read_text", return_value='{"send_for_deep_research": false, "triage": "CUT"}'):

            run_autonomous_screener_pipeline([cand], auto_max=1, run_deep=True, date_str="2029-01-01")

            # Check commands executed by subprocess.run
            called_cmds = [call.args[0] for call in mock_subproc.call_args_list]

            # Local research MUST be called
            assert any("run_local_research.py" in str(cmd) for cmd in called_cmds)

            # Playwright scraping MUST NOT be called because local research was not satisfied!
            assert not any("run_swing_research.py" in str(cmd) for cmd in called_cmds)
            # Deep research MUST NOT be called
            assert not any("run_deep_research.py" in str(cmd) for cmd in called_cmds)

        # Test approved setup: local research emits PASS and send_for_deep_research=True
        mock_subproc.reset_mock()
        with patch("pathlib.Path.exists", autospec=True, side_effect=mock_exists), \
             patch("pathlib.Path.read_text", return_value='{"send_for_deep_research": true, "triage": "PASS"}'):

            run_autonomous_screener_pipeline([cand], auto_max=1, run_deep=True, date_str="2029-01-01")

            called_cmds = [call.args[0] for call in mock_subproc.call_args_list]

            # 1. Local research called first
            assert any("run_local_research.py" in str(cmd) for cmd in called_cmds)
            # 2. Playwright scraping called ONLY AFTER local research is satisfied
            assert any("run_swing_research.py" in str(cmd) for cmd in called_cmds)
            # 3. Deep research dispatched in slot
            assert any("run_deep_research.py" in str(cmd) for cmd in called_cmds)
            # 4. Watch alerts synced
            assert any("run_watch_alerts.py" in str(cmd) for cmd in called_cmds)



