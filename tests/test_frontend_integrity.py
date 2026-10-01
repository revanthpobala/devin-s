"""
INVARIANT: every JS module parses, and each App* object it defines is actually defined.

A single stray `</span>` in watchlist.js sat at the top level for long enough to take out the
entire tactical radar — the whole file failed to parse, so `window.AppWatchlist` never existed,
`loadWatchlist()` never ran, and the panel sat on "Loading live tactical radar..." with every
counter at its static 0. Nothing in the UI reported an error, because a script that fails to parse
just... doesn't run.

So this file parses every module with `node --check` and asserts the globals the HTML wires up
with inline onclick handlers actually exist.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "web" / "static" / "js"
HTML = ROOT / "web" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to parse-check the frontend"
)


def _modules():
    return sorted(p for p in JS_DIR.glob("*.js"))


def test_there_are_modules_to_check():
    assert len(_modules()) >= 10, "no JS modules found; the glob is wrong"


@pytest.mark.parametrize("path", [p.name for p in _modules()])
def test_module_parses(path):
    """`node --check` is a full parse. A failure here blanks whatever that module drives."""
    r = subprocess.run(
        ["node", "--check", str(JS_DIR / path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"{path} does not parse:\n{r.stderr[:1200]}"


def test_every_module_that_drives_a_panel_parses():
    """The bug this exists for, stated as a rule rather than a heuristic.

    A stray top-level `</span>` once sat in watchlist.js and took the whole tactical radar down:
    the file failed to parse, `window.AppWatchlist` was never created, and the panel sat on
    "Loading live tactical radar..." with every counter at its static 0 -- with nothing in the UI
    reporting an error, because a script that fails to parse simply never runs.

    `node --check` is the only reliable oracle here. A textual heuristic for "orphan markup"
    produces false positives on any file with a template literal spanning lines, which is most of
    them, so the parse is what gets asserted.
    """
    broken = []
    for p in _modules():
        r = subprocess.run(["node", "--check", str(p)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            first = (r.stderr.strip().splitlines() or ["?"])
            broken.append(f"{p.name}: {first[0]}")
    assert not broken, "these modules do not parse and their panels will be blank:\n  " + \
        "\n  ".join(broken)


def test_the_session_modal_is_wired():
    """Clicking a calendar day must open the queue modal, not just filter."""
    src = (JS_DIR / "status.js").read_text(encoding="utf-8", errors="replace")
    for fn in ("openSessionModal", "closeSessionModal", "renderSessionModalBody"):
        assert f"{fn}(" in src, f"{fn} is missing from status.js"
    assert "openSessionModal('${key}')" in src, "calendar cells do not open the modal"
    assert "openSessionModal('${today}')" in src, "the pinned today card does not open the modal"

    api = (JS_DIR / "api.js").read_text(encoding="utf-8", errors="replace")
    assert "getAlertsHistory" in api and "/api/alerts/history" in api
    assert "getAlertsHistory(day)" in src, "the modal does not fetch that day's alerts"


def _globals_defined() -> set:
    found = set()
    for p in _modules():
        text = p.read_text(encoding="utf-8", errors="replace")
        found |= set(re.findall(r"window\.(App\w+)\s*=", text))
    return found


def test_app_objects_referenced_by_the_html_exist():
    """Every App* global the page wires up inline must be defined by some module.

    If a module fails to parse its App object silently disappears, and the only symptom is a
    button that does nothing.
    """
    html = HTML.read_text(encoding="utf-8", errors="replace")
    referenced = set(re.findall(r"\b(App\w+)\.", html))
    defined = _globals_defined()
    missing = sorted(referenced - defined)
    assert not missing, (
        f"index.html calls these but no JS module defines them: {missing}. "
        f"Usually means the owning file failed to parse."
    )


def test_the_watchlist_radar_globals_exist():
    """The tactical radar specifically: this is what silently disappeared."""
    defined = _globals_defined()
    for name in ("AppWatchlist",):
        assert name in defined, f"{name} is not defined by any module"


def test_radar_render_targets_exist_in_the_html():
    """The radar's mount point and counters must be in the markup the module drives."""
    html = HTML.read_text(encoding="utf-8", errors="replace")
    for anchor in ("swing-radar-widget", "rf-count-all", "radar-date-select",
                   "session-history-container", "rev-chat-focus-badge",
                   "rev-chat-focus-input"):
        assert anchor in html, f"{anchor} is missing from index.html"


def test_radar_filter_buttons_point_at_real_filters():
    """Every data-radar-filter button must have a branch in setRadarFilter."""
    html = HTML.read_text(encoding="utf-8", errors="replace")
    filters = set(re.findall(r'data-radar-filter="([A-Z_]+)"', html))
    assert filters, "no radar filter buttons found in index.html"
    src = (JS_DIR / "watchlist.js").read_text(encoding="utf-8", errors="replace")
    for f in sorted(filters):
        assert f"'{f}'" in src or f in src, f"radar filter {f} has no handler in watchlist.js"


def test_queue_date_control_is_wired():
    """The session date picker is built in JS, so both halves of the wiring live there."""
    src = (JS_DIR / "status.js").read_text(encoding="utf-8", errors="replace")
    assert "queue-date-select" in src, "the session date <select> is never rendered"
    assert "AppStatus.setQueueDate" in src, "the select is not bound to a handler"
    assert "setQueueDate(" in src and "loadSessions(" in src

    html = HTML.read_text(encoding="utf-8", errors="replace")
    assert 'id="session-history-container"' in html, "the session pane has no mount point"
    assert 'onclick="AppStatus.setQueueDate(' in html or "setQueueDate(" in src


def test_the_rev_chat_focus_controls_exist():
    """The focus badge and the on-demand focus box must both be present and bound."""
    html = HTML.read_text(encoding="utf-8", errors="replace")
    assert 'id="rev-chat-focus-badge"' in html
    assert 'id="rev-chat-focus-input"' in html
    assert "AppChat.clearSidebarFocus()" in html
    assert "AppChat.applySidebarFocusInput()" in html