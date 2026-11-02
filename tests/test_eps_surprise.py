import importlib.util
import json
from pathlib import Path

import pandas as pd

from src.clients import alphavantage_client as av

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = _load("move_postmortem", "scripts/research/move_postmortem.py")
bf = _load("eps_surprise_backfill", "scripts/research/eps_surprise_backfill.py")


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_fetch_earnings_history_parses_quarters(monkeypatch):
    fixture = {"symbol": "INTC", "quarterlyEarnings": [
        {"reportedDate": "2026-10-29", "reportedEPS": "0.1", "estimatedEPS": "0.2", "surprisePercentage": "-50.0"}]}
    monkeypatch.setattr(av, "_check_and_increment_quota", lambda: True)
    monkeypatch.setattr(av.requests, "get", lambda *a, **k: _Resp(fixture))
    assert av.fetch_earnings_history("INTC")[0]["surprisePercentage"] == "-50.0"


def test_fetch_earnings_history_none_on_quota_or_rate_limit(monkeypatch):
    monkeypatch.setattr(av, "_check_and_increment_quota", lambda: False)
    assert av.fetch_earnings_history("INTC") is None
    monkeypatch.setattr(av, "_check_and_increment_quota", lambda: True)
    monkeypatch.setattr(av.requests, "get", lambda *a, **k: _Resp({"Information": "rate limit"}))
    assert av.fetch_earnings_history("INTC") is None


def test_attach_surprise_joins_nearest_quarter_within_window(tmp_path):
    cache = tmp_path / "eps.json"
    cache.write_text(json.dumps({"INTC": {"quarters": [
        {"reportedDate": "2026-10-29", "surprisePercentage": "-50.0"},
        {"reportedDate": "2026-07-23", "surprisePercentage": "3.0"}]}}))
    e = pd.DataFrame({"ticker": ["INTC", "INTC", "AMD"], "date": ["2026-10-30", "2026-04-24", "2026-10-30"],
                      "m": [-0.28, 0.02, 0.01], "pred": [0.08, 0.08, 0.05], "era": ["2022-2026"] * 3})
    out = pm.attach_surprise(e, cache)
    assert out.surprise_bucket.tolist() == ["MISS", None, None]
    assert out.surprise_pct.iloc[0] == -50.0
    assert pm.category_table(out, "surprise_bucket")["MISS"]["n"] == 1


def test_surprise_bucket_bands():
    assert pm.surprise_bucket(-2.5) == "MISS" and pm.surprise_bucket(0.5) == "IN_LINE" and pm.surprise_bucket(2.5) == "BEAT"


def test_backfill_resumes_and_stops_at_quota(monkeypatch, tmp_path):
    cache = tmp_path / "eps.json"
    cache.write_text(json.dumps({"AAA": {"quarters": []}}))
    monkeypatch.setattr(bf, "CACHE", cache)
    monkeypatch.setattr(bf, "UNIVERSE", tmp_path / "missing.json")
    monkeypatch.setattr(bf, "priority_order", lambda uni: ["AAA", "BBB", "CCC"])
    calls = []

    def fake(t):
        calls.append(t)
        return None if t == "CCC" else [{"reportedDate": "2026-01-01", "surprisePercentage": "1"}]

    monkeypatch.setattr(bf, "fetch_earnings_history", fake)
    monkeypatch.setattr("sys.argv", ["x"])
    assert bf.main() == 0
    assert calls == ["BBB", "CCC"]
    saved = json.loads(cache.read_text())
    assert set(saved) == {"AAA", "BBB"}
