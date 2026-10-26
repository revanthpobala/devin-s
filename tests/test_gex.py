from datetime import datetime
from zoneinfo import ZoneInfo

from src.data import gex

import pytest

NOW = datetime(2026, 10, 26, 11, 0, tzinfo=ZoneInfo("America/New_York"))


def _c(side, strike, oi, dte=1, iv=0.30):
    return {"side": side, "strike": strike, "oi": oi, "iv": iv, "dte": dte}


def test_signs_calls_positive_puts_negative():
    calls = gex.compute_gex([_c("C", 100, 1000)], 100.0, 45, NOW)
    puts = gex.compute_gex([_c("P", 100, 1000)], 100.0, 45, NOW)
    assert calls["net_gex_usd_per_1pct"] > 0 and calls["regime"] == "LONG_GAMMA"
    assert puts["net_gex_usd_per_1pct"] < 0 and puts["regime"] == "SHORT_GAMMA"
    assert calls["net_gex_usd_per_1pct"] == -puts["net_gex_usd_per_1pct"]


def test_known_gamma_value():
    # ATM, iv 0.2, 30d: gamma = phi(d1)/(S*iv*sqrt(T))
    g = gex._bs_gamma(100.0, 100.0, 0.2, 30 / 365)
    assert abs(g - 0.0693) < 0.001


def test_walls_and_flip_between_put_and_call_clusters():
    cs = [_c("P", 95, 20000, 5), _c("C", 105, 20000, 5), _c("C", 100, 3000, 5), _c("P", 100, 3000, 5)]
    r = gex.compute_gex(cs, 100.0, 45, NOW)
    assert r["call_gex_wall"] == 105 and r["put_gex_wall"] == 95


def test_flip_found_when_sign_changes():
    cs = [_c("P", 100, 30000, 5), _c("C", 110, 30000, 5)]
    r = gex.compute_gex(cs, 100.0, 45, NOW)
    assert r["gamma_flip"] is not None
    assert 90 <= r["gamma_flip"] <= 110


def test_dte_window_filters_and_empty():
    cs = [_c("C", 100, 1000, 10)]
    assert gex.compute_gex(cs, 100.0, 1, NOW) is None
    assert gex.compute_gex(cs, 100.0, 45, NOW) is not None
    assert gex.compute_gex([_c("C", 100, 0)], 100.0, 45, NOW) is None


def test_zero_dte_uses_time_left():
    assert gex._t_years(0, NOW) < gex._t_years(1, NOW)


def test_summary_and_block(monkeypatch):
    res = gex.build_gex([_c("C", 105, 5000, 0), _c("P", 95, 5000, 0)], 100.0, "test", NOW)
    assert res["descriptive_only"] and res["intraday"]
    assert "net" in gex.gex_summary_line(res)
    monkeypatch.setattr(gex, "fetch_gex", lambda t: res)
    block = gex.format_gex_block("XYZ")
    assert "descriptive only" in block and "Unmeasured" in block
    assert gex.gex_summary_line({"status": "error"}) == "unavailable"


def test_ledger_written_once_per_bucket(tmp_path, monkeypatch):
    monkeypatch.delenv("GEX_NO_LEDGER", raising=False)
    monkeypatch.setattr(gex, "_LEDGER_PATH", str(tmp_path / "l.jsonl"))
    monkeypatch.setattr(gex, "_LEDGER_SEEN", set())
    res = gex.build_gex([_c("C", 105, 5000, 0), _c("P", 95, 5000, 0)], 100.0, "test", NOW)
    gex._record_ledger("XYZ", res)
    gex._record_ledger("XYZ", res)
    assert len((tmp_path / "l.jsonl").read_text().splitlines()) == 1
