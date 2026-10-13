"""
tests/test_googl_prior_plan.py

The GOOGL regression (plan/no-lost-movers follow-up). The stored plan carried a
breakout trigger whose target sat AT it, the old gate zeroed the trigger, and the
desk then answered "no breakout" from the spot price alone. The rules under test:

  1. A broken trigger is never zeroed: it stays armed, its T1/T2 are rebuilt above
     it (next resistance or an ATR multiple), and the plan is tagged targets_rebuilt.
  2. The armed set includes the BREAKOUT.
  3. A cross of the trigger on ANY intraday poll fires exactly one alert — after
     close or not — stating RVOL, R:R at market to the rebuilt T1/T2, and the lane's
     MEASURED / UNMEASURED tag. A close back under the level re-arms it.
  4. On the cross the name is re-scored at market: RUNNING_ACTIONABLE or
     RUNNING_NO_ROOM — never a bare STALK.
  5. The prior plan outcome block states, per level: crossed or not, when, held on
     close — then the judgment at the live price.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from src.clients.quote_router import QuoteData
from src.tracking import level_engine, watch_manager
import run_watch_alerts
from run_watch_alerts import evaluate_watch_cycle
from src.tracking.plan_outcome import build_prior_plan_outcome, judge_at_market, plan_outcome_markdown

GOOGL_PLAN = {
    "ticker": "GOOGL",
    "date": "2026-10-12",
    "verdict": "STALK",
    "conviction": 5,
    "actionable": True,
    "side": "LONG",
    "shares_plan": {
        "entry_type": "LIMIT",
        "side": "LONG",
        "entry_zone_low": 349.0,
        "entry_zone_high": 351.0,
        # The plan's own trigger is 356.83 — the arbitration's binding directive
        # ("Buy Stop at $356.83, Daily Close > $356.83 with RVOL > 1.0"). The stored
        # row once carried 355.0, which is the options PUT WALL, not the breakout.
        "breakout_level": 356.83,
        "breakout_stop": 353.22,
        "tactical_stop": 344.0,
        "target_1": 357.3803,
        "target_2": 363.971,
        "targets_rebuilt": True,
    },
    "options_plan": {"structure": "NONE", "summary": "None"},
    "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 344.0, "rationale": "Floor"},
    "status": "STALKING",
    "signal_pack": 36,
    "fade": 0.0,
    "action_long": 20,
    "ext_z": 0.0,
    "atr_at_signal": 8.2,
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    test_db = tmp_path / "googl.db"
    monkeypatch.setattr(watch_manager, "DB_PATH", test_db)
    watch_manager.init_watch_db()
    level_engine.reset_engine_state()
    monkeypatch.setattr("src.config.BASE_DIR", tmp_path)
    # No market data reachable from the test: quiet surveillance channel, no bars, no
    # intraday candles — each test injects exactly what it needs.
    monkeypatch.setattr(
        "src.clients.quote_router.quote_router.get_watchlist_quotes_batch", lambda syms: {}
    )
    monkeypatch.setattr(run_watch_alerts, "_RULE_LABELS_PATH", tmp_path / "no_rule_labels.json")
    monkeypatch.setattr(run_watch_alerts, "_rule_labels_cache", {"ts": 0.0, "data": None})
    yield
    level_engine.reset_engine_state()


def _quote(price: float, volume: int = 0) -> QuoteData:
    return QuoteData(symbol="GOOGL", last_price=price, volume=volume, source="TEST")


def _mock_et():
    return datetime(2026, 10, 13, 12, 0)  # midday: no after-close gate applies


def _alert_rows(ticker: str = "GOOGL") -> list:
    with watch_manager._db_lock, watch_manager._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT trigger_type, message, spot_price FROM watch_alerts WHERE ticker = ?", (ticker,)
        ).fetchall()]


def _engine_state():
    key = level_engine.engine_state_key("GOOGL", "GOOGL_2026-10-12")
    return level_engine._ENGINE_STATE.get(key)


class _Row(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def _synthetic_daily_bars():
    """Twenty quiet sessions ending at the plan date (close 353.30)."""
    class _Bars:
        def __init__(self, rows, dates):
            self._rows = rows
            self.index = dates

        def __len__(self):
            return len(self._rows)

        @property
        def iloc(self):
            outer = self

            class _Iloc:
                def __getitem__(self, i):
                    return outer._rows[i]

            return _Iloc()

    rows = [
        _Row({"Open": 348.0, "High": 349.0, "Low": 347.0, "Close": 348.5, "Volume": 1_000_000 + i * 1000})
        for i in range(20)
    ]
    rows.append(_Row({"Open": 352.0, "High": 353.4, "Low": 351.8, "Close": 353.30, "Volume": 2_000_000}))
    dates = [f"2026-09-{d:02d}" for d in range(1, 21)] + ["2026-10-12"]
    return _Bars(rows, dates)


def _synthetic_intraday_bars():
    """The user's tick: 353.3 -> 357.02 (cross) -> 357.6 -> 353 (close back below)."""
    seq = [
        ("2026-10-13T09:30", 353.0, 353.6, 352.8, 353.30),
        ("2026-10-13T09:35", 353.3, 354.0, 353.1, 353.80),
        ("2026-10-13T09:40", 353.8, 357.1, 353.7, 357.02),   # the cross of 356.83
        ("2026-10-13T09:45", 357.0, 357.6, 356.9, 357.60),   # holding above
        ("2026-10-13T09:50", 357.5, 357.6, 353.0, 353.10),   # back below
        ("2026-10-13T09:55", 353.1, 353.4, 352.9, 353.00),   # the close
    ]
    out = []
    base_ts = int(datetime(2026, 10, 13, 9, 30).timestamp() * 1000)
    for i, (ts, o, h, l, c) in enumerate(seq):
        out.append({"datetime": base_ts + i * 300_000, "open": o, "high": h, "low": l,
                    "close": c, "volume": 900_000 + i * 50_000})
    return out


# ---------------------------------------------------------------------------
# 1. A broken trigger is never zeroed
# ---------------------------------------------------------------------------

def test_broken_trigger_is_kept_and_its_targets_rebuilt():
    from src.logic.level_validation import validate_levels

    plan = {
        "entry_type": "LIMIT",
        "entry_zone_low": 349.0,
        "entry_zone_high": 351.0,
        "breakout_level": 355.0,
        "breakout_stop": 350.0,
        "tactical_stop": 344.0,
        "target_1": 354.0,   # at/below the trigger -> the old gate zeroed the trigger
        "target_2": 354.5,
        "spot": 353.30,
        "setup_lane": "JUDGE",
    }
    dw = {"close": 353.30, "rsi2_atr14": 8.2}

    ok, reasons = validate_levels(plan, dw, "LONG", ticker="GOOGL", date_str="2026-10-12")

    # The trigger survived, and the targets were rebuilt strictly above it.
    assert plan["breakout_level"] == 355.0
    assert plan["targets_rebuilt"] is True
    assert plan["target_1"] > 355.0
    assert plan["target_2"] > plan["target_1"]
    assert plan["targets_rebuilt_basis"] in ("next_resistance", "atr_multiple", "stop_distance_multiple")
    assert "targets rebuilt above it" in plan["breakout_demoted_reason"]


def test_demoted_plan_still_arms_a_breakout():
    """A plan written by the old gate (trigger zeroed, reason recorded) still arms the
    BREAKOUT: the trigger value is recovered from the recorded demotion reason."""
    legacy = {
        "entry_zone_low": 349.0,
        "entry_zone_high": 351.0,
        "breakout_level": 0,
        "tactical_stop": 344.0,
        "target_1": 356.73,
        "target_2": 370.0,
        "breakout_demoted_reason": "target_1 $354.0000 is below breakout trigger $355.0000 — targets must sit above the trigger",
    }
    armed = level_engine.build_armed_levels(legacy, side="LONG")
    by_kind = {lv.kind: lv for lv in armed}
    assert "BREAKOUT" in by_kind
    assert by_kind["BREAKOUT"].price == 355.0


# ---------------------------------------------------------------------------
# 2+3. Arm, cross, one alert, re-arm
# ---------------------------------------------------------------------------

def test_googl_breakout_cross_fires_one_alert_and_rearms():
    """The synthetic replay: 353.3 -> 357.02 -> 357.6 -> 353 produces exactly one
    BREAKOUT alert, one re-arm, and a plan outcome that says failed breakout."""
    watch_manager.upsert_watch_target(dict(GOOGL_PLAN))

    bars = _synthetic_daily_bars()
    intraday = _synthetic_intraday_bars()

    prices = [353.30, 357.02, 357.60, 353.00]
    for px in prices:
        with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
             patch("run_watch_alerts.get_current_price", return_value=px), \
             patch("src.clients.quote_router.quote_router.get_watchlist_quotes_batch",
                   return_value={"GOOGL": _quote(px, volume=3_000_000)}), \
             patch("src.tracking.execution_validator.get_bars_since_date", return_value=bars), \
             patch("src.clients.schwab_client.get_intraday_candles", return_value=intraday):
            evaluate_watch_cycle(sync_sheets=False)

    rows = _alert_rows()
    bo = [r for r in rows if r["trigger_type"] == "LEVEL_BREAKOUT"]
    assert len(bo) == 1, f"exactly one BREAKOUT alert, got {[r['trigger_type'] for r in rows]}"

    msg = bo[0]["message"]
    assert "$356.83" in msg                      # which level crossed
    assert "RVOL" in msg                          # volume pace stated
    assert "R:R to T1" in msg and "R:R to T2" in msg  # R:R at market to the plan targets
    assert "lane" in msg                          # MEASURED / UNMEASURED tag
    assert "at market: RUNNING_" in msg           # judged at market, not a bare print

    # One re-arm: after the 353 print the level is armed again for the next session.
    state = _engine_state()
    assert state is not None
    lv = state.by_kind("BREAKOUT")
    assert lv is not None and lv.armed is True, "a close back below the trigger re-arms the level"

    # The re-score at the cross price is said plainly, and is not STALK.
    res = run_watch_alerts._rescore_at_market(
        "GOOGL", 357.02, dict(GOOGL_PLAN), "LONG", _mock_et(), ["BREAKOUT"]
    )
    assert res["outcome"] == "RUNNING_NO_ROOM"    # best R:R ~1.32 < 2.0 floor
    assert res["live_rr"] < 2.0
    assert "ran, no room" in res["message"]        # said plainly
    assert "wait for pullback" in res["message"]
    assert "STALK" not in res["message"].replace("STALKING", "")


def test_googl_alert_visible_via_watch_alerts_api():
    """/api/watch-alerts shows the alert (the desk's alert stream)."""
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.ui.app import create_app

    # The endpoint reads the cockpit's shared DB (STOCK_DB_PATH, redirected by conftest);
    # point the watcher at the same file so the write and the read agree.
    monkeypatch_db = Path(os.environ["STOCK_DB_PATH"])
    watch_manager.DB_PATH = monkeypatch_db
    watch_manager.init_watch_db()

    watch_manager.upsert_watch_target(dict(GOOGL_PLAN))
    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=353.30), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=_synthetic_daily_bars()):
        evaluate_watch_cycle(sync_sheets=False)
    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=357.02), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=_synthetic_daily_bars()):
        evaluate_watch_cycle(sync_sheets=False)

    client = TestClient(create_app())
    res = client.get("/api/watch-alerts?ticker=GOOGL&limit=25")
    assert res.status_code == 200
    alerts = res.json().get("alerts") or res.json().get("rows") or []
    assert any(a.get("trigger_type") == "LEVEL_BREAKOUT" for a in alerts), (
        f"the BREAKOUT alert must be visible in /api/watch-alerts; got {[a.get('trigger_type') for a in alerts]}"
    )


# ---------------------------------------------------------------------------
# 4. The judgment at market
# ---------------------------------------------------------------------------

def test_judgment_at_market_states_run_terms_plainly():
    plan = GOOGL_PLAN["shares_plan"]
    # Zone 349-351, trigger 356.83, stop 344, T1 357.38 / T2 363.97 (rebuilt above the
    # trigger) -> at the 357.02 print the R:R to T1 is thin and to T2 below the 2.0
    # floor -> RUNNING_NO_ROOM, said plainly.
    j = judge_at_market(plan, 357.02)
    assert j["position"] == "ABOVE_ZONE_RUNNING"
    assert j["rr_at_market_to_t2"] == pytest.approx(0.53, abs=0.02)
    assert j["outcome"] == "RUNNING_NO_ROOM"
    assert "RUNNING_NO_ROOM" in j["verdict"]
    assert "STALK" != j["outcome"]

    # Inside the zone the name is judged there, not collapsed to a stalk either.
    j_in = judge_at_market(plan, 350.0)
    assert j_in["position"] == "IN_ZONE"
    assert j_in["outcome"] in ("RUNNING_ACTIONABLE", "RUNNING_NO_ROOM")


# ---------------------------------------------------------------------------
# 4b. The PRINTED verdict comes from the level engine and the gate
# ---------------------------------------------------------------------------

def test_printed_verdict_comes_from_the_engine_not_the_llm():
    """The row's verdict line is the engine's: outcome, R:R at market, lane label,
    armed levels — never the judge's conviction."""
    watch_manager.upsert_watch_target(dict(GOOGL_PLAN))
    bars = _synthetic_daily_bars()

    results = []
    for px in (353.30, 357.02):
        with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
             patch("src.clients.quote_router.quote_router.get_watchlist_quotes_batch",
                   return_value={"GOOGL": _quote(px, volume=3_000_000)}), \
             patch("src.tracking.execution_validator.get_bars_since_date", return_value=bars), \
             patch("src.clients.schwab_client.get_intraday_candles", return_value=[]):
            results = evaluate_watch_cycle(sync_sheets=False)

    row = next(r for r in results if r["ticker"] == "GOOGL")
    line = row["verdict_line"]
    # The gate/engine verdict, R:R at market, the lane tag, and the armed set — no conviction.
    assert "RUNNING_NO_ROOM" in line
    assert "[UNMEASURED]" in line
    assert "R:R@mkt" in line
    assert "ARMED" in line
    assert "cross: BREAKOUT" in line
    assert "conviction" not in line.lower()
    assert row["engine_verdict"]["armed_levels"] > 0
    assert row["engine_verdict"]["trigger_armed"] is True


def test_printed_verdict_labels_a_stalk_with_no_armed_level_unarmed():
    """A row whose plan arms nothing prints UNARMED — and that plan is rejected at
    write time (the DB gate and the sync/judge armed-level check), so this label is
    the read-time backstop for rows stored before the rule existed."""
    payload = dict(GOOGL_PLAN)
    payload["shares_plan"] = {"entry_type": "LIMIT"}  # arms nothing
    v = run_watch_alerts._engine_verdict(
        "GOOGL", 353.30, payload, "LONG", "STALKING", None, [], armed_levels=0
    )
    line = run_watch_alerts._verdict_line(v)
    assert "UNARMED" in line
    assert "no armed level" in line
    assert v["armed_levels"] == 0
    assert v["trigger_armed"] is False


# ---------------------------------------------------------------------------
# 5. The prior plan outcome block
# ---------------------------------------------------------------------------
def test_prior_plan_outcome_block_states_crossed_when_and_held():
    watch_manager.upsert_watch_target(dict(GOOGL_PLAN))
    bars = _synthetic_daily_bars()
    intraday = _synthetic_intraday_bars()

    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=bars), \
         patch("src.clients.schwab_client.get_intraday_candles", return_value=intraday), \
         patch("src.clients.price_client.get_current_price", return_value=353.00):
        outcome = build_prior_plan_outcome("GOOGL", live_price=353.00, as_of="2026-10-13")

    by_kind = {lv["kind"]: lv for lv in outcome["levels"]}
    assert set(by_kind) >= {"ZONE_LOW", "ZONE_TOP", "BREAKOUT", "T1", "T2", "STOP"}

    bo = by_kind["BREAKOUT"]
    assert bo["level"] == 356.83
    assert bo["crossed"] is True                       # crossed...
    assert bo["first_cross"] is not None               # ...when...
    assert bo["held_on_close"] is False                 # ...but the 353 close gave it back
    assert bo["outcome"] == "failed_breakout_closed_back_below"

    stop_lv = by_kind["STOP"]
    assert stop_lv["outcome"] == "not_triggered"       # never crossed — level unchanged

    # The stop and invalidation levels are distinct facts.
    j = outcome["judgment"]
    assert j["live_price"] == 353.00
    assert j["position"] == "ABOVE_ZONE_RUNNING"  # above the 349-351 zone, below the 356.83 trigger
    assert j["outcome"] == "RUNNING_NO_ROOM"

    md = plan_outcome_markdown(outcome)
    assert "PRIOR PLAN OUTCOME" in md
    assert "FAILED BREAKOUT" in md
    assert "NOT TRIGGERED" in md
    assert "JUDGMENT AT MARKET" in md
    assert "crossed: yes" in md


def test_prior_plan_outcome_uses_the_stored_plan_when_none_given():
    """The block is built from the stored plan — the desk's own levels — not a re-derivation."""
    watch_manager.upsert_watch_target(dict(GOOGL_PLAN))
    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=None), \
         patch("src.clients.schwab_client.get_intraday_candles", return_value=[]), \
         patch("src.clients.price_client.get_current_price", return_value=353.30):
        outcome = build_prior_plan_outcome("GOOGL")

    assert outcome["ticker"] == "GOOGL"
    assert outcome["plan_date"] == "2026-10-12"
    by_kind = {lv["kind"]: lv["level"] for lv in outcome["levels"]}
    assert by_kind["BREAKOUT"] == 356.83
    assert by_kind["ZONE_TOP"] == 351.0
    assert by_kind["T1"] == pytest.approx(357.3803)
    assert by_kind["T2"] == pytest.approx(363.971)
    assert by_kind["STOP"] == 344.0


def test_prior_plan_outcome_absent_means_absent():
    with patch("src.tracking.execution_validator.get_bars_since_date", return_value=None), \
         patch("src.clients.schwab_client.get_intraday_candles", return_value=[]), \
         patch("src.clients.price_client.get_current_price", return_value=10.0):
        outcome = build_prior_plan_outcome("NOSUCHTICKER")
    assert outcome.get("error") == "no stored plan"
    md = plan_outcome_markdown(outcome)
    assert "no stored plan" in md
