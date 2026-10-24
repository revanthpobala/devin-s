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


def test_ticket_fields_and_sizing(monkeypatch):
    monkeypatch.setattr(order_ticket, "price_option", lambda *a: ({"cost_usd": 420.0}, None))
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


def test_todays_dossier_is_extended_not_redone(monkeypatch, tmp_path):
    import json as _j
    from src import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    d = tmp_path / "data" / "raw" / "2026-10-16" / "TST"
    d.mkdir(parents=True)
    (d / "TST_watch_levels.json").write_text(_j.dumps({"shares_plan": {"tactical_stop": 95.0, "target_1": 110.0}}))
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 100.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is False
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 94.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is True
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 111.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is True


def test_short_dossier_breaks_upward(monkeypatch, tmp_path):
    import json as _j
    from src import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    d = tmp_path / "data" / "raw" / "2026-10-16" / "TST"
    d.mkdir(parents=True)
    (d / "TST_watch_levels.json").write_text(_j.dumps({"side": "SHORT", "shares_plan": {"tactical_stop": 105.0, "target_1": 90.0}}))
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 100.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is False
    monkeypatch.setattr(research_freshness, "_live_price", lambda t: 106.0)
    assert research_freshness.needs_fresh_research("TST", "2026-10-16")[0] is True


def test_researched_today_without_levels_is_fresh(monkeypatch, tmp_path):
    from src import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "reports" / "2026-10-16").mkdir(parents=True)
    (tmp_path / "reports" / "2026-10-16" / "TST_arbitration.md").write_text("x")
    assert research_freshness.needs_fresh_research("TST", "2026-10-16") == (False, "researched today")


def test_scorecard_rules_fire_on_veto_loss_and_weak_discretion(monkeypatch):
    from src.tracking import book_scorecard as bs

    st = lambda n, m: {"n": n, "win_pct": 40.0, "mean_r": m, "median_r": m}
    monkeypatch.setattr(bs, "book_stats", lambda: {"USER": {"n": 0}, "PINE_LANE": st(40, 0.2), "LLM_DISCRETIONARY": st(35, -0.1), "LLM_VETOED": st(32, 0.3)})
    txt = bs.scorecard_text()
    assert "vetoes lost money" in txt and "trail the Pine lanes" in txt
    monkeypatch.setattr(bs, "book_stats", lambda: {"USER": {"n": 0}, "PINE_LANE": st(10, 0.2), "LLM_DISCRETIONARY": st(10, -0.1), "LLM_VETOED": st(10, 0.3)})
    assert "RULE" not in bs.scorecard_text()


def test_local_slot_cap_uses_idle_deep_slots(monkeypatch):
    from src.ui.services import research_queue as rq
    monkeypatch.setattr("src.data.tv_scraper.signed_in_profiles", lambda names=None: ["a", "b"])
    monkeypatch.setattr(rq, "get_active_deep_research_count", lambda: 0)
    assert rq.local_slot_cap() == 2
    monkeypatch.setattr(rq, "get_active_deep_research_count", lambda: 1)
    assert rq.local_slot_cap() == 1
    monkeypatch.setattr(rq, "get_active_deep_research_count", lambda: 2)
    assert rq.local_slot_cap() == 0


def test_spread_math_debit_credit_and_rules():
    sm = order_ticket.spread_math
    n, _ = sm("BULL_CALL_SPREAD", 310, 330, 20.0, 11.71, 335, 290)
    assert (n["mid_debit"], n["max_loss"], n["max_profit"], n["breakeven"], n["r_at_t1"]) == (8.29, 829.0, 1171.0, 318.29, 1.41)
    assert sm("BULL_CALL_SPREAD", 310, 330, 20.0, 11.71, 325, 290)[0] is None
    n, _ = sm("BULL_PUT_SPREAD", 730, 750, 10.0, 19.4, 800, 760)
    assert sm("BULL_PUT_SPREAD", 730, 750, 10.0, 19.4, 800, 740)[0] is None
    n, _ = sm("BULL_PUT_SPREAD", 730, 750, 10.0, 19.4, 800, 760)
    assert (n["mid_credit"], n["max_loss"], n["max_profit"], n["breakeven"]) == (9.4, 1060.0, 940.0, 740.6)
    assert sm("BULL_PUT_SPREAD", 730, 750, 17.0, 19.0, 800, 760)[0] is None


def test_watch_gate_uses_zone_rr_not_buy_score():
    from src.logic.process_survivor import _deep_research_gate

    base = {"triage": "WATCH", "chosen_side": "long", "conviction": 95, "pursue": True, "regime": 0, "flags": [],
            "long_plan": {"zone": [99.0, 100.0], "stop": 95.0, "target": 112.0}}
    assert _deep_research_gate(dict(base), "PASS")[1] is True
    low = dict(base, long_plan={"zone": [99.0, 100.0], "stop": 95.0, "target": 106.0})
    assert _deep_research_gate(low, "PASS")[1] is False
    short = dict(base, chosen_side="short", short_plan=base["long_plan"])
    assert _deep_research_gate(short, "PASS")[1] is False


def _calls_df(rows):
    import pandas as pd

    return pd.DataFrame(rows, columns=["strike", "bid", "ask"])


def test_bounce_play_spread_call_and_shares(monkeypatch):
    monkeypatch.setattr(order_ticket, "_expiries", lambda t: ["2026-10-23", "2026-11-06", "2026-11-20"])
    # entry 100, atr 4 -> bounce target 104; exit_by 2026-10-30 -> first expiry on or after 11-02 is 11-06
    monkeypatch.setattr(order_ticket, "_calls", lambda t, e: _calls_df([(95, 7.4, 7.6), (100, 4.4, 4.6), (104, 2.4, 2.6), (110, 1.0, 1.2)]))
    b = order_ticket.bounce_play("TST", 100.0, 95.0, 115.0, 4.0, "2026-10-16")
    assert b["bounce_target"] == 104.0 and b["exit_by"] == "2026-10-30"
    assert b["play"] == "SHARES+SPREAD" and b["option"]["expiration"] == "2026-11-06"
    assert (b["option"]["long_strike"], b["option"]["short_strike"], b["option"]["mid_debit"]) == (100.0, 104.0, 2.0)
    assert b["option"]["r_at_t1"] == 1.0
    wide = _calls_df([(100, 1.0, 9.0), (104, 2.4, 2.6)])
    monkeypatch.setattr(order_ticket, "_calls", lambda t, e: wide)
    assert order_ticket.bounce_play("TST", 100.0, 95.0, 115.0, 4.0, "2026-10-16")["play"] == "SHARES"


def test_bounce_play_earnings_blocks_spanning_expiry(monkeypatch):
    monkeypatch.setattr(order_ticket, "_expiries", lambda t: ["2026-11-06"])
    monkeypatch.setattr(order_ticket, "_calls", lambda t, e: _calls_df([(100, 4.4, 4.6), (104, 2.4, 2.6)]))
    b = order_ticket.bounce_play("TST", 100.0, 95.0, 115.0, 4.0, "2026-10-16", stance="exit before", report_date="2026-10-28")
    assert b["play"] == "SHARES" and "spans earnings" in b["option_note"]


def _wd():
    return _watch(atr14=4.0, next_earnings_date=None)


def test_touch_fresh_dossier_gives_ticket(monkeypatch):
    monkeypatch.setattr("src.tracking.auto_triage_daemon.needs_fresh_research", lambda *a, **k: (False, ""))
    monkeypatch.setattr(order_ticket, "persist_ticket", lambda t: None)
    monkeypatch.setattr(order_ticket, "_expiries", lambda t: ["2026-11-06"])
    monkeypatch.setattr(order_ticket, "_calls", lambda t, e: _calls_df([(100, 4.4, 4.6), (104, 2.4, 2.6)]))
    r = order_ticket.act_on_touch("TST", 100.5, "2026-10-16", wd=_wd())
    assert r["action"] == "TICKET" and "SHARES+SPREAD" in r["text"] and "by 2026-10-30" in r["text"]


def test_touch_stale_queues_one_mid_and_approach_stays_quiet(monkeypatch):
    calls = []
    monkeypatch.setattr("src.tracking.auto_triage_daemon.needs_fresh_research", lambda *a, **k: (True, "plan 6 sessions old"))
    monkeypatch.setattr("src.tracking.auto_triage_daemon.enqueue_touch_mid", lambda t, d: calls.append(t) or True)
    r = order_ticket.act_on_touch("TST", 100.5, "2026-10-16", wd=_wd())
    assert r["action"] == "RESEARCH" and calls == ["TST"]
    assert order_ticket.act_on_touch("TST", 101.5, "2026-10-16", mode="approach", wd=_wd())["action"] == "NO TRADE" and calls == ["TST"]


def test_evaluator_never_says_stalk():
    from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival

    t = {"ticker": "AAPL", "status": "STALKING", "side": "LONG", "entry_zone_low": 99.0, "entry_zone_high": 100.0,
         "invalidation_price": 95.0, "tactical_stop": 95.0, "target_1": 112.0, "target_2": 120.0}
    for spot in (97.0, 101.0, 150.0):
        r = evaluate_target_on_zone_arrival("AAPL", spot, dict(t))
        assert "STALK" not in (r["verdict"] + r["verdict_label"] + r["playbook"]).upper()


def test_chain_prefers_schwab_over_alpaca(monkeypatch):
    from datetime import date, timedelta
    from types import SimpleNamespace
    from src.clients import options_client as oc
    exp = (date.today() + timedelta(days=20)).isoformat()
    leg = lambda b, a, d: [{"bid": b, "ask": a, "delta": d, "totalVolume": 10, "openInterest": 50}]
    payload = {"underlyingPrice": 100.0, "callExpDateMap": {f"{exp}:20": {"100.0": leg(3.0, 3.2, 0.5)}},
               "putExpDateMap": {f"{exp}:20": {"100.0": leg(2.8, 3.0, -0.5)}}}
    client = SimpleNamespace(Options=SimpleNamespace(ContractType=SimpleNamespace(ALL="ALL")),
                             get_option_chain=lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))
    monkeypatch.setattr("src.clients.schwab_client.get_schwab_client", lambda force_new=False: client)
    monkeypatch.setattr(oc, "_fetch_alpaca_chain", lambda *a, **k: (_ for _ in ()).throw(AssertionError("alpaca used")))
    out = oc.fetch_targeted_chain("TST", {"direction": "BOTH", "strike_low": 80, "strike_high": 120, "min_dte": 14, "max_dte": 60})
    assert "3.0/3.2" in out and "2.8/3.0" in out and exp in out


def test_chain_falls_back_to_tastytrade(monkeypatch):
    from types import SimpleNamespace
    from src.clients import options_client as oc
    monkeypatch.setattr(oc, "_fetch_schwab_chain", lambda *a, **k: None)
    monkeypatch.setattr(oc, "_fetch_alpaca_chain", lambda *a, **k: (_ for _ in ()).throw(AssertionError("alpaca used")))
    rows = [{"exp": "2026-11-20", "dte": 35, "strike": 100.0, "calls": {"bid": 3.0, "ask": 3.2, "mid": 3.1, "vol": 7}, "puts": {"bid": 2.8, "ask": 3.0, "mid": 2.9, "vol": 4}}]
    fake = SimpleNamespace(get_option_chain_quotes=lambda *a, **k: {"spot": 100.0, "rows": rows})
    monkeypatch.setattr("src.clients.tastytrade_client.get_tastytrade_client", lambda: fake)
    out = oc.fetch_targeted_chain("TST", {"direction": "BOTH", "strike_low": 80, "strike_high": 120, "min_dte": 14, "max_dte": 60})
    assert "3.0/3.2" in out and "2.8/3.0" in out and "2026-11-20" in out
