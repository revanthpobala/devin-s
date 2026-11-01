import pytest
from run_ui import (
    _init_db,
    _detect_tickers_from_prompt,
    _detect_ticker_metadata,
    _build_copilot_context_and_tools,
)


@pytest.fixture(autouse=True)
def isolated_copilot_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "copilot_switch_test.db"))
    _init_db()


def test_personal_finance_and_market_reflection_resets_ticker_context():
    """Verify that personal finances, cash flow, paycheck, and trading reflections

    never inherit a prior conversation ticker or output a false Ticker Check banner.
    """
    history = [
        {"role": "user", "content": "7.7k will be used for INTC"},
        {"role": "assistant", "content": "INTC is currently consolidating near support."},
    ]

    # The exact utterance from the user's report
    q = "I am not gonna buy anything till my other paycheck hits though. It was a brutal month for me"
    meta = _detect_ticker_metadata(q, history=history)

    assert meta["primary_ticker"] == "GENERAL"
    assert meta["tickers"] == ["GENERAL"]
    assert meta["is_explicit"] is False
    assert meta["is_inferred"] is False
    assert meta["match_type"] == "none"

    sys_prompt, _, _, ticker_u, _ = _build_copilot_context_and_tools(q, history=history)
    assert ticker_u == "GENERAL"
    assert "MANDATORY TICKER VERIFICATION" not in sys_prompt
    assert "🎯 **Ticker Check**" not in sys_prompt
    assert "INTC" not in ticker_u


def test_common_financial_words_not_parsed_as_stock_tickers():
    """Verify that common English words (cash, pay, job, fund, save, grow, cost)

    never trigger phantom standalone stock tickers without an explicit $.
    """
    for phrase in [
        "What should I do with my cash balance?",
        "I need to wait for my pay to arrive next week.",
        "How do I balance my job and my trading?",
        "Is it better to keep money in an emergency fund?",
        "I want to save my capital for better setups.",
        "How can I grow my portfolio safely?",
        "What is my cost basis on these positions?",
    ]:
        meta = _detect_ticker_metadata(phrase)
        assert meta["primary_ticker"] == "GENERAL", f"Failed for phrase: '{phrase}' (got {meta['primary_ticker']})"
        assert meta["is_inferred"] is False


def test_legitimate_ticker_continuity_without_banner():
    """Verify that a legitimate follow-up question inherits the active ticker

    without showing the intrusive '🎯 Ticker Check' card on subsequent turns.
    """
    history = [
        {"role": "user", "content": "What is the setup on INTC?"},
        {"role": "assistant", "content": "INTC is consolidating near $21.50 with resistance at $23.00."},
    ]

    q = "What is its target?"
    meta = _detect_ticker_metadata(q, history=history)

    assert meta["primary_ticker"] == "INTC"
    assert meta["match_type"] == "history"
    assert meta["is_inferred"] is False

    sys_prompt, _, _, ticker_u, _ = _build_copilot_context_and_tools(q, history=history)
    assert ticker_u == "INTC"
    # Crucial: verification card must NOT be injected for natural conversational follow-up
    assert "MANDATORY TICKER VERIFICATION" not in sys_prompt


def test_inferred_alias_still_triggers_verification():
    """Verify that genuine ambiguous company aliases in the prompt still

    prompt the user with the Ticker Verification Card.
    """
    q = "ui path is na example. check the latest quote"
    meta = _detect_ticker_metadata(q)

    assert meta["primary_ticker"] == "PATH"
    assert meta["match_type"] == "alias"
    assert meta["is_inferred"] is True

    sys_prompt, _, _, ticker_u, _ = _build_copilot_context_and_tools(q)
    assert ticker_u == "PATH"
    assert "MANDATORY TICKER VERIFICATION" in sys_prompt
    assert "action:ask?prompt=Yes,+analyze+$PATH" in sys_prompt

