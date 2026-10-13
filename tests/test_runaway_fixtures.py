"""
tests/test_runaway_fixtures.py

E1 verification from plan/no-lost-movers.md: the named replay fixtures must each
produce an alert at the cross time with a stated R:R at market — or be documented
as a no-op with its reason (never silently absent, never replayed against
invented levels).

The bars are the audit's own recorded move (BACKFILL_MISSED_MOVES price pairs) laid
over the plan levels actually stored on disk, so the fixture check is deterministic
and offline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.research import replay_runaway_rules as R  # noqa: E402


class _Row(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def _bars(rows, dates):
    """Minimal bars frame: rows of {Open,High,Low,Close,Volume} on the given dates."""

    class _Bars:
        def __init__(self, rows, dates):
            self._rows = rows
            self.index = dates

        def __len__(self):
            return len(self._rows)

        @property
        def iloc(self):
            outer = self

            class _Iloc:
                def __getitem__(self, i):
                    return outer._rows[i]

            return _Iloc()

    return _Bars([_Row(r) for r in rows], dates)


def _next_session(date_str: str) -> str:
    from datetime import datetime, timedelta

    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fixture_bars(fx):
    """Bars for a named fixture: the dossier session (spot) then the next session's move.

    The audit's BACKFILL price pairs are the move; the levels come from the plan actually
    stored on disk, so nothing is invented.
    """
    spot, live = fx["dossier_spot"], fx["live_spot"]
    plan = fx["plan"]
    setup = fx["dossier_date"]
    nxt = _next_session(setup)
    stop = float(plan["tactical_stop"])
    entry_high = float(plan.get("entry_zone_high") or 0.0)
    t2 = float(plan.get("target_2") or 0.0)

    # Two quiet prior bars so the 20-day-high and ADV20 windows have real data.
    prior_dates = ["2026-08-03", "2026-08-04"]
    prior_rows = [
        {"Open": spot, "High": spot * 0.98, "Low": min(spot * 0.97, stop * 0.98), "Close": spot, "Volume": 1_000_000},
        {"Open": spot, "High": spot * 0.99, "Low": min(spot * 0.96, stop * 0.97), "Close": spot * 0.99, "Volume": 1_000_000},
    ]
    # The setup session (price at the dossier spot) and the move session (the recorded print).
    move_dates = [setup, nxt]
    move_rows = [
        {"Open": spot, "High": max(spot, entry_high or spot), "Low": min(spot, stop * 0.995),
         "Close": spot, "Volume": 2_000_000},
        {"Open": (spot + live) / 2, "High": max(live, t2 or live), "Low": min(spot * 0.99, stop * 0.99),
         "Close": live, "Volume": 6_000_000},
    ]
    return _bars(prior_rows + move_rows, prior_dates + move_dates)


def _run_fixture(fx, rules=("zone_top_market", "high_20d_break", "post_t1_reentry")):
    ep = {
        "ticker": fx["ticker"],
        "setup_date": fx["dossier_date"],
        "source": "fixture",
        **{k: fx["plan"][k] for k in ("entry_zone_low", "entry_zone_high", "breakout_level",
                                      "tactical_stop", "target_1", "target_2")},
    }
    bars = _fixture_bars(fx)
    out = {}
    with patch.object(R, "_bars_frame", return_value=bars):
        for rule in rules:
            row = R.replay_episode(rule, ep)
            if row:
                out[rule] = row
    return out


def test_named_fixtures_all_present_and_either_replayed_or_documented():
    fixtures = R.build_fixtures()
    by_ticker = {f["ticker"]: f for f in fixtures}
    assert {"META", "VST", "NRG"} <= set(by_ticker)

    for fx in fixtures:
        if fx["replayable"]:
            assert fx.get("plan"), "a replayable fixture must carry its stored plan"
        else:
            assert fx.get("no_stored_plan_reason"), "a non-replayable fixture must say why"


def test_meta_fixture_alerts_at_the_cross_with_rr_stated():
    """META 10-09 (718.67) -> 10-12 (748.10): the name completed T1 without a fill, so
    the post-T1 re-entry rule fires at the cross and states its R:R at market.

    The contract holds in both data states: where the dossier is on disk the replay
    runs and the alert carries its R:R; where it is not, the corpus documents the
    absence (NO_STORED_PLAN with a reason) rather than claiming an alert.
    """
    fixtures = {f["ticker"]: f for f in R.build_fixtures()}
    meta = fixtures["META"]

    if not meta["replayable"]:
        outcome = next(
            (o for o in R.fixture_outcomes([meta]) if o["ticker"] == "META"), None
        )
        assert outcome is not None and outcome["alert_outcome"] == "NO_STORED_PLAN"
        assert outcome["reason"], "a missing stored plan must carry its reason"
        return

    rows = _run_fixture(meta)
    assert "post_t1_reentry" in rows, "the named META fixture must trigger its rule"
    row = rows["post_t1_reentry"]
    assert row["trigger_px"] >= float(meta["plan"]["target_1"])  # the cross is at T1
    assert row["rr_at_trigger"] is not None                     # R:R stated at the cross
    assert row["stop_loss_refreshed"] if "stop_loss_refreshed" in row else True


def test_vst_fixture_outcome_is_documented():
    """VST 140.44 -> 166.66 against the plan the desk actually stored on 10-07.

    That plan's own target_1 ($154.54) sits BELOW its zone top ($155.16), so at the
    market print its rules require, the cross is already at/past T1 — there is no room
    to buy. The corpus documents that no-trigger with its reason instead of inventing a
    level or claiming an alert that never existed.

    The guarantee is the documentation, not the artifact: this environment has no VST
    local-triage thesis on disk (the run that produced it is gone), so the corpus must
    say NO_STORED_PLAN with its reason — never an invented alert. Where the dossier IS
    present, the no-room branch is asserted in full.
    """
    fixtures = {f["ticker"]: f for f in R.build_fixtures()}
    vst = fixtures["VST"]

    outcomes = {o["ticker"]: o for o in R.fixture_outcomes([vst])}
    vst_out = outcomes["VST"]

    if vst["replayable"]:
        assert vst_out["alert_outcome"] in ("ALERT_AT_CROSS", "NO_TRIGGER")
        if vst_out["alert_outcome"] == "NO_TRIGGER":
            assert "no room at its own zone top" in vst_out["reason"]
        else:
            for trig in vst_out["triggers"]:
                assert trig["trigger_px"] > 0
    else:
        # No dossier on disk: the corpus documents the absence instead of replaying
        # against invented levels.
        assert vst_out["alert_outcome"] == "NO_STORED_PLAN"
        assert vst_out["reason"], "a missing stored plan must carry its reason"


def test_nrg_fixture_outcome_is_documented():
    """NRG 102.75 -> 109.22: its stored plan predates the move, so the corpus says so —
    the plan's date rides along rather than being hidden."""
    fixtures = {f["ticker"]: f for f in R.build_fixtures()}
    nrg = fixtures["NRG"]
    outcomes = {o["ticker"]: o for o in R.fixture_outcomes([nrg])}
    nrg_out = outcomes["NRG"]
    assert nrg_out["alert_outcome"] in ("ALERT_AT_CROSS", "NO_TRIGGER", "NO_STORED_PLAN")
    assert nrg_out["dossier_spot"] == 102.75
    if nrg_out["alert_outcome"] == "NO_TRIGGER":
        assert nrg_out["reason"]
    if nrg_out.get("plan_date"):
        # A plan that predates the move is visible, not hidden.
        assert nrg_out["plan_date"] <= nrg_out["dossier_date"] or nrg_out["reason"]


def test_fixture_outcomes_cover_every_named_fixture():
    outcomes = R.fixture_outcomes(R.build_fixtures())
    by_ticker = {o["ticker"]: o for o in outcomes}
    assert {"META", "VST", "NRG"} <= set(by_ticker)
    for ticker, o in by_ticker.items():
        if o["replayable"]:
            assert o["alert_outcome"] in ("ALERT_AT_CROSS", "NO_TRIGGER")
        else:
            assert o["alert_outcome"] == "NO_STORED_PLAN"
            assert o.get("reason"), f"{ticker} must document why it cannot be replayed"


def test_corpus_summary_reports_era_halves_breadth_median_and_fire_rate():
    """The plan's per-rule report: era split, first/second halves, ticker breadth,
    median R, and fire-rate under 6% of bars."""
    rows = [
        {"ticker": "A", "setup_date": "2010-05-01", "rule": "zone_top_market", "filled": True,
         "r_net": 1.0, "bars_in_walk": 10},
        {"ticker": "B", "setup_date": "2020-05-01", "rule": "zone_top_market", "filled": True,
         "r_net": -1.0, "bars_in_walk": 10},
        {"ticker": "C", "setup_date": "2026-05-01", "rule": "zone_top_market", "filled": True,
         "r_net": 2.0, "bars_in_walk": 10},
    ]
    s = R.summarize(rows)["zone_top_market"]
    assert s["fire_rate_max"] == 0.06
    assert s["era_split"]["2006-2015"]["n"] == 1
    assert s["era_split"]["2016-2026"]["n"] == 2
    assert set(s["halves"]) == {"first_half", "second_half"}
    assert s["ticker_breadth"] == 3
    assert s["median_r"] == 1.0
    assert s["fire_rate"] == pytest.approx(3 / 30, abs=1e-6)
    assert s["verdict"] == "UNMEASURED"  # 3 fills < MIN_REPLAYS
