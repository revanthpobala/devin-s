"""Cross-process signal that a chat message is waiting for a GPU slot.

Research and triage run in separate processes from the cockpit, so the flag is a file. While it is
fresh, `llm_client._wait_for_spare_slot` holds new research requests, so the next slot that frees goes
to the chat instead of another queued research call.
"""
from __future__ import annotations

import time
from pathlib import Path

from src import config

FLAG = Path(config.BASE_DIR) / "data" / "chat_waiting.flag"
FRESH_S = 15.0


def mark_waiting() -> None:
    try:
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.touch()
    except OSError:
        pass


def clear() -> None:
    try:
        FLAG.unlink()
    except OSError:
        pass


def is_waiting() -> bool:
    try:
        return time.time() - FLAG.stat().st_mtime < FRESH_S
    except OSError:
        return False
