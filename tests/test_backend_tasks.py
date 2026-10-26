import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src import config
from src.tracking import backend_tasks as bt

ET = ZoneInfo("America/New_York")


def _chain():
    def c(dte, delta, iv, bid=1.0, ask=1.2):
        return [{"daysToExpiration": dte, "delta": delta, "volatility": iv, "bid": bid, "ask": ask, "openInterest": 10, "totalVolume": 5}]

    return {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-11-20:35": {"100.0": c(35, 0.5, 30.1), "125.0": c(35, 0.05, 40.0)},
                           "2026-10-16:3": {"100.0": c(3, 0.5, 31.0)}},
        "putExpDateMap": {"2026-11-20:35": {"95.0": c(35, -0.3, "NaN")}},
    }


def test_parse_keeps_only_near_money_and_window():
    rows = bt.parse_chain(_chain(), "2026-10-13", "AAA")
    assert sorted((r["cp"], r["strike"]) for r in rows) == [("C", 100.0), ("P", 95.0)]
    put = [r for r in rows if r["cp"] == "P"][0]
    assert put["iv"] is None and put["delta"] == -0.3 and put["dte"] == 35


def test_store_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "chain_db_path", lambda: tmp_path / "c.db")
    rows = bt.parse_chain(_chain(), "2026-10-13", "AAA")
    assert bt.store_rows(rows) == 2
    assert bt.store_rows(rows) == 0
    with sqlite3.connect(tmp_path / "c.db") as conn:
        assert conn.execute("select count(*) from chain").fetchone()[0] == 2


def test_last_session_rolls_back_to_last_completed_day():
    assert bt.last_session(datetime(2026, 10, 12, 16, 30, tzinfo=ET)) == "2026-10-12"
    assert bt.last_session(datetime(2026, 10, 12, 15, 0, tzinfo=ET)) == "2026-10-09"
    assert bt.last_session(datetime(2026, 10, 17, 17, 0, tzinfo=ET)) == "2026-10-16"
    assert bt.last_session(datetime(2026, 10, 19, 9, 0, tzinfo=ET)) == "2026-10-16"
    assert bt.last_session(datetime(2026, 10, 13, 10, 0, tzinfo=ET), ["2026-10-09", "2026-10-12"]) == "2026-10-12"
    assert bt.last_session(datetime(2026, 11, 27, 18, 0, tzinfo=ET), ["2026-11-25", "2026-11-26"]) == "2026-11-26"


def test_due_jobs_schedule(monkeypatch):
    from src.tracking import thesis_ledger
    monkeypatch.setattr(thesis_ledger, "has_active", lambda: False)
    mon_after = datetime(2026, 10, 12, 16, 30, tzinfo=ET)
    mon_before = datetime(2026, 10, 12, 9, 0, tzinfo=ET)
    sat = datetime(2026, 10, 17, 17, 0, tzinfo=ET)
    tue_mid = datetime(2026, 10, 13, 11, 0, tzinfo=ET)
    done = {"last_backup_date": "2026-10-12", "last_chain_date": "2026-10-12"}
    assert bt.due_jobs(mon_after, {}) == ["backup", "chain", "sweep"]
    assert bt.due_jobs(mon_after, done) == ["sweep"]
    swept = {**done, "last_sweep_date": "2026-10-12"}
    assert bt.due_jobs(mon_after, swept) == ["score"]
    scored = {**swept, "last_score_date": "2026-10-12"}
    assert bt.due_jobs(mon_after, scored) == ["counterfactual"]
    assert bt.due_jobs(mon_after, {**scored, "last_counterfactual_date": "2026-10-12"}) == []
    assert bt.due_jobs(mon_after, {**swept, "last_score_attempt_ts": mon_after.timestamp() - 60}) == ["counterfactual"]
    # Missed close: Saturday and Tuesday morning catch up on the last completed session.
    assert bt.due_jobs(sat, {"last_backup_date": "2026-10-17"}) == ["chain", "sweep"]
    assert bt.due_jobs(sat, {"last_backup_date": "2026-10-17", "last_chain_date": "2026-10-16", "last_sweep_date": "2026-10-16"}) == ["score"]
    assert bt.due_jobs(mon_before, {"last_backup_date": "2026-10-12"}) == ["chain", "sweep"]
    # Mid-session chain quotes belong to the live day, so the chain waits; sweep and score still catch up.
    assert bt.due_jobs(tue_mid, {"last_backup_date": "2026-10-13", "last_chain_date": "2026-10-09"}, session="2026-10-12") == ["sweep"]


def test_backup_copies_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    data = tmp_path / "data"
    (data / "backups" / "2026-01-01").mkdir(parents=True)
    (data / "backups" / "2026-01-02").mkdir(parents=True)
    with sqlite3.connect(data / "a.db") as c:
        c.execute("create table t (x)")
        c.execute("insert into t values (1)")
    (data / "positions.json").write_text("{}")
    res = bt.backup_databases("2026-10-13", keep=2)
    assert res["databases"] == 1 and not res["failed"]
    assert sqlite3.connect(data / "backups" / "2026-10-13" / "a.db").execute("select x from t").fetchone()[0] == 1
    assert (data / "backups" / "2026-10-13" / "positions.json").exists()
    assert sorted(p.name for p in (data / "backups").iterdir()) == ["2026-01-02", "2026-10-13"]
