import pytest
from datetime import datetime, timezone
from unittest.mock import patch


def _candle(ts_min: int, o: float, h: float, l: float, c: float, v: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "datetime": ts_min * 60000}


def _now_min() -> int:
    return int(datetime.now(timezone.utc).timestamp()) // 60000


# ---------------------------------------------------------------------------
# read_tape unit tests (mock get_intraday_candles)
# ---------------------------------------------------------------------------

def test_read_tape_insufficient_data():
    from src.plugins.order_flow_plugin import read_tape
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", return_value=[]):
        result = read_tape("SPY")
    assert result == {"verdict": "insufficient data"}


def test_read_tape_never_raises():
    from src.plugins.order_flow_plugin import read_tape
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", side_effect=RuntimeError("boom")):
        result = read_tape("SPY")
    assert "error" in result["verdict"]


def test_read_tape_aggressive_buying():
    from src.plugins.order_flow_plugin import read_tape
    now = _now_min()
    # 12 baseline candles: mild buying (close near high → pressure ~0.8)
    base = [_candle(now - 18 + i, 10.0, 10.5, 9.9, 10.4, 100) for i in range(12)]
    # 3 recent candles: close pinned to high (pressure 1.0), high volume
    recent = [_candle(now - 6 + i, 10.0, 10.5, 9.9, 10.5, 1000) for i in range(3)]
    candles = base + recent
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", return_value=candles):
        result = read_tape("SPY")
    # pressure ≈ (12*0.833 + 3*1.0) / 15 ≈ 0.867; vol_accel = 3000/300 = 10
    assert result["pressure"] > 0.6, f"expected buying pressure, got {result['pressure']}"
    assert "aggressive buying" in result["verdict"], result


def test_read_tape_aggressive_selling():
    from src.plugins.order_flow_plugin import read_tape
    now = _now_min()
    # 12 baseline candles: mild selling (close near low → pressure ~0.167)
    base = [_candle(now - 18 + i, 10.0, 10.5, 9.9, 10.0, 100) for i in range(12)]
    # 3 recent candles: close pinned to low (pressure 0.0), high volume
    recent = [_candle(now - 6 + i, 10.0, 10.5, 9.5, 9.5, 1000) for i in range(3)]
    candles = base + recent
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", return_value=candles):
        result = read_tape("SPY")
    # pressure ≈ (12*0.167 + 3*0.0) / 15 ≈ 0.133; vol_accel = 10
    assert result["pressure"] < 0.4, f"expected selling pressure, got {result['pressure']}"
    assert "aggressive selling" in result["verdict"], result


def test_read_tape_balanced():
    from src.plugins.order_flow_plugin import read_tape
    now = _now_min()
    candles = [_candle(now - 15 + i, 10.0, 10.1, 9.9, 10.0, 100) for i in range(14)]
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", return_value=candles):
        result = read_tape("SPY")
    assert "balanced" in result["verdict"], result


def test_read_tape_drops_in_flight_candle():
    """The last candle (timestamp == now) must be excluded from pressure calc."""
    from src.plugins.order_flow_plugin import read_tape
    now = _now_min()
    # 13 completed candles: close at high (strong buying) → pressure ≈ 1.0
    done = [_candle(now - 14 + i, 10.0, 10.5, 9.9, 10.5, 100) for i in range(13)]
    # 1 in-flight candle (timestamp == now): close at low → would drag pressure down
    in_flight = _candle(now, 10.5, 10.5, 9.5, 9.5, 100)
    candles = done + [in_flight]
    with patch("src.plugins.order_flow_plugin.get_intraday_candles", return_value=candles):
        result = read_tape("SPY")
    # If the in-flight candle were included, pressure would be pulled below 0.6
    assert result["pressure"] > 0.6, f"in-flight candle not dropped: pressure={result['pressure']}"


# ---------------------------------------------------------------------------
# review_tv_exit tape override tests
# ---------------------------------------------------------------------------

def _open_long(tmp_path, symbol="MSFT", entry=500.0, stop=499.70, target=505.0):
    from src.tracking import position_state
    fake = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake):
        position_state.open_position(symbol, side="LONG", strategy="Intraday",
                                     entry_price=entry, stop=stop, target=target)
        return position_state


def test_tape_override_confirms_exit_on_aggressive_selling(tmp_path):
    from src.tracking.position_monitor import review_tv_exit
    ps = _open_long(tmp_path)
    exit_alert = {"symbol": "MSFT", "action": "EXIT", "alert_price": 499.66, "strategy": "Intraday"}
    with patch.object(ps, "POSITIONS_FILE", tmp_path / "positions.json"):
        with patch("src.tracking.position_monitor.get_current_price", return_value=499.85):
            with patch("src.plugins.order_flow_plugin.read_tape",
                       return_value={"pressure": 0.2, "vol_accel": 2.1,
                                     "verdict": "aggressive selling, volume accelerating"}):
                decision = review_tv_exit("MSFT", exit_alert)
    assert decision["action"] == "CONFIRM_EXIT"
    assert "Tape override" in decision["reason"]


def test_tape_override_holds_on_quiet_tape(tmp_path):
    from src.tracking.position_monitor import review_tv_exit
    ps = _open_long(tmp_path)
    exit_alert = {"symbol": "MSFT", "action": "EXIT", "alert_price": 499.66, "strategy": "Intraday"}
    with patch.object(ps, "POSITIONS_FILE", tmp_path / "positions.json"):
        with patch("src.tracking.position_monitor.get_current_price", return_value=499.85):
            with patch("src.plugins.order_flow_plugin.read_tape",
                       return_value={"pressure": 0.5, "vol_accel": 1.0,
                                     "verdict": "balanced tape, low conviction"}):
                decision = review_tv_exit("MSFT", exit_alert)
    assert decision["action"] == "VETO_HOLD"
    assert "Intra-bar wick noise" in decision["reason"]


def test_tape_override_falls_back_on_error(tmp_path):
    from src.tracking.position_monitor import review_tv_exit
    ps = _open_long(tmp_path)
    exit_alert = {"symbol": "MSFT", "action": "EXIT", "alert_price": 499.66, "strategy": "Intraday"}
    with patch.object(ps, "POSITIONS_FILE", tmp_path / "positions.json"):
        with patch("src.tracking.position_monitor.get_current_price", return_value=499.85):
            with patch("src.plugins.order_flow_plugin.read_tape",
                       side_effect=RuntimeError("schwab down")):
                decision = review_tv_exit("MSFT", exit_alert)
    # read_tape error → falls through to VETO_HOLD (no change from existing behavior)
    assert decision["action"] == "VETO_HOLD"


def test_short_tape_override_confirms_exit_on_aggressive_buying(tmp_path):
    from src.tracking import position_state
    from src.tracking.position_monitor import review_tv_exit
    fake = tmp_path / "positions.json"
    with patch.object(position_state, "POSITIONS_FILE", fake):
        position_state.open_position("TSLA", side="SHORT", strategy="Intraday",
                                     entry_price=200.0, stop=201.0, target=195.0)
        exit_alert = {"symbol": "TSLA", "action": "EXIT", "alert_price": 201.20, "strategy": "Intraday"}
        with patch("src.tracking.position_monitor.get_current_price", return_value=200.95):
            with patch("src.plugins.order_flow_plugin.read_tape",
                       return_value={"pressure": 0.8, "vol_accel": 2.1,
                                     "verdict": "aggressive buying, volume accelerating"}):
                decision = review_tv_exit("TSLA", exit_alert)
    assert decision["action"] == "CONFIRM_EXIT"
    assert "Tape override" in decision["reason"]


# ---------------------------------------------------------------------------
# build_live_market_context tape injection
# ---------------------------------------------------------------------------

def test_build_live_market_context_includes_tape():
    from src.tracking.alert_evaluator import build_live_market_context
    with patch("src.tracking.alert_evaluator.TastytradeClient") as mock_tt, \
         patch("src.tracking.alert_evaluator.search_web", return_value=[]), \
         patch("src.tracking.alert_evaluator.fetch_options_chain_tool", return_value=""), \
         patch("src.plugins.order_flow_plugin.read_tape",
               return_value={"pressure": 0.7, "vol_accel": 1.8,
                             "verdict": "aggressive buying, volume accelerating"}) as mock_tape:
        ctx = build_live_market_context("SPY")
    assert "tape" in ctx
    assert "aggressive buying" in ctx["tape"]["verdict"]
