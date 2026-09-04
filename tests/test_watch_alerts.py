import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.logic.report_level_extractor import extract_watch_levels_from_report
from src.tracking import watch_manager
from run_watch_alerts import evaluate_watch_cycle


def test_watch_manager_crud(tmp_path):
    # Point DB_PATH to temporary test database
    test_db = tmp_path / "test_watch.db"
    with patch.object(watch_manager, "DB_PATH", test_db):
        watch_manager.init_watch_db()

        test_payload = {
            "ticker": "TEST",
            "date": "2026-08-29",
            "verdict": "STALK",
            "conviction": 6,
            "actionable": True,
            "shares_plan": {
                "entry_type": "LIMIT",
                "entry_zone_low": 100.0,
                "entry_zone_high": 105.0,
                "tactical_stop": 95.0,
                "target_1": 120.0,
                "target_2": 130.0,
            },
            "options_plan": {
                "structure": "BULL_CALL_SPREAD",
                "summary": "Test Spread",
            },
            "invalidation": {
                "condition": "DAILY_CLOSE_BELOW",
                "price_level": 95.0,
                "rationale": "Stop loss",
            },
            "status": "STALKING",
        }

        # Upsert
        watch_manager.upsert_watch_target(test_payload)

        # Retrieve active
        active = watch_manager.get_active_watch_targets()
        assert len(active) == 1
        assert active[0]["ticker"] == "TEST"
        assert active[0]["verdict"] == "STALK"
        assert active[0]["entry_zone_low"] == 100.0
        assert active[0]["entry_zone_high"] == 105.0
        assert active[0]["tactical_stop"] == 95.0
        assert active[0]["target_1"] == 120.0

        # Update live state
        watch_manager.update_target_live_state("TEST", live_price=102.5, status="IN_ZONE", distance_to_entry_pct=0.0)
        updated = watch_manager.get_active_watch_targets()
        assert updated[0]["last_price"] == 102.5
        assert updated[0]["status"] == "IN_ZONE"

        # Log alert
        watch_manager.log_trigger_alert("TEST", "ENTRY_TRIGGERED", "Entered zone", 102.5)
        alerts = watch_manager.get_recent_alerts(10)
        assert len(alerts) == 1
        assert alerts[0]["ticker"] == "TEST"
        assert alerts[0]["trigger_type"] == "ENTRY_TRIGGERED"


def test_evaluate_watch_cycle_state_transitions(tmp_path):
    test_db = tmp_path / "test_watch_cycle.db"
    with patch.object(watch_manager, "DB_PATH", test_db):
        watch_manager.init_watch_db()

        target_payload = {
            "ticker": "MOCK_TEST_TICKER",
            "date": "2026-08-29",
            "verdict": "STALK",
            "conviction": 5,
            "actionable": True,
            "shares_plan": {
                "entry_type": "LIMIT",
                "entry_zone_low": 50.0,
                "entry_zone_high": 52.0,
                "tactical_stop": 47.0,
                "target_1": 60.0,
                "target_2": 65.0,
            },
            "options_plan": {"structure": "NONE", "summary": "None"},
            "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 47.0, "rationale": "Floor break"},
            "status": "STALKING",
        }
        watch_manager.upsert_watch_target(target_payload)

        # 1. Price above entry zone -> STALKING (Distance > 0)
        with patch("run_watch_alerts.get_current_price", return_value=55.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "STALKING"
            assert res[0]["distance_to_entry_pct"] == pytest.approx(5.77, 0.01)

        # 2. Price enters zone ($51.0) -> Transitions to IN_ZONE
        with patch("run_watch_alerts.get_current_price", return_value=51.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "IN_ZONE"
            assert res[0]["distance_to_entry_pct"] == 0.0
            assert res[0]["last_alert_type"] == "ENTRY_TRIGGERED"

        # 3. Price reaches Target 1 ($60.5) -> Transitions to TARGET_HIT
        with patch("run_watch_alerts.get_current_price", return_value=60.5):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "TARGET_HIT"
            assert res[0]["last_alert_type"] == "TARGET_1_REACHED"

        # 4. Reset to STALKING and test Stop loss breach ($46.0) -> Transitions to INVALIDATED
        watch_manager.upsert_watch_target(target_payload)
        with patch("run_watch_alerts.get_current_price", return_value=46.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "INVALIDATED"
            assert res[0]["last_alert_type"] == "STOP_BREACHED"


def test_extract_watch_levels_from_amzn_report():
    data = extract_watch_levels_from_report("AMZN", "2026-08-29")
    assert data is not None
    assert data["ticker"] == "AMZN"
    assert data["verdict"] in ("STALK", "ENTER")
    assert data["shares_plan"]["entry_zone_low"] == 263.42
    assert data["shares_plan"]["entry_zone_high"] == 265.0
    assert data["shares_plan"]["tactical_stop"] == 262.0
    assert data["shares_plan"]["target_1"] == 275.0
    assert data["options_plan"]["structure"] == "BULL_CALL_SPREAD"
    assert data["options_plan"]["long_strike"] == 265.0
    assert data["options_plan"]["short_strike"] == 280.0
    assert data["invalidation"]["price_level"] == 262.0


def test_missed_runaway_and_breakout_triggers(tmp_path):
    test_db = tmp_path / "test_runaway.db"
    with patch.object(watch_manager, "DB_PATH", test_db):
        watch_manager.init_watch_db()

        # 1. Target with both Limit zone ($100-$102) and Breakout level ($112)
        hood_payload = {
            "ticker": "HOOD_TEST",
            "date": "2026-08-31",
            "verdict": "STALK",
            "conviction": 5,
            "actionable": True,
            "side": "LONG",
            "shares_plan": {
                "entry_type": "LIMIT",
                "side": "LONG",
                "entry_zone_low": 100.0,
                "entry_zone_high": 102.0,
                "breakout_level": 112.0,
                "breakout_stop": 109.0,
                "tactical_stop": 98.0,
                "target_1": 110.0,
                "target_2": 115.0,
            },
            "options_plan": {"structure": "NONE", "summary": "None"},
            "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 98.0, "rationale": "Floor break"},
            "status": "STALKING",
        }
        watch_manager.upsert_watch_target(hood_payload)

        # Case A: Price breaks out above $112 -> Transitions to IN_TRADE with BREAKOUT_ENTERED
        with patch("run_watch_alerts.get_current_price", return_value=112.5):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "IN_TRADE"
            assert res[0]["last_alert_type"] == "BREAKOUT_ENTERED"

        # Case B: Reset to STALKING without breakout level; price rips to Target 2 ($120) without entering zone
        hood_payload_no_bo = dict(hood_payload)
        hood_payload_no_bo["shares_plan"] = dict(hood_payload["shares_plan"])
        hood_payload_no_bo["shares_plan"]["breakout_level"] = None
        hood_payload_no_bo["ticker"] = "HOOD_RUNAWAY"
        watch_manager.upsert_watch_target(hood_payload_no_bo)

        with patch("run_watch_alerts.get_current_price", return_value=120.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            hood_r = next(r for r in res if r["ticker"] == "HOOD_RUNAWAY")
            assert hood_r["status"] == "MISSED_RUNAWAY"
            assert hood_r["last_alert_type"] == "MISSED_RUNAWAY_TARGET_2"


def test_short_side_watch_alerts(tmp_path):
    test_db = tmp_path / "test_short.db"
    with patch.object(watch_manager, "DB_PATH", test_db):
        watch_manager.init_watch_db()

        short_payload = {
            "ticker": "SHORT_TEST",
            "date": "2026-08-31",
            "verdict": "ENTER",
            "conviction": 5,
            "actionable": True,
            "side": "SHORT",
            "shares_plan": {
                "entry_type": "LIMIT",
                "side": "SHORT",
                "entry_zone_low": 98.0,
                "entry_zone_high": 100.0,
                "tactical_stop": 105.0,
                "target_1": 90.0,
                "target_2": 85.0,
            },
            "options_plan": {"structure": "NONE", "summary": "None"},
            "invalidation": {"condition": "DAILY_CLOSE_ABOVE", "price_level": 105.0, "rationale": "Ceiling break"},
            "status": "IN_TRADE",
        }
        watch_manager.upsert_watch_target(short_payload)

        # 1. Price drops to $84.0 -> Hits Target 2 on Short
        with patch("run_watch_alerts.get_current_price", return_value=84.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "TARGET_HIT"
            assert res[0]["last_alert_type"] == "TARGET_2_REACHED"

        # 2. Reset and price rises to $106.0 -> Breaches Stop on Short
        watch_manager.upsert_watch_target(short_payload)
        with patch("run_watch_alerts.get_current_price", return_value=106.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "INVALIDATED"
            assert res[0]["last_alert_type"] == "STOP_BREACHED"


def test_floor_proximity_buffer(tmp_path):
    test_db = tmp_path / "test_prox.db"
    with patch.object(watch_manager, "DB_PATH", test_db):
        watch_manager.init_watch_db()

        # Target with zone $300.00 - $301.50 (AAPL Put Wall simulation)
        aapl_payload = {
            "ticker": "AAPL_PROX",
            "date": "2026-08-14",
            "verdict": "STALK",
            "conviction": 5,
            "actionable": True,
            "side": "LONG",
            "shares_plan": {
                "entry_type": "LIMIT",
                "side": "LONG",
                "entry_zone_low": 300.0,
                "entry_zone_high": 301.5,
                "tactical_stop": 298.0,
                "target_1": 316.0,
                "target_2": 320.0,
            },
            "options_plan": {
                "structure": "BULL_CALL_SPREAD",
                "summary": "$320C/$330C Bull Call Spread",
                "actionable": True,
                "entry_trigger": "AT_FLOOR_LIMIT",
            },
            "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 298.0, "rationale": "Floor break"},
            "status": "STALKING",
        }
        watch_manager.upsert_watch_target(aapl_payload)

        # Price pulls back to $302.00 (within 1% above $301.50, exactly front-running the floor)
        with patch("run_watch_alerts.get_current_price", return_value=302.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "IN_ZONE"
            assert res[0]["last_alert_type"] == "ENTRY_TRIGGERED"


def test_research_queue_date_resolution():
    from run_ui import get_research_queue
    # 1. Default call auto-resolves to latest raw date (e.g. 2026-09-03)
    res = get_research_queue()
    assert "date" in res
    assert "queue" in res
    assert len(res["queue"]) > 0

    # 2. Scraped tickers ready for research are marked deep_only
    deep_only_items = [q for q in res["queue"] if q["action"] == "deep_only"]
    for item in deep_only_items:
        assert item["status"] == "READY_FOR_RESEARCH"
        assert item["has_chart"] is True
        assert item["has_report"] is False

    # 3. Explicit historical date lookup
    hist_res = get_research_queue(date="2026-08-20")
    assert hist_res["date"] == "2026-08-20"
    assert len(hist_res["queue"]) > 0


