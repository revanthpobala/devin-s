from src.tracking import universe_sweep as us


def _bars(closes, lo_pad=1.0, hi_pad=1.0):
    return [{"date": f"d{i}", "open": c, "high": c + hi_pad, "low": c - lo_pad, "close": c} for i, c in enumerate(closes)]


def _pullback_series():
    up = [100 + i * 0.5 for i in range(240)]
    return up + [up[-1] * 0.96, up[-1] * 0.955, up[-1] * 0.95]


def test_metrics_flag_pullback_and_skip_short_history():
    m = us.metrics(_bars(_pullback_series()))
    assert m["a50"] and m["off_high20"] >= us.PULLBACK_OFF_HIGH
    assert us.metrics(_bars([100] * 50)) is None


def test_run_queues_held_first_and_writes_market(tmp_path, monkeypatch):
    monkeypatch.setattr(us, "out_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(us, "_sectors", lambda t: {x: "Technology" for x in t})
    flat = _bars([100 + i * 0.1 for i in range(260)])
    data = {"AAA": _bars(_pullback_series()), "BBB": _bars(_pullback_series()), "SPY": flat, "QQQ": flat, "IWM": flat}
    queued = []
    monkeypatch.setattr("src.tracking.alert_db.queue_for_research", lambda t, **k: queued.append(t) or True)
    res = us.run(True, bars_fetch=lambda names, d: data, tickers=["AAA", "BBB"], held={"BBB"})
    assert queued[0] == "BBB" and set(queued) <= {"AAA", "BBB"}
    assert res["market"]["names"] == 2 and "SPY" in res["etfs"] and (tmp_path / "s.json").exists()
