"""
Meta-tests that keep the suite from acquiring new landmines.

A landmine is a test whose result depends on something outside the test: the market, the clock,
the filesystem's timestamp granularity, the machine's load, or a live server. Each one here
produced a real, observed failure that looked like a code regression and was not:

  * BE+ ratchet          -- read LIVE NVDA intraday ATR; passed only while ATR < $0.70
  * runner trailing stop -- same, plus a vacuous `or stop >= 100.0` that accepted any value
  * catastrophic breaker -- flipped run-to-run on a live Schwab fallback
  * artifact TTL         -- time.sleep(0.05); a coarse filesystem mtime can round it to zero
  * options memory cache -- asserted a cache hit took <50ms of wall clock
  * copilot chat         -- wrote to the LIVE research_watch.db, leaving a uuid4 orphan each run
  * live E2E             -- auto-enabled itself whenever a cockpit was listening, then POSTed to
                           the sandboxed code-execution endpoint on the live trading server

These checks are cheap, run on every suite, and fail at review time rather than at 3am.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# This file names every pattern it hunts for, so it must never scan itself.
SELF = Path(__file__).name


def _test_files():
    return sorted(f for f in TESTS.glob("test_*.py") if f.name != SELF)


def _source(name: str) -> str:
    return (TESTS / name).read_text(encoding="utf-8", errors="replace")


def _code_lines(name: str):
    """(lineno, text) for real code only.

    Every pattern below is also written out in this file's prose and in the docstrings of the tests
    it is describing, so scanning raw text makes the check report itself.
    """
    text = _source(name)
    docstrings = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.update(range(body[0].lineno, body[0].end_lineno + 1))
    return [
        (i, ln)
        for i, ln in enumerate(text.split("\n"), 1)
        if i not in docstrings and not ln.strip().startswith("#")
    ]


# =====================================================================================
# No sleep-based timing assertions
# =====================================================================================

# Files that legitimately wait on a real socket or a background thread, with the reason.
_SLEEP_ALLOWLIST = {
    # Polls a subprocess/thread with a bounded retry loop; not a TTL assertion.
    "test_continuous_screener.py",
    "test_ui_routes.py",
}


@pytest.mark.parametrize("fname", [f.name for f in _test_files()])
def test_no_sleep_driven_assertions(fname):
    """time.sleep inside a test makes the result a function of machine load."""
    if fname in _SLEEP_ALLOWLIST:
        return
    for i, line in _code_lines(fname):
        if "time.sleep(" in line:
            pytest.fail(
                f"{fname}:{i} uses time.sleep(). A TTL or retry assertion must use a frozen "
                f"clock or an explicit state transition instead: {line.strip()!r}"
            )


# =====================================================================================
# No wall-clock performance assertions
# =====================================================================================

@pytest.mark.parametrize("fname", [f.name for f in _test_files()])
def test_no_wallclock_performance_assertions(fname):
    """`assert elapsed < 0.05` measures the machine, not the code."""
    if fname in _SLEEP_ALLOWLIST:
        return
    for i, line in _code_lines(fname):
        if re.search(r"time\.time\(\)\s*-\s*t\d", line) or re.search(r"\bd\d\s*=\s*time\.time\(\)\s*-", line):
            pytest.fail(
                f"{fname}:{i} measures elapsed wall-clock time and asserts on it: "
                f"{line.strip()!r}. Assert that the work was skipped (e.g. the fetcher was not "
                f"called again) rather than how fast it was."
            )


# =====================================================================================
# No vacuous assertions
# =====================================================================================

# `assert x in (...) or <always-true condition>` never fails.
_LOOSE_OR = re.compile(r"\bin\s*\([^)]*\)\s+or\s+\S")


@pytest.mark.parametrize("fname", [f.name for f in _test_files()])
def test_no_loose_or_assertions(fname):
    """`assert stop in (a, b) or stop >= 0` accepts anything, so it asserts nothing."""
    for i, line in _code_lines(fname):
        if _LOOSE_OR.search(line):
            pytest.fail(
                f"{fname}:{i} is vacuous -- the 'or' clause accepts any value the tuple "
                f"does not: {line.strip()!r}"
            )


def test_the_specific_known_vacuous_assertion_is_gone():
    """Pin the exact regression: a trailing-stop assert that could not fail."""
    code = "".join(ln for _, ln in _code_lines("test_exit_veto_engine.py"))
    assert "in (100.0, 100.05, 100.24) or" not in code
    assert "== 100.05" in code and "== 100.24" in code, (
        "the trailing-stop tests should now assert the exact value at a pinned ATR"
    )


# =====================================================================================
# ATR must be pinned wherever it can reach the live Schwab call
# =====================================================================================

# Symbols position_monitor can be asked about. Any test that ticks or reviews a position and does
# not pin ATR is one market-data round-trip away from a different result.
ATR_LIVE = "calculate_intraday_atr"
ATR_PINNERS = ("_pin_atr(", "calculate_intraday_atr\", return_value",
               "calculate_intraday_atr', return_value")


def test_exit_veto_tests_pin_the_live_atr_call():
    """Every position_monitor test must pin ATR, or it is gated on the market.

    `calculate_intraday_atr` is a real Schwab call and position_monitor falls back to it wherever
    a rule needs ATR. Two of these tests passed and failed with nothing changed in the code,
    purely because the underlying moved.
    """
    text = _source("test_exit_veto_engine.py")
    tree = ast.parse(text)

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test")):
            continue
        body = ast.get_source_segment(text, node) or ""
        # Tests that drive PositionMonitor._tick or review_tv_exit can read ATR.
        touches_monitor = "_tick()" in body or "review_tv_exit(" in body
        pins_atr = any(p in body for p in ATR_PINNERS)
        if touches_monitor and not pins_atr:
            offenders.append(node.name)

    assert not offenders, (
        "these tests drive the monitor without pinning ATR, so they depend on live market data: "
        f"{offenders}"
    )


def test_the_atr_pinning_helper_exists_and_documents_itself():
    text = _source("test_exit_veto_engine.py")
    assert "def _pin_atr(" in text
    assert ATR_LIVE in text, "the helper should name the call it is neutralising"


# =====================================================================================
# No test writes to the live database
# =====================================================================================

_LIVE_DB_LITERALS = (
    '"data/research_watch.db"',
    "'data/research_watch.db'",
    '"research_watch.db"',
    "BASE_DIR / \"data\"",
    "BASE_DIR / 'data'",
)


@pytest.mark.parametrize("fname", [f.name for f in _test_files()])
def test_no_test_hardcodes_the_live_database_path(fname):
    """A test that opens the real DB mutates trading data and leaves debris."""
    for i, line in _code_lines(fname):
        for lit in _LIVE_DB_LITERALS:
            if lit in line:
                pytest.fail(
                    f"{fname}:{i} references the live database path ({lit}). Tests must use "
                    f"tmp_path, or STOCK_DB_PATH when they need the UI state helpers: "
                    f"{line.strip()!r}"
                )


def test_ui_state_db_path_is_overridable():
    """Without this hook there is no way to point get_db() somewhere harmless."""
    from src.ui import state

    assert hasattr(state, "db_path")
    src = (ROOT / "src" / "ui" / "state.py").read_text(encoding="utf-8")
    assert "STOCK_DB_PATH" in src or "research_watch_db_path" in src, (
        "get_db() needs an env override or tests will keep opening the live database"
    )


# =====================================================================================
# One source of truth for the database paths
# =====================================================================================

_INLINE_WATCH_DB = re.compile(r'config\.BASE_DIR\s*/\s*["\']data["\']\s*/\s*["\']research_watch\.db["\']')
_INLINE_ALERTS_DB = re.compile(r'config\.BASE_DIR\s*/\s*["\']data["\']\s*/\s*["\']trading_alerts\.db["\']')


def test_no_module_builds_a_database_path_inline():
    """The live-database leak had six separate sources.

    Modules used to write `config.BASE_DIR / "data" / "research_watch.db"` inline. Redirecting
    watch_manager.DB_PATH -- the obvious lever -- left the other five pointing at production, so a
    full suite run mutated data/research_watch.db while every test passed. All of them must go
    through config.research_watch_db_path() / config.alerts_db_path().
    """
    offenders = []
    for p in sorted((ROOT / "src").rglob("*.py")) + [ROOT / n for n in ("run_ui.py", "run_deep_research.py")]:
        if not p.exists() or p.name == "config.py":
            # config.py defines the helper; its default branch is the literal by definition.
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            if _INLINE_WATCH_DB.search(line) or _INLINE_ALERTS_DB.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}")
    assert not offenders, (
        "these build a database path inline and bypass the test override:\n  "
        + "\n  ".join(offenders[:12])
    )


def test_config_exposes_both_db_path_helpers():
    from src import config

    assert hasattr(config, "research_watch_db_path")
    assert hasattr(config, "alerts_db_path")


def test_conftest_redirects_databases_before_collection():
    """Import-time `from ... import DB_PATH` captures the value, so the env var must be set in
    pytest_configure rather than in a fixture."""
    text = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "def pytest_configure(" in text
    configure = text.split("def pytest_configure(", 1)[1].split("def pytest_unconfigure", 1)[0]
    for var in ("ALERT_DB_PATH", "STOCK_DB_PATH"):
        assert var in configure, f"{var} must be set before collection, not in a fixture"


def test_no_test_claims_real_db_without_saying_why():
    """The opt-out marker exists so a live-DB test is argued for, not fallen into."""
    marked = []
    for f in _test_files():
        code = "\n".join(ln for _, ln in _code_lines(f.name))
        if "pytest.mark.real_db" in code:
            marked.append(f.name)
            window = "\n".join(
                ln for ln in code.split("\n")
                if "real_db" in ln or ln.strip().startswith('"""') or "reason" in ln.lower()
            )
            assert len(window.split("\n")) >= 3, (
                f"{f.name} marks real_db without explaining why it needs production data"
            )
    assert isinstance(marked, list)


def test_copilot_tests_are_isolated():
    code = "\n".join(ln for _, ln in _code_lines("test_copilot_chat.py"))
    assert "STOCK_DB_PATH" in code
    assert "uuid" not in code, (
        "a uuid4 session id means every run leaves a different orphan row; the id must be fixed "
        "or the session must be cleaned up deterministically"
    )


# =====================================================================================
# Live-server tests are opt-in
# =====================================================================================

def test_live_e2e_requires_explicit_opt_in():
    """"A server happens to be up" is not consent to test against the trading cockpit."""
    text = _source("test_ui_api_core.py")
    assert "RUN_LIVE_E2E" in text, "live E2E must be behind an explicit opt-in env var"
    assert "_is_server_running()" in text, "it should still verify the server is reachable"

    # The gate must be opt-in AND reachability, never reachability alone.
    assert not re.search(
        r"skipif\(\s*not _is_server_running\(\)\s*,", text
    ), "the skip condition must not be reachability alone"


def test_live_e2e_warns_about_the_code_execution_endpoint():
    """The suite POSTs to a sandboxed executor; the skip reason must say so out loud."""
    text = _source("test_ui_api_core.py")
    assert "execute-python" in text

    # The reason is an implicitly-concatenated multi-line string; join before searching.
    start = text.index("reason=")
    reason = text[start:start + 500].replace('"\n        "', "").replace('" ', "")
    assert "execute-python" in reason, (
        "a skipped live E2E run should still tell the reader it POSTs to the code executor"
    )


# =====================================================================================
# Shared-state hygiene
# =====================================================================================

_ASSIGN = re.compile(r"^([A-Za-z_][\w]*)\._(\w+)\s*=\s*(lambda|MagicMock|\{|Magic)")


def _module_aliases(fname: str) -> set:
    """Names bound to a module (not to a local instance) in this test file.

    `mgr._stop_monitor = MagicMock()` patches a local object and is harmless. `notify_mod._dispatch
    = ...` patches a module and leaks into every later test. Only the latter can be told apart
    syntactically, by checking which names were actually imported as modules.
    """
    aliases = set()
    for _, line in _code_lines(fname):
        m = re.match(r"\s*import\s+([A-Za-z_][\w.]*)(?:\s+as\s+([A-Za-z_]\w*))?", line)
        if m:
            aliases.add((m.group(2) or m.group(1)).split(".")[0])
            continue
        m = re.match(r"\s*from\s+[\w.]+\s+import\s+([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?", line)
        if m and (m.group(2) or m.group(1)) in _IMPORTED_MODULES:
            aliases.add(m.group(2) or m.group(1))
    return aliases


_IMPORTED_MODULES = {
    "datetime", "pathlib", "json", "sqlite3", "threading", "pytest", "os", "sys", "time",
    "src", "run_ui", "copy", "typing", "unittest", "collections", "math", "re", "uuid",
}


def test_no_bare_module_attribute_assignment_for_patching():
    """`module._thing = lambda ...` without monkeypatch leaks into every later test in the session.

    This exact mistake shipped here once: a notify test assigned _dispatch directly and silently
    broke the ntfy/SMTP tests that ran after it.
    """
    offenders = []
    for f in _test_files():
        aliases = _module_aliases(f.name)
        for i, line in _code_lines(f.name):
            m = _ASSIGN.match(line.strip())
            if m and m.group(1) in aliases:
                offenders.append(f"{f.name}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "these patch a MODULE attribute without monkeypatch, so they leak into every later "
        "test:\n  " + "\n  ".join(sorted(offenders)[:10])
    )


def test_the_known_leak_is_fixed():
    code = "".join(ln for _, ln in _code_lines("test_alert_robustness.py"))
    assert 'monkeypatch.setattr(notify_mod, "_dispatch"' in code
    assert "nm._dispatch =" not in code, \
        "_dispatch must be patched via monkeypatch so it is restored at teardown"