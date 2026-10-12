"""
tests/test_watch_level_events.py

A2/A3/A4/B from plan/no-lost-movers.md, exercised through evaluate_watch_cycle:
a mid-day cross of any level fires exactly one alert, one alert per (ticker, level,
day), the RVOL gate holds quiet crosses, a runaway re-score never writes a terminal
status, and its actionable wording carries the UNMEASURED lane tag with no sizing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from src.clients.quote_router import QuoteData
from src.tracking import watch_manager
from run_watch_alerts import evaluate_watch_cycle


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    test_db = tmp_path / "watch_events.db"
    monkeypatch.setattr(watch_manager, "DB_PATH", test_db)
    watch_manager.init_watch_db()
    from src.tracking import level_engine
    level_engine.reset_engine_state()
    # Same scoping as test_catch_movers: filesystem-scoped tests patch BASE_DIR so the
    # coverage scan cannot reach the real machine's data dir (and live quote pools).
    monkeypatch.setattr("src.config.BASE_DIR", tmp_path)
    # Prices come from get_current_price in these tests; keep the surveillance channel quiet.
    monkeypatch.setattr(
        "src.clients.quote_router.quote_router.get_watchlist_quotes_batch", lambda syms: {}
    )
    yield
    level_engine.reset_engine_state()


def _payload(ticker: str, **over):
    p = {
        "ticker": ticker,
        "date": "2026-10-12",
        "verdict": "STALK",
        "conviction": 5,
        "actionable": True,
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            "side": "LONG",
            "entry_zone_low": 100.0,
            "entry_zone_high": 105.0,
            "tactical_stop": 95.0,
            "target_1": 120.0,
            "target_2": 130.0,
        },
        "options_plan": {"structure": "NONE", "summary": "None"},
        "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 95.0, "rationale": "Floor"},
        "status": "STALKING",
    }
    p.update(over)
    return p


def _quote(price: float, volume: int = 0) -> QuoteData:
    return QuoteData(symbol="X", last_price=price, volume=volume, source="TEST")


def _alert_rows(ticker: str) -> list:
    with watch_manager._db_lock, watch_manager._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT trigger_type, message FROM watch_alerts WHERE ticker = ?", (ticker,)
        ).fetchall()]


def _mock_et():
    return datetime(2026, 10, 12, 12, 0)  # midday: 12:00 ET


def test_midday_cross_of_a_level_fires_exactly_one_alert(tmp_path):
    """A3: the old after-close gate is gone. A 12:00 cross of the zone top fires."""
    watch_manager.upsert_watch_target(_payload("MIDDAY"))

    # Cycle 1: below the zone, no previous price -> no crosses (cold start).
    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=98.0):
        evaluate_watch_cycle(sync_sheets=False)
    assert _alert_rows("MIDDAY") == []

    # Cycle 2: trades up through the zone -> one alert per crossed level.
    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=106.0):
        res = evaluate_watch_cycle(sync_sheets=False)
    types = [r["trigger_type"] for r in _alert_rows("MIDDAY")]
    assert "LEVEL_ZONE_TOP" in types
    assert len([t for t in types if t == "LEVEL_ZONE_TOP"]) == 1

    # Cycle 3: keeps running above the zone -> no repeat of the same level alert.
    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=110.0):
        evaluate_watch_cycle(sync_sheets=False)
    types = [r["trigger_type"] for r in _alert_rows("MIDDAY")]
    assert len([t for t in types if t == "LEVEL_ZONE_TOP"]) == 1


def test_rvol_gate_holds_a_quiet_cross():
    """A3: ADV20 from daily bars; a cross on 0.5x volume is held, not alerted."""
    watch_manager.upsert_watch_target(_payload("QUIET"))

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=98.0), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=_bars_with_volume(2_000_000)):
        evaluate_watch_cycle(sync_sheets=False)

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("src.clients.quote_router.quote_router.get_watchlist_quotes_batch",
               return_value={"QUIET": _quote(106.0, volume=500_000)}), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=_bars_with_volume(2_000_000)):
        evaluate_watch_cycle(sync_sheets=False)
    types = [r["trigger_type"] for r in _alert_rows("QUIET")]
    assert "LEVEL_ZONE_TOP" not in types


def test_cross_without_adv20_still_fires_unmeasured():
    """A3: no ADV20 available -> the cross is tagged unmeasured and still fires."""
    watch_manager.upsert_watch_target(_payload("NOADV"))

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=98.0):
        evaluate_watch_cycle(sync_sheets=False)

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=106.0), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
        evaluate_watch_cycle(sync_sheets=False)

    msgs = [r["message"] for r in _alert_rows("NOADV") if r["trigger_type"] == "LEVEL_ZONE_TOP"]
    assert len(msgs) == 1
    assert "unmeasured" in msgs[0]


def test_runaway_rescore_is_never_terminal_and_words_the_alert():
    """B: RUNNING_ACTIONABLE keeps the push wording and tags the lane UNMEASURED; a
    no-room runaway keeps the name armed instead of writing MISSED_RUNAWAY."""
    watch_manager.upsert_watch_target(_payload(
        "RUN",
        shares_plan={
            "entry_type": "LIMIT",
            "side": "LONG",
            "entry_zone_low": 100.0,
            "entry_zone_high": 105.0,
            "breakout_level": 108.0,
            "breakout_stop": 103.0,
            "tactical_stop": 95.0,
            "target_1": 140.0,
            "target_2": 150.0,
        },
        signal_pack=36,   # PB funnel (bit 5) + not-fade (bit 2)
        fade=0.0,
        action_long=20,
        ext_z=0.0,
        atr_at_signal=3.0,
    ))

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=109.0), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
        res = evaluate_watch_cycle(sync_sheets=False)

    row = next(r for r in res if r["ticker"] == "RUN")
    # 109: above zone top, below T1, stop 95 below price, R:R (140-109)/(109-95)=2.21
    assert row["status"] == "IN_TRADE"
    assert row["last_alert_type"] == "RUNNING_ACTIONABLE"

    alerts = _alert_rows("RUN")
    push = next(r for r in alerts if r["trigger_type"] == "RUNNING_ACTIONABLE")
    assert "BUY 100 RUN at market-$109.00" in push["message"]
    assert "stop $95.00" in push["message"]
    assert "T1 $140.00" in push["message"]
    assert "R:R 2.21@mkt" in push["message"]
    assert "UNMEASURED" in push["message"]
    assert "shares" not in push["message"].lower().replace("BUY 100", "")

    with watch_manager._db_lock, watch_manager._get_connection() as conn:
        db_row = conn.execute("SELECT status FROM watch_targets WHERE ticker = 'RUN'").fetchone()
    assert db_row[0] == "IN_TRADE"


def test_legacy_missed_runaway_row_is_resurrected_not_terminal():
    """B3: a row that was written MISSED_RUNAWAY before this change does not stay dead."""
    watch_manager.upsert_watch_target(_payload("LEGACY", status="MISSED_RUNAWAY"))

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=98.0), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
        res = evaluate_watch_cycle(sync_sheets=False)

    row = next(r for r in res if r["ticker"] == "LEGACY")
    assert row["status"] == "STALKING"

    with watch_manager._db_lock, watch_manager._get_connection() as conn:
        db_row = conn.execute("SELECT status FROM watch_targets WHERE ticker = 'LEGACY'").fetchone()
    assert db_row[0] == "STALKING"


def test_target_hit_without_fill_requeues_local_triage():
    """B3: T1 reached without a fill re-queues local triage with force."""
    watch_manager.upsert_watch_target(_payload("REQ"))

    with patch("run_watch_alerts.get_eastern_now", return_value=_mock_et()), \
         patch("run_watch_alerts.get_current_price", return_value=121.0), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=None), \
         patch("src.tracking.alert_db.queue_for_research") as qfr:
        res = evaluate_watch_cycle(sync_sheets=False)

    row = next(r for r in res if r["ticker"] == "REQ")
    assert row["status"] == "STALKING"
    assert row["last_alert_type"] == "TARGET_HIT_NO_FILL"
    assert qfr.called
    assert qfr.call_args.kwargs.get("force") is True
    assert qfr.call_args.kwargs.get("source") == "watch_runaway"


def test_forward_log_records_the_runaway_rule_as_unmeasured(tmp_path, monkeypatch):
    """B4: the runaway buy is forward-logged under its own UNMEASURED rule."""
    from src.tracking import forward_log
    ledger = tmp_path / "forward_log_ledger.json"
    monkeypatch.setattr(forward_log, "get_forward_log_path", lambda: ledger)
    stats = forward_log.get_forward_summary()
    assert "RUNAWAY_AT_MARKET" in stats["rules"]
    assert stats["rules"]["RUNAWAY_AT_MARKET"]["status"] == "UNMEASURED"
    assert stats["rules"]["RUNAWAY_AT_MARKET"]["ship_date"] == "2026-10-12"

    forward_log.record_forward_observation("RUNAWAY_AT_MARKET", "REQ", {"rr_at_market": 2.67})
    import json as _json
    data = _json.loads(ledger.read_text(encoding="utf-8"))
    assert data["RUNAWAY_AT_MARKET"][0]["ticker"] == "REQ"
    assert data["RUNAWAY_AT_MARKET"][0]["ship_date"] == "2026-10-12"


# --- helpers -----------------------------------------------------------------

class _FakeRow(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def _bars_with_volume(adv: int):
    """A minimal bars frame whose Volume column averages `adv` over 20 bars."""
    class _Bars:
        def __init__(self, rows, index):
            self._rows = rows
            self.index = index

        def __len__(self):
            return len(self._rows)

        def __getitem__(self, mask):
            rows = [r for r, m in zip(self._rows, mask) if m]
            idx = [i for i, m in zip(self.index, mask) if m]
            return _Bars(rows, idx)

        @property
        def iloc(self):
            outer = self

            class _Iloc:
                def __getitem__(self, i):
                    return outer._rows[i]

            return _Iloc()

    rows = [_FakeRow({"High": 200.0, "Low": 95.0, "Close": 100.0, "Volume": adv}) for _ in range(20)]
    index = [f"2026-09-{d:02d}" for d in range(1, 31)]
    return _Bars(rows, index)
