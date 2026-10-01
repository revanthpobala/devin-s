"""
Regression tests for two concurrency/robustness bugs found by adversarial probing.

Bug A -- notify() shared connection. `_lock` guarded only connection CREATION, not the queries.
         Eight concurrent callers lost 51 of 400 dedupe records to "bad parameter or other API
         misuse", swallowed by the except clause. Those keys would be re-sent on the next sweep:
         duplicate phone pushes, the one outcome this ledger exists to prevent.

Bug B -- None-formatting in the alert bodies. `entry_body` and `build_digest` formatted
         `stop_width_atr` / `rr_at_market` with `:.2f` unconditionally. atr14 is a separate column
         and can be absent on a bar that still passes the gate, so one malformed snapshot raised
         TypeError inside fire_entry() -- taking every other onset in the sweep down with it.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

import src.tracking.notify as notify_mod
from src.tracking.entry_risk_alerts import Onset, _num, entry_body, fire_entry
from src.tracking.income_digest import build_digest, covered_call_candidate, income_body


# =====================================================================================
# Bug A: notify() dedupe under concurrency
# =====================================================================================

@pytest.fixture
def channel(tmp_path, monkeypatch):
    db = tmp_path / "dedupe.db"
    monkeypatch.setattr(notify_mod, "_dedupe_db_path", lambda: db)
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)
    monkeypatch.setattr(notify_mod, "_ntfy_configured", lambda: False)
    monkeypatch.setattr(notify_mod, "_smtp_configured", lambda: False)
    # A channel that ACCEPTS. The concurrency guarantee is about claims, and a claim is only kept
    # when something was actually delivered -- so a no-op transport would exercise nothing.
    monkeypatch.setattr(notify_mod, "_dispatch", lambda tier, title, body: ["capture"])
    monkeypatch.setattr(notify_mod.config, "GMAIL_EMAIL", "")
    monkeypatch.setattr(notify_mod.config, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(notify_mod, "_mountain_now", lambda: datetime(2026, 9, 28, 9, 30))
    yield db
    monkeypatch.setattr(notify_mod, "_DEDUPE_DB", None)


def _ledger_counts(db: Path):
    conn = sqlite3.connect(str(db))
    try:
        total, distinct = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT dedupe_key) FROM sent"
        ).fetchone()
    finally:
        conn.close()
    return total, distinct


def test_concurrent_notify_records_every_key(channel):
    """8 threads x 50 calls: all 400 keys must reach the ledger. Lost records mean re-paging."""
    errors: list = []

    def hammer(i):
        try:
            for j in range(50):
                notify_mod.notify("OPS", f"t{i}-{j}", "b", dedupe_key=f"K:{i}:{j}")
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors[:3]
    total, distinct = _ledger_counts(channel)
    assert total == 400, f"{400 - total} dedupe records were silently lost"
    assert distinct == total, "a duplicate row means the same key was recorded twice"


def test_concurrent_identical_keys_are_dispatched_once(channel, monkeypatch):
    """Twelve threads racing the SAME key must produce exactly one dispatch."""
    sent: list = []
    monkeypatch.setattr(notify_mod, "_dispatch", lambda tier, title, body: (sent.append(tier) or ["x"]))

    def hammer():
        for _ in range(25):
            notify_mod.notify("ENTRY", "GLW", "body", dedupe_key="SAME:GLW:2026-09-28")

    threads = [threading.Thread(target=hammer) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total, distinct = _ledger_counts(channel)
    assert total == 1 and distinct == 1
    assert len(sent) <= 1


def test_the_lock_covers_the_queries_not_just_connection_creation():
    """Pin the fix: a future refactor must not move the lock back outside the query."""
    import inspect

    for fn in (notify_mod._already_sent, notify_mod._claim_key, notify_mod._record_sent):
        src = inspect.getsource(fn)
        body = src.split("conn.execute", 1)
        assert len(body) == 2, f"{fn.__name__} no longer queries directly"
        with_idx, exec_idx = src.index("with _lock"), src.index("conn.execute")
        assert with_idx < exec_idx, (
            f"{fn.__name__} executes outside the lock; the shared connection is not "
            f"thread-safe and records will be lost"
        )


def test_dedupe_claims_by_atomic_insert_not_read_then_write():
    """Read-then-write is a race: two threads both see 'not sent' and both dispatch."""
    import inspect

    src = inspect.getsource(notify_mod.notify)
    assert "_claim_key" in src, "notify() must claim the key, not read-then-write"
    assert "_already_sent(key)" not in src, (
        "notify() still does SELECT-then-INSERT, which duplicates concurrent alerts"
    )
    claim = inspect.getsource(notify_mod._claim_key)
    assert "INSERT OR IGNORE" in claim and "rowcount" in claim, \
        "the claim must be a single atomic INSERT whose rowcount decides the winner"


def test_concurrent_dedupe_then_send_still_never_raises(channel):
    """notify() must survive concurrency even when the ledger is fine."""
    errors: list = []

    def hammer(i):
        try:
            notify_mod.notify("OPS", f"x{i}", "b", dedupe_key=f"C:{i}")
            notify_mod._already_sent(f"C:{i}")
            notify_mod._record_sent(f"post:{i}", "OPS", "t", "log")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


# =====================================================================================
# Bug B: None-formatting in the alert bodies
# =====================================================================================

def _passing(**kw):
    """An onset that satisfies every ENTRY gate condition."""
    base = dict(ticker="GLW", date="d", long_in_zone=True, long_rr_valid=True,
                fade_long=False, pb_funnel=True, packs_present=True)
    base.update(kw)
    return Onset(**base)


def test_num_helper_renders_absent_measurements():
    assert _num(None) == "–"
    assert _num(None, ".1f") == "–"
    assert _num(3.14159) == "3.14"
    assert _num(3.16, ".1f") == "3.2"


@pytest.mark.parametrize("missing", ["atr", "close", "stop", "target", "risk_pct", "lane_prior"])
def test_entry_body_survives_each_missing_measurement(missing):
    o = _passing(close=100.0, stop=95.0, target=115.0, atr=5.0)
    setattr(o, missing, None)
    body = entry_body(o)          # must not raise
    assert isinstance(body, str) and body.strip()


def test_entry_body_survives_a_bar_with_no_atr():
    """The reachable case: atr14 is a separate column and is absent on plenty of snapshots."""
    o = _passing(close=100.0, stop=95.0, target=115.0, atr=None)
    assert o.entry_gate()[0] is True, "precondition: this bar does pass the gate"
    assert o.stop_width_atr is None
    body = entry_body(o)
    assert "– ATR" in body
    assert "unmeasured" in body, "the missing width must say so rather than imply a number"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_entry_body_survives_non_finite_values(bad):
    for field in ("close", "stop", "target", "atr"):
        o = _passing(close=100.0, stop=95.0, target=115.0, atr=5.0)
        setattr(o, field, bad)
        entry_body(o)


def test_entry_body_survives_an_onset_with_nothing_set():
    body = entry_body(_passing())
    assert "no persisted levels" in body


def test_one_bad_bar_cannot_take_down_the_whole_sweep(channel, monkeypatch):
    """This is the actual severity: a raise inside fire_entry kills every other onset too."""
    good = _passing(ticker="GOOD", close=100.0, stop=95.0, target=115.0, atr=5.0)
    bad = _passing(ticker="NOATR", close=100.0, stop=95.0, target=115.0, atr=None)
    assert good.entry_gate()[0] and bad.entry_gate()[0], "both must be gate-passing"

    sent: list = []
    monkeypatch.setattr(notify_mod, "_dispatch",
                        lambda tier, title, body: (sent.append(title) or ["x"]))
    out = fire_entry([bad, good], "2026-09-28")

    assert "GOOD" in out["pushed"], "the healthy onset must still be delivered"
    assert "NOATR" in out["pushed"], "a bar with no ATR is still a valid push"
    total, distinct = _ledger_counts(channel)
    assert total == 2 and distinct == 2


def test_build_digest_survives_a_non_finite_atr():
    """Same bug class in the sibling function: it formatted stop_width_atr unguarded."""
    o = _passing(close=100.0, stop=95.0, target=115.0, atr=float("nan"))
    assert o.stop_width_atr is None, "nan atr must not produce a width"
    body = build_digest("d", [o])
    assert "– ATR" in body


def test_build_digest_survives_onsets_with_no_levels():
    assert isinstance(build_digest("d", [_passing()]), str)


def test_income_body_survives_missing_context():
    for overrides in ({"exp_move_pct": None}, {"close": None}, {"regime": None}):
        ctx = dict(close=100.0, stop=95.0, target=115.0, atr=5.0, ext_z=2.5,
                   regime=0, exp_move_pct=7.0)
        ctx.update(overrides)
        o = _passing(**ctx)
        sig = covered_call_candidate(o, index_drawdown_pct=20.0)
        assert sig is not None
        income_body(sig)           # must not raise