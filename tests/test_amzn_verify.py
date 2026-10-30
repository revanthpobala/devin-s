"""
tests/test_amzn_verify.py — End-to-end verification for AMZN dark pool integration.
"""

from unittest.mock import patch
from src.data.darkpool import fetch_darkpool_data, format_darkpool_block
from src.ui.routes.research import get_darkpool_data_endpoint
from src.logic.deep_research.live_fetcher import fetch_live_context


def test_amzn_darkpool_engine():
    res = fetch_darkpool_data("AMZN", force_refresh=True)
    assert res["status"] == "ok"
    assert res["ticker"] == "AMZN"
    assert "dp_trades_count" in res
    assert "ladder" in res
    assert "sentiment" in res
    assert "major_shelves" in res
    assert "signature_prints" in res


def test_amzn_darkpool_formatter():
    md = format_darkpool_block("AMZN")
    assert "OFF-EXCHANGE (FINRA TRF) TAPE" in md
    assert "Off-exchange volume clusters in window" in md


def test_amzn_darkpool_api_endpoint():
    api_res = get_darkpool_data_endpoint("AMZN", force=True)
    assert api_res["status"] == "ok"
    assert api_res["ticker"] == "AMZN"
    assert "markdown_summary" in api_res
    assert "ladder" in api_res


def test_amzn_live_context_integration(tmp_path):
    tdir = tmp_path / "triage"
    raw_dir = tmp_path / "raw"
    tdir.mkdir()
    raw_dir.mkdir()
    dossier = tdir / "dossier.md"
    dossier.write_text("dummy", encoding="utf-8")

    paths = {
        "dossier": dossier,
        "chart": raw_dir / "chart.png",
        "zoom": raw_dir / "zoom.png",
        "csv": raw_dir / "dw.csv",
        "json": raw_dir / "dw.json",
        "quote": raw_dir / "quote.json",
        "gex": raw_dir / "gex.json",
    }
    with patch("src.data.darkpool.format_darkpool_block", return_value="### OFF-EXCHANGE / DARK POOL ORDER FLOW (Alpaca SIP FINRA TRF)\nMock shelves"), \
         patch("src.logic.deep_research.live_fetcher._load_or_fetch_quote", return_value="Quote Mock"), \
         patch("src.logic.deep_research.live_fetcher._load_or_fetch_gex", return_value="GEX Mock"), \
         patch("src.clients.alphavantage_client.format_av_block", return_value=""), \
         patch("src.clients.finnhub_client.format_finnhub_institutional_block", return_value=""):
        ctx = fetch_live_context("AMZN", tdir, raw_dir, "2026-10-24", dossier, paths, {})
        assert ctx.darkpool_block is not None
        assert "OFF-EXCHANGE / DARK POOL ORDER FLOW" in ctx.darkpool_block
