"""Transmission-chain tests: schema/cite checks withhold bad chains without ever failing the
document, tape_status is code-computed, and the market-events cache is shared per date."""
import json
from datetime import date

from src.logic.deep_research import deterministic as c
from src.logic.deep_research import market_events as me

DW = {"close": 100.0, "Long Entry Zone Bot": 95.0, "Long Entry Zone Top": 97.0, "Long Stop Loss": 90.0,
      "Long Target": 115.0, "MA 50 Mid": 92.0, "VP VAH": 120.0, "bar_date": "2026-11-02"}
ATR = 2.0
TODAY = date(2026, 11, 2)
SECTIONS = [("Code facts", "ATR14 2.00\nNext earnings 2026-11-05"),
            ("Market events (last 24h)", "2026-11-01: Congress flips; datacenter permits stall"),
            ("Market tape (code)", "SOXX: 1d -2.10% · 5d -4.00%")]
TAPE = {"as_of": "2026-11-02", "rows": [
    {"symbol": "SPY", "d1": -1.2, "d5": -2.0, "last": 500.0},
    {"symbol": "SOXX", "d1": -2.1, "d5": -4.0, "last": 200.0},
    {"symbol": "QQQ", "d1": 0.1, "d5": 0.5, "last": 400.0},
    {"symbol": "NVDA", "d1": -3.0, "d5": -5.0, "last": 150.0}]}


def _obj(**kw):
    o = {"side": "LONG", "verdict": "ARMED", "veto": "NONE", "veto_fact": "",
         "entry": {"kind": "ZONE", "low": "PINE_ZONE_LOW", "high": "PINE_ZONE_HIGH"},
         "stop": "PINE_STOP", "target_1": "PINE_TARGET", "target_2": "VP_VAH", "scenarios": [],
         "earnings_stance": "NONE", "earnings_rationale": "", "thesis": ["trend intact"],
         "case_against": ["weak tape"]}
    o.update(kw)
    return o


def _chain(**kw):
    ch = {"event": "2026-11-01 Congress flips; datacenter permits stall [S1]",
          "status": "VERIFIED", "channel": "AI_CAPEX",
          "chain": [{"step": "Chip supply indexes fell [S2]", "kind": "FACT"},
                    {"step": "hyperscaler orders follow with a lag", "kind": "INFERENCE"}],
          "exposure": "TSM is a supplier [S2]", "direction": "HEADWIND", "horizon": "1M",
          "confirm": [{"symbol": "SOXX", "move": "DOWN"}], "invalidate": "permits clear"}
    ch.update(kw)
    return ch


def _check(obj, tape=TAPE):
    return c.check(obj, side="LONG", reg=c.registry(DW, ATR), atr=ATR, em_pct=10.0, forming=False,
                   state="ARMED", rsi2_ready=False, earnings_days=None, earnings_date=None,
                   block_days=7, today=TODAY, sections=SECTIONS, dw=DW, tape=tape)


def test_valid_chain_passes_with_tape_status():
    res, fails = _check(_obj(transmission=[_chain()]))
    assert fails == []
    (ch,) = res["transmission"]
    assert ch["tape_status"] == "CONFIRMED"          # SOXX 1d -2.1% vs expected DOWN
    assert ch["confirm"][0]["actual"] == -2.1
    assert ch["direction"] == "HEADWIND" and ch["horizon"] == "1M"


def test_tape_status_confirmed_contradicted_untested():
    rows = TAPE["rows"]
    assert me.tape_status([{"symbol": "SOXX", "move": "DOWN"}, {"symbol": "NVDA", "move": "DOWN"}], TAPE) == "CONFIRMED"
    assert me.tape_status([{"symbol": "SOXX", "move": "UP"}], TAPE) == "CONTRADICTED"      # -2.1 vs UP
    assert me.tape_status([{"symbol": "QQQ", "move": "DOWN"}], TAPE) == "UNTESTED"          # +0.1 too small
    assert me.tape_status([{"symbol": "NOPE", "move": "UP"}], TAPE) == "UNTESTED"           # no row
    assert me.tape_status([], TAPE) == "UNTESTED"


def test_uncited_event_withheld():
    res, fails = _check(_obj(transmission=[_chain(event="Congress flips, no cite")]))
    assert fails == [] and res["transmission"] == []
    assert any("no [S" in w for w in res["transmission_withheld"])


def test_scenario_chain_needs_if_phrasing():
    res, _ = _check(_obj(transmission=[_chain(status="SCENARIO")]))
    assert res["transmission"] == []
    res, _ = _check(_obj(transmission=[_chain(status="SCENARIO",
                                            event="If Congress flips, permits stall [S1]")]))
    assert len(res["transmission"]) == 1


def test_verified_chain_needs_cited_date():
    res, _ = _check(_obj(transmission=[_chain(event="Congress flips [S1]")]))
    assert res["transmission"] == []           # no ISO date in event
    res, _ = _check(_obj(transmission=[_chain(event="2030-01-01 invented [S1]")]))
    assert res["transmission"] == []           # date not in the cited section


def test_confirm_symbol_must_be_channel_proxy():
    res, fails = _check(_obj(transmission=[_chain(confirm=[{"symbol": "GLD", "move": "UP"}])]))
    assert fails == [] and res["transmission"] == []      # GLD is not an AI_CAPEX proxy
    assert any("not a AI_CAPEX proxy" in w for w in res["transmission_withheld"])


def test_chain_kinds_and_enums_enforced():
    res, _ = _check(_obj(transmission=[_chain(chain=[{"step": "fell 2%", "kind": "INFERENCE"}])]))
    assert res["transmission"] == []                      # INFERENCE with numbers
    res, _ = _check(_obj(transmission=[_chain(chain=[{"step": "fell", "kind": "FACT"}])]))
    assert res["transmission"] == []                      # FACT without cite
    res, _ = _check(_obj(transmission=[_chain(direction="BULLISH")]))
    assert res["transmission"] == []
    res, _ = _check(_obj(transmission=[_chain(channel="MARS")]))
    assert res["transmission"] == []


def test_transmission_never_grows_fails():
    res, fails = _check(_obj(transmission=[_chain(event="garbage"),
                                           _chain(channel="NOPE"),
                                           _chain(direction="WRONG")]))
    assert fails == [] and res["transmission"] == [] and len(res["transmission_withheld"]) >= 3


def test_transmission_drops_do_not_feed_retry_lists():
    """A dropped chain must stay out of `withheld` — withheld lines trigger and score the
    writer retry, so a bad chain could otherwise change the verdict."""
    res, fails = _check(_obj(transmission=[_chain(channel="NOPE")]))
    assert fails == []
    assert res["transmission"] == []
    assert res["transmission_withheld"] and all("transmission" in w for w in res["transmission_withheld"])
    assert not any("transmission" in w for w in res["withheld"])


def test_tape_status_untested_when_event_postdates_tape():
    """A VERIFIED event dated after the tape's last session cannot be tested against it."""
    sections = [("Code facts", "ATR14 2.00"),
                ("Market events (last 24h)", "2026-11-05: futures dump overnight"),
                ("Market tape (code)", "SOXX 1d -2.10%")]
    stale_tape = {"as_of": "2026-11-02",
                  "rows": [{"symbol": "SOXX", "d1": -2.1, "d5": -4.0, "last": 200.0}]}
    res, fails = c.check(_obj(transmission=[_chain(event="2026-11-05 futures dump [S1]")]),
                         side="LONG", reg=c.registry(DW, ATR), atr=ATR, em_pct=10.0, forming=False,
                         state="ARMED", rsi2_ready=False, earnings_days=None, earnings_date=None,
                         block_days=7, today=TODAY, sections=sections, dw=DW, tape=stale_tape)
    assert fails == []
    (ch,) = res["transmission"]
    assert ch["tape_status"] == "UNTESTED"     # SOXX -2.1 would otherwise be CONFIRMED


def test_ticker_and_sector_etf_are_valid_confirms():
    """The ticker and its sector ETF ride in the tape rows; any channel may confirm off them."""
    tape = {"as_of": "2026-11-02", "extras": ["AMD", "SMH"],
            "rows": [{"symbol": "AMD", "d1": -3.5, "d5": -6.0, "last": 100.0, "med_move": 1.0},
                     {"symbol": "SMH", "d1": -2.5, "d5": -4.0, "last": 150.0, "med_move": 1.0}]}
    res, fails = _check(_obj(transmission=[_chain(confirm=[{"symbol": "AMD", "move": "DOWN"},
                                                          {"symbol": "SMH", "move": "DOWN"}])]), tape=tape)
    assert fails == []
    (ch,) = res["transmission"]
    assert ch["tape_status"] == "CONFIRMED"


def test_per_symbol_threshold_overrides_fixed_floor():
    """med_move is the significance bar: a 1% move is loud for TLT, noise for ^VIX."""
    tape = {"rows": [{"symbol": "TLT", "d1": 0.5, "med_move": 0.9},
                     {"symbol": "^VIX", "d1": 0.5, "med_move": 4.0}]}
    # 0.5% clears the old fixed 0.3 floor but is under TLT's own 0.9 median -> UNTESTED
    assert me.tape_status([{"symbol": "TLT", "move": "UP"}], tape) == "UNTESTED"
    assert me.tape_status([{"symbol": "^VIX", "move": "UP"}], tape) == "UNTESTED"
    tape["rows"][0]["d1"] = 1.2
    assert me.tape_status([{"symbol": "TLT", "move": "UP"}], tape) == "CONFIRMED"


def test_dedupe_sources_keeps_distinct_titles_on_redirect_domain():
    links = [
        {"title": "Tesla deliveries miss", "url": "https://vertexaisearch.cloud.google.com/r1", "topic": "Results"},
        {"title": "Tesla margins compress", "url": "https://vertexaisearch.cloud.google.com/r2", "topic": "Results"},
        {"title": "Tesla deliveries miss", "url": "https://other.example/x", "topic": "Results"},
    ]
    out = c._dedupe_sources(links)
    assert len(out) == 2 and out[0]["title"] == "Tesla deliveries miss" and out[1]["title"] == "Tesla margins compress"


def test_empty_market_events_scan_not_cached(tmp_path, monkeypatch):
    import src.clients.google_grounding_client as gg
    calls = []
    monkeypatch.setattr(gg, "ask_grounded", lambda q: calls.append(q) or {"text": "", "citations": []})
    me.fetch_market_events("2026-11-02", tmp_path)
    assert not (tmp_path / "_MARKET" / "market_events.json").exists()
    monkeypatch.setattr(gg, "ask_grounded", lambda q: calls.append(q) or {"text": "now events", "citations": []})
    out = me.fetch_market_events("2026-11-02", tmp_path)
    assert out["text"] == "now events" and len(calls) == 2   # retried, not stuck on the empty cache


def test_tape_section_handles_missing_d5():
    tape = {"as_of": "2026-11-02", "rows": [{"symbol": "NEW", "d1": 1.0, "d5": None, "last": 10.0}]}
    txt = me.tape_section(tape)
    assert "NEW" in txt and "5d —" in txt


def test_pre_close_bar_cache_refetched_after_close(tmp_path, monkeypatch):
    from datetime import date as _date
    from src.logic.deep_research import quant_facts as qf

    fetches = []
    monkeypatch.setattr(qf, "_yf_download", lambda s: fetches.append(s) or
                        [{"time": _date.today().isoformat(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    monkeypatch.setattr(qf, "bars_settled", lambda now=None: False)
    qf.daily_bars("SPY", tmp_path)
    qf.daily_bars("SPY", tmp_path)
    assert len(fetches) == 1                        # intraday: one snapshot shared
    monkeypatch.setattr(qf, "bars_settled", lambda now=None: True)
    qf.daily_bars("SPY", tmp_path)
    qf.daily_bars("SPY", tmp_path)
    assert len(fetches) == 2                        # refetched once after the close, then cached


def test_bars_settled_on_non_trading_day():
    from datetime import datetime as _dt
    from src.logic.deep_research.quant_facts import bars_settled
    assert bars_settled(_dt(2026, 11, 7, 9, 0))      # Saturday
    assert not bars_settled(_dt(2026, 11, 3, 9, 0))  # Tuesday morning


def test_earnings_past_calendar_date_returns_none(monkeypatch):
    """A stale past earnings date from the yfinance calendar must not read as 'earnings today'."""
    from datetime import datetime as _dt

    class _FakeTicker:
        def get_earnings_dates(self, limit=12):
            return None
        calendar = {"Earnings Date": [_dt(2019, 1, 7)]}

    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: _FakeTicker())
    from src.clients.earnings_client import get_next_earnings_days
    assert get_next_earnings_days("FAKE", as_of_date="2019-06-03") is None


def test_render_and_report_json_section():
    res, _ = _check(_obj(transmission=[_chain()]))
    obj = {"thesis": ["x <b>"]}
    facts = {"bar_date": "2026-11-02", "close": 100.0, "market_tape": TAPE}
    md = c.render("AMD", "2026-11-02", "LONG", {"verdict": "ARMED"}, res, [], "PASS", facts, obj)
    assert "## 🔀 EVENTS & TRANSMISSION" in md and "Tape check" in md and "Market tape" in md
    rep = c.build_report_obj("AMD", "2026-11-02", "LONG", {"verdict": "ARMED"}, res, [], "PASS",
                             {"close": 100.0, "market_tape": TAPE}, obj, {}, [])
    assert rep["transmission"][0]["tape_status"] == "CONFIRMED" and rep["market_tape"]["rows"]
    json.dumps(rep)


def test_adversary_plan_md_excludes_transmission():
    res, _ = _check(_obj(transmission=[_chain()]))
    facts = {"bar_date": "2026-11-02", "close": 100.0, "market_tape": TAPE}
    md = c.render("AMD", "2026-11-02", "LONG", {"verdict": "ARMED"}, res, [], "pending", facts,
                  {"thesis": ["x"]}, include_transmission=False)
    assert "EVENTS & TRANSMISSION" not in md


def test_market_events_cached_per_date(tmp_path, monkeypatch):
    import src.clients.google_grounding_client as gg
    calls = []
    monkeypatch.setattr(gg, "ask_grounded",
                        lambda q: calls.append(q) or {"text": "events", "citations": []})
    a = me.fetch_market_events("2026-11-02", tmp_path)
    b = me.fetch_market_events("2026-11-02", tmp_path)
    assert len(calls) == 1 and a["topic"] == "market_events" and b["text"] == "events"


def test_rules_prompt_block_lists_channels():
    from src.logic.deep_research.transmission_rules import CHANNELS, rules_prompt_block
    block = rules_prompt_block()
    for name, ch in CHANNELS.items():
        assert name in block and ch["proxies"]
        for sym in ch["proxies"]:
            assert sym in me.TAPE
