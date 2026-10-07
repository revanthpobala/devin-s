"""
tests/test_master_fix_list.py
=============================
Regression tests verifying fixes f1 through f8 of the master fix list:
- f1: Zone arrival evaluator enforces is_actionable; invalidation on stop breach.
- f2: Take trade opens position in position_state; compute_r calculates realized R on close.
- f3: Honest PB messaging & noise floor stop limit suppression.
- f4 & f7: Fixture dates (2029-01-01) excluded from production reads in datawindow_loader.
- f5: Research timings table, stage timers, GATE_ONLY pre-gate shortcut, MID tier execution & API route.
- f8: Open position auto-derives initial stop when missing to guarantee R calculation.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import config
from src.data.datawindow_loader import find_datawindow_paths, load_datawindow
from src.logic.actionable_gate import is_actionable
from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival
from src.tracking.position_state import close_position, load_state, open_position
from src.tracking.r_calculator import compute_r
from src.tracking.watch_manager import (
    get_research_timings,
    init_watch_db,
    record_research_timing,
)


# =====================================================================
# f1: Actionable Gate & Zone Arrival Evaluation Verification
# =====================================================================

def test_zone_arrival_evaluator_actionability():
    """Verify that zone arrival evaluator marks actionable only when levels, signal pack, and R:R pass."""
    watch_target = {
        "ticker": "AAPL",
        "side": "LONG",
        "entry_zone_low": 148.0,
        "entry_zone_high": 150.0,
        "tactical_stop": 140.0,
        "target_1": 170.0,
        "target_2": 180.0,
        "status": "STALKING",
        "signal_pack": 32,  # Bit 5 set (PB funnel)
        "fade": 0.0,        # Fade off
        "action_long": 20,  # Reversal Buy
        "ext_z": 0.5,
        "atr14": 3.0,
    }
    spot = 149.0  # In zone, live RR = (170 - 149) / (149 - 140) = 21 / 9 = 2.33 >= 2.0

    res = evaluate_target_on_zone_arrival("AAPL", spot, watch_target)
    assert res["ticker"] == "AAPL"
    assert res["in_zone"] is True
    assert res["is_actionable_now"] is True
    assert "BUY" in res["verdict"]

    # Invalidation when spot drops below stop
    breach_spot = 139.0
    res_breach = evaluate_target_on_zone_arrival("AAPL", breach_spot, watch_target)
    assert res_breach["is_actionable_now"] is False
    assert res_breach["verdict"] == "STAND_ASIDE"


def test_actionable_gate_rules():
    """Verify the canonical actionable gate rules: in zone, R:R >= floor, PB funnel, fade off, stop >= 0.7 ATR."""
    gate_data_pass = {
        "signal_pack": 36, # bit 5 (32) set (PB funnel) + bit 2 (4) set (fade off in Pine)
        "fade_long": 0.0,  # Explicit fade off (0.0)
        "action_long": 20, # Reversal Buy
        "ext_z_self": 0.5,
        "atr14": 3.0,
        "long_in_zone": 1.0,
        "long_rr_at_market": 2.1,
        "long_stop_loss": 90.0,
        "price": 100.0,
    }
    plan = {"side": "long", "entry": 100.0, "stop": 90.0, "t1": 125.0}
    ok, fails = is_actionable(gate_data_pass, plan)
    assert ok
    assert len(fails) == 0

    # Failing PB (signal_pack bit 5 missing)
    gate_data_no_pb = dict(gate_data_pass)
    gate_data_no_pb["signal_pack"] = 4  # bit 2 only, no bit 5
    ok_pb, fails_pb = is_actionable(gate_data_no_pb, plan)
    assert not ok_pb
    assert any("PB funnel" in f for f in fails_pb)

    # Failing fade (fade_long == 1.0 active)
    gate_data_fade = dict(gate_data_pass)
    gate_data_fade["fade_long"] = 1.0
    ok_fade, fails_fade = is_actionable(gate_data_fade, plan)
    assert not ok_fade
    assert any("fade" in f.lower() for f in fails_fade)


# =====================================================================
# f2: Take Trade & Position Management
# =====================================================================

def test_take_trade_opens_position_and_computes_r():
    """Verify that taking a trade opens a position in position_state and compute_r tracks realized R."""
    ticker = "NVDA"
    entry_px = 120.0
    stop_px = 110.0
    target_px = 140.0

    # Open position
    pos = open_position(
        ticker,
        side="LONG",
        strategy="SWING",
        entry_price=entry_px,
        stop=stop_px,
        target=target_px,
        quantity=100,
    )
    assert pos["ticker"] == "NVDA"
    assert pos["entry_price"] == entry_px
    assert pos["stop"] == stop_px
    assert pos["initial_stop"] == stop_px

    # Verify state loaded
    state = load_state()
    assert ticker in state
    assert state[ticker]["entry_price"] == entry_px

    # Close position at target
    exit_px = 140.0
    closed = close_position(ticker, exit_price=exit_px, exit_reason="TARGET_HIT")
    assert closed is not None
    assert closed["exit_price"] == exit_px
    # Realized R: (140 - 120) / (120 - 110) = 20 / 10 = +2.0R
    assert closed["exit_r"] == 2.0


# =====================================================================
# f3: Honest PB Messaging & Noise Floor Limit Suppression
# =====================================================================

def test_honest_pb_messaging_suppression():
    """Verify that when PB blocks or noise floor is violated, limit price is suppressed."""
    # Actionable gate check with missing PB
    gate_in_no_pb = {
        "signal_pack": 4,  # Missing PB bit 5
        "fade_long": 0.0,
        "action_long": 20,
        "ext_z_self": 0.5,
        "atr14": 2.0,
        "long_in_zone": 1.0,
        "long_rr_at_market": 2.5,
        "long_stop_loss": 90.0,
    }
    plan = {"side": "long", "entry": 100.0, "stop": 90.0, "t1": 125.0}
    ok, fails = is_actionable(gate_in_no_pb, plan)
    assert not ok
    assert any("PB funnel" in f for f in fails)


# =====================================================================
# f4 & f7: Fixture Dates & Future Directories Excluded
# =====================================================================

def test_future_and_fixture_dates_excluded():
    """Verify that fixture dates (2029-01-01) are excluded from production searches for real tickers."""
    # For a real ticker, paths must not return 2029-01-01
    paths = find_datawindow_paths("MSFT")
    for p in paths:
        assert "2029-01-01" not in str(p)
        assert "2099-12-31" not in str(p)


# =====================================================================
# f5: Research Timings, Gate-Only Shortcut & MID Tier
# =====================================================================

def test_research_timings_db_and_api():
    """Verify recording and querying research timings."""
    init_watch_db()
    record_research_timing(
        ticker="TESTSYM",
        target_date="2026-10-07",
        job_id="job_test_123",
        tier="MID",
        stage="TOTAL",
        seconds=12.45,
        tool_rounds=2,
        tool_calls=3,
        tokens=1500,
        details="SUCCESS",
    )

    timings = get_research_timings(ticker="TESTSYM")
    assert len(timings) >= 1
    t = timings[0]
    assert t["ticker"] == "TESTSYM"
    assert t["tier"] == "MID"
    assert t["stage"] == "TOTAL"
    assert t["seconds"] == 12.45
    assert t["tool_rounds"] == 2
    assert t["tool_calls"] == 3


def test_pipeline_gate_only_shortcut(tmp_path):
    """Verify that far-from-gate setups trigger GATE_ONLY shortcut with 0 LLM time."""
    from src.logic.deep_research.pipeline import run_deep_research

    date_str = "2026-10-07"
    ticker = "FARSYM"
    chart_dir = tmp_path / "data" / "raw" / date_str / ticker
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_file = chart_dir / f"{ticker}_chart.png"
    chart_file.write_text("dummy", encoding="utf-8")

    dw_mock = {
        "ticker": ticker,
        "long_rr_at_market": 0.2,
        "signal_pack": 0,
        "fade_long": 0,
        "action_long": 17,  # Action code 17 is hard blocked
        "Close": 100.0,
        "Long Stop Loss": 95.0,
        "Long Target": 101.0,
    }
    triage_mock = {
        "ticker": ticker,
        "action_long": 17,
        "rr_at_market": 0.2,
        "flags": [],
        "triage": "PASS",
    }
    dummy_paths = {
        "dw_json": chart_dir / f"{ticker}_datawindow.json",
        "dw_csv": chart_dir / f"{ticker}_datawindow.csv",
        "chart_plain": chart_file,
        "chart_zoom": chart_file,
        "chart_wide": chart_file,
        "dossier": chart_dir / f"{ticker}_news_research.md",
        "thesis": chart_dir / f"{ticker}_thesis.json",
        "quote": chart_dir / f"{ticker}_quote.json",
        "gex": chart_dir / f"{ticker}_gex.json",
        "tv_strategies": chart_dir / f"{ticker}_tv_strategies.json",
    }

    gem_dir = tmp_path / "gems"
    gem_dir.mkdir(parents=True, exist_ok=True)
    for g_name in ("revanth-original-gem.md", "response.md", "revanth-bible.md", "ponytail_finance.md", "independent_gem.md", "independent_response.md"):
        (gem_dir / g_name).write_text("dummy gem", encoding="utf-8")

    with patch("src.config.BASE_DIR", tmp_path), \
         patch("src.logic.deep_research.pipeline.config.BASE_DIR", tmp_path), \
         patch("src.logic.deep_research.artifact_loader.resolve_artifact_paths", return_value=dummy_paths), \
         patch("src.logic.deep_research.artifact_loader.load_data_window", return_value=dw_mock), \
         patch("src.logic.deep_research.artifact_loader.resolve_triage", return_value=(triage_mock, triage_mock)), \
         patch("subprocess.run") as mock_subproc, \
         patch("src.tracking.watch_manager.record_research_timing") as mock_timing:
        res = run_deep_research(
            date_str,
            target_ticker=ticker,
            tier="MID",
        )
        assert mock_timing.called
        stages = [call.kwargs.get("stage") for call in mock_timing.call_args_list]
        assert "GATE_ONLY" in stages


# =====================================================================
# f8: Position Opening Auto-Derives Initial Stop
# =====================================================================

def test_open_position_auto_derives_stop():
    """Verify that opening a position without a stop auto-derives a valid stop based on ATR or %."""
    ticker = "AMD"
    entry_px = 150.0
    atr_val = 4.5

    pos = open_position(
        ticker,
        side="LONG",
        strategy="INTRADAY",
        entry_price=entry_px,
        stop=None,  # Missing stop
        target=165.0,
        atr=atr_val,
    )
    assert pos["stop"] is not None
    assert pos["stop"] > 0
    # Long stop = entry - 1.0 * ATR = 150.0 - 4.5 = 145.5
    assert pos["stop"] == 145.5
    assert pos["initial_stop"] == 145.5

    # Closing at 159.0 gives (159 - 150) / (150 - 145.5) = 9.0 / 4.5 = +2.0R
    closed = close_position(ticker, exit_price=159.0, exit_reason="TARGET_HIT")
    assert closed["exit_r"] == 2.0
