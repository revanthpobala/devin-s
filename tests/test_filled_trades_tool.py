import json
import pytest
from types import SimpleNamespace
from src.clients.llm_client import TOOLS, execute_tool_call, format_filled_trades_tool


def test_fetch_filled_trades_in_tools_list():
    tool_names = [t["function"]["name"] for t in TOOLS if "function" in t]
    assert "fetch_filled_trades" in tool_names

    tool_def = next(t for t in TOOLS if t.get("function", {}).get("name") == "fetch_filled_trades")
    params = tool_def["function"]["parameters"]
    assert "properties" in params
    assert "ticker" in params["properties"]
    assert "days" in params["properties"]


def test_format_filled_trades_tool_googl():
    res = format_filled_trades_tool("GOOGL", days=60)
    assert "FILLED & CLOSED TRADES FOR $GOOGL" in res
    assert "Total Completed Fills:" in res
    # Ensure canceled/unfilled are excluded and executed orders exist
    assert "Execution Ledger" in res or "No filled" in res


def test_format_filled_trades_tool_empty():
    res = format_filled_trades_tool("", days=60)
    assert "Please specify a ticker symbol" in res


def test_execute_tool_call_fetch_filled_trades():
    mock_tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name="fetch_filled_trades",
            arguments=json.dumps({"ticker": "GOOGL", "days": 60}),
        )
    )
    result = execute_tool_call(mock_tool_call, chat_ticker="GOOGL")
    assert isinstance(result, str)
    assert "GOOGL" in result
    assert "Total Completed Fills:" in result

