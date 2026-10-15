import os
import time

from src.clients import llm_client
from src.utils import chat_priority


def test_flag_marks_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_priority, "FLAG", tmp_path / "chat_waiting.flag")
    assert not chat_priority.is_waiting()
    chat_priority.mark_waiting()
    assert chat_priority.is_waiting()
    old = time.time() - 60
    os.utime(chat_priority.FLAG, (old, old))
    assert not chat_priority.is_waiting()
    chat_priority.mark_waiting()
    chat_priority.clear()
    assert not chat_priority.is_waiting()


def test_research_waits_while_chat_waits_then_proceeds(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_priority, "FLAG", tmp_path / "chat_waiting.flag")
    chat_priority.mark_waiting()
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        chat_priority.clear()

    monkeypatch.setattr(llm_client.time, "sleep", fake_sleep)
    llm_client._wait_for_spare_slot(max_wait_s=30)
    assert len(sleeps) == 1


def test_research_leaves_one_slot_for_chat():
    assert llm_client._local_llm_semaphore._value == max(1, llm_client._LOCAL_LLM_CONCURRENCY - 1)
