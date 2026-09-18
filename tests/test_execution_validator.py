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
