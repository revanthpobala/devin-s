import json

from src.logic import earnings_move as em
from src.tracking import trade_rules


def _point_forecast(monkeypatch, tmp_path, tickers):
    p = tmp_path / "earnings_move_forecast.json"
    p.write_text(json.dumps({"asof": "2026-10-30", "k80": 2.2, "k95": 4.4, "tickers": tickers}))
    monkeypatch.setattr(em, "FORECAST", p)
    monkeypatch.setattr(em, "_mtime", None)
    monkeypatch.setattr(em, "_cache", None)


def test_gap_pct_none_without_forecast_or_implied(monkeypatch, tmp_path):
    _point_forecast(monkeypatch, tmp_path, {})
    assert em.gap_pct("NOPE") is None
    assert em.forecast("NOPE") is None


def test_gap_pct_uses_implied_only_when_no_forecast(monkeypatch, tmp_path):
    _point_forecast(monkeypatch, tmp_path, {})
    assert em.gap_pct("NOPE", 8.3) == 16.6


def test_gap_pct_takes_larger_of_forecast_p95_and_twice_implied(monkeypatch, tmp_path):
    _point_forecast(monkeypatch, tmp_path, {"INTC": {"median_abs": 0.0785, "p95_abs": 0.343}})
    assert em.gap_pct("intc") == 34.3
    assert em.gap_pct("INTC", 20.0) == 40.0


def test_trade_rules_gap_pct_is_full_move_not_doubled(monkeypatch):
    monkeypatch.setattr(trade_rules, "MAX_EARNINGS_GAP_LOSS_USD", 1000.0)
    assert trade_rules.max_shares_by_earnings_gap(92.0, None, 2, gap_pct=34.3, max_gap_loss_usd=1000.0) == 31
    assert trade_rules.max_shares_by_earnings_gap(92.0, None, 2, gap_pct=None, max_gap_loss_usd=1000.0) is None
