from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.tracking import earnings_drift_scan as eds

NY = ZoneInfo("America/New_York")


def _bars(n=60, gap=0.06, vol_r=3000.0, close_d=107.0):
    out = []
    for i in range(n):
        d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        o = c = 100.0
        v = 1000.0
        out.append({"date": d, "open": o, "high": 101.0, "low": 99.0, "close": c, "volume": v})
    r = n - 3
    o_r = 100.0 * (1 + gap)
    out[r].update(open=o_r, high=o_r * 1.01, low=o_r * 0.99, close=o_r, volume=vol_r)
    out[r + 1].update(open=o_r, high=o_r * 1.01, low=o_r * 0.99, close=o_r)
    out[r + 2].update(open=o_r, high=close_d * 1.01, low=o_r * 0.99, close=close_d)
    return out


def test_signal_when_gap_held_and_filed_day_before_reaction():
    b = _bars()
    c = eds.candidate(b)
    assert c is not None and c["reaction"] == b[-3]["date"] and c["prev_date"] == b[-4]["date"]
    assert eds.filing_matches(c, [c["prev_date"]])
    assert not eds.filing_matches(c, [c["reaction"]])  # same-day release is the pre-market case, not measured
    assert not eds.filing_matches(c, [])


def test_gap_below_five_percent_is_no_signal():
    assert eds.candidate(_bars(gap=0.03, close_d=104.0)) is None


def test_volume_below_two_times_is_no_signal():
    assert eds.candidate(_bars(vol_r=1500.0)) is None


def test_gap_not_held_is_no_signal():
    assert eds.candidate(_bars(close_d=104.0)) is None


def test_run_write_false_reports_but_writes_nothing():
    b = _bars()
    res = eds.run({"AAA": b, "BBB": _bars(gap=0.01)}, lambda t: [b[-4]["date"]] if t == "AAA" else [], write=False)
    assert [s["ticker"] for s in res["signals"]] == ["AAA"] and res["written"] == []


def test_run_ignores_name_whose_last_bar_is_stale():
    b = _bars()
    assert eds.candidate(b) is not None
    res = eds.run({"AAA": b, "ZZZ": _bars(n=61, gap=0.01)}, lambda t: [b[-4]["date"]], write=False)
    assert res["signals"] == []


def test_run_writes_unmeasured_next_open_suggestion(monkeypatch):
    import src.tracking.forward_log as fl
    import src.tracking.suggestions_ledger as sl

    rows, obs = [], []
    monkeypatch.setattr(sl, "append_suggestion", lambda d: rows.append(d) or 1)
    monkeypatch.setattr(fl, "record_forward_observation", lambda rule, t, data: obs.append((rule, t)))
    monkeypatch.setattr(eds, "_open_names", lambda since, through: set())
    b = _bars()
    day = date.fromisoformat(b[-1]["date"])
    before_open = datetime.combine(day, time(17, 0), tzinfo=NY)
    res = eds.run({"AAA": b}, lambda t: [b[-4]["date"]], write=True, now_et=before_open)
    assert res["written"] == ["AAA"] and len(rows) == 1 and obs == [("EARNINGS_DRIFT", "AAA")]
    row = rows[0]
    assert row["source"] == "earnings_drift" and row["setup_lane"] == "EARNINGS_DRIFT"
    assert row["gate_status"] == "UNMEASURED" and row["verdict"] == "STALK" and row["entry_type"] == "NEXT_OPEN"
    assert row["stop"] < row["entry_low"] and abs(row["entry_low"] - row["stop"] - 2 * row["atr_at_signal"]) < 1e-9


def test_run_skips_write_after_entry_open_passed(monkeypatch):
    import src.tracking.suggestions_ledger as sl

    rows = []
    monkeypatch.setattr(sl, "append_suggestion", lambda d: rows.append(d) or 1)
    b = _bars()
    late = datetime.combine(date.fromisoformat(b[-1]["date"]) + timedelta(days=1), time(10, 0), tzinfo=NY)
    res = eds.run({"AAA": b}, lambda t: [b[-4]["date"]], write=True, now_et=late)
    assert res["stale"] is True and rows == []
