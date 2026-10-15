import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from src.tracking import alert_setups as a

N = 121
DATES = list(pd.bdate_range("2026-01-02", periods=N).strftime("%Y-%m-%d"))


def _bars(high, low_after, high_after):
    h = np.full(N, high)
    h[81:] = high_after
    lo = np.full(N, 99.0)
    lo[81:] = low_after
    return pd.DataFrame({"open": 100.0, "high": h, "low": lo, "close": 100.0, "date": DATES})


@pytest.fixture
def replay():
    try:
        return a._replay()
    except RuntimeError:
        pytest.skip("honest_entry_replay not available")


def test_scores_first_occurrence_only_and_separates_pending(replay):
    win = _bars(101.0, 99.5, 101.0)
    win.loc[60, "high"] = 110.0
    win.loc[82, "high"] = 110.0
    flat = _bars(100.5, 99.5, 100.4)
    alerts = {"events": {
        ("A+ Trend Long", "AAA"): [DATES[80], DATES[81]],
        ("A+ Trend Long", "BBB"): [DATES[80], DATES[100]],
    }}
    rows = a.score(alerts, {"AAA": win, "BBB": flat, "CCC": flat})
    assert [(r["tk"], r["status"]) for r in rows] == [("AAA", "SCORED"), ("BBB", "SCORED"), ("BBB", "PENDING")]
    aaa, bbb = rows[0], rows[1]
    assert aaa["R"] == pytest.approx(9.895)
    assert bbb["R"] == pytest.approx(-0.1)
    tape = (9.895 - 0.1 - 0.1) / 3
    assert aaa["R_rel"] == pytest.approx(9.895 - tape)

    s = a.summarize(rows)["A+ Trend Long"]
    assert (s["n"], s["pending"], s["signals"], s["win_pct"]) == (2, 1, 3, 50.0)
    assert s["status"] == "UNMEASURED" and s["breadth_pct"] == 50.0
    assert a.summarize(rows)["Coiled VCP"] == {"n": 0, "pending": 0, "skipped": 0, "signals": 0, "status": "UNMEASURED"}


def test_measured_only_at_n_200():
    rows = [{"setup": "Coiled VCP", "tk": f"T{i}", "date": "d", "R": 0.1, "R_rel": 0.1, "status": "SCORED"} for i in range(a.MIN_N)]
    assert a.summarize(rows)["Coiled VCP"]["status"] == "MEASURED"
    assert a.summarize(rows[:-1])["Coiled VCP"]["status"] == "UNMEASURED"


def test_load_alerts_long_real_daily_only(tmp_path):
    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE alerts (message_id TEXT, symbol TEXT, date TEXT, strategy TEXT, setup TEXT, raw_payload TEXT)")
    long_p, short_p = json.dumps({"side": "LONG"}), json.dumps({"side": "SHORT"})
    c.executemany("INSERT INTO alerts VALUES (?,?,?,?,?,?)", [
        ("m1", "aaa", "2026-10-01", "Daily", "A+ Trend Long", long_p),
        ("synth-1", "BBB", "2026-10-01", "Daily", "A+ Trend Long", long_p),
        ("m2", "CCC", "2026-10-01", "Daily", "A+ Trend Short", short_p),
        ("m3", "DDD", "2026-10-01", "Daily", "Stagnation (Research)", json.dumps({"side": "NEUTRAL"})),
        ("m4", "EEE", "2026-10-01", "Intraday", "A+ Trend Long", long_p),
        ("m5", "FFF", "2026-10-01", "Daily", "Coiled VCP", "bad"),
    ])
    c.commit()
    c.close()
    got = a.load_alerts(db)
    assert got["events"] == {("A+ Trend Long", "AAA"): ["2026-10-01"]}
    assert "BBB" in got["universe"] and "EEE" in got["universe"]


def test_summary_never_blocks_and_serves_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "_tracking_dir", lambda: tmp_path)
    started = []
    monkeypatch.setattr(a.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: started.append(target)})())
    monkeypatch.setattr(a, "_running", False)
    monkeypatch.setattr(a, "_last_try", 0.0)
    assert a.summary() == {"computing": True, "error": None} and len(started) == 1
    assert a.summary() == {"computing": True, "error": None} and len(started) == 1
    (tmp_path / "alert_setups.json").write_text(json.dumps({"setups": {}}))
    assert a.summary() == {"setups": {}}


def test_trust_payload_exposes_alert_setups(monkeypatch):
    from src.ui.routes import notifications

    monkeypatch.setattr(notifications, "build_trust_report", lambda rows: {})
    monkeypatch.setattr(notifications, "load_rows", lambda: [])
    monkeypatch.setattr(a, "summary", lambda: {"setups": {"Coiled VCP": {"n": 0}}})
    assert notifications.trust()["alert_setups"] == {"setups": {"Coiled VCP": {"n": 0}}}
