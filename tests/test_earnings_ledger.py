import json
from datetime import date

import pandas as pd

from src.logic import earnings_move as em
from src.logic.prefetch_facts import format_earnings_packet

FC = {
    "INTC": {"median_abs": 0.0821, "p80_abs": 0.1814, "p95_abs": 0.3585, "down_share": 0.75, "n_hist": 8, "last": "2026-10-30"}
}
CONDS = {
    "run21": {"cuts": [-0.0478, 0.0835], "low": {"n": 10, "p_down_2x_hist": 0.1406}, "high": {"n": 10, "p_down_2x_hist": 0.0964}},
    "extz": {"cuts": [-0.949, 1.08], "low": {"n": 10, "p_down_2x_hist": 0.1377}, "high": {"n": 10, "p_down_2x_hist": 0.0993}},
    "ivr": {"cuts": [18.25, 77.78], "low": {"n": 10, "p_down_2x_hist": 0.0936}, "high": {"n": 10, "p_down_2x_hist": 0.1451}},
}


def _forecast(monkeypatch, tmp_path, tickers, conds=CONDS):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"asof": "2026-10-30", "k80": 2.2, "k95": 4.4, "base_p_down_2x": 0.1098,
                             "conditions": conds, "tickers": tickers}))
    monkeypatch.setattr(em, "FORECAST", p)
    monkeypatch.setattr(em, "_mtime", None)
    monkeypatch.setattr(em, "_cache", None)


def test_packet_shows_own_history_and_only_true_conditions(monkeypatch, tmp_path):
    _forecast(monkeypatch, tmp_path, FC)
    pkt = {"ticker": "INTC", "report_date": "2026-11-20", "source": "x", "trading_days": 12, "in_window": True,
           "own_history": FC["INTC"], "conditions": em.condition_lines(-0.2, -1.2, 25.0)}
    out = format_earnings_packet(pkt)
    assert "Own-history earnings move: median 8.2%, 80% within 18.1%, 95% within 35.9%, down 6/8" in out
    assert "21-bar run in the bottom quintile (14.1% vs 11.0% base)" in out
    assert "Ext Z in the bottom quintile" in out
    assert "IV rank" not in out


def test_packet_without_forecast_has_no_own_history_line(monkeypatch, tmp_path):
    _forecast(monkeypatch, tmp_path, {})
    pkt = {"ticker": "ZZZ", "report_date": "2026-11-20", "source": "x", "trading_days": 12, "in_window": True,
           "own_history": None, "conditions": em.condition_lines(0.0, 0.0, 50.0)}
    out = format_earnings_packet(pkt)
    assert "Own-history" not in out and "Measured elevated" not in out


def test_condition_lines_empty_when_no_forecast(monkeypatch, tmp_path):
    _forecast(monkeypatch, tmp_path, {}, conds={})
    assert em.condition_lines(-0.5, -2.0, 90.0) == []


def test_ledger_dedupes_same_ticker_report_and_day(tmp_path):
    path = tmp_path / "ledger.jsonl"
    row = {"recorded": "2026-11-01T10:00:00", "ticker": "INTC", "report_date": "2026-11-20", "implied_move_pct": 9.0}
    assert em.append_ledger(row, path) is True
    assert em.append_ledger({**row, "recorded": "2026-11-01T15:00:00"}, path) is False
    assert em.append_ledger({**row, "recorded": "2026-11-02T09:00:00"}, path) is True
    assert len(path.read_text().splitlines()) == 2


def test_score_ledger_fills_realized_move_from_next_session(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    scored = tmp_path / "scored.json"
    em.append_ledger({"recorded": "2026-10-20T10:00:00", "ticker": "INTC", "report_date": "2026-10-29",
                      "implied_move_pct": 8.0}, ledger)
    em.append_ledger({"recorded": "2026-10-20T10:00:00", "ticker": "AAA", "report_date": "2026-12-30",
                      "implied_move_pct": 8.0}, ledger)
    hist = pd.DataFrame({"Close": [100.0, 102.0, 70.0]},
                        index=pd.to_datetime(["2026-10-28", "2026-10-29", "2026-10-30"]))
    res = em.score_ledger(today=date(2026, 11, 1), history=lambda t, a, b: hist, ledger=ledger, scored=scored)
    assert res == {"ledger_rows": 2, "scored_total": 1, "newly_scored": 1}
    row = json.loads(scored.read_text())[0]
    assert row["realized_move_pct"] == -31.37
    assert row["implied_vs_realized"] == round(8.0 / 31.37, 2)


def test_realized_move_uses_report_day_close_as_reference():
    hist = pd.DataFrame({"Close": [50.0, 55.0, 60.0]}, index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]))
    assert em.realized_move(hist, date(2026, 1, 5)) == 0.1


def test_realized_move_catches_pre_market_report_day_reaction():
    hist = pd.DataFrame({"Close": [100.0, 80.0, 81.0]}, index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]))
    legs = em.realized_legs(hist, date(2026, 1, 6))
    assert legs == {"report_day": -0.2, "next_session": 0.0125, "reaction": -0.2}
