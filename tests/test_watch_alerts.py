import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

from src.logic.report_level_extractor import (
    _credit_strike_em_consistent,
    _demote_unscaled_credit,
    _dw_lookup,
    extract_watch_levels_from_report,
)
from src.tracking import watch_manager
from run_watch_alerts import evaluate_watch_cycle


@pytest.fixture(autouse=True)
def _mock_watch_alerts_network():
    with patch("src.clients.quote_router.quote_router.get_watchlist_quotes_batch", return_value={}), \
         patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
        yield


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
    from datetime import datetime
    test_db = tmp_path / "test_watch_cycle.db"
    mock_now = datetime(2026, 8, 29, 16, 30)
    with patch.object(watch_manager, "DB_PATH", test_db), patch("run_watch_alerts.get_eastern_now", return_value=mock_now):
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


def test_extract_watch_levels_from_amzn_report(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-08-29"
    reports_dir.mkdir(parents=True)
    summary = reports_dir / "AMZN_summary.md"
    summary.write_text(
        "# AMZN Tactical Summary\n"
        "```json:watch_levels\n"
        '{"verdict": "STALK", "side": "LONG", '
        '"shares_plan": {"entry_type": "LIMIT", "entry_zone_low": 263.42, '
        '"entry_zone_high": 265.0, "tactical_stop": 262.0, "target_1": 275.0}, '
        '"options_plan": {"structure": "BULL_CALL_SPREAD", "long_strike": 265.0, "short_strike": 280.0}, '
        '"invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 262.0}}\n'
        "```\n",
        encoding="utf-8",
    )
    with patch("src.logic.report_level_extractor.config.BASE_DIR", tmp_path):
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
    from datetime import datetime
    test_db = tmp_path / "test_runaway.db"
    mock_now = datetime(2026, 8, 31, 16, 30)
    with patch.object(watch_manager, "DB_PATH", test_db), patch("run_watch_alerts.get_eastern_now", return_value=mock_now):
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

        with patch("run_watch_alerts.get_current_price", return_value=120.0), patch("src.tracking.execution_validator.get_bars_since_date", return_value=None):
            res = evaluate_watch_cycle(sync_sheets=False)
            hood_r = next(r for r in res if r["ticker"] == "HOOD_RUNAWAY")
            assert hood_r["status"] == "MISSED_RUNAWAY"
            assert hood_r["last_alert_type"] == "MISSED_RUNAWAY_TARGET_2"


def test_short_side_watch_alerts(tmp_path):
    from datetime import datetime
    test_db = tmp_path / "test_short.db"
    mock_now = datetime(2026, 8, 31, 16, 30)
    with patch.object(watch_manager, "DB_PATH", test_db), patch("run_watch_alerts.get_eastern_now", return_value=mock_now):
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
        with patch("run_watch_alerts.get_current_price", return_value=107.0):
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

        from datetime import datetime
        mock_now = datetime(2026, 8, 14, 16, 30)
        # Price at $302.00 is above $301.50 -> STALKING (Phase 3: tight IN_ZONE removes +1% above-zone trigger)
        with patch("run_watch_alerts.get_eastern_now", return_value=mock_now), patch("run_watch_alerts.get_current_price", return_value=302.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "STALKING"
            assert res[0]["distance_to_entry_pct"] > 0

        # Price enters zone at $301.00 -> IN_ZONE
        with patch("run_watch_alerts.get_eastern_now", return_value=mock_now), patch("run_watch_alerts.get_current_price", return_value=301.0):
            res = evaluate_watch_cycle(sync_sheets=False)
            assert len(res) == 1
            assert res[0]["status"] == "IN_ZONE"
            assert res[0]["last_alert_type"] == "ENTRY_TRIGGERED"


def test_research_queue_date_resolution(tmp_path):
    import json
    from unittest.mock import patch
    from run_ui import get_research_queue

    # Create mock raw directories
    d1 = tmp_path / "data" / "raw" / "2026-09-23"
    d1.mkdir(parents=True)
    d2 = tmp_path / "data" / "raw" / "2026-08-20"
    d2.mkdir(parents=True)

    # 1. Qualified candidate: Scraped + PASS triage + send_for_deep_research = True
    aapl_dir = d1 / "AAPL"
    aapl_dir.mkdir()
    (aapl_dir / "AAPL_datawindow.json").write_text("{}", encoding="utf-8")
    (aapl_dir / "AAPL_chart.png").write_bytes(b"fake_chart")
    (aapl_dir / "AAPL_thesis.json").write_text(
        json.dumps({"triage": {"triage": "PASS", "send_for_deep_research": True}}),
        encoding="utf-8",
    )

    # 2. Disqualified candidate: Scraped + CUT triage (send_for_deep_research = False)
    msft_dir = d1 / "MSFT"
    msft_dir.mkdir()
    (msft_dir / "MSFT_datawindow.json").write_text("{}", encoding="utf-8")
    (msft_dir / "MSFT_chart.png").write_bytes(b"fake_chart")
    (msft_dir / "MSFT_thesis.json").write_text(
        json.dumps({"triage": {"triage": "CUT", "send_for_deep_research": False}}),
        encoding="utf-8",
    )

    # 3. Screener survivor needing scrape
    (d1 / "survivors.json").write_text(json.dumps([{"Symbol": "NVDA"}]), encoding="utf-8")

    # 4. Historical date survivor
    (d2 / "survivors.json").write_text(json.dumps([{"Symbol": "AMZN"}]), encoding="utf-8")

    with patch("src.config.BASE_DIR", tmp_path):
        # Default auto-resolves to latest raw date
        res = get_research_queue()
        assert res["date"] == "2026-09-23"
        q_tickers = {item["ticker"]: item for item in res["queue"]}
        
        # Qualified candidate is marked deep_only and READY_FOR_RESEARCH
        assert "AAPL" in q_tickers
        assert q_tickers["AAPL"]["action"] == "deep_only"
        assert q_tickers["AAPL"]["status"] == "READY_FOR_RESEARCH"
        assert q_tickers["AAPL"]["has_chart"] is True
        assert q_tickers["AAPL"]["has_report"] is False

        # Disqualified candidate should not be in queue
        assert "MSFT" not in q_tickers

        # Survivor needing scrape is marked full and NEEDS_SCRAPE
        assert "NVDA" in q_tickers
        assert q_tickers["NVDA"]["action"] == "full"
        assert q_tickers["NVDA"]["status"] == "NEEDS_SCRAPE"

        # Explicit historical date lookup
        hist_res = get_research_queue(date="2026-08-20")
        assert hist_res["date"] == "2026-08-20"
        hist_tickers = [item["ticker"] for item in hist_res["queue"]]
        assert "AMZN" in hist_tickers


def test_extract_watch_levels_options_menu(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-09-17"
    reports_dir.mkdir(parents=True)
    raw_dir = tmp_path / "data" / "raw" / "2026-09-17" / "AMZN"
    raw_dir.mkdir(parents=True)

    summary_file = reports_dir / "AMZN_summary.md"
    summary_file.write_text("Bar close: $250.00\nTACTICAL ENTRY ZONE: $245.00 – $248.00\nTACTICAL STOP: $240.00\nTARGET 1: $265.00", encoding="utf-8")

    arbitration_file = reports_dir / "AMZN_arbitration.md"
    arbitration_file.write_text(
        """# AMZN | ⚖️ SENIOR PM ARBITRATION
```json:watch_levels
{
  "ticker": "AMZN",
  "verdict": "ENTER",
  "conviction": 8,
  "shares_plan": {
    "entry_type": "LIMIT",
    "entry_zone_low": 245.0,
    "entry_zone_high": 248.0,
    "tactical_stop": 240.0,
    "target_1": 265.0,
    "target_2": 280.0
  },
  "options_plan": {
    "actionable": true,
    "entry_trigger": "AT_MARKET",
    "structure": "BULL_CALL_SPREAD",
    "expiration": "2026-10-16",
    "long_strike": 245.0,
    "short_strike": 260.0,
    "target_debit": 3.98,
    "max_loss": 398.0,
    "max_profit": 1102.0,
    "summary": "Oct 16 $245C/$260C Bull Call Spread"
  },
  "options_menu": {
    "tactical_spread": {
      "structure": "BULL_CALL_SPREAD",
      "expiration": "2026-10-16",
      "long_strike": 245.0,
      "short_strike": 260.0,
      "target_debit": 3.98,
      "summary": "Oct 16 $245C/$260C Bull Call Spread"
    },
    "leaps": {
      "structure": "LONG_CALL",
      "expiration": "2027-06-18",
      "long_strike": 210.0,
      "target_debit": 48.0,
      "summary": "Jun 2027 $210C Deep ITM LEAPS (0.80 delta)"
    },
    "income_or_csp": {
      "structure": "COVERED_CALL",
      "expiration": "2026-10-16",
      "short_strike": 265.0,
      "target_credit": 2.85,
      "summary": "Oct 16 $265C Covered Call on shares"
    }
  }
}
```
""",
        encoding="utf-8",
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("AMZN", "2026-09-17")
        assert res is not None
        assert "options_menu" in res
        menu = res["options_menu"]
        assert menu["tactical_spread"]["structure"] == "BULL_CALL_SPREAD"
        assert menu["leaps"]["structure"] == "LONG_CALL"
        assert menu["leaps"]["long_strike"] == 210.0
        assert menu["income_or_csp"]["structure"] == "COVERED_CALL"
        assert menu["income_or_csp"]["short_strike"] == 265.0


# ---------------------------------------------------------------------------
# EM-consistency backstop for credit spreads (unscaled premium-sale demotion)
# ---------------------------------------------------------------------------

def test_dw_lookup_real_keys():
    # Real data-window headers are "Close" and "Exp Move % (21b)" — the gate must read them.
    dw = {"Close": "317.31", "Exp Move % (21b)": "9.90"}
    assert _dw_lookup(dw, "close", "Close") == 317.31
    assert _dw_lookup(dw, "Exp Move % (21b)", "exp_move_pct") == 9.9
    # Absent / non-numeric fields fall back to 0.0 without raising.
    assert _dw_lookup({}, "close", "Close") == 0.0
    assert _dw_lookup({"Close": ""}, "close", "Close") == 0.0


def test_credit_strike_em_consistent_boundaries():
    spot, em = 317.31, 9.9
    floor = spot * (1 - 1.25 * em / 100.0)   # ~282.66
    ceil = spot * (1 + 1.25 * em / 100.0)    # ~349.60
    # Bull put / CSP: short strike must sit at or below the floor.
    assert _credit_strike_em_consistent("BULL_PUT_SPREAD", floor, spot, em) is True
    assert _credit_strike_em_consistent("BULL_PUT_SPREAD", floor - 1, spot, em) is True
    assert _credit_strike_em_consistent("BULL_PUT_SPREAD", floor + 1, spot, em) is False
    assert _credit_strike_em_consistent("CASH_SECURED_PUT", floor + 1, spot, em) is False
    # Bear call: short strike must sit at or above the ceiling.
    assert _credit_strike_em_consistent("BEAR_CALL_SPREAD", ceil, spot, em) is True
    assert _credit_strike_em_consistent("BEAR_CALL_SPREAD", ceil + 1, spot, em) is True
    assert _credit_strike_em_consistent("BEAR_CALL_SPREAD", ceil - 1, spot, em) is False


def test_credit_gate_ignores_debit_and_missing_data():
    # Debit / long structures are the buyer's side — never gated.
    assert _credit_strike_em_consistent("BULL_CALL_SPREAD", 320, 317.31, 9.9) is True
    assert _credit_strike_em_consistent("BEAR_PUT_SPREAD", 320, 317.31, 9.9) is True
    assert _credit_strike_em_consistent("LONG_CALL", 320, 317.31, 9.9) is True
    # Missing EM or spot defaults to consistent (no forced demotion on missing data).
    assert _credit_strike_em_consistent("BULL_PUT_SPREAD", 300, 317.31, 0) is True
    assert _credit_strike_em_consistent("BULL_PUT_SPREAD", 300, 0, 9.9) is True


def test_demote_unscaled_credit_clears_strikes():
    s, summary, cleared = _demote_unscaled_credit(
        "BULL_PUT_SPREAD", 300, 317.31, 9.9, "$300P Bull Put", "AAPL"
    )
    assert s == "NONE"
    assert cleared is True
    assert "DEMOTED" in summary
    # A scaled credit is left untouched.
    floor = 317.31 * (1 - 1.25 * 9.9 / 100.0)
    s2, _sum2, cleared2 = _demote_unscaled_credit(
        "BULL_PUT_SPREAD", floor - 1, 317.31, 9.9, "$280P Bull Put", "AAPL"
    )
    assert s2 == "BULL_PUT_SPREAD"
    assert cleared2 is False


def test_regex_path_demotes_unscaled_credit(tmp_path):
    raw_dir = tmp_path / "data" / "raw" / "2026-09-17" / "AMZN"
    raw_dir.mkdir(parents=True)
    reports_dir = tmp_path / "reports" / "2026-09-17"
    reports_dir.mkdir(parents=True)

    # No embedded block -> forces the regex path.
    (reports_dir / "AMZN_summary.md").write_text(
        "Bar close: $317.31\nTACTICAL ENTRY ZONE: $300.00 – $310.00"
        "\nTACTICAL STOP: $295.00\nTARGET 1: $340.00",
        encoding="utf-8",
    )
    (reports_dir / "AMZN_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 7/10\n"
        "$300P/$290P Bull Put Spread\nMax Loss: $1000\nMax Profit: $500",
        encoding="utf-8",
    )
    # Real data-window keys; EM short strike ($300) is inside 1.25x EM (~$282.7 floor).
    (raw_dir / "AMZN_datawindow.json").write_text(
        json.dumps({"Close": "317.31", "Exp Move % (21b)": "9.90"}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("AMZN", "2026-09-17")
    assert res is not None
    assert res["options_plan"]["structure"] == "NONE"
    assert res["options_plan"]["actionable"] is False
    assert res["options_plan"]["short_strike"] == 0.0
    assert "DEMOTED" in res["options_plan"]["summary"]


def test_regex_path_keeps_scaled_credit(tmp_path):
    raw_dir = tmp_path / "data" / "raw" / "2026-09-17" / "AMZN"
    raw_dir.mkdir(parents=True)
    reports_dir = tmp_path / "reports" / "2026-09-17"
    reports_dir.mkdir(parents=True)

    (reports_dir / "AMZN_summary.md").write_text(
        "Bar close: $317.31\nTACTICAL ENTRY ZONE: $300.00 – $310.00"
        "\nTACTICAL STOP: $295.00\nTARGET 1: $340.00",
        encoding="utf-8",
    )
    # $275P is below the ~$282.7 floor -> properly scaled, must survive.
    (reports_dir / "AMZN_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 7/10\n"
        "$275P/$265P Bull Put Spread\nMax Loss: $1000\nMax Profit: $500",
        encoding="utf-8",
    )
    (raw_dir / "AMZN_datawindow.json").write_text(
        json.dumps({"Close": "317.31", "Exp Move % (21b)": "9.90"}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("AMZN", "2026-09-17")
    assert res is not None
    assert res["options_plan"]["structure"] == "BULL_PUT_SPREAD"
    assert res["options_plan"]["actionable"] is True
    assert res["options_plan"]["short_strike"] == 275.0


def test_embedded_path_demotes_unscaled_credit(tmp_path):
    raw_dir = tmp_path / "data" / "raw" / "2026-09-17" / "AMZN"
    raw_dir.mkdir(parents=True)
    reports_dir = tmp_path / "reports" / "2026-09-17"
    reports_dir.mkdir(parents=True)

    (raw_dir / "AMZN_datawindow.json").write_text(
        json.dumps({"Close": "317.31", "Exp Move % (21b)": "9.90"}), encoding="utf-8"
    )
    # Embedded block path (priority 1) with an unscaled credit short strike ($300).
    (reports_dir / "AMZN_arbitration.md").write_text(
        """# AMZN | ⚖️ SENIOR PM ARBITRATION
```json:watch_levels
{
  "ticker": "AMZN",
  "verdict": "ENTER",
  "conviction": 8,
  "shares_plan": {
    "entry_type": "LIMIT",
    "entry_zone_low": 300.0,
    "entry_zone_high": 310.0,
    "tactical_stop": 295.0,
    "target_1": 340.0,
    "target_2": 360.0
  },
  "options_plan": {
    "structure": "BULL_PUT_SPREAD",
    "expiration": "2026-10-16",
    "long_strike": 290.0,
    "short_strike": 300.0,
    "target_credit": 4.5,
    "max_loss": 950.0,
    "max_profit": 450.0,
    "summary": "Oct 16 $300P/$290P Bull Put Spread"
  }
}
```
""",
        encoding="utf-8",
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("AMZN", "2026-09-17")
    assert res is not None
    assert res["options_plan"]["structure"] == "NONE"
    assert res["options_plan"]["actionable"] is False
    assert res["options_plan"]["short_strike"] == 0.0
    ts = res.get("options_menu", {}).get("tactical_spread", {})
    assert ts.get("structure") == "NONE"


def test_sync_reports_to_watchlist_skips_tastytrade_on_gate_rejection():
    """Verify Tastytrade alerts are NOT set and upsert is skipped if the report levels fail the validation gate."""
    from run_watch_alerts import sync_reports_to_watchlist

    fake_data = {
        "ticker": "BADTICKER",
        "date": "2026-09-23",
        "side": "LONG",
        "shares_plan": {
            "entry_zone_low": 100.0,
            "entry_zone_high": 105.0,
            "tactical_stop": 110.0,  # inverted stop!
            "target_1": 120.0,
        },
    }

    mock_dw = {"Close": 100.0, "Long Stop Loss": 95.0, "Long Target": 120.0, "ATR": 2.0, "Action Code": 20, "setup_lane": "RR_SETUP"}
    with patch("run_watch_alerts.extract_watch_levels_from_report", return_value=fake_data), \
         patch("run_watch_alerts.upsert_watch_target") as mock_upsert, \
         patch("run_watch_alerts.TastytradeClient") as mock_tt_cls, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=json.dumps(mock_dw)):
    
        mock_tt = MagicMock()
        mock_tt_cls.return_value = mock_tt

        count = sync_reports_to_watchlist(target_date="2026-09-23", target_ticker="BADTICKER", sync_tastytrade=True)
        # Rejected plans are not upserted to watchlist
        assert count == 0
        assert fake_data["verdict"] == "REJECTED_BY_GATE"
        mock_upsert.assert_not_called()
        mock_tt.sync_watch_levels.assert_not_called()


def test_sync_reports_to_watchlist_defaults_tastytrade_disabled():
    from run_watch_alerts import sync_reports_to_watchlist
    from unittest.mock import patch, MagicMock

    fake_data = {
        "ticker": "GOODTICKER",
        "date": "2026-09-23",
        "side": "LONG",
        "current_price": 100.5,
        "atr_14": 3.0,
        "shares_plan": {
            "entry_zone_low": 100.0,
            "entry_zone_high": 101.0,
            "tactical_stop": 95.0,
            "target_1": 115.0,
        },
    }

    mock_dw = {"Close": 100.0, "Long Stop Loss": 95.0, "Long Target": 115.0, "ATR": 3.0, "Action Code": 20, "setup_lane": "RR_SETUP"}
    with patch("run_watch_alerts.extract_watch_levels_from_report", return_value=fake_data), \
         patch("run_watch_alerts.upsert_watch_target") as mock_upsert, \
         patch("run_watch_alerts.TastytradeClient") as mock_tt_cls, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=json.dumps(mock_dw)), \
         patch("src.logic.level_validation.validate_levels", return_value=(True, [])):

        # Default behavior: Tastytrade alerts are NOT generated until explicitly instructed
        count = sync_reports_to_watchlist(target_date="2026-09-23", target_ticker="GOODTICKER")
        assert count == 1
        mock_upsert.assert_called_once()
        mock_tt_cls.assert_not_called()




