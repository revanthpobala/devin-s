"""A DXLink Summary event alone (prevDayClosePrice) must never be reported as the live price."""
import json
import sys
import types

from src.clients.tastytrade_client import TastytradeClient


class _FakeWS:
    def __init__(self, feed):
        self._msgs = [{"type": "AUTH_STATE", "state": "AUTHORIZED"}, {"type": "CHANNEL_OPENED"},
                      {"type": "FEED_DATA", "data": feed}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, _):
        pass

    async def recv(self):
        if self._msgs:
            return json.dumps(self._msgs.pop(0))
        raise TimeoutError


def _quotes(monkeypatch, feed):
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *a, **k: _FakeWS(feed)))
    client = TastytradeClient.__new__(TastytradeClient)
    import threading
    client._lock = threading.Lock()
    monkeypatch.setattr(client, "_get_quote_token", lambda: {"dxlink-url": "wss://x", "token": "t"}, raising=False)
    return client.get_realtime_quotes_batch(["AMZN"], timeout=0.5)


def test_summary_only_is_not_a_price(monkeypatch):
    feed = ["Summary", ["AMZN", 250.69, 264.78, 271.49, 263.94]]
    assert "AMZN" not in _quotes(monkeypatch, feed)


def test_trade_price_wins_over_prev_close(monkeypatch):
    feed = ["Summary", ["AMZN", 250.69, 264.78, 271.49, 263.94], "Trade", ["AMZN", 268.71, 62758355]]
    q = _quotes(monkeypatch, feed)["AMZN"]
    assert q["last_price"] == 268.71 and q["prev_close"] == 250.69
