"""trade_rules, order_ticket (repair, ticket, pre-open), freshness gate."""

from datetime import date

import pytest

from src.logic import order_ticket
from src.tracking import auto_triage_daemon as research_freshness
from src.tracking import trade_rules


def test_entry_max_for_rr_hits_floor():
    em = trade_rules.entry_max_for_rr(115.0, 95.0)
    assert em == pytest.approx(101.6667, abs=1e-3)
    assert (115 - em) / (em - 95) == pytest.approx(trade_rules.GREEN_RR, abs=1e-6)


def test_earnings_gate_never_blocks_but_window_flags():
    assert trade_rules.earnings_gate(1) == "PASS" and trade_rules.earnings_gate(None) == "UNKNOWN"
    assert trade_rules.earnings_in_window(10) and not trade_rules.earnings_in_window(40)


def test_repair_converts_chase_to_resting_limit():
    sp = {"tactical_stop": 95.0, "target_1": 115.0, "entry_zone_low": 99.0, "entry_zone_high": 103.0}
    notes = order_ticket.repair_long_shares_plan(sp, close=103.0, atr=2.0, pine_stop=True)
    assert sp["entry_zone_high"] == pytest.approx(101.67, abs=0.01)
    assert sp["entry_type"] == "LIMIT" and "repair_rejected" not in sp
    assert any("resting limit" in n for n in notes)


def test_repair_rejects_limit_too_far_below():
    sp = {"tactical_stop": 95.0, "target_1": 115.0}
    order_ticket.repair_long_shares_plan(sp, close=110.0, atr=2.0, pine_stop=True)
    assert "repair_rejected" in sp


def test_repair_widens_judge_stop_to_noise_floor():
    sp = {"tactical_stop": 99.5, "target_1": 112.0, "entry_zone_low": 99.0, "entry_zone_high": 100.0}
    order_ticket.repair_long_shares_plan(sp, close=100.0, atr=2.0, pine_stop=False)
    assert sp["tactical_stop"] <= 100.0 - trade_rules.STOP_ATR_MIN * 2.0 + 1e-6


def test_option_spanning_earnings_needs_stance():
    opt = {"actionable": True, "expiration": "2026-11-20"}
    assert order_ticket.options_span_earnings_ok(opt, "2026-11-05", None)
    assert order_ticket.options_span_earnings_ok(opt, "2026-11-05", "EXIT_BEFORE")
    assert order_ticket.options_span_earnings_ok(opt, "2026-11-05", "HOLD_THROUGH") is None
    assert order_ticket.options_span_earnings_ok(opt, "2026-12-05", None) is None


def _watch(**kw):
    w = {
        "ticker": "TST", "side": "LONG", "verdict": "ARMED", "actionable": 1, "setup_lane": "LLM_DISCRETIONARY",
        "levels_source": "llm", "earnings_stance": "hold through",
        "shares_plan": {"tactical_stop": 95.0, "target_1": 115.0, "target_2": 120.0, "entry_zone_low": 99.0, "entry_zone_high": 101.0},
        "options_plan": {"actionable": True, "structure": "BULL_CALL_SPREAD", "expiration": "2026-12-18", "long_strike": 100, "short_strike": 115, "target_debit": 4.2},
        "invalidation": {"price_level": 95.0},
    }
    w.update(kw)
    return w


def test_ticket_fields_and_sizing():
    t = order_ticket.build_ticket(_watch(), spot=100.5, date_str="2026-10-16")
    assert t["qty"] == 100 and t["limit_price"] == 101.0
    assert t["max_chase_price"] == pytest.approx(101.67, abs=0.01)
    assert t["risk_usd"] == 600.0
    assert t["earnings_stance"] == "HOLD_THROUGH"
    assert t["option_alternative"]["cost_usd"] == 420.0
    assert t["good_til"] == "2026-10-30"


def test_no_ticket_for_skip_or_non_actionable():
    assert order_ticket.build_ticket(_watch(verdict="CASH_SKIP"), 100.0, "2026-10-16") is None
    assert order_ticket.build_ticket(_watch(actionable=0), 100.0, "2026-10-16") is None


def test_preopen_verdicts():
    t = {"stop": 95.0, "limit_price": 101.0, "max_chase_price": 101.67, "target_1": 115.0}
    assert order_ticket.evaluate_ticket(t, 94.0)["verdict"] == "STAND DOWN"
    assert order_ticket.evaluate_ticket(t, 104.0)["verdict"] == "STAND DOWN"
    assert order_ticket.evaluate_ticket(t, 100.0)["verdict"] == "GO"
    assert order_ticket.evaluate_ticket(t, 101.3)["verdict"] == "RESTING"


def test_freshness_gate(monkeypatch):
    monkeypatch.setattr(research_freshness, "last_report_date", lambda t, b: "2026-10-14")
    monkeypatch.setattr(research_freshness, "_prior_close_and_daily_atr", lambda t, d: (100.0, 2.0))
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 100.5)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is False
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 103.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is True
    assert research_freshness.needs_fresh_research("TST", "2026-10-16", held=True)[0] is True
    monkeypatch.setattr(research_freshness, "last_report_date", lambda t, b: "2026-10-01")
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 100.1)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is True


def test_scorecard_rules_fire_on_veto_loss_and_weak_discretion(monkeypatch):
    from src.tracking import book_scorecard as bs

    st = lambda n, m: {"n": n, "win_pct": 40.0, "mean_r": m, "median_r": m}
    monkeypatch.setattr(bs, "book_stats", lambda: {"USER": {"n": 0}, "PINE_LANE": st(40, 0.2), "LLM_DISCRETIONARY": st(35, -0.1), "LLM_VETOED": st(32, 0.3)})
    txt = bs.scorecard_text()
    assert "vetoes lost money" in txt and "trail the Pine lanes" in txt
    monkeypatch.setattr(bs, "book_stats", lambda: {"USER": {"n": 0}, "PINE_LANE": st(10, 0.2), "LLM_DISCRETIONARY": st(10, -0.1), "LLM_VETOED": st(10, 0.3)})
    assert "RULE" not in bs.scorecard_text()
