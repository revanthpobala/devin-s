from datetime import date

from src.logic import scenario_plans as sp
from src.logic.options_menu import shifted_chain
from src.tracking.ticket_paper import fill_ticket
from tests.test_options_menu import _df

TODAY = date(2026, 10, 25)


def _chain(spot):
    strikes = [spot + d for d in range(-40, 45, 5)]
    return ["2026-11-27", "2026-12-18", "2027-06-17"], (lambda e: (_df(strikes, spot), _df(strikes, spot, put=True)))


def test_touch_odds_closer_trigger_is_likelier():
    near, _ = sp.touch_odds(100.0, 97.0, 0.02, 21)
    far, med_far = sp.touch_odds(100.0, 85.0, 0.02, 21)
    assert 0 < far < near < 100
    assert med_far > sp.touch_odds(100.0, 97.0, 0.02, 21)[1]
    assert sp.touch_odds(100.0, 97.0, 0.0, 21) == (None, None)


def test_normalize_widens_tight_stop_and_keeps_rr_floor():
    sc, why = sp.normalize({"kind": "pullback", "trigger": 95, "stop": 94.5, "target": 110}, 100, 2.0, 8.0, TODAY, None)
    assert why is None and sc["zone_low"] == 94.5 and sc["stop"] == 93.1 and sc["entry"] == "LIMIT" and sc["notes"]
    sc, _ = sp.normalize({"kind": "PULLBACK", "trigger": 95, "stop": 93, "target": 110}, 100, 2.0, 8.0, TODAY, None, pine_stop=90)
    assert sc["stop"] == 90
    sc, why = sp.normalize({"kind": "PULLBACK", "trigger": 95, "stop": 90, "target": 130}, 100, 2.0, 8.0, TODAY, None)
    assert why is None and sc["target"] == 112 and any("expected move" in n for n in sc["notes"])
    sc, why = sp.normalize({"kind": "PULLBACK", "trigger": 97, "stop": 92, "target": 105}, 100, 2.0, 8.0, TODAY, None)
    assert why is None and sc["trigger"] == 96.31 and sc["rr_at_trigger"] >= 2 and any("lowered" in n for n in sc["notes"])
    sc, why = sp.normalize({"kind": "PULLBACK", "trigger": 96.5, "stop": 95.5, "target": 100.2}, 100, 2.0, 8.0, TODAY, None)
    assert why is None and sc["rr_at_trigger"] >= 2 and sc["stop"] <= round(sc["zone_low"] - 1.4, 2)
    _, why = sp.normalize({"kind": "BREAKOUT", "trigger": 104, "stop": 100, "target": 110}, 100, 2.0, 8.0, TODAY, None)
    assert "R:R" in why
    assert sc["rr_at_trigger"] >= 2 and sc["p_touch_pct"] is not None and sc["window_end"] > str(TODAY)
    _, why = sp.normalize({"kind": "PULLBACK", "trigger": 95, "stop": 90, "target": 100}, 100, 2.0, 8.0, TODAY, None)
    assert "R:R" in why
    _, why = sp.normalize({"kind": "PULLBACK", "trigger": 105, "stop": 100, "target": 130}, 100, 2.0, 8.0, TODAY, None)
    assert "not below spot" in why


def test_post_earnings_waits_for_the_print():
    sc, _ = sp.normalize({"kind": "POST_EARNINGS", "trigger": 96, "stop": 92, "target": 110}, 100, 2.0, 8.0, TODAY, "2026-11-12")
    assert sc["not_before"] == "2026-11-13" and sc["window_end"] > "2026-12-01"
    plan = {}
    assert sp._arm(plan, [sc]) is None and not plan


def test_cash_skip_without_hard_veto_becomes_armed_with_priced_scenarios():
    wd = {"verdict": "CASH_SKIP", "veto": "NONE", "side": "LONG",
          "shares_plan": {"entry_type": "NO_ENTRY", "entry_zone_low": 0, "entry_zone_high": 0},
          "scenarios": [{"kind": "PULLBACK", "trigger": 96, "stop": 92, "target": 112, "confirm": ["holds MA50"]},
                        {"kind": "BREAKOUT", "trigger": 104, "stop": 100.5, "target": 111}]}
    kept = sp.attach_scenarios(wd, "X", 100.0, 2.0, 8.0, None, today=TODAY, chain=_chain(100.0), persist=False)
    assert len(kept) == 2 and wd["verdict"] == "ARMED"
    assert wd["shares_plan"]["entry_zone_high"] == 96 and wd["shares_plan"]["breakout_level"] == 104
    pb = kept[0]
    assert pb["options"]["priced_at"]["spot"] == 96
    assert pb["options"]["income_or_csp"]["structure"] == "CASH_SECURED_PUT"
    assert pb["options"]["income_or_csp"]["short_strike"] <= 92
    assert "CONDITIONAL SCENARIOS" in sp.scenario_markdown(kept, 100.0)


def test_hard_veto_and_rsi2_veto_stay_skipped():
    wd = {"verdict": "CASH_SKIP", "veto": "HARD_FUNDAMENTAL", "side": "LONG", "shares_plan": {},
          "scenarios": [{"kind": "PULLBACK", "trigger": 96, "stop": 92, "target": 112}]}
    assert sp.attach_scenarios(wd, "X", 100.0, 2.0, 8.0, today=TODAY, chain=_chain(100.0), persist=False) == []
    assert wd["verdict"] == "CASH_SKIP" and wd["scenarios"] == []
    wd = {"verdict": "CASH_SKIP", "side": "LONG", "shares_plan": {},
          "scenarios": [{"kind": "PULLBACK", "trigger": 96, "stop": 92, "target": 112}]}
    sp.attach_scenarios(wd, "X", 100.0, 2.0, 8.0, today=TODAY, chain=_chain(100.0), persist=False, allow_unskip=False)
    assert wd["verdict"] == "CASH_SKIP" and not wd["shares_plan"].get("entry_zone_high")


def test_seed_from_plan_when_judge_writes_none():
    seeds = sp.seed_from_plan({"breakout_level": 104, "breakout_stop": 100, "target_1": 125}, 100.0)
    assert seeds == [{"kind": "BREAKOUT", "trigger": 104, "stop": 100, "target": 125, "origin": "seeded"}]


def test_shifted_chain_reprices_at_the_trigger():
    exps, f = shifted_chain(_chain(100.0), 100.0, 90.0, TODAY, date(2026, 11, 2))
    calls, puts = f("2026-11-27")
    c100 = float(calls[calls["strike"] == 100]["bid"].iloc[0])
    p90 = float(puts[puts["strike"] == 90]["bid"].iloc[0])
    assert c100 < 3.9 and p90 > 0
    assert "2026-11-27" in exps


def _bars(rows):
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows]


def test_ledger_limit_fill_uses_trigger_risk():
    bars = _bars([("2026-10-23", 100, 101, 99, 100), ("2026-10-26", 99, 99.5, 95.5, 97),
                  ("2026-10-27", 97, 112.5, 96, 111)])
    r = fill_ticket(100.0, 92.0, 112.0, bars, "LIMIT", 96.0)
    assert r["filled_price"] == 96.0 and r["exit_via"] == "TARGET"
    assert abs(r["r_multiple"] - (16 - 0.0005 * 208) / 4) < 1e-3


def test_ledger_stop_entry_respects_not_before():
    bars = _bars([("2026-10-23", 100, 101, 99, 100), ("2026-10-26", 101, 105, 100, 104),
                  ("2026-11-13", 103, 106, 102, 105), ("2026-11-16", 105, 126, 104, 125)])
    r = fill_ticket(100.0, 100.0, 125.0, bars, "STOP", 104.0, not_before="2026-11-13")
    assert r["filled_date"] == "2026-11-13" and r["filled_price"] == 104.0 and r["exit_via"] == "TARGET"


def test_ledger_window_expires_unfilled_and_allows_late_fill():
    bars = _bars([("2026-10-23", 100, 101, 99, 100)] + [(f"2026-11-{d:02d}", 100, 101, 99, 100) for d in range(2, 28)]
                 + [("2026-12-01", 97, 98, 95, 96), ("2026-12-02", 96, 97, 95.5, 96.5)])
    assert fill_ticket(100.0, 92.0, 112.0, bars[:27], "LIMIT", 96.0, fill_by="2026-11-20")["outcome"] == "NOT_FILLED"
    late = fill_ticket(100.0, 92.0, 112.0, bars, "LIMIT", 96.0, fill_by="2026-12-11")
    assert late["filled_date"] == "2026-12-01" and late["outcome"] == "OPEN"


def test_ledger_weekend_research_anchors_and_newer_plan_supersedes(tmp_path, monkeypatch):
    import sqlite3

    from src.tracking import ticket_paper as tp

    db = tmp_path / "t.db"
    monkeypatch.setattr(tp, "_get_connection", lambda: sqlite3.connect(str(db)))
    sc, _ = sp.normalize({"kind": "PULLBACK", "trigger": 96, "stop": 92, "target": 112}, 100, 2.0, 8.0, TODAY, None)
    assert sp.record("X", "2026-10-25", 100.0, [sc]) == 1
    bars = {"X": _bars([("2026-10-23", 100, 101, 99, 100), ("2026-10-26", 99, 99.5, 95.5, 97)])}
    assert tp.score_pending(bars)["scored"] == 1
    sp.cancel_unfilled("X", "2026-10-27")
    with sqlite3.connect(str(db)) as c:
        assert c.execute("SELECT filled_date, outcome FROM ticket_paper_trades").fetchone() == ("2026-10-26", "OPEN")
    for t in ("Y", "Z"):
        assert sp.record(t, "2026-10-25", 100.0, [sc]) == 1
        sp.cancel_unfilled(t, "2026-10-27")
        assert sp.record(t, "2026-10-27", 100.0, [sc]) == 1
    quiet = [("2026-10-23", 100, 101, 99, 100), ("2026-10-26", 100, 101, 99, 100)]
    tp.score_pending({"Y": _bars(quiet + [("2026-10-27", 98, 99, 95, 97)]),
                      "Z": _bars(quiet + [("2026-10-27", 100, 101, 99, 100), ("2026-10-28", 97, 98, 95, 96)])})
    with sqlite3.connect(str(db)) as c:
        rows = dict(c.execute("SELECT ticker || date, outcome FROM ticket_paper_trades WHERE ticker IN ('Y','Z')"))
        meta = c.execute("SELECT p_touch_pct, origin, zone_low FROM ticket_paper_trades WHERE ticker='Z'").fetchone()
    assert rows == {"Y2026-10-25": "OPEN", "Y2026-10-27": "PENDING", "Z2026-10-25": "SUPERSEDED", "Z2026-10-27": "OPEN"}
    assert meta[0] == sc["p_touch_pct"] and meta[1] == "judge" and meta[2] == sc["zone_low"]


def test_vetoed_book_keeps_judge_skips_that_code_rearmed(tmp_path, monkeypatch):
    import sqlite3

    from src.tracking import book_scorecard as bs
    from src.tracking.trust_report import component_scorecard

    db = tmp_path / "s.db"
    with sqlite3.connect(str(db)) as c:
        c.execute("CREATE TABLE suggestions (ticker, date, source, verdict, judge_verdict, gate_status, kind, setup_lane, "
                  "scorer_version, r_net, entry_low, stop, target_1)")
        c.executemany("INSERT INTO suggestions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("A", "2026-10-01", "judge", "STALK", "CASH_SKIP", "PASS", "NEW", "LLM_DISCRETIONARY", 2, -1.0, 95, 92, 110),
            ("B", "2026-10-01", "judge", "STALK", "ARMED", "PASS", "NEW", "LLM_DISCRETIONARY", 2, 0.5, 95, 92, 110),
            ("C", "2026-10-01", "judge", "CASH_SKIP", None, "PASS", "NEW", "JUDGE", None, None, 95, 92, 110)])
    monkeypatch.setattr(bs, "_get_connection", lambda: sqlite3.connect(str(db)))
    monkeypatch.setattr(bs, "_cache_path", lambda: tmp_path / "v.json")
    monkeypatch.setattr(bs, "score_suggestion", lambda r: {"r_net": 2.0 if r["ticker"] == "C" else -1.0, "exit_date": "x"})
    monkeypatch.setattr(bs, "_user_scores", lambda: [])
    monkeypatch.setattr(bs, "_scenario_stats", lambda: {})
    b = bs.book_stats()
    assert b["LLM_DISCRETIONARY"]["n"] == 1 and b["LLM_DISCRETIONARY"]["mean_r"] == 0.5
    assert b["LLM_VETOED"]["n"] == 2
    keys = {r["verdict"] for r in component_scorecard([{"verdict": "STALK", "judge_verdict": "CASH_SKIP"},
                                                       {"verdict": "STALK", "judge_verdict": "ARMED"}])}
    assert keys == {"STALK (judge CASH_SKIP)", "STALK"}
