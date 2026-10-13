"""
tests/test_candle_tool.py

Chat-tool acceptance tests (rig item 3). The chat answers a level question with
deterministic numbers, never model arithmetic:

  1. GOOGL 355 — the tool returns the day's high, whether 355 was crossed, the
     FIRST TOUCH time, whether it held on close, and R:R at market — all as numbers.
  2. The META replay — the same contract on the META plan's levels.
  3. A level above the day's high reports "never touched" rather than a guess.
  4. Intraday candles reuse schwab_client.get_intraday_candles (5m and 1m).
  5. get_candles is registered in TOOLS and dispatched by execute_tool_call.
  6. The chat prompt mandates the call for named levels and quotes the live price.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.clients import schwab_client
from src.clients.llm_client import TOOLS, execute_tool_call
from src.tracking import candle_service, watch_manager
from src.tracking.candle_service import evaluate_levels_against_candles, fetch_candles, get_candles_tool

# A GOOGL session whose high traded through 355 and gave it back on the last bar:
# first touch 10:05 ET, close 353.10 below 355 -> failed breakout.
_GOOGL_SESSION = [
    ("2026-10-13T13:35", 353.0, 353.6, 352.8, 353.30),
    ("2026-10-13T13:50", 353.3, 354.4, 353.1, 354.00),
    ("2026-10-13T14:05", 354.0, 355.5, 353.9, 355.23),   # the first touch of 355
    ("2026-10-13T14:20", 355.2, 356.4, 355.0, 356.00),   # holding above
    ("2026-10-13T14:35", 355.9, 356.0, 353.0, 353.10),   # back below -> failed
    ("2026-10-13T14:50", 353.1, 353.4, 352.9, 353.00),
]

_META_SESSION = [
    ("2026-10-13T13:35", 716.0, 718.4, 715.2, 717.90),
    ("2026-10-13T13:50", 717.9, 730.0, 717.5, 729.80),
    ("2026-10-13T14:05", 729.8, 748.1, 729.0, 747.60),   # crosses the META trigger 747.60
    ("2026-10-13T14:20", 747.6, 749.9, 740.0, 741.20),   # gives it back
    ("2026-10-13T14:35", 741.2, 743.0, 738.5, 739.90),
]

_DAILY = [
    {"date": "2026-10-08", "open": 348.0, "high": 349.0, "low": 347.0, "close": 348.5, "volume": 1_100_000},
    {"date": "2026-10-09", "open": 348.5, "high": 350.2, "low": 347.8, "close": 349.9, "volume": 1_250_000},
    {"date": "2026-10-12", "open": 351.0, "high": 353.4, "low": 350.6, "close": 353.30, "volume": 2_000_000},
    {"date": "2026-10-13", "open": 353.0, "high": 356.0, "low": 352.8, "close": 353.00, "volume": 2_400_000},
]

_META_DAILY = [
    {"date": "2026-10-08", "open": 715.0, "high": 722.0, "low": 712.0, "close": 720.5, "volume": 9_000_000},
    {"date": "2026-10-09", "open": 720.5, "high": 724.4, "low": 710.4, "close": 718.67, "volume": 12_000_000},
    {"date": "2026-10-12", "open": 718.7, "high": 731.0, "low": 715.5, "close": 729.80, "volume": 15_000_000},
]


def _bars(rows):
    """Session rows -> Schwab candle dicts (epoch ms, UTC) in ET session order."""
    out = []
    base = datetime(2026, 10, 13, 13, 35, tzinfo=timezone.utc)  # 09:35 ET (EDT)
    for i, (_ts, o, h, l, c) in enumerate(rows):
        out.append({
            "open": o, "high": h, "low": l, "close": c,
            "volume": 900_000 + i * 60_000,
            "datetime": int((base.timestamp() + i * 900) * 1000),
        })
    return out


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(watch_manager, "DB_PATH", tmp_path / "watch.db")
    watch_manager.init_watch_db()
    yield


def _patch_swab(monkeypatch, session, daily, quote):
    calls = {"n": []}

    def _fake_intraday(symbol, frequency=5, lookback_bars=30):
        calls["n"].append(frequency)
        return _bars(session)

    monkeypatch.setattr(schwab_client, "get_intraday_candles", _fake_intraday)
    monkeypatch.setattr(schwab_client, "get_realtime_quote", lambda sym: quote)
    monkeypatch.setattr(candle_service, "_schwab_daily_candles", lambda sym, days: daily)
    monkeypatch.setattr(candle_service, "_daily_bars_fallback", lambda sym, days: daily)
    return calls


# ---------------------------------------------------------------------------
# 1. GOOGL 355
# ---------------------------------------------------------------------------

def test_googl_355_states_cross_when_held_and_rr_at_market(monkeypatch):
    calls = _patch_swab(
        monkeypatch, _GOOGL_SESSION, _DAILY,
        {"last_price": 353.00, "high": 356.0, "low": 352.8, "open": 353.0,
         "close": 353.30, "volume": 2_400_000, "source": "SCHWAB"},
    )
    # The stored GOOGL plan: the levels the desk is actually watching.
    payload = {
        "ticker": "GOOGL", "date": "2026-10-12", "verdict": "STALK", "conviction": 6,
        "side": "LONG", "setup_lane": "COIL",
        "shares_plan": {
            "entry_type": "LIMIT", "entry_zone_low": 349.0, "entry_zone_high": 351.0,
            "breakout_level": 355.0, "breakout_stop": 350.0, "tactical_stop": 344.0,
            "target_1": 356.73, "target_2": 370.0,
        },
        "status": "STALKING",
    }
    watch_manager.upsert_watch_target(payload)

    out = get_candles_tool("GOOGL", interval="5m", days=5, levels=[355.0, 344.0])

    # The day's high is a number in the answer, not a model estimate.
    assert "Day High: $356.00" in out
    assert "Day Low: $352.80" in out
    assert "Live Quote: $353.00" in out

    # 355 crossed, when it first touched, and that it did NOT hold on close.
    row = next(r for r in evaluate_levels_against_candles(fetch_candles("GOOGL"), [355.0]))
    assert row["crossed"] is True
    assert "10:05 AM ET" in row["first_touch_time"]
    assert row["held_on_close"] is False
    assert row["outcome"] == "failed_breakout_closed_back_below"
    assert "$355.00 | YES | 10:05 AM ET | NO" in out

    # 344 never came into play: never touched, said plainly.
    assert "never touched" in out
    stop_row = next(r for r in evaluate_levels_against_candles(fetch_candles("GOOGL"), [344.0]))
    assert stop_row["crossed"] is False
    assert stop_row["first_touch_time"] == "never touched"

    # R:R at market from the stored plan, at the live price.
    assert "R:R at market" in out
    assert "STRUCTURED_CANDLES:" in out
    payload_json = json.loads(out.split("STRUCTURED_CANDLES: ", 1)[1].strip())
    assert payload_json["day_high"] == 356.0
    assert payload_json["live_price"] == 353.00
    assert payload_json["rr_at_market"]["position"] in ("BELOW_ZONE", "ABOVE_ZONE_RUNNING", "IN_ZONE", "NO_ZONE_STORED")
    assert payload_json["rr_at_market"]["stop"] == 344.0

    # The intraday candles come from the shared Schwab helper: 5m and 1m.
    assert sorted(set(calls["n"])) == [1, 5]


# ---------------------------------------------------------------------------
# 2. META replay
# ---------------------------------------------------------------------------

def test_meta_replay_states_the_trigger_cross_and_hold(monkeypatch):
    _patch_swab(
        monkeypatch, _META_SESSION, _META_DAILY,
        {"last_price": 739.90, "high": 749.9, "low": 715.2, "open": 716.0,
         "close": 729.80, "volume": 15_000_000, "source": "SCHWAB"},
    )
    payload = {
        "ticker": "META", "date": "2026-10-09", "verdict": "STALK", "conviction": 6,
        "side": "LONG", "setup_lane": "JUDGE",
        "shares_plan": {
            "entry_type": "LIMIT", "entry_zone_low": 710.43, "entry_zone_high": 715.95,
            "breakout_level": 747.60, "breakout_stop": 735.00, "tactical_stop": 698.50,
            "target_1": 775.0, "target_2": 800.0,
        },
        "status": "STALKING",
    }
    watch_manager.upsert_watch_target(payload)

    out = get_candles_tool("META", interval="5m", days=5, levels=[747.60, 715.95])

    assert "Day High: $749.90" in out
    evals = evaluate_levels_against_candles(fetch_candles("META"), [747.60])
    assert evals[0]["crossed"] is True
    assert "10:05 AM ET" in evals[0]["first_touch_time"]
    assert evals[0]["held_on_close"] is False     # closed back below the trigger
    assert "$747.60 | YES |" in out
    assert "R:R at market" in out
    payload_json = json.loads(out.split("STRUCTURED_CANDLES: ", 1)[1].strip())
    assert payload_json["rr_at_market"]["target_1"] == 775.0


# ---------------------------------------------------------------------------
# 3. Never touched
# ---------------------------------------------------------------------------

def test_level_above_the_day_high_reports_never_touched(monkeypatch):
    _patch_swab(
        monkeypatch, _GOOGL_SESSION, _DAILY,
        {"last_price": 353.00, "high": 356.0, "low": 352.8, "open": 353.0,
         "close": 353.30, "volume": 2_400_000, "source": "SCHWAB"},
    )
    out = get_candles_tool("GOOGL", interval="5m", days=5, levels=[400.0])
    assert "$400.00 | NO | never touched" in out
    assert "Day High: $356.00" in out


def test_interval_selects_the_touch_resolution(monkeypatch):
    _patch_swab(
        monkeypatch, _GOOGL_SESSION, _DAILY,
        {"last_price": 353.00, "high": 356.0, "low": 352.8, "open": 353.0,
         "close": 353.30, "volume": 2_400_000, "source": "SCHWAB"},
    )
    data_5m = fetch_candles("GOOGL", interval="5m")
    data_1m = fetch_candles("GOOGL", interval="1m")
    # Both carry the 5m series when only one intraday fetch answers; the requested
    # interval is recorded so the evaluation layer can pick its timeline.
    assert data_5m["interval"] == "5m" and data_1m["interval"] == "1m"
    assert data_5m["m5_candles"] and data_5m["m1_candles"]


# ---------------------------------------------------------------------------
# 4+5. Registration and dispatch
# ---------------------------------------------------------------------------

def test_get_candles_is_registered_and_dispatched():
    names = [t["function"]["name"] for t in TOOLS]
    assert "get_candles" in names
    schema = next(t["function"] for t in TOOLS if t["function"]["name"] == "get_candles")
    props = schema["parameters"]["properties"]
    assert set(props) >= {"ticker", "interval", "days", "levels"}
    assert props["levels"]["type"] == "array"
    assert "never touched" in schema["description"]


def test_execute_tool_call_routes_get_candles(monkeypatch):
    monkeypatch.setattr(candle_service, "get_candles_tool", lambda **kw: "CANDLE-OK")
    with patch("src.tracking.candle_service.get_candles_tool", lambda **kw: "CANDLE-OK"):

        class _Fn:
            name = "get_candles"
            arguments = json.dumps({"ticker": "GOOGL", "levels": [355.0], "interval": "5m", "days": 3})

        class _Tc:
            function = _Fn()

        res = execute_tool_call(_Tc(), "2026-10-13")
    assert res == "CANDLE-OK"


# ---------------------------------------------------------------------------
# 6. The chat prompt mandates the call
# ---------------------------------------------------------------------------

def test_chat_prompt_mandates_get_candles_for_named_levels():
    from run_ui import _build_copilot_context_and_tools

    system_prompt, _messages, _user_prompt, _ticker, _date = _build_copilot_context_and_tools(
        question="Did GOOGL break 355 today?",
        explicit_ticker="GOOGL",
        date_str="2026-10-13",
    )
    assert "get_candles" in system_prompt
    assert "15b. LEVEL QUESTIONS ARE DETERMINISTIC" in system_prompt
    assert "FIRST TOUCH" in system_prompt
    assert "never touched" in system_prompt
    assert "HELD on close" in system_prompt
    # The live price, never the report spot.
    assert "report's historical spot price" in system_prompt
