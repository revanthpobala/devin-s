import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
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
            assert "Catastrophic stop safeguard breached" in decision["reason"]

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
            stop=197.0,  # $3.00 initial risk (1.5% < 2.5% flat leg)
        )

        # Price at 196.5 is -1.75% from entry: below the 2.5% flat leg AND past
        # the full $3.00 ATR-stop distance -> Tier 2 catastrophic breaker fires
        # on the ATR leg.
        exit_alert = {
            "symbol": "AAPL",
            "action": "EXIT",
            "alert_price": 196.5,
            "strategy": "Intraday",
        }

        with patch("src.tracking.position_monitor.get_current_price", return_value=196.5):
            decision = review_tv_exit("AAPL", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "Catastrophic stop safeguard breached" in decision["reason"]


def test_review_tv_exit_catastrophic_wide_stop_within_risk_budget(tmp_path):
    """Tier 2 must NOT fire on a wide-ATR setup when the drawdown is inside the
    designed risk budget (stop distance). The trade keeps running to its real stop."""
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "AAPL",
            side="LONG",
            strategy="Intraday",
            entry_price=200.0,
            stop=190.0,  # Wide $10.00 (5%) initial risk — wide-ATR setup
        )

        # -3.0% drawdown: above the old flat 2.5% breaker, but well inside the
        # $10.00 stop distance and price still holds above stop -> VETO_HOLD.
        exit_alert = {
            "symbol": "AAPL",
            "action": "EXIT",
            "alert_price": 194.0,
            "strategy": "Intraday",
        }

        with patch("src.tracking.position_monitor.get_current_price", return_value=194.0):
            decision = review_tv_exit("AAPL", exit_alert)
            assert decision["action"] == "VETO_HOLD"
            assert "Catastrophic" not in decision["reason"]

        # Once price breaks the real stop, it exits via catastrophic breaker (ATR leg).
        with patch("src.tracking.position_monitor.get_current_price", return_value=189.5):
            decision = review_tv_exit("AAPL", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "Catastrophic stop safeguard breached" in decision["reason"]


def test_review_tv_exit_catastrophic_option_premium_leg(tmp_path):
    """Option trades use the 30% premium-loss leg instead of the flat 2.5%."""
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Wide stop distance ($1.00 on a $5.00 premium) — only the premium leg fires.
        position_state.open_position(
            "SPY260922C4800",
            side="LONG",
            strategy="Intraday",
            entry_price=5.00,
            stop=4.00,
            instrument_type="OPTION",
        )

        # -30% premium loss ($3.50): inside the $1.00 ATR distance, so only the
        # 30% premium leg can fire.
        exit_alert = {
            "symbol": "SPY260922C4800",
            "action": "EXIT",
            "alert_price": 3.50,
            "strategy": "Intraday",
        }

        with patch("src.tracking.position_monitor.get_current_price", return_value=3.50):
            decision = review_tv_exit("SPY260922C4800", exit_alert)
            assert decision["action"] == "CONFIRM_EXIT"
            assert "premium loss" in decision["reason"]


def test_position_monitor_catastrophic_breaker_uses_atr_stop_distance(tmp_path):
    """The monitor's breaker must respect the ATR risk budget: a -3% drawdown on a
    wide-ATR setup (stop distance 5%) is NOT catastrophic; breaking the real stop is."""
    fake_positions = tmp_path / "positions.json"
    fake_now = datetime(2026, 9, 17, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_db.get_eastern_now", return_value=fake_now):
        position_state.open_position(
            "TSLA",
            side="LONG",
            strategy="Intraday",
            entry_price=200.0,
            stop=190.0,  # $10.00 (5%) initial risk — wide-ATR setup
            target=215.0,
        )

        monitor = PositionMonitor("TSLA", poll_interval=1)
        monitor._eval_playbook = MagicMock(return_value="Hold")

        # -3.0% drawdown: inside the designed risk budget and above stop -> stays open.
        with patch("src.tracking.position_monitor.get_current_price", return_value=194.0):
            monitor._tick()
        state = position_state.load_state()
        assert "TSLA" in state, "wide-ATR -3% drawdown must NOT trip the catastrophic breaker"

        # Price breaks the real stop -> autonomous stop close (not the flat breaker).
        with patch("src.tracking.position_monitor.get_current_price", return_value=189.5):
            monitor._tick()
        state = position_state.load_state()
        assert "TSLA" not in state


def test_position_monitor_autonomous_profit_locking_trailing_stop(tmp_path):
    fake_positions = tmp_path / "positions.json"
    fake_now = datetime(2026, 9, 17, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_db.get_eastern_now", return_value=fake_now):
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
        # Stop must be autonomously moved to break-even (100.0 or 100.05)
        assert state["AMD"]["stop"] in (100.0, 100.05)
        assert any(k in state["AMD"]["last_eval"] for k in ("Break-Even", "BE+", "Target hit"))


def test_position_monitor_autonomous_stop_breach_close(tmp_path):
    fake_positions = tmp_path / "positions.json"
    fake_now = datetime(2026, 9, 17, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_db.get_eastern_now", return_value=fake_now):
        position_state.open_position(
            "AMD",
            side="LONG",
            strategy="Intraday",
            entry_price=180.0,
            stop=178.0,
            target=185.0,
        )

        monitor = PositionMonitor("AMD", poll_interval=1)
        monitor._eval_playbook = MagicMock()

        # Price dropped to 177.50 (below stop 178.0)
        with patch("src.tracking.position_monitor.get_current_price", return_value=177.50):
            monitor._tick()

        state = position_state.load_state()
        # Position must be autonomously closed by monitor!
        assert "AMD" not in state
        assert monitor._stop.is_set()


def test_position_monitor_early_gain_traction_breakeven_ratchet(tmp_path):
    """Test that a trade achieving +$100 unrealized gain (or 0.5R) ratchets stop to BE+ 0.05 immediately."""
    fake_positions = tmp_path / "positions.json"
    fake_now = datetime(2026, 9, 17, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_db.get_eastern_now", return_value=fake_now):
        # Entry 500.0, Stop 496.0 ($4.00 initial risk), Target 508.0 ($8.00 reward)
        position_state.open_position(
            "NVDA",
            side="LONG",
            strategy="Intraday",
            entry_price=500.0,
            stop=496.0,
            target=508.0,
        )

        monitor = PositionMonitor("NVDA", poll_interval=1)
        monitor._eval_playbook = MagicMock(return_value="Hold")

        # Price climbs to 501.50 (+1.50 = +$150 unrealized gain, before target 508.0)
        with patch("src.tracking.position_monitor.get_current_price", return_value=501.50):
            monitor._tick()

        state = position_state.load_state()
        assert "NVDA" in state
        assert state["NVDA"]["be_locked"] is True
        assert state["NVDA"]["stop"] == 500.05
        assert "Gain traction" in state["NVDA"]["last_eval"]


def test_position_monitor_post_t1_runner_trailing_protection(tmp_path):
    """Test that post-T1 runner trails stop to protect at least 65% of peak gains."""
    fake_positions = tmp_path / "positions.json"
    fake_now = datetime(2026, 9, 17, 10, 15, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(position_state, "POSITIONS_FILE", fake_positions), \
         patch("src.tracking.alert_db.get_eastern_now", return_value=fake_now):
        # Position already scaled at T1
        position_state.open_position(
            "AAPL",
            side="LONG",
            strategy="Intraday",
            entry_price=300.0,
            stop=300.05,
            target=302.0,
            scaled_at_t1=True,
            peak_price=304.0,  # Peak was +$4.00 (+$400 gain)
        )

        monitor = PositionMonitor("AAPL", poll_interval=1)
        monitor._eval_playbook = MagicMock(return_value="Hold")

        # Current price pulls back slightly to 303.50
        with patch("src.tracking.position_monitor.get_current_price", return_value=303.50):
            monitor._tick()

        state = position_state.load_state()
        assert "AAPL" in state
        # 65% of $4.00 peak is $2.60 -> Stop must trail to 300.0 + 2.60 = 302.60
        assert state["AAPL"]["stop"] == 302.60
        assert "Runner profit locked" in state["AAPL"]["last_eval"]


def test_evaluate_risk_vetoes_grade_b():
    """Verify that sub-Grade-B (score < 65) alerts are hard-vetoed; Grade-B 65-79 passes to LLM."""
    from src.tracking.alert_evaluator import evaluate_risk_vetoes
    dt = datetime(2026, 9, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    # Score 64 (Grade C territory) -> hard veto
    res = evaluate_risk_vetoes(
        symbol="AAPL",
        action="ENTER_CALLS",
        score=64,
        current_time_et="10:00 AM ET",
        eastern_dt=dt,
        grade="C",
    )
    assert res is not None
    hdr, pb = res
    assert "GRADE B / LOW CONVICTION" in hdr
    assert "QUALITY VETO" in pb

    # Score 75 (Grade B range) -> passes through to the LLM for the catalyst call
    res_b = evaluate_risk_vetoes(
        symbol="AAPL",
        action="ENTER_CALLS",
        score=75,
        current_time_et="10:00 AM ET",
        eastern_dt=dt,
        grade="B",
    )
    assert res_b is None, "Grade-B 65-79 must pass the quality gate (new default 65)."


def test_evaluate_risk_vetoes_weinstein_stage_alignment():
    """Verify that counter-trend trades against Stan Weinstein stages are vetoed."""
    from src.tracking.alert_evaluator import evaluate_risk_vetoes
    dt = datetime(2026, 9, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    
    with patch("src.tracking.position_state.list_open", return_value={}):
        # 1. CALLS into Stage 4 Decline -> VETOED
        res_call_stg4 = evaluate_risk_vetoes(
            symbol="TSLA",
            action="ENTER_CALLS",
            score=95,
            current_time_et="10:00 AM ET",
            eastern_dt=dt,
            grade="A",
            align="W ↓ D ↓ Stg4 decline",
        )
        assert res_call_stg4 is not None
        hdr, pb = res_call_stg4
        assert "COUNTER-STAGE: STAGE 4 DECLINE" in hdr
        assert "REGIME VETO" in pb

        # 2. PUTS into Stage 2 Advance -> VETOED
        res_put_stg2 = evaluate_risk_vetoes(
            symbol="JPM",
            action="ENTER_PUTS",
            score=95,
            current_time_et="10:00 AM ET",
            eastern_dt=dt,
            grade="A",
            align="W ↑ D ↑ Stg2 advance",
        )
        assert res_put_stg2 is not None
        hdr, pb = res_put_stg2
        assert "COUNTER-STAGE: STAGE 2 ADVANCE" in hdr
        assert "REGIME VETO" in pb

        # 3. CALLS into Stage 2 Advance -> PERMITTED (Aligned with trend)
        res_call_stg2 = evaluate_risk_vetoes(
            symbol="NVDA",
            action="ENTER_CALLS",
            score=95,
            current_time_et="10:00 AM ET",
            eastern_dt=dt,
            grade="A",
            align="W ↑ D ↑ Stg2 advance",
        )
        assert res_call_stg2 is None

        # 4. PUTS into Stage 4 Decline -> PERMITTED (Aligned with trend)
        res_put_stg4 = evaluate_risk_vetoes(
            symbol="TSLA",
            action="ENTER_PUTS",
            score=95,
            current_time_et="10:00 AM ET",
            eastern_dt=dt,
            grade="A",
            align="W ↓ D ↓ Stg4 decline",
        )
        assert res_put_stg4 is None


def test_evaluate_risk_vetoes_mid_morning_trap():
    """Verify that 10:30-11:30 ET mid-morning extension alerts require Score >= MID_MORNING_MIN_SCORE (default 85)."""
    from src.tracking.alert_evaluator import evaluate_risk_vetoes
    # 10:45 AM ET (in the trap window)
    dt = datetime(2026, 9, 17, 10, 45, tzinfo=ZoneInfo("America/New_York"))

    with patch("src.tracking.position_state.list_open", return_value={}):
        # Score 84 should be vetoed in mid-morning trap (below default 85)
        res = evaluate_risk_vetoes(
            symbol="AAPL",
            action="ENTER_CALLS",
            score=84,
            current_time_et="10:45 AM ET",
            eastern_dt=dt,
            grade="A",
        )
        assert res is not None
        hdr, pb = res
        assert "10:30-11:30 ET EXHAUSTION TRAP" in hdr

        # Score 85 should now pass (A-grade 85-89 unlocked)
        res_pass = evaluate_risk_vetoes(
            symbol="AAPL",
            action="ENTER_CALLS",
            score=85,
            current_time_et="10:45 AM ET",
            eastern_dt=dt,
            grade="A",
        )
        assert res_pass is None

        # Score 92 should pass
        res_high = evaluate_risk_vetoes(
            symbol="AAPL",
            action="ENTER_CALLS",
            score=92,
            current_time_et="10:45 AM ET",
            eastern_dt=dt,
            grade="A",
        )
        assert res_high is None


def test_review_tv_exit_confirms_strategic_exits(tmp_path):
    """Verify that structural/strategic exits (bias flipped, chop stall, EOD flat, etc.)
    are immediately confirmed and NEVER mistakenly vetoed as intra-bar wick noise."""
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Open LONG position: Entry 500.0, Stop 490.0
        position_state.open_position(
            "MSFT",
            side="LONG",
            strategy="Intraday",
            entry_price=500.0,
            stop=490.0,
            target=520.0,
        )

        # 1. Bias flipped exit while price is at 505.0 (above stop)
        bias_exit = {
            "symbol": "MSFT",
            "action": "EXIT",
            "exit_why": "bias flipped",
            "alert_price": 505.0,
            "strategy": "Intraday",
        }
        with patch("src.tracking.position_monitor.get_current_price", return_value=505.0):
            res = review_tv_exit("MSFT", bias_exit)
            assert res["action"] == "CONFIRM_EXIT"
            assert "strategic exit" in res["reason"]

        # 2. Chop stall exit while price is above stop
        chop_exit = {
            "symbol": "MSFT",
            "action": "EXIT",
            "exit_why": "chop stall — no follow-through",
            "alert_price": 502.0,
            "strategy": "Intraday",
        }
        with patch("src.tracking.position_monitor.get_current_price", return_value=502.0):
            res = review_tv_exit("MSFT", chop_exit)
            assert res["action"] == "CONFIRM_EXIT"

        # 3. EOD flat (0DTE) exit
        eod_exit = {
            "symbol": "MSFT",
            "action": "EXIT",
            "exit_why": "EOD flat (0DTE)",
            "alert_price": 503.0,
            "strategy": "Intraday",
        }
        with patch("src.tracking.position_monitor.get_current_price", return_value=503.0):
            res = review_tv_exit("MSFT", eod_exit)
            assert res["action"] == "CONFIRM_EXIT"


def test_position_manager_vetoes_low_grade_and_counter_stage_before_opening(tmp_path):
    """Verify that PositionManager._handle_alert rejects sub-Grade-B (score < 65) or
    counter-stage alerts and never opens them into position_state."""
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        mgr = PositionManager(poll_interval=10)
        mgr._ensure_monitor = MagicMock()

        # 1. Score 62 (true Grade C territory) -> VETOED, not opened
        low_grade_alert = {
            "symbol": "TSLA",
            "strategy": "Intraday",
            "action": "CALLS",
            "side": "LONG",
            "grade": "C",
            "score": 62,
            "alert_price": 220.0,
        }
        mgr._handle_alert(low_grade_alert)
        state = position_state.load_state()
        assert "TSLA" not in state
        mgr._ensure_monitor.assert_not_called()

        # 2. Counter-stage alert (CALL into Stage 4 Decline) -> VETOED, not opened
        counter_stage_alert = {
            "symbol": "NVDA",
            "strategy": "Intraday",
            "action": "CALLS",
            "side": "LONG",
            "grade": "A",
            "score": 95,
            "align": "W ↓ D ↓ Stg4 decline",
            "alert_price": 115.0,
        }
        mgr._handle_alert(counter_stage_alert)
        state = position_state.load_state()
        assert "NVDA" not in state
        mgr._ensure_monitor.assert_not_called()


