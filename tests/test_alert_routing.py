from unittest.mock import MagicMock, patch
import pytest

from src.tracking.position_monitor import PositionManager
from src.tracking import position_state
from src.tracking.sheets_tracker import _format_triage_label


def test_position_manager_rejects_neutral_and_screener_alerts(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        mgr = PositionManager(poll_interval=10)
        mgr._ensure_monitor = MagicMock()

        # 1. Alert with side NEUTRAL (e.g. Put OK / Stagnation screener)
        neutral_alert = {
            "symbol": "AAPL",
            "strategy": "Intraday",
            "side": "NEUTRAL",
            "action": "NEUTRAL",
            "alert_price": 220.5,
        }
        mgr._handle_alert(neutral_alert)
        assert "AAPL" not in position_state.load_state()
        mgr._ensure_monitor.assert_not_called()

        # 2. Alert with setup key (screener candidate)
        screener_alert = {
            "symbol": "MSFT",
            "strategy": "Intraday",
            "setup": "Put-Sell Timing (Research)",
            "side": "LONG",
            "alert_price": 410.0,
        }
        mgr._handle_alert(screener_alert)
        assert "MSFT" not in position_state.load_state()
        mgr._ensure_monitor.assert_not_called()

        # 3. Non-intraday strategy
        daily_alert = {
            "symbol": "NVDA",
            "strategy": "Daily",
            "side": "LONG",
            "action": "BUY",
            "alert_price": 120.0,
        }
        mgr._handle_alert(daily_alert)
        assert "NVDA" not in position_state.load_state()
        mgr._ensure_monitor.assert_not_called()


def test_position_manager_accepts_valid_directional_intraday_alerts(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_evaluator.evaluate_risk_vetoes", return_value=None):
        mgr = PositionManager(poll_interval=10)
        mgr._ensure_monitor = MagicMock()

        # Valid LONG entry
        long_alert = {
            "symbol": "AMD",
            "strategy": "Intraday",
            "action": "CALLS",
            "side": "LONG",
            "alert_price": 160.0,
        }
        mgr._handle_alert(long_alert)
        state = position_state.load_state()
        assert "AMD" in state
        assert state["AMD"]["side"] == "LONG"
        mgr._ensure_monitor.assert_called_with("AMD")

        # Valid SHORT entry
        short_alert = {
            "symbol": "INTC",
            "strategy": "Intraday",
            "action": "PUTS",
            "side": "SHORT",
            "alert_price": 20.0,
        }
        mgr._handle_alert(short_alert)
        state = position_state.load_state()
        assert "INTC" in state
        assert state["INTC"]["side"] == "SHORT"
        mgr._ensure_monitor.assert_called_with("INTC")


def test_format_triage_label_income_modes():
    # Directional
    assert _format_triage_label({"triage": "PASS", "conviction": 7, "entry_mode": "TREND_LONG"}) == "PASS (7/10)"
    assert _format_triage_label({"triage": "CUT"}) == "CUT"

    # Income CSP
    assert (
        _format_triage_label({"triage": "PASS", "conviction": 6, "entry_mode": "INCOME_CSP"})
        == "PASS [CSP] (6/10)"
    )
    # Income CC
    assert (
        _format_triage_label({"triage": "WATCH", "conviction": 5, "entry_mode": "INCOME_CC"})
        == "WATCH [CC] (5/10)"
    )
    # Structure fallback
    assert (
        _format_triage_label({
            "triage": "WATCH",
            "structure": "cash_secured_put_or_put_credit",
            "conviction": 6,
            "reasoning": "some reasoning",
        })
        == "WATCH [CSP] (6/10)"
    )


def test_position_manager_handles_exit_events_even_with_neutral_side(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        mgr = PositionManager(poll_interval=10)
        mgr._stop_monitor = MagicMock()

        # Pre-populate open position
        position_state.open_position("META", side="LONG", strategy="Intraday", entry_price=500.0)
        assert "META" in position_state.load_state()

        # Exit alert carrying side: NEUTRAL
        exit_alert = {
            "symbol": "META",
            "strategy": "Intraday",
            "action": "EXIT",
            "side": "NEUTRAL",
            "alert_price": 510.0,
        }
        mgr._handle_alert(exit_alert)
        assert "META" not in position_state.load_state()
        mgr._stop_monitor.assert_called_with("META")


def test_intraday_trade_execution_alerts_parsed_and_routed(tmp_path):
    from src.logic.alert_parser import parse_alert

    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_evaluator.evaluate_risk_vetoes", return_value=None):
        mgr = PositionManager(poll_interval=10)
        mgr._ensure_monitor = MagicMock()
        mgr._stop_monitor = MagicMock()

        # 1. Intraday entry payload
        body_entry = '{"event":"ENTRY", "action":"ENTER_PUTS", "ticker":"CAT", "verdict":"BUY PUTS", "plan":"Held 822.74 · Stop 825.26 · T1 817.73", "act_now":"YES — entered PUTS"}'
        parsed_entry = parse_alert("Alert: Intraday", body_entry)
        assert parsed_entry["symbol"] == "CAT"
        assert parsed_entry["action"] == "ENTER_PUTS"
        assert parsed_entry["strategy"] == "Intraday"

        # Provide today's timestamp so the prior-day guard passes
        from datetime import datetime
        from zoneinfo import ZoneInfo
        parsed_entry["timestamp"] = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")

        mgr._handle_alert(parsed_entry)
        state = position_state.load_state()
        assert "CAT" in state
        assert state["CAT"]["side"] == "SHORT"
        assert state["CAT"]["entry_price"] == 822.74
        assert state["CAT"]["stop"] == 825.26
        assert state["CAT"]["target"] == 817.73
        mgr._ensure_monitor.assert_called_with("CAT")

        # 2. Intraday exit payload
        body_exit = '{"event":"EXIT", "action":"EXIT", "ticker":"CAT", "verdict":"STAND ASIDE", "exit_px":827.15, "act_now":"EXIT — catastrophe stop"}'
        parsed_exit = parse_alert("Alert: Intraday", body_exit)
        assert parsed_exit["symbol"] == "CAT"
        assert parsed_exit["action"] == "EXIT"
        assert parsed_exit["strategy"] == "Intraday"

        mgr._handle_alert(parsed_exit)
        assert "CAT" not in position_state.load_state()
        mgr._stop_monitor.assert_called_with("CAT")

        # 3. Screener alert must be Daily and never open positions
        parsed_screener = parse_alert("Alert: Screener", body_entry)
        assert parsed_screener["strategy"] == "Daily"
        mgr._handle_alert(parsed_screener)
        assert "CAT" not in position_state.load_state()


def test_rsi2_alert_parsing_and_isolation():
    from src.logic.alert_parser import parse_alert

    # RSI2 SETUP text alert as generated by Pine
    subject = "TradingView Alert: AMZN"
    body_setup = (
        "RSI2 SETUP: next opening <= 251.50; SL 233.70; target 282.60. "
        "Opening-only model, not an unconditional market order."
    )
    parsed = parse_alert(subject, body_setup)

    assert parsed["symbol"] == "AMZN"
    assert parsed["strategy"] == "RSI2"
    assert parsed["action"] == "SETUP"
    assert parsed["side"] == "LONG"
    assert parsed["opening_ceiling"] == 251.50
    assert parsed["alert_price"] == 251.50
    assert parsed["stop_loss"] == 233.70
    assert parsed["target"] == 282.60

    # RSI2 RECOVERY alert
    body_recovery = "RSI2 RECOVERY: exit at next opening, with protective gap stop/target precedence."
    parsed_rec = parse_alert(subject, body_recovery)

    assert parsed_rec["symbol"] == "AMZN"
    assert parsed_rec["strategy"] == "RSI2"
    assert parsed_rec["action"] == "EXIT"
    assert parsed_rec["exit_reason"] == "RECOVERY"

