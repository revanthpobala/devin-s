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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.ui.services.copilot_context import _build_copilot_context_and_tools
from src.ui.state import get_db as _get_db, init_db as _init_db

logger = logging.getLogger("ui_server")

router = APIRouter(tags=["copilot"])


class CopilotChatRequest(BaseModel):
    question: Optional[str] = ""
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
    """Executes sandboxed Python code with pre-loaded df (300 bars x 85 indicators), dw, numpy, pandas, scipy, and stats."""
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
        
        system_prompt, messages, user_prompt, ticker_u, date_str = _build_copilot_context_and_tools(
            question=req.question,
            explicit_ticker=req.ticker,
            date_str=req.date,
            history=req.history,
            session_id=req.session_id,
            board_context=req.board_context,
        )

        session_id = req.session_id or f"sess_{ticker_u}_{date_str}_{uuid.uuid4().hex[:8]}"
        _save_chat_turn(session_id, ticker_u, date_str, "user", req.question)

        # Fast check if local LLM port 8000 is listening
        llm_alive = False
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                llm_alive = True
        except Exception:
            llm_alive = False

        if not llm_alive:
            response_text = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server_qwen38_27b_q4.bat` (or `start_market.bat`) to ask follow-up questions."
        else:
            try:
                response_text = query_local_llm(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    messages=messages,
                    use_tools=True,
                    use_openrouter=False,
                    max_tokens=2048,
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


@router.post("/api/copilot/chat/stream")
async def copilot_chat_stream_endpoint(req: CopilotChatRequest, request: Request):
    """
    Real-time Server-Sent Events (SSE) token-by-token streaming endpoint for REV CHAT.
    Provides fast, conversational market intelligence, live options chains, real-time quotes, and on-demand scrapes.
    Persists all turns into SQLite copilot_chat_history.
    Supports WYSIWYG board telemetry and multimodal vision input (Qwen 27B --mmproj).
    """
    try:
        question_text = (req.question or "").strip()
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
                    done, _ = await asyncio.wait([build_task], timeout=4.0)
                    if not done:
                        if await request.is_disconnected():
                            build_task.cancel()
                            return
                        yield ": ping\n\n"
                    else:
                        system_prompt, messages, user_prompt, ticker_u, date_str = build_task.result()
                        break
            except Exception as ce:
                logger.error(f"Error compiling copilot context: {ce}", exc_info=True)
                yield f"data: {json.dumps({'error': f'Context compilation error: {ce}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            session_id = req.session_id or f"sess_{ticker_u}_{date_str}_{uuid.uuid4().hex[:8]}"
            _save_chat_turn(session_id, ticker_u, date_str, "user", question_text)

            # Fast socket check
            import socket
            llm_alive = False
            try:
                with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                    llm_alive = True
            except Exception:
                llm_alive = False

            if not llm_alive:
                offline_msg = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server_qwen38_27b_q4.bat` to stream responses in real time."
                yield f"data: {json.dumps({'token': offline_msg})}\n\n"
                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                yield "data: [DONE]\n\n"
                _save_chat_turn(session_id, ticker_u, date_str, "assistant", offline_msg)
                return

            if await request.is_disconnected():
                return

            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                base_url=os.getenv("LLM_LOCAL_URL", "http://127.0.0.1:8000/v1"),
                api_key="sk-no-key-required",
                timeout=180,
            )
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

            # Multimodal Vision Payload (Qwen 27B Vision Projector)
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
            max_turns = 2
            turn = 0
            tools_enabled = True
            stream_response = None

            try:
                while turn < max_turns:
                    turn += 1
                    tool_calls_acc = {}
                    turn_content_chunks = []

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
                        "max_tokens": 2048,
                        "temperature": 0.2,
                        "stream": True,
                        "extra_body": {"cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}},
                    }
                    if turn_tools_enabled:
                        create_kwargs["tools"] = TOOLS
                        create_kwargs["tool_choice"] = "auto"

                    try:
                        stream_response = await client.chat.completions.create(**create_kwargs)
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
                        done, _ = await asyncio.wait([next_chunk_task], timeout=3.0)
                        if not done:
                            if await request.is_disconnected():
                                next_chunk_task.cancel()
                                break
                            if not full_answer_chunks:
                                yield f"data: {json.dumps({'status': 'slot_notice', 'notice': '⚡ Generating response on GPU Slot 0 (shared compute)...'})}\n\n"
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
                            if hasattr(delta, "content") and delta.content:
                                turn_content_chunks.append(delta.content)
                                # Suppress streaming raw <tool_call> XML markup to client
                                if "<tool_call>" not in delta.content and "<function=" not in delta.content:
                                    full_answer_chunks.append(delta.content)
                                    yield f"data: {json.dumps({'token': delta.content})}\n\n"
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

                    # Inform UI of active tool execution
                    tool_names = [tc["name"] for tc in tool_calls_acc.values() if tc.get("name")]
                    if tool_names:
                        status_badge = f"\n\n⚙️ *Executing live tool(s): `{', '.join(tool_names)}`...*\n\n"
                        yield f"data: {json.dumps({'token': status_badge})}\n\n"

                    # Execute all tools concurrently in parallel threadpool
                    async def _run_single_tool(tc_dict):
                        mock_tc = _MockToolCall(tc_dict["id"], tc_dict["name"], tc_dict["arguments"])
                        try:
                            tool_res = await asyncio.to_thread(execute_tool_call, mock_tc, date_str)
                            res_str = str(tool_res) if tool_res is not None else "No output."
                        except Exception as ex_tool:
                            res_str = f"Tool execution error: {ex_tool}"
                        return {
                            "role": "tool",
                            "tool_call_id": tc_dict["id"],
                            "name": tc_dict["name"],
                            "content": res_str
                        }

                    tool_results = await asyncio.gather(*[_run_single_tool(tc) for tc in tool_calls_acc.values()])
                    for tr in tool_results:
                        messages.append(tr)

                import re
                raw_full = "".join(full_answer_chunks)
                complete_answer = re.sub(r"<tool_call>.*?</tool_call>", "", raw_full, flags=re.DOTALL)
                complete_answer = re.sub(r"<function=.*?>.*?</function>", "", complete_answer, flags=re.DOTALL)
                complete_answer = re.sub(r"<parameter=.*?>.*?</parameter>", "", complete_answer, flags=re.DOTALL)
                complete_answer = re.sub(r"</?[a-zA-Z0-9_]+>", "", complete_answer)
                complete_answer = re.sub(r"⚙️\s*\*Executing live tool.*?\(s\):.*?\*", "", complete_answer)
                complete_answer = complete_answer.strip()
                if complete_answer and len(complete_answer) > 20 and not (await request.is_disconnected()):
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", complete_answer)

                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                if not (await request.is_disconnected()):
                    err_msg = f"⚠️ **Stream Error:** {stream_err}"
                    yield f"data: {json.dumps({'error': str(stream_err)})}\n\n"
                    yield "data: [DONE]\n\n"
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", err_msg)
            finally:
                if stream_response is not None:
                    try:
                        await stream_response.close()
                    except Exception:
                        pass
                try:
                    await client.close()
                except Exception:
                    pass

        return StreamingResponse(
            token_generator(),
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

