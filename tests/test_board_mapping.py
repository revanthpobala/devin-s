"""
Tests for the board layer: setup-name -> lane mapping, and the screener's replacement of the
priority-tier badge with the PB funnel plus the lane's measured prior.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.logic.data_window_filter import LANE_PRIORS
from src.logic.setup_lane_map import (
    NO_EDGE_ACTION_CODES,
    UNMEASURED,
    classify_action_code,
    classify_setup_name,
    inbox_row_mapping,
)
from src.ui.routes.screener import _pb_lane_prior


# --- the two dominant inbox families -------------------------------------------------------------

def test_a_plus_trend_long_is_unmeasured_not_a_lane():
    """41 of 81 inbox rows. It has never been measured, so it must not carry a lane."""
    r = classify_setup_name("A+ Trend Long")
    assert r["tag"] == UNMEASURED
    assert r["lane"] is None
    assert r["prior_win"] is None
    assert "unmeasured" in r["label"].lower()


@pytest.mark.parametrize("name", ["A+ Trend Long", "a+ trend long", "A+ TREND LONG"])
def test_a_plus_trend_matching_is_case_insensitive(name):
    assert classify_setup_name(name)["tag"] == UNMEASURED


def test_early_action_long_is_tagged_no_edge():
    """27 rows. It maps to ACTION (code 2), which measured flat/unstable -- so 'no edge', not a lane."""
    r = classify_setup_name("Early Action Long")
    assert r["action_code"] == 2
    assert r["tag"] == "no edge"
    assert r["lane"] is None
    assert r["prior_win"] is None


def test_code_two_is_in_the_no_edge_set():
    assert 2 in NO_EDGE_ACTION_CODES
    assert classify_action_code(2)["tag"] == "no edge"


def test_reversal_buy_maps_to_the_code20_lane_with_its_prior():
    r = classify_setup_name("REVERSAL BUY")
    assert r["lane"] == "CODE20"
    assert r["tag"] == "measured"
    assert r["prior_win"] == LANE_PRIORS["reversal_buy_lane"][0]
    assert r["prior_ev"] == LANE_PRIORS["reversal_buy_lane"][1]


@pytest.mark.parametrize("name,code", [
    ("OVERSOLD", None),
    ("RSI2 Setup", None),
])
def test_measured_lanes_carry_their_prior(name, code):
    r = classify_setup_name(name)
    assert r["tag"] == "measured"
    assert r["prior_win"] is not None


def test_excluded_and_hard_cut_families_are_not_actionable():
    assert classify_action_code(11)["tag"] == "excluded"   # EXTENDED
    assert classify_action_code(12)["tag"] == "excluded"   # STRETCHED
    assert classify_action_code(16)["tag"] == "excluded"   # BLOW-OFF
    assert classify_action_code(17)["tag"] == "hard cut"   # PARABOLIC
    assert classify_action_code(18)["tag"] == "hard cut"   # TOXIC RISK
    for code in (11, 12, 16, 17, 18):
        assert classify_action_code(code)["lane"] is None


def test_unknown_names_are_tagged_unmeasured_with_the_name_visible():
    r = classify_setup_name("Some Unheard-Of Alert")
    assert r["tag"] == UNMEASURED
    assert "Some Unheard-Of Alert" in r["label"], "a blank tag is indistinguishable from a bug"


def test_empty_setup_is_unmeasured():
    assert classify_setup_name("")["tag"] == UNMEASURED
    assert classify_setup_name(None)["tag"] == UNMEASURED


def test_missing_or_bad_action_code_is_unmeasured():
    assert classify_action_code(None)["tag"] == UNMEASURED
    assert classify_action_code("nope")["tag"] == UNMEASURED


def test_numeric_code_wins_when_present_but_a_blank_name_falls_back():
    r = inbox_row_mapping("A+ Trend Long", action_code=11)
    assert r["tag"] == "excluded", "the measured code is more informative than the name"
    assert r["setup"] == "A+ Trend Long"

    blank = inbox_row_mapping("", action_code=None)
    assert blank["tag"] == UNMEASURED


def test_unmeasured_code_falls_through_to_the_name():
    r = inbox_row_mapping("REVERSAL BUY", action_code=8)
    assert r["tag"] == "measured"
    assert r["lane"] == "CODE20"


def test_no_family_ever_claims_a_measured_tag_without_a_prior():
    """A 'measured' badge with no number behind it is the failure mode this module exists to stop."""
    from src.logic.setup_lane_map import _NAME_MAP

    for needle, _code, _lane, tag, prior in _NAME_MAP:
        if tag == "measured":
            assert prior is not None, f"{needle} is tagged measured with no prior"
            assert prior[0] > 0


# --- screener: the retired priority badge -------------------------------------------------------

def test_pb_lane_prior_splits_on_the_pb_bit():
    pb = _pb_lane_prior(3.0, True)
    no_pb = _pb_lane_prior(3.0, False)
    assert pb[0] > no_pb[0]
    assert pb[1] > no_pb[1], "PB is the reliable exclusion; it must read better than no-PB"
    assert pb[1] > 0 and no_pb[1] < 0.1


def test_pb_lane_prior_tiers_at_rr_five():
    strong = _pb_lane_prior(5.0, True)
    regular = _pb_lane_prior(4.9, True)
    assert strong[1] > regular[1]


def test_pb_lane_prior_is_none_without_a_measurement():
    assert _pb_lane_prior(0, True) is None
    assert _pb_lane_prior(None, True) is None


def test_candidates_are_enriched_and_split_by_eligibility(tmp_path):
    """The table must not render 4 PB and 3 non-PB longs as seven equals."""
    import src.ui.routes.screener as sr

    raw_dir = tmp_path / "data" / "raw"
    day = raw_dir / "2026-09-30"
    day.mkdir(parents=True)
    manifest = [
        {"symbol": "GLW", "price": 100.0, "support_level": 95.0, "priority_score": 80.0,
         "pb_funnel": True, "proxy_rr": 3.4, "stop_width_atr": 0.9},
        {"symbol": "AAPL", "price": 200.0, "support_level": 195.0, "priority_score": 70.0,
         "pb_funnel": False, "proxy_rr": 4.1, "stop_width_atr": 0.2},
        {"symbol": "TXNM", "price": 50.0, "support_level": 48.0, "priority_score": 60.0,
         "pb_funnel": False, "proxy_rr": 2.6},
    ]
    (day / "schwab_survivors.json").write_text(json.dumps(manifest), encoding="utf-8")

    with patch.object(sr.config, "BASE_DIR", tmp_path), \
         patch("src.screener.schwab_pre_move_scan.get_schwab_client", return_value=MagicMock()), \
         patch("src.screener.schwab_pre_move_scan.check_market_tide",
               return_value={"is_bullish": True, "available": True, "trend_str": "BULLISH (TIDE ON)"}):
        out = sr.get_schwab_screener_candidates(date="2026-09-30", side="long")

    assert out["count"] == 3
    assert out["eligible_count"] == 1, "only the PB long is dispatch-eligible"
    by_sym = {c["symbol"]: c for c in out["candidates"]}
    assert by_sym["GLW"]["eligible"] is True
    assert by_sym["AAPL"]["eligible"] is False
    assert by_sym["AAPL"]["stop_width_atr"] == 0.2
    assert by_sym["TXNM"]["pb_bucket"] == "no PB"
    assert by_sym["GLW"]["display_prior_win"] is not None
    assert by_sym["GLW"]["display_prior_ev"] > 0
    assert "Priority tier retired" in out["notes"]


def test_unmeasured_pb_renders_as_unmeasured_not_false(tmp_path):
    import src.ui.routes.screener as sr

    raw_dir = tmp_path / "data" / "raw"
    day = raw_dir / "2026-09-30"
    day.mkdir(parents=True)
    (day / "schwab_survivors.json").write_text(json.dumps([
        {"symbol": "ZZZ", "price": 10.0, "support_level": 9.0, "priority_score": 75.0},
    ]), encoding="utf-8")

    with patch.object(sr.config, "BASE_DIR", tmp_path), \
         patch("src.screener.schwab_pre_move_scan.get_schwab_client", return_value=MagicMock()), \
         patch("src.screener.schwab_pre_move_scan.check_market_tide",
               return_value={"is_bullish": True, "available": True, "trend_str": "BULLISH"}):
        out = sr.get_schwab_screener_candidates(date="2026-09-30", side="long")

    c = out["candidates"][0]
    assert c["pb_bucket"] == "unmeasured"
    assert c["eligible"] is False, "an unmeasured bar is never dispatch-eligible"


# --- desk: the unmeasured group and the honest counts --------------------------------------------

def test_desk_partitions_rows_and_isolates_test_tickers():
    """missing_symbols held 6 real core gaps plus a GOODTICKER fixture row."""
    from src.ui.routes.desk import is_test_ticker

    assert is_test_ticker("GOODTICKER") is True
    assert is_test_ticker("TESTCO") is True
    assert is_test_ticker("") is True
    for real in ("SPY", "QQQ", "AMZN", "WMT", "HOOD", "CRM", "GLW", "SWKS", "QRVO", "WDAY"):
        assert is_test_ticker(real) is False, f"{real} is a real coverage gap, not a fixture"


def test_needs_you_gate_follows_the_live_rr_config():
    """The desk used to keep its own RR_MKT_ACTIONABLE = 2.0 literal alongside the tunable one."""
    import inspect

    from src.tracking import rr_config
    from src.ui.routes import desk as desk_mod

    assert not hasattr(desk_mod, "RR_MKT_ACTIONABLE"), \
        "the desk must not carry a second copy of a tunable threshold"
    assert "rr_config.min_rr()" in inspect.getsource(desk_mod.get_today)
    assert rr_config.min_rr() == 2.0, "the shipped default is the measured floor"