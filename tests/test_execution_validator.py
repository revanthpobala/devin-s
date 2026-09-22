import pandas as pd
from unittest.mock import patch

from src.tracking.execution_validator import evaluate_setup_lifecycle


def test_dip_buy_fill_and_target_hit():
    """Verify that a dip buy whose historical bar touched entry zone and reached T1 is TARGET_HIT."""
    mock_df = pd.DataFrame(
        {
            "Open": [708.0, 708.0, 716.0],
            "High": [709.5, 711.8, 716.94],
            "Low": [703.64, 700.0, 713.32],
            "Close": [704.54, 704.72, 716.87],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="QQQ",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=703.50,
            entry_high=705.40,
            stop_loss=698.47,
            target_1=716.72,
            target_2=721.89,
            live_price=716.87,
            current_status="STALKING",
        )

        assert res["was_filled"] is True
        assert res["status"] == "TARGET_HIT"
        assert res["hit_t1"] is True
        assert res["hit_stop"] is False
        assert res["unrealized_pnl_pct"] > 0


def test_true_missed_runaway():
    """Verify that an order whose low never touched the entry zone is correctly marked MISSED_RUNAWAY."""
    mock_df = pd.DataFrame(
        {
            "Open": [98.0, 101.0, 105.0],
            "High": [100.5, 104.4, 111.14],
            "Low": [96.52, 99.29, 104.70],
            "Close": [97.14, 101.05, 110.98],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="INTC",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=92.50,
            entry_high=94.00,
            stop_loss=85.78,
            target_1=103.41,
            target_2=106.69,
            live_price=110.98,
            current_status="STALKING",
        )

        assert res["was_filled"] is False
        assert res["status"] == "MISSED_RUNAWAY"
        assert res["hit_t1"] is True


def test_active_in_trade_preservation():
    """Verify that an order that filled and is holding above stop stays IN_TRADE."""
    mock_df = pd.DataFrame(
        {
            "Open": [252.0, 248.0, 251.0],
            "High": [253.2, 249.2, 252.7],
            "Low": [247.2, 244.3, 249.2],
            "Close": [248.4, 245.9, 251.2],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="AMZN",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=245.80,
            entry_high=250.50,
            stop_loss=243.50,
            target_1=258.31,
            target_2=263.42,
            live_price=251.25,
            current_status="STALKING",
        )

        assert res["was_filled"] is True
        assert res["status"] == "IN_TRADE"
        assert res["hit_t1"] is False
        assert res["hit_stop"] is False
        assert res["unrealized_pnl_pct"] > 0


def test_target_reached_before_fill_yields_missed_runaway():
    """Verify that if target is reached on an earlier bar than entry fill, order is MISSED_RUNAWAY."""
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 93.0],
            "High": [106.0, 94.0],   # Day 1 touches Target 1 (105.0)
            "Low": [98.0, 91.0],     # Day 1 Low never touches entry limit (92.0). Day 2 touches 91.0.
            "Close": [104.0, 92.5],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="XYZ",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=90.0,
            entry_high=92.0,
            stop_loss=85.0,
            target_1=105.0,
            target_2=110.0,
            live_price=92.5,
            current_status="STALKING",
        )

        # In old code: min_low was 91.0 (filled), hit_t1 was True -> erroneously reported TARGET_HIT.
        # In chronological code: Target reached on 2026-09-15 before fill -> correctly MISSED_RUNAWAY.
        assert res["status"] == "MISSED_RUNAWAY"
        assert res["was_filled"] is False
        assert res["hit_t1"] is True


def test_stop_breached_before_target_yields_stop_breached():
    """Verify that if stop is hit on Day 2 and target on Day 3, the stopped trade is not rewritten as TARGET_HIT."""
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 95.0, 90.0],
            "High": [101.0, 96.0, 115.0],   # Day 3 reaches target 110.0
            "Low": [91.0, 84.0, 89.0],      # Day 1 fills at 92.0. Day 2 breaches stop at 85.0.
            "Close": [95.0, 88.0, 114.0],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="XYZ",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=90.0,
            entry_high=92.0,
            stop_loss=85.0,
            target_1=110.0,
            target_2=115.0,
            live_price=114.0,
            current_status="STALKING",
        )

        assert res["status"] == "STOP_BREACHED"
        assert res["was_filled"] is True
        assert res["hit_stop"] is True
        assert res["exit_date"] == "2026-09-16"
        assert res["exit_price"] == 85.0


def test_same_bar_stop_target_ambiguity_resolves_to_stop_first():
    """Verify conservative stop-first resolution when both stop and target are breached in the same bar."""
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 95.0],
            "High": [101.0, 115.0],   # Day 2 reaches target 110.0
            "Low": [91.0, 80.0],      # Day 1 fills at 92.0. Day 2 touches 80.0 (below stop 85.0)
            "Close": [95.0, 105.0],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="XYZ",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=90.0,
            entry_high=92.0,
            stop_loss=85.0,
            target_1=110.0,
            target_2=115.0,
            live_price=105.0,
            current_status="STALKING",
        )

        assert res["status"] == "STOP_BREACHED"
        assert res["hit_stop"] is True
        assert res["exit_price"] == 85.0


def test_gap_through_stop_prices_at_gap_open():
    """Verify that a gap down through stop prices execution at the opening price, not the stop limit."""
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 82.0],   # Day 2 gaps down to 82.0 (stop is 85.0)
            "High": [101.0, 84.0],
            "Low": [91.0, 80.0],
            "Close": [95.0, 81.0],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="XYZ",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=90.0,
            entry_high=92.0,
            stop_loss=85.0,
            target_1=110.0,
            target_2=115.0,
            live_price=81.0,
            current_status="STALKING",
        )

        assert res["status"] == "STOP_BREACHED"
        assert res["exit_price"] == 82.0  # Gap open price, not 85.0


def test_cache_earlier_start_date_coverage():
    """Verify that get_bars_since_date does not use cached bars that start after the requested start_date."""
    import time
    from src.tracking import execution_validator

    # Populate cache with data starting from 2026-09-10
    cached_df = pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [99.0], "Close": [104.0]},
        index=pd.to_datetime(["2026-09-10"]),
    )
    execution_validator._BARS_CACHE["TEST_SYM"] = (time.time(), cached_df)

    with patch("yfinance.download") as mock_yf:
        # Request with earlier start date: 2026-08-01
        fresh_df = pd.DataFrame(
            {"Open": [90.0, 100.0], "High": [95.0, 105.0], "Low": [89.0, 99.0], "Close": [94.0, 104.0]},
            index=pd.to_datetime(["2026-08-01", "2026-09-10"]),
        )
        mock_yf.return_value = fresh_df

        bars = execution_validator.get_bars_since_date("TEST_SYM", "2026-08-01")
        assert mock_yf.called
        assert len(bars) == 2


def test_rsi2_opening_ceiling_gap_skip():
    """Verify RSI2 next-open gap skip when market opens above opening ceiling."""
    mock_df = pd.DataFrame(
        {
            "Open": [155.0],   # Opened above ceiling of 150.0
            "High": [160.0],
            "Low": [154.0],
            "Close": [159.0],
        },
        index=pd.to_datetime(["2026-09-15"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="RSI2_TEST",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="RSI2",
            opening_ceiling=150.0,
            stop_loss=140.0,
            target_1=165.0,
            live_price=159.0,
            current_status="STALKING",
        )

        assert res["was_filled"] is False
        assert res["status"] == "EXPIRED_CEILING"
        assert res["is_terminal"] is True


def test_rsi2_does_not_fill_on_second_day_after_ceiling_gap():
    """Verify that an RSI2 setup with day 1 open 155 (above ceiling 150) expires and does NOT fill on day 2 open 149."""
    mock_df = pd.DataFrame(
        {
            "Open": [155.0, 149.0],
            "High": [160.0, 153.0],
            "Low": [154.0, 147.0],
            "Close": [159.0, 151.0],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="RSI2_TEST",
            setup_date="2026-09-14",
            side="LONG",
            entry_type="RSI2",
            opening_ceiling=150.0,
            stop_loss=140.0,
            target_1=165.0,
            live_price=151.0,
            current_status="STALKING",
        )

        assert res["was_filled"] is False
        assert res["status"] == "EXPIRED_CEILING"
        assert res["is_terminal"] is True


def test_rsi2_ema5_recovery_exit():
    """Verify that an active RSI2 position closing above EMA5 exits on the next open."""
    mock_df = pd.DataFrame(
        {
            "Open": [144.0, 148.0, 156.0],
            "High": [146.0, 156.0, 158.0],
            "Low": [142.0, 146.0, 154.0],
            "Close": [145.0, 155.0, 157.0],
        },
        index=pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="RSI2_TEST",
            setup_date="2026-09-14",
            side="LONG",
            entry_type="RSI2",
            opening_ceiling=150.0,
            stop_loss=140.0,
            target_1=165.0,
            live_price=157.0,
            current_status="STALKING",
        )

        assert res["was_filled"] is True
        assert res["fill_price"] == 148.0
        assert res["status"] == "RECOVERY_EXIT"
        assert res["hit_target_level"] == "RECOVERY"
        assert res["hit_t1"] is False
        assert res["hit_recovery"] is True
        assert res["exit_price"] == 156.0
        assert res["exit_date"] == "2026-09-16"


def test_time_exit_after_max_holding_bars():
    """Verify time exit triggers after max holding bars elapsed."""
    # 3 bars where price holds between stop and target
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 100.5, 101.0],
            "High": [102.0, 102.0, 102.0],
            "Low": [98.0, 99.0, 99.5],
            "Close": [101.0, 101.0, 101.5],
        },
        index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17"]),
    )

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=mock_df):
        res = evaluate_setup_lifecycle(
            ticker="TIME_TEST",
            setup_date="2026-09-15",
            side="LONG",
            entry_type="LIMIT",
            entry_low=98.0,
            entry_high=100.0,
            stop_loss=95.0,
            target_1=110.0,
            live_price=101.5,
            current_status="STALKING",
            max_holding_bars=3,
        )

        assert res["was_filled"] is True
        assert res["status"] == "TIME_EXIT"
        assert res["exit_date"] == "2026-09-17"
        assert res["exit_price"] == 101.5

