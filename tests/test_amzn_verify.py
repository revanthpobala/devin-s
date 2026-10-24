"""
tests/test_amzn_verify.py — End-to-end verification for AMZN dark pool integration.
"""

from src.data.darkpool import fetch_darkpool_data, format_darkpool_block
from src.ui.routes.research import get_darkpool_data_endpoint
from src.logic.deep_research.live_fetcher import fetch_live_context


def test_amzn_darkpool_engine():
    res = fetch_darkpool_data("AMZN", force_refresh=True)
    assert res["status"] == "ok"
    assert res["ticker"] == "AMZN"
    assert res["dp_trades_count"] > 0
    assert len(res["ladder"]) > 0
    assert "sentiment" in res
    assert "major_shelves" in res
    assert "signature_prints" in res


def test_amzn_darkpool_formatter():
    md = format_darkpool_block("AMZN")
    assert "OFF-EXCHANGE / DARK POOL ORDER FLOW" in md
    assert "Key Institutional Defense / Resistance Shelves" in md


def test_amzn_darkpool_api_endpoint():
    api_res = get_darkpool_data_endpoint("AMZN", force=True)
    assert api_res["status"] == "ok"
    assert api_res["ticker"] == "AMZN"
    assert "markdown_summary" in api_res
    assert len(api_res["ladder"]) > 0


def test_amzn_live_context_integration():
    ctx = fetch_live_context("AMZN")
    assert ctx.darkpool_block is not None
    assert "OFF-EXCHANGE / DARK POOL ORDER FLOW" in ctx.darkpool_block
