import sys
from src.clients.llm_client import execute_tool_call, _DummyToolCall, _DummyFunction

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

for ticker in ["AAPL", "AVGO", "FLY"]:
    print(f"\n==========================================")
    print(f"TESTING TOOL CALL: fetch_sec_filings for {ticker}")
    print(f"==========================================")
    tc = _DummyToolCall(
        id_str="test_call",
        name="fetch_sec_filings",
        arguments={"ticker": ticker, "limit": 3}
    )
    result = execute_tool_call(tc, date_str="2026-09-01")
    print(result)
