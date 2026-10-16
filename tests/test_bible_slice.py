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
