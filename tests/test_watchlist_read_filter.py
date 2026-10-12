"""Read-side filter for the universe placeholder rows (§2.1 of the desk/watchlist plan).

`ensure_universe_prepopulated` used `watch_targets` as universe storage, seeding one row per
Schwab 1000 constituent with `status='UNRESEARCHED'`. The Watchlist tab, the copilot context and
the coverage Gaps card all read that table, so 844 rows with no thesis behind them were presented
as research results -- 85% of the row count on the Watchlist tab, and the reason the Gaps card
called the whole universe unresearched.

These tests pin that the placeholders are filtered out of every read path, and that the
self-healing repopulate (which used to undo any prune on the next `GET /api/watch-targets`) is
gone for good.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from src import config
from src.tracking.watch_manager import _db_lock, _get_connection
from src.ui.app import create_app


def _seed_targets():
    from src.tracking.watch_manager import _now_iso

    now = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO watch_targets (ticker, date, status, verdict, side, tactical_stop,
                                                     target_1, updated_at)
                VALUES (?, ?, ?, 'PASS', 'LONG', 380.0, 420.0, ?)
                """,
                [
                    ("MSFT", "2026-10-09", "STALKING", now),   # real thesis
                    ("NVDA", "2026-10-08", "IN_ZONE", now),    # real thesis
                ],
            )
            # The 844 placeholders: no date, no verdict, no thesis anywhere.
            conn.executemany(
                """
                INSERT OR REPLACE INTO watch_targets (ticker, date, status, verdict, updated_at)
                VALUES (?, '', 'UNRESEARCHED', 'UNMEASURED', ?)
                """,
                [(f"PLA{i:03d}", now) for i in range(12)],
            )
            conn.commit()


def _status_counts():
    conn = sqlite3.connect(str(config.research_watch_db_path()))
    try:
        return dict(
            conn.execute("SELECT status, COUNT(*) FROM watch_targets GROUP BY status").fetchall()
        )
    finally:
        conn.close()


def test_watch_targets_read_filters_out_placeholders(isolated_databases):
    client = TestClient(create_app())
    _seed_targets()

    res = client.get("/api/watch-targets")
    assert res.status_code == 200
    body = res.json()

    tickers = [t["ticker"] for t in body["targets"]]
    assert tickers == ["MSFT", "NVDA"], f"placeholders leaked into /api/watch-targets: {tickers[:5]}"
    assert body["count"] == 2
    assert all(t.get("status") != "UNRESEARCHED" for t in body["targets"])
    # The rows are still in the table -- this is a read filter, not a delete.
    assert _status_counts()["UNRESEARCHED"] == 12


def test_the_placeholder_self_heal_is_disabled(isolated_databases, monkeypatch):
    """The guard that used to re-seed all 844 placeholders whenever the table dipped under 500.

    Without this the migration in §2.2 is untestable: delete a placeholder, refresh, watch it come
    back. Pin both directions -- no repopulate on read, and delete actually sticks.
    """
    client = TestClient(create_app())
    _seed_targets()

    # Simulate the migration having run: the placeholders are gone.
    conn = sqlite3.connect(str(config.research_watch_db_path()))
    conn.execute("DELETE FROM watch_targets WHERE status = 'UNRESEARCHED'")
    conn.commit()
    conn.close()
    assert "UNRESEARCHED" not in _status_counts()

    res = client.get("/api/watch-targets")
    assert res.status_code == 200
    assert [t["ticker"] for t in res.json()["targets"]] == ["MSFT", "NVDA"]
    # ...and the read did not bring them back.
    assert "UNRESEARCHED" not in _status_counts(), "the read re-created the placeholder rows"


def test_copilot_context_does_not_list_placeholders(isolated_databases):
    from src.ui.services import copilot_context

    _seed_targets()

    ctx, tickers = copilot_context._build_daily_overview_context()
    assert "PLA000" not in ctx, "the copilot context is handing the model placeholder rows"
    assert "PLA011" not in ctx
    assert "MSFT" in ctx, "the real thesis rows must still be in the context"
    assert "PLA000" not in tickers and "MSFT" in tickers


def test_coverage_gaps_ignores_placeholders(isolated_databases):
    from src.ui.routes import status as status_routes

    _seed_targets()

    res = status_routes.get_all_tickers_endpoint()
    assert "PLA000" not in res["tickers"]
    assert "MSFT" in res["tickers"]


def test_sync_universe_still_works_on_demand(isolated_databases, monkeypatch):
    """The manual repopulate path must survive: the read path no longer self-heals it."""
    client = TestClient(create_app())
    _seed_targets()

    calls = []
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(wm, "ensure_universe_prepopulated", lambda: calls.append(1) or 7)
    res = client.post("/api/watch-targets/sync-universe")
    assert res.status_code == 200
    assert res.json()["total_constituents"] == 7
    assert calls == [1]
