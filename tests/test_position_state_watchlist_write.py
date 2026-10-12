"""Regression test for the watch_targets exit-capture hole (§0.1 of the desk/watchlist plan).

`close_position()` used to run

    UPDATE watch_targets SET status='COMPLETED', exit_price=?, r_realized=? WHERE rowid=?

against a table that has neither `exit_price` nor `r_realized`. sqlite3 raised
OperationalError, the caller's broad `except` swallowed it, and every close silently failed to
record its outcome in the watchlist -- which is what the calibration panel's "only 22% of closed
trades recorded an exit price" was actually measuring.

These tests assert the row really changes, through both the rowid path (a position opened from the
trades table, which carries a watch_target_id) and the ticker fallback (a position opened by the
alert tracker, which carries none).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

from src import config
from src.tracking import position_state
from src.tracking.watch_manager import _db_lock, _get_connection


def _watch_row(ticker: str) -> dict:
    conn = sqlite3.connect(str(config.research_watch_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT rowid AS id, * FROM watch_targets WHERE ticker = ?", (ticker,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _seed_watch_target(ticker: str = "MSFT") -> int:
    """Insert one real-schema watch target; return its rowid (what the trades UI passes)."""
    from src.tracking.watch_manager import _db_lock, _get_connection

    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO watch_targets (ticker, date, verdict, status, side, tactical_stop,
                                           target_1, updated_at)
                VALUES (?, '2026-10-09', 'PASS', 'STALKING', 'LONG', 380.0, 420.0, ?)
                """,
                (ticker, position_state._now_iso()),
            )
            rowid = cur.lastrowid
            conn.commit()
    return int(rowid)


def test_close_position_writes_the_real_watch_target_columns(tmp_path):
    fake_positions = tmp_path / "positions.json"
    rowid = _seed_watch_target("MSFT")

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "MSFT",
            side="LONG",
            strategy="Swing",
            entry_price=400.0,
            stop=390.0,
            target=420.0,
            watch_target_id=rowid,
        )
        before = _watch_row("MSFT")
        assert before["status"] == "STALKING"
        assert before["last_price"] is None

        closed = position_state.close_position("MSFT", exit_price=425.0, exit_reason="TARGET_2_HIT")
        assert closed is not None

    after = _watch_row("MSFT")
    assert after["id"] == rowid, "the rowid path must update the row the trades UI handed us"
    assert after["status"] == "COMPLETED"
    assert after["last_price"] == 425.0
    # Verdict vocabulary matches what run_watch_alerts writes for a target hit.
    assert after["evaluation_verdict"] == "TARGET_HIT"
    assert after["updated_at"] >= before["updated_at"]


def test_close_position_without_watch_target_id_still_captures_the_exit(tmp_path):
    """Tracker-opened positions carry no watch_target_id; the close must not be dropped."""
    fake_positions = tmp_path / "positions.json"
    _seed_watch_target("NVDA")

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "NVDA", side="LONG", strategy="Intraday", entry_price=100.0, stop=95.0, target=110.0
        )
        assert position_state.close_position("NVDA", exit_price=92.0, exit_reason="STOP_BREACHED")

    after = _watch_row("NVDA")
    assert after["status"] == "COMPLETED"
    assert after["last_price"] == 92.0
    assert after["evaluation_verdict"] == "STAND_ASIDE"


def test_close_position_writes_the_suggestions_columns_that_exist(tmp_path):
    """`suggestions` has exit_price / r_net / exit_reason, not r_realized."""
    fake_positions = tmp_path / "positions.json"

    from src.tracking.suggestions_ledger import ensure_suggestions_schema
    from src.tracking.watch_manager import _db_lock, _get_connection

    with _db_lock:
        with _get_connection() as conn:
            ensure_suggestions_schema(conn)
            conn.execute(
                "INSERT INTO suggestions (ticker, date, source, gate_status, entry_low, entry_high, stop, target_1)"
                " VALUES ('AAPL', '2026-10-09', 'test', 'PASS', 150.0, 152.0, 145.0, 165.0)"
            )
            sugg_id = conn.execute(
                "SELECT id FROM suggestions WHERE ticker = 'AAPL'"
            ).fetchone()[0]
            conn.commit()

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "AAPL",
            side="LONG",
            strategy="Swing",
            entry_price=150.0,
            stop=145.0,
            target=165.0,
            suggestion_id=sugg_id,
        )
        position_state.close_position("AAPL", exit_price=170.0, exit_reason="TARGET_2_HIT")

    with _db_lock:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute("SELECT * FROM suggestions WHERE id = ?", (sugg_id,)).fetchone())

    assert row["exit_price"] == 170.0
    assert row["exit_reason"] == "TARGET_2_HIT"
    assert row["exit_date"] is not None
    assert row["r_net"] is not None


def test_close_position_never_marks_a_placeholder_as_a_completed_thesis(tmp_path):
    """A tracker-opened close must not rewrite a placeholder row into a completed thesis.

    watch_targets is keyed by ticker, and 844 of its rows are universe placeholders (no date, no
    levels) covering the whole Schwab 1000. An unrestricted `WHERE ticker = ?` fallback in
    close_position flipped one of those into status='COMPLETED' with a price and a verdict, which
    the read filters then published as a real closed trade that never existed.
    """
    from src.tracking.watch_manager import placeholder_rows_sql

    fake_positions = tmp_path / "positions.json"
    # 'PLTR' exists only as a placeholder -- verified shape of 844 live rows.
    with _db_lock, _get_connection() as conn:
        conn.execute(
            "INSERT INTO watch_targets (ticker, date, verdict, status, updated_at, is_active) "
            "VALUES ('PLTR', '', 'UNRESEARCHED', 'UNRESEARCHED', '1970-01-01T00:00:00', 1)"
        )
        conn.commit()
    assert "PLTR" in [r["ticker"] for r in _all_tickers()]

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "PLTR", side="LONG", strategy="Intraday", entry_price=50.0, stop=48.0, target=55.0
        )
        position_state.close_position("PLTR", exit_price=60.0, exit_reason="MANUAL_CLOSE")

    after = _watch_row("PLTR")
    assert after["status"] == "UNRESEARCHED", (
        "close_position rewrote a placeholder into a closed thesis"
    )
    assert after["last_price"] is None
    assert after["evaluation_verdict"] is None
    # The guard is the shared predicate, not a hand-rolled CHECK.
    assert "upper(status)" in placeholder_rows_sql(active_only=False)


def _all_tickers():
    with _db_lock, _get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT ticker FROM watch_targets").fetchall()]


def test_close_position_falls_back_to_a_real_thesis_row(tmp_path):
    """The fallback still works where a thesis exists and carries a date."""
    fake_positions = tmp_path / "positions.json"
    with _db_lock, _get_connection() as conn:
        conn.execute(
            "INSERT INTO watch_targets (ticker, date, verdict, status, updated_at, is_active) "
            "VALUES ('CRM', '2026-10-09', 'PASS', 'IN_ZONE', ?, 1)",
            (position_state._now_iso(),),
        )
        conn.commit()

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "CRM", side="LONG", strategy="Swing", entry_price=200.0, stop=190.0, target=220.0
        )
        position_state.close_position("CRM", exit_price=230.0, exit_reason="TARGET_2_HIT")

    after = _watch_row("CRM")
    assert after["status"] == "COMPLETED"
    assert after["last_price"] == 230.0
    assert after["evaluation_verdict"] == "TARGET_HIT"


def test_close_position_with_no_linked_rows_is_silent(tmp_path):
    """No suggestion and no watch row: the close still succeeds and throws nothing."""
    fake_positions = tmp_path / "positions.json"

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "PLTR", side="LONG", strategy="Intraday", entry_price=50.0, stop=48.0, target=55.0
        )
        rec = position_state.close_position("PLTR", exit_price=60.0, exit_reason="MANUAL_CLOSE")

    assert rec["ticker"] == "PLTR"
    assert rec["exit_price"] == 60.0
