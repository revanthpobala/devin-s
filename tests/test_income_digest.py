"""
INCOME tier and after-close digest.

The load-bearing property under test: the INCOME body must never claim an expectancy. There is no
options price data in this pipeline, so any "this pays" framing is unsupported, and a body that
implies one is worse than no body at all.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import src.tracking.notify as notify_mod
from src.tracking.entry_risk_alerts import Onset
from src.tracking.income_digest import (
    CLIMAX_RELATIVE_DRIFT_PCT,
    POST_CRASH_DRAWDOWN_PCT,
    TOUCH_PROB_BASELINE,
    TOUCH_PROB_WITH_EXT_Z_GATE,
    build_digest,
    collect_income,
    covered_call_candidate,
    fire_income,
    income_body,
    run_digest,
)


def _extended(ticker="HELD", ext_z=2.6, regime=0, close=100.0, em=7.0, action=1):
    return Onset(
        ticker=ticker, date="2026-09-30", close=close, stop=94.0, target=115.0, atr=5.0,
        ext_z=ext_z, regime=regime, exp_move_pct=em, action_code=action,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=True,
    )


@pytest.fixture
def quiet_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    yield


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append((tier, title, body)) or ["x"]))
    return sent


# --- candidacy ------------------------------------------------------------------------------

def test_extended_name_in_a_healthy_regime_is_a_candidate():
    sig = covered_call_candidate(_extended())
    assert sig is not None
    assert sig.blocked is False
    assert "covered-call candidate" in income_body(sig)


def test_nonextended_name_is_not_a_candidate():
    assert covered_call_candidate(_extended(ext_z=1.2)) is None
    assert covered_call_candidate(_extended(ext_z=None)) is None


def test_ext_z_exactly_at_the_gate_qualifies():
    assert covered_call_candidate(_extended(ext_z=2.0)) is not None
    assert covered_call_candidate(_extended(ext_z=1.99)) is None


# --- the two hard blocks -------------------------------------------------------------------------

def test_regime_2_climax_is_a_do_not_sell():
    sig = covered_call_candidate(_extended(regime=2))
    assert sig.blocked is True
    body = income_body(sig)
    assert "do NOT sell calls" in body
    assert f"{CLIMAX_RELATIVE_DRIFT_PCT:+.1f}%" in body
    assert "No strike suggested" in body


def test_climax_push_carries_the_block(quiet_channel, monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now",
                        lambda: datetime(2026, 9, 28, 9, 30))
    fire_income([covered_call_candidate(_extended(ticker="CLMX", regime=2))], "2026-09-28")
    assert len(sent) == 1
    assert "DO NOT sell calls" in sent[0][1]
    assert sent[0][0] == "INCOME"


def test_strike_is_at_least_one_expected_move():
    sig = covered_call_candidate(_extended(close=100.0, em=7.0))
    assert sig.strike == pytest.approx(107.0)
    body = income_body(sig)
    assert "$107.00" in body
    assert "do NOT place the strike at the Short Stop Loss" in body
    assert "63%-73%" in body


def test_post_crash_regime_shortens_the_expiry():
    sig = covered_call_candidate(_extended(), index_drawdown_pct=POST_CRASH_DRAWDOWN_PCT)
    assert sig.is_post_crash is True
    assert sig.bars_to_expiry == 5
    assert "5-10 bars (post-crash)" == sig.expiry_window
    assert "index is 15% off its high" in income_body(sig)


def test_normal_regime_keeps_the_21_bar_window():
    sig = covered_call_candidate(_extended(), index_drawdown_pct=8.0)
    assert sig.is_post_crash is False
    assert sig.bars_to_expiry == 21
    assert "21 bars (standard)" == sig.expiry_window


# --- the honesty requirement ------------------------------------------------------------------

def test_body_states_a_touch_probability_not_an_expectancy():
    body = income_body(covered_call_candidate(_extended()))
    assert f"{TOUCH_PROB_WITH_EXT_Z_GATE:.1%}" in body
    assert f"{TOUCH_PROB_BASELINE:.1%}" in body
    assert "touch probability, not an expectancy" in body
    for forbidden in ("expectancy of", "expected profit", "win rate", "edge of"):
        assert forbidden not in body.lower(), f"body implies {forbidden!r}, which is unmeasured"


def test_body_says_the_gate_is_an_exclusion_not_an_edge():
    body = income_body(covered_call_candidate(_extended()))
    assert "EXCLUSION, not an edge" in body
    assert TOUCH_PROB_WITH_EXT_Z_GATE < TOUCH_PROB_BASELINE, "the measured gate is worse than baseline"


# --- collection and push dedupe -----------------------------------------------------------------

def test_collect_income_filters_to_candidates():
    sigs = collect_income([
        _extended(ticker="A", ext_z=2.5),
        _extended(ticker="B", ext_z=0.5),
        _extended(ticker="C", ext_z=2.1),
    ])
    assert [s.ticker for s in sigs] == ["A", "C"]


def test_income_pushes_once_per_ticker_per_day(quiet_channel, monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: datetime(2026, 9, 28, 9, 30))
    sigs = collect_income([_extended(ticker="HELD", ext_z=2.5)])
    fire_income(sigs, "2026-09-28")
    fire_income(sigs, "2026-09-28")
    fire_income(sigs, "2026-09-29")
    assert len(sent) == 2


# =====================================================================================
# DIGEST
# =====================================================================================

def _watch(ticker, rr_target, ext_z=0.3, action=1, packs=True):
    o = Onset(
        ticker=ticker, date="2026-09-30", close=100.0, stop=96.5, target=rr_target, atr=5.0,
        ext_z=ext_z, regime=0, exp_move_pct=7.0, action_code=action,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=packs,
    )
    return o


def test_digest_carries_non_pb_onsets():
    body = build_digest(
        "2026-09-30",
        onsets=[],
        entry_digest_candidates=[
            {"ticker": "NOPB", "rr_at_market": 3.2, "pb": False,
             "stop_width_atr": 0.9, "reasons": ["not PB funnel"]},
        ],
    )
    assert "Non-PB R:R onsets" in body
    assert "NOPB" in body
    assert "no push" in body


def test_digest_carries_code20_as_a_secondary_lane():
    body = build_digest("2026-09-30", onsets=[_watch("RV20", 110.0, action=20)],
                        code20_on_action=["RV20"])
    assert "Code 20 REVERSAL BUY" in body
    assert "RV20" in body
    assert "secondary lane" in body
    assert "+0.05R" in body


def _tight_watch(ticker="TIGHT", target=125.0):
    """Stop 0.1 below a 100 close on a 5 ATR name: 0.02 ATR, deep inside daily noise."""
    return Onset(
        ticker=ticker, date="2026-09-30", close=100.0, stop=99.9, target=target, atr=5.0,
        ext_z=0.3, regime=0, exp_move_pct=7.0, action_code=1,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=True,
    )


def test_digest_ranks_the_watch_list_by_rr_with_stop_width():
    body = build_digest("2026-09-30", onsets=[
        _watch("LOWRR", 112.0),
        _watch("HIGHRR", 130.0),
        _tight_watch("TIGHTSTOP"),
    ])
    watch = body.split("Watch list by RR@mkt")[1]
    assert watch.index("HIGHRR") < watch.index("LOWRR") < watch.index("TIGHTSTOP"), (
        "a stop inside noise must never outrank a row with a measurable stop, however big its RR"
    )
    assert "inside noise" in watch
    assert "0.70 ATR" in watch, "and a sane stop still shows its measured width"


def test_a_noise_stop_cannot_take_the_top_slot():
    """The live defect: SWKS/QRVO at 0.2 ATR showed RR 14-16 and sorted to the top."""
    assert _tight_watch("TIGHT").rr_at_market > 200
    body = build_digest("2026-09-30", onsets=[_tight_watch("TIGHT"), _watch("SANE", 118.0)])
    watch = body.split("Watch list by RR@mkt")[1]
    assert watch.index("SANE") < watch.index("TIGHT")


def test_digest_does_not_flag_a_stop_exactly_at_the_threshold():
    """0.70 ATR is the corpus median, not a defect. The flag is for strictly narrower."""
    body = build_digest("2026-09-30", onsets=[_watch("MEDIAN", 125.0)])
    assert "inside noise" not in body
    assert "0.70 ATR" in body


def test_digest_flags_a_stop_inside_noise():
    tight = _tight_watch("TIGHT")
    assert tight.stop_tight is True
    assert tight.rr_at_market > 100, "the R:R here is an artifact of the tiny denominator"
    body = build_digest("2026-09-30", onsets=[tight])
    assert "inside noise" in body


def test_digest_lists_held_names_that_reached_their_zone():
    o = _watch("HELD", 125.0)
    o.long_in_zone = True
    body = build_digest("2026-09-30", onsets=[o], holdings=["HELD"])
    assert "Held names now in zone" in body
    assert "HELD" in body


def test_digest_does_not_list_unheld_names_as_in_zone():
    body = build_digest("2026-09-30", onsets=[_watch("NOTMINE", 125.0)], holdings=["SOMETHINGELSE"])
    assert "Held names now in zone" not in body


def test_digest_includes_the_scoreboard_delta():
    body = build_digest("2026-09-30", onsets=[], scoreboard_delta="swing RR_SETUP n 35 -> 41, mean +0.42R")
    assert "Scoreboard delta" in body
    assert "mean +0.42R" in body


def test_quiet_session_says_so():
    body = build_digest("2026-09-30", onsets=[])
    assert "Quiet session" in body


def test_run_digest_end_to_end(quiet_channel, monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: datetime(2026, 9, 28, 17, 30))
    import src.tracking.income_digest as mod
    monkeypatch.setattr(mod, "load_onsets_for_date",
                        lambda date: [_watch("GLW", 125.0), _extended("HELD", ext_z=2.5)])

    out = run_digest(date="2026-09-28", holdings=["HELD"], notify_enabled=True)
    assert out["onsets_scanned"] == 2
    assert out["income_candidates"] == ["HELD"]
    assert out["digest_sent"] is True
    tiers = [t for t, _, _ in sent]
    assert "INCOME" in tiers and "DIGEST" in tiers
    assert "HELD" in out["digest_body"]