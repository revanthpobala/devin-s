"""
ENTRY-predicate parity tests.

SPEC DEVIATION, stated up front: the spec asks for parity between the ENTRY predicate and
`forward_log.ZONE_RR_PB` over a `full_v6` fixture. Neither exists in this repo:

  * `forward_log.py` does not exist. The only mention of it is a doc note at
    `gems/revanth-bible.md:1813` stating it "does not track these structures at all yet".
  * `ZONE_RR_PB` appears nowhere in the tree.
  * there is no `full_v6` fixture. `tests/fixtures/` holds a single file (META_2026-08-14.json).

Parity is therefore asserted against the PB funnel definition that does exist and is
authoritative: `src.logic.data_window_filter.parse_data_window` (Signal Pack bit 5 / mask 32,
decoded tri-state so an absent column stays None) plus the PB-split priors in `LANE_PRIORS_PB`.
The ENTRY predicate must agree with that decode on every bit combination, and must never promote
a bar whose packs are missing.

The corpus sweep over on-disk Data Windows is a drift detector, not a proving ground: on the
current corpus exactly one bar carries the PB bit and none is also in-zone, so its assertion is
0 == 0. The exhaustive bit-matrix below is what actually has teeth.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from src.logic.data_window_filter import LANE_PRIORS_PB, decode_action, parse_data_window
from src.tracking.entry_risk_alerts import (
    FADE_SET_CODES,
    STOP_ATR_MIN,
    Onset,
    decode_signal_pack,
    decode_zone_rr_flags,
    entry_body,
    fire_entry,
    load_onsets_for_date,
    onset_from_datawindow,
)

REPO = Path(__file__).resolve().parent.parent


# --- pack decoding parity with the canonical filter ---------------------------------------------

def _canonical(raw: dict) -> dict:
    return parse_data_window(raw)


@pytest.mark.parametrize("pack", list(range(64)))
def test_signal_pack_decode_matches_canonical_filter(pack):
    """Bit 5 / mask 32 is PB; bit 2 is the INVERTED fade bit. Both must agree exactly."""
    canon = _canonical({"Signal Pack": pack})
    ours = decode_signal_pack(pack)

    assert bool(canon["pb_funnel"] == 1.0) == bool(ours["pb_funnel"])
    assert bool(canon["fade_long"] == 1.0) == bool(ours["fade_long"])
    assert bool(canon["strong_buy"] == 1.0) == bool(ours["strong_buy"])
    assert bool(canon["strong_sell"] == 1.0) == bool(ours["strong_sell"])
    assert bool(canon["is_topping"] == 1.0) == bool(ours["is_topping"])
    assert bool(canon["is_bottoming"] == 1.0) == bool(ours["is_bottoming"])


@pytest.mark.parametrize("pack", list(range(16)))
def test_zone_rr_flags_decode_matches_canonical_filter(pack):
    canon = _canonical({"Zone RR Flags Pack": pack})
    ours = decode_zone_rr_flags(pack)
    assert bool(canon["long_in_zone"] == 1.0) == ours["long_in_zone"]
    assert bool(canon["short_in_zone"] == 1.0) == ours["short_in_zone"]
    assert bool(canon["long_rr_valid"] == 1.0) == ours["long_rr_valid"]
    assert bool(canon["short_rr_valid"] == 1.0) == ours["short_rr_valid"]


def test_absent_packs_stay_unmeasured_not_false():
    """Treating a missing column as 'no fade' / 'no PB' would promote the bars meant to exclude."""
    canon = _canonical({})
    assert canon["pb_funnel"] is None
    assert canon["fade_long"] is None

    ours = decode_signal_pack(None)
    assert ours["present"] is False
    assert ours["pb_funnel"] is None
    assert ours["fade_long"] is None
    assert decode_zone_rr_flags(None)["present"] is False


# --- the ENTRY gate: exhaustive bit matrix ------------------------------------------------------

def _onset(**kw):
    base = dict(
        ticker="TEST", date="2026-09-30", close=100.0, stop=95.0, target=115.0, atr=5.0,
        long_in_zone=True, long_rr_valid=True, fade_long=False, pb_funnel=True,
        packs_present=True,
    )
    base.update(kw)
    return Onset(**base)


# (long_in_zone, long_rr_valid, fade_off, pb, rr >= 2) -> should qualify
GATE_CASES = []
for in_zone, rr_valid, fade, pb in itertools.product([True, False], repeat=4):
    GATE_CASES.append((in_zone, rr_valid, fade, pb))


@pytest.mark.parametrize("in_zone,rr_valid,fade,pb", GATE_CASES)
def test_entry_gate_is_exactly_the_four_declared_conditions(in_zone, rr_valid, fade, pb):
    o = _onset(long_in_zone=in_zone, long_rr_valid=rr_valid, fade_long=fade, pb_funnel=pb)
    qualifies, fails = o.entry_gate()
    expected = in_zone and rr_valid and (fade is False) and pb and o.rr_ok
    assert qualifies is expected, fails
    # Every rejection must name itself; a silent skip is indistinguishable from a crash.
    if not qualifies:
        assert fails


@pytest.mark.parametrize("in_zone,rr_valid,fade,pb", GATE_CASES)
def test_every_rejection_is_a_declared_reason(in_zone, rr_valid, fade, pb):
    """No silent disqualifiers: every failing reason maps to one of the four gate conditions."""
    allowed = (
        "not in the long zone",
        "long R:R not marked valid",
        "RR@mkt",
        "fade gate active",
        "not PB funnel",
    )
    o = _onset(long_in_zone=in_zone, long_rr_valid=rr_valid, fade_long=fade, pb_funnel=pb)
    _qualifies, fails = o.entry_gate()
    for f in fails:
        assert any(f.startswith(a) for a in allowed), f


def test_unmeasured_bar_never_qualifies():
    o = _onset(packs_present=False, fade_long=None, pb_funnel=None)
    qualifies, fails = o.entry_gate()
    assert qualifies is False
    assert "unmeasured" in " ".join(fails)


def test_unknown_fade_state_is_not_fade_off():
    """fade_long=None must block, not pass through."""
    o = _onset(fade_long=None)
    assert o.fade_off is False
    assert o.entry_gate()[0] is False


def test_rr_below_two_never_qualifies():
    o = _onset(close=100.0, stop=95.0, target=108.0)     # RR = 8/5 = 1.6
    assert o.rr_at_market == pytest.approx(1.6)
    assert o.entry_gate()[0] is False


def test_rr_exactly_two_qualifies():
    o = _onset(close=100.0, stop=95.0, target=110.0)     # RR = 2.0
    assert o.rr_at_market == pytest.approx(2.0)
    assert o.rr_ok is True


def test_rr_is_not_computed_when_price_is_at_or_below_stop():
    o = _onset(close=94.0, stop=95.0, target=115.0)
    assert o.rr_at_market is None
    assert o.risk_pct is None
    assert o.entry_gate()[0] is False


# --- lane assignment and priors -------------------------------------------------------------------

def test_lane_is_assigned_by_rr_tier():
    assert _onset(target=112.0).lane == "rr_at_market_lane"          # RR 2.4
    assert _onset(target=115.0).lane == "rr_at_market_lane_strong"   # RR 3.0
    assert _onset(target=125.0).lane == "rr_at_market_lane_strong"   # RR 5.0
    assert _onset(target=108.0).lane is None                         # RR 1.6


def test_pb_split_prior_is_attached():
    o = _onset(target=125.0, pb_funnel=True)
    assert (o.lane_prior_win, o.lane_prior_ev) == LANE_PRIORS_PB[("rr_at_market_lane_strong", True)]

    no_pb = _onset(target=125.0, pb_funnel=False)
    assert (no_pb.lane_prior_win, no_pb.lane_prior_ev) == LANE_PRIORS_PB[
        ("rr_at_market_lane_strong", False)
    ]


def test_the_four_pb_prior_cohorts_all_exist():
    """The push body quotes these by name; a missing cohort would render a blank line."""
    from src.tracking.entry_risk_alerts import LANE_PRIOR_TEXT
    for key in LANE_PRIOR_TEXT:
        assert key in LANE_PRIORS_PB, f"quoted prior for {key} has no measurement behind it"


# --- stop width ----------------------------------------------------------------------------------

def test_stop_inside_noise_is_flagged():
    """Corpus median stop is 0.69 ATR; a 0.2-ATR stop produces a huge R:R and loses money."""
    tight = _onset(close=100.0, stop=99.8, target=120.0, atr=1.0)
    assert tight.stop_width_atr == pytest.approx(0.2)
    assert tight.stop_tight is True
    assert tight.rr_at_market > 50
    assert "inside noise" in entry_body(tight)

    sane = _onset(close=100.0, stop=96.5, target=120.0, atr=5.0)
    assert sane.stop_width_atr == pytest.approx(0.7)
    assert sane.stop_tight is False
    assert "inside noise" not in entry_body(sane)


def test_stop_tight_flag_does_not_silently_allow_the_push():
    """Under B1/B3 gate, stop width < 0.7 ATR strictly fails entry_gate."""
    o = _onset(close=100.0, stop=99.8, target=120.0, atr=1.0)
    qualifies, reasons = o.entry_gate()
    assert qualifies is False
    assert any("stop width" in r for r in reasons)


def test_missing_atr_leaves_stop_width_unknown_rather_than_guessing():
    o = _onset(atr=None)
    assert o.stop_width_atr is None
    assert o.stop_tight is False


# --- next-open rule -------------------------------------------------------------------------------

def test_open_at_or_below_stop_is_skipped():
    o = _onset(close=100.0, stop=95.0)
    ok, note = o.next_open_verdict(94.0)
    assert ok is False
    assert "SKIP" in note and "stop" in note
    ok2, _ = o.next_open_verdict(95.0)
    assert ok2 is False, "at the stop is still a skip"


def test_open_at_or_above_target_is_skipped():
    o = _onset(close=100.0, stop=95.0, target=115.0)
    ok, note = o.next_open_verdict(115.0)
    assert ok is False
    assert "target" in note


def test_open_inside_the_levels_trades():
    o = _onset(close=100.0, stop=95.0, target=115.0)
    ok, note = o.next_open_verdict(102.0)
    assert ok is True
    assert "tradeable" in note


def test_oversized_risk_is_skipped():
    """Risk cap is 5% for stocks, 3% for ETFs."""
    stock = _onset(ticker="XYZ", close=100.0, stop=94.0, target=120.0)   # 6%
    assert stock.risk_pct == pytest.approx(6.0)
    assert stock.risk_cap_pct == 5.0
    ok, note = stock.next_open_verdict(100.0)
    assert ok is False
    assert "6.0%" in note and "5%" in note

    etf = _onset(ticker="QQQ", close=100.0, stop=96.5, target=120.0)     # 3.5%
    assert etf.risk_cap_pct == 3.0
    assert etf.next_open_verdict(100.0)[0] is False


def test_unknown_open_does_not_block():
    """No open quote yet is not a reason to swallow an alert."""
    o = _onset()
    ok, note = o.next_open_verdict(None)
    assert ok is True
    assert "confirm at the bell" in note


# --- push vs digest routing ------------------------------------------------------------------------

def test_pb_onset_pushes_and_non_pb_onset_goes_to_the_digest(monkeypatch, tmp_path):
    import src.tracking.notify as notify_mod

    sent = []
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(notify_mod, "_mountain_now",
                        lambda: __import__("datetime").datetime(2026, 9, 28, 9, 30))
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append((tier, title)) or ["x"]))

    onsets = [
        _onset(ticker="PBGOOD", pb_funnel=True),
        _onset(ticker="NOPB", pb_funnel=False),
        _onset(ticker="FADED", pb_funnel=True, fade_long=True),
        _onset(ticker="NOTINZONE", pb_funnel=True, long_in_zone=False),
    ]
    out = fire_entry(onsets, "2026-09-28")

    assert out["pushed"] == ["PBGOOD"]
    assert [t for t, _ in sent] == ["ENTRY"]
    # The non-PB onset is a real onset and must survive to the digest, not be discarded.
    dig = {d["ticker"]: d for d in out["digest_candidates"]}
    assert "NOPB" in dig
    assert dig["NOPB"]["pb"] is False
    assert "PBGOOD" not in dig, "a pushed onset does not need to be in the digest"


def test_hi_rr_onset_is_tagged(monkeypatch, tmp_path):
    import src.tracking.notify as notify_mod

    sent = []
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(notify_mod, "_mountain_now",
                        lambda: __import__("datetime").datetime(2026, 9, 28, 9, 30))
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append((tier, title)) or ["x"]))

    fire_entry([_onset(ticker="HIRR", target=130.0)], "2026-09-28")   # RR 6.0
    assert len(sent) == 1
    assert "HI-RR" in sent[0][1]


def test_repeat_sweep_pushes_only_once(monkeypatch, tmp_path):
    import src.tracking.notify as notify_mod

    sent = []
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(notify_mod, "_mountain_now",
                        lambda: __import__("datetime").datetime(2026, 9, 28, 9, 30))
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append((tier, title)) or ["x"]))

    o = _onset(ticker="GLW")
    fire_entry([o], "2026-09-28")
    fire_entry([o], "2026-09-28")
    fire_entry([o], "2026-09-29")     # a new session is a new alert
    assert len(sent) == 2


# --- body content ----------------------------------------------------------------------------------

def test_body_carries_the_prior_and_the_base_rate_line():
    body = entry_body(_onset(ticker="GLW", target=125.0))
    assert "GLW" in body
    assert "PB R:R >=3" in body
    assert "1 time in 4" in body, "26% win rate should read as roughly 1 in 4"
    assert "fixed-fractional" in body
    assert "Next open" in body


def test_body_quotes_the_measured_stop_width():
    body = entry_body(_onset(atr=5.0))
    assert "1.00 ATR" in body


# --- never-push list ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", FADE_SET_CODES)
def test_fade_set_codes_are_the_extended_family(code):
    assert decode_action(code) in ("EXTENDED", "STRETCHED", "BLOW-OFF/CAPITULATION")


def test_corpus_sweep_covers_only_pine_exported_bars():
    """Corpus sweep. A drift detector, not a proving ground -- see the module docstring.

    Pre-August snapshots carry the old 58-column schema (capitalised `Close`, no packs, no stop or
    target). Those bars cannot express the gate, so they are skipped rather than coerced into a
    verdict -- and skipping them must be visible, not silent.
    """
    root = REPO / "data" / "triage"
    if not root.exists():
        pytest.skip("no triage corpus on disk")
    dates = sorted(d.name for d in root.glob("202*") if d.is_dir())
    assert dates, "expected at least one dated triage folder"

    total_files = sum(len(list((root / d).glob("**/*_datawindow.json"))) for d in dates)
    total = pb_in_zone = pb_in_zone_rr2 = eligible = skipped = 0
    for d in dates:
        for o in load_onsets_for_date(d):
            total += 1
            if not o.packs_present:
                skipped += 1
            else:
                eligible += 1
            if o.is_pb and o.in_zone:
                pb_in_zone += 1
                if o.rr_ok:
                    pb_in_zone_rr2 += 1

    assert skipped + eligible == total
    assert eligible > 0, "no Pine-exported bar found; the corpus would be silently untested"
    assert total < total_files, (
        "expected some legacy-schema snapshots to be skipped; if none are, the "
        "has-close filter stopped discriminating and old bars may be leaking through"
    )
    # The predicate must count exactly the bars that are PB AND in-zone AND RR>=2.
    assert pb_in_zone_rr2 <= pb_in_zone <= total
    print(f"\ncorpus: {total_files} files -> {total} decoded "
          f"({eligible} Pine-exported, {skipped} legacy-schema skipped); "
          f"{pb_in_zone} PB+in-zone, {pb_in_zone_rr2} PB+in-zone+RR>=2")


def test_legacy_schema_bars_are_skipped_not_coerced():
    """A July-era snapshot has no packs and no stop. It must not become an entry."""
    legacy = {"Close": 344.08, "ticker": "ABBV", "RSI2 RSI2": 30.0}
    assert "close" not in legacy, "precondition: the legacy schema really does lack 'close'"
    o = onset_from_datawindow("ABBV", legacy, "2026-07-17")
    assert o.packs_present is False
    assert o.close is None
    assert o.rr_at_market is None
    qualifies, fails = o.entry_gate()
    assert qualifies is False
    assert "unmeasured" in " ".join(fails)


def test_onset_from_datawindow_reads_the_canonical_columns():
    dw = {
        "ticker": "META",
        "close": 344.08,
        "stop_loss": 316.55,
        "target_1": 354.37,
        "atr14": 8.93,
        "Zone RR Flags Pack": 5,      # bit0 long in zone, bit2 long RR valid
        "Signal Pack": 4,             # bit2 NOT-fade -> fade OFF
        "Ext Z Self Relative": -0.29,
        "Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz": 6,
        "Exp Move Pct 21b": 7.12,
        "Action Long Code": 8,
    }
    o = onset_from_datawindow("META", dw, "2026-09-30")
    assert o.ticker == "META"
    assert o.in_zone is True
    assert o.long_rr_valid is True
    assert o.fade_off is True
    assert o.is_pb is False            # bit 5 not set on this real bar
    assert o.rr_at_market == pytest.approx((354.37 - 344.08) / (344.08 - 316.55), abs=0.01)
    assert o.stop_width_atr == pytest.approx(27.53 / 8.93, abs=0.01)
    assert o.action_code == 8
    assert o.regime == 6
    # Real META bar: PB absent, so it is a digest candidate, never a push.
    assert o.entry_gate()[0] is False


def test_labelled_column_names_survive_a_case_insensitive_snapshot():
    """The Pine exports the pretty labels; the filter binds them by name."""
    dw = {"Signal Pack": 32, "Zone RR Flags Pack": 1, "close": 100.0,
          "stop_loss": 95.0, "target_1": 130.0, "atr14": 5.0}
    o = onset_from_datawindow("T", dw, "2026-09-30")
    assert o.is_pb is True
    assert o.in_zone is True
    assert o.long_rr_valid is False
    assert o.entry_gate()[0] is False, "RR not marked valid -> digest, not push"


# =====================================================================================
# RISK tier
# =====================================================================================

from src.tracking.entry_risk_alerts import (  # noqa: E402
    EXT_Z_TRIM,
    evaluate_risk,
    fire_risk,
)


def test_close_below_stop_exits_at_open():
    ev = evaluate_risk("GLW", planned_stop=95.0, today_close=94.0, prev_close=99.0,
                       date="2026-09-30")
    assert len(ev) == 1
    assert ev[0].kind == "STOP_CLOSE"
    assert "EXIT at open" in ev[0].message
    assert "$94.00" in ev[0].message and "$95.00" in ev[0].message


def test_gap_through_the_stop_is_a_different_event():
    ev = evaluate_risk("GLW", planned_stop=95.0, today_close=92.0, prev_close=94.0,
                       date="2026-09-30")
    assert len(ev) == 1
    assert ev[0].kind == "GAP_THROUGH_STOP"
    assert "gapped through stop, exit" in ev[0].message
    assert "EXIT at open" not in ev[0].message, "a gap leaves no decision to make at the open"


def test_intraday_touch_does_not_fire_but_close_does():
    """Decisions are made at the close; firing on a touch turns every shakeout into an exit."""
    touched_not_closed = evaluate_risk("GLW", planned_stop=95.0, today_close=98.0,
                                       prev_close=99.0, date="2026-09-30")
    assert touched_not_closed == []

    closed = evaluate_risk("GLW", planned_stop=95.0, today_close=94.5, prev_close=99.0,
                           date="2026-09-30")
    assert len(closed) == 1


def test_stop_breach_suppresses_the_extension_trim():
    """Once the stop is gone, 'trim or sell a covered call' is advice about a position you no
    longer have."""
    ev = evaluate_risk("GLW", planned_stop=95.0, today_close=90.0, prev_close=99.0,
                       ext_z=2.4, action_code=11, date="2026-09-30")
    assert len(ev) == 1
    assert ev[0].kind == "STOP_CLOSE"


@pytest.mark.parametrize("code", FADE_SET_CODES)
def test_fade_set_entry_triggers_a_trim(code):
    ev = evaluate_risk("GLW", planned_stop=90.0, today_close=100.0, action_code=code,
                       date="2026-09-30")
    assert [e.kind for e in ev] == ["TRIM_EXTENSION"]
    assert "trim or sell a covered call" in ev[0].message
    assert ev[0].detail["link"] == "INCOME"


def test_ext_z_at_or_above_two_triggers_a_trim():
    assert evaluate_risk("GLW", 90.0, 100.0, ext_z=EXT_Z_TRIM, date="d") != []
    assert evaluate_risk("GLW", 90.0, 100.0, ext_z=EXT_Z_TRIM - 0.01, date="d") == []


def test_a_clean_bar_produces_nothing():
    assert evaluate_risk("GLW", 90.0, 100.0, ext_z=0.2, action_code=1, date="d") == []


def test_unheld_names_produce_no_risk_events():
    assert evaluate_risk("GLW", 90.0, 80.0, date="d", held=False) == []


def test_missing_stop_or_close_produces_no_stop_event():
    """Without both there is no stop decision to make; do not invent one."""
    assert evaluate_risk("GLW", None, 80.0, date="d") == []
    assert evaluate_risk("GLW", 95.0, None, date="d") == []


def test_risk_dedupe_key_is_tier_ticker_date():
    ev = evaluate_risk("GLW", 95.0, 94.0, prev_close=99.0, date="2026-09-30")
    assert ev[0].dedupe_key == "RISK:STOP_CLOSE:GLW:2026-09-30"
    ev2 = evaluate_risk("GLW", 95.0, 100.0, ext_z=2.5, date="2026-09-30")
    assert ev2[0].dedupe_key == "RISK:TRIM_EXTENSION:GLW:2026-09-30"


def test_fire_risk_pushes_each_event_once(monkeypatch, tmp_path):
    import src.tracking.notify as notify_mod

    sent = []
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(notify_mod, "_mountain_now",
                        lambda: __import__("datetime").datetime(2026, 9, 28, 9, 30))
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append((tier, title)) or ["x"]))

    events = evaluate_risk("GLW", 95.0, 94.0, prev_close=99.0, date="2026-09-28")
    fire_risk(events)
    fire_risk(events)          # same session, same state
    assert len(sent) == 1
    assert sent[0][0] == "RISK"

# =====================================================================================
# Stop width in the RISK alert body
# =====================================================================================

def _risk_capture(monkeypatch, tmp_path):
    """Capture the bodies a fire_risk() call would push."""
    import src.tracking.notify as nm

    sent = []
    monkeypatch.setattr(nm, "_DEDUPE_DB", None)
    monkeypatch.setattr(nm, "_dedupe_db_path", lambda: tmp_path / "d.db")
    monkeypatch.setattr(nm, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(nm, "_smtp_configured", lambda: False)
    monkeypatch.setattr(nm.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(nm.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(nm, "_mountain_now",
                        lambda: __import__("datetime").datetime(2026, 9, 28, 9, 30))
    monkeypatch.setattr(nm, "_dispatch",
                        lambda tier, title, body: (sent.append(body) or ["x"]))
    return sent


def test_risk_event_carries_the_stop_width(monkeypatch, tmp_path):
    """A stop-out alert that does not say how wide the stop was cannot be acted on.

    A 0.2-ATR stop was always going to be hit; that is the difference between a real break and a
    stop that sat inside the noise band all along.
    """
    sent = _risk_capture(monkeypatch, tmp_path)
    # close 99.80, stop 100.00, atr 1.0 -> a 0.20 ATR stop, deep inside noise.
    fire_risk(evaluate_risk("SWKS", 100.0, 99.80, prev_close=100.5, date="2026-09-28", atr=1.0))
    body = sent[0]
    assert "0.20 ATR" in body
    assert "inside noise" in body
    assert "65% of stops are hit" in body


def test_risk_body_flags_a_wide_stop_as_unremarkable(monkeypatch, tmp_path):
    sent = _risk_capture(monkeypatch, tmp_path)
    # close 90.00 vs stop 95.00 on a 5.0 ATR name -> a 1.00 ATR stop, at the corpus median.
    fire_risk(evaluate_risk("GLW", 95.0, 90.0, prev_close=99.0, date="2026-09-28", atr=5.0))
    body = sent[0]
    assert "1.00 ATR" in body
    assert "inside noise" not in body, "a stop at the median is not an artifact"


def test_risk_body_says_unknown_rather_than_guessing(monkeypatch, tmp_path):
    sent = _risk_capture(monkeypatch, tmp_path)
    fire_risk(evaluate_risk("GLW", 95.0, 94.0, prev_close=99.0, date="2026-09-28", atr=None))
    assert "stop width: unknown" in sent[0]


def test_risk_body_says_unknown_when_there_is_no_atr_column(monkeypatch, tmp_path):
    sent = _risk_capture(monkeypatch, tmp_path)
    fire_risk(evaluate_risk("GLW", 95.0, 94.0, prev_close=99.0, date="2026-09-28", atr=0.0))
    assert "stop width: unknown" in sent[0]


def test_stop_width_never_changes_the_decision():
    """It is an explanation, not an input."""
    tight = evaluate_risk("T", 100.0, 99.8, prev_close=100.5, date="d", atr=1.0)
    wide = evaluate_risk("T", 100.0, 99.8, prev_close=100.5, date="d", atr=50.0)
    assert [e.kind for e in tight] == [e.kind for e in wide] == ["STOP_CLOSE"]
    assert tight[0].stop_width_atr != wide[0].stop_width_atr


def test_trim_event_also_carries_stop_width(monkeypatch, tmp_path):
    sent = _risk_capture(monkeypatch, tmp_path)
    fire_risk(evaluate_risk("GLW", 95.0, 100.0, ext_z=2.5, date="2026-09-28", atr=1.0))
    body = sent[0]
    assert "5.00 ATR" in body
    assert "See the INCOME tier" in body
