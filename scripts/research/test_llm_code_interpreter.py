"""
scripts/research/test_llm_code_interpreter.py

Tests the local LLM's ability to autonomously invoke the `execute_python_code` tool
to calculate exact mathematical values from datawindow.csv and simulate options spread payoffs.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.clients.llm_client import query_local_llm
from src.clients import options_client


def test_code_interpreter_on_llm(ticker: str = "AMD", date_str: str = "2026-08-16"):
    ticker = ticker.upper()
    options_client._ACTIVE_TICKER = ticker

    system_prompt = (
        "You are an elite quantitative researcher and derivatives hedge fund analyst. "
        "You have access to tools including `execute_python_code` (which has pre-loaded 'df' of 1-year OHLCV data "
        "and 'dw' Data Window dict) and market data tools.\n\n"
        "Whenever asked to compute quantitative statistics or options spread math, "
        "YOU MUST CALL the `execute_python_code` tool to compute the exact numbers deterministically."
    )

    user_prompt = (
        f"Perform the following quantitative tasks for {ticker} by executing Python code via tool calls:\n\n"
        "1. Using pre-loaded `df`, calculate the exact average volume on green bars (close > open) vs red bars (close < open) "
        "over the last 60 trading days, and compute the volume ratio.\n\n"
        "2. Calculate the exact Max Profit, Max Loss, Breakeven, and R:R for an AMD $520 Call / $550 Call Debit Spread "
        "assuming a net debit of $9.80 per share.\n\n"
        "Execute your Python code tool call, inspect the output, and present the verified results in clean Markdown."
    )

    print("=" * 70)
    print(f"TESTING LLM AUTONOMOUS PYTHON CODE INTERPRETER TOOL ({ticker})")
    print("=" * 70)

    response = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        use_tools=True,
        disable_thinking=True,
        max_tokens=2048,
        use_openrouter=False,
    )

    print("\n" + "=" * 70)
    print("LLM RESPONSE WITH EXECUTED CODE RESULTS:")
    print("=" * 70)
    print(response)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test LLM code interpreter tool")
    parser.add_argument("ticker", nargs="?", default="AMD", help="Ticker symbol")
    parser.add_argument("--date", default="2026-08-16", help="Date folder")
    args = parser.parse_args()

    test_code_interpreter_on_llm(args.ticker, args.date)
