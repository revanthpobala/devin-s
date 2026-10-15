from datetime import date, timedelta

import pytest

from src.tracking import thesis_ledger as tl


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "db_path", lambda: tmp_path / "thesis_ledger.db")


def _days(n, start="2026-01-05"):
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(days, closes):
    return [{"date": d, "close": c} for d, c in zip(days, closes)]


def _setup(closes, inval=95.0, verdicts=None):
    days = _days(len(closes))
    tl.seed(book={"equity": {"AAA": 100.0}}, today=days[0])
    tl.upsert_marks("AAA", _bars(days, closes), _bars(days, [400.0] * len(days)))
    tl.record_update("AAA", days[0], "judge", "STALK", 60, 2.5, 0, 0, inval)
    for d, v in (verdicts or {}).items():
        tl.record_update("AAA", d, "judge", v, None, None, None, 0, inval)
    return days


def test_seed_held_first_and_cap():
    r = tl.seed(user=["B", "C", "D"], book={"equity": {"A": 1.0}}, today="2026-01-05", cap=2)
    assert r["added"] == ["A", "B"] and r["skipped_over_cap"] == ["C", "D"]
    assert tl.seed(user=["B"], book={"equity": {}}, cap=2)["added"] == []


def test_fade_bit_inverted():
    assert tl.fade_active(0) == 1 and tl.fade_active(4) == 0 and tl.fade_active(None) is None


def test_invalidation_close_kills_and_stops_adds():
    closes = [100] * 5 + [94] + [100] * 3
    days = _setup(closes)
    out = tl.process_ticker("AAA", days[-1], earnings=[])
    assert out["killed"]["reason"] == "INVALIDATION_BREAK" and out["killed"]["date"] == days[5]
    th = tl._rows("SELECT * FROM thesis")[0]
    assert th["status"] == "BROKEN" and th["exit_px"] == 94
    assert len(tl._rows("SELECT * FROM tranches")) == 1


def test_break_right_after_earnings_is_labelled():
    closes = [100] * 5 + [94]
    days = _setup(closes)
    out = tl.process_ticker("AAA", days[-1], earnings=[days[4]])
    assert out["killed"]["reason"] == "EARNINGS_BREAK"


def test_late_invalidation_does_not_kill_retroactively():
    days = _days(4)
    tl.seed(book={"equity": {"AAA": 1.0}}, today=days[0])
    tl.upsert_marks("AAA", _bars(days, [90, 90, 100, 100]), _bars(days, [1] * 4))
    tl.record_update("AAA", days[2], "judge", "STALK", None, None, None, 0, 95.0)
    assert tl.process_ticker("AAA", days[-1], earnings=[])["killed"] is None


def test_three_cash_skips_kill():
    days = _days(6)
    _setup([100] * 6, verdicts={days[2]: "CASH_SKIP", days[3]: "CASH_SKIP", days[4]: "CASH_SKIP"})
    tl.record_update("AAA", days[0], "judge", "CASH_SKIP", None, None, None, 0, 95.0)
    out = tl.process_ticker("AAA", days[-1], earnings=[])
    assert out["killed"]["reason"] == "CASH_SKIP_X3" and out["killed"]["date"] == days[3]


def test_tranche_spacing_freshness_and_third_needs_catalyst():
    days = _days(30)
    _setup([100] * 30)
    for d in days:
        tl.record_update("AAA", d, "judge", "STALK", None, None, None, 0, 95.0)
    out = tl.process_ticker("AAA", days[-1], earnings=[])
    assert [t["date"] for t in tl._rows("SELECT * FROM tranches ORDER BY n")] == [days[0], days[5]]
    assert [t["n"] for t in out["tranches"]] == [1, 2]
    tl2 = tl.process_ticker("AAA", days[-1], earnings=[days[10]])
    assert tl2["tranches"] == []  # already caught up to as_of; replay is idempotent


def test_third_tranche_after_catalyst_and_sizing_cap():
    days = _days(30)
    _setup([100] * 30)
    for d in days:
        tl.record_update("AAA", d, "judge", "STALK", None, None, None, 0, 95.0)
    tl.process_ticker("AAA", days[-1], earnings=[days[7]])
    tr = tl._rows("SELECT * FROM tranches ORDER BY n")
    assert len(tr) == 3 and tr[2]["date"] > days[7]
    total = sum(t["shares"] * t["price"] for t in tr)
    assert total == pytest.approx(5000.0)  # min(5% = 5000, 1% risk 1000 / 5% stop = 20000)


def test_risk_sized_when_stop_is_wide():
    assert tl.target_notional(100.0, 80.0) == pytest.approx(5000.0 * 0 + 1000.0 / 0.2)
    assert tl.target_notional(100.0, 95.0) == 5000.0


def test_fade_or_stale_update_blocks_add():
    days = _days(12)
    _setup([100] * 12)
    for d in days:
        tl.record_update("AAA", d, "judge", "STALK", None, None, None, 1, 95.0)
    tl.process_ticker("AAA", days[-1], earnings=[])
    assert len(tl._rows("SELECT * FROM tranches")) == 1


def test_earnings_within_five_days_blocks_starter():
    days = _setup([100] * 3)
    tl.process_ticker("AAA", days[-1], earnings=[days[2]])
    assert tl._rows("SELECT * FROM tranches") == []


def test_score_vs_spy_net_of_costs():
    days = _days(8)
    tl.seed(book={"equity": {"AAA": 1.0}}, today=days[0])
    closes = [100, 100, 100, 100, 100, 110, 110, 110]
    spy = [400, 400, 400, 400, 400, 404, 404, 404]
    tl.upsert_marks("AAA", _bars(days, closes), _bars(days, spy))
    with tl._conn() as c:
        c.execute("INSERT INTO tranches VALUES ('AAA',1,?,100,10,95)", (days[0],))
    h = tl.score(horizons=(5,))["horizons"][5]
    assert h["n"] == 1 and h["unmeasured"] is True
    assert h["mean_excess_pct"] == pytest.approx(100 * (0.10 - 0.001 - 0.01), abs=1e-3)


def test_nightly_replay_idempotent_and_queues_stalest():
    days = _days(10)
    tl.seed(user=["BBB"], book={"equity": {"AAA": 1.0}}, today=days[0])
    queued = []
    bars = lambda t: _bars(days, [100] * 10)
    r1 = tl.run_nightly(days[-1], bars_for=bars, queue=lambda t: queued.append(t) or True)
    r2 = tl.run_nightly(days[-1], bars_for=bars, queue=lambda t: True)
    assert r1["names"] == 2 and queued == ["AAA", "BBB"] and r2["tranches"] == 0
    assert tl.last_processed() == days[-1]


def test_backend_due_job_requires_seeded_ledger():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.tracking.backend_tasks import due_jobs

    now = datetime(2026, 1, 7, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    assert "thesis" not in due_jobs(now, {})
    tl.seed(user=["AAA"], book={"equity": {}})
    assert "thesis" in due_jobs(now, {})
    assert "thesis" not in due_jobs(now, {"last_thesis_attempt_ts": now.timestamp() - 60})
