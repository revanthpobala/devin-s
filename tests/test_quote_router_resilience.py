import threading
import time

from src.clients.quote_router import QuoteData, QuoteRouter


def _router():
    r = QuoteRouter()
    r._fetch_alpaca_snapshot_single = lambda s: None
    r._schwab_quotes = lambda syms: {}
    r._fetch_tastytrade_quote = lambda s: None
    return r


def test_hanging_yfinance_is_bounded_and_negative_cached():
    r = _router()
    r._fetch_yahoo_direct = lambda s, is_index=False: None
    r._fetch_yfinance_fast = lambda s: threading.Event().wait(10)
    r.slow_timeout = 0.3
    t = time.time()
    assert r.get_watchlist_quote("ZZZZ") is None
    assert time.time() - t < 2
    t = time.time()
    assert r.get_watchlist_quote("ZZZZ") is None
    assert time.time() - t < 0.1


def test_stale_quote_served_when_providers_fail():
    r = _router()
    r._fetch_yahoo_direct = lambda s, is_index=False: None
    r._fetch_yfinance_fast = lambda s: None
    r._cache_surveillance["AAPL"] = (time.time() - 60, QuoteData(symbol="AAPL", last_price=200.0, source="YAHOO_DIRECT"))
    q = r.get_watchlist_quote("AAPL")
    assert q and q.last_price == 200.0 and q.source.endswith("_STALE")


def test_yahoo_breaker_skips_calls_when_open():
    r = QuoteRouter()
    r.yahoo_breaker.force_cooldown(60, "test")
    assert r._fetch_yahoo_direct("AAPL") is None


def test_alpaca_bad_print_far_from_bid_ask_is_replaced_by_mid(monkeypatch):
    import requests

    r = QuoteRouter()
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")

    class Resp:
        status_code = 200

        def json(self):
            return {"AAPL": {"latestTrade": {"p": 361.26}, "latestQuote": {"bp": 342.8, "ap": 343.0},
                             "dailyBar": {}, "prevDailyBar": {"c": 341.27}}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    q = r._fetch_alpaca_snapshot_single("AAPL")
    assert q and abs(q.last_price - 342.9) < 0.01


def test_tastytrade_batch_is_first_source_and_maps_change(monkeypatch):
    import src.clients.tastytrade_client as tt

    class Fake:
        def get_realtime_quotes_batch(self, syms, timeout=6.0):
            return {s: {"last_price": 110.0, "bid": 109.9, "ask": 110.1, "prev_close": 100.0} for s in syms}

    monkeypatch.setattr(tt, "get_tastytrade_client", lambda: Fake())
    r = _router()
    r._fetch_alpaca_snapshots_batch = lambda syms: (_ for _ in ()).throw(AssertionError("alpaca not needed"))
    out = r.get_watchlist_quotes_batch(["AAA", "BBB"])
    assert out["AAA"].source == "TASTYTRADE" and out["AAA"].net_percent_change == 10.0
    assert set(out) == {"AAA", "BBB"}
