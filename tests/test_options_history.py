import os
import tempfile
import sqlite3
import pytest
from datetime import datetime, timedelta

from src.tracking.options_history_manager import OptionsHistoryManager

@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mgr = OptionsHistoryManager(db_path=db_path)
    yield mgr
    try:
        os.remove(db_path)
    except OSError:
        pass

def test_record_and_get_history(temp_db):
    mgr = temp_db
    sample_data = {
        "ticker": "AAPL",
        "date": "2026-10-15",
        "underlying_price": 235.50,
        "sentiment": "BULLISH_SWEEPS",
        "sentiment_label": "Bullish Sweeps (Calls Dominant)",
        "total_anomalies_count": 2,
        "call_sweeps_count": 2,
        "put_sweeps_count": 0,
        "total_call_volume": 4500,
        "total_put_volume": 500,
        "total_call_premium": 1250000.0,
        "total_put_premium": 50000.0,
        "put_call_volume_ratio": 0.11,
        "put_call_premium_ratio": 0.04,
        "iv_rank": 32.5,
        "iv_percentile": 40.0,
        "hv_30": 24.1,
        "iv_30": 28.0,
        "iv_hv_spread": 3.9,
        "beta": 1.12,
        "data_source": "Tastytrade + Schwab",
        "anomalies": [
            {
                "type": "CALL",
                "strike": 240.0,
                "expiry": "2026-11-20",
                "dte": 36,
                "volume": 3000,
                "open_interest": 800,
                "vol_to_oi": 3.75,
                "delta": 0.45,
                "mid": 4.10,
                "notional_premium": 1230000.0,
                "distance_from_spot_pct": 1.9,
            },
            {
                "type": "CALL",
                "strike": 245.0,
                "expiry": "2026-11-20",
                "dte": 36,
                "volume": 1500,
                "open_interest": 500,
                "vol_to_oi": 3.0,
                "delta": 0.35,
                "mid": 2.50,
                "notional_premium": 375000.0,
                "distance_from_spot_pct": 4.0,
            }
        ]
    }

    # Record snapshot
    res = mgr.record_options_snapshot(sample_data)
    assert res is True

    # Retrieve history
    history = mgr.get_options_history("AAPL", days=30)
    assert len(history) == 1
    rec = history[0]
    assert rec["ticker"] == "AAPL"
    assert rec["date"] == "2026-10-15"
    assert rec["spot_price"] == 235.50
    assert rec["sentiment"] == "BULLISH_SWEEPS"
    assert rec["iv_rank"] == 32.5
    assert rec["call_sweeps"] == 2
    assert len(rec["anomalies"]) == 2

    # Check sweeps detail table
    sweeps = mgr.get_sweeps_history("AAPL", date="2026-10-15")
    assert len(sweeps) == 2
    assert sweeps[0]["strike"] == 240.0
    assert sweeps[0]["vol_to_oi"] == 3.75

def test_upsert_same_day(temp_db):
    mgr = temp_db
    data1 = {
        "ticker": "NVDA",
        "date": "2026-10-15",
        "underlying_price": 130.0,
        "sentiment": "NEUTRAL",
        "sentiment_label": "Balanced Flow",
        "total_anomalies_count": 0,
        "anomalies": []
    }
    mgr.record_options_snapshot(data1)

    # Later on same day, update with new sweeps
    data2 = {
        "ticker": "NVDA",
        "date": "2026-10-15",
        "underlying_price": 132.5,
        "sentiment": "BULLISH_SWEEPS",
        "sentiment_label": "Bullish Sweeps",
        "total_anomalies_count": 1,
        "anomalies": [
            {
                "type": "CALL",
                "strike": 140.0,
                "expiry": "2026-11-20",
                "dte": 36,
                "volume": 2000,
                "open_interest": 400,
                "vol_to_oi": 5.0,
                "mid": 3.0,
                "notional_premium": 600000.0,
            }
        ]
    }
    mgr.record_options_snapshot(data2)

    history = mgr.get_options_history("NVDA", days=30)
    assert len(history) == 1
    assert history[0]["spot_price"] == 132.5
    assert history[0]["sentiment"] == "BULLISH_SWEEPS"
    assert history[0]["total_sweeps"] == 1

def test_30_day_rolling_pruning(temp_db):
    mgr = temp_db
    today = datetime.now().date()
    
    # 1. Test auto-pruning on record: insert 45 days
    for i in range(45):
        d = (today - timedelta(days=i)).isoformat()
        sample = {
            "ticker": "SPY",
            "date": d,
            "underlying_price": 500.0 + i,
            "sentiment": "NEUTRAL",
            "sentiment_label": "Neutral",
            "total_anomalies_count": 0,
            "anomalies": []
        }
        mgr.record_options_snapshot(sample)

    # After inserting 45 days, rolling 30-day retention automatically kept only the last 30 days
    retained_rows = mgr.get_options_history("SPY", days=60)
    assert len(retained_rows) <= 31
    assert len(retained_rows) >= 30

    # 2. Test explicit prune_options_history call
    # Directly insert old rows into database
    with sqlite3.connect(str(mgr.db_path)) as conn:
        for i in range(50, 55):
            d = (today - timedelta(days=i)).isoformat()
            conn.execute(
                "INSERT INTO options_flow_daily (ticker, date, timestamp, spot_price) VALUES (?, ?, ?, ?)",
                ("SPY", d, d + "T00:00:00", 400.0),
            )

    # Verify old rows exist before explicit prune
    old_rows = mgr.get_options_history("SPY", days=60)
    assert len(old_rows) > len(retained_rows)

    # Run explicit 30-day pruning
    pruned = mgr.prune_options_history(days=30)
    assert pruned >= 5

    # Verify pruned back to last 30 days
    final_rows = mgr.get_options_history("SPY", days=60)
    assert len(final_rows) <= 31


def test_robustness_with_none_and_malformed_values(temp_db):
    mgr = temp_db
    sample_with_nones = {
        "ticker": "TSLA",
        "date": "2026-10-15",
        "underlying_price": None,
        "sentiment": "NEUTRAL",
        "total_anomalies_count": None,
        "call_sweeps_count": None,
        "total_call_premium": None,
        "iv_rank": None,
        "anomalies": [
            {
                "type": "CALL",
                "strike": None,
                "expiry": "2026-11-20",
                "dte": None,
                "volume": None,
                "open_interest": None,
                "vol_to_oi": None,
                "delta": None,
                "mid": None,
                "notional_premium": None,
                "distance_from_spot_pct": None,
            }
        ]
    }
    # Should safely record without TypeError or ValueError
    ok = mgr.record_options_snapshot(sample_with_nones)
    assert ok is True

    history = mgr.get_options_history("TSLA", days=7)
    assert len(history) == 1
    assert history[0]["spot_price"] is None

    sweeps = mgr.get_sweeps_history("TSLA", days=7)
    assert len(sweeps) == 1
    assert sweeps[0]["strike"] == 0.0
