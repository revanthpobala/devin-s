import pytest
from src.ui.services.copilot_context import (
    _detect_ticker_metadata,
    _detect_ticker_from_prompt,
    _is_general_conceptual_question,
    _build_copilot_context_and_tools,
)


def test_when_does_it_make_sense_to_buy_leap_spreads():
    """General options question with no ticker must return GENERAL, never LINE."""
    q = "when does it make sense to buy leap spreads"
    assert _is_general_conceptual_question(q) is True

    meta = _detect_ticker_metadata(q)
    assert meta["primary_ticker"] == "GENERAL"
    assert meta["tickers"] == ["GENERAL"]
    assert meta["is_explicit"] is False
    assert meta["is_inferred"] is False


def test_general_question_ignores_modal_explicit_ticker():
    """Even if frontend passes an explicit_ticker like LINE or AAPL, general question must stay GENERAL."""
    q = "when does it make sense to buy leap spreads"
    meta = _detect_ticker_metadata(q, explicit_ticker="LINE")
    assert meta["primary_ticker"] == "GENERAL"

    meta_aapl = _detect_ticker_metadata(q, explicit_ticker="AAPL")
    assert meta_aapl["primary_ticker"] == "GENERAL"


def test_general_question_does_not_inherit_from_history():
    """A prior conversation discussing LINE or AAPL must not pollute a new general strategy question."""
    q = "when does it make sense to buy leap spreads"
    history = [
        {"role": "user", "content": "Using my real book, give the dollar impact on my positions if it prints hot, in line, or cool."},
        {"role": "assistant", "content": "I detected LINE (Lineage, Inc.). Here is the analysis for LINE..."},
    ]
    meta = _detect_ticker_metadata(q, history=history)
    assert meta["primary_ticker"] == "GENERAL"
    assert "LINE" not in meta["tickers"]


def test_phrase_in_line_does_not_match_ticker_line():
    """The common conversational phrase 'in line' must not trigger ticker LINE."""
    q = "give the dollar impact on my positions if it prints hot, in line, or cool"
    meta = _detect_ticker_metadata(q)
    assert "LINE" not in meta["tickers"]
    assert meta["primary_ticker"] == "GENERAL"


def test_options_terms_do_not_match_as_tickers():
    """Option terms like LEAP, LEAPS, SPREAD, CALL, PUT must not match as tickers."""
    for term in ["leap", "leaps", "spread", "spreads", "call", "put", "strike", "straddle"]:
        q = f"explain how a {term} works in options"
        meta = _detect_ticker_metadata(q)
        assert meta["primary_ticker"] == "GENERAL"


def test_explicit_ticker_still_detected():
    """If user explicitly writes $AAPL or 'stock AAPL' in a leap spread question, AAPL is detected."""
    q = "when does it make sense to buy leap spreads on $AAPL"
    meta = _detect_ticker_metadata(q)
    assert meta["primary_ticker"] == "AAPL"
    assert meta["is_explicit"] is True


def test_continuity_inherits_prior_ticker():
    """When the question has anaphoric continuity ('its stop loss', 'what is the target'), history is inherited."""
    history = [
        {"role": "user", "content": "analyze $NVDA chart"},
        {"role": "assistant", "content": "NVDA is in Stage 2 advance with support at $118."},
    ]
    q = "what is its stop loss?"
    meta = _detect_ticker_metadata(q, history=history)
    assert meta["primary_ticker"] == "NVDA"


def test_build_copilot_context_returns_general():
    """Test full context builder for 'when does it make sense to buy leap spreads'."""
    q = "when does it make sense to buy leap spreads"
    sys_prompt, messages, user_prompt, primary_ticker, date_str = _build_copilot_context_and_tools(
        question=q,
        explicit_ticker="LINE",
        history=[{"role": "user", "content": "prints hot, in line, or cool"}],
    )
    assert primary_ticker == "GENERAL"
    # Ensure no Ticker Confirmation Card for LINE is generated
    assert "🎯 **Ticker Check**: I detected **$LINE**" not in sys_prompt
    assert "Conducting a rigorous quantitative audit and tactical execution review for $LINE" not in sys_prompt
    # Ensure options strategy directive is present
    assert "General Quantitative, Educational & Options Strategy Inquiries" in sys_prompt
