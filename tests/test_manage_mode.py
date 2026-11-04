"""Manage mode: a held name gets a code-validated HOLD/TRIM/EXIT against protective levels, not entry geometry."""
import json
from datetime import date, datetime

from src.logic.deep_research import deterministic as c

# No Pine plan (no Long Entry Zone / Stop / Target): a held name still gets the LONG side in manage mode.
DW = {"close": 100.0, "MA 50 Mid": 92.0, "VP VAH": 115.0, "VP VAL": 90.0, "bar_date": "2026-11-02"}
ATR = 2.0
TODAY = date(2026, 11, 2)
SECTIONS = [("Code facts", "ATR14 2.00\nNext earnings 2026-12-16"),
            ("Fundamentals", "Q3 revenue $1,234.5M, up 12.4% year over year")]

POS = {"has_position": True, "source": "SCHWAB", "ticker": "AMZN", "side": "LONG", "quantity": 133,
       "average_price": 200.0, "market_value": 33250.0, "day_pnl": 2439.0,
       "accounts": [{"account": "brk-1", "quantity": 133, "average_price": 200.0}],
       "options": [{"symbol": "AMZN 260117C250", "quantity": 2, "market_value": 500.0}]}
RAW_BLOCK = ("--- USER ACTIVE BROKER POSITION (SCHWAB) ---\n"
             "• Status: ACTIVE HOLDING (LONG 133 units / EQUITY)\n"
             "• Cost Basis: $200.00 | Market Value: $33,250.00\n"
             "• Unrealized / Day P/L: $+2,439.00 (+0.00%)\n"
             "• MANDATORY SENIOR PM DIRECTIVE: treat this as held")


def _mobj(**kw):
    o = {"side": "LONG", "verdict": "HOLD", "veto": "NONE", "veto_fact": "",
         "entry": {"kind": "NONE"}, "stop": "MA50", "target_1": "VP_VAH", "target_2": None,
         "trim": "NONE", "scenarios": [], "earnings_stance": "NONE", "earnings_rationale": "",
         "thesis": ["holds the base"], "case_against": ["distribution above"]}
    o.update(kw)
    return o


def _check(obj, **kw):
    args = dict(side="LONG", reg=c.registry(DW, ATR), atr=ATR, em_pct=10.0, forming=False, state="NO_TRADE",
                rsi2_ready=False, earnings_days=None, earnings_date=None, block_days=7, today=TODAY,
                sections=SECTIONS, dw=DW, manage=True, has_entry_plan=False)
    args.update(kw)
    return c.check(obj, **args)


# --- side + verdict gate -------------------------------------------------------

def test_held_name_gets_long_side_without_a_pine_plan():
    assert c.choose_side(DW, "RR_SETUP") is None
    assert c.choose_side(DW, "RR_SETUP", held=True) == "LONG"


def test_hold_verdict_passes_manage_checks():
    res, fails = _check(_mobj())
    assert fails == []
    assert res["verdict"] == "HOLD"
    assert res["levels"]["stop"]["price"] == 92.0 and res["levels"]["target_1"]["price"] == 115.0


def test_manage_verdict_on_a_name_not_held_fails():
    _, fails = _check(_mobj(), manage=False)
    assert any("not in" in f for f in fails)


def test_hold_with_stop_at_or_above_close_fails():
    _, fails = _check(_mobj(stop="VP_VAH", target_1="MA50"))
    assert any("at or above close" in f for f in fails)


def test_manage_stop_tighter_than_atr_floor_fails():
    _, fails = _check(_mobj(stop="CLOSE"))
    assert any("at or above close" in f or "ATR" in f for f in fails)


# --- trim / exit pairing --------------------------------------------------------

def test_trim_verdict_requires_a_partial_trim_amount():
    _, fails = _check(_mobj(verdict="TRIM"))
    assert any("without a trim amount" in f for f in fails)
    _, fails = _check(_mobj(verdict="TRIM", trim="ALL"))
    assert any("ALL is an EXIT" in f for f in fails)
    res, fails = _check(_mobj(verdict="TRIM", trim="THIRD"))
    assert fails == [] and res["trim"] == "THIRD"


def test_hold_with_a_trim_amount_fails():
    _, fails = _check(_mobj(trim="HALF"))
    assert any("HOLD with trim" in f for f in fails)


def test_exit_needs_a_cited_reason():
    _, fails = _check(_mobj(verdict="EXIT", trim="ALL"))
    assert any("without a cited reason" in f for f in fails)
    _, fails = _check(_mobj(verdict="EXIT", trim="ALL", wrong_if="accounting restatement filed [S0]"))
    assert fails == []


def test_manage_verdict_with_an_entry_fails():
    _, fails = _check(_mobj(entry={"kind": "ZONE", "low": "VP_VAL", "high": "CLOSE"}))
    assert any("no new entry" in f for f in fails)


# --- decision label -------------------------------------------------------------

_LV = {"stop": {"level": "MA50", "price": 92.0, "source": "MA 50 Mid"},
       "target_1": {"level": "VP_VAH", "price": 115.0, "source": "VP VAH"}}


def _dec(watch=None, res=None, facts=None):
    return c.decision_label(watch or {"kind": "MANAGE"}, res or {"levels": _LV}, facts or {})


def test_held_label_maps_manage_verdicts():
    assert _dec(res={"levels": _LV, "verdict": "HOLD"}, facts={"live": 105.0})["code"] == "HOLD"
    assert _dec(res={"levels": _LV, "verdict": "EXIT"}, facts={"live": 105.0})["code"] == "EXIT"
    dec = _dec(res={"levels": _LV, "verdict": "TRIM", "trim": "HALF"}, facts={"live": 105.0})
    assert dec["code"] == "TRIM" and "half" in dec["why"][0].lower()


def test_held_stop_hit_is_sell():
    assert _dec(res={"levels": _LV, "verdict": "HOLD"}, facts={"live": 91.0})["code"] == "SELL"
    assert _dec(res={"levels": _LV, "verdict": "HOLD"}, facts={"live": 116.0})["code"] == "SELL"


def test_contract_fail_on_held_is_hold_plan_rejected():
    dec = _dec(watch={"kind": "MANAGE", "contract_failures": ["verdict 'SELL' not in"]},
               res={"levels": _LV}, facts={"live": 105.0})
    assert dec["code"] == "HOLD" and dec["label"] == "HOLD (plan rejected)"
    assert "not in" in dec["why"][0]


# --- structured position rendering ----------------------------------------------

def test_unrealized_pnl_is_price_minus_basis_times_qty():
    md = "\n".join(c._position_md({"close": 250.0, "position_obj": POS}))
    assert "133" in md and "$200.00" in md
    assert "$+6,650.00" in md and "+25.0%" in md
    assert "Day P/L" in md and "+2,439.00" in md
    assert "AMZN 260117C250" in md


def test_negative_day_pnl_is_shown():
    md = "\n".join(c._position_md({"close": 250.0, "position_obj": {**POS, "day_pnl": -812.0}}))
    assert "$-812.00" in md


def test_report_never_contains_the_raw_prompt_directive():
    facts = {"bar_date": "2026-11-02", "close": 250.0, "position": RAW_BLOCK, "position_obj": POS}
    md = c.render("AMZN", "2026-11-02", None, {}, {"levels": {}}, ["no document"], "FAIL", facts, None)
    assert "MANDATORY SENIOR PM DIRECTIVE" not in md
    assert "## Your position" in md and "Unrealized P/L" in md
    assert "NO TRADE" in md


def test_report_json_exports_the_structured_position():
    rep = c.build_report_obj("AMZN", "2026-11-02", "LONG", {"kind": "MANAGE", "verdict": "HOLD"},
                             {"levels": _LV, "verdict": "HOLD"}, [], "PASS",
                             {"close": 100.0, "bar_date": "2026-11-02", "position_obj": POS}, {}, {}, [])
    assert rep["position"]["quantity"] == 133 and rep["decision"]["code"] == "HOLD"
    rep = c.build_report_obj("AMZN", "2026-11-02", "LONG", {"verdict": "ARMED"},
                             {"levels": _LV, "verdict": "ARMED"}, [], "PASS",
                             {"close": 100.0, "bar_date": "2026-11-02", "position": "POSITION: NONE"}, {}, {}, [])
    assert rep["position"] is None


# --- suggestion ledger -----------------------------------------------------------

def test_manage_verdicts_stay_out_of_the_suggestion_ledger(tmp_path, monkeypatch):
    """A HOLD/TRIM/EXIT manages a position, it never fills — so it must not pad the suggestion stats."""
    calls = []
    monkeypatch.setattr("src.tracking.suggestions_ledger.append_suggestion",
                        lambda p: calls.append(p) or len(calls))
    monkeypatch.setattr(c, "_writer", lambda *a, **k: json.dumps(_mobj()))
    monkeypatch.setattr(c, "_llm", lambda *a, **k: "PASS")
    monkeypatch.setattr("src.logic.options_menu.attach_options_menu", lambda *a, **k: None)
    monkeypatch.setattr("src.logic.scenario_plans.cancel_unfilled", lambda *a, **k: None)
    monkeypatch.setattr("src.logic.order_ticket.void_ticket", lambda *a, **k: None)
    monkeypatch.setattr("src.clients.earnings_client.resolve_next_earnings_date", lambda t: (None, None))
    watch = c._run_contract("AMZN", "2026-11-02", {**DW, "RSI2 ATR14": ATR}, side="LONG",
                            setup_lane="RR_SETUP", kind="MANAGE",
                            captured_at=datetime(2026, 11, 3, 9, 0), inputs_md="", sources=[],
                            position_block="", tdir=tmp_path, raw_dir=tmp_path / "raw",
                            reports_dir=tmp_path / "reports", position=POS)
    assert watch["verdict"] == "HOLD" and watch["suggestion_id"] is None
    assert calls == []
