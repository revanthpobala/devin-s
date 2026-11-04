"""No plan + not held: the pipeline writes the NO TRADE stub without Models A/B, debate or live fetch."""
import json

from src import config
from src.logic.deep_research import pipeline

DW_NO_PLAN = {"close": 100.0, "MA 50 Mid": 92.0, "bar_date": "2026-11-02", "time": "2026-11-02"}


def test_no_plan_not_held_skips_models(monkeypatch, tmp_path):
    date_str = "2026-11-02"
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    gems = tmp_path / "gems"
    gems.mkdir()
    for name in ("revanth-original-gem.md", "response.md", "revanth-bible.md"):
        (gems / name).write_text("gem", encoding="utf-8")

    safe = "XXX"
    tdir = tmp_path / "data" / "raw" / date_str / safe
    tdir.mkdir(parents=True)
    (tdir / f"{safe}_chart.png").write_bytes(b"png")
    (tdir / f"{safe}_datawindow.json").write_text(json.dumps(DW_NO_PLAN), encoding="utf-8")

    calls = {"contract": []}
    monkeypatch.setattr("src.logic.deep_research.deterministic.run_contract",
                        lambda *a, **kw: calls["contract"].append({"args": a, "kw": kw}) or {"ticker": safe})
    monkeypatch.setattr("src.logic.deep_research.pass2.run_pass2",
                        lambda *a, **kw: calls.setdefault("pass2", []).append(kw))
    monkeypatch.setattr("src.logic.deep_research.debate.run_debate",
                        lambda *a, **kw: calls.setdefault("debate", []).append(kw))
    monkeypatch.setattr("src.logic.deep_research.live_fetcher.fetch_live_context",
                        lambda *a, **kw: calls.setdefault("live", []).append(kw))
    monkeypatch.setattr("src.logic.deep_research.live_fetcher.pull_macro_news", lambda *a, **kw: "")
    monkeypatch.setattr("src.tracking.position_state.list_open", lambda: {})
    monkeypatch.setattr("src.clients.schwab_client.get_all_active_positions", lambda: [])
    monkeypatch.setattr("src.tracking.watch_manager.record_research_timing", lambda *a, **kw: None)

    pipeline.run_deep_research(date_str, target_ticker=safe, force=True)

    assert calls["contract"], "run_contract was never called for the stub"
    assert calls["contract"][0]["kw"]["side"] is None
    assert "pass2" not in calls and "debate" not in calls and "live" not in calls


def test_no_plan_but_held_does_not_early_exit(monkeypatch, tmp_path):
    """A held name with no plan skips the stub path: doc_side is LONG (manage mode) and the
    pipeline proceeds into the live-context fetch stage."""
    import pytest

    date_str = "2026-11-02"
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    gems = tmp_path / "gems"
    gems.mkdir()
    for name in ("revanth-original-gem.md", "response.md", "revanth-bible.md"):
        (gems / name).write_text("gem", encoding="utf-8")

    safe = "AMZN"
    tdir = tmp_path / "data" / "raw" / date_str / safe
    tdir.mkdir(parents=True)
    (tdir / f"{safe}_chart.png").write_bytes(b"png")
    (tdir / f"{safe}_datawindow.json").write_text(json.dumps(DW_NO_PLAN), encoding="utf-8")

    monkeypatch.setattr("src.clients.schwab_client.get_all_active_positions",
                        lambda: [{"ticker": "AMZN", "quantity": 133}])
    monkeypatch.setattr("src.tracking.position_state.list_open", lambda: {})
    monkeypatch.setattr("src.logic.deep_research.live_fetcher.pull_macro_news", lambda *a, **kw: "")
    monkeypatch.setattr("src.tracking.watch_manager.record_research_timing", lambda *a, **kw: None)
    monkeypatch.setattr("src.logic.deep_research.deterministic.run_contract",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("held name took the stub path")))

    # The first call past the early-exit point: if it fires, manage mode was entered.
    def _live(*a, **kw):
        raise RuntimeError("LIVE_CONTEXT_REACHED")

    monkeypatch.setattr("src.logic.deep_research.live_fetcher.fetch_live_context", _live)

    with pytest.raises(RuntimeError, match="LIVE_CONTEXT_REACHED"):
        pipeline.run_deep_research(date_str, target_ticker=safe, force=True)
