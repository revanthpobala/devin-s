"""
Regression tests for the screener market-tide key mismatch.

Bug: src/ui/routes/screener.py read market_tide["bullish"] while check_market_tide returns
"is_bullish". The miss always fell through to a hardcoded True, so the API answered
bullish: true next to trend_str "DEFENSIVE / BEARISH".
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.screener.schwab_pre_move_scan import check_market_tide
from src.ui.routes import screener as screener_routes


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _spy_client(closes):
    return MagicMock(**{
        "get_price_history_every_day.return_value": _Resp(
            {"candles": [{"close": c} for c in closes]}
        )
    })


def _bearish_closes(n=60):
    # Steady 1%/day bleed in chronological order: close sits under both the 20 EMA and the 50 SMA.
    return [100.0 * (0.99 ** i) for i in range(n)]


def test_check_market_tide_emits_is_bullish_not_bullish():
    tide = check_market_tide(_spy_client(_bearish_closes()))
    assert "is_bullish" in tide
    assert "bullish" not in tide, "a 'bullish' key would let a silent-miss fallback read as True"
    assert tide["is_bullish"] is False
    assert tide["trend_str"] == "DEFENSIVE / BEARISH"


def test_check_market_tide_failure_path_has_same_keys():
    """Callers f-format last_px/ema20/sma50, so a partial dict raises TypeError instead of degrading."""
    good = set(check_market_tide(_spy_client([100.0 + i for i in range(60)])).keys())
    dead = MagicMock(**{"get_price_history_every_day.return_value": _Resp({}, 500)})
    bad = set(check_market_tide(dead).keys())
    assert good == bad
    for key in ("last_px", "ema20", "sma50"):
        assert f"{check_market_tide(dead)[key]:.2f}" == "0.00"


def test_check_market_tide_unknown_is_not_bullish():
    tide = check_market_tide(MagicMock(**{"get_price_history_every_day.return_value": _Resp({}, 500)}))
    assert tide["is_bullish"] is False
    assert tide["available"] is False
    assert tide["trend_str"] == "UNKNOWN"


def _call_route(tide: dict) -> dict:
    with patch(
        "src.screener.schwab_pre_move_scan.get_schwab_client", return_value=MagicMock()
    ), patch("src.screener.schwab_pre_move_scan.check_market_tide", return_value=tide):
        return screener_routes.get_schwab_screener_candidates(date="2099-01-01", side="long")


def test_screener_route_reports_bearish_tide_as_not_bullish():
    """The exact regression: a mocked bearish tide must come back bullish: false."""
    out = _call_route(
        {
            "last_px": 90.0,
            "ema20": 95.0,
            "sma50": 100.0,
            "is_bullish": False,
            "is_neutral": False,
            "available": True,
            "trend_str": "DEFENSIVE / BEARISH",
            "spy_20d_return": -0.12,
            "candles": [],
        }
    )
    assert out["market_tide"]["bullish"] is False
    assert out["market_tide"]["trend_str"] == "DEFENSIVE / BEARISH"
    assert out["market_tide"]["available"] is True


def test_screener_route_bullish_tide_still_true():
    out = _call_route(
        {
            "last_px": 110.0, "ema20": 105.0, "sma50": 100.0,
            "is_bullish": True, "is_neutral": True, "available": True,
            "trend_str": "BULLISH (TIDE ON)", "spy_20d_return": 0.05, "candles": [],
        }
    )
    assert out["market_tide"]["bullish"] is True


def test_screener_route_tide_failure_does_not_report_bullish():
    """An unreachable tide must read UNKNOWN, not a fabricated confirmed uptrend."""
    with patch(
        "src.screener.schwab_pre_move_scan.get_schwab_client",
        side_effect=RuntimeError("no auth"),
    ):
        out = screener_routes.get_schwab_screener_candidates(date="2099-01-01", side="long")
    assert out["market_tide"]["bullish"] is False
    assert out["market_tide"]["available"] is False
    assert out["market_tide"]["trend_str"] == "UNKNOWN"