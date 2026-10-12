"""
tests/test_conviction_write_paths.py

D2 from plan/no-lost-movers.md: cap_conviction runs on EVERY write path, not just
arbitration. A conviction above the band its own verdict supports is an absurd
badge (an 8 on a CUT), and an absent conviction stays absent — no fabricated
default anywhere. The cap only ever pulls the number down.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.logic.process_survivor import _cap_thesis_conviction


# ---------------------------------------------------------------------------
# 1. Triage write path (process_survivor)
# ---------------------------------------------------------------------------

def test_thesis_write_path_caps_conviction_to_verdict_band():
    llm_json = {"triage": "CUT", "conviction": 8, "key_flags": []}
    _cap_thesis_conviction(llm_json, has_plan=True)
    assert llm_json["conviction"] <= 4


def test_thesis_write_path_caps_flagged_watch():
    llm_json = {"triage": "WATCH", "conviction": 9, "key_flags": ["no_edge"], "entry_mode": "TREND_LONG"}
    _cap_thesis_conviction(llm_json, has_plan=True)
    assert llm_json["conviction"] <= 4


def test_thesis_write_path_keeps_supported_band():
    llm_json = {"triage": "PASS", "conviction": 8, "key_flags": [], "entry_mode": "TREND_LONG"}
    _cap_thesis_conviction(llm_json, has_plan=True)
    assert llm_json["conviction"] == 8


def test_thesis_write_path_absent_conviction_stays_absent():
    llm_json = {"triage": "WATCH", "key_flags": []}
    _cap_thesis_conviction(llm_json, has_plan=False)
    assert "conviction" not in llm_json


def test_thesis_write_path_planless_watch_capped():
    llm_json = {"triage": "WATCH", "conviction": 8, "key_flags": [], "entry_mode": "NONE"}
    _cap_thesis_conviction(llm_json, has_plan=False)
    assert llm_json["conviction"] <= 4


# ---------------------------------------------------------------------------
# 2. watch_manager.upsert_watch_target defence in depth
# ---------------------------------------------------------------------------

def _payload(ticker: str, verdict: str, conviction, stop: float = 110.0) -> dict:
    return {
        "ticker": ticker,
        "date": "2026-10-06",
        "verdict": verdict,
        "conviction": conviction,
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            "entry_zone_low": 118.0,
            "entry_zone_high": 122.0,
            "tactical_stop": stop,
            "target_1": 135.0,
            "target_2": 145.0,
        },
        "status": "STALKING",
    }


def _persisted_conviction(ticker: str) -> int:
    with sqlite3.connect(str(_test_db)) as conn:
        row = conn.execute(
            "SELECT conviction FROM watch_targets WHERE ticker = ?", (ticker,)
        ).fetchone()
    assert row is not None, f"{ticker} not persisted"
    return row[0]


_test_db: Path = Path("unset")


@pytest.fixture()
def watch_db(tmp_path, monkeypatch):
    global _test_db
    import src.tracking.watch_manager as wm

    _test_db = tmp_path / "watch.db"
    monkeypatch.setattr(wm, "DB_PATH", _test_db)
    monkeypatch.setattr(wm, "_db_initialized", False, raising=False)
    wm.init_watch_db()
    return wm


def test_upsert_caps_uncapped_cash_skip_conviction(watch_db):
    watch_db.upsert_watch_target(_payload("CASHX", "CASH_SKIP", 9))
    assert _persisted_conviction("CASHX") <= 4


def test_upsert_keeps_verdict_supported_conviction(watch_db):
    watch_db.upsert_watch_target(_payload("ENTERX", "ENTER (Limit @ Floor)", 8))
    assert _persisted_conviction("ENTERX") == 8


def test_upsert_missing_conviction_stays_absent(watch_db):
    # Pre-change behaviour: an explicit None conviction persisted as NULL, and the
    # cap must not turn that absence into a fabricated number.
    watch_db.upsert_watch_target(_payload("NOCONV", "STALK", None))
    assert _persisted_conviction("NOCONV") is None
