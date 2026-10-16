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


def test_due_jobs_schedule(monkeypatch):
    from src.tracking import thesis_ledger
    monkeypatch.setattr(thesis_ledger, "has_active", lambda: False)
    mon_after = datetime(2026, 10, 12, 16, 30, tzinfo=ET)
    mon_before = datetime(2026, 10, 12, 15, 0, tzinfo=ET)
    sat = datetime(2026, 10, 17, 17, 0, tzinfo=ET)
    assert bt.due_jobs(mon_before, {}) == ["backup"]
    assert bt.due_jobs(mon_after, {}) == ["backup", "chain", "sweep"]
    assert bt.due_jobs(mon_after, {"last_backup_date": "2026-10-12", "last_chain_date": "2026-10-12"}) == ["sweep"]
    assert bt.due_jobs(mon_after, {"last_backup_date": "2026-10-12", "last_chain_date": "2026-10-12", "last_sweep_date": "2026-10-12"}) == []
    assert bt.due_jobs(sat, {"last_backup_date": "2026-10-17"}) == []
    assert bt.due_jobs(mon_after, {"last_backup_date": "2026-10-12", "last_chain_attempt_ts": mon_after.timestamp() - 60, "last_sweep_date": "2026-10-12"}) == []


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
