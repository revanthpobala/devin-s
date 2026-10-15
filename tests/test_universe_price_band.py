from src.screener import universe


def test_price_band(monkeypatch):
    caps = {"META": 1.6e12, "AZO": 55e9}
    monkeypatch.setattr(universe, "market_cap", lambda s: caps.get(s))
    assert universe.price_band_reject("X", 9.99) == "price_under_min"
    assert universe.price_band_reject("X", 10.0) is None
    assert universe.price_band_reject("X", 500.0) is None
    assert universe.price_band_reject("META", 700.0) is None
    assert universe.price_band_reject("AZO", 3000.0) == "price_over_max_not_large_cap"
    assert universe.price_band_reject("UNKNOWN", 600.0) == "price_over_max_not_large_cap"


def test_constituents_include_etf_names_and_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "_schwab_csv", lambda: tmp_path / "none.csv")
    monkeypatch.setattr(universe, "_r2k_file", lambda: tmp_path / "none_r2k.csv")
    monkeypatch.setattr(universe, "_extras_file", lambda: tmp_path / "none.txt")
    monkeypatch.setattr(universe, "_etf_constituents", lambda etf: {"OKTA": "OKTA INC", "BRK.B": "x"} if etf == "SPMD" else {"OKTA": "dup", "AAOI": "APPLIED OPTO"})
    out = universe.load_constituents(filtered=False)
    assert out["OKTA"]["source"] == "SPMD" and "AAOI" in out
    assert "BRK/B" in [k.replace(".", "/") for k in universe.load_constituents(filtered=False)]


def test_extras_file_adds_names(tmp_path, monkeypatch):
    f = tmp_path / "extras.txt"
    f.write_text("# note\naaoi\nBAD TICKER\n", encoding="utf-8")
    monkeypatch.setattr(universe, "_extras_file", lambda: f)
    monkeypatch.setattr(universe, "_schwab_csv", lambda: tmp_path / "none.csv")
    monkeypatch.setattr(universe, "_r2k_file", lambda: tmp_path / "none_r2k.csv")
    monkeypatch.setattr(universe, "_etf_constituents", lambda etf: {})
    assert universe.load_constituents(filtered=False)["AAOI"]["source"] == "EXTRA"


def test_russell_list_is_a_source(tmp_path, monkeypatch):
    f = tmp_path / "r2k.csv"
    f.write_text("Ticker,Name\nAAOI,Applied Optoelectronics Inc\nXX,CASH USD\n", encoding="utf-8")
    monkeypatch.setattr(universe, "_r2k_file", lambda: f)
    monkeypatch.setattr(universe, "_schwab_csv", lambda: tmp_path / "none.csv")
    monkeypatch.setattr(universe, "_extras_file", lambda: tmp_path / "none.txt")
    monkeypatch.setattr(universe, "_etf_constituents", lambda etf: {})
    out = universe.load_constituents(filtered=False)
    assert out["AAOI"]["source"] == "R2000" and "XX" not in out


def test_quality_gate_and_filtered_universe(tmp_path, monkeypatch):
    full = {
        "GOOD": {"name": "", "source": "R2000"}, "BIO": {"name": "", "source": "R2000"}, "MID": {"name": "", "source": "R2000"},
        "SMALL": {"name": "", "source": "SPSM"}, "NOPROF": {"name": "", "source": "SPMD"},
        "AAOI": {"name": "", "source": "EXTRA"},
    }
    ok = {"sector": "Technology", "mcap": 5e9, "dollar_vol": 80e6}
    prof = {
        "GOOD": [0, ok], "BIO": [0, {**ok, "sector": "Healthcare", "industry": "Biotechnology"}], "MID": [0, {**ok, "mcap": 4.9e9}],
        "SMALL": [0, {**ok, "mcap": 5e8}],
    }
    monkeypatch.setattr(universe, "_all_constituents", lambda: full)
    monkeypatch.setattr(universe, "_profiles", lambda: prof)
    assert set(universe.load_constituents()) == {"GOOD", "BIO", "AAOI"}
    monkeypatch.setattr(universe, "_profiles", lambda: {})
    assert set(universe.load_constituents()) == {"AAOI"}
