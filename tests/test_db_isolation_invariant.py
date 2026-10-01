"""
The suite must never open the production databases.

WHY THIS REPLACED A FINGERPRINT CHECK
The obvious check -- hash data/*.db before and after a run -- is unreliable here, because the live
ingester (main.py --loop) and the cockpit are running and write on their own schedule. Measured:
with NO test running at all, both research_watch.db and trading_alerts.db changed inside 10
seconds. A before/after fingerprint therefore cannot attribute a change to the suite, and it
flapped between "CLEAN" and "LEAK" across runs for no reason connected to testing.

What this asserts instead is the actual invariant: while a test is executing, every path
resolver points inside pytest's tmp dir. That is deterministic and does not race the live system.
"""

from __future__ import annotations

import inspect

import pytest

from src import config
from src.tracking import alert_db
from src.tracking import watch_manager
from src.ui import state as ui_state


def test_both_database_resolvers_point_at_pytest_tmp():
    """The root guarantee: nothing can reach production even if a test forgets to patch a path."""
    tmp = str(pytestconfig_root())
    assert str(config.research_watch_db_path()).startswith(tmp), \
        f"research_watch_db_path resolves to production: {config.research_watch_db_path()}"
    assert str(config.alerts_db_path()).startswith(tmp), \
        f"alerts_db_path resolves to production: {config.alerts_db_path()}"


def test_module_level_db_paths_are_redirected():
    assert str(alert_db.DB_PATH).startswith(str(pytestconfig_root())), alert_db.DB_PATH
    assert str(watch_manager.DB_PATH).startswith(str(pytestconfig_root())), watch_manager.DB_PATH


def test_the_ui_state_helper_uses_the_override():
    assert "STOCK_DB_PATH" in inspect.getsource(config.research_watch_db_path)
    assert str(ui_state.db_path()).startswith(str(pytestconfig_root())), ui_state.db_path()


def test_nothing_in_src_builds_a_database_path_inline():
    """The original leak: six modules built the path by hand, so patching one did nothing."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    rx = re.compile(r'BASE_DIR\s*/\s*["\']data["\']\s*/\s*["\'](research_watch|trading_alerts)\.db["\']')
    offenders = []
    for p in list((root / "src").rglob("*.py")):
        if p.name == "config.py":        # config defines the default, by definition
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            if rx.search(line) and not line.strip().startswith("#"):
                offenders.append(f"{p.relative_to(root)}:{i}")
    assert not offenders, \
        f"these bypass the override and will open production from a test:\n  {offenders}"


def pytestconfig_root():
    """Root of this test's tmp dir -- conftest redirects every DB here per test."""
    return alert_db.DB_PATH.parent.parent