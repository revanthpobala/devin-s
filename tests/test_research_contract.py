from datetime import date, datetime

from src.logic.deep_research import deterministic as c

DW = {"close": 100.0, "Long Entry Zone Bot": 95.0, "Long Entry Zone Top": 97.0, "Long Stop Loss": 90.0,
      "Long Target": 115.0, "MA 50 Mid": 92.0, "VP VAH": 120.0, "bar_date": "2026-11-02"}
ATR = 2.0
TODAY = date(2026, 11, 2)


def _obj(**kw):
    o = {"side": "LONG", "verdict": "ARMED", "veto": "NONE", "veto_fact": "",
         "entry": {"kind": "ZONE", "low": "PINE_ZONE_LOW", "high": "PINE_ZONE_HIGH"},
         "stop": "PINE_STOP", "target_1": "PINE_TARGET", "target_2": "VP_VAH", "scenarios": [],
         "earnings_stance": "NONE", "earnings_rationale": "", "thesis": ["trend intact above MA200"],
         "case_against": ["sector distribution"]}
    o.update(kw)
    return o


SECTIONS = [("Code facts", "ATR14 2.00\nNext earnings 2026-11-05"), ("Fundamentals", "Q3 revenue $1,234.5M, up 12.4% year over year")]


def _check(obj, **kw):
    args = dict(side="LONG", reg=c.registry(DW, ATR), atr=ATR, em_pct=10.0, forming=False, state="ARMED",
                rsi2_ready=False, earnings_days=None, earnings_date=None, block_days=7, today=TODAY,
                sections=SECTIONS, dw=DW)
    args.update(kw)
    return c.check(obj, **args)


def test_numbers_skip_dates_and_identifiers():
    assert c.numbers("held MA200 into the 8-K on 2026-11-05; Q3 guide intact") == []
    assert c.numbers("stop at 60.99, a 4x move, $1,234.5M, S&P 500") == ["60.99", "4", "1234.5", "500"]


def test_trace_requires_matching_citation():
    reg = c.registry(DW, ATR)
    rows, fails = c.trace("t", "revenue $1,234.5M [S1] grew 12% [S1], stop 90.00 [L:PINE_STOP], VAH 120 [DW:VP VAH]",
                          SECTIONS, DW, reg)
    assert fails == [] and [r["number"] for r in rows] == ["1234.5", "12", "90.00", "120"]
    assert rows[0]["source"] == "Fundamentals"
    assert c.trace("t", "reports 2026-11-05 [S0]", SECTIONS, DW, reg) == ([{"field": "t", "number": "2026-11-05", "cite": "S0", "source": "Code facts"}], [])
    rows, fails = c.trace("t", "grew 12.4% [S0], reports 2026-11-05", SECTIONS, DW, reg)
    assert fails == [] and [r["cite"] for r in rows] == ["S1", "S0"]
    for text in ("grew 15% [S1]", "grew 15%", "grew 15% [S9]", "close 101 [L:CLOSE]", "reports 2026-11-06 [S0]"):
        assert any("appears in no source" in f for f in c.trace("t", text, SECTIONS, DW, reg)[1]), text


def test_registry_spans_non_pine_research():
    dw = {**DW, "options_put_wall": 94.0, "conformal_90_floor_21d": 88.2, "_underlying_key_supports": [93.1, 91.0],
          "_htf_levels": {"Weekly 20 EMA": 96.4}, "unfilled_gaps": ["Gap Up [$91.55 – $92.28]"]}
    bars = [{"high": 100 + i % 3, "low": 95 - i % 4, "close": 98} for i in range(60)]
    reg = c.registry(dw, ATR, bars)
    assert reg["OPT_PUT_WALL"]["source"] == "options_put_wall" and reg["CONF_FLOOR_21D"]["price"] == 88.2
    assert reg["KEY_SUPPORT_2"]["price"] == 91.0 and reg["HTF_WEEKLY_20_EMA"]["price"] == 96.4
    assert (reg["GAP_UP_1_LOW"]["price"], reg["GAP_UP_1_HIGH"]["price"]) == (91.55, 92.28)
    assert reg["SWING_LOW_20"]["price"] == 92.0 and reg["PREV_HIGH"]["price"] == 100 + 58 % 3
    assert c.atr_from_bars(bars) > 0


def test_registry_drops_invalid_pine_and_stale_rsi2_levels():
    reg = c.registry({**DW, "Long Entry Zone Bot": 85.0, "Long Entry Zone Top": 88.0, "RSI2 Fixed Stop": 80.0,
                      "RSI2 Events Pack": 2 * 1024}, ATR)
    assert "PINE_ZONE_LOW" not in reg and "RSI2_STOP" not in reg and reg["PINE_STOP"]["price"] == 90.0
    reg = c.registry({**DW, "Long Target": 99.0}, ATR)
    assert "PINE_TARGET" not in reg and "PINE_STOP" not in reg


def test_registry_effective_stop_and_floor_limit():
    reg = c.registry(DW, ATR)
    assert reg["ATR_FLOOR_STOP"]["price"] == 90.0 and reg["ATR_FLOOR_STOP"]["source"].startswith("PINE_STOP")
    assert reg["RR_FLOOR_LIMIT"]["price"] == round((115 + 2 * 90) / 3, 2)
    tight = c.registry({**DW, "Long Stop Loss": 94.5}, ATR)
    assert tight["ATR_FLOOR_STOP"]["price"] == 93.6


def test_valid_armed_plan_resolves_with_provenance():
    res, fails = _check(_obj())
    assert fails == []
    assert res["levels"]["stop"] == {"level": "PINE_STOP", "price": 90.0, "source": "Long Stop Loss"}
    assert res["rr_at_entry"] == round(18 / 7, 2)


def test_numeric_level_is_banned():
    _, fails = _check(_obj(stop=90.0))
    assert any("numeric level is banned" in f for f in fails)
    _, fails = _check(_obj(stop="88.5"))
    assert any("numeric level is banned" in f for f in fails)


def test_inverted_geometry_fails():
    _, fails = _check(_obj(target_1="MA50"))
    assert any("inverted geometry" in f for f in fails)


def test_tight_stop_fails_not_repaired():
    dw = {**DW, "Long Stop Loss": 94.5}
    _, fails = _check(_obj(), reg=c.registry(dw, ATR))
    assert any("tighter than" in f for f in fails)


def test_enter_on_forming_bar_and_wrong_state_fail():
    _, fails = _check(_obj(verdict="ENTER"), forming=True)
    assert any("forming" in f for f in fails) and any("Pine state is ARMED" in f for f in fails)


def test_earnings_inside_block_requires_event_risk_and_stance():
    _, fails = _check(_obj(), earnings_days=3)
    assert any("block 7" in f for f in fails) and any("earnings_stance NONE" in f for f in fails)
    _, fails = _check(_obj(verdict="WATCH (EVENT RISK)", veto="EVENT_RISK", veto_fact="earnings on 2026-11-05 [S0]",
                           earnings_stance="EXIT_BEFORE"), earnings_days=3)
    assert fails == []


def test_prose_with_price_and_side_mismatch_fail():
    res, fails = _check(_obj(thesis=["buy near 97.50"], side="SHORT"))
    assert any("document side" in f for f in fails) and any("97.50 appears in no source" in w for w in res["withheld"])
    _, fails = _check(_obj(veto_fact="buy near 97.50"))
    assert any("97.50 appears in no source" in f for f in fails)
    res, fails = _check(_obj(thesis=["zone top 97.00 [L:PINE_ZONE_HIGH]"]))
    assert fails == [] and res["traces"][0]["cite"] == "L:PINE_ZONE_HIGH"


def test_cash_skip_with_plan_and_missing_atr_fail():
    _, fails = _check(_obj(verdict="CASH_SKIP"))
    assert any("CASH_SKIP with entry kind ZONE" in f for f in fails)
    assert _check(_obj(), atr=None)[1] == ["ATR14 unavailable: stop width cannot be asserted"]


def test_enter_needs_rr_at_market_with_chosen_levels():
    dw = {**DW, "close": 96.0}
    _, fails = _check(_obj(verdict="ENTER"), reg=c.registry(dw, ATR), state="ACTIONABLE")
    assert fails == []
    dw = {**DW, "close": 96.0, "Long Target": 104.0}
    _, fails = _check(_obj(verdict="ENTER", target_2=None), reg=c.registry(dw, ATR), state="ACTIONABLE")
    assert any("R:R at market" in f for f in fails)


def test_hard_veto_forces_cash_skip_without_scenarios():
    sc = [{"kind": "PULLBACK", "trigger": "PINE_ZONE_HIGH", "zone_low": "PINE_ZONE_LOW", "stop": "PINE_STOP",
           "target": "PINE_TARGET", "window_end": "2026-11-30", "confirm": ["holds MA50"]}]
    _, fails = _check(_obj(veto="HARD_FUNDAMENTAL", veto_fact="restatement", scenarios=sc))
    assert any("HARD_FUNDAMENTAL" in f for f in fails)


def test_no_object_fails_closed():
    assert _check(None)[1] == ["writer emitted no parseable JSON object"]


def test_adversary_is_pass_or_fail_only():
    assert c.adversary_verdict(" PASS ") == "PASS"
    for s in ("", "pass, but", "PASS with notes", "FAIL", None):
        assert c.adversary_verdict(s) == "FAIL"


def test_side_none_without_plan_and_forming_bar():
    assert c.choose_side({"close": 100.0, "Long Stop Loss": 101.0, "Long Target": 110.0}, "RR_SETUP") is None
    assert c.choose_side(DW, "RR_SETUP") == "LONG"
    assert c.bar_is_forming("2026-11-02", datetime(2026, 11, 2, 8, 13), (14, 30))
    assert not c.bar_is_forming("2026-11-02", datetime(2026, 11, 2, 15, 0), (14, 30))


def test_rsi2_pending_is_live_only_on_its_signal_bar():
    from src.logic.pine_levels import rsi2_order_live

    assert rsi2_order_live({"RSI2 Events Pack": 2 * 1024 + 3})
    assert not rsi2_order_live({"RSI2 Events Pack": 2 * 1024})


def test_live_quote_outside_stop_target_fails():
    assert c.live_last([("Live quote", "- Last: 89.50\n- Bid/Ask")]) == 89.5
    _, fails = _check(_obj(), live=89.5)
    assert any("live quote 89.5 outside" in f for f in fails)
    assert _check(_obj(), live=99.0)[1] == []


def test_annotate_marks_only_untraceable_dollar_prices():
    allowed = [90.0, 115.0, 3715.0]
    text = "stop $90.00, target $115, invented $132.40, revenue $3.7 billion, raised to $3,715 million"
    out, n = c.annotate(text, allowed)
    assert n == 1 and "$132.40 ⚠️" in out and "$90.00 ⚠️" not in out and "$115 ⚠️" not in out


def test_humanize_swaps_level_names_for_prices_and_tags_for_source_names():
    from src.logic.deep_research.deterministic import humanize

    reg = {"PINE_TARGET": {"price": 517.35}, "ATR_FLOOR_STOP": {"price": 437.57}, "KEY_SUPPORT_1": {"price": 483.9}}
    secs = [("Code facts and level ladder", ""), ("Fresh news", ""), ("Judge research (fetch_sec_filings {})", "")]
    out = humanize("Stop ATR_FLOOR_STOP [L:ATR_FLOOR_STOP], target PINE_TARGET [L:PINE_TARGET], KEY_SUPPORT_1 holds, "
                   "RSI2 and UNKNOWN_NAME stay. Beat [S1][S2] and flow 1.29 [DW:x] [L:PINE_TARGET].", reg, secs)
    assert out == ("Stop ATR-floor stop $437.57, target Pine target $517.35, key support 1 $483.90 holds, "
                   "RSI2 and UNKNOWN_NAME stay. Beat (Fresh news, Judge research) and flow 1.29 (Data Window).")
    assert "[" not in out



def test_render_leads_with_plain_decision_and_flags_spent_target():
    lv = {"entry_low": {"level": "PINE_ZONE_LOW", "price": 459.04, "source": "Pine"},
          "entry_high": {"level": "PINE_ZONE_HIGH", "price": 464.16, "source": "Pine"},
          "stop": {"level": "ATR_FLOOR_STOP", "price": 437.57, "source": "code"},
          "target_1": {"level": "PINE_TARGET", "price": 517.35, "source": "Pine"}}
    facts = {"bar_date": "2026-11-03", "close": 483.9, "live": 521.05, "atr": 24.0, "forming": False}
    watch = {"verdict": "CASH_SKIP", "veto": "EVENT_RISK", "veto_fact": "earnings tonight",
             "adversary_reason": "the plan ignores the post-earnings gap [S1]"}
    md = c.render("AMD", "2026-11-03", "LONG", watch, {"levels": lv, "entry_kind": "ZONE"}, ["adversarial pass: FAIL"], "FAIL",
                  facts, {"thesis": ["trend intact"]}, display=({}, [("Live quote", ""), ("Fresh news", "")]))
    head = md.split("## ⚖️ THE JUDGE'S FINAL RULING")[0]
    assert "## 🧭 DECISION: AVOID" in head
    assert "already reached target 1 ($517.35)" in head
    assert "independent reviewer rejected the plan: the plan ignores the post-earnings gap (Fresh news)" in head
    assert "Buy zone:** $459.04 – $464.16" in head and "[L:" not in md and "ATR_FLOOR_STOP" not in md
    assert md.index("## ⚖️ THE JUDGE'S FINAL RULING") < md.index("## 🔒 Audit trail")
    assert c.adversary_reason("FAIL: guidance cut [S3].") == "guidance cut [S3]."
