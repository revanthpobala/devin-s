"""Screener universe: Schwab 1000, S&P 400 / S&P 600 ETF holdings, the Russell 2000 list and manual extras, with a runtime price band.

Holdings come from the SPDR daily files and are cached for UNIVERSE_REFRESH_DAYS; a failed download falls back to the
stale cache. A name priced above MAX_PRICE is only kept when its market cap is at least LARGE_CAP_MIN_MCAP, looked up
once per UNIVERSE_MCAP_TTL_DAYS and cached.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional

from src import config

logger = logging.getLogger("universe")

MIN_PRICE = 10.0
MAX_PRICE = 500.0
LARGE_CAP_MIN_MCAP = 100e9
UNIVERSE_REFRESH_DAYS = 7
UNIVERSE_MCAP_TTL_DAYS = 14
PROFILE_TTL_DAYS = 14
EXCLUDED_SECTORS: set = set()
MIN_MCAP = 5e9
MIN_DOLLAR_VOL = 25e6

ETF_HOLDINGS = {
    "SPMD": "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spmd.xlsx",
    "SPSM": "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spsm.xlsx",
}
_JUNK_NAME = re.compile(r"EARNOUT|E-MINI|CVR|CASH|USD", re.I)
_TICKER = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")


def _schwab_csv():
    return config.BASE_DIR / "All_tickrs" / "Schwab_1000_Index®_Index_Constituents.csv"


def _r2k_file():
    return config.BASE_DIR / "All_tickrs" / "russell_2000_components.csv"


def _extras_file():
    return config.BASE_DIR / "All_tickrs" / "universe_extras.txt"


def _cache_dir():
    return config.BASE_DIR / "data" / "universe"


def _etf_file(etf: str):
    CACHE_DIR = _cache_dir()
    path = CACHE_DIR / f"{etf.lower()}_holdings.xlsx"
    if path.exists() and time.time() - path.stat().st_mtime < UNIVERSE_REFRESH_DAYS * 86400:
        return path
    try:
        import requests

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        r = requests.get(ETF_HOLDINGS[etf], headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    except Exception as e:
        logger.warning("%s holdings download failed (%s); %s", etf, e, "using stale cache" if path.exists() else "skipped")
        return path if path.exists() else None
    return path


def _etf_constituents(etf: str) -> Dict[str, str]:
    path = _etf_file(etf)
    if path is None:
        return {}
    import pandas as pd

    try:
        df = pd.read_excel(path, header=4).dropna(subset=["Ticker"])
    except Exception as e:
        logger.warning("%s holdings unreadable: %s", etf, e)
        return {}
    out = {}
    for name, tk in zip(df["Name"].astype(str), df["Ticker"].astype(str)):
        tk = tk.strip().upper()
        if _TICKER.fullmatch(tk) and not _JUNK_NAME.search(name):
            out[tk] = name.strip()
    return out


def _all_constituents() -> Dict[str, Dict[str, str]]:
    """symbol (dot form, e.g. BRK.B) -> {name, source}: Schwab 1000, S&P 400/600, Russell 2000, extras."""
    import csv

    out: Dict[str, Dict[str, str]] = {}
    if _schwab_csv().exists():
        with open(_schwab_csv(), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sym = (row.get("Ticker") or "").strip().upper()
                if sym and not sym.startswith("^") and sym != "NAN" and sym not in out:
                    out[sym] = {"name": (row.get("Name") or "").strip(), "source": "SCHWAB1000"}
    for etf in ETF_HOLDINGS:
        for sym, name in _etf_constituents(etf).items():
            out.setdefault(sym, {"name": name, "source": etf})
    r2k = _r2k_file()
    if r2k.exists():
        import pandas as pd

        df = pd.read_csv(r2k, comment=None)
        if "Ticker" in df:
            for sym, name in zip(df["Ticker"].astype(str), df.get("Name", df["Ticker"]).astype(str)):
                sym = sym.strip().upper()
                if _TICKER.fullmatch(sym) and not _JUNK_NAME.search(name):
                    out.setdefault(sym, {"name": name.strip(), "source": "R2000"})
    if _extras_file().exists():
        for line in _extras_file().read_text(encoding="utf-8").splitlines():
            sym = line.split("#")[0].strip().upper()
            if _TICKER.fullmatch(sym):
                out[sym] = {"name": "", "source": "EXTRA"}
    return out


def _yf(sym: str) -> str:
    return sym.replace("/", "-").replace(".", "-").upper()


def _profiles() -> Dict[str, list]:
    try:
        return json.loads((_cache_dir() / "profiles.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fetch_profile(sym: str) -> Optional[dict]:
    import yfinance as yf

    for _ in range(3):
        try:
            i = yf.Ticker(_yf(sym)).info
            px = i.get("currentPrice") or i.get("regularMarketPrice") or i.get("previousClose") or 0.0
            return {
                "sector": i.get("sector") or "",
                "industry": i.get("industry") or "",
                "mcap": float(i.get("marketCap") or 0.0),
                "dollar_vol": float(i.get("averageVolume") or 0.0) * float(px),
                "price": float(px),
            }
        except Exception:
            time.sleep(1.0)
    return None


def refresh_profiles(symbols: Optional[List[str]] = None, workers: int = 12) -> int:
    """Fetch sector, market cap and dollar volume for symbols without a fresh profile. Run offline, not per scan."""
    from concurrent.futures import ThreadPoolExecutor

    cache = _profiles()
    now = time.time()
    todo = [s for s in (symbols or list(_all_constituents())) if _yf(s) not in cache or now - cache[_yf(s)][0] > PROFILE_TTL_DAYS * 86400]
    with ThreadPoolExecutor(workers) as ex:
        for sym, prof in zip(todo, ex.map(_fetch_profile, todo)):
            if prof is not None:
                cache[_yf(sym)] = [now, prof]
    _cache_dir().mkdir(parents=True, exist_ok=True)
    (_cache_dir() / "profiles.json").write_text(json.dumps(cache), encoding="utf-8")
    return len(todo)


def quality_ok(p: dict) -> bool:
    return (
        p.get("sector") not in EXCLUDED_SECTORS
        and bool(p.get("sector"))
        and p.get("mcap", 0.0) >= MIN_MCAP
        and p.get("dollar_vol", 0.0) >= MIN_DOLLAR_VOL
    )


def load_constituents(filtered: bool = True) -> Dict[str, Dict[str, str]]:
    """The screener universe. Filtered: only names with a cached profile that passes the quality gate
    (non-healthcare, market cap and dollar volume floors). Extras always stay. With no profile cache yet
    it falls back to the Schwab 1000 plus extras, so the Schwab API is never asked for the unfiltered list."""
    full = _all_constituents()
    if not filtered:
        return full
    prof = _profiles()
    if not prof:
        return {s: v for s, v in full.items() if v["source"] in ("SCHWAB1000", "EXTRA")}
    return {
        s: v for s, v in full.items()
        if v["source"] == "EXTRA" or (_yf(s) in prof and quality_ok(prof[_yf(s)][1]))
    }


def load_tickers() -> List[str]:
    """Screener symbols in slash form (BRK/B), deduplicated, in universe order."""
    return [s.replace(".", "/") for s in load_constituents()]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="fetch missing sector/market-cap profiles (yfinance)")
    a = ap.parse_args()
    if a.refresh:
        print(f"profiled {refresh_profiles()} symbols")
    print(f"{len(load_tickers())} tickers pass the quality gate")


def _mcap_cache() -> Dict[str, list]:
    try:
        return json.loads((_cache_dir() / "mcap_cache.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def market_cap(symbol: str) -> Optional[float]:
    sym = symbol.replace("/", "-").replace(".", "-").upper()
    cache = _mcap_cache()
    hit = cache.get(sym)
    if hit and time.time() - hit[0] < UNIVERSE_MCAP_TTL_DAYS * 86400:
        return hit[1]
    try:
        import yfinance as yf

        mc = yf.Ticker(sym).fast_info["marketCap"]
        mc = float(mc) if mc else None
    except Exception as e:
        logger.warning("market cap lookup failed for %s: %s", sym, e)
        mc = None
    if mc:
        cache[sym] = [time.time(), mc]
        _cache_dir().mkdir(parents=True, exist_ok=True)
        (_cache_dir() / "mcap_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    return mc


def price_band_reject(symbol: str, price: float) -> Optional[str]:
    """None when the name may proceed, else the rejection key."""
    if price < MIN_PRICE:
        return "price_under_min"
    if price > MAX_PRICE:
        mc = market_cap(symbol)
        if mc is None or mc < LARGE_CAP_MIN_MCAP:
            return "price_over_max_not_large_cap"
    return None
