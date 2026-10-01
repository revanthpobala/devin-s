"""
Global test isolation for the live trading databases.

Without this, a full `pytest` run wrote into production data. Measured: `data/trading_alerts.db`
and `data/research_watch.db` were both mutated, and `test_exit_veto_engine.py` /
`test_position_state.py` grew `trading_alerts.db` by two pages, because they drive
`PositionMonitor._tick()` -> `upsert_intraday_signal()` against whatever `DB_PATH` was bound.

That is not just a dirty working tree. `intraday_signals` is the table the intraday scoreboard
reads, so a test run injects synthetic rows into the same records the Go/No-Go verdict is computed
from. It is Bug 2 (R units) one level up.

Two mechanisms, because one is not enough:

  * `pytest_configure` sets ALERT_DB_PATH / STOCK_DB_PATH BEFORE collection. Several modules bind
    `DB_PATH` by value at import time (`auto_triage_daemon` does), so patching the module attribute
    later leaves those copies pointing at production.
  * the autouse fixture re-points the module attributes per test for real isolation.

A test that genuinely needs production data marks itself `@pytest.mark.real_db`, which is
deliberately awkward so the case has to be argued for rather than reached by accident.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Modules that bind DB_PATH by value at import time. Patching the source module's attribute is not
# enough for these; they must be re-pointed too or a test writes to production through the back door.
_VALUE_CAPTURED = (
    ("src.tracking.auto_triage_daemon", "DB_PATH"),
)

_SESSION_DIR = None
_TEMPLATE = None          # dir holding a pre-built schema, copied per test


def pytest_configure(config):
    """Point every DB resolver at a scratch directory before anything is imported."""
    global _SESSION_DIR
    _SESSION_DIR = tempfile.mkdtemp(prefix="stock_suite_")
    os.environ["ALERT_DB_PATH"] = os.path.join(_SESSION_DIR, "trading_alerts.db")
    os.environ["STOCK_DB_PATH"] = os.path.join(_SESSION_DIR, "research_watch.db")

    config.addinivalue_line(
        "markers",
        "real_db: test reads or writes the live data/ databases. Off by default on purpose.",
    )


def pytest_unconfigure(config):
    if _SESSION_DIR and os.path.isdir(_SESSION_DIR):
        shutil.rmtree(_SESSION_DIR, ignore_errors=True)


def _build_schema(root: Path):
    """Create the full schema once. Copying it per test is far cheaper than re-running CREATE TABLE
    IF NOT EXISTS for every table on every one of the ~870 tests."""
    import src.tracking.alert_db as alert_db
    import src.tracking.watch_manager as watch_manager
    from src.tracking.suggestions_ledger import ensure_suggestions_schema
    from src.ui import state as ui_state

    # All three resolvers must land in the SAME directory. ui_state resolves STOCK_DB_PATH from the
    # environment while watch_manager reads its module attribute, so setting one and not the other
    # splits the schema across two files -- which is how `active_research_jobs` went missing from
    # the template and every consumer of it failed.
    os.environ["ALERT_DB_PATH"] = str(root / "trading_alerts.db")
    os.environ["STOCK_DB_PATH"] = str(root / "research_watch.db")
    alert_db.DB_PATH = root / "trading_alerts.db"
    watch_manager.DB_PATH = root / "research_watch.db"

    alert_db.init_db()
    ui_state.init_db()
    watch_manager.init_watch_db()

    conn = watch_manager._get_connection()
    try:
        ensure_suggestions_schema(conn)
        conn.commit()
        # Fold the WAL into the main file so a plain file copy is a complete, consistent database.
        # Without this the -wal stays locked by an open handle and the copy is missing committed
        # rows -- on Windows the unlink raises PermissionError.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()

    for suffix in ("-wal", "-shm"):
        for base in ("research_watch.db", "trading_alerts.db"):
            try:
                stale = root / f"{base}{suffix}"
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass          # still held open; harmless, SQLite recreates it per test


def _copy_schema(template: Path, root: Path):
    """Copy the template DBs into this test's directory."""
    for name in ("trading_alerts.db", "research_watch.db"):
        src = template / name
        if not src.exists():
            raise RuntimeError(f"schema template missing {name}; build failed")
        shutil.copy(src, root / name)


@pytest.fixture(scope="session", autouse=True)
def schema_template():
    global _TEMPLATE
    _TEMPLATE = Path(tempfile.mkdtemp(prefix="stock_schema_"))
    _build_schema(_TEMPLATE)
    yield _TEMPLATE
    shutil.rmtree(_TEMPLATE, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_databases(request, tmp_path):
    """Give every test its own copy of each shared database.

    Applied suite-wide rather than per-file, because the failure mode is silent: a test that opens
    the right module but forgets to redirect the path still writes to production, and nothing in the
    run looks wrong.
    """
    if request.node.get_closest_marker("real_db"):
        yield
        return

    import importlib

    import src.tracking.alert_db as alert_db
    import src.tracking.watch_manager as watch_manager

    root = tmp_path / "db"
    root.mkdir(parents=True, exist_ok=True)
    alert_target = root / "trading_alerts.db"
    watch_target = root / "research_watch.db"

    saved = {
        "alert": alert_db.DB_PATH,
        "watch": watch_manager.DB_PATH,
        "env_alert": os.environ.get("ALERT_DB_PATH"),
        "env_watch": os.environ.get("STOCK_DB_PATH"),
    }

    if _TEMPLATE is not None:
        _copy_schema(_TEMPLATE, root)
    else:                       # a test asked for isolation before the template fixture ran
        _build_schema(root)

    alert_db.DB_PATH = alert_target
    watch_manager.DB_PATH = watch_target
    os.environ["ALERT_DB_PATH"] = str(alert_target)
    os.environ["STOCK_DB_PATH"] = str(watch_target)

    # Re-point anything that captured the path by value at import time.
    for mod_name, attr in _VALUE_CAPTURED:
        try:
            setattr(importlib.import_module(mod_name), attr, alert_target)
        except Exception:
            pass

    yield

    alert_db.DB_PATH = saved["alert"]
    watch_manager.DB_PATH = saved["watch"]
    if saved["env_alert"] is None:
        os.environ.pop("ALERT_DB_PATH", None)
    else:
        os.environ["ALERT_DB_PATH"] = saved["env_alert"]
    if saved["env_watch"] is None:
        os.environ.pop("STOCK_DB_PATH", None)
    else:
        os.environ["STOCK_DB_PATH"] = saved["env_watch"]
    for mod_name, attr in _VALUE_CAPTURED:
        try:
            setattr(importlib.import_module(mod_name), attr, alert_target)
        except Exception:
            pass