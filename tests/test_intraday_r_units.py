"""
Unit tests for R-unit integrity in src/tracking/intraday_stats.py.

Bug: compute_group_stats averaged whatever sat in pine_exit_r / exit_r / replay_r. Rows whose
stop sits on the wrong side of entry (or is missing) have no risk denominator, so the stored value
was a percent or dollar figure being read as R -- the scoreboard reported UNKNOWN grade mean_r
+51.2 while the real expectancy sat inside noise.
"""

from __future__ import annotations

import math

import pytest

from src.tracking.intraday_stats import (
    MAX_PLAUSIBLE_R,
    _risk_per_share,
    _scoreable_r,
    compute_group_stats,
    lookup_bucket_stats,
)


def _row(**kw):
    base = {
        "ticker": "AAPL",
        "side": "LONG",
        "entry_price": 100.0,
        "stop": 97.0,          # 3.00 risk per share
        "entry_px": 100.0,
        "entry_stop": 97.0,
        "grade": "A",
        "score": 90.0,
        "hour": 10,
    }
    base.update(kw)
    return base


# --- the specific regression the spec calls out ---------------------------------------------

def test_row_with_implausible_r_is_dropped_not_averaged():
    rows = [
        _row(exit_r=1.0),
        _row(exit_r=51.2),      # a percentage stored as R
        _row(exit_r=44.7),      # a dollar figure stored as R
    ]
    stats = compute_group_stats(rows)
    assert stats["scored_n"] == 1
    assert stats["mean_r"] == 1.0
    assert stats["dropped_implausible_r"] == 2


def test_r_over_twenty_fails():
    """Spec: 'a row with R>20 fails'. Absurd R is a unit error, not a win."""
    assert _scoreable_r(_row(exit_r=MAX_PLAUSIBLE_R + 0.001)) is None
    assert _scoreable_r(_row(exit_r=-(MAX_PLAUSIBLE_R + 0.001))) is None
    assert _scoreable_r(_row(exit_r=MAX_PLAUSIBLE_R)) == MAX_PLAUSIBLE_R
    assert _scoreable_r(_row(exit_r=1.5)) == 1.5


def test_scoreboard_no_longer_reports_percentages_as_r():
    """The three buckets the live board showed as +51.2 / +41.4 / +44.7."""
    records = [_row(exit_r=51.2, grade="UNKNOWN"), _row(exit_r=41.4, grade="B"), _row(exit_r=44.7, grade="A")]
    stats = compute_group_stats(records)
    assert stats["scored_n"] == 0
    assert stats["mean_r"] == 0.0
    assert stats["n"] == 3


# --- no denominator -> not scored -----------------------------------------------------------

def test_row_with_no_stop_is_dropped():
    stats = compute_group_stats([_row(stop=None, entry_stop=None, exit_r=1.2)])
    assert stats["scored_n"] == 0
    assert stats["dropped_no_stop"] == 1
    assert stats["n"] == 1, "n still counts the row; only the mean excludes it"


def test_row_with_stop_on_wrong_side_has_no_risk():
    """The live corpus defect: LONG at 822.74 with stop at 825.26 -> risk = -2.52 -> R = +124."""
    row = _row(entry_price=822.74, entry_px=822.74, stop=825.26, entry_stop=825.26, exit_r=124.1032)
    assert _risk_per_share(row) is None
    assert _scoreable_r(row) is None
    assert compute_group_stats([row])["scored_n"] == 0


def test_stop_equal_to_entry_has_no_risk():
    assert _risk_per_share(_row(stop=100.0, entry_stop=100.0)) is None


def test_short_side_stop_above_entry_is_valid():
    row = _row(side="SHORT", entry_price=100.0, stop=103.0, entry_stop=103.0, exit_r=-1.0)
    assert _risk_per_share(row) == 3.0
    assert _scoreable_r(row) == -1.0


def test_entry_stop_takes_precedence_over_plan_stop():
    row = _row(stop=None, entry_stop=95.0, exit_r=2.0)
    assert _risk_per_share(row) == 5.0
    assert _scoreable_r(row) == 2.0


# --- field precedence + junk inputs ---------------------------------------------------------

def test_pine_exit_r_wins_and_a_junk_primary_drops_the_row():
    """A mismatched primary is dropped, not silently backfilled from exit_r.

    Substituting a neighbouring field would hide the integrity problem behind a plausible number;
    dropping keeps it visible in dropped_implausible_r.
    """
    assert _scoreable_r(_row(pine_exit_r=1.25, exit_r=0.95)) == 1.25
    assert _scoreable_r(_row(pine_exit_r=99.0, exit_r=0.95)) is None
    assert compute_group_stats([_row(pine_exit_r=99.0, exit_r=0.95)])["dropped_implausible_r"] == 1


def test_replay_r_is_used_when_others_absent():
    assert _scoreable_r(_row(replay_r=-0.5)) == -0.5


@pytest.mark.parametrize("junk", ["abc", None, math.inf, -math.inf, math.nan])
def test_unusable_values_are_dropped_not_averaged(junk):
    """None / NaN / infinity / non-numeric are all dropped, and the row is still counted in n."""
    assert _scoreable_r(_row(exit_r=junk)) is None
    stats = compute_group_stats([_row(exit_r=junk), _row(exit_r=1.0)])
    assert stats["scored_n"] == 1
    assert stats["mean_r"] == 1.0
    assert stats["n"] == 2


@pytest.mark.parametrize("junk", [True, False])
def test_booleans_coerce_to_valid_r_because_bool_is_an_int(junk):
    """float(True) == 1.0, which is a legal R. Documented rather than accidental."""
    assert _scoreable_r(_row(exit_r=junk)) == float(int(junk))
    assert compute_group_stats([_row(exit_r=junk)])["scored_n"] == 1


def test_nan_never_pollutes_the_mean():
    stats = compute_group_stats([_row(exit_r=math.nan), _row(exit_r=1.0)])
    assert stats["scored_n"] == 1
    assert stats["mean_r"] == 1.0


# --- win / stop accounting only counts scored rows -------------------------------------------

def test_win_and_stop_rates_use_scored_rows_only():
    rows = [
        _row(exit_r=2.0),        # win
        _row(exit_r=-1.0),       # stop
        _row(exit_r=51.2),       # dropped
        _row(stop=None, entry_stop=None, exit_r=-3.0),  # dropped
    ]
    stats = compute_group_stats(rows)
    assert stats["scored_n"] == 2
    assert stats["win_rate"] == 50.0
    assert stats["stop_rate"] == 50.0
    assert stats["mean_r"] == 0.5
    assert stats["dropped_n"] == 2


def test_empty_group_reports_zero_not_error():
    stats = compute_group_stats([])
    assert stats == {
        "n": 0, "scored_n": 0, "mean_r": 0.0, "win_rate": 0.0, "stop_rate": 0.0,
        "dropped_n": 0, "dropped_no_stop": 0, "dropped_implausible_r": 0,
    }


# --- the bucket lookup inherits the same gate -----------------------------------------------

def test_bucket_lookup_stub_shape_matches_group_stats(tmp_path):
    stats = lookup_bucket_stats(None, None, None, db_path=tmp_path / "none.db")
    assert set(stats) >= {"n", "scored_n", "mean_r", "win_rate", "stop_rate", "dropped_n", "read", "text"}
    assert stats["read"] is False


def test_bucket_lookup_drops_junk_rows(tmp_path):
    import sqlite3
    from src.tracking import alert_db

    db = tmp_path / "intraday.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE intraday_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT, ticker TEXT NOT NULL,
            date TEXT NOT NULL, entry_ts TEXT, hour INTEGER, grade TEXT, score REAL,
            side TEXT DEFAULT 'LONG', stop REAL, entry_price REAL, entry_stop REAL,
            pine_exit_r REAL, exit_r REAL, replay_r REAL
        )
    """)
    rows = [
        ("T1", "AAPL", "2026-09-30", "09:35:00", 9, "A", 95.0, "LONG", 97.0, 100.0, 97.0, None, 1.5, None),
        ("T2", "AAPL", "2026-09-30", "09:36:00", 9, "A", 95.0, "LONG", 97.0, 100.0, 97.0, None, 88.5, None),
    ]
    conn.executemany(
        "INSERT INTO intraday_signals (trade_id,ticker,date,entry_ts,hour,grade,score,side,stop,"
        "entry_price,entry_stop,pine_exit_r,exit_r,replay_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    stats = lookup_bucket_stats("A", 9, 95.0, min_n=1, db_path=db)
    assert stats["scored_n"] == 1
    assert stats["mean_r"] == 1.5