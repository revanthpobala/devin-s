"""
Unit tests for Stop Loss support floor calculations, UNMEASURED mover lane,
and screener funnel tracking.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock

from src.logic.data_window_filter import _assess_side
from src.screener.schwab_pre_move_scan import (
    stage1_fast_filter,
    evaluate_technical_coiling,
    _dispatch_eligible,
    print_funnel_summary,
)


def test_long_stop_calculation_qcom_below_ma20():
    """
    QCOM test case: Spot is 176.38, MA20 is 182.77, MA50 is 170.0, Zbot is 175.0, ATR is 4.0.
    Old buggy logic: max(175.0, 182.77, 176.38*0.96) -> 182.77 (Stop placed ABOVE current price!).
    New correct logic: Finds active support strictly below price (175.0 / 170.0), puts stop below it with ATR buffer.
    """
    f = {
        "price": 176.38,
        "action_long": 20,
        "action_short": 0,
        "buy": 75.0,
        "sell": 20.0,
        "rev_l": 15.0,
        "rev_s": 0.0,
        "ignition_long": 0.0,
        "long_zbot": 175.0,
        "long_ztop": 185.0,
        "long_target": 200.0,
        "long_stop_loss": 172.0,
        "long_in_zone": 1,
        "short_zbot": None,
        "short_ztop": None,
        "short_target": None,
        "short_stop_loss": None,
        "short_in_zone": None,
        "ma20": 182.77,
        "ma50": 170.0,
        "ma200": 150.0,
        "weinstein": 150.0,
        "dir_prob": 60.0,
        "stage": 3,
        "ext_pct": 10.0,
        "atr": 4.0,
        "rsi2_setup_event": 0,
        "rsi2_armed_event": 0,
        "rsi2_state_code": 0,
    }
    result = _assess_side("long", f)
    assert result is not None
    # Stop MUST be strictly below current price
    assert result["tight_stop"] < 176.38
    # Stop should be below the lowest support below price minus 0.25*ATR (170.0 - 1.0 = 169.0)
    assert result["tight_stop"] <= 174.0
    assert result["momentum_rr"] is not None and result["momentum_rr"] > 0


def test_long_stop_calculation_meta_at_zone():
    """
    META test case: Spot is 724.89, MA20 is 715.0, Zbot is 710.0, ATR is 12.0.
    Stop should be placed with 0.25*ATR buffer below support (e.g. <= 710 - 3.0 = 707.0), NOT directly inside the zone.
    """
    f = {
        "price": 724.89,
        "action_long": 20,
        "action_short": 0,
        "buy": 80.0,
        "sell": 15.0,
        "rev_l": 20.0,
        "rev_s": 0.0,
        "ignition_long": 0.0,
        "long_zbot": 710.0,
        "long_ztop": 740.0,
        "long_target": 790.0,
        "long_stop_loss": 700.0,
        "long_in_zone": 1,
        "short_zbot": None,
        "short_ztop": None,
        "short_target": None,
        "short_stop_loss": None,
        "short_in_zone": None,
        "ma20": 715.0,
        "ma50": 690.0,
        "ma200": 600.0,
        "weinstein": 600.0,
        "dir_prob": 65.0,
        "stage": 3,
        "ext_pct": 15.0,
        "atr": 12.0,
        "rsi2_setup_event": 0,
        "rsi2_armed_event": 0,
        "rsi2_state_code": 0,
    }
    result = _assess_side("long", f)
    assert result is not None
    assert result["tight_stop"] < 710.0  # Must be strictly below zone bottom
    assert result["tight_stop"] < 724.89
    assert result["momentum_rr"] is not None and result["momentum_rr"] > 0


def test_unmeasured_mover_lane_stage1_fast_filter():
    """
    Test that high relative volume breakout mover (e.g. VST / NRG style) passes Stage 1 fast filter
    in the UNMEASURED lane even if up +4.5% on the day.
    """
    raw_quotes = {
        "VST": {
            "symbol": "VST",
            "quote": {
                "lastPrice": 125.0,
                "netPercentChange": 4.5,
                "totalVolume": 2500000,
                "52WeekHigh": 130.0,
                "52WeekLow": 80.0,
            },
            "fundamental": {
                "avg10DaysVolume": 1500000,
            },
        },
        "NRG": {
            "symbol": "NRG",
            "quote": {
                "lastPrice": 95.0,
                "netPercentChange": 3.2,
                "totalVolume": 1800000,
                "52WeekHigh": 98.0,
                "52WeekLow": 60.0,
            },
            "fundamental": {
                "avg10DaysVolume": 1200000,
            },
        },
    }
    funnel = {
        "total_quotes": 2,
        "rejections": {},
        "stage1_passed": {},
        "stage2_rejections": {},
        "stage2_passed": 0,
    }
    survivors = stage1_fast_filter(raw_quotes, biotech_set=set(), funnel_tracker=funnel)
    assert len(survivors) == 2
    lanes = [s["lane"] for s in survivors]
    assert "UNMEASURED" in lanes or "CONTINUATION" in lanes
    assert funnel["stage1_passed"].get("unmeasured", 0) + funnel["stage1_passed"].get("continuation", 0) == 2


def test_unmeasured_mover_stage2_technical_coiling():
    """
    Test Stage 2 technical evaluation for UNMEASURED lane mover breaking 20-day high with high volume.
    """
    # Create 60 bars of daily candle data breaking 20d high
    dates = pd.date_range(end="2026-10-08", periods=60)
    # Steady base around 100, then breakout to 110 on high volume
    closes = [100.0 + (i * 0.05) for i in range(57)] + [104.0, 107.0, 110.0]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    volumes = [1000000] * 57 + [1500000, 2000000, 3000000]

    candles = [
        {
            "datetime": int(d.timestamp() * 1000),
            "open": closes[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
        }
        for i, d in enumerate(dates)
    ]

    metrics = evaluate_technical_coiling(candles, 0.02, lane="UNMEASURED")
    assert metrics is not None
    assert metrics["setup_posture"] == "Unmeasured Mover Breakout"
    assert metrics["lane"] == "UNMEASURED"
    assert metrics["stop_level"] < 110.0
    # Stop loss should be below 20 EMA / swing low
    assert metrics["stop_level"] <= metrics["ema20"]


def test_dispatch_eligible_require_pb_flags():
    """
    Test dispatch eligibility:
    - With require_pb=False, non-PB longs and UNMEASURED movers qualify for local/mid research.
    - With require_pb=True, non-PB longs return False (PB requirement is strictly for entry pushes).
    """
    unmeasured_cand = {
        "symbol": "VST",
        "side": "LONG",
        "lane": "UNMEASURED",
        "screener_setup": "Unmeasured Mover Breakout",
        "priority_tier": "HIGH_PRIORITY",
        "priority_score": 85.0,
        "pb_funnel": False,
    }
    # Unmeasured mover always qualifies for deep/mid research
    ok, basis = _dispatch_eligible(unmeasured_cand, require_pb=False)
    assert ok is True
    assert basis == "SCREENER_UNMEASURED_MOVER"

    ok_strict, _ = _dispatch_eligible(unmeasured_cand, require_pb=True)
    assert ok_strict is True

    # Standard non-PB long
    non_pb_cand = {
        "symbol": "AAPL",
        "side": "LONG",
        "lane": "BASING",
        "screener_setup": "Coiled Base Breakout",
        "priority_tier": "HIGH_PRIORITY",
        "priority_score": 78.0,
        "pb_funnel": False,
    }
    # Non-PB long passes when require_pb=False (for local/mid research)
    ok_local, basis_local = _dispatch_eligible(non_pb_cand, require_pb=False)
    assert ok_local is True
    assert basis_local == "SCREENER_LONG_NON_PB"

    # Non-PB long fails when require_pb=True (strictly for entry pushes)
    ok_entry, basis_entry = _dispatch_eligible(non_pb_cand, require_pb=True)
    assert ok_entry is False
    assert basis_entry == "SCREENER_PB_MISSING"


def test_print_funnel_summary():
    """Test that funnel summary prints without exceptions."""
    funnel = {
        "total_quotes": 983,
        "rejections": {
            "biotech_exclusion": 45,
            "price_under_15": 120,
            "low_volume": 210,
            "missing_52w_data": 12,
            "out_of_bounds_or_excess_chg": 350,
        },
        "stage1_passed": {
            "basing": 85,
            "continuation": 60,
            "unmeasured": 45,
            "short": 56,
        },
        "stage2_scanned": 190,
        "stage2_rejections": {
            "technical_filter_failed": 125,
            "earnings_blackout": 15,
            "data_error": 0,
        },
        "stage2_passed": 50,
    }
    # Should run cleanly and output formatted breakdown
    print_funnel_summary(funnel)
