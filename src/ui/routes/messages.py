"""
Scratch pad API — file-based.
data/messages/board.json
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.ui.services.messages_db import (
    clear_all,
    delete_message,
    get_messages,
    store_message,
)

logger = logging.getLogger("ui_server")

router = APIRouter(tags=["messages"])


class MessageRequest(BaseModel):
    sender: str = "user"
    content: str


@router.post("/api/pad/init")
def init_pad():
    return {"status": "ok"}


@router.get("/api/pad")
def get_pad_endpoint():
    messages = get_messages()
    return {"messages": messages, "count": len(messages)}


@router.post("/api/pad")
def store_pad_endpoint(req: MessageRequest):
    if not req.content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    message_id = store_message(req.sender, req.content)
    return {"status": "ok", "id": message_id}


@router.delete("/api/pad/{message_id}")
def delete_pad_endpoint(message_id: int):
    deleted = delete_message(message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "ok", "deleted_id": message_id}


@router.post("/api/pad/clear")
def clear_pad_endpoint():
    count = clear_all()
    return {"status": "ok", "deleted": count}
