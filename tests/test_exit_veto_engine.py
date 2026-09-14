import pytest
from unittest.mock import MagicMock, patch

from src.tracking import position_state
from src.tracking.position_monitor import PositionManager, PositionMonitor, review_tv_exit


def test_review_tv_exit_vetoes_intrabar_wick_for_long(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Open LONG position: Entry 500.0, Stop 499.70, Target 505.0
        position_state.open_position(
            "MSFT",
            side="LONG",
            strategy="Intraday",
            entry_price=500.0,
            stop=499.70,
            target=505.0,
        )

        # TV fires EXIT alert at 499.66 (11-cent wick below stop)
        exit_alert = {
            "symbol": "MSFT",
            "action": "EXIT",
            "alert_price": 499.66,
            "strategy": "Intraday",
        }

        # Case 1: Live broker quote has rebounded / is holding at 499.85 (above stop 499.70)
        with patch("src.tracking.position_monitor.get_current_price", return_value=499.85):
            decision = review_tv_exit("MSFT", exit_alert)
            assert decision["action"] == "VETO_HOLD"
            assert "Intra-bar wick noise" in decision["reason"]
            assert decision["stop"] == 499.70

        # Now test through PositionManager: position must remain OPEN
        mgr = PositionManager(poll_interval=10)
        mgr._stop_monitor = MagicMock()
        with patch("src.tracking.position_monitor.get_current_price", return_value=499.85):
            mgr._handle_alert(exit_alert)
            state = position_state.load_state()
            assert "MSFT" in state
            assert "VETOED TV EXIT" in state["MSFT"].get("last_eval", "")
            mgr._stop_monitor.assert_not_called()


def test_review_tv_exit_confirms_valid_stop_breach(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Open LONG position: Entry 200.0, Stop 199.0
        position_state.open_position(
            "TSLA",
            side="LONG",
            strategy="Intraday",
            entry_price=200.0,
            stop=199.0,
            target=205.0,
        )

        exit_alert = {
            "symbol": "TSLA",
            "action": "EXIT",
            "alert_price": 197.86,
            "strategy": "Intraday",
        }

        # Live broker quote confirmed below stop at 197.86
        with patch("src.tracking.position_monitor.get_current_price", return_value=197.86):
            decision = review_tv_exit("TSLA", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "Confirmed stop breach" in decision["reason"]

        # PositionManager must close the position
        mgr = PositionManager(poll_interval=10)
        mgr._stop_monitor = MagicMock()
        with patch("src.tracking.position_monitor.get_current_price", return_value=197.86):
            mgr._handle_alert(exit_alert)
            state = position_state.load_state()
            assert "TSLA" not in state
            mgr._stop_monitor.assert_called_with("TSLA")


def test_review_tv_exit_confirms_target_hit(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "NVDA",
            side="LONG",
            strategy="Intraday",
            entry_price=120.0,
            stop=119.0,
            target=123.0,
        )

        exit_alert = {
            "symbol": "NVDA",
            "action": "TAKE PROFIT",
            "alert_price": 123.50,
            "strategy": "Intraday",
        }

        with patch("src.tracking.position_monitor.get_current_price", return_value=123.50):
            decision = review_tv_exit("NVDA", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "Target reached" in decision["reason"]


def test_review_tv_exit_catastrophic_safeguard(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "AAPL",
            side="LONG",
            strategy="Intraday",
            entry_price=200.0,
            stop=190.0,  # Wide stop
        )

        # Alert triggered after >2.5% drop (e.g. price at 194.0 is -3.0%)
        exit_alert = {
            "symbol": "AAPL",
            "action": "EXIT",
            "alert_price": 194.0,
            "strategy": "Intraday",
        }

        with patch("src.tracking.position_monitor.get_current_price", return_value=194.0):
            decision = review_tv_exit("AAPL", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "Catastrophic stop safeguard breached" in decision["reason"]


def test_position_monitor_autonomous_profit_locking_trailing_stop(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Open LONG trade: Entry 100.0, Stop 99.0, Target 102.0
        position_state.open_position(
            "AMD",
            side="LONG",
            strategy="Intraday",
            entry_price=100.0,
            stop=99.0,
            target=102.0,
        )

        monitor = PositionMonitor("AMD", poll_interval=1)
        monitor._eval_playbook = MagicMock(return_value="Hold strong")

        # Live quote hits Target at 102.50
        with patch("src.tracking.position_monitor.get_current_price", return_value=102.50):
            monitor._tick()

        state = position_state.load_state()
        assert "AMD" in state
        # Stop must be autonomously moved to break-even (100.0)
        assert state["AMD"]["stop"] == 100.0
        assert "Stop trailed to Break-Even" in state["AMD"]["last_eval"]


def test_position_monitor_autonomous_stop_breach_close(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "GOOGL",
            side="LONG",
            strategy="Intraday",
            entry_price=180.0,
            stop=178.0,
            target=185.0,
        )

        monitor = PositionMonitor("GOOGL", poll_interval=1)
        monitor._eval_playbook = MagicMock()

        # Price dropped to 177.50 (below stop 178.0)
        with patch("src.tracking.position_monitor.get_current_price", return_value=177.50):
            monitor._tick()

        state = position_state.load_state()
        # Position must be autonomously closed by monitor!
        assert "GOOGL" not in state
        assert monitor._stop.is_set()
