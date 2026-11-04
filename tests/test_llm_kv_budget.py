import threading
import time
from types import SimpleNamespace

import pytest

from src.clients import llm_client as lc


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "_KV_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(lc, "_KV_BUDGET", 100)
    monkeypatch.setattr(lc, "_wait_for_spare_slot", lambda: None)


def test_second_request_waits_until_budget_frees(ledger, monkeypatch):
    real_sleep = time.sleep
    polling = threading.Event()

    def fake_sleep(s):
        polling.set()
        real_sleep(0.01)

    monkeypatch.setattr(lc.time, "sleep", fake_sleep)
    order = []
    with lc._kv_reservation(80):
        t = threading.Thread(target=lambda: lc._kv_reservation(50).__enter__() or order.append("second"))
        t.start()
        assert polling.wait(2)          # the second request is blocked and polling the ledger
        assert order == []
        order.append("first-released")
    t.join(2)
    assert order == ["first-released", "second"]


def test_oversized_request_runs_alone(ledger):
    with lc._kv_reservation(10_000):
        pass


def test_context_overflow_is_retried_not_dropped(ledger, monkeypatch):
    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    calls = []

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("Error code: 500 - Context size has been exceeded.")
        return "ok"

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert lc._create_completion(client, "local", messages=[{"role": "user", "content": "x"}], max_tokens=5) == "ok"
    assert len(calls) == 2


def test_tool_call_key_ignores_argument_order():
    a = SimpleNamespace(function=SimpleNamespace(name="fetch_options_chain", arguments='{"ticker":"AMD","dte":30}'))
    b = SimpleNamespace(function=SimpleNamespace(name="fetch_options_chain", arguments='{"dte": 30, "ticker": "AMD"}'))
    assert lc._tool_call_key(a) == lc._tool_call_key(b)


def test_dead_process_reservation_is_pruned(ledger, monkeypatch):
    import json, os
    path = lc._KV_LEDGER
    with open(path, "w") as f:
        json.dump({"dead": [90, time.time() + 9999, 99999999], "legacy": [90, time.time() + 9999]}, f)
    monkeypatch.setattr(lc, "_pid_alive", lambda pid: pid == os.getpid())
    with lc._kv_reservation(80):                       # would block for 9999s if the dead entries counted
        pass
