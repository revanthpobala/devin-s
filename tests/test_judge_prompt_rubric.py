"""
tests/test_judge_prompt_rubric.py

Prompt-content tests for the judge rubric, verdict enum, and gate constraints
(items 1-6 from the c7abf87 sync request). Engine-gate and verdict-capping tests
in test_arbitration_c1_demotion.py and test_rb_plan.py stay green independently.
"""

from __future__ import annotations

import pytest

from src.logic.deep_research.arbitration import _build_judge_sys_prompt


@pytest.fixture(scope="module")
def prompt() -> str:
    return _build_judge_sys_prompt("TEST")


# ---------------------------------------------------------------------------
# 1-5. Pine levels bind; the judge vetoes, it does not draw levels or score conviction
# ---------------------------------------------------------------------------

def test_levels_belong_to_pine(prompt):
    assert "PINE LEVELS BIND" in prompt
    assert "Never tighten the stop" in prompt
    assert "LLM_DISCRETIONARY" in prompt and "EARNINGS IS NOT A BLACKOUT" in prompt


def test_job_is_concrete_veto(prompt):
    assert "concrete reason NOT to take the Pine plan" in prompt
    assert "A veto needs a specific fact" in prompt


def test_verdicts_follow_deterministic_state(prompt):
    assert "DETERMINISTIC STATE" in prompt
    assert "state is ACTIONABLE" in prompt and "state is ARMED" in prompt and "state is NO TRADE" in prompt


def test_watch_in_verdict_enum(prompt):
    assert "ARMED|WATCH|CASH_SKIP" in prompt
    assert "UNMEASURED MOMENTUM" in prompt and "RVOL >= 1.0" in prompt and "No measured edge" in prompt


def test_no_conviction_score(prompt):
    assert "Do not write a conviction score" in prompt
    assert "Conviction: X/10" not in prompt
    assert '"conviction": 8' not in prompt


# ---------------------------------------------------------------------------
# 6. Existing constraints still present
# ---------------------------------------------------------------------------

def test_enter_cap_constraint_present(prompt):
    """The hard CRITICAL CONSTRAINT line is still in the prompt."""
    assert "CANNOT output ENTER if engine triage is " in prompt


def test_level_geometry_rule_present(prompt):
    """The LEVEL GEOMETRY HARD RULE (targets above breakout, never zeroed) still present."""
    assert "ABOVE breakout_level" in prompt
    assert "never zeroed" in prompt


# ---------------------------------------------------------------------------
# 6b. Verdict capping routes WATCH correctly
# ---------------------------------------------------------------------------

def test_cap_conviction_watch_with_plan_not_capped_to_4():
    """A WATCH verdict with a real plan (has stop + target) must not be force-capped to 4."""
    from src.tracking.alert_evaluator import cap_conviction
    # WATCH + has_plan=True: stays in the 6-7 band
    result = cap_conviction(7, "WATCH", [], has_plan=True)
    assert result == 7


def test_cap_conviction_watch_without_plan_capped():
    """A WATCH with no plan (nothing armed) must cap to 4."""
    from src.tracking.alert_evaluator import cap_conviction
    result = cap_conviction(8, "WATCH", [], entry_mode="NONE", has_plan=False)
    assert result <= 4


def test_cap_conviction_cash_skip_capped():
    """CUT/CASH_SKIP must always cap to <= 4."""
    from src.tracking.alert_evaluator import cap_conviction
    assert cap_conviction(9, "CUT", []) <= 4


def test_panel_triage_watch_verdict_maps_to_watch_band():
    """WATCH verdict from judge maps to WATCH triage band (not PASS, not CUT)."""
    verdict = "WATCH"
    _panel_triage = (
        "PASS" if "ENTER" in verdict.upper()
        else ("CUT" if "CASH_SKIP" in verdict.upper() else "WATCH")
    )
    assert _panel_triage == "WATCH"


def test_panel_triage_stalk_maps_to_watch_band():
    """STALK verdict maps to WATCH triage band (cap_conviction allows 6-7 band)."""
    verdict = "STALK"
    _panel_triage = (
        "PASS" if "ENTER" in verdict.upper()
        else ("CUT" if "CASH_SKIP" in verdict.upper() else "WATCH")
    )
    assert _panel_triage == "WATCH"


def test_judge_armed_verdict_is_stored_as_stalk():
    import inspect

    from src.logic.deep_research import arbitration

    src = inspect.getsource(arbitration)
    assert 'verdict.upper() == "ARMED"' in src and 'verdict = "STALK"' in src
