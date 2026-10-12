"""Identity backfill tests (§2.3 of the plan): thesis_id / created_date / expires_on.

watch_targets.thesis_id is 0/993 populated in production, and it is the dedupe key. The paper
ledger joins on (ticker, thesis_id); run_watch_alerts writes `thesis_id` as "live" when the row
already has one. A row with no identity therefore reads as a brand-new thesis every time it is
looked at, which is the mechanism behind "856 rows read live-active while only 239 ever had a
filled paper trade" and "one fresh thesis appears twice with conflicting outcomes".

The derivation must match upsert_watch_target exactly or the backfill creates a second, parallel
identity space.
"""

from __future__ import annotations

from src.tracking.watch_manager import (
    _db_lock,
    _get_connection,
    backfill_watch_target_identity,
    count_placeholder_rows,
    init_watch_db,
    migrate_watch_target_placeholders,
    upsert_watch_target,
)

# No thesis_id, no created_date, no expires_on: the shape of every row written before those
# columns existed.
LEGACY_ROWS = [
    ("MSFT", "2026-10-05", "PASS", "STALKING", "2026-10-05T09:00:00"),
    ("NVDA", "2026-10-07", "ENTER", "IN_ZONE", "2026-10-07T10:30:00"),
    ("AAPL", "2026-09-30", "STALK", "TARGET_HIT", "2026-09-30T15:00:00"),
]
PLACEHOLDERS = [("PLA000", "UNRESEARCHED"), ("PLA001", "UNRESEARCHED")]


def _seed():
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO watch_targets (ticker, date, verdict, status, updated_at,
                                                  entry_zone_low, tactical_stop, target_1, is_active)
            VALUES (?, ?, ?, ?, ?, 380.0, 375.0, 420.0, 1)
            """,
            [(sym, date, verdict, status, updated) for sym, date, verdict, status, updated in LEGACY_ROWS],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO watch_targets (ticker, date, verdict, status, updated_at, is_active)
            VALUES (?, '', 'UNRESEARCHED', 'UNRESEARCHED', '1970-01-01T00:00:00', 1)
            """,
            [(sym,) for sym, _ in PLACEHOLDERS],
        )
        conn.commit()


def _row(ticker: str) -> dict:
    with _db_lock, _get_connection() as conn:
        r = conn.execute(
            "SELECT ticker, date, created_date, thesis_id, expires_on, status FROM watch_targets WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        return dict(r) if r else {}


def test_backfill_assigns_the_identity_the_writer_would_have(isolated_databases):
    _seed()

    res = backfill_watch_target_identity()

    assert res["updated"] == len(LEGACY_ROWS)
    assert res["missing_after"] == 0

    msft = _row("MSFT")
    assert msft["thesis_id"] == "MSFT_2026-10-05"
    assert msft["created_date"] == "2026-10-05"
    # 5 trading days after Monday 2026-10-05 is Monday 2026-10-12.
    assert msft["expires_on"] == "2026-10-12"

    nvda = _row("NVDA")
    assert nvda["thesis_id"] == "NVDA_2026-10-07"
    assert nvda["created_date"] == "2026-10-07"
    # Wednesday -> following Wednesday.
    assert nvda["expires_on"] == "2026-10-14"

    assert _row("AAPL")["thesis_id"] == "AAPL_2026-09-30"


def test_backfill_matches_a_freshly_written_row(isolated_databases):
    """One identity space: a backfilled row must not look different from a new one."""
    _seed()
    backfill_watch_target_identity()

    upsert_watch_target({
        "ticker": "AMD",
        "date": "2026-10-06",
        "verdict": "STALK",
        "side": "LONG",
        "shares_plan": {
            "side": "LONG",
            "entry_zone_low": 200.0,
            "entry_zone_high": 202.0,
            "tactical_stop": 195.0,
            "target_1": 215.0,
            "target_2": 225.0,
            "entry_type": "LIMIT",
        },
        "status": "STALKING",
    })

    backfilled = _row("AMD")
    assert backfilled["thesis_id"] == "AMD_2026-10-06"
    assert backfilled["created_date"] == "2026-10-06"

    # The same shape as the row the backfill produced for MSFT: ticker_created_date, both dated.
    assert backfilled["thesis_id"].count("_") == _row("MSFT")["thesis_id"].count("_")
    assert backfilled["expires_on"] == "2026-10-13"


def test_backfill_is_idempotent(isolated_databases):
    _seed()

    first = backfill_watch_target_identity()
    before = {t: _row(t) for t, *_ in LEGACY_ROWS}
    second = backfill_watch_target_identity()

    assert first["updated"] == len(LEGACY_ROWS)
    assert second["updated"] == 0, "a second pass re-assigned identities instead of leaving them"
    for t in before:
        assert _row(t) == before[t], f"{t} identity churned on re-run"


def test_backfill_skips_placeholders(isolated_databases):
    _seed()

    backfill_watch_target_identity()

    for ticker, _ in PLACEHOLDERS:
        row = _row(ticker)
        assert row.get("thesis_id") in (None, ""), f"{ticker} is a placeholder and is owned by the migration"
    assert count_placeholder_rows() == len(PLACEHOLDERS)


def test_backfill_does_not_touch_placeholder_migration(isolated_databases):
    """The two migrations must not fight: identity backfill then archive, and back the other way."""
    _seed()
    backfill_watch_target_identity()
    migrate_watch_target_placeholders()
    assert count_placeholder_rows() == 0

    _seed()
    migrate_watch_target_placeholders()
    backfill_watch_target_identity()
    assert count_placeholder_rows() == 0
    assert _row("MSFT")["thesis_id"] == "MSFT_2026-10-05"


def test_backfill_respects_an_existing_created_date(isolated_databases):
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO watch_targets (ticker, date, created_date, verdict, status, updated_at, is_active)
            VALUES ('CRWD', '2026-10-10', '2026-10-01', 'STALK', 'STALKING', '2026-10-10T09:00:00', 1)
            """
        )
        conn.commit()

    backfill_watch_target_identity()

    row = _row("CRWD")
    # created_date wins over date, exactly as upsert_watch_target computes it.
    assert row["created_date"] == "2026-10-01"
    assert row["thesis_id"] == "CRWD_2026-10-01"
    assert row["expires_on"] == "2026-10-08"


def test_backfill_ignores_the_1970_placeholder_timestamp(isolated_databases):
    """A row with no date and a 1970 sentinel updated_at must not get a 1970 thesis."""
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        conn.execute(
            """
            INSERT INTO watch_targets (ticker, date, verdict, status, updated_at, is_active)
            VALUES ('DDOG', '', 'STALK', 'STALKING', '1970-01-01T00:00:00', 1)
            """
        )
        conn.commit()

    backfill_watch_target_identity()

    row = _row("DDOG")
    assert row["thesis_id"].startswith("DDOG_")
    assert "1970" not in row["thesis_id"], "the placeholder sentinel leaked into the thesis id"
    assert row["created_date"] >= "2020-01-01"


def test_dry_run_changes_nothing(isolated_databases):
    _seed()

    res = backfill_watch_target_identity(dry_run=True)

    assert res["planned"] == len(LEGACY_ROWS)
    assert res["updated"] == 0
    assert _row("MSFT").get("thesis_id") in (None, "")
