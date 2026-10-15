import json

from src.clients import llm_client, options_client


class _Call:
    def __init__(self, name, args):
        self.id = "1"
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


def test_tool_names_unique():
    names = [t["function"]["name"] for t in llm_client.TOOLS]
    assert len(names) == len(set(names))


def test_chat_ticker_not_rewritten_by_research_global(monkeypatch):
    seen = []
    import src.tracking.candle_service as cs

    monkeypatch.setattr(cs, "get_candles_tool", lambda **k: seen.append(k) or "ok")
    monkeypatch.setattr(options_client, "_ACTIVE_TICKER", "ZZZZ")
    llm_client.execute_tool_call(_Call("get_candles", {"ticker": "GOOGL", "levels": [355, "344", "x"]}), "2026-10-13", False, "META")
    llm_client.execute_tool_call(_Call("get_candles", {"levels": [355]}), "2026-10-13", False, "META")
    assert seen[0]["ticker"] == "GOOGL" and seen[0]["levels"] == [355.0, 344.0]
    assert seen[1]["ticker"] == "META"


def test_research_leaves_one_slot_idle_for_chat(monkeypatch):
    import io
    import json as _json
    import urllib.request

    from src.clients import llm_client as L

    states = [[{"is_processing": True}, {"is_processing": True}], [{"is_processing": True}, {"is_processing": False}], [{"is_processing": False}, {"is_processing": False}]]
    calls = {"n": 0}

    def fake_urlopen(url, timeout=0):
        s = states[min(calls["n"], 2)]
        calls["n"] += 1
        return io.BytesIO(_json.dumps(s).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    L._wait_for_spare_slot(max_wait_s=5)
    assert calls["n"] == 3  # one busy slot still holds research back: the idle one is for chat
