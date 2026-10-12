"""
Tests for the thesis paper-trade evaluator.

The metric the desk advertises is: "when a thesis is generated, take 100 shares at the spot we
observed, then let the tape decide whether the thesis was right." These tests pin the three
things that make that claim honest:

  1. the spot really is captured at thesis time (and the thesis spot is what gets walked),
  2. a degenerate plan (stop == entry) cannot manufacture an R of zero,
  3. resolution uses the shared bar-walk validator rather than today's spot, so a 3-week-old
     thesis is not silently scored as if it had been held until today.

Run: python -m pytest tests/test_thesis_paper.py -q
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict

import pytest

from src.tracking import thesis_evaluator as te
from src.tracking.watch_manager import _get_connection


def _clear_paper_rows() -> None:
    te.init_thesis_paper_db()
    with _get_connection() as conn:
        conn.cursor().execute("DELETE FROM thesis_paper_trades")
        conn.commit()


@pytest.fixture(autouse=True)
def _clean_ledger():
    _clear_paper_rows()
    yield
    _clear_paper_rows()


def _thesis(ticker: str, spot: float, stop: float, target: float, day: str = "2026-09-15") -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "date": day,
        "created_date": day,
        "thesis_id": f"{ticker}_{day}",
        "side": "LONG",
        "expires_on": "2026-09-22",
        "shares_plan": {
            "entry_zone_low": spot - 1.0,
            "entry_zone_high": spot,
            "tactical_stop": stop,
            "target_1": target,
        },
    }


def test_captures_thesis_spot_at_generation_time(monkeypatch):
    monkeypatch.setattr(te, "_latest_spot", lambda t: 111.36)
    row = te.record_thesis_paper_trade(_thesis("WMT", 111.36, 108.0, 120.0))
    assert row is not None
    # the frozen spot is what the metric is measured from
    assert float(row["thesis_spot"]) == 111.36
    assert int(row["qty"]) == te.PAPER_QTY
    assert row["status"] == "OPEN"


def test_rejects_degenerate_plan(monkeypatch):
    """A stop equal to the entry cannot produce a meaningful R multiple."""
    monkeypatch.setattr(te, "_latest_spot", lambda t: 50.0)
    row = te.record_thesis_paper_trade(_thesis("FLAT", 50.0, 50.0, 55.0))
    assert row is None


def test_requires_stop_and_target(monkeypatch):
    payload = _thesis("NOLVLS", 50.0, 0.0, 0.0)
    payload["shares_plan"]["tactical_stop"] = None
    row = te.record_thesis_paper_trade(payload)
    assert row is None


def test_refresh_scores_on_real_bars(monkeypatch):
    """Resolution must come from the bar walk, not from today's spot."""
    te.init_thesis_paper_db()
    monkeypatch.setattr(te, "_latest_spot", lambda t: 100.0)
    te.record_thesis_paper_trade(_thesis("BARTEST", 100.0, 98.0, 106.0, "2026-09-15"))

    # Stub the shared validator so the test does not depend on live market data.
    monkeypatch.setattr(
        te,
        "_evaluate_with_bars",
        lambda row: {
            "was_filled": True,
            "fill_price": 100.10,
            "exit_price": 106.00,
            "exit_date": "2026-09-18",
            "hit_t1": True,
            "hit_stop": False,
            "is_terminal": True,
            "bars_held": 3,
            "max_high": 106.0,
            "min_low": 99.0,
        },
    )

    res = te.refresh_thesis_paper_trades()
    assert res["resolved"] == 1

    with _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = dict(
            conn.cursor().execute(
                "SELECT * FROM thesis_paper_trades WHERE ticker = 'BARTEST'"
            ).fetchone()
        )

    # filled on the tape at the next open, not at the recorded thesis spot
    assert float(row["thesis_spot"]) == pytest.approx(100.10, abs=0.01)
    assert row["outcome"] == "TARGET_HIT"
    assert row["thesis_correct"] == 1
    # 100 shares * (106.00 - 100.10) risk = 100 shares * (100.10 - 98.00)
    assert row["pnl_dollars"] == pytest.approx(590.0, abs=1.0)
    assert row["r_multiple"] == pytest.approx(590.0 / 210.0, abs=0.05)


def test_stop_hit_is_scored_as_incorrect(monkeypatch):
    te.init_thesis_paper_db()
    monkeypatch.setattr(te, "_latest_spot", lambda t: 100.0)
    te.record_thesis_paper_trade(_thesis("STOPTEST", 100.0, 98.0, 106.0, "2026-09-15"))
    monkeypatch.setattr(
        te,
        "_evaluate_with_bars",
        lambda row: {
            "was_filled": True,
            "fill_price": 100.10,
            "exit_price": 98.00,
            "exit_date": "2026-09-16",
            "hit_t1": False,
            "hit_stop": True,
            "is_terminal": True,
            "bars_held": 1,
            "max_high": 100.5,
            "min_low": 98.0,
        },
    )
    assert te.refresh_thesis_paper_trades()["resolved"] == 1

    stats = te.get_paper_trade_stats(force=True)
    agg = stats["aggregate"]
    assert agg["target_hit"] == 0
    assert agg["stop_hit"] == 1
    assert agg["thesis_correct_rate_pct"] == 0.0
    assert agg["total_pnl_dollars"] < 0


def test_live_thesis_stays_open_until_it_resolves(monkeypatch):
    te.init_thesis_paper_db()
    monkeypatch.setattr(te, "_latest_spot", lambda t: 100.0)
    te.record_thesis_paper_trade(_thesis("OPEN", 100.0, 98.0, 106.0, "2026-09-15"))
    monkeypatch.setattr(
        te,
        "_evaluate_with_bars",
        lambda row: {"was_filled": False, "is_terminal": False, "status": "STALKING"},
    )
    res = te.refresh_thesis_paper_trades()
    assert res["resolved"] == 0
    stats = te.get_paper_trade_stats(force=True)
    assert stats["open"] == 1
    assert stats["aggregate"]["resolved"] == 0
