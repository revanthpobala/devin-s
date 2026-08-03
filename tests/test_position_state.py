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
