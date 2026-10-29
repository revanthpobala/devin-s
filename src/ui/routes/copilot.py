"""
Interactive AI Copilot & REV CHAT endpoints.
Includes session management, Python execution sandbox, non-streaming query, and SSE token-by-token streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import openai
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.ui.services.copilot_context import _build_copilot_context_and_tools
from src.ui.state import get_db as _get_db, init_db as _init_db

logger = logging.getLogger("ui_server")

router = APIRouter(tags=["copilot"])


def _make_async_openai_client(
    base_url: str,
    api_key: str,
    default_headers: Optional[dict] = None,
) -> tuple[Any, httpx.AsyncClient]:
    """
    Creates an AsyncOpenAI client backed by a tuned httpx.AsyncClient connection pool.
    keepalive_expiry=15.0 ensures that idle pooled connections are closed and fresh
    sockets are opened on subsequent tool turns, preventing Cloudflare 60s idle
    socket drops (RemoteProtocolError / APIConnectionError: Connection error).
    """
    limits = httpx.Limits(
        max_keepalive_connections=5,
        max_connections=20,
        keepalive_expiry=15.0,
    )
    timeout = httpx.Timeout(
        connect=25.0,
        read=180.0,
        write=30.0,
        pool=15.0,
    )
    http_client = httpx.AsyncClient(limits=limits, timeout=timeout)
    client_kwargs: dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "timeout": 180,
        "http_client": http_client,
        "max_retries": 3,
    }
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    return openai.AsyncOpenAI(**client_kwargs), http_client


class CopilotChatRequest(BaseModel):
    question: Optional[str] = ""
    prompt: Optional[str] = None
    ticker: Optional[str] = None
    date: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = []
    session_id: Optional[str] = None
    board_context: Optional[str] = None
    image_data: Optional[str] = None


class SaveChatMessageRequest(BaseModel):
    session_id: str
    ticker: str
    date: str
    role: str
    content: str


class ExecutePythonRequest(BaseModel):
    code: str
    ticker: Optional[str] = "AMD"
    date: Optional[str] = None
    approved: bool = False


# A silent chat stream is a failure: when no token has arrived after this long the
# request is queued behind deep research on the shared GPU, and the user is told
# "GPU busy, queued" instead of watching an empty stream. A mid-stream stall past
# CHAT_STALL_BUSY_S raises the same notice (once; tokens clear it).
CHAT_FIRST_TOKEN_BUSY_S = 45.0
CHAT_STALL_BUSY_S = 60.0
# The stream's poll cadence: how often the not-yet-arrived chunk check runs (and how
# often the busy-notice budget is re-evaluated).
CHAT_WAIT_TICK_S = 3.0


def get_chat_llm_config() -> dict:
    """
    Resolves the LLM configuration for Copilot Chat.
    Priority:
      1. Explicit CHAT_PROVIDER override ('local' or 'openrouter' / 'cloud').
      2. If CHAT_OPEN_ROUTER_API_KEY or OPENROUTER_API_KEY is configured:
         Use OpenRouter (model from CHAT_MODEL or OPENROUTER_MODEL, e.g. z-ai/glm-5.3-flash).
      3. Fallback: Local llama.cpp / Qwen server on port 8000.
    """
    provider_override = (os.getenv("CHAT_PROVIDER") or "").strip().lower()

    if provider_override == "local":
        return {
            "provider": "local",
            "api_key": "sk-no-key-required",
            "base_url": os.getenv("LLM_LOCAL_URL", "http://127.0.0.1:8000/v1"),
            "model": None,
            "default_headers": None,
        }

    # OpenRouter primary if configured
    chat_key = (
        os.getenv("CHAT_OPEN_ROUTER_API_KEY")
        or os.getenv("CHAT_OPENROUTER_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("OPENROUTER_KEY")
    )
    if chat_key and chat_key.strip().lower() not in ("none", "false", "0"):
        raw_model = (
            os.getenv("CHAT_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or "openrouter/free"
        )
        model = raw_model.lstrip("=").strip() if raw_model else "openrouter/free"
        return {
            "provider": "openrouter",
            "api_key": chat_key.strip(),
            "base_url": "https://openrouter.ai/api/v1",
            "model": model,
            "default_headers": {
                "HTTP-Referer": "http://localhost:8050",
                "X-Title": "Stock Cockpit Copilot",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            },
        }

    # Local llama.cpp fallback
    local_url = os.getenv("LLM_LOCAL_URL", "http://127.0.0.1:8000/v1")
    return {
        "provider": "local",
        "api_key": "sk-no-key-required",
        "base_url": local_url,
        "model": None,
        "default_headers": None,
    }


def _save_chat_turn(session_id: str, ticker: str, date_str: str, role: str, content: str):
    """Saves a chat message turn directly to SQLite database."""
    try:
        _init_db()
        with _get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO copilot_chat_history (session_id, ticker, date, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, (ticker or "GENERAL").upper(), date_str or datetime.now().strftime("%Y-%m-%d"), role, content, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"Error saving chat turn: {e}")


@router.get("/api/copilot/chats/sessions")
def get_copilot_chat_sessions_endpoint(days: int = 15, ticker: Optional[str] = None):
    """Returns prior chat sessions from the last N days (default 15 days), ordered by most recent."""
    _init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _get_db() as conn:
        c = conn.cursor()
        query = """
            SELECT session_id, ticker, date, role, content, created_at
            FROM copilot_chat_history
            WHERE created_at >= ?
        """
        params = [cutoff]
        if ticker and ticker not in ("ALL", "GENERAL", ""):
            query += " AND ticker = ?"
            params.append(ticker.upper())
        query += " ORDER BY id ASC"
        
        rows = c.execute(query, params).fetchall()
        
        sessions_map = {}
        for r in rows:
            sid = r["session_id"]
            if sid not in sessions_map:
                sessions_map[sid] = {
                    "session_id": sid,
                    "ticker": r["ticker"],
                    "date": r["date"],
                    "first_prompt": r["content"] if r["role"] == "user" else "Chat Session",
                    "last_snippet": r["content"][:140],
                    "message_count": 0,
                    "created_at": r["created_at"],
                    "updated_at": r["created_at"]
                }
            sess = sessions_map[sid]
            sess["message_count"] += 1
            sess["updated_at"] = r["created_at"]
            if r["role"] == "user" and sess["first_prompt"] == "Chat Session":
                sess["first_prompt"] = r["content"]
            sess["last_snippet"] = r["content"][:140]

        res = sorted(list(sessions_map.values()), key=lambda s: s["updated_at"], reverse=True)
        return {"sessions": res, "days": days}


@router.get("/api/copilot/chats/history")
def get_copilot_chat_history_endpoint(session_id: Optional[str] = None, ticker: Optional[str] = None, date: Optional[str] = None):
    """Returns all stored messages for a specific session or most recent session for ticker/date."""
    _init_db()
    with _get_db() as conn:
        c = conn.cursor()
        if session_id:
            rows = c.execute(
                "SELECT id, session_id, ticker, date, role, content, created_at FROM copilot_chat_history WHERE session_id = ? ORDER BY id ASC",
                (session_id,)
            ).fetchall()
        elif ticker:
            q = "SELECT session_id FROM copilot_chat_history WHERE ticker = ?"
            p = [ticker.upper()]
            if date:
                q += " AND date = ?"
                p.append(date)
            q += " ORDER BY id DESC LIMIT 1"
            latest_sess = c.execute(q, p).fetchone()
            if latest_sess:
                target_sid = latest_sess["session_id"]
                rows = c.execute(
                    "SELECT id, session_id, ticker, date, role, content, created_at FROM copilot_chat_history WHERE session_id = ? ORDER BY id ASC",
                    (target_sid,)
                ).fetchall()
                session_id = target_sid
            else:
                rows = []
        else:
            rows = []
        
        messages = [dict(r) for r in rows]
        return {"messages": messages, "session_id": session_id}


@router.post("/api/copilot/chats/save")
def save_copilot_chat_endpoint(req: SaveChatMessageRequest):
    """Explicitly saves a chat message turn to SQLite."""
    _save_chat_turn(req.session_id, req.ticker, req.date, req.role, req.content)
    return {"status": "ok"}


@router.post("/api/copilot/execute-python")
def copilot_execute_python_endpoint(req: ExecutePythonRequest):
    """Executes sandboxed Python code with explicit user approval required."""
    if not req.approved:
        return {
            "success": False,
            "output": None,
            "error": "Execution denied: explicit user approval required for Python code execution.",
            "ticker": req.ticker,
            "duration_ms": 0.0,
        }
    from src.clients.llm_client import execute_python_code_tool
    t0 = time.time()
    res = execute_python_code_tool(code=req.code, ticker=req.ticker or "AMD", date_str=req.date)
    duration_ms = (time.time() - t0) * 1000
    is_err = isinstance(res, str) and (res.startswith("Python Execution Error:") or res.startswith("Security Error:"))
    return {
        "success": not is_err,
        "output": res,
        "error": res if is_err else None,
        "ticker": req.ticker,
        "duration_ms": round(duration_ms, 2)
    }



@router.delete("/api/copilot/chats/session/{session_id}")
def delete_copilot_chat_session_endpoint(session_id: str):
    """Deletes an entire chat session from SQLite."""
    _init_db()
    with _get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM copilot_chat_history WHERE session_id = ?", (session_id,))
        conn.commit()
    return {"status": "ok", "deleted_session_id": session_id}


@router.post("/api/copilot/chat")
def copilot_chat_endpoint(req: CopilotChatRequest):
    """
    Interactive Copilot Q&A endpoint supporting dossier-specific follow-ups,
    live price quotes, options chain lookups, and general market analysis.
    """
    try:
        from src.clients.llm_client import query_local_llm
        
        question_val = (req.question or req.prompt or "").strip()
        system_prompt, messages, user_prompt, ticker_u, date_str = _build_copilot_context_and_tools(
            question=question_val,
            explicit_ticker=req.ticker,
            date_str=req.date,
            history=req.history,
            session_id=req.session_id,
            board_context=req.board_context,
        )

        session_id = req.session_id or f"sess_{ticker_u}_{date_str}_{uuid.uuid4().hex[:8]}"
        _save_chat_turn(session_id, ticker_u, date_str, "user", question_val)

        chat_cfg = get_chat_llm_config()
        if chat_cfg["provider"] == "openrouter":
            try:
                response_text = query_local_llm(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    messages=messages,
                    use_tools=True,
                    use_openrouter=True,
                    model=chat_cfg["model"],
                    max_tokens=4096,
                )
            except Exception as llm_err:
                response_text = f"⚠️ **OpenRouter Chat Error:** {llm_err}"
        else:
            # Fast check if local LLM port 8000 is listening
            llm_alive = False
            import socket
            try:
                with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                    llm_alive = True
            except Exception:
                llm_alive = False

            if not llm_alive:
                response_text = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server.bat` (or `start_market.bat`) to ask follow-up questions."
            else:
                try:
                    response_text = query_local_llm(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        messages=messages,
                        use_tools=True,
                        use_openrouter=False,
                        max_tokens=4096,
                    )
                except Exception as llm_err:
                    response_text = f"⚠️ **LLM Inference Error:** {llm_err}"

        _save_chat_turn(session_id, ticker_u, date_str, "assistant", response_text)

        return {
            "answer": response_text,
            "ticker": ticker_u,
            "date": date_str,
            "session_id": session_id,
        }
    except Exception as e:
        logger.error(f"Error in copilot chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class _AlwaysConnected:
    """The generation runs detached from the HTTP connection, so it never sees a disconnect."""

    async def is_disconnected(self) -> bool:
        return False


_DETACHED_TASKS: set = set()


async def _detached_stream(gen):
    """Run `gen` as its own task and relay its events; the task finishes (and saves the answer)
    even when the browser leaves the page and this relay is cancelled."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for item in gen:
                queue.put_nowait(item)
        except Exception as e:
            logger.error(f"Detached chat generation failed: {e}", exc_info=True)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(pump())
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_DETACHED_TASKS.discard)
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


@router.post("/api/copilot/chat/stream")
async def copilot_chat_stream_endpoint(req: CopilotChatRequest, request: Request):
    """
    Real-time Server-Sent Events (SSE) token-by-token streaming endpoint for REV CHAT.
    Provides fast, conversational market intelligence, live options chains, real-time quotes, and on-demand scrapes.
    Persists all turns into SQLite copilot_chat_history.
    Supports WYSIWYG board telemetry and multimodal vision input (Qwen 27B --mmproj).
    """
    request = _AlwaysConnected()
    try:
        question_text = (req.question or req.prompt or "").strip()
        if not question_text and req.image_data:
            question_text = "Please analyze this attached chart / snapshot and provide actionable trade execution guidance."
        elif not question_text:
            question_text = "Provide a summary of current market status and actionable setups."

        initial_ticker = (req.ticker or "").strip().upper()

        async def token_generator():
            # 1. Immediately yield initial connection event so client receives HTTP 200 in <50ms
            yield f"data: {json.dumps({'status': 'connecting', 'ticker': initial_ticker})}\n\n"

            if await request.is_disconnected():
                return

            from starlette.concurrency import run_in_threadpool
            session_id = req.session_id or f"sess_{initial_ticker or 'GENERAL'}_{uuid.uuid4().hex[:8]}"
            _save_chat_turn(session_id, initial_ticker, req.date, "user", question_text)
            try:
                build_task = asyncio.create_task(run_in_threadpool(
                    _build_copilot_context_and_tools,
                    question=question_text,
                    explicit_ticker=req.ticker,
                    date_str=req.date,
                    history=req.history,
                    session_id=req.session_id,
                    board_context=req.board_context,
                ))
                while True:
                    done, _ = await asyncio.wait([build_task], timeout=2.0)
                    if not done:
                        if await request.is_disconnected():
                            build_task.cancel()
                            return
                        yield f"data: {json.dumps({'status': 'progress', 'step': 'Compiling market intelligence & quantitative context...'})}\n\n"
                    else:
                        system_prompt, messages, user_prompt, ticker_u, date_str = build_task.result()
                        break
            except Exception as ce:
                logger.error(f"Error compiling copilot context: {ce}", exc_info=True)
                _save_chat_turn(session_id, initial_ticker, req.date, "assistant", f"❌ Context compilation error: {ce}")
                yield f"data: {json.dumps({'error': f'Context compilation error: {ce}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            chat_cfg = get_chat_llm_config()
            is_openrouter = (chat_cfg["provider"] == "openrouter")

            if not is_openrouter:
                # Fast socket check for local port 8000
                import socket
                llm_alive = False
                try:
                    with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                        llm_alive = True
                except Exception:
                    llm_alive = False

                if not llm_alive:
                    offline_msg = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server.bat` to stream responses in real time."
                    yield f"data: {json.dumps({'token': offline_msg})}\n\n"
                    yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                    yield "data: [DONE]\n\n"
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", offline_msg)
                    return

            if await request.is_disconnected():
                return

            client, http_client = _make_async_openai_client(
                base_url=chat_cfg["base_url"],
                api_key=chat_cfg["api_key"],
                default_headers=chat_cfg.get("default_headers"),
            )

            if is_openrouter:
                model_name = chat_cfg["model"]
            else:
                try:
                    models = await client.models.list()
                    model_name = models.data[0].id
                except Exception:
                    model_name = "gpt-4"

                # Fast check if local LLM port 8000 slots are under heavy prompt prefill or dual occupancy
                slot_notice = None
                try:
                    base_host = os.getenv("LLM_LOCAL_URL", "http://127.0.0.1:8000/v1").split("/v1")[0]
                    import urllib.request
                    with urllib.request.urlopen(f"{base_host}/slots", timeout=0.3) as s_resp:
                        s_data = json.loads(s_resp.read().decode("utf-8"))
                        heavy_prefill = any(
                            s.get("is_processing") and s.get("n_prompt_tokens", 0) > 8000
                            and s.get("n_prompt_tokens_processed", 0) < s.get("n_prompt_tokens", 0)
                            for s in s_data
                        )
                        all_busy = all(s.get("is_processing") for s in s_data) if s_data else False
                        if heavy_prefill:
                            slot_notice = "⚡ Deep Research context ingestion active on GPU Slot 1. Your chat is assigned to Slot 0 with shared GPU compute."
                        elif all_busy:
                            slot_notice = "⏳ Both GPU slots active. Request queued in Slot 0 — streaming will begin shortly."
                except Exception:
                    pass

                if slot_notice:
                    yield f"data: {json.dumps({'status': 'slot_notice', 'notice': slot_notice})}\n\n"

            yield f"data: {json.dumps({'status': 'provider', 'provider': chat_cfg['provider'], 'model': model_name or 'Qwen (Local GPU)'})}\n\n"

            # Multimodal Vision Payload (Qwen 27B Vision Projector or multimodal cloud)
            if req.image_data and messages:
                if messages[-1].get("role") == "user":
                    raw_user_content = messages[-1].get("content", "")
                    if isinstance(raw_user_content, str):
                        messages[-1]["content"] = [
                            {"type": "text", "text": raw_user_content},
                            {"type": "image_url", "image_url": {"url": req.image_data}}
                        ]

            from src.clients.llm_client import TOOLS, execute_tool_call

            class _MockFunc:
                def __init__(self, name: str, arguments: str):
                    self.name = name
                    self.arguments = arguments

            class _MockToolCall:
                def __init__(self, id_str: str, name: str, arguments: str):
                    self.id = id_str
                    self.function = _MockFunc(name, arguments)

            full_answer_chunks = []
            answer_saved = False
            max_turns = 2
            turn = 0
            tools_enabled = True
            stream_response = None
            # A silent stream is a failure: if no token has arrived after this long the
            # request is queued behind deep research on the shared GPU, and the user is
            # told "GPU busy, queued" instead of watching an empty stream.
            stream_t0 = time.monotonic()
            first_token_t = None
            last_token_t = None
            busy_notice_sent = False
            generating_notice_sent = False

            try:
                while turn < max_turns:
                    turn += 1
                    tool_calls_acc = {}
                    turn_content_chunks = []
                    turn_reasoning_chunks = []
                    stream_t0 = time.monotonic()
                    first_token_t = None
                    last_token_t = None
                    busy_notice_sent = False
                    generating_notice_sent = False

                    # Force synthesis on final allowed turn
                    turn_tools_enabled = tools_enabled and (turn < max_turns)
                    if not turn_tools_enabled and tools_enabled:
                        tools_enabled = False
                        if messages and messages[-1].get("role") != "user":
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"Data collection passes complete. Do not call any more tools or emit <tool_call> tags. "
                                    f"Now synthesize all findings and provide your direct, final markdown answer to the user's specific question: '{question_text}'. "
                                    f"CRITICAL: If the user holds active Schwab positions in {initial_ticker or 'this stock'}, analyze their EXACT holdings, contracts, strikes, and expirations. "
                                    f"Never output generic hypothetical text like 'If you hold shares... If you hold options...'. Address their real legs directly."
                                )
                            })

                    create_kwargs = {
                        "model": model_name,
                        "messages": messages,
                        "max_tokens": 4096,
                        "temperature": 0.2,
                        "stream": True,
                    }
                    if not is_openrouter:
                        create_kwargs["extra_body"] = {"cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}}
                    else:
                        create_kwargs["extra_body"] = {"include_reasoning": False}

                    if turn_tools_enabled:
                        create_kwargs["tools"] = TOOLS
                        create_kwargs["tool_choice"] = "auto"

                    # The call itself blocks while the GPU has no free slot, before any token loop
                    # exists, so poll it and keep the user informed instead of a silent stream.
                    from src.utils import chat_priority

                    if not is_openrouter:
                        chat_priority.mark_waiting()
                    open_task = asyncio.create_task(client.chat.completions.create(**create_kwargs))
                    client_gone = False
                    while True:
                        opened, _ = await asyncio.wait([open_task], timeout=CHAT_WAIT_TICK_S)
                        if opened:
                            if not is_openrouter:
                                chat_priority.clear()
                            break
                        if not is_openrouter:
                            chat_priority.mark_waiting()
                        if await request.is_disconnected():
                            open_task.cancel()
                            if not is_openrouter:
                                chat_priority.clear()
                            client_gone = True
                            break
                        if not busy_notice_sent and time.monotonic() - stream_t0 >= CHAT_FIRST_TOKEN_BUSY_S:
                            busy_notice_sent = True
                            busy_msg = (
                                "⏳ GPU busy, queued — deep research holds the GPU; your chat is queued and will stream as soon as a slot frees."
                                if not is_openrouter
                                else f"⏳ Waiting for response from OpenRouter ({model_name})..."
                            )
                            yield f"data: {json.dumps({'status': 'slot_notice', 'notice': busy_msg})}\n\n"
                        else:
                            yield ": ping\n\n"
                    if client_gone:
                        break
                    try:
                        stream_response = open_task.result()
                    except Exception as stream_call_err:
                        if tools_enabled and ("400" in str(stream_call_err) or "tool" in str(stream_call_err).lower()):
                            tools_enabled = False
                            create_kwargs.pop("tools", None)
                            create_kwargs.pop("tool_choice", None)
                            stream_response = await client.chat.completions.create(**create_kwargs)
                        else:
                            raise

                    aiter = stream_response.__aiter__()
                    next_chunk_task = None
                    while True:
                        if next_chunk_task is None:
                            next_chunk_task = asyncio.create_task(aiter.__anext__())
                        done, _ = await asyncio.wait([next_chunk_task], timeout=CHAT_WAIT_TICK_S)
                        if not done:
                            if await request.is_disconnected():
                                next_chunk_task.cancel()
                                break
                            now_t = time.monotonic()
                            waiting_on_first = first_token_t is None
                            idle_for = now_t - (last_token_t or stream_t0)
                            if not busy_notice_sent and (
                                (waiting_on_first and now_t - stream_t0 >= CHAT_FIRST_TOKEN_BUSY_S)
                                or (not waiting_on_first and idle_for >= CHAT_STALL_BUSY_S)
                            ):
                                busy_notice_sent = True
                                busy_msg = (
                                    "⏳ GPU busy, queued — deep research holds the GPU; your chat is queued and will stream as soon as a slot frees."
                                    if not is_openrouter
                                    else f"⏳ Waiting for response from OpenRouter ({model_name})..."
                                )
                                yield f"data: {json.dumps({'status': 'slot_notice', 'notice': busy_msg})}\n\n"
                            if not full_answer_chunks and not generating_notice_sent:
                                generating_notice_sent = True
                                gen_msg = (
                                    f"⚡ Generating response via OpenRouter ({model_name})..."
                                    if is_openrouter
                                    else "⚡ Generating response on GPU Slot 0 (shared compute)..."
                                )
                                yield f"data: {json.dumps({'status': 'slot_notice', 'notice': gen_msg})}\n\n"
                            else:
                                yield ": ping\n\n"
                            continue
                        try:
                            chunk = next_chunk_task.result()
                            next_chunk_task = None
                        except StopAsyncIteration:
                            break
                        except (asyncio.CancelledError, GeneratorExit):
                            break

                        if await request.is_disconnected():
                            break
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            delta_content = getattr(delta, "content", None)
                            if delta_content:
                                now_t = time.monotonic()
                                if first_token_t is None:
                                    first_token_t = now_t
                                last_token_t = now_t
                                busy_notice_sent = False
                                turn_content_chunks.append(delta_content)
                                # Suppress streaming raw <tool_call> and <think> XML markup to client
                                if "<tool_call>" not in delta_content and "<function=" not in delta_content and "<think>" not in delta_content and "</think>" not in delta_content:
                                    full_answer_chunks.append(delta_content)
                                    yield f"data: {json.dumps({'token': delta_content})}\n\n"
                            reasoning_delta = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                            if reasoning_delta:
                                now_t = time.monotonic()
                                if first_token_t is None:
                                    first_token_t = now_t
                                last_token_t = now_t
                                busy_notice_sent = False
                                turn_reasoning_chunks.append(reasoning_delta)
                                yield f"data: {json.dumps({'status': 'thinking', 'thought': reasoning_delta})}\n\n"
                            if hasattr(delta, "tool_calls") and delta.tool_calls:
                                for tc in delta.tool_calls:
                                    idx = tc.index
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {"id": tc.id or f"call_{idx}_{turn}", "name": "", "arguments": ""}
                                    if tc.id:
                                        tool_calls_acc[idx]["id"] = tc.id
                                    if tc.function:
                                        if tc.function.name:
                                            tool_calls_acc[idx]["name"] += tc.function.name
                                        if tc.function.arguments:
                                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

                    if stream_response is not None:
                        try:
                            await stream_response.close()
                        except Exception:
                            pass
                        stream_response = None

                    if await request.is_disconnected():
                        break

                    # If model emitted tool calls in raw text format rather than native JSON
                    if not tool_calls_acc and turn < max_turns:
                        from src.clients.llm_client import _parse_text_tool_calls
                        raw_turn_text = "".join(turn_content_chunks)
                        text_tcs = _parse_text_tool_calls(raw_turn_text)
                        if text_tcs:
                            for idx, tc in enumerate(text_tcs):
                                args_str = json.dumps(tc.function.arguments) if isinstance(tc.function.arguments, dict) else str(tc.function.arguments)
                                tool_calls_acc[idx] = {
                                    "id": tc.id,
                                    "name": tc.function.name,
                                    "arguments": args_str,
                                }

                    if not tool_calls_acc:
                        # Generation finished; no further tools needed
                        if not turn_content_chunks and turn_reasoning_chunks:
                            reasoning_fallback = "".join(turn_reasoning_chunks).strip()
                            if reasoning_fallback and "<tool_call>" not in reasoning_fallback:
                                full_answer_chunks.append(reasoning_fallback)
                                yield f"data: {json.dumps({'token': reasoning_fallback})}\n\n"
                        break

                    # Append assistant message with emitted tool calls
                    turn_content = "".join(turn_content_chunks)
                    asst_msg = {
                        "role": "assistant",
                        "content": turn_content if turn_content else None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]}
                            }
                            for tc in tool_calls_acc.values()
                        ]
                    }
                    messages.append(asst_msg)

                    # Inform UI of active tool execution (live timeline start)
                    tool_names = [tc["name"] for tc in tool_calls_acc.values() if tc.get("name")]
                    if tool_names:
                        yield f"data: {json.dumps({'status': 'tool_start', 'tools': tool_names})}\n\n"

                    # Execute all tools concurrently in parallel threadpool
                    _tool_t0 = asyncio.get_event_loop().time()

                    async def _run_single_tool(tc_dict):
                        mock_tc = _MockToolCall(tc_dict["id"], tc_dict["name"], tc_dict["arguments"])
                        _t0 = asyncio.get_event_loop().time()
                        try:
                            tool_res = await asyncio.to_thread(execute_tool_call, mock_tc, date_str, False, ticker_u or initial_ticker or "")
                            res_str = str(tool_res) if tool_res is not None else "No output."
                            ok, err = True, None
                        except Exception as ex_tool:
                            res_str = f"Tool execution error: {ex_tool}"
                            ok, err = False, str(ex_tool)
                        return {
                            "role": "tool",
                            "tool_call_id": tc_dict["id"],
                            "name": tc_dict["name"],
                            "content": res_str,
                            "_ms": int((asyncio.get_event_loop().time() - _t0) * 1000),
                            "_ok": ok,
                            "_err": err,
                            "_chars": len(res_str),
                        }

                    async def _execute_all_tools():
                        return await asyncio.gather(*[_run_single_tool(tc) for tc in tool_calls_acc.values()])

                    tool_task = asyncio.create_task(_execute_all_tools())
                    while True:
                        done, _ = await asyncio.wait([tool_task], timeout=2.0)
                        if done:
                            tool_results = tool_task.result()
                            break
                        yield ": ping\n\n"
                    _tool_ms = int((asyncio.get_event_loop().time() - _tool_t0) * 1000)
                    for tr in tool_results:
                        messages.append({k: v for k, v in tr.items() if not k.startswith("_")})

                    # Live timeline completion (names, durations, ok/err) for the UI.
                    # The payload is hoisted out of the f-string: a replacement field spanning
                    # multiple lines is PEP 701 syntax (3.12+) and a SyntaxError on 3.10/3.11.
                    tool_done_payload = {
                        'status': 'tool_done',
                        'tools': [
                            {
                                'name': tr.get('name', ''),
                                'ms': tr.get('_ms', 0),
                                'ok': tr.get('_ok', True),
                                'chars': tr.get('_chars', 0),
                            }
                            for tr in tool_results
                        ],
                        'total_ms': _tool_ms,
                    }
                    yield f"data: {json.dumps(tool_done_payload)}\n\n"

                import re
                raw_full = "".join(full_answer_chunks)
                complete_answer = re.sub(r"<think>.*?</think>", "", raw_full, flags=re.DOTALL)
                if "<think>" in complete_answer and "</think>" not in complete_answer:
                    complete_answer = complete_answer.split("<think>")[0]
                complete_answer = re.sub(r"<tool_call>.*?</tool_call>", "", complete_answer, flags=re.DOTALL)
                complete_answer = re.sub(r"<function=.*?>.*?</function>", "", complete_answer, flags=re.DOTALL)
                complete_answer = re.sub(r"<parameter=.*?>.*?</parameter>", "", complete_answer, flags=re.DOTALL)
                complete_answer = re.sub(r"</?[a-zA-Z0-9_]+>", "", complete_answer)
                complete_answer = re.sub(r"⚙️\s*\*Executing live tool.*?\(s\):.*?\*", "", complete_answer)
                complete_answer = complete_answer.strip()
                if complete_answer and len(complete_answer) > 20:
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", complete_answer)
                    answer_saved = True

                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                if not (await request.is_disconnected()):
                    err_msg = f"⚠️ **Stream Error:** {stream_err}"
                    yield f"data: {json.dumps({'error': str(stream_err)})}\n\n"
                    yield "data: [DONE]\n\n"
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", err_msg)
                    answer_saved = True
            finally:
                from src.utils import chat_priority as _cp

                _cp.clear()
                if not answer_saved and full_answer_chunks:
                    import re as _re
                    partial = _re.sub(r"<tool_call>.*?</tool_call>|<function=.*?>.*?</function>|<parameter=.*?>.*?</parameter>|</?[a-zA-Z0-9_]+>", "", "".join(full_answer_chunks), flags=_re.DOTALL).strip()
                    if len(partial) > 20:
                        _save_chat_turn(session_id, ticker_u, date_str, "assistant", partial + "\n\n_(interrupted: you left the page before the answer finished)_")
                if stream_response is not None:
                    try:
                        await stream_response.close()
                    except Exception:
                        pass
                try:
                    await client.close()
                except Exception:
                    pass
                if 'http_client' in locals() and http_client is not None:
                    try:
                        await http_client.aclose()
                    except Exception:
                        pass

        return StreamingResponse(
            _detached_stream(token_generator()),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"Error in copilot chat stream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

