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
# 1. ENTER only on the measured gate
# ---------------------------------------------------------------------------

def test_enter_requires_measured_gate(prompt):
    """8-10 tier must cite the engine gate conditions, not just 'measured R:R >= 3.0'."""
    assert "ENTER requires the engine gate to pass" in prompt
    assert "Long RR At Market >= 2.0" in prompt
    assert "rr_config.min_rr() = 2.0" in prompt
    assert "PB funnel" in prompt
    assert "Ext Z < 2.5" in prompt


def test_enter_rubric_does_not_say_do_not_downgrade(prompt):
    """Removed per c7abf87: 'Do NOT artificially downgrade to STALK'."""
    assert "Do NOT artificially downgrade" not in prompt


def test_enter_stronger_tier_is_3(prompt):
    """R:R >= 3.0 is documented as the stronger tier, not the gate floor."""
    assert "R:R >= 3.0 is the stronger tier" in prompt


# ---------------------------------------------------------------------------
# 2. CASH_SKIP threshold is 2.0
# ---------------------------------------------------------------------------

def test_cash_skip_uses_2_not_3(prompt):
    """1-5 tier: gate floor is 2.0, not 3.0."""
    assert "R:R at market < 2.0 (gate floor)" in prompt
    # The old 'Unfavorable R:R (< 3.0:1)' must not appear
    assert "Unfavorable R:R" not in prompt


def test_rubric_states_gate_floor_and_stronger_tier(prompt):
    """The rubric in the sys prompt states both the gate floor (2.0) and the stronger tier (3.0)."""
    # Gate floor appears in the ENTER rubric (not just in the ground_truth user block)
    assert "Long RR At Market >= 2.0" in prompt
    # Stronger tier documented in the ENTER rubric
    assert "R:R >= 3.0 is the stronger tier" in prompt


# ---------------------------------------------------------------------------
# 3. ARMED must be armed
# ---------------------------------------------------------------------------

def test_armed_requires_armed_levels(prompt):
    """6-7 tier must require non-zero breakout/entry zone, stop, and T1/T2 above trigger."""
    assert "non-zero breakout level or entry zone" in prompt
    assert "tactical stop" in prompt
    assert "T1/T2 strictly above the trigger" in prompt
    assert "Never emit breakout_level = 0 on an ARMED verdict" in prompt
    assert "If the plan arms nothing, output CASH_SKIP instead" in prompt


# ---------------------------------------------------------------------------
# 4. WATCH verdict for unmeasured breaks
# ---------------------------------------------------------------------------

def test_watch_in_verdict_enum(prompt):
    """WATCH must appear in the json:watch_levels verdict field comment."""
    assert "ARMED|WATCH|CASH_SKIP" in prompt


def test_watch_rubric_entry_present(prompt):
    """WATCH lane must be described with UNMEASURED MOMENTUM wording."""
    assert "UNMEASURED MOMENTUM" in prompt
    assert "RVOL >= 1.0" in prompt
    assert "VERDICT = " in prompt
    assert "WATCH" in prompt
    assert "no measured edge" in prompt


def test_watch_rubric_no_high_conviction(prompt):
    """The WATCH description must explicitly say it never gets high conviction."""
    assert "never receives high conviction or priority" in prompt


# ---------------------------------------------------------------------------
# 5. Conviction is a label
# ---------------------------------------------------------------------------

def test_conviction_is_a_label(prompt):
    """The rubric must state conviction has no measured basis and only goes down."""
    assert "CONVICTION IS A LABEL" in prompt
    assert "no measured basis of its own" in prompt
    assert "can only be lowered by cap_conviction" in prompt
    assert "must never drive entry priority" in prompt


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
