import pytest
from src.logic.process_survivor import _TRIAGE_SCHEMA


def test_triage_schema_supports_income_modes():
    properties = _TRIAGE_SCHEMA["properties"]
    entry_mode_enum = properties["entry_mode"]["enum"]

    assert "INCOME_CSP" in entry_mode_enum
    assert "INCOME_CC" in entry_mode_enum
    assert "INCOME_STRUCTURE" in entry_mode_enum
    assert "TREND_LONG" in entry_mode_enum
    assert "NONE" in entry_mode_enum
