from datetime import datetime
from zoneinfo import ZoneInfo

from src.tracking import rsi2_scan as rs

ET = ZoneInfo("America/New_York")


def _bars(closes):
    return [{"date": f"2026-01-{i:04d}", "open": c, "high": c + 1, "low": c - 1, "close": c} for i, c in enumerate(closes)]


def _dip():
    up = [100 + i * 0.5 for i in range(300)]
    return up + [up[-1] - 3, up[-1] - 6]


def test_signal_fresh_dip_above_ema200():
    s = rs.signal(_bars(_dip()))
    assert s and s["rsi2"] < rs.RSI_MAX
    assert abs((s["close"] - s["stop"]) - 2 * s["atr"]) < 1e-9 and abs((s["target"] - s["close"]) - 4 * s["atr"]) < 1e-9
    assert s["ceiling"] == min(s["close"], s["stop"] / (1 - rs.MAX_RISK_PCT))


def test_signal_needs_rising_edge_history_and_price():
    assert rs.signal(_bars(_dip() + [_dip()[-1] - 3])) is None
    assert rs.signal(_bars(_dip()[-200:])) is None
    assert rs.signal(_bars([c / 20 for c in _dip()])) is None


def _dated(bars, last="2026-10-23"):
    from datetime import date, timedelta

    end = date.fromisoformat(last)
    n = len(bars)
    return [{**b, "date": (end - timedelta(days=n - 1 - i)).isoformat()} for i, b in enumerate(bars)]


def test_run_regime_verdict_without_writing():
    qqq = _dated(_bars([100.0] * 260 + [92.0]))
    res = rs.run({"AAA": _dated(_bars(_dip())), "QQQ": qqq}, write=False)
    assert res["verdict"] == "ENTER" and res["qqq_off_high_pct"] == 8.0
    assert [s["ticker"] for s in res["signals"]] == ["AAA"] and res["written"] == []
    assert rs.run({"AAA": _dated(_bars(_dip())), "QQQ": _dated(_bars([100.0] * 261))}, write=False)["verdict"] == "STALK"


def test_entry_open_passed_after_next_open():
    assert not rs.entry_open_passed("2026-10-23", datetime(2026, 10, 24, 17, 0, tzinfo=ET))
    assert not rs.entry_open_passed("2026-10-23", datetime(2026, 10, 26, 9, 29, tzinfo=ET))
    assert rs.entry_open_passed("2026-10-23", datetime(2026, 10, 26, 9, 30, tzinfo=ET))
    assert rs.entry_open_passed("2026-10-21", datetime(2026, 10, 22, 10, 0, tzinfo=ET))


def test_run_writes_only_signals_on_the_last_market_bar(monkeypatch):
    from src.tracking import suggestions_ledger

    sugg = []
    monkeypatch.setattr(suggestions_ledger, "append_suggestion", lambda d: sugg.append(d) or 7)
    monkeypatch.setattr(rs, "_open_names", lambda *a: set())
    bars = {"AAA": _dated(_bars(_dip())), "OLD": _dated(_bars(_dip()), "2026-10-22"),
            "QQQ": _dated(_bars([100.0] * 260 + [92.0]))}
    res = rs.run(bars, write=True, now_et=datetime(2026, 10, 24, 17, 0, tzinfo=ET))
    assert res["written"] == ["AAA"] and not res["stale"]
    assert sugg[0]["verdict"] == "ENTER" and sugg[0]["entry_type"] == "NEXT_OPEN" and sugg[0]["setup_lane"] == "RSI2"


def test_run_writes_nothing_after_the_next_open(monkeypatch):
    from src.tracking import suggestions_ledger

    monkeypatch.setattr(suggestions_ledger, "append_suggestion", lambda d: 1 / 0)
    bars = {"AAA": _dated(_bars(_dip())), "QQQ": _dated(_bars([100.0] * 260 + [92.0]))}
    res = rs.run(bars, write=True, now_et=datetime(2026, 10, 26, 10, 0, tzinfo=ET))
    assert res["written"] == [] and res["stale"] is True


def test_run_writes_nothing_without_the_qqq_regime(monkeypatch):
    from src.tracking import suggestions_ledger

    monkeypatch.setattr(suggestions_ledger, "append_suggestion", lambda d: 1 / 0)
    res = rs.run({"AAA": _dated(_bars(_dip()))}, write=True, now_et=datetime(2026, 10, 24, 17, 0, tzinfo=ET))
    assert res["written"] == [] and res["verdict"] == "STALK"
