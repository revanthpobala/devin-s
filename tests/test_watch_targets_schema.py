"""The watch_targets schema contract (§3.1 of the plan).

`init_watch_db()` owns a 28-branch migration ladder at `src/tracking/watch_manager.py:80-136`.
Nothing tested it, which is exactly what makes a schema change safe to attempt and easy to get
wrong: `CREATE TABLE IF NOT EXISTS` means a table that already exists is never re-created, so the
only thing standing between a legacy database and a broken column is one of those branches, and a
branch that silently does nothing produces an `sqlite3.OperationalError: no such column` at a call
site nobody is looking at -- which is how `close_position()` came to update `watch_targets`
`exit_price` / `r_realized` (neither a real column) into the calibration panel's "only 22% of
closed trades recorded an exit price".

This file pins three things:
  * the exact 51-column set, in order, that a fresh database must end up with
  * that every ladder column comes BACK after an `ALTER TABLE ... DROP COLUMN`
  * that a legacy table missing all of them ends up with the same schema as a fresh create

Run: pytest tests/test_watch_targets_schema.py
"""

from __future__ import annotations

import pytest

from src.tracking import watch_manager as wm
from src.tracking.watch_manager import _db_lock, _get_connection, init_watch_db

# The full column set, in PRAGMA order, as a fresh database must present it. Anything added to
# init_watch_db() shows up here as a failure, which is the point. The order is the CREATE order
# followed by the ALTER ladder order, and it is asserted too: a branch that moves changes the
# physical layout of every existing row.
EXPECTED_COLUMNS = [
    "ticker", "date", "verdict", "conviction", "actionable", "side", "entry_type",
    "entry_zone_low", "entry_zone_high", "breakout_level", "breakout_stop",
    "tactical_stop", "target_1", "target_2", "options_structure", "options_summary",
    "options_actionable", "options_entry_trigger", "invalidation_price",
    "invalidation_condition", "invalidation_rationale", "last_price",
    "distance_to_entry_pct", "status", "last_alert_type", "last_alert_at", "updated_at",
    "raw_json", "signal_pack", "fade", "action_long", "ext_z", "atr_at_signal",
    "zone_rr_flags", "rr_at_market_at_signal", "is_active", "user_taken", "suggestion_id",
    "fill_price", "quantity", "evaluation_verdict", "evaluation_playbook",
    "evaluation_time", "is_actionable_now", "thesis_id", "created_date", "expires_on",
    "history_key", "bars_in_status", "breakout_target_1", "breakout_target_2",
]

# Every column a migration-ladder branch adds. The first five overlap the CREATE -- that is how
# the CREATE grew: a branch was added, then folded into the CREATE for new databases. The last two
# (history_key, bars_in_status) exist in data/research_watch.db but in neither place, which is why
# they are in this list and in the ladder.
MIGRATED_COLUMNS = [
    "side", "breakout_level", "breakout_stop", "options_actionable", "options_entry_trigger",
    "is_active", "user_taken", "suggestion_id", "fill_price", "quantity",
    "evaluation_verdict", "evaluation_playbook", "evaluation_time", "is_actionable_now",
    "thesis_id", "created_date", "expires_on", "signal_pack", "fade", "action_long",
    "ext_z", "atr_at_signal", "zone_rr_flags", "rr_at_market_at_signal",
    "history_key", "bars_in_status", "breakout_target_1", "breakout_target_2",
]

# The columns a table created by the very first version of the CREATE had, before it grew.
LEGACY_CREATE_COLUMNS = [
    "ticker", "date", "verdict", "conviction", "actionable", "side", "entry_type",
    "entry_zone_low", "entry_zone_high", "breakout_level", "breakout_stop",
    "tactical_stop", "target_1", "target_2", "options_structure", "options_summary",
    "options_actionable", "options_entry_trigger", "invalidation_price",
    "invalidation_condition", "invalidation_rationale", "last_price",
    "distance_to_entry_pct", "status", "last_alert_type", "last_alert_at", "updated_at",
    "raw_json",
]


def _columns() -> list[str]:
    with _get_connection() as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(watch_targets)").fetchall()]


def _column_names_not_in_pragma() -> set[str]:
    return set(EXPECTED_COLUMNS) ^ set(_columns())


def test_the_pin_covers_every_branch_in_the_source():
    """A new ladder branch must fail here, not silently widen the schema.

    Parsed from source rather than listed by hand twice: if init_watch_db() grows a column, this
    test fails until MIGRATED_COLUMNS and EXPECTED_COLUMNS are updated with it, which is the only
    way the contract stays honest.
    """
    import re
    from pathlib import Path

    src = Path(wm.__file__).read_text(encoding="utf-8")
    block = src[src.index("CREATE TABLE IF NOT EXISTS watch_targets ("):src.index("CREATE TABLE IF NOT EXISTS last_researched")]
    branches = re.findall(
        r'if "([a-z_0-9]+)" not in existing_cols:\s*\n\s*cursor\.execute\(\s*"ALTER TABLE watch_targets ADD COLUMN',
        block,
    )

    assert sorted(branches) == sorted(MIGRATED_COLUMNS), (
        "the ladder branches in init_watch_db() changed; update MIGRATED_COLUMNS and "
        f"EXPECTED_COLUMNS. in source: {sorted(branches)}\n pinned:  {sorted(MIGRATED_COLUMNS)}"
    )
    assert len(MIGRATED_COLUMNS) == 28, (
        "the ladder grew or shrank; this number is the reason the schema test exists"
    )


@pytest.fixture()
def watch_db(isolated_databases):
    """A fresh, fully-migrated watch database in this test's own directory."""
    init_watch_db()
    return None


def test_fresh_create_has_the_pinned_column_set(watch_db):
    assert _columns() == EXPECTED_COLUMNS, (
        "watch_targets column set changed. Expected\n  " + "\n  ".join(EXPECTED_COLUMNS) +
        "\nGot\n  " + "\n  ".join(_columns())
    )


@pytest.mark.parametrize("column", MIGRATED_COLUMNS)
def test_init_watch_db_restores_every_dropped_ladder_column(watch_db, column):
    """Each ladder branch must be able to bring its column back.

    This is the whole reason the ladder exists: `CREATE TABLE IF NOT EXISTS` will not re-create an
    existing table, so the ALTER branches are the only repair path, and a branch that no-ops leaves
    a missing column that surfaces later as an UPDATE naming a column that isn't there -- exactly
    what close_position did with exit_price / r_realized on watch_targets.

    Only the ladder columns are parametrised. A column that the CREATE itself defines is guaranteed
    by the CREATE, and cannot be re-added once the table exists (pinned separately below).
    """
    with _db_lock, _get_connection() as conn:
        conn.execute(f"ALTER TABLE watch_targets DROP COLUMN {column}")
        conn.commit()
    assert column not in _columns()

    init_watch_db()

    # Order is not asserted here: SQLite appends a re-added column at the end, so the physical
    # position moves while the schema itself is whole again. The pinned order is asserted once, on
    # the fresh-create path, where the layout is deterministic.
    assert set(_columns()) == set(EXPECTED_COLUMNS), (
        f"{column} was not restored by init_watch_db(); the migration ladder is missing a branch"
    )


def test_the_create_columns_are_the_ones_the_ladder_cannot_restore(watch_db):
    """Documents the boundary, and why the CREATE must only ever grow.

    A table that already exists is never re-created, so a column missing from the CREATE cannot be
    repaired by the ladder. The CREATE therefore has to keep every column a legacy table might be
    missing -- which is what made `signal_pack` (present in both the CREATE and the ladder) the
    right pattern, and `history_key` (present in production, in neither) the wrong one before the
    ladder grew those two branches.
    """
    create_only = [c for c in LEGACY_CREATE_COLUMNS if c not in MIGRATED_COLUMNS]
    assert create_only, "the legacy create columns should not all be ladder columns"

    for column in create_only:
        if column in ("ticker", "date"):
            continue        # part of the primary key: SQLite forbids dropping it at all
        with _db_lock, _get_connection() as conn:
            conn.execute(f"ALTER TABLE watch_targets DROP COLUMN {column}")
            conn.commit()
        init_watch_db()
        # Not restorable by design; the schema is permanently short of it.
        assert column not in _columns()
        # Restore by hand so the next iteration starts from a whole table.
        with _db_lock, _get_connection() as conn:
            conn.execute(f"ALTER TABLE watch_targets ADD COLUMN {column} TEXT")
            conn.commit()


@pytest.mark.parametrize("column", MIGRATED_COLUMNS)
def test_init_watch_db_is_idempotent_over_the_ladder(watch_db, column):
    """Second run must be a no-op, not a duplicate-column error."""
    init_watch_db()
    assert column in _columns()
    assert _columns().count(column) == 1


def test_a_legacy_table_migrates_up_to_the_same_schema():
    """A database created by the first version of the CREATE ends up identical to a fresh one."""
    init_watch_db()
    with _db_lock, _get_connection() as conn:
        conn.execute("DROP TABLE watch_targets")
        conn.commit()

    cols = ",\n    ".join([f"{c} {'BOOLEAN' if c in ('actionable','options_actionable') else 'TEXT'}" for c in LEGACY_CREATE_COLUMNS])
    with _db_lock, _get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE watch_targets (
                {cols},
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.commit()

    assert _columns() == LEGACY_CREATE_COLUMNS
    init_watch_db()
    # Identical as a schema: the same 51 columns, same types, same primary key. The physical order
    # differs (a legacy table has 28 CREATE columns then the ladder; a fresh one has 35 CREATE
    # columns then the ladder) and that is inherent to ALTER, not a divergence in the contract.
    assert sorted(_columns()) == sorted(EXPECTED_COLUMNS), (
        "migrate-up produced a different schema than fresh-create:\n  "
        + "\n  ".join(
            f"{c}: fresh={'yes' if c in EXPECTED_COLUMNS else 'NO'} "
            f"migrated={'yes' if c in _columns() else 'NO'}"
            for c in set(EXPECTED_COLUMNS) | set(_columns())
        )
    )
    assert set(LEGACY_CREATE_COLUMNS) < set(EXPECTED_COLUMNS)


def test_two_init_runs_do_not_change_the_schema(watch_db):
    before = _columns()
    init_watch_db()
    init_watch_db()
    assert _columns() == before


def test_every_migrated_column_is_reachable_from_the_pinned_set(watch_db):
    """A column in the ladder but not in the pin would mean the pin is stale, or the ladder is."""
    missing = set(MIGRATED_COLUMNS) - set(EXPECTED_COLUMNS)
    assert not missing, f"the ladder migrates columns the pin does not know about: {sorted(missing)}"


def test_init_watch_db_on_an_empty_database(isolated_databases):
    """No watch_targets at all: init must create it, not fail on the ALTERs."""
    init_watch_db()
    assert _columns() == EXPECTED_COLUMNS


def test_the_two_new_tables_are_part_of_the_schema(watch_db):
    with _get_connection() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for expected in ("tracked_universe", "watch_targets_backup_placeholders"):
        assert expected in tables, f"{expected} missing from init_watch_db()"
