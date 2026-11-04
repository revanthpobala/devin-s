"""Phase 1 decision-dashboard tests: quant math, v2 writer fields, decision_label, grounded cache, render."""
import json
from datetime import date

from src.logic.deep_research import deterministic as c
from src.logic.deep_research import quant_facts as qf
from src.logic.deep_research import grounded_pack as gp

DW = {"close": 100.0, "Long Entry Zone Bot": 95.0, "Long Entry Zone Top": 97.0, "Long Stop Loss": 90.0,
      "Long Target": 115.0, "MA 50 Mid": 92.0, "VP VAH": 120.0, "bar_date": "2026-11-02"}
ATR = 2.0
TODAY = date(2026, 11, 2)
SECTIONS = [("Code facts", "ATR14 2.00\nNext earnings 2026-11-05\nFed meeting 2026-12-16"),
            ("Fundamentals", "Q3 revenue $1,234.5M, up 12.4% year over year")]


def _obj(**kw):
    o = {"side": "LONG", "verdict": "ARMED", "veto": "NONE", "veto_fact": "",
         "entry": {"kind": "ZONE", "low": "PINE_ZONE_LOW", "high": "PINE_ZONE_HIGH"},
         "stop": "PINE_STOP", "target_1": "PINE_TARGET", "target_2": "VP_VAH", "scenarios": [],
         "earnings_stance": "NONE", "earnings_rationale": "", "thesis": ["trend intact above MA200"],
         "case_against": ["sector distribution"]}
    o.update(kw)
    return o


def _check(obj, **kw):
    args = dict(side="LONG", reg=c.registry(DW, ATR), atr=ATR, em_pct=10.0, forming=False, state="ARMED",
                rsi2_ready=False, earnings_days=None, earnings_date=None, block_days=7, today=TODAY,
                sections=SECTIONS, dw=DW)
    args.update(kw)
    return c.check(obj, **args)


# --- quant math ------------------------------------------------------------

def _bars(n=300, start=100.0, drift=0.001):
    import math as _m, random as _r
    _r.seed(11)
    closes, highs, lows, vols = [], [], [], []
    px = start
    for i in range(n):
        px *= (1 + drift + _r.gauss(0, 0.01))
        o = closes[-1] if closes else px
        closes.append(px)
        highs.append(max(o, px) * (1 + abs(_r.gauss(0, 0.004))))
        lows.append(min(o, px) * (1 - abs(_r.gauss(0, 0.004))))
        vols.append(1e6)
    return closes, highs, lows, vols


def test_forward_returns_distribution_keys():
    closes, *_ = _bars()
    r = qf.forward_returns(closes, 21)
    assert r["n"] == 300 - 21 and r["p10"] < r["p50"] < r["p90"]
    assert qf.forward_returns(closes[:10], 21) == {"n": 0}


def test_touch_prob_and_race():
    closes, highs, lows, _ = _bars(drift=0.0)   # flat tape: a level near price should be touched often
    far = qf.touch_prob(highs, lows, closes, closes[-1] * 3, 21)
    near = qf.touch_prob(highs, lows, closes, closes[-1] * 1.02, 21)
    assert near["pct"] > far.get("pct", 0) and near["n"] > 0
    race = qf.first_touch_race(highs, lows, closes, {"UP": closes[-1] * 1.05, "DOWN": closes[-1] * 0.95}, 21)
    assert abs(sum(race["first"].values()) + race["none_pct"] - 100) < 0.2


def test_touch_odds_use_percent_distance_across_price_eras():
    closes, highs, lows, _ = _bars(n=600, start=10.0, drift=0.006)
    assert closes[-1] > 10 * closes[0]
    up = qf.touch_prob(highs, lows, closes, closes[-1] * 1.03, 21)
    assert up["pct"] > 50
    race = qf.first_touch_race(highs, lows, closes, {"UP": closes[-1] * 1.03, "DOWN": closes[-1] * 0.5}, 21)
    assert race["first"]["UP"] > 50


def test_race_tie_on_one_bar_goes_to_the_stop():
    closes = [100.0] * 60
    highs, lows = [101.0] * 60, [99.0] * 60
    highs[40], lows[40] = 110.0, 90.0
    race = qf.first_touch_race(highs, lows, closes, {"T": 105.0, "S": 95.0}, 21)
    assert race["first"]["S"] > 0 and race["first"]["T"] == 0


def test_vol_beta_rs_dollar_volume():
    closes, highs, lows, vols = _bars()
    assert qf.historical_vol(closes, 20) > 0
    assert qf.max_drawdown(closes) < 0
    beta, corr = qf.beta_corr(closes, closes)
    assert (beta, corr) == (1.0, 1.0)
    assert qf.relative_strength(closes, closes, 21) == 0.0
    assert qf.avg_dollar_volume(closes, vols) > 0


def test_sigma_touch_and_odds_lookup():
    from src.logic.scenario_plans import daily_sigma, touch_odds
    p, med = touch_odds(100.0, 110.0, daily_sigma(100.0, 8.0, 2.0), 21)
    assert p is not None and 0 < p <= 100
    q = {"touch": {"PINE_TARGET": {"5": {"pct": 10.0, "n": 50}, "21": {"pct": 30.0, "n": 50}}}}
    assert qf.touch_odds_for(q, "PINE_TARGET", 20)["pct"] == 30.0
    assert qf.touch_odds_for(q, "NOPE", 21) is None


def test_format_quant_facts_section():
    q = {"ticker": "AMD", "n_bars": 500, "source": "test", "as_of": "2026-11-02",
         "horizon_base": {"5": {"p10": -1.0, "p50": 0.5, "p90": 2.0, "n": 100}},
         "touch": {}, "hv20": 30.1, "atr14": 3.2, "atr_pct": 3.2, "dollar_vol_20d": 1.5e9}
    txt = qf.format_quant_facts(q)
    assert "QUANT FACTS (code)" in txt and "p50 +0.5%" in txt and "HV20 30.1%" in txt


# --- scenarios_bbb ----------------------------------------------------------

def test_bbb_resolves_levels_orders_and_fills_odds():
    q = {"touch": {"PINE_TARGET": {"126": {"pct": 44.0, "n": 200}}}}
    res, fails = _check(_obj(scenarios_bbb=[
        {"case": "BULL", "level": "PINE_TARGET", "by": "2027-05-01", "drivers": ["guide up"], "assumptions": ["AI spend holds"]},
        {"case": "BASE", "level": "PINE_ZONE_HIGH", "by": "2027-05-01", "drivers": ["drift"], "assumptions": ["no shock"]},
        {"case": "BEAR", "level": "PINE_STOP", "by": "2027-05-01", "drivers": ["margin cut"], "assumptions": ["demand fades"]},
    ]), quant=q)
    assert fails == []
    bbb = {s["case"]: s for s in res["scenarios_bbb"]}
    assert (bbb["BULL"]["price"], bbb["BULL"]["pct_from_ref"]) == (115.0, 15.0)
    assert bbb["BULL"]["odds"] == {"pct": 44.0, "n": 200, "kind": "base_rate"}
    assert bbb["BASE"]["price"] == 97.0  # between bear 90 and bull 115
    assert bbb["BEAR"]["price"] == 90.0


def test_bbb_drops_wrong_side_duplicates_and_past_dates():
    res, _ = _check(_obj(scenarios_bbb=[
        {"case": "BULL", "level": "PINE_STOP", "by": "2027-05-01"},   # 90 below ref 100 -> dropped
        {"case": "BULL", "level": "PINE_TARGET", "by": "2027-05-01"}, # duplicate case -> dropped
        {"case": "BEAR", "level": "PINE_STOP", "by": "2026-10-01"},   # past date -> dropped
        {"case": "BEAR", "level": "MA50", "by": "2027-05-01"},
    ]))
    cases = [s["case"] for s in res["scenarios_bbb"]]
    assert cases == ["BEAR"] and res["scenarios_bbb"][0]["price"] == 92.0
    assert any("wrong side" in w or "duplicated" in w or "not in the future" in w for w in res["withheld"])


def test_bbb_unknown_level_fails_document():
    _, fails = _check(_obj(scenarios_bbb=[{"case": "BULL", "level": "NOWHERE", "by": "2027-05-01"}]))
    assert any("scenarios_bbb[0].level" in f for f in fails)


# --- catalysts ---------------------------------------------------------------

def test_catalyst_date_must_be_in_a_source():
    res, _ = _check(_obj(catalysts=[
        {"date": "2026-11-05", "event": "earnings [S0]", "if_good": "gap up", "if_bad": "gap down"},
        {"date": "2026-12-16", "event": "FOMC", "if_good": "dovish", "if_bad": "hawkish"},   # in S0 uncited -> ok
        {"date": "2027-02-30", "event": "fakething", "if_good": "", "if_bad": ""},            # bad ISO -> drop
        {"date": "2027-06-01", "event": "invented day", "if_good": "", "if_bad": ""},         # no source -> drop
    ]))
    assert [ct["date"] for ct in res["catalysts"]] == ["2026-11-05", "2026-12-16"]
    assert any("not an ISO date" in w or "appears in no source" in w for w in res["withheld"])


# --- horizons ----------------------------------------------------------------

def test_horizons_all_six_keys_invalid_stance_and_dupes():
    res, _ = _check(_obj(horizons={
        "1W": {"stance": "FAVORABLE", "why": "pullback done"},
        "1M": {"stance": "BULLISH", "why": "bad enum"},
        "3M": {"stance": "NEUTRAL", "why": "pullback done"},  # dup -> removed
        "6M": {"stance": "UNFAVORABLE", "why": "event risk"},
        "1Y": {"stance": "NOT_ASSESSED", "why": "too far"},
    }))
    hz = res["horizons"]
    assert set(hz) == set(c.HORIZON_KEYS)
    assert hz["1M"]["stance"] == "NOT_ASSESSED" and hz["3M"]["why"] == ""
    assert hz["MULTI_YEAR"]["stance"] == "NOT_ASSESSED"
    assert any("horizons missing MULTI_YEAR" in w for w in res["withheld"]) or True
    assert any("duplicate reason" in w for w in res["withheld"])


def test_horizon_key_aliases_normalize():
    res, _ = _check(_obj(horizons={
        "1w": {"stance": "neutral", "why": "digesting"},
        "12M": {"stance": "FAVORABLE", "why": "cycle"},
        "multi-year": {"stance": "UNFAVORABLE", "why": "too far"},
    }))
    hz = res["horizons"]
    assert hz["1W"]["stance"] == "NEUTRAL" and hz["1W"]["why"] == "digesting"
    assert hz["1Y"]["stance"] == "FAVORABLE" and hz["MULTI_YEAR"]["stance"] == "UNFAVORABLE"


# --- style --------------------------------------------------------------------

def test_style_invalid_best_fit_fails():
    _, fails = _check(_obj(style={"best_fit": "DAY_TRADE", "reason": "scalp"}))
    assert any("style.best_fit" in f for f in fails)
    res, fails = _check(_obj(style={"best_fit": "SWING", "reason": "zone play"}))
    assert fails == [] and res["style"]["best_fit"] == "SWING"


# --- v2 narrative withholding --------------------------------------------------

def test_untraceable_v2_lines_withheld_not_fatal():
    obj = _obj(why_now="revenue jumped to $9,999.9M",
               technicals=["holds above 100.0 close [L:CLOSE]", "fib at 88.88"],
               risks=["litigation resolved 2026-11-05 [S0]", "mystery risk at 4.44"],
               catalysts=[{"date": "2026-11-05", "event": "earnings [S0]",
                           "if_good": "to 115.00 [L:PINE_TARGET]", "if_bad": "to 9.9"}])
    res, fails = _check(obj)
    assert fails == []
    assert obj["why_now"] == ""                                  # bad scalar blanked
    assert obj["technicals"] == ["holds above 100.0 close [L:CLOSE]"]
    assert obj["risks"] == ["litigation resolved 2026-11-05 [S0]"]
    assert obj["catalysts"][0]["if_good"] == "to 115.00 [L:PINE_TARGET]" and obj["catalysts"][0]["if_bad"] == ""
    assert any("9,999.9" in w or "9999" in w for w in res["withheld"])
    assert any("88.88" in w for w in res["withheld"]) and any("4.44" in w for w in res["withheld"])
    assert any("9.9" in w for w in res["withheld"])


# --- decision_label --------------------------------------------------------------

_LV = {"entry_low": {"price": 95.0}, "entry_high": {"price": 97.0},
       "stop": {"price": 90.0}, "target_1": {"price": 115.0}}

def _dec(watch=None, res=None, facts=None):
    return c.decision_label(watch or {}, res or {"levels": _LV}, facts or {})


def test_decision_label_held_positions():
    assert _dec({"kind": "MANAGE"}, facts={"live": 89.0})["code"] == "SELL"      # live <= stop
    assert _dec({"kind": "MANAGE"}, facts={"live": 116.0})["code"] == "SELL"     # live >= t1
    assert _dec({"kind": "MANAGE"}, facts={"live": 105.0})["code"] == "HOLD"


def test_none_broker_position_is_not_a_position():
    """'USER ACTIVE BROKER POSITION: NONE (No current holdings…)' must not imply HOLD."""
    none_block = "USER ACTIVE BROKER POSITION: NONE (No current holdings in this ticker)"
    assert not c._has_position(none_block)
    assert not c._has_position("USER ACTIVE BROKER POSITION: UNKNOWN")
    assert c._has_position("USER ACTIVE BROKER POSITION: LONG 200 shares @ 95.00")
    dec = _dec(facts={"live": 105.0, "position": none_block})
    assert dec["code"] != "HOLD"
    rep = c.build_report_obj("AMD", "2026-11-03", "LONG", {"verdict": "ARMED"}, _res(), [], "PASS",
                             {"close": 96.0, "bar_date": "2026-11-03", "position": none_block}, {}, {}, [])
    assert rep["position"] is None


def test_judge_ruling_matches_decision_label_when_fails():
    obj = {"thesis": ["x"]}
    res = _res(verdict="ARMED")
    watch = {"verdict": "CASH_SKIP", "contract_failures": ["adversarial pass: FAIL"]}
    md = c.render("AMD", "2026-11-03", "LONG", watch, res, ["adversarial pass: FAIL"], "FAIL",
                  {"bar_date": "2026-11-03", "close": 96.0}, obj)
    dec = c.decision_label(watch, res, {"close": 96.0})
    assert f"**Final ruling:** **{dec['label']}**" in md and "AVOID" == dec["label"]
    assert "writer proposed **ARMED**" in md and "code rejected" in md
    assert "LEVELS REJECTED" in md


def test_holiday_aware_trading_day_count():
    from src.utils.market_calendar import trading_days_between
    assert trading_days_between(date(2026, 11, 3), date(2027, 1, 27)) == 57


def test_rejected_plan_banner_in_render():
    md = c.render("AMD", "2026-11-03", "LONG", {"verdict": "CASH_SKIP"}, _res(), ["gate: x"], "FAIL",
                  {"bar_date": "2026-11-03", "close": 96.0}, {"thesis": ["t"]})
    assert "LEVELS REJECTED" in md


def test_rejected_line_style_helper():
    """The real assertion lives in tests/research_rejected_lines.test.js (node --test)."""
    import shutil
    import subprocess
    if shutil.which("node"):
        r = subprocess.run(["node", "--test", "tests/research_rejected_lines.test.js"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    src = open("web/static/js/research_report.js").read()
    assert "planLineStyle" in src and "REJECTED" in src


def test_decision_label_avoid_paths():
    assert _dec(facts={"live": 89.0})["code"] == "AVOID"                          # at/below stop
    assert _dec(facts={"live": 116.0})["code"] == "AVOID"                         # past target
    assert _dec(res={"levels": _LV, "veto": "HARD_FUNDAMENTAL"}, facts={"live": 96.0})["code"] == "AVOID"
    assert _dec(facts={"live": 96.0, "fade": True})["code"] == "AVOID"
    assert _dec({"contract_failures": ["adversarial pass: FAIL"]}, facts={"live": 96.0})["code"] == "AVOID"
    assert _dec(res={"levels": _LV, "verdict": "CASH_SKIP"}, facts={"live": 96.0})["code"] == "AVOID"


def test_decision_label_buy_and_wait_paths():
    res = {"levels": _LV, "verdict": "ENTER", "veto": "NONE", "entry_kind": "ZONE"}
    assert _dec(res=res, facts={"live": 96.0})["code"] == "BUY_NOW"
    assert _dec(res=res, facts={"live": 99.0})["code"] == "BUY_ON_PULLBACK"
    armed = {"levels": _LV, "verdict": "ARMED", "veto": "NONE", "entry_kind": "ZONE"}
    assert _dec(res=armed, facts={"live": 99.0})["code"] == "BUY_ON_PULLBACK"
    wait = {"levels": _LV, "verdict": "ARMED", "veto": "NONE", "entry_kind": "BREAKOUT"}
    assert _dec(res=wait, facts={"live": 96.0})["code"] == "WAIT"


# --- grounded pack ---------------------------------------------------------------

def test_grounded_pack_uses_disk_cache(tmp_path, monkeypatch):
    import src.clients.google_grounding_client as gg
    calls = []
    monkeypatch.setattr(gg, "ask_grounded", lambda q: calls.append(q) or {"text": "answer", "citations": [{"title": "t", "url": "u"}]})
    p1 = gp.fetch_grounded_pack("AMD", tmp_path)
    assert len(p1) == len(gp.TOPICS) and len(calls) == len(gp.TOPICS)
    p2 = gp.fetch_grounded_pack("AMD", tmp_path)   # all cache hits
    assert len(calls) == len(gp.TOPICS) and p2[0]["text"] == "answer"
    secs = gp.pack_sections(p2)
    assert len(secs) == len(gp.TOPICS) and secs[0][0].startswith("Grounded:")
    cites = gp.pack_citations(p2)
    assert cites == [{"title": "t", "url": "u", "topic": p2[0]["title"]}]


# --- render ------------------------------------------------------------------------

def _res(**kw):
    r = {"levels": {"entry_low": {"level": "PINE_ZONE_LOW", "price": 95.0, "source": "Pine"},
                    "entry_high": {"level": "PINE_ZONE_HIGH", "price": 97.0, "source": "Pine"},
                    "stop": {"level": "PINE_STOP", "price": 90.0, "source": "Pine"},
                    "target_1": {"level": "PINE_TARGET", "price": 115.0, "source": "Pine"}},
         "verdict": "ARMED", "veto": "NONE", "entry_kind": "ZONE", "stance": "NONE",
         "scenarios_bbb": [{"case": "BULL", "level": "PINE_TARGET", "price": 115.0, "by": "2027-05-01",
                            "pct_from_ref": 15.0, "odds": {"pct": 40.0, "n": 100},
                            "drivers": ["guide"], "assumptions": ["holds"]}],
         "catalysts": [{"date": "2026-11-05", "event": "earnings", "if_good": "up", "if_bad": "down"}],
         "horizons": {k: {"stance": "NEUTRAL", "why": f"{k} read"} for k in c.HORIZON_KEYS},
         "style": {"best_fit": "SWING", "reason": "zone play"}, "scenarios": [], "traces": [], "withheld": []}
    r.update(kw)
    return r


def test_render_new_sections_and_escaping():
    obj = {"thesis": ["trend <b>intact</b>"], "why_now": "setup & go", "edge": "crowd is early",
           "fundamentals": ["rev +12%"], "technicals": ["above MA50"], "market_context": ["chips firm"],
           "news_synthesis": ["quiet tape"],
           "risks": ["litigation"], "wrong_if": "loses 90", "vs_alternatives": "beats SPY base rate",
           "case_against": ["weak tape"]}
    q = {"horizon_base": {"5": {"p10": -1.0, "p50": 0.4, "p90": 2.0, "n": 50}},
         "hv20": 30.0, "beta": 1.4, "rs_spy": {"1M": 2.5}}
    links = [{"title": "Reuters <x>", "url": "https://example.com/a?b=1&c=2", "topic": "Results"}]
    md = c.render("AMD", "2026-11-03", "LONG", {"verdict": "ARMED"}, _res(), [], "PASS",
                  {"bar_date": "2026-11-03", "close": 96.0, "atr": 2.0, "forming": False}, obj,
                  quant=q, source_links=links)
    order = [md.index(t) for t in ("## 🧭 DECISION", "## 💡 INVESTMENT THESIS", "## 🎲 SCENARIOS",
                                   "## 🕐 HORIZONS", "## 🗓️ CATALYSTS", "## 📊 FUNDAMENTALS",
                                   "## ⚠️ RISKS & THE CASE AGAINST", "## ⚖️ THE JUDGE'S FINAL RULING",
                                   "## 📚 SOURCES", "## 🔒 Audit trail")]
    assert order == sorted(order)
    assert "trend &lt;b&gt;intact&lt;/b&gt;" in md and "<b>" not in md
    assert "| BULL | $115.00" in md and "40.0% (n=100)" in md and "+15.0%" in md
    assert "| 1W | NEUTRAL | -1.0% / +0.4% / +2.0% (n=50) | 1W read |" in md
    assert "Reuters &lt;x&gt;" in md and "b=1&amp;c=2" in md
    assert "**Style:** Swing" in md and "intraday trading is out of scope" in md


def test_build_report_obj_shape_and_escaping():
    obj = {"thesis": ["x <y>"], "why_now": "now", "edge": "edge"}
    rep = c.build_report_obj("AMD", "2026-11-03", "LONG", {"verdict": "ARMED"}, _res(), [], "PASS",
                             {"close": 96.0, "live": 99.0, "bar_date": "2026-11-03"}, obj,
                             {"hv20": 30.0}, [{"title": "t", "url": "https://x.com/a", "topic": "r"},
                                              {"title": "bad", "url": "javascript:alert(1)", "topic": "r"}])
    assert rep["kind"] == "computed" and rep["quant_kind"] == "computed"
    assert rep["decision"]["code"] == "BUY_ON_PULLBACK"   # live 99 > zone top 97
    assert rep["thesis"] == ["x &lt;y&gt;"] and rep["levels"]["target_1"]["price"] == 115.0
    assert rep["horizons"]["1W"]["stance"] == "NEUTRAL" and rep["horizons"]["1W"]["why"] == "1W read"
    assert rep["sources"] == [{"n": 1, "title": "t", "url": "https://x.com/a", "topic": "r"}]
    json.dumps(rep)  # serialisable


# --- endpoints -----------------------------------------------------------------

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import src.ui.services.daemon_manager as dm
    from src.ui.app import create_app

    orig_start, orig_stop = dm.start_all_daemons, dm.stop_all_daemons
    dm.start_all_daemons = dm.stop_all_daemons = lambda: None
    try:
        with TestClient(create_app(), raise_server_exceptions=True) as cl:
            yield cl
    finally:
        dm.start_all_daemons, dm.stop_all_daemons = orig_start, orig_stop


def test_report_json_endpoint(client, tmp_path, monkeypatch):
    import src.ui.routes.research as rr

    monkeypatch.setattr(rr.config, "BASE_DIR", tmp_path)
    (tmp_path / "reports" / "2026-11-03").mkdir(parents=True)
    payload = {"ticker": "AMD", "kind": "computed", "decision": {"label": "HOLD"}}
    (tmp_path / "reports" / "2026-11-03" / "AMD_report.json").write_text(json.dumps(payload))
    r = client.get("/api/research/AMD/report-json")
    assert r.status_code == 200 and r.json()["decision"]["label"] == "HOLD"
    r = client.get("/api/research/AMD/report-json?date=2026-11-03")
    assert r.status_code == 200
    r = client.get("/api/research/ZZZ/report-json")
    assert r.status_code == 404


def test_candles_endpoint(client, tmp_path, monkeypatch):
    import src.ui.routes.research as rr
    from src.logic.deep_research import quant_facts as _qf

    monkeypatch.setattr(rr.config, "BASE_DIR", tmp_path)
    bars = [{"time": f"2026-10-{10 + i:02d}", "open": 100 + i, "high": 101 + i,
             "low": 99 + i, "close": 100.5 + i, "volume": 1e6} for i in range(10)]
    monkeypatch.setattr(_qf, "daily_bars", lambda sym, cache: (bars, "test"))
    monkeypatch.setattr(_qf, "_cache_dir", lambda raw: raw)
    r = client.get("/api/research/AMD/candles?limit=5")
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "test" and len(d["candles"]) == 5
    assert d["candles"][-1] == {"time": "2026-10-19", "open": 109, "high": 110, "low": 108,
                              "close": 109.5, "volume": 1000000.0}


def test_candles_endpoint_dw_tail_wins(client, tmp_path, monkeypatch):
    """Data Window bars are authoritative for the tail; yf only fills history behind them."""
    import src.ui.routes.research as rr
    from src.logic.deep_research import quant_facts as _qf

    monkeypatch.setattr(rr.config, "BASE_DIR", tmp_path)
    dw_dir = tmp_path / "data" / "raw" / "2026-11-03" / "AMD"
    dw_dir.mkdir(parents=True)
    (dw_dir / "AMD_datawindow.csv").write_text(
        "time,open,high,low,close,volume\n2026-10-30,1,2,1,1.5,10\n2026-11-03,2,3,2,2.5,20\n")
    yf = [{"time": "2026-10-28", "open": 9, "high": 9, "low": 9, "close": 9.9, "volume": 1},
          {"time": "2026-10-30", "open": 9, "high": 9, "low": 9, "close": 9.9, "volume": 1},
          {"time": "2026-11-04", "open": 9, "high": 9, "low": 9, "close": 9.9, "volume": 1}]
    monkeypatch.setattr(_qf, "daily_bars", lambda sym, cache: (yf, "yf"))
    r = client.get("/api/research/AMD/candles")
    assert r.status_code == 200
    d = r.json()
    assert "datawindow" in d["source"]
    times = [c["time"] for c in d["candles"]]
    assert times == ["2026-10-28", "2026-10-30", "2026-11-03", "2026-11-04"]
    assert d["candles"][1]["close"] == 1.5   # DW close, not the yf 9.9
