"""
tests/test_copilot_stream_busy_notice.py

The chat stream must never sit silent: a request queued behind deep research on
the shared GPU reports "GPU busy, queued" instead of an empty stream.

The repo has no pytest-asyncio plugin, so these tests drive the async generator
through asyncio.run() from a sync test.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from src.ui.routes import copilot as cop
from src.ui.routes.copilot import CopilotChatRequest


def _fake_request():
    class _Req:
        async def is_disconnected(self):
            return False

    return _Req()


@pytest.fixture(autouse=True)
def _small_busy_budgets(monkeypatch):
    monkeypatch.setattr(cop, "CHAT_FIRST_TOKEN_BUSY_S", 0.2, raising=False)
    monkeypatch.setattr(cop, "CHAT_STALL_BUSY_S", 0.2, raising=False)
    monkeypatch.setattr(cop, "CHAT_WAIT_TICK_S", 0.1, raising=False)


def _chunk(text: str):
    class _Delta:
        content = text
        tool_calls = None

    class _Choice:
        delta = _Delta()
        finish_reason = None

    class _Chunk:
        choices = [_Choice()]

    return _Chunk()


class _FakeStream:
    """An async stream that emits nothing for `delay` seconds, then one token."""

    def __init__(self, delay: float):
        self.delay = delay

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        await asyncio.sleep(self.delay)
        yield _chunk("The late answer.")


class _FakeCompletions:
    def __init__(self, delay: float):
        self.delay = delay

    async def create(self, **kwargs):
        return _FakeStream(self.delay)


class _FakeChat:
    def __init__(self, delay: float):
        self.completions = _FakeCompletions(delay)


class _FakeModels:
    async def list(self):
        class _Data:
            id = "qwen-test"

        class _Resp:
            data = [_Data()]

        return _Resp()


class _FakeOpenAI:
    def __init__(self, delay: float):
        self.chat = _FakeChat(delay)
        self.models = _FakeModels()


async def _drain(generator, limit: int = 500) -> str:
    out = []
    async for piece in generator:
        out.append(piece)
        if len(out) >= limit:
            break
    return "".join(out)


class _FakeSocketConn:
    """A live port-8000 probe result: the stream must not depend on whether a real
    llama-server happens to be running on this machine when the test runs."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_chat(monkeypatch, delay: float, session_id: str, limit: int = 500) -> str:
    async def _drive():
        # The patch must stay active while the generator streams, not just while
        # the endpoint coroutine is awaited: the SSE body is produced afterwards.
        # `socket` is imported inside the handler, so the patch targets the module
        # object the handler resolves at call time.
        with patch("openai.AsyncOpenAI", lambda *a, **k: _FakeOpenAI(delay)), \
             patch("socket.create_connection", lambda *a, **k: _FakeSocketConn()):
            response = await cop.copilot_chat_stream_endpoint(
                CopilotChatRequest(
                    prompt="Did GOOGL break 355?",
                    ticker="GOOGL",
                    date="2026-10-13",
                    session_id=session_id,
                ),
                request=_fake_request(),
            )
            return await asyncio.wait_for(
                _drain(response.body_iterator, limit=limit), timeout=30
            )

    return asyncio.run(_drive())


def test_slow_stream_reports_gpu_busy_queued(monkeypatch):
    """No token inside the first-token budget -> the stream says GPU busy, queued."""
    # A generous piece cap: the notice must be found even when the connection
    # preamble (slot probe, skill-carousel trigger) emits its own events first.
    body = _run_chat(monkeypatch, delay=2.0, session_id="test_busy_stream", limit=10)

    assert "GPU busy, queued" in body
    notices = [
        json.loads(line[6:]) for line in body.splitlines()
        if line.startswith("data: ") and "notice" in line
    ]
    assert any("GPU busy, queued" in n.get("notice", "") for n in notices)


def test_fast_stream_has_no_busy_notice(monkeypatch):
    """A stream that starts immediately never emits the busy notice."""
    monkeypatch.setattr(cop, "CHAT_FIRST_TOKEN_BUSY_S", 30.0, raising=False)
    body = _run_chat(monkeypatch, delay=0.0, session_id="test_fast_stream", limit=2)

    assert "GPU busy, queued" not in body
