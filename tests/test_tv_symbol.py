from src.data.tv_scraper import tv_symbol


def test_bare_us_tickers_get_bats():
    assert [tv_symbol(s) for s in ("HAL", "dow", "PPL", "EG", "BRK.B", "BF-B")] == [
        "BATS:HAL", "BATS:DOW", "BATS:PPL", "BATS:EG", "BATS:BRK.B", "BATS:BF-B"]


def test_qualified_and_non_equity_symbols_unchanged():
    assert [tv_symbol(s) for s in ("NYSE:HAL", "SPX", "VIX", "$SPX", "BTCUSD")] == ["NYSE:HAL", "SPX", "VIX", "$SPX", "BTCUSD"]
