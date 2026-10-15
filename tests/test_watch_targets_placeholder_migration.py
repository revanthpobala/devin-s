"""The placeholder migration: archive before delete, idempotent, and never again (§2.4 of the plan).

`watch_targets` is the thesis ledger. For a long time it was also universe storage: one
`status='UNRESEARCHED'` row per Schwab 1000 constituent, inserted on every read whenever the table
dipped under 500 rows. In data/research_watch.db that is 844 of 993 rows -- 85% of what the
Watchlist tab, the copilot context and the coverage Gaps card show as research results, each one a
1s job invocation per cycle, and not one of them an artifact.

The rules pinned here:
  1. the archive table gets the full row, as JSON, BEFORE the DELETE (so a rollback is possible)
  2. a second run is a no-op that reports zeros -- no double archive, no overwrite
  3. a row with a thesis (real date or real verdict) is never archived
  4. `ensure_universe_prepopulated` writes constituents to `tracked_universe`, not `watch_targets`
"""

from __future__ import annotations

import json

from src import config
from src.tracking import watch_manager as wm
from src.tracking.watch_manager import (
    _db_lock,
    _get_connection,
    count_placeholder_rows,
    init_watch_db,
    migrate_watch_target_placeholders,
)

PLACEHOLDERS = [(f"PLA{i:03d}", f"Schwab 1000 (Placeholder {i})") for i in range(6)]
# The code that wrote them used verdict='UNMEASURED'; data/research_watch.db carries
# 'UNRESEARCHED'. Both spellings must be matched, or the migration silently no-ops on production.
PLACEHOLDER_VERDICTS = ("UNMEASURED", "UNRESEARCHED")
REAL_ROWS = [
    ("MSFT", "2026-10-09", "PASS", "STALKING"),      # real thesis
    ("NVDA", "2026-10-08", "ENTER", "IN_ZONE"),      # real thesis
    ("AAPL", "", "STALK", "MISSED_RUNAWAY"),         # no date, but a real verdict: keep it
    ("TSLA", "2026-10-07", "UNRESEARCHED", "EXPIRED"),  # no thesis, but a real date: keep it
]


def _seed():
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO watch_targets (ticker, date, verdict, status, options_summary,
                                                  updated_at, is_active)
            VALUES (?, '', ?, 'UNRESEARCHED', ?, '1970-01-01T00:00:00', 1)
            """,
            [(sym, PLACEHOLDER_VERDICTS[i % 2], note) for i, (sym, note) in enumerate(PLACEHOLDERS)],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO watch_targets (ticker, date, verdict, status, updated_at, is_active)
            VALUES (?, ?, ?, ?, '2026-10-09T12:00:00', 1)
            """,
            REAL_ROWS,
        )
        conn.commit()


def _tickers_in(table: str) -> set:
    with _db_lock, _get_connection() as conn:
        return {r["ticker"] for r in conn.execute(f"SELECT ticker FROM {table}").fetchall()}


def test_archive_before_delete(isolated_databases):
    """The archive must contain the FULL row, not just the ticker. That is the rollback."""
    _seed()
    assert count_placeholder_rows() == len(PLACEHOLDERS)

    res = migrate_watch_target_placeholders()

    assert res["archived"] == len(PLACEHOLDERS)
    assert res["deleted"] == len(PLACEHOLDERS)
    assert res["remaining_placeholders"] == 0

    with _db_lock, _get_connection() as conn:
        archived = {r["ticker"]: json.loads(r["snapshot_json"])
                    for r in conn.execute("SELECT * FROM watch_targets_backup_placeholders").fetchall()}
        assert set(archived) == {sym for sym, _ in PLACEHOLDERS}
        # Every column of the row is recoverable, including the one that made it a placeholder.
        first = archived["PLA000"]
        assert first["status"] == "UNRESEARCHED"
        assert first["verdict"] == "UNMEASURED"
        assert first["date"] == ""
        assert first["options_summary"] == "Schwab 1000 (Placeholder 0)"
        assert first["updated_at"] == "1970-01-01T00:00:00"
        assert first["is_active"] == 1


def test_migration_is_idempotent(isolated_databases):
    _seed()

    first = migrate_watch_target_placeholders()
    second = migrate_watch_target_placeholders()
    third = migrate_watch_target_placeholders()

    assert first["archived"] == len(PLACEHOLDERS)
    # Nothing left to archive, and nothing deleted: no double archive, no re-archive, no churn.
    assert second == {
        "planned": 0, "archived": 0, "already_archived": 0, "deleted": 0,
        "remaining_placeholders": 0,
        "watch_targets_rows": len(REAL_ROWS),
    }
    assert third == second
    assert _tickers_in("watch_targets_backup_placeholders") == {s for s, _ in PLACEHOLDERS}


def test_archive_is_not_overwritten_on_a_second_run(isolated_databases):
    _seed()
    migrate_watch_target_placeholders()

    with _db_lock, _get_connection() as conn:
        conn.execute(
            "UPDATE watch_targets_backup_placeholders SET snapshot_json = 'tampered' WHERE ticker = 'PLA000'"
        )
        conn.commit()

    migrate_watch_target_placeholders()

    with _db_lock, _get_connection() as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM watch_targets_backup_placeholders WHERE ticker = 'PLA000'"
        ).fetchone()
    assert row["snapshot_json"] == "tampered", "a second run overwrote the existing archive"


def test_a_row_with_a_thesis_is_never_archived(isolated_databases):
    _seed()

    migrate_watch_target_placeholders()

    assert _tickers_in("watch_targets") == {"MSFT", "NVDA", "AAPL", "TSLA"}
    # ...and the archive holds only the placeholders.
    assert _tickers_in("watch_targets_backup_placeholders") == {s for s, _ in PLACEHOLDERS}


def test_dry_run_touches_nothing(isolated_databases):
    _seed()

    res = migrate_watch_target_placeholders(dry_run=True)

    assert res["planned"] == len(PLACEHOLDERS)
    assert res["archived"] == 0 and res["deleted"] == 0
    assert count_placeholder_rows() == len(PLACEHOLDERS)
    assert not _tickers_in("watch_targets_backup_placeholders")


def test_ensure_universe_prepopulated_writes_tracked_universe_not_watch_targets(
    isolated_databases, monkeypatch, tmp_path
):
    """The only real defect in the old function was using watch_targets as universe storage."""
    _seed()
    migrate_watch_target_placeholders()

    (tmp_path / "All_tickrs").mkdir(parents=True)
    (tmp_path / "All_tickrs" / "Schwab_1000_Index®_Index_Constituents.csv").write_text(
        "Ticker,Name,IndexWeighting\nAAA,Alpha Corp,0.50\nBBB,Beta Corp,0.30\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr("src.screener.universe._etf_constituents", lambda etf: {})

    total = wm.ensure_universe_prepopulated()

    assert total == 2
    assert _tickers_in("tracked_universe") == {"AAA", "BBB"}
    # The universe run must not put a single thesis-less row back into the ledger.
    assert _tickers_in("watch_targets") == {"MSFT", "NVDA", "AAPL", "TSLA"}
    assert count_placeholder_rows() == 0


def test_prepopulated_then_migrated_then_prepopulated_is_stable(
    isolated_databases, monkeypatch, tmp_path
):
    """The whole loop: universe in tracked_universe, thesis in watch_targets, no placeholders."""
    _seed()
    migrate_watch_target_placeholders()
    (tmp_path / "All_tickrs").mkdir(parents=True)
    (tmp_path / "All_tickrs" / "Schwab_1000_Index®_Index_Constituents.csv").write_text(
        "Ticker,Name,IndexWeighting\nMSFT,Microsoft,0.50\nZZZ,Zeta,0.30\n", encoding="utf-8",
    )
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr("src.screener.universe._etf_constituents", lambda etf: {})

    wm.ensure_universe_prepopulated()
    after_first = wm.ensure_universe_prepopulated()

    assert after_first == 2
    assert _tickers_in("tracked_universe") == {"MSFT", "ZZZ"}
    assert _tickers_in("watch_targets") == {"MSFT", "NVDA", "AAPL", "TSLA"}
    assert count_placeholder_rows() == 0


def test_init_watch_db_creates_the_two_new_tables(isolated_databases):
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "tracked_universe" in tables
    assert "watch_targets_backup_placeholders" in tables


def test_migration_on_a_fresh_db(isolated_databases):
    """Nothing to do on an empty database, and no crash."""
    init_watch_db()
    res = migrate_watch_target_placeholders()
    assert res["planned"] == 0
    assert res["archived"] == 0
