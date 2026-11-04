from pathlib import Path

from src.logic.deep_research.bible_slice import DROP_HEADINGS, slim_bible

BIBLE = Path(__file__).resolve().parent.parent / "gems" / "revanth-bible.md"


def test_slim_drops_only_build_internals_and_is_deterministic():
    full = BIBLE.read_text(encoding="utf-8")
    slim = slim_bible(full)
    assert slim == slim_bible(full)
    assert len(slim) < len(full) * 0.92
    for h in DROP_HEADINGS:
        assert f"## {h}" not in slim
    for keep in ("## 3. Signal Labels & Actions", "## 8. Data Window Exports", "## 16. Measured Combinations", "## 18. Research Verdicts"):
        assert keep in slim


def test_bible_full_env_restores_everything(monkeypatch):
    full = BIBLE.read_text(encoding="utf-8")
    monkeypatch.setenv("BIBLE_FULL", "1")
    assert slim_bible(full) == full


def _sections(text):
    import re
    return {int(m) for m in re.findall(r"(?m)^## (\d+)\.", text)}


def test_router_core_only_for_quiet_ticker_and_triggers_add_sections():
    from src.logic.deep_research.bible_slice import ALWAYS_SECTIONS, route_bible
    full = BIBLE.read_text(encoding="utf-8")
    quiet = route_bible(full, {"close": 100, "Action Long Code": 18, "Ext Z Self Relative": 0.2,
                               "Energy IV Rank Pct": 20, "Long Rev Zone": 0})
    assert _sections(quiet) == set(ALWAYS_SECTIONS)
    assert len(quiet) < len(slim_bible(full)) * 0.7
    hot = route_bible(full, {"close": 100, "Action Long Code": 20, "Ext Z Self Relative": 2.0,
                             "Energy IV Rank Pct": 80, "Long Rev Zone": 2})
    assert {9, 11, 16, 17} <= _sections(hot)


def test_router_sections_are_verbatim_and_deterministic(monkeypatch):
    from src.logic.deep_research.bible_slice import route_bible
    full = BIBLE.read_text(encoding="utf-8")
    dw = {"Ext Z Self Relative": 2.0}
    out = route_bible(full, dw)
    assert out == route_bible(full, dw)
    assert "Table of Contents" not in out and "Verification Record" not in out
    sec8 = full.split("## 8. ")[1].split("\n## ")[0]
    assert sec8 in out


def test_router_env_overrides(monkeypatch):
    from src.logic.deep_research.bible_slice import route_bible
    full = BIBLE.read_text(encoding="utf-8")
    monkeypatch.setenv("BIBLE_ROUTE", "0")
    assert route_bible(full, {}) == slim_bible(full)
    monkeypatch.setenv("BIBLE_FULL", "1")
    assert route_bible(full, {}) == full
