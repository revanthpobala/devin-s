"""
tests/test_darkpool.py — Unit and integration tests for On-Demand Dark Pool engine and API.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.data.darkpool import fetch_darkpool_data, format_darkpool_block, _CACHE
from src.ui.app import create_app


@pytest.fixture(autouse=True)
def _no_ledger(monkeypatch):
    monkeypatch.setenv("DARKPOOL_NO_LEDGER", "1")


@pytest.fixture
def test_client():
    app = create_app()
    return TestClient(app)


def test_darkpool_missing_creds():
    with patch("src.data.darkpool._get_alpaca_creds", return_value=(None, None, "https://data.alpaca.markets")):
        res = fetch_darkpool_data("SPY", force_refresh=True)
        assert res["status"] == "error"
        assert "not configured" in res["message"]


def test_darkpool_invalid_ticker(test_client):
    res = test_client.get("/api/darkpool/INVALID$$$")
    assert res.status_code == 400


def test_darkpool_mocked_aggregation():
    sample_trades = [
        # Regular lit trade (exchange 'P' -> Arca)
        {"t": "2026-10-22T14:30:00Z", "x": "P", "p": 262.45, "s": 100, "c": ["@"]},
        # Dark Pool / Off-Exchange trades (exchange 'D' -> FINRA TRF)
        {"t": "2026-10-22T14:30:01Z", "x": "D", "p": 262.45, "s": 50000, "c": ["@"]},
        {"t": "2026-10-22T14:30:02Z", "x": "D", "p": 262.45, "s": 25000, "c": ["@"]},
        {"t": "2026-10-22T14:30:03Z", "x": "D", "p": 262.10, "s": 10000, "c": ["@"]},
        {"t": "2026-10-22T14:30:04Z", "x": "D", "p": 262.63, "s": 5000, "c": ["@"]},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": sample_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("test_key", "test_secret", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):

        res = fetch_darkpool_data("TEST", force_refresh=True)

        assert res["status"] == "ok"
        assert res["ticker"] == "TEST"
        assert res["dp_trades_count"] == 4  # Only 4 have x == 'D'
        assert res["dp_total_volume"] == 90000  # 50k + 25k + 10k + 5k

        # Check ladder
        ladder = res["ladder"]
        assert len(ladder) == 3  # 262.63, 262.45, 262.10
        # Sorted descending by price
        assert ladder[0]["price"] == 262.63
        assert ladder[1]["price"] == 262.45
        assert ladder[2]["price"] == 262.10

        # Check 262.45 cluster (50k + 25k = 75k shares, 2 prints)
        cluster = ladder[1]
        assert cluster["prints"] == 2
        assert cluster["volume"] == 75000
        assert cluster["is_major_shelf"] is True

        # Check signature prints
        sigs = res["signature_prints"]
        assert len(sigs) >= 2
        assert sigs[0]["shares"] == 50000


def test_format_darkpool_block():
    sample_trades = [
        {"t": "2026-10-22T14:30:01Z", "x": "D", "p": 150.00, "s": 20000, "c": ["@"]},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": sample_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("test_key", "test_secret", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):
        block = format_darkpool_block("AAPL")
        assert "OFF-EXCHANGE (FINRA TRF) TAPE (AAPL)" in block
        assert "DESCRIPTIVE ONLY" in block
        assert "$150.00" in block
        assert "20,000 shares" in block


def test_darkpool_api_endpoint(test_client):
    sample_trades = [
        {"t": "2026-10-22T14:30:01Z", "x": "D", "p": 100.50, "s": 5000, "c": ["@"]},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": sample_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("test_key", "test_secret", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):
        res = test_client.get("/api/darkpool/MSFT?force=true")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["ticker"] == "MSFT"
        assert "markdown_summary" in data


def test_darkpool_ai_summary_endpoint(test_client):
    sample_trades = [
        {"t": "2026-10-22T14:30:01Z", "x": "D", "p": 100.50, "s": 5000, "c": ["@"]},
        {"t": "2026-10-22T14:30:02Z", "x": "D", "p": 100.50, "s": 3000, "c": ["@"]},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": sample_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("test_key", "test_secret", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp), \
         patch("socket.create_connection", side_effect=OSError("Offline")):
        res = test_client.get("/api/darkpool/MSFT/ai-summary?force=true")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["ticker"] == "MSFT"
        assert "summary" in data
        assert "Net Institutional Bias" in data["summary"]
        assert data["provider"] in ("rules_engine", "local_llm")


def test_darkpool_chronological_reversal_and_tick_rule():
    # Alpaca returns trades newest first when sort='desc'
    # Trade 1 (older): $100.50
    # Trade 2 (newer): $100.00 (downtick -> SELL)
    # If descending order were evaluated directly without reversal:
    # 100.00 would be first, then 100.50 would be an uptick (BUY) - which would be WRONG!
    # By reversing, 100.50 is first, then 100.00 is a downtick (SELL) - which is CORRECT!
    descending_trades = [
        {"t": "2026-10-23T14:30:05Z", "x": "D", "p": 100.00, "s": 1000, "c": ["@"]},  # newer (downtick)
        {"t": "2026-10-23T14:30:00Z", "x": "D", "p": 100.50, "s": 1000, "c": ["@"]},  # older
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": descending_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("key", "sec", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):
        res = fetch_darkpool_data("TEST_REV", force_refresh=True)
        assert res["status"] == "ok"
        # Chronological order: 100.50 first (no prior tick -> unclassified), then 100.00 downtick (SELL)
        assert res["dp_buy_volume"] == 0
        assert res["dp_sell_volume"] == 1000
        assert res["dp_unclassified_volume"] == 1000


def test_darkpool_non_tick_condition_filtering():
    # Trade 1: $100.00 (regular)
    # Trade 2: $95.00 with condition '4' (options derivative / combo) -> should NOT update baseline
    # Trade 3: $100.20 (regular) -> compared to Trade 1 ($100.00), this is an uptick (BUY), NOT a buy from $95
    descending_trades = [
        {"t": "2026-10-23T14:30:10Z", "x": "D", "p": 100.20, "s": 1000, "c": ["@"]},
        {"t": "2026-10-23T14:30:05Z", "x": "D", "p": 95.00, "s": 500, "c": ["4"]},
        {"t": "2026-10-23T14:30:00Z", "x": "D", "p": 100.00, "s": 1000, "c": ["@"]},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": descending_trades}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("key", "sec", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):
        res = fetch_darkpool_data("TEST_COND", force_refresh=True)
        assert res["status"] == "ok"
        # 100.00 unclassified (first), 100.20 uptick vs 100.00 (the cond-4 print is ignored) -> BUY
        assert res["dp_buy_volume"] == 1000
        assert res["dp_sell_volume"] == 0
        assert [l["price"] for l in res["ladder"]] == [100.2, 100.0]


def test_darkpool_preserves_symbol_dot():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": []}

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("key", "sec", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp) as mock_get:
        fetch_darkpool_data("BRK.B", force_refresh=True)
        called_url = mock_get.call_args[0][0]
        # Must preserve BRK.B rather than converting to BRK/B
        assert "BRK.B" in called_url
        assert "BRK/B" not in called_url


def test_darkpool_tastytrade_enrichment():
    sample_trades = [
        {"t": "2026-10-23T14:30:00Z", "x": "D", "p": 150.00, "s": 5000, "c": ["@"]},
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": sample_trades}

    mock_metrics = [{
        "symbol": "ENRICH_TEST",
        "implied-volatility-index-rank": 0.655,
        "implied-volatility-percentile": 0.820,
        "historical-volatility-30-day": 21.4,
        "beta": 1.15,
    }]

    mock_tt = MagicMock()
    mock_tt.get_market_metrics.return_value = mock_metrics

    with patch("src.data.darkpool._get_alpaca_creds", return_value=("key", "sec", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp), \
         patch("src.clients.tastytrade_client.get_tastytrade_client", return_value=mock_tt):
        res = fetch_darkpool_data("ENRICH_TEST", force_refresh=True)
        assert res["status"] == "ok"
        assert "tastytrade_volatility" in res
        tt_vol = res["tastytrade_volatility"]
        assert tt_vol["iv_rank"] == 65.5
        assert tt_vol["iv_percentile"] == 82.0
        assert tt_vol["hv_30d"] == 21.4


def test_darkpool_sync_tastytrade_alerts_disabled(test_client):
    mock_tt = MagicMock()
    with patch("src.clients.tastytrade_client.get_tastytrade_client", return_value=mock_tt):
        res = test_client.post("/api/darkpool/TEST/sync-tastytrade-alerts")
    assert res.status_code == 200
    assert res.json()["status"] == "disabled"
    mock_tt.create_quote_alert.assert_not_called()


def _run(trades, **kw):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"trades": trades}
    with patch("src.data.darkpool._get_alpaca_creds", return_value=("k", "s", "https://data.alpaca.markets")), \
         patch("requests.get", return_value=mock_resp):
        return fetch_darkpool_data("SYN", force_refresh=True, **kw)


def _d(sec, price, size, cond=("@",), x="D", hh="14", mm="30"):
    return {"t": f"2026-10-23T{hh}:{mm}:{sec:02d}Z", "x": x, "p": price, "s": size, "c": list(cond)}


def test_flat_tape_is_not_accumulation():
    res = _run([_d(i, 100.00, 500) for i in range(10)][::-1])
    assert res["dp_buy_volume"] == 0 and res["dp_sell_volume"] == 0
    assert res["dp_buy_pct"] == 50.0
    assert res["sentiment"] == "INSUFFICIENT CLASSIFIED FLOW"


def test_benchmark_print_excluded_from_ladder_and_blocks():
    res = _run([_d(0, 100.0, 500), _d(1, 90.0, 50000, ("W",)), _d(2, 100.1, 500)][::-1])
    assert [l["price"] for l in res["ladder"]] == [100.1, 100.0]
    assert res["signature_prints"] == [] and res["major_shelves"] == []
    assert res["dp_nonprice_volume"] == 50000 and res["dp_total_volume"] == 51000


def test_odd_lots_excluded_from_ladder_but_counted_in_total():
    res = _run([_d(i, 100.0 + 0.01 * (i % 2), 50, ("I",)) for i in range(20)][::-1])
    assert res["dp_total_volume"] == 1000 and res["dp_odd_lot_volume"] == 1000
    assert res["ladder"] == [] and res["dp_ladder_volume"] == 0


def test_extended_hours_and_premarket_dropped():
    res = _run([
        _d(0, 100.0, 1000, hh="12", mm="00"),            # 08:00 ET pre-market
        _d(1, 100.0, 1000, ("T",)),                      # extended-hours condition
        _d(2, 101.0, 1000),                              # regular
        _d(3, 101.0, 1000, hh="21", mm="00"),            # 17:00 ET after-hours
    ][::-1])
    assert res["total_trades_scanned"] == 1 and res["dp_total_volume"] == 1000


def test_block_needs_both_shares_and_notional():
    assert _run([_d(0, 20.0, 2500)])["signature_prints"] == []        # $50k
    assert _run([_d(0, 1000.0, 250)])["signature_prints"] == []       # 250 sh < 1,000
    assert len(_run([_d(0, 300.0, 1000)])["signature_prints"]) == 1   # $300k


def test_window_metadata_and_unsorted_input():
    res = _run([_d(30, 100.5, 1000), _d(0, 100.0, 1000), _d(10, 100.2, 1000)])
    assert res["window_minutes"] == 0.5 and res["window_is_full_session"] is False
    assert res["descriptive_only"] is True
    assert res["dp_buy_volume"] == 2000 and res["dp_sell_volume"] == 0   # chronological ticks: up, up


def test_ledger_row_written(tmp_path, monkeypatch):
    import json
    import src.data.darkpool as dp
    monkeypatch.delenv("DARKPOOL_NO_LEDGER", raising=False)
    monkeypatch.setattr(dp, "_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    dp._LEDGER_SEEN.clear()
    _run([_d(0, 100.0, 1000), _d(10, 100.2, 5000)][::-1])
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["ticker"] == "SYN" and "dp_buy_pct" in row and "shelves" in row and row["n_blocks"] == 1
