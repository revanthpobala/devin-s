"""
Test end-to-end dispatch of fetch_prior_research tool.
"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.clients.llm_client import execute_tool_call, fetch_prior_research_tool

# 1. Test direct tool helper
hood_prior = fetch_prior_research_tool("HOOD", lookback_days=14, date_str="2026-08-17")
assert "PRIOR RESEARCH SUMMARY FOR HOOD" in hood_prior
assert "2026-08-14" in hood_prior
print("Direct helper test: PASSED")

# 2. Test tool_call mock dispatch
mock_tool = MagicMock()
mock_tool.function.name = "fetch_prior_research"
mock_tool.function.arguments = json.dumps({"ticker": "HOOD", "lookback_days": 14})

res = execute_tool_call(mock_tool, date_str="2026-08-17")
assert "PRIOR RESEARCH SUMMARY FOR HOOD" in res
assert "2026-08-14" in res
print("Mock tool dispatch test: PASSED")

print("ALL TESTS PASSED!")
