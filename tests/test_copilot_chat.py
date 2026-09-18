import pytest
import uuid
from datetime import datetime, timezone, timedelta
from run_ui import (
    _init_db,
    _save_chat_turn,
    get_copilot_chat_sessions_endpoint,
    get_copilot_chat_history_endpoint,
    delete_copilot_chat_session_endpoint,
    _detect_tickers_from_prompt,
    _detect_ticker_metadata,
    _get_company_name,
    _build_copilot_context_and_tools,
)


def test_copilot_chat_persistence_and_sessions():
    _init_db()
    sid = f"test_session_{uuid.uuid4().hex[:8]}"
    ticker = "AVGO"
    date_str = "2026-09-01"

    # Save user question & assistant response
    _save_chat_turn(sid, ticker, date_str, "user", "What is AVGO support zone?")
    _save_chat_turn(sid, ticker, date_str, "assistant", "AVGO support zone is $365 - $368.")

    # Test 15-day session discovery
    sessions_res = get_copilot_chat_sessions_endpoint(days=15)
    assert "sessions" in sessions_res
    matching = [s for s in sessions_res["sessions"] if s["session_id"] == sid]
    assert len(matching) == 1
    assert matching[0]["ticker"] == "AVGO"
    assert matching[0]["message_count"] == 2
    assert "What is AVGO support zone?" in matching[0]["first_prompt"]

    # Test retrieving history by session_id
    history_res = get_copilot_chat_history_endpoint(session_id=sid)
    assert len(history_res["messages"]) == 2
    assert history_res["messages"][0]["role"] == "user"
    assert history_res["messages"][1]["role"] == "assistant"

    # Test retrieving history by ticker
    ticker_history = get_copilot_chat_history_endpoint(ticker="AVGO", date=date_str)
    assert len(ticker_history["messages"]) >= 2

    # Clean up test session
    del_res = delete_copilot_chat_session_endpoint(sid)
    assert del_res["status"] == "ok"

    # Verify deleted
    post_del = get_copilot_chat_history_endpoint(session_id=sid)
    assert len(post_del["messages"]) == 0


def test_multi_ticker_prompt_detection():
    # Test correlated peer detection inside active modal
    tickers_1 = _detect_tickers_from_prompt("check EIX stock as well.", explicit_ticker="PCG")
    assert "EIX" in tickers_1
    assert "PCG" in tickers_1
    assert tickers_1[0] == "EIX"  # Prompt-mentioned ticker gets top focus

    # Test single standalone query
    tickers_2 = _detect_tickers_from_prompt("What is the options structure for $FLY?")
    assert "FLY" in tickers_2

    # Test company alias mapping
    tickers_3 = _detect_tickers_from_prompt("compare Edison International with Pacific Gas", explicit_ticker=None)
    assert "EIX" in tickers_3
    assert "PCG" in tickers_3


def test_copilot_python_math_execution():
    from run_ui import _build_single_ticker_context, copilot_execute_python_endpoint, ExecutePythonRequest

    # Test that Python quantitative math baseline and custom math execute without error
    prompt = "Execute python: print(f'Spread Width Math: {500 - 480}, Max Profit: {4.50}, Max Loss: {20 - 4.50}')"
    parts = _build_single_ticker_context("AVGO", "2026-08-22", prompt)
    joined = "\n".join(parts)
    
    assert "USER PYTHON CODE EXECUTION RESULTS" in joined
    assert "Spread Width Math: 20" in joined
    assert "Max Loss: 15.5" in joined

    # Test direct python execution endpoint
    req = ExecutePythonRequest(
        code="print(f'Kelly Fraction: {(0.65 * 2.0 - (1 - 0.65)) / 2.0:.4f}')",
        ticker="AVGO",
        date="2026-08-22"
    )
    res = copilot_execute_python_endpoint(req)
    assert "Kelly Fraction: 0.4750" in res["output"]
    assert res["duration_ms"] >= 0


def test_ticker_verification_and_confirmation():
    # 1. Inferred ticker from conversational text
    meta_inferred = _detect_ticker_metadata("ui path is na example. check the latest quote")
    assert meta_inferred["primary_ticker"] == "PATH"
    assert meta_inferred["is_inferred"] is True
    assert meta_inferred["is_explicit"] is False
    assert "UiPath" in meta_inferred["company_name"]

    # 2. Explicit $TICKER
    meta_explicit = _detect_ticker_metadata("What is the options chain for $PATH?")
    assert meta_explicit["primary_ticker"] == "PATH"
    assert meta_explicit["is_inferred"] is False
    assert meta_explicit["is_explicit"] is True

    # 3. Context builder injects verification directive when ticker is inferred
    sys_prompt, messages, user_prompt, ticker_u, date_str = _build_copilot_context_and_tools(
        question="ui path is na example. check the latest quote",
        explicit_ticker=None,
        date_str="2026-09-03"
    )
    assert ticker_u == "PATH"
    assert "MANDATORY TICKER VERIFICATION" in sys_prompt
    assert "action:ask?prompt=Yes,+analyze+$PATH" in sys_prompt
    assert "action:ask?prompt=No,+I+meant+$" in sys_prompt
    assert "Interactive Clarification & Ticker Verification" in sys_prompt


def test_board_context_injection():
    # Verify that board_context telemetry is injected at high priority in the copilot context
    mock_board = "**CURRENTLY VISIBLE OPTIONS FLOW TABLE FOR AMZN:**\nCALL $245.0 Exp 2026-09-09: Vol 6,365 vs OI 93 (68.4x OI)"
    sys_prompt, messages, user_prompt, ticker_u, date_str = _build_copilot_context_and_tools(
        question="What is the unusual flow on this table?",
        explicit_ticker="AMZN",
        date_str="2026-09-03",
        board_context=mock_board
    )
    assert "USER'S ACTIVE BOARD / SCREEN TELEMETRY" in sys_prompt
    assert "CALL $245.0 Exp 2026-09-09" in sys_prompt
    assert "68.4x OI" in sys_prompt


def test_targeted_schwab_strike_extractor():
    from run_ui import _extract_targeted_schwab_strikes

    # Test extracting targeted strikes from user prompt for AMZN
    res = _extract_targeted_schwab_strikes("AMZN", "245c and 250 puts oi right. check the schwab chain and see")
    if res:  # If Schwab credentials present in environment
        assert "TARGETED SCHWAB OPTIONS CHAIN LOOKUP" in res
        assert "245" in res
        assert "250" in res
        assert "Vol=" in res
        assert "OI=" in res
        assert "IN-THE-MONEY (ITM)" in res  # 245C is ITM for AMZN at ~$257!


def test_copilot_chat_request_multimodal_fields():
    from run_ui import CopilotChatRequest

    req = CopilotChatRequest(
        question="Inspect chart",
        ticker="AMZN",
        board_context="Active options table",
        image_data="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    assert req.board_context == "Active options table"
    assert req.image_data.startswith("data:image/png;base64,")


def test_unified_options_chain_and_memory_cache():
    import time
    from src.clients.options_client import fetch_options_chain_tool, _CHAIN_MEM_CACHE

    # Clear cache for clean test
    _CHAIN_MEM_CACHE.clear()

    # Call 1: Fetches unified chain
    t0 = time.time()
    chain = fetch_options_chain_tool("GOOGL", direction="BOTH", min_dte=20, max_dte=50)
    d1 = time.time() - t0
    assert chain is not None
    assert "Call Bid/Ask" in chain or "Call" in chain
    assert "Put Bid/Ask" in chain or "Put" in chain
    # Verify compact size: well under 5,000 chars (not 40k!)
    assert len(chain) < 5000

    # Call 2: In-memory cache hit (<10ms)
    t1 = time.time()
    cached_chain = fetch_options_chain_tool("GOOGL", direction="BOTH", min_dte=20, max_dte=50)
    d2 = time.time() - t1
    assert cached_chain == chain
    assert d2 < 0.05  # Instant memory cache hit




