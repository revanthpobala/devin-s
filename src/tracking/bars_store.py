"""Batched daily bars from Alpaca (raw prices, one request per 100 symbols) with a short in-memory cache."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List

import requests

logger = logging.getLogger(__name__)

CHUNK = 100
TTL_SECONDS = 600
_CACHE: Dict[str, tuple] = {}


def _creds():
    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY")
    url = (os.getenv("ALPACA_DATA_URL") or "https://data.alpaca.markets").strip().rstrip("/")
    return key, secret, url.replace("paper-api.", "data.").replace("//api.", "//data.")


def fetch_daily(tickers: Iterable[str], days: int = 60) -> Dict[str, List[Dict[str, Any]]]:
    """{ticker: [{date, open, high, low, close, volume}]}. Missing tickers are absent; never raises."""
    names = sorted({str(t).upper() for t in tickers if t})
    now = time.time()
    out = {t: _CACHE[t][1] for t in names if t in _CACHE and now - _CACHE[t][0] < TTL_SECONDS}
    todo = [t for t in names if t not in out]
    key, secret, url = _creds()
    if not todo or not key or not secret:
        return out
    start = (date.today() - timedelta(days=days)).isoformat()
    for i in range(0, len(todo), CHUNK):
        params = {"symbols": ",".join(todo[i:i + CHUNK]), "timeframe": "1Day", "start": start, "limit": 10000,
                  "adjustment": "raw", "feed": os.getenv("ALPACA_BARS_FEED", "sip")}
        got: Dict[str, List[Dict[str, Any]]] = {}
        try:
            while True:
                r = requests.get(f"{url}/v2/stocks/bars", params=params, timeout=20,
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
                if r.status_code != 200:
                    logger.warning(f"[bars_store] Alpaca {r.status_code}: {r.text[:120]}")
                    break
                body = r.json()
                for sym, bars in (body.get("bars") or {}).items():
                    got.setdefault(sym, []).extend(
                        {"date": b["t"][:10], "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"],
                         "volume": b.get("v", 0)} for b in bars)
                if not body.get("next_page_token"):
                    break
                params["page_token"] = body["next_page_token"]
        except Exception as e:
            logger.warning(f"[bars_store] Alpaca batch failed: {type(e).__name__} {e}")
        for sym, bars in got.items():
            _CACHE[sym] = (now, bars)
            out[sym] = bars
    return out
