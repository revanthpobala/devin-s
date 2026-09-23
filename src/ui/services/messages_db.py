"""
Scratch pad — file-based.
All messages stored in data/messages/board.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src import config


def _get_messages_dir() -> Path:
    d = config.BASE_DIR / "data" / "messages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_board_path() -> Path:
    return _get_messages_dir() / "board.json"


def _read_board() -> list[dict]:
    path = _get_board_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_board(messages: list[dict]) -> None:
    path = _get_board_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def get_messages() -> list[dict]:
    return _read_board()


def store_message(sender: str, content: str) -> int:
    messages = _read_board()
    now = datetime.now(timezone.utc).isoformat()
    msg_id = (messages[-1]["id"] + 1) if messages else 1
    messages.append({
        "id": msg_id,
        "sender": sender,
        "content": content,
        "created_at": now,
    })
    _write_board(messages)
    return msg_id


def delete_message(message_id: int) -> bool:
    messages = _read_board()
    new = [m for m in messages if m.get("id") != message_id]
    if len(new) == len(messages):
        return False
    _write_board(new)
    return True


def clear_all() -> int:
    _write_board([])
    return 0
