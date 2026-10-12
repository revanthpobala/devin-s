"""
tests/test_level_engine.py

A1 from plan/no-lost-movers.md: the universal level engine. Every armed level,
every watched name, evaluated as cross events — not a top-down elif cascade.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tracking.level_engine import (
    KIND_BREAKOUT,
    KIND_HIGH_20D,
    KIND_INVALIDATION,
    KIND_STOP,
    KIND_T1,
    KIND_T2,
    KIND_ZONE_LOW,
    KIND_ZONE_TOP,
    LevelEngineState,
    adv20_from_bars,
    bars_high_20d_and_adv20,
    build_armed_levels,
    get_engine_state,
    prior_20d_high,
    rearm_on_close,
    reset_engine_state,
    volume_pace,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_engine_state()
    yield
    reset_engine_state()


META_PLAN = {
    "entry_type": "LIMIT",
    "entry_zone_low": 710.43,
    "entry_zone_high": 715.95,
    "breakout_level": 747.60,
    "tactical_stop": 698.50,
    "target_1": 742.09,
    "target_2": 747.60,
    "invalidation_price": 698.50,
}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def test_build_all_level_kinds_from_plan():
    levels = build_armed_levels(META_PLAN, side="LONG", high_20d=718.67)
    kinds = [lv.kind for lv in levels]
    assert kinds == [KIND_ZONE_LOW, KIND_ZONE_TOP, KIND_BREAKOUT, KIND_HIGH_20D, KIND_T1, KIND_T2, KIND_STOP]
    # tactical_stop == invalidation_price -> one STOP level, no duplicate INVALIDATION
    assert KIND_INVALIDATION not in kinds


def test_build_reads_dossier_long_plan_vocabulary():
    long_plan = {"zone": [138.0, 142.0], "stop": 135.0, "target": 170.0}
    levels = build_armed_levels(long_plan, side="LONG")
    by_kind = {lv.kind: lv for lv in levels}
    assert by_kind[KIND_ZONE_LOW].price == 138.0
    assert by_kind[KIND_ZONE_TOP].price == 142.0
    assert by_kind[KIND_T1].price == 170.0
    assert by_kind[KIND_STOP].price == 135.0


def test_build_omits_absent_levels_never_fabricates():
    levels = build_armed_levels({"entry_zone_low": 100.0}, side="LONG")
    assert [lv.kind for lv in levels] == [KIND_ZONE_LOW]
    assert build_armed_levels({}, side="LONG") == []


def test_build_mirrors_short_side_directions():
    levels = build_armed_levels(META_PLAN, side="SHORT", high_20d=None)
    by_kind = {lv.kind: lv for lv in levels}
    assert by_kind[KIND_T1].direction == "DOWN"
    assert by_kind[KIND_ZONE_TOP].direction == "DOWN"


# ---------------------------------------------------------------------------
# Cross detection
# ---------------------------------------------------------------------------

def test_zone_top_cross_fires_only_upward():
    state = LevelEngineState(ticker="META", levels=build_armed_levels(META_PLAN), last_price=710.0)
    crosses = state.detect_crosses(720.0)
    kinds = [c.kind for c in crosses]
    assert KIND_ZONE_LOW in kinds and KIND_ZONE_TOP in kinds

    # Falling back through the zone top from above must NOT re-fire the breakout
    # path, and ZONE_LOW only fires on an up-cross (a bounce), so a fall through
    # it fires nothing.
    state2 = LevelEngineState(ticker="META", levels=build_armed_levels(META_PLAN), last_price=730.0)
    kinds2 = [c.kind for c in state2.detect_crosses(705.0)]
    assert kinds2 == []
    # A bounce up through the zone low fires it.
    state3 = LevelEngineState(ticker="META", levels=build_armed_levels(META_PLAN), last_price=705.0)
    assert KIND_ZONE_LOW in [c.kind for c in state3.detect_crosses(712.0)]


def test_breakout_and_20d_high_cross():
    levels = build_armed_levels(META_PLAN, high_20d=720.0)
    state = LevelEngineState(ticker="META", levels=levels, last_price=715.0)
    kinds = sorted(c.kind for c in state.detect_crosses(750.0))
    assert KIND_BREAKOUT in kinds and KIND_HIGH_20D in kinds and KIND_T1 in kinds


def test_target_crosses():
    levels = build_armed_levels(META_PLAN)
    state = LevelEngineState(ticker="META", levels=levels, last_price=730.0)
    kinds = [c.kind for c in state.detect_crosses(744.0)]
    assert KIND_T1 in kinds
    state2 = LevelEngineState(ticker="META", levels=levels, last_price=744.0)
    kinds2 = [c.kind for c in state2.detect_crosses(748.0)]
    assert KIND_T2 in kinds2


def test_stop_cross_fires_downward_only():
    levels = build_armed_levels(META_PLAN)
    state = LevelEngineState(ticker="META", levels=levels, last_price=700.0)
    kinds = [c.kind for c in state.detect_crosses(697.0)]
    assert KIND_STOP in kinds
    # Rising back through the stop does not fire it again (single-fire anyway).
    state2 = LevelEngineState(ticker="META", levels=levels, last_price=697.0)
    assert state2.detect_crosses(705.0) == []


def test_first_poll_after_cold_start_has_no_prev_price():
    state = LevelEngineState(ticker="META", levels=build_armed_levels(META_PLAN), last_price=None)
    assert state.detect_crosses(750.0) == []


# ---------------------------------------------------------------------------
# Volume pace (A3)
# ---------------------------------------------------------------------------

def test_cross_confirmed_on_volume_pace():
    levels = build_armed_levels(META_PLAN)
    state = LevelEngineState(ticker="META", levels=levels, last_price=715.0)
    (cross,) = [c for c in state.detect_crosses(750.0, rvol=1.4) if c.kind == KIND_ZONE_TOP]
    assert cross.volume_confirmed is True
    assert cross.rvol == 1.4


def test_cross_held_when_volume_pace_below_floor():
    levels = build_armed_levels(META_PLAN)
    state = LevelEngineState(ticker="META", levels=levels, last_price=715.0)
    (cross,) = [c for c in state.detect_crosses(750.0, rvol=0.6) if c.kind == KIND_ZONE_TOP]
    assert cross.volume_confirmed is False


def test_cross_unmeasured_volume_still_fires():
    levels = build_armed_levels(META_PLAN)
    state = LevelEngineState(ticker="META", levels=levels, last_price=715.0)
    (cross,) = [c for c in state.detect_crosses(750.0, rvol=None) if c.kind == KIND_ZONE_TOP]
    assert cross.volume_confirmed is True  # unmeasured, never silently dropped
    assert cross.rvol is None


def test_volume_pace_none_when_either_side_missing():
    assert volume_pace(None, 1_000_000) is None
    assert volume_pace(500_000, None) is None
    assert volume_pace(0, 1_000_000) is None
    assert volume_pace(2_000_000, 1_000_000) == 2.0


# ---------------------------------------------------------------------------
# Re-arm rules
# ---------------------------------------------------------------------------

def test_rearm_after_close_back_through():
    levels = build_armed_levels(META_PLAN, high_20d=720.0)
    zt = next(lv for lv in levels if lv.kind == KIND_ZONE_TOP)
    zt.armed = False
    # Still above the zone top at the "close" -> stays disarmed.
    rearm_on_close(levels, 730.0)
    assert zt.armed is False
    # Close back inside the zone -> re-armed.
    rearm_on_close(levels, 712.0)
    assert zt.armed is True


def test_stop_single_fire_per_day():
    levels = build_armed_levels(META_PLAN)
    stop = next(lv for lv in levels if lv.kind == KIND_STOP)
    stop.armed = False
    rearm_on_close(levels, 705.0)  # a close back above does NOT re-arm the stop
    assert stop.armed is False


def test_mark_fired_disarms_and_stamps():
    state = LevelEngineState(ticker="META", levels=build_armed_levels(META_PLAN), last_price=715.0)
    state.mark_fired(KIND_ZONE_TOP)
    zt = state.by_kind(KIND_ZONE_TOP)
    assert zt.armed is False
    assert zt.last_fired_date is not None
    assert state.detect_crosses(720.0) == []  # disarmed levels do not re-fire


def test_engine_state_rebuilds_on_new_thesis():
    state = get_engine_state("META", plan=META_PLAN, plan_identity="META_2026-10-09")
    state.mark_fired(KIND_ZONE_TOP)
    # Same identity -> state persists.
    same = get_engine_state("META", plan_identity="META_2026-10-09")
    assert same.by_kind(KIND_ZONE_TOP).armed is False
    # New thesis -> levels re-armed (they are new levels).
    fresh = get_engine_state("META", plan=META_PLAN, plan_identity="META_2026-10-12")
    assert fresh.by_kind(KIND_ZONE_TOP).armed is True


# ---------------------------------------------------------------------------
# 20-day stats from bars
# ---------------------------------------------------------------------------

class _FakeRow(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class _FakeBars:
    def __init__(self, rows, dates):
        self._rows = rows
        self.index = dates

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, mask):
        rows = [r for r, m in zip(self._rows, mask) if m]
        dates = [d for d, m in zip(self.index, mask) if m]
        return _FakeBars(rows, dates)

    @property
    def iloc(self):
        outer = self

        class _Iloc:
            def __getitem__(self, i):
                return outer._rows[i]

        return _Iloc()


def _fake_bars():
    dates = [f"2026-09-{d:02d}" for d in range(1, 31)]
    rows = []
    for i, d in enumerate(dates):
        rows.append(_FakeRow({"High": 700.0 + i, "Low": 690.0, "Close": 695.0 + i, "Volume": 1_000_000 + i * 1000}))
    return _FakeBars(rows, dates)


def test_bars_high_20d_and_adv20():
    with patch("src.tracking.level_engine._bars_frame", return_value=_fake_bars()):
        high_20d, adv = bars_high_20d_and_adv20("META", as_of="2026-09-30")
    assert high_20d == 729.0                 # max High of the last 20 rows (700+29)
    assert adv == pytest.approx(1_019_500.0)  # mean Volume of the last 20 rows (i = 10..29)


def test_bars_high_20d_and_adv20_absent_means_absent():
    with patch("src.tracking.level_engine._bars_frame", return_value=None):
        assert bars_high_20d_and_adv20("META") == (None, None)
    assert bars_high_20d_and_adv20("") == (None, None)
    assert adv20_from_bars("") is None


def test_prior_20d_high_prefers_live_datawindow():
    dw = {"high_20d": 761.25}
    assert prior_20d_high("META", dw=dw) == 761.25
    with patch("src.tracking.level_engine._bars_frame", return_value=None):
        assert prior_20d_high("META", dw={}) is None  # absent, never fabricated
