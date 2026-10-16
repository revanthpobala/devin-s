import json
from datetime import date

import pytest

from src.tracking import agent_trader as ag


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "db_path", lambda: tmp_path / "a.db")


def plan(pid=1, label="ACT", rr_top=3.0, price=100.0):
    return {"id": pid, "ticker": "ABC", "date": "2026-10-14", "side": "LONG", "last_price": price, "entry_zone_low": 98.0, "entry_zone_high": 101.0,
            "tactical_stop": 95.0, "target_1": 115.0, "target_2": None, "rr_live": 3.0, "conviction": 6,
            "action": {"label": label, "why": "w", "rr_top": rr_top}}


def take(sys_p, usr):
    return json.dumps({"decision": "TAKE", "technical": "t", "fundamental": "f", "reason": "both agree"})


def passed(sys_p, usr):
    return json.dumps({"decision": "PASS", "technical": "t", "fundamental": "binary earnings", "reason": "thesis says not an entry"})


def run(plans, ask, ed=lambda t: None, equity=100000.0):
    return ag.propose(plans, equity, ed, ask, thesis=lambda t, d: "thesis text")


def test_take_opens_sized_position_and_stop_closes_it():
    ev = run([plan()], take)
    assert ev[0]["kind"] == "AGENT_TAKE" and "bought 100 @ $100.00" in ev[0]["title"]
    out = ag.manage({1: 94.0}, date.today())
    assert out[0]["kind"] == "AGENT_EXIT" and out[0]["severity"] == "WARN"
    s = ag.summary()
    assert s["closed"]["n"] == 1 and s["closed"]["mean_r"] < -1.0


def test_target_exit_is_positive_r():
    run([plan()], take)
    ag.manage({1: 116.0}, date.today())
    assert ag.summary()["closed"]["mean_r"] > 2.5


def test_pass_opens_nothing_and_is_not_asked_twice():
    calls = []
    run([plan()], lambda s, u: calls.append(1) or passed(s, u))
    run([plan()], lambda s, u: calls.append(1) or passed(s, u))
    assert len(calls) == 1 and ag.summary()["open"] == [] and ag.by_plan()[1]["stance"] == "PASS"


def test_earnings_is_context_not_a_veto():
    calls = []
    run([plan()], lambda s, u: calls.append(u) or take(s, u), ed=lambda t: 3)
    assert len(calls) == 1 and '"earnings_in_days": 3' in calls[0]


def test_size_is_always_one_hundred_shares():
    assert ag.size(100000.0, 100.0, 95.0) == 100 and ag.size(1000.0, 400.0, 380.0) == 100 and ag.size(100000.0, 100.0, 100.0) == 0


def test_wait_plan_becomes_limit_order_that_fills_then_expires_unfilled_otherwise():
    run([plan(label="WAIT", price=105.0)], take)
    assert ag.summary()["open"][0]["state"] == "PENDING"
    ev = ag.manage({1: 100.5}, date.today())
    assert ev[0]["kind"] == "AGENT_FILL" and ag.summary()["open"][0]["state"] == "OPEN"


def test_low_rr_wait_and_user_decided_plans_are_not_candidates():
    assert ag.candidates([plan(label="WAIT", rr_top=1.2), {**plan(2), "my_decision": "SKIP"}, {**plan(3), "is_taken": True}]) == []


def test_bad_llm_answer_is_recorded_not_traded():
    run([plan()], lambda s, u: "I think maybe")
    assert ag.summary()["open"] == []
