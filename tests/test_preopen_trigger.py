"""Pre-open re-check, plan voiding, traced news veto, gap-through-zone touch, research-time signal-bar stop gate."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src import config
from src.clients import news_client
from src.logic import level_validation, order_ticket as ot
from src.tracking import watch_manager, zone_ledger as zl

ET = ZoneInfo("America/New_York")
TODAY = "2026-11-03"
PLAN_DATE = "2026-11-02"


def _at(h, m):
    return datetime(2026, 11, 3, h, m, tzinfo=ET)


def _plan(stop=95.0, t1=115.0, lo=99.0, hi=101.0, entry_max=105.0, atr=0.0, **kw):
    wd = {"ticker": "TST", "date": PLAN_DATE, "side": "LONG", "verdict": "ARMED", "actionable": 1,
          "setup_lane": "LLM_DISCRETIONARY", "levels_source": "llm", "atr14": atr,
          "shares_plan": {"tactical_stop": stop, "target_1": t1, "entry_zone_low": lo, "entry_zone_high": hi,
                          "entry_max": entry_max}}
    wd.update(kw)
    return wd


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ot, "held_symbols", lambda: set())
    monkeypatch.setattr(news_client, "get_fresh_news", lambda *a, **k: "")
    monkeypatch.setattr(level_validation, "_compute_atr14_from_bars", lambda *a, **k: 0.0)
    notes = []
    import src.tracking.notify as nt
    monkeypatch.setattr(nt, "notify", lambda tier, title, body, dedupe_key=None, **k: notes.append((title, body)))
    tr = {"db": []}
    monkeypatch.setattr(watch_manager, "update_target_live_state", lambda t, px, st, **k: tr["db"].append((t, st)))
    tr["notes"] = notes
    return tmp_path, tr


def _write_plan(root, wd, d=PLAN_DATE):
    p = root / "reports" / d / "TST_watch_levels.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(wd), encoding="utf-8")
    return p


def _classify(last, low=0.0, qd=TODAY, atr=0.0, etf=False, now=None, cap=False, **plan):
    p = ot.plan_levels(_plan(**plan))
    return ot.classify_plan(p, last, low, qd, TODAY, atr, etf, now or _at(10, 0), cap_risk=cap)


def test_classify_stale_quote_never_voids():
    assert _classify(90.0, qd="2026-11-02")["cls"] == "STALE"


def test_classify_void_stop_last_and_regular_session_day_low_only():
    assert _classify(95.0)["cls"] == "VOID_STOP"
    assert _classify(99.5, low=94.0, now=_at(10, 0))["cls"] == "VOID_STOP"
    assert _classify(99.5, low=94.0, now=_at(8, 30))["cls"] != "VOID_STOP"


def test_classify_void_target_and_chase_cap():
    assert _classify(115.0)["cls"] == "VOID_TARGET"
    assert _classify(106.0)["cls"] == "STAND_ASIDE"


def test_classify_risk_cap_only_for_pine_plans():
    # risk 4.5 at fill 99.5: under the 5% stock cap (4.975), over the 3% ETF cap (2.985)
    assert _classify(99.5, stop=95.0, cap=True)["cls"] == "FILL"
    assert _classify(99.5, stop=95.0, cap=True, etf=True)["cls"] == "VOID_RISK"
    assert _classify(99.5, stop=95.0, cap=True, atr=2.0)["cls"] == "VOID_RISK"
    assert _classify(99.5, stop=95.0, cap=True, atr=3.0)["cls"] == "FILL"
    assert _classify(99.5, stop=95.0, etf=True, atr=2.0)["cls"] == "FILL"


def test_classify_resting_and_fill():
    assert _classify(101.5, stop=97.0)["cls"] == "RESTING"
    f = _classify(99.5)
    assert f["cls"] == "FILL" and f["fill"] == 99.5


def test_trading_days_skip_weekend_and_holiday():
    from datetime import date

    assert ot.is_trading_day(date(2026, 11, 3))
    assert not ot.is_trading_day(date(2026, 11, 26))
    assert not ot.is_trading_day(date(2026, 11, 7))


def test_preopen_runs_once_per_window_and_day(root, monkeypatch):
    calls = []
    monkeypatch.setattr(ot, "run_preopen", lambda now=None, **k: calls.append(now.strftime("%H:%M")))
    ot.maybe_run_preopen(_at(8, 30))
    ot.maybe_run_preopen(_at(8, 45))
    ot.maybe_run_preopen(_at(10, 0))
    ot.maybe_run_preopen(_at(9, 27))
    ot.maybe_run_preopen(_at(9, 31))
    assert calls == ["08:30", "09:27"]


def test_no_preopen_on_non_trading_day(root, monkeypatch):
    calls = []
    monkeypatch.setattr(ot, "run_preopen", lambda now=None, **k: calls.append(1))
    ot.maybe_run_preopen(datetime(2026, 11, 26, 9, 27, tzinfo=ET))
    ot.maybe_run_preopen(datetime(2026, 11, 7, 9, 27, tzinfo=ET))
    assert calls == []


def test_void_plan_blocks_touch_even_when_db_row_has_no_void(root, monkeypatch):
    base, tr = root
    _write_plan(base, _plan(atr=0.0))
    ot.void_plan("TST", TODAY, "VOID_STOP", "test", 94.0)
    assert (base / "reports" / PLAN_DATE / "TST_watch_levels.json").exists()
    assert json.loads((base / "reports" / PLAN_DATE / "TST_watch_levels.json").read_text())["void"].startswith("VOID_STOP")
    assert tr["db"] == [("TST", "STOP_BREACHED")]
    monkeypatch.setattr(ot, "_quotes", lambda s: {})
    db_row = {"ticker": "TST", "shares_plan": _plan()["shares_plan"], "date": PLAN_DATE, "actionable": 1}
    res = ot.act_on_touch("TST", 100.0, TODAY, "touch", wd=db_row, plan_date=PLAN_DATE)
    assert res["action"] == "NO TRADE" and "voided" in res["text"]


def test_act_on_touch_above_chase_cap_keeps_ticket_path(root, monkeypatch):
    base, _ = root
    _write_plan(base, _plan())
    monkeypatch.setattr(ot, "_quotes", lambda s: {"TST": {"last": 106.0, "low": 105.0, "date": TODAY}})
    import src.tracking.auto_triage_daemon as atd
    monkeypatch.setattr(atd, "needs_fresh_research", lambda *a, **k: (False, ""))
    monkeypatch.setattr(ot, "persist_ticket", lambda t: None)
    res = ot.act_on_touch("TST", 106.0, TODAY, "touch")
    assert res["action"] == "TICKET"


def test_armed_plan_voided_pre_open_and_no_ticket(root, monkeypatch):
    base, tr = root
    _write_plan(base, _plan())
    monkeypatch.setattr(ot, "open_tickets", lambda days=14: [])
    quotes = {"TST": {"last": 94.0, "low": 93.5, "date": TODAY}}
    out = ot.run_preopen(quote_fn=lambda syms: quotes, now=_at(9, 27))
    assert out == []
    assert json.loads((base / "reports" / PLAN_DATE / "TST_watch_levels.json").read_text())["void"].startswith("VOID_STOP")
    assert any("VOID_STOP" in t for t, _ in tr["notes"])


def test_armed_plan_fill_builds_one_ticket_then_idempotent(root, monkeypatch):
    base, tr = root
    _write_plan(base, _plan(atr=0.0))
    monkeypatch.setattr(ot, "open_tickets", lambda days=14: [])
    monkeypatch.setattr(ot, "_quotes", lambda s: {})
    quotes = {"TST": {"last": 99.5, "low": 99.0, "date": TODAY}}
    out = ot.run_preopen(quote_fn=lambda syms: quotes, now=_at(9, 27))
    assert [o["verdict"] for o in out] == ["TICKET"]
    assert (base / "reports" / TODAY / "TST_ticket.json").exists()
    assert ot._ticket_today("TST", TODAY)
    assert ot.run_preopen(quote_fn=lambda syms: quotes, now=_at(9, 28)) == []


def test_raw_only_stalk_plan_voided_pre_open_never_ticketed(root, monkeypatch):
    base, tr = root
    p = base / "data" / "raw" / PLAN_DATE / "TST" / "TST_watch_levels.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(_plan(verdict="STALK", actionable=0)), encoding="utf-8")
    monkeypatch.setattr(ot, "open_tickets", lambda days=14: [])
    assert ot.run_preopen(quote_fn=lambda syms: {"TST": {"last": 99.5, "low": 99.0, "date": TODAY}}, now=_at(9, 27)) == []
    assert not (base / "reports" / TODAY / "TST_ticket.json").exists()
    ot.run_preopen(quote_fn=lambda syms: {"TST": {"last": 94.0, "low": 93.5, "date": TODAY}}, now=_at(9, 28))
    assert json.loads(p.read_text())["void"].startswith("VOID_STOP") and tr["db"] == [("TST", "STOP_BREACHED")]


def test_void_of_older_zone_plan_leaves_newest_plan_and_db(root):
    base, tr = root
    old = _write_plan(base, _plan(), d="2026-10-28")
    new = _write_plan(base, _plan(stop=90.0))
    ot.void_plan("TST", TODAY, "VOID_STOP", "old stop", 94.0, plan_date="2026-10-28")
    assert json.loads(old.read_text())["void"] and not json.loads(new.read_text()).get("void") and tr["db"] == []
    ot.void_plan("TST", TODAY, "VOID_TARGET", "target", 116.0)
    assert json.loads(new.read_text())["void"].startswith("VOID_TARGET") and tr["db"] == [("TST", "INVALIDATED")]


def test_traced_veto_voids_and_untraced_veto_blocks(root, monkeypatch):
    base, tr = root
    news = "Acme cuts full-year guidance sharply after weak quarter."
    monkeypatch.setattr(news_client, "get_fresh_news", lambda *a, **k: news)
    import src.clients.llm_client as lc
    reply = {"v": f'VETO: guidance cut "{news}"'}
    monkeypatch.setattr(lc, "query_local_llm", lambda **k: reply["v"])
    assert ot._veto_verdict("TST", PLAN_DATE, TODAY)[0] == "VETO"
    reply["v"] = 'VETO: invented "Acme will be sued by every investor tomorrow"'
    monkeypatch.setattr(ot, "_load_json", lambda fp: {})
    assert ot._veto_verdict("TST", PLAN_DATE, TODAY)[0] == "UNTRACED"
    reply["v"] = "OK"
    assert ot._veto_verdict("TST", PLAN_DATE, TODAY) is None


def test_judge_failure_fails_closed(root, monkeypatch):
    news = "Some headline about the company today."
    monkeypatch.setattr(news_client, "get_fresh_news", lambda *a, **k: news)
    import src.clients.llm_client as lc

    def boom(**k):
        raise RuntimeError("openrouter down")

    monkeypatch.setattr(lc, "query_local_llm", boom)
    assert ot._veto_verdict("TST", PLAN_DATE, TODAY)[0] == "UNTRACED"


def test_gap_through_zone_emits_once_per_day():
    z = {"low": 100.0, "high": 101.0, "names": [PLAN_DATE], "newest": PLAN_DATE, "stop": 97.0, "t1": 115.0}
    st = {"prev_px": 102.0}
    ev = zl.evaluate("TST", 98.0, TODAY, [z], st)
    assert [e["type"] for e in ev] == ["ZONE_TOUCH"] and ev[0]["plan_date"] == PLAN_DATE
    st["prev_px"] = 98.0
    assert zl.evaluate("TST", 98.0, TODAY, [z], st) == []
    assert zl.evaluate("TST", 98.0, TODAY, [z], {"prev_px": 98.5}) == []


def test_research_gate_rejects_signal_bar_through_stop(monkeypatch):
    monkeypatch.setattr(level_validation, "_signal_bar_low", lambda t, d: 94.0)
    ok, reasons = level_validation.validate_levels(
        {"entry_low": 99.0, "entry_high": 101.0, "stop": 95.0, "target_1": 115.0, "target_2": 120.0},
        {"close": 100.0}, "LONG", ticker="TST", date_str=TODAY)
    assert not ok and any("signal bar low 94.00 at or through stop 95.00" in r for r in reasons)


def _b(lo, hi, close, vol=1000):
    return {"low": lo, "high": hi, "close": close, "volume": vol}


def test_confirm_wait_until_two_closed_bars_off_the_low_and_above_its_high():
    zone = (99.0, 101.0)
    assert ot.confirm_from_bars([_b(105, 106, 105.5)], *zone)[0] == "WAIT"
    assert ot.confirm_from_bars([_b(100, 101, 100.5)], *zone)[0] == "WAIT"
    assert ot.confirm_from_bars([_b(99.5, 100.5, 100), _b(99.8, 100.4, 100.2), _b(100.0, 100.6, 100.5)], *zone)[0] == "WAIT"
    state, why = ot.confirm_from_bars([_b(99.5, 100.5, 100), _b(99.8, 101.0, 100.9), _b(100.0, 101.2, 101.1)], *zone)
    assert state == "CONFIRMED" and "99.50" in why


def test_confirm_failed_on_close_below_zone_low_after_touch():
    bars = [_b(99.5, 100.5, 100), _b(98.0, 100.0, 98.4), _b(98.2, 101.0, 100.9), _b(98.5, 101.5, 101.4)]
    assert ot.confirm_from_bars(bars, 99.0, 101.0)[0] == "FAILED"


def test_touch_waits_then_tickets_with_notification_when_confirmed(root, monkeypatch):
    base, tr = root
    _write_plan(base, _plan())
    monkeypatch.setattr(ot, "_quotes", lambda s: {"TST": {"last": 100.0, "low": 99.5, "date": TODAY}})
    import src.tracking.auto_triage_daemon as atd
    monkeypatch.setattr(atd, "needs_fresh_research", lambda *a, **k: (False, ""))
    monkeypatch.setattr(ot, "confirm_touch", lambda *a, **k: ("WAIT", "last 5m close not above the low bar's high"))
    res = ot.act_on_touch("TST", 100.0, TODAY, "touch")
    assert res["action"] == "NO TRADE" and "waiting for confirmation" in res["text"] and not ot._ticket_today("TST", TODAY)
    monkeypatch.setattr(ot, "confirm_touch", lambda *a, **k: ("CONFIRMED", "held 99.50"))
    res = ot.act_on_touch("TST", 100.0, TODAY, "touch")
    assert res["action"] == "TICKET" and res["text"].startswith("confirmed") and ot._ticket_today("TST", TODAY)
    assert any("ticket" in t for t, _ in tr["notes"])


def test_touch_failed_zone_and_above_zone_skip_confirmation(root, monkeypatch):
    base, _ = root
    _write_plan(base, _plan())
    monkeypatch.setattr(ot, "_quotes", lambda s: {"TST": {"last": 100.0, "low": 99.5, "date": TODAY}})
    import src.tracking.auto_triage_daemon as atd
    monkeypatch.setattr(atd, "needs_fresh_research", lambda *a, **k: (False, ""))
    monkeypatch.setattr(ot, "confirm_touch", lambda *a, **k: ("FAILED", "5m close below zone low"))
    assert "zone failed today" in ot.act_on_touch("TST", 100.0, TODAY, "touch")["text"]
    called = []
    monkeypatch.setattr(ot, "confirm_touch", lambda *a, **k: called.append(1) or ("WAIT", "x"))
    monkeypatch.setattr(ot, "_quotes", lambda s: {"TST": {"last": 102.0, "low": 101.5, "date": TODAY}})
    monkeypatch.setattr(ot, "persist_ticket", lambda t: None)
    assert ot.act_on_touch("TST", 102.0, TODAY, "touch")["action"] == "TICKET" and called == []


def test_confirm_touch_uses_completed_rth_bars_only(monkeypatch):
    import sys
    import types
    t0 = int(datetime(2026, 11, 3, 10, 0, tzinfo=ET).timestamp() * 1000)
    raw = [{"datetime": t0 + i * 300000, "open": 100, "high": h, "low": lo, "close": c, "volume": 1000}
           for i, (lo, h, c) in enumerate([(99.5, 100.5, 100.0), (99.8, 101.0, 100.9), (100.0, 101.2, 101.1), (100.5, 101.5, 101.4)])]
    monkeypatch.setitem(sys.modules, "src.clients.schwab_client", types.SimpleNamespace(get_intraday_candles=lambda *a, **k: raw))
    monkeypatch.setenv("TOUCH_CONFIRM", "1")
    now_ts = (t0 + 3 * 300000 + 60000) / 1000  # bar 3 is still forming
    assert ot.confirm_touch("TST", TODAY, 99.0, 101.0, now_ts)[0] == "CONFIRMED"
    assert ot.confirm_touch("TST", TODAY, 99.0, 101.0, (t0 + 100000) / 1000)[0] == "NO_DATA"
    monkeypatch.setenv("TOUCH_CONFIRM", "0")
    assert ot.confirm_touch("TST", TODAY, 99.0, 101.0, now_ts)[0] == "NO_DATA"

