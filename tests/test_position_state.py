from datetime import datetime, timedelta
from unittest.mock import patch

from src.tracking import position_state


def test_open_and_load_position(tmp_path):
    fake_positions = tmp_path / "positions.json"

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Verify initial empty state
        state = position_state.load_state()
        assert state == {}

        # Open position
        rec = position_state.open_position(
            "AAPL", side="LONG", strategy="Intraday", entry_price=150.0, stop=145.0, target=160.0
        )

        assert rec["ticker"] == "AAPL"
        assert rec["side"] == "LONG"
        assert rec["entry_price"] == 150.0

        # Verify saved state
        state_after = position_state.load_state()
        assert "AAPL" in state_after
        assert state_after["AAPL"]["target"] == 160.0


def test_close_position(tmp_path):
    fake_positions = tmp_path / "positions.json"

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position("TSLA", side="SHORT", strategy="Swing", entry_price=200.0)
        assert "TSLA" in position_state.load_state()

        closed = position_state.close_position("TSLA")
        assert closed is not None
        assert closed["ticker"] == "TSLA"
        assert "TSLA" not in position_state.load_state()


def test_quantity_aware_scale_position(tmp_path):
    fake_positions = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Open 200 shares
        rec = position_state.open_position(
            "MSFT", side="LONG", strategy="Intraday", entry_price=400.0, stop=390.0, target=420.0,
            quantity=200.0,
        )
        assert rec["quantity"] == 200.0
        assert rec["remaining_quantity"] == 200.0

        # Scale 50% at 410.0
        scaled = position_state.scale_position("MSFT", scale_pct=0.5, fill_price=410.0)
        assert scaled is not None
        assert scaled["scaled_at_t1"] is True
        assert scaled["remaining_quantity"] == 100.0
        # 100 shs * ($410 - $400) = $1,000 realized
        assert scaled["realized_pnl"] == 1000.0
        assert scaled["stop"] == 400.05  # BE + 0.05


def test_eod_flatten_force_closes_today_intraday(tmp_path):
    """Regression: the RTH-close backstop must flatten intraday positions opened TODAY.

    The default force=False path filters through is_old (opened_at not today), which
    excludes exactly the positions the EOD flatten exists for. Orchestrator calls it
    with force=True; this pins that behavior."""
    fake_positions = tmp_path / "positions.json"

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "NVDA", side="LONG", strategy="Intraday", entry_price=100.0, stop=95.0, target=110.0
        )
        assert "NVDA" in position_state.load_state()

        # force=True (orchestrator RTH-close call site) must flatten today's intraday trade
        closed = position_state.flatten_eod_intraday_positions(force=True)
        assert [c["ticker"] for c in closed] == ["NVDA"]
        assert closed[0]["exit_reason"] == "EOD Flatten"
        assert "NVDA" not in position_state.load_state()


def test_eod_flatten_no_force_skips_today_intraday(tmp_path):
    """force=False keeps the documented 'older than today' semantics: a fresh intraday
    position is NOT flattened, but an old one is."""
    fake_positions = tmp_path / "positions.json"

    with patch.object(position_state, "POSITIONS_FILE", fake_positions):
        # Opened today — must survive a non-forced flatten
        position_state.open_position(
            "AMD", side="LONG", strategy="Intraday", entry_price=50.0, stop=48.0, target=55.0
        )
        # Opened yesterday — must be flattened
        yesterday = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        state = position_state.load_state()
        state["AMD"]["opened_at"] = yesterday
        position_state._save_state(state)

        closed = position_state.flatten_eod_intraday_positions(force=False)
        assert [c["ticker"] for c in closed] == ["AMD"]

