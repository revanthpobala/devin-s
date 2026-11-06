import math
from datetime import datetime

import numpy as np
import pandas as pd

from src.tracking import market_stress as ms

ET = ms.ET


def _px(n=900, crash_at=700, seed=0):
    rng = np.random.default_rng(seed)
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2010-01-01", periods=n)]
    r = rng.normal(0.0005, 0.01, n)
    r[crash_at:crash_at + 15] = -0.03
    q = 100 * np.cumprod(1 + r)
    vix = np.where((np.arange(n) >= crash_at) & (np.arange(n) < crash_at + 40), 35.0, 15.0) + rng.normal(0, 0.5, n)
    lag = np.cumprod(1 + np.where((np.arange(n) >= crash_at) & (np.arange(n) < crash_at + 15), -0.005, 0.0)
                     + rng.normal(0, 0.002, n))
    px = pd.DataFrame({"QQQ": q, "^NDX": q * 40, "SPY": q * 4, "^GSPC": q * 40, "RSP": q * 1.5 * lag, "SMH": q * 2 * lag,
                       "^VIX": vix, "^VIX3M": vix * 0.9 + 2, "HYG": 80 * lag, "LQD": 110 + 0 * q,
                       "^TNX": 4 + rng.normal(0, 0.02, n), "^VVIX": 90 + 0 * q}, index=idx)
    return px


def test_score_in_range_and_rises_in_crash():
    px = _px()
    s = ms.score_series(ms.components(px)).dropna()
    assert s.between(0, 100).all()
    calm, crash = s.loc[:px.index[650]].tail(50).mean(), s.loc[px.index[705]:px.index[714]].mean()
    assert crash > calm + 20


def test_bands_and_recovery_actions():
    assert [ms.band_of(x) for x in (10, 50, 70, 85)] == ["NORMAL", "NO_NEW_ENTRIES", "REDUCE", "DEFENSIVE"]
    idx = [f"2020-01-{i:02d}" for i in range(1, 31)]
    score = pd.Series([40] * 10 + [85] * 5 + [60] * 5 + [45] * 10, index=idx, dtype=float)
    term = pd.Series([0.9] * 30, index=idx)
    qqq = pd.Series(np.linspace(100, 130, 30), index=idx)
    a = ms.actions(score, term, qqq).tolist()
    assert a[0] == "NORMAL" and a[10] == "DEFENSIVE"
    assert a[16] == "SELECTIVE_ACCUMULATION"
    assert a[25] == "ADD_EXPOSURE"


def test_events_decluster():
    assert ms._events([True, True, False, True, False, False, True], 3) == [0, 3, 6]


def test_vix_and_lead_study_and_backtest_run():
    px = _px()
    v = ms.vix_study(px)
    assert v["events"] and v["spx"]["n"] == len(v["events"])
    score = ms.score_series(ms.components(px))
    lead = ms.lead_study(px, score)
    assert lead["events"] and "score_ge_65" in lead["flags"]
    act = ms.actions(score, px["^VIX"] / px["^VIX3M"], px["QQQ"]).dropna()
    bt = ms.backtest(px, act)
    assert bt["verdict"] in ("MEASURED", "UNMEASURED") and bt["drawdowns_10pct"] >= 1


def _candles(day, closes, vol=1000):
    out = []
    for i, c in enumerate(closes):
        out.append({"date": day, "session": "RTH", "open": c, "high": c + 0.5, "low": c - 0.5, "close": c,
                    "volume": vol})
    return out


def test_intraday_pressure_flags_selloff():
    down = [100 - 0.3 * i for i in range(30)]
    flat = [100.0] * 30
    candles = {"QQQ": _candles("2026-11-04", flat, 500) + _candles("2026-11-05", down, 1000),
               "SMH": _candles("2026-11-05", [100 - 0.6 * i for i in range(30)])}
    for s in ms.SECTOR_ETFS:
        candles[s] = _candles("2026-11-05", down)
    res = ms.intraday_pressure(candles, vix_change=0.15)
    assert res["signals"]["qqq_orb_break"] and res["signals"]["sectors_below_vwap"] and res["signals"]["qqq_rvol"]
    assert res["pressure"] >= 80
    calm = ms.intraday_pressure({"QQQ": _candles("2026-11-05", [100 + 0.1 * i for i in range(30)])}, vix_change=-0.02)
    assert calm["pressure"] is not None and calm["pressure"] < 60


def test_intraday_due_rth_only():
    assert not ms.intraday_due(datetime(2026, 11, 5, 9, 40, tzinfo=ET), None)
    assert ms.intraday_due(datetime(2026, 11, 5, 10, 0, tzinfo=ET), None)
    t = datetime(2026, 11, 5, 10, 0, tzinfo=ET)
    assert not ms.intraday_due(t, t.timestamp() - 60)
    assert not ms.intraday_due(datetime(2026, 11, 7, 11, 0, tzinfo=ET), None)
    assert not ms.intraday_due(datetime(2026, 11, 5, 16, 5, tzinfo=ET), None)


def test_quality_row_score_and_traps():
    cand = {"ticker": "AAA", "close": 50.0, "drawdown_pct": 35.0, "r21_pct": -15.0, "high_252d": 77.0}
    good = {"freeCashflow": 2e9, "totalRevenue": 1e10, "operatingMargins": 0.25, "returnOnEquity": 0.25,
            "revenueGrowth": 0.12, "forwardPE": 18, "enterpriseValue": 4e10, "totalDebt": 1e9, "totalCash": 2e9,
            "ebitda": 3e9}
    r = ms.quality_row(cand, good, datetime(2026, 11, 5))
    assert r["score"] is not None and r["score"] > 50 and r["value_trap_flags"] == []
    bad = dict(good, freeCashflow=-1e8, revenueGrowth=-0.05, totalDebt=2e10)
    rb = ms.quality_row(cand, bad, datetime(2026, 11, 5))
    assert {"no_fcf", "revenue_shrinking", "net_debt_over_3x_ebitda"} <= set(rb["value_trap_flags"])
    assert ms.quality_row(cand, {}, datetime(2026, 11, 5))["score"] is None


def test_run_writes_outputs_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(ms.config, "BASE_DIR", tmp_path)
    px = _px()
    hist = {s: pd.DataFrame({"close": px[s]}) for s in px.columns}
    monkeypatch.setattr(ms, "fetch_history", lambda session: hist)
    sent = []
    monkeypatch.setattr("src.tracking.notification_center.add", lambda *a, **k: sent.append(k.get("dedupe_key")))
    cur = ms.run({}, {"advancing_pct": 10.0}, session="2013-06-14", queue=False)
    assert 0 <= cur["score"] <= 100 and cur["action"] and cur["label"] in ("MEASURED", "UNMEASURED")
    assert ms.out_path().exists() and ms.ledger_path().exists() and sent
    ms.run({}, None, session="2013-06-14", queue=False)
    assert len(ms.ledger_path().read_text().splitlines()) == 1
    assert ms.latest()["daily"]["session"] == "2013-06-14"
    assert not math.isnan(cur["score"])
