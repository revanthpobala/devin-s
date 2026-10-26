import pytest

from src.tracking import notification_center as nc
from src.tracking.notifier_rules import closed_positions, position_events, setup_events, trust_event
from src.tracking.trust_report import build_trust_report, summarize, wilson


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "db_path", lambda: tmp_path / "n.db")
    return nc


def _pos(**kw):
    base = {"side": "LONG", "entry_price": 100.0, "stop": 95.0, "target": 110.0, "opened_at": "2026-10-13T10:00"}
    base.update(kw)
    return base


def _kinds(evs):
    return sorted(e["kind"] for e in evs)


def test_long_position_rules():
    assert _kinds(position_events("AAA", _pos(), 102.0)) == []
    assert _kinds(position_events("AAA", _pos(), 96.0)) == ["NEAR_STOP"]
    assert _kinds(position_events("AAA", _pos(), 94.9)) == ["STOP_BREACH"]
    assert _kinds(position_events("AAA", _pos(), 110.0)) == ["TARGET_HIT"]


def test_short_position_rules():
    s = _pos(side="SHORT", entry_price=100.0, stop=105.0, target=90.0)
    assert _kinds(position_events("AAA", s, 105.0)) == ["STOP_BREACH"]
    assert _kinds(position_events("AAA", s, 89.0)) == ["TARGET_HIT"]
    assert _kinds(position_events("AAA", s, 99.0)) == []


def test_missing_levels_and_price_are_reported_not_ignored():
    assert _kinds(position_events("AAA", _pos(stop=None, target=None), 100.0)) == ["NO_LEVELS"]
    assert _kinds(position_events("AAA", _pos(), None)) == []


def test_keys_are_stable_per_position():
    a = position_events("AAA", _pos(), 94.0)[0]["key"]
    assert a == position_events("AAA", _pos(), 90.0)[0]["key"]
    assert a != position_events("AAA", _pos(opened_at="2026-10-14T10:00"), 94.0)[0]["key"]


def test_closed_positions():
    assert closed_positions(["A", "B"], ["B", "C"]) == ["A"]


def test_only_take_now_notifies():
    opps = [{"ticker": "x", "opportunity_state": "ARMED"}, {"ticker": "y", "opportunity_state": "TAKE_NOW", "live_rr": 2.4}]
    ev = setup_events(opps, "2026-10-13")
    assert [e["ticker"] for e in ev] == ["Y"] and ev[0]["key"] == "TAKE_NOW:Y:2026-10-13"


def test_store_dedupes_and_marks_read(store):
    assert store.add("STOP_BREACH", "CRITICAL", "t", ticker="a", dedupe_key="k1")
    assert not store.add("STOP_BREACH", "CRITICAL", "t again", ticker="a", dedupe_key="k1")
    assert store.add("NEAR_STOP", "WARN", "w", dedupe_key="k2")
    assert store.unread_count()["total"] == 2 and store.unread_count()["CRITICAL"] == 1
    assert store.mark_read() == 2 and store.unread_count()["total"] == 0
    assert len(store.list_recent()) == 2


def test_heartbeat_age(store):
    assert store.heartbeat_age_s() is None
    store.heartbeat()
    assert store.heartbeat_age_s() < 5


def test_wilson_bounds():
    lo, hi = wilson(13, 35)
    assert 22 < lo < 37.1 < hi < 54


def test_few_trades_make_no_claim_and_top_winners_are_shown():
    r = [6.0, 3.0] + [-1.0] * 10
    s = summarize(r)
    assert "no claim" in s["verdict"] and s["mean_ex_top2"] == -1.0 and s["n"] == 12


def test_enough_trades_negative_interval():
    s = summarize([-1.0, -0.9, -1.1] * 12)
    assert "negative" in s["verdict"]


def test_trust_report_splits_groups_and_pending():
    row = lambda i, gate, r, taken=0, v="STALK": {"id": i, "ticker": f"T{i}", "verdict": v, "gate_status": gate, "taken": taken, "r_net": r}
    rep = build_trust_report([
        row(1, "PASS", 2.0), row(2, "PASS", -1.0, taken=1), row(3, "PASS", None), row(4, "REJECTED_BY_GATE", -1.0),
        row(5, "PASS", 1.0, v="WATCH"),
    ])
    assert rep["gated"]["n"] == 2 and rep["rejected"]["n"] == 1 and rep["taken"]["n"] == 1
    assert rep["pending_gated"] == 1 and rep["taken_total"] == 1
    assert trust_event(rep, "2026-10-13")["key"] == "TRUST:2026-10-13"


def test_notifications_api(store, monkeypatch):
    from fastapi.testclient import TestClient
    from src.ui.routes import notifications

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(notifications.router)
    c = TestClient(app)
    store.add("OPS", "CRITICAL", "UI is down", dedupe_key="x")
    d = c.get("/api/notifications").json()
    assert d["unread"]["CRITICAL"] == 1 and d["notifier"]["alive"] is False
    store.heartbeat()
    assert c.get("/api/notifications/unread").json()["notifier"]["alive"] is True
    assert c.post("/api/notifications/read", json={}).json()["unread"]["total"] == 0


def test_second_notifier_instance_cannot_start():
    import run_notifier

    first = run_notifier.acquire_single_instance()
    try:
        assert first is not None
        assert run_notifier.acquire_single_instance() is None
    finally:
        first.close()


def test_watch_alerts_keep_levels_drop_noise():
    from src.tracking.notifier_rules import watch_alert_events

    rows = [
        {"id": 1, "ticker": "AAA", "date": "2026-10-13", "trigger_type": "LEVEL_BREAKOUT", "message": "m", "spot_price": 50.0},
        {"id": 2, "ticker": "AAA", "date": "2026-10-13", "trigger_type": "THESIS_EXPIRED", "message": "m", "spot_price": 1},
        {"id": 3, "ticker": "AAA", "date": "2026-10-13", "trigger_type": "MOVING", "message": "m", "spot_price": 1},
    ]
    evs = watch_alert_events(rows)
    assert [e["kind"] for e in evs] == ["LEVEL_BREAKOUT"]
    assert evs[0]["title"] == "CHECK AAA: broke a level @ $50.00"


def test_portfolio_events_thresholds():
    from src.tracking.notifier_rules import portfolio_events

    pos = [
        {"symbol": "BIG", "asset_type": "EQUITY", "market_value": 30000, "day_pct": -10.2},
        {"symbol": "MID", "asset_type": "EQUITY", "market_value": 30000, "day_pct": -5.5},
        {"symbol": "DUST", "asset_type": "EQUITY", "market_value": 400, "day_pct": -20.0},
        {"symbol": "FINE", "asset_type": "EQUITY", "market_value": 30000, "day_pct": -1.4},
        {"symbol": "OPT", "underlying": "XYZ", "asset_type": "OPTION", "market_value": 5000, "day_pct": -40.0},
        {"symbol": "UNK", "asset_type": "EQUITY", "market_value": 30000, "day_pct": None},
    ]
    by = {e["ticker"]: e["severity"] for e in portfolio_events(pos, -3.4, "2026-10-13", 180000)}
    assert by == {None: "WARN", "BIG": "CRITICAL", "MID": "WARN", "XYZ": "WARN"}


def test_take_now_blocked_near_earnings_and_flagged_when_unknown():
    opps = [{"ticker": "AAA", "opportunity_state": "TAKE_NOW"}, {"ticker": "BBB", "opportunity_state": "TAKE_NOW"},
            {"ticker": "CCC", "opportunity_state": "TAKE_NOW"}, {"ticker": "DDD", "opportunity_state": "ARMED"}]
    evs = {e["ticker"]: e for e in setup_events(opps, "2026-10-13", {"AAA": 3, "BBB": 30, "CCC": None})}
    assert evs["AAA"]["kind"] == "EARNINGS_BLOCK" and evs["AAA"]["severity"] == "INFO"
    assert evs["BBB"]["kind"] == "TAKE_NOW" and "unknown" not in evs["BBB"]["body"]
    assert "Earnings date unknown" in evs["CCC"]["body"]
    assert "DDD" not in evs


def test_digest_waits_for_risk_and_reports_limits():
    from src.tracking.notifier_rules import digest_event

    opps = [{"opportunity_state": "TAKE_NOW"}, {"opportunity_state": "ARMED"}, {"opportunity_state": "ARMED"}]
    assert digest_event("2026-10-13", opps, {"loading": True}, True) is None
    risk = {"book": 1, "open_risk_pct": 2.0, "risk_cap_pct": 5.0, "tracked": 3, "position_cap": 10, "beta_weighted_pct": 80.0,
            "regime_dial": {"multiplier": 0.5, "why": "x"}, "flags": ["GOOGL is 36.0% of the book"]}
    ev = digest_event("2026-10-13", opps, risk, False)
    assert ev["severity"] == "WARN" and "1 take-now, 2 armed" in ev["title"]
    assert "NOT running" in ev["body"] and "GOOGL is 36.0%" in ev["body"]


def test_funnel_and_readiness_separate_fills_from_misses():
    from src.tracking.trust_report import delegation_readiness, pipeline_funnel
    live = [
        {"gate_status": "PASS", "scored_at": "x", "fill_price": 10, "r_net": 1.0, "exit_reason": "TARGET_1"},
        {"gate_status": "PASS", "scored_at": "x", "fill_price": 10, "r_net": -1.0, "exit_reason": "STOP_BREACHED"},
        {"gate_status": "PASS", "scored_at": "x", "fill_price": None, "r_net": None, "exit_reason": "NOT_FILLED"},
        {"gate_status": "REJECTED_BY_GATE", "scored_at": "x", "fill_price": 10, "r_net": 2.0, "exit_reason": "TARGET_1"},
    ]
    f = pipeline_funnel(live)
    assert f["fill_rate_pct"] == 66.7 and f["unfilled_by_reason"] == {"NOT_FILLED": 1}
    assert f["gate"] == {"PASS": 3, "REJECTED_BY_GATE": 1} and f["filled_by_exit"]["TARGET_1"]["n"] == 1
    assert delegation_readiness({"n": 3})["ready"] is False


def test_info_alerts_collapse_into_one_group_but_warns_stay_single():
    from src.tracking.notifier_rules import watch_alert_events
    rows = [{"id": i, "ticker": f"T{i}", "date": "2026-10-14", "trigger_type": "ZONE_APPROACH", "message": f"[T{i}] near", "spot_price": 10}
            for i in range(5)]
    rows.append({"id": 9, "ticker": "AMZN", "date": "2026-10-14", "trigger_type": "ZONE_TOUCH", "message": "[AMZN] touch, R:R at market 3.0", "spot_price": 247})
    ev = watch_alert_events(rows)
    assert len(ev) == 2
    grp = [e for e in ev if e["ticker"] is None][0]
    assert grp["title"].startswith("WAIT 5 names") and grp["body"].count("[T") == 5
    assert [e for e in ev if e["ticker"] == "AMZN"][0]["severity"] == "WARN"


def test_list_after_returns_only_newer(store):
    nc.add("K", "WARN", "a", "", "AAA", "k1")
    first = nc.max_id()
    nc.add("K", "WARN", "b", "", "BBB", "k2")
    assert [n["title"] for n in nc.list_after(first)] == ["b"]
    assert nc.list_after(nc.max_id()) == []


def test_watch_alerts_carry_a_verdict():
    from src.tracking.notifier_rules import watch_alert_events

    rows = [
        {"id": 1, "trigger_type": "ZONE_TOUCH", "ticker": "MMSI", "date": "d", "spot_price": 87.69,
         "message": "[MMSI] Touch #1. R:R at market 18.49 to T1 $111 (floor 2.0: meets)."},
        {"id": 2, "trigger_type": "ZONE_TOUCH_LOW_RR", "ticker": "LLY", "date": "d", "spot_price": 1204.0,
         "message": "[LLY] Touch #1. R:R at market 0.86 (floor 2.0: below)."},
        {"id": 3, "trigger_type": "LEVEL_HIGH_20D", "ticker": "PSX", "date": "d", "spot_price": 292.9, "message": "[PSX] high"},
    ]
    ev = {e["ticker"]: e for e in watch_alert_events(rows)}
    assert "LLY" not in ev
    assert ev["MMSI"]["title"].startswith("ACT MMSI") and ev["MMSI"]["body"].startswith("ACT:") and "R:R 18.49" in ev["MMSI"]["body"]
    assert "PSX" not in ev


def test_spread_alerts_on_net_loss_not_per_leg():
    from src.tracking.notifier_rules import portfolio_events

    rows = [{"symbol": "NDXP 2026-10-14 CALL spread", "underlying": "NDXP", "asset_type": "SPREAD", "market_value": 500.0, "day_pct": -60.0}]
    ev = portfolio_events(rows, None, "2026-10-14", 190000.0)
    assert ev and ev[0]["severity"] == "CRITICAL" and ev[0]["title"].startswith("EXIT? NDXP")
    rows[0]["day_pct"] = 2.0
    assert portfolio_events(rows, None, "2026-10-14", 190000.0) == []


def test_backend_stale_code_detection(tmp_path, monkeypatch):
    import time
    from src import config
    from src.tracking import backend_control as bc
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "a.py"
    f.write_text("x=1")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert bc.code_is_newer(time.time() - 10) is True
    assert bc.code_is_newer(time.time() + 10) is False
    assert bc.code_is_newer(None) is False


def test_stop_family_collapses_to_most_severe_per_ticker_day():
    from src.tracking.notifier_rules import watch_alert_events
    def row(i, t, kind, d="d1"):
        return {"id": i, "ticker": t, "date": d, "trigger_type": kind, "message": "m", "spot_price": 10.0}
    rows = [row(1, "PANW", "TESTING_SUPPORT"), row(2, "PANW", "LEVEL_STOP"), row(3, "PANW", "STOP_BREACHED"),
            row(4, "SCHW", "TESTING_SUPPORT"), row(5, "SCHW", "LEVEL_STOP"), row(6, "PANW", "TESTING_SUPPORT", "d2"),
            row(7, "PANW", "TAKE_NOW")]
    ev = {(e["ticker"], e["kind"]): e for e in watch_alert_events(rows)}
    assert set(ev) == {("PANW", "STOP_BREACHED"), ("SCHW", "LEVEL_STOP"), ("PANW", "TESTING_SUPPORT")}
    assert ev[("PANW", "STOP_BREACHED")]["group"] == "WA:STOP:PANW:d1"


def test_ranked_notification_upgrades_in_place_and_ignores_weaker(store):
    from run_notifier import _emit
    def ev(kind, rank):
        return {"kind": kind, "severity": "WARN", "ticker": "PANW", "key": f"WA:{kind}:PANW:d", "title": kind, "group": "WA:STOP:PANW:d", "rank": rank}
    _emit(ev("TESTING_SUPPORT", 1))
    _emit(ev("STOP_BREACHED", 3))
    _emit(ev("LEVEL_STOP", 2))
    _emit(ev("TESTING_SUPPORT", 1))
    assert [n["kind"] for n in nc.list_recent()] == ["STOP_BREACHED"]


def test_equity_day_pct_distrusts_a_live_quote_that_contradicts_the_broker():
    from run_notifier import _equity_day_pct

    assert _equity_day_pct(-8.2, -1.79) == -1.79
    assert _equity_day_pct(-1.8, -1.79) == -1.8
    assert _equity_day_pct(None, -1.79) == -1.79
    assert _equity_day_pct(-3.0, None) == -3.0


def test_notifications_list_paginates_and_caches(tmp_path, monkeypatch):
    from src.tracking import notification_center as nc
    from src.ui.routes import notifications as routes

    monkeypatch.setattr(nc, "db_path", lambda: tmp_path / "n.db")
    routes._LIST_CACHE.clear()
    for i in range(25):
        nc.add("K", "INFO", f"t{i}", dedupe_key=f"k{i}")
    p1 = routes.list_notifications(limit=10)
    assert len(p1["items"]) == 10 and p1["has_more"]
    p2 = routes.list_notifications(limit=10, before_id=p1["items"][-1]["id"])
    assert len(p2["items"]) == 10 and p2["items"][0]["id"] < p1["items"][-1]["id"]
    p3 = routes.list_notifications(limit=10, before_id=p2["items"][-1]["id"])
    assert len(p3["items"]) == 5 and not p3["has_more"]


def test_notification_delete(tmp_path, monkeypatch):
    from src.tracking import notification_center as nc

    monkeypatch.setattr(nc, "db_path", lambda: tmp_path / "d.db")
    nc.add("K", "INFO", "a", dedupe_key="a")
    nc.add("K", "INFO", "b", dedupe_key="b")
    first = nc.list_recent()[-1]["id"]
    assert nc.delete([first]) == 1 and len(nc.list_recent()) == 1


def test_zone_touch_acts_only_in_zone_at_green_rr():
    from src.tracking.notifier_rules import watch_alert_events

    def row(i, t, msg):
        return {"id": i, "trigger_type": "ZONE_TOUCH", "ticker": t, "date": "d", "spot_price": 10.0, "message": msg}
    rows = [row(1, "IN", "[IN] Touch #1 of tracked zone $9.50-$10.50 (named d) at $10.00. R:R at market 3.1 -> IN BUY 100 LIMIT 10.5"),
            row(2, "NEAR", "[NEAR] Touch #1 of tracked zone $10.50-$11.00 (named d) at $10.00. R:R at market 9.0"),
            row(3, "YEL", "[YEL] Touch #1 of tracked zone $9.50-$10.50 (named d) at $10.00. R:R at market 1.7")]
    ev = {e["ticker"]: e for e in watch_alert_events(rows)}
    assert ev["IN"]["title"].startswith("ACT IN") and ev["IN"]["push"] == "ENTRY" and "IN BUY 100 LIMIT 10.5" in ev["IN"]["body"]
    assert ev["NEAR"]["title"].startswith("CHECK NEAR") and ev["NEAR"]["severity"] == "INFO" and "push" not in ev["NEAR"]
    assert ev["YEL"]["title"].startswith("CHECK YEL")


def test_noise_is_silent_and_stops_on_unheld_names_become_cancels():
    from src.tracking.notifier_rules import watch_alert_events

    def row(i, t, kind):
        return {"id": i, "trigger_type": kind, "ticker": t, "date": "d", "spot_price": 10.0, "message": "m"}
    rows = [row(1, "A", "LEVEL_HIGH_20D"), row(2, "B", "TARGET_HIT_NO_FILL"), row(3, "C", "ZONE_REBOUND"),
            row(4, "D", "STOP_BREACHED"), row(5, "E", "STOP_BREACHED"), row(6, "F", "TARGET_1_REACHED"), row(7, "G", "TARGET_1_REACHED")]
    ev = {e["ticker"]: e for e in watch_alert_events(rows, held={"E", "G"})}
    assert set(ev) == {"D", "E", "G"}
    assert ev["D"]["title"].startswith("CANCEL D") and ev["D"]["severity"] == "INFO" and "push" not in ev["D"]
    assert ev["E"]["title"].startswith("EXIT E") and ev["E"]["push"] == "RISK"
    assert ev["G"]["push"] == "RISK"


def test_rsi2_order_lives_until_the_next_open():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.tracking.notifier_rules import rsi2_events

    et = ZoneInfo("America/New_York")
    rows = [{"ticker": "afrm", "date": "2026-10-23", "entry_high": 65.79, "stop": 62.5, "target_1": 77.0,
             "lane_prior_win": 64.0, "lane_prior_ev": 0.075}]
    ev = rsi2_events(rows, datetime(2026, 10, 24, 18, 0, tzinfo=et))
    assert len(ev) == 1 and ev[0]["kind"] == "ORDER" and ev[0]["push"] == "ENTRY"
    assert ev[0]["title"] == "BUY AFRM at the 2026-10-26 open, only if it opens at or below $65.79"
    assert "MARKET-ON-OPEN 2026-10-26" in ev[0]["body"] and "risk at the ceiling $329" in ev[0]["body"]
    assert rsi2_events(rows, datetime(2026, 10, 26, 9, 30, tzinfo=et)) == []


def test_rsi2_order_flags_earnings_inside_hold():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.tracking.notifier_rules import rsi2_events

    et = ZoneInfo("America/New_York")
    row = {"ticker": "AMZN", "date": "2026-10-23", "entry_high": 247.03, "stop": 234.67, "target_1": 274.65}
    ev = rsi2_events([{**row, "next_earnings": "2026-10-29"}], datetime(2026, 10, 24, 18, 0, tzinfo=et))[0]
    assert ev["title"].startswith("EARNINGS IN HOLD") and "ends 2026-11-06" in ev["body"]
    ev = rsi2_events([{**row, "next_earnings": "2026-11-09"}], datetime(2026, 10, 24, 18, 0, tzinfo=et))[0]
    assert ev["title"].startswith("BUY") and "Earnings" not in ev["body"]
