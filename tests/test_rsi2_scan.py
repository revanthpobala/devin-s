from src.tracking import rsi2_scan as rs


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


def test_run_regime_verdict_without_writing():
    qqq = _bars([100.0] * 260 + [92.0])
    res = rs.run({"AAA": _bars(_dip()), "QQQ": qqq}, write=False)
    assert res["verdict"] == "ENTER" and res["qqq_off_high_pct"] == 8.0
    assert [s["ticker"] for s in res["signals"]] == ["AAA"] and res["written"] == []
    assert rs.run({"AAA": _bars(_dip()), "QQQ": _bars([100.0] * 261)}, write=False)["verdict"] == "STALK"
