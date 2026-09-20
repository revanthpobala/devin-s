"""
src/clients/options_client.py

Live options data for the deep-research tool-calling flow.

The PAID deep-research model (Minimax) drives these tools directly via tool calls:
  1. get_realtime_quote   — live price / day range / 52-week range
  2. fetch_options_chain_tool — live option chain (strikes, bids/asks, greeks,
     volume) for a given direction / strike band / DTE window.

The chain is sourced from Alpaca's LIVE option snapshot endpoint:
    GET https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}
yfinance is retained as a fallback when the Alpaca call fails.

NOTE: Alpaca's chain snapshot does NOT include implied volatility or open
interest inline. IV/OI are intentionally omitted from the Alpaca table; greeks
(delta/gamma/theta/vega) are included when the feed provides them.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# Fast in-memory cache for live options chains (TTL: 120s)
_CHAIN_MEM_CACHE: Dict[str, Tuple[float, str]] = {}

# Set by the pipeline (deep_research) so the live tools can fall back to the
# active ticker when the model omits the argument in a tool call.
_ACTIVE_TICKER = None

# Live market-data host for options. Alpaca's option chain lives on the data
# host, not the paper/live trading API. Prefer a dedicated live key when set.
ALPACA_OPTIONS_DATA_HOST = os.getenv("ALPACA_OPTIONS_DATA_HOST", "https://data.alpaca.markets")


def set_active_ticker(ticker: str):
    global _ACTIVE_TICKER
    _ACTIVE_TICKER = ticker


def _alpaca_creds():
    """Options use the live account when a dedicated key is provided, otherwise
    fall back to the general Alpaca credentials."""
    key = (
        os.getenv("ALPACA_OPTIONS_KEY") or os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
    )
    secret = os.getenv("ALPACA_OPTIONS_SECRET") or os.getenv("ALPACA_SECRET_KEY")
    return key, secret


def _safe_int(v) -> int:
    """Coerce volume/values (which can be NaN or numpy types) to int."""
    try:
        if v is None:
            return 0
        f = float(v)
        if f != f:  # NaN check
            return 0
        return int(f)
    except (TypeError, ValueError):
        return 0


def _safe_float(v):
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_occ_symbol(symbol: str):
    """Parse an OCC option symbol (root + YYMMDD + C/P + 8-digit strike*1000).
    Parses from the right so variable-length roots are handled correctly."""
    try:
        strike = int(symbol[-8:]) / 1000.0
        otype = "CALL" if symbol[-9] == "C" else "PUT"
        exp_str = symbol[-15:-9]  # YYMMDD
        exp_date = datetime.strptime(exp_str, "%y%m%d").date()
        root = symbol[:-15]
        return root, exp_date, otype, strike
    except Exception:
        return None, None, None, None


# ---------------------------------------------------------------------------
# Alpaca live option chain
# ---------------------------------------------------------------------------
def _alpaca_underlying_last(ticker: str) -> Optional[float]:
    """Get the live underlying last price from Alpaca to anchor a strike band."""
    key, secret = _alpaca_creds()
    if not key or not secret:
        return None
    try:
        url = f"{ALPACA_OPTIONS_DATA_HOST}/v2/stocks/quotes/latest?symbols={ticker}"
        r = requests.get(
            url,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        q = r.json().get("quotes", {}).get(ticker)
        if not q:
            return None
        bp = _safe_float(q.get("bp"))
        ap = _safe_float(q.get("ap"))
        if bp and ap:
            return round((bp + ap) / 2, 2)
        return bp or ap
    except Exception as e:
        logger.warning(f"[{ticker}] Alpaca underlying quote failed: {e}")
        return None


def _fetch_alpaca_chain(ticker: str, intent: dict) -> Optional[str]:
    """Fetch live options chain from Alpaca's snapshot endpoint. Returns a
    compact, high-density markdown table string (Calls + Puts unified, or single-side),
    filtered to near-the-money liquid strikes and standard monthly expiries."""
    key, secret = _alpaca_creds()
    if not key or not secret:
        logger.warning("Alpaca options credentials missing.")
        return None
    try:
        direction = str(intent.get("direction", "BOTH")).upper()
        strike_low = float(intent.get("strike_low", 0))
        strike_high = float(intent.get("strike_high", 1e9))
        min_dte = int(intent.get("min_dte", 14))
        max_dte = int(intent.get("max_dte", 120))
        explicit_exp = intent.get("expiration")

        today = date.today()
        params = {"limit": 1000}
        if direction in ("CALL", "PUT"):
            params["type"] = "call" if direction == "CALL" else "put"
        if strike_low and strike_low > 0:
            params["strike_price_gte"] = strike_low
        if strike_high and strike_high < 1e9:
            params["strike_price_lte"] = strike_high
        if min_dte and min_dte > 0:
            params["expiration_date_gte"] = (today + timedelta(days=min_dte)).isoformat()
        if max_dte:
            params["expiration_date_lte"] = (today + timedelta(days=max_dte)).isoformat()

        url = f"{ALPACA_OPTIONS_DATA_HOST}/v1beta1/options/snapshots/{ticker}?{urlencode(params)}"
        r = requests.get(
            url,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(
                f"[{ticker}] Alpaca options snapshot HTTP {r.status_code}: {r.text[:200]}"
            )
            return None

        snapshots = r.json().get("snapshots", {})
        if not snapshots:
            logger.warning(f"[{ticker}] Alpaca returned no option snapshots for the filters.")
            return None

        grid = {}
        all_expiries = set()
        for symbol, snap in snapshots.items():
            root, exp_date, otype, strike = _parse_occ_symbol(symbol)
            if not exp_date or not otype:
                continue
            exp_str = exp_date.isoformat()
            all_expiries.add(exp_str)
            dte = (exp_date - today).days
            q = snap.get("latestQuote", {}) or {}
            bp = _safe_float(q.get("bp"))
            ap = _safe_float(q.get("ap"))
            if bp is None and ap is None:
                continue
            mid = round((bp + ap) / 2, 2) if (bp is not None and ap is not None) else (bp or ap)
            vol = _safe_int((snap.get("dailyBar", {}) or {}).get("v")) or 0
            oi = _safe_int(snap.get("openInterest")) or 0
            g = snap.get("greeks", {}) or {}
            delta = _safe_float(g.get("delta"))

            k = (exp_str, strike)
            if k not in grid:
                grid[k] = {"exp": exp_str, "dte": dte, "strike": strike, "calls": None, "puts": None}
            data_dict = {
                "bid": round(bp, 2) if bp is not None else None,
                "ask": round(ap, 2) if ap is not None else None,
                "mid": mid,
                "delta": round(delta, 3) if delta is not None else None,
                "vol": vol,
                "oi": oi,
            }
            if otype == "CALL":
                grid[k]["calls"] = data_dict
            else:
                grid[k]["puts"] = data_dict

        if not grid:
            logger.warning(f"[{ticker}] Alpaca chain came back empty after parsing.")
            return None

        # Filter expiries: pick the front 1-2 standard expiries unless explicit_exp is given
        sorted_exp = sorted(list(all_expiries))
        if explicit_exp and explicit_exp in all_expiries:
            selected_exp = [explicit_exp]
        else:
            selected_exp = sorted_exp[:2]

        spot = _alpaca_underlying_last(ticker)

        # Build rows for selected expiries, prioritizing strikes nearest spot
        final_rows = []
        for exp in selected_exp:
            exp_rows = [v for v in grid.values() if v["exp"] == exp]
            if spot:
                exp_rows.sort(key=lambda r: abs(r["strike"] - spot))
            else:
                exp_rows.sort(key=lambda r: r["strike"])
            # Cap to top 12 most liquid/relevant strikes per expiry
            exp_rows = exp_rows[:12]
            exp_rows.sort(key=lambda r: r["strike"])
            final_rows.extend(exp_rows)

        if not final_rows:
            return None

        if direction == "BOTH":
            header = "| Expiry | DTE | Strike | Call Bid/Ask | Call Mid | Call Delta | Put Bid/Ask | Put Mid | Put Delta | Vol (C/P) |"
            sep    = "|--------|-----|--------|--------------|----------|------------|-------------|---------|-----------|-----------|"
            lines = [header, sep]
            for r in final_rows:
                c = r["calls"] or {}
                p = r["puts"] or {}
                c_ba = f"{c.get('bid', '-')}/{c.get('ask', '-')}" if c else "-"
                p_ba = f"{p.get('bid', '-')}/{p.get('ask', '-')}" if p else "-"
                c_mid = f"{c.get('mid', '-')}" if c else "-"
                p_mid = f"{p.get('mid', '-')}" if p else "-"
                c_d = f"{c.get('delta', '-')}" if c else "-"
                p_d = f"{p.get('delta', '-')}" if p else "-"
                vol_c = c.get("vol", 0)
                vol_p = p.get("vol", 0)
                lines.append(
                    f"| {r['exp']} | {r['dte']} | {r['strike']} | {c_ba} | {c_mid} | {c_d} | {p_ba} | {p_mid} | {p_d} | {vol_c}/{vol_p} |"
                )
        else:
            # Single-side table
            is_call = (direction == "CALL")
            header = f"| Expiry | DTE | Strike | {'Call' if is_call else 'Put'} Bid/Ask | Mid | Delta | Vol | OI |"
            sep    = "|--------|-----|--------|--------------|-----|-------|-----|----|"
            lines = [header, sep]
            for r in final_rows:
                side = r["calls"] if is_call else r["puts"]
                if not side:
                    continue
                ba = f"{side.get('bid', '-')}/{side.get('ask', '-')}"
                mid = side.get("mid", "-")
                d = side.get("delta", "-")
                vol = side.get("vol", 0)
                oi = side.get("oi", 0)
                lines.append(f"| {r['exp']} | {r['dte']} | {r['strike']} | {ba} | {mid} | {d} | {vol} | {oi} |")

        logger.info(f"[{ticker}] Alpaca compact options chain table: {len(final_rows)} rows ({direction})")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[{ticker}] _fetch_alpaca_chain failed: {e}")
        return None


# ---------------------------------------------------------------------------
# yfinance fallback (kept for resilience)
# ---------------------------------------------------------------------------
def _fetch_yfinance_chain(ticker: str, intent: dict) -> Optional[str]:
    """Fetch live options chain for strikes/expiries via yfinance. Returns a
    compact, high-density markdown table string, or None on failure."""
    try:
        import yfinance as yf

        direction = str(intent.get("direction", "BOTH")).upper()
        strike_low = float(intent.get("strike_low", 0))
        strike_high = float(intent.get("strike_high", 1e9))
        min_dte = int(intent.get("min_dte", 14))
        max_dte = int(intent.get("max_dte", 120))

        yf_ticker = yf.Ticker(ticker)
        all_expiries = yf_ticker.options  # tuple of YYYY-MM-DD strings

        if not all_expiries:
            logger.warning(f"[{ticker}] yfinance returned no options expiries.")
            return None

        today = datetime.now().date()

        relevant_expiries = []
        for exp_str in all_expiries:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if min_dte <= dte <= max_dte:
                    relevant_expiries.append((dte, exp_str))
            except Exception:
                continue

        relevant_expiries.sort()
        selected_expiries = relevant_expiries[:2]

        if not selected_expiries:
            logger.warning(f"[{ticker}] No expiries found in {min_dte}-{max_dte} DTE window.")
            return None

        grid = {}
        for dte, exp_str in selected_expiries:
            try:
                chain = yf_ticker.option_chain(exp_str)
                for otype, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
                    if direction in ("CALL", "PUT") and otype != direction:
                        continue
                    df = df[(df["strike"] >= strike_low) & (df["strike"] <= strike_high)].copy()
                    for _, row in df.iterrows():
                        k = (exp_str, row["strike"])
                        if k not in grid:
                            grid[k] = {"exp": exp_str, "dte": dte, "strike": row["strike"], "calls": None, "puts": None}
                        mid = round((row.get("bid", 0) + row.get("ask", 0)) / 2, 2)
                        side_dict = {
                            "bid": round(row.get("bid", 0), 2),
                            "ask": round(row.get("ask", 0), 2),
                            "mid": mid,
                            "vol": _safe_int(row.get("volume")) or 0,
                            "oi": _safe_int(row.get("openInterest")) or 0,
                            "iv": round(row.get("impliedVolatility", 0) * 100, 1),
                        }
                        if otype == "CALL":
                            grid[k]["calls"] = side_dict
                        else:
                            grid[k]["puts"] = side_dict
            except Exception as e:
                logger.warning(f"[{ticker}] Failed to fetch chain for expiry {exp_str}: {e}")

        if not grid:
            logger.warning(f"[{ticker}] Options chain came back empty after filtering.")
            return None

        # Sort and select top strikes
        final_rows = []
        for _, exp_str in selected_expiries:
            exp_rows = [v for v in grid.values() if v["exp"] == exp_str]
            exp_rows = exp_rows[:12]
            exp_rows.sort(key=lambda r: r["strike"])
            final_rows.extend(exp_rows)

        if not final_rows:
            return None

        if direction == "BOTH":
            header = "| Expiry | DTE | Strike | Call Bid/Ask | Call Mid | Put Bid/Ask | Put Mid | Vol (C/P) |"
            sep    = "|--------|-----|--------|--------------|----------|-------------|---------|-----------|"
            lines = [header, sep]
            for r in final_rows:
                c = r["calls"] or {}
                p = r["puts"] or {}
                c_ba = f"{c.get('bid', '-')}/{c.get('ask', '-')}" if c else "-"
                p_ba = f"{p.get('bid', '-')}/{p.get('ask', '-')}" if p else "-"
                c_mid = f"{c.get('mid', '-')}" if c else "-"
                p_mid = f"{p.get('mid', '-')}" if p else "-"
                vol_c = c.get("vol", 0)
                vol_p = p.get("vol", 0)
                lines.append(f"| {r['exp']} | {r['dte']} | {r['strike']} | {c_ba} | {c_mid} | {p_ba} | {p_mid} | {vol_c}/{vol_p} |")
        else:
            is_call = (direction == "CALL")
            header = f"| Expiry | DTE | Strike | {'Call' if is_call else 'Put'} Bid/Ask | Mid | Volume | OI | IV% |"
            sep    = "|--------|-----|--------|--------------|-----|--------|----|-----|"
            lines = [header, sep]
            for r in final_rows:
                side = r["calls"] if is_call else r["puts"]
                if not side:
                    continue
                ba = f"{side.get('bid', '-')}/{side.get('ask', '-')}"
                mid = side.get("mid", "-")
                lines.append(f"| {r['exp']} | {r['dte']} | {r['strike']} | {ba} | {mid} | {side.get('vol', 0)} | {side.get('oi', 0)} | {side.get('iv', 0)}% |")

        logger.info(f"[{ticker}] yfinance compact options chain table: {len(final_rows)} rows")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[{ticker}] _fetch_yfinance_chain failed: {e}")
        return None


def fetch_targeted_chain(ticker: str, intent: dict) -> Optional[str]:
    """Fetch live options chain. Primary source: Alpaca; falls back to yfinance."""
    result = _fetch_alpaca_chain(ticker, intent)
    if result:
        return result
    logger.info(f"[{ticker}] Falling back to yfinance for options chain.")
    return _fetch_yfinance_chain(ticker, intent)


# ---------------------------------------------------------------------------
# Real-time quote (for LLM tool use) — Alpaca first, yfinance fallback
# ---------------------------------------------------------------------------
def _alpaca_52w(ticker: str) -> Optional[tuple]:
    """Return (52w_low, 52w_high) from ~1y of daily bars, or None on failure."""
    key, secret = _alpaca_creds()
    if not key or not secret:
        return None
    try:
        start = (date.today() - timedelta(days=400)).isoformat()
        url = (
            f"{ALPACA_OPTIONS_DATA_HOST}/v2/stocks/bars"
            f"?symbols={ticker}&timeframe=1Day&limit=300&start={start}"
        )
        r = requests.get(
            url,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        bars = r.json().get("bars", {}).get(ticker, [])
        if not bars:
            return None
        lows = [b["l"] for b in bars if b.get("l") is not None]
        highs = [b["h"] for b in bars if b.get("h") is not None]
        if not lows or not highs:
            return None
        return (round(min(lows), 2), round(max(highs), 2))
    except Exception as e:
        logger.warning(f"[{ticker}] Alpaca 52w bars failed: {e}")
        return None


def _alpaca_quote(ticker: str) -> Optional[str]:
    """Real-time quote from Alpaca's stock snapshot endpoint. Returns a formatted
    block, or None on failure."""
    key, secret = _alpaca_creds()
    if not key or not secret:
        logger.warning("Alpaca options credentials missing.")
        return None
    try:
        url = f"{ALPACA_OPTIONS_DATA_HOST}/v2/stocks/{ticker}/snapshot"
        r = requests.get(
            url,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=20,
        )
        if r.status_code != 200:
            logger.warning(f"[{ticker}] Alpaca snapshot HTTP {r.status_code}: {r.text[:200]}")
            return None
        j = r.json()
        quote = j.get("latestQuote", {}) or {}
        trade = j.get("latestTrade", {}) or {}
        daily = j.get("dailyBar", {}) or {}
        prev = j.get("prevDailyBar", {}) or {}

        bp = _safe_float(quote.get("bp"))
        ap = _safe_float(quote.get("ap"))
        last = _safe_float(trade.get("p"))
        if last is None and bp and ap:
            last = round((bp + ap) / 2, 2)
        day_low = _safe_float(daily.get("l"))
        day_high = _safe_float(daily.get("h"))
        prev_close = _safe_float(prev.get("c"))
        vol = _safe_int(daily.get("v"))

        yr = _alpaca_52w(ticker)

        import pytz
        from datetime import datetime
        ny_time = datetime.now(pytz.timezone('America/New_York'))
        current_time = ny_time.time()
        
        if ny_time.weekday() >= 5:
            session = "closed"
        elif current_time < datetime.strptime("04:00", "%H:%M").time() or current_time >= datetime.strptime("20:00", "%H:%M").time():
            session = "closed"
        elif current_time < datetime.strptime("09:30", "%H:%M").time():
            session = "pre-market"
        elif current_time < datetime.strptime("16:00", "%H:%M").time():
            session = "intraday"
        else:
            session = "after-hours"

        lines = [
            f"REAL-TIME QUOTE for {ticker} (source: Alpaca, {session}):",
            f"- Last: {last}",
            f"- Bid/Ask: {bp} / {ap}",
            f"- Day Range: {day_low} - {day_high}",
            f"- Previous Close: {prev_close}",
            f"- 52-Week Range: {yr[0]} - {yr[1]}" if yr else "- 52-Week Range: n/a",
            f"- Volume: {vol}",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[{ticker}] _alpaca_quote failed: {e}")
        return None


def _tastytrade_quote(ticker: str) -> Optional[str]:
    """Real-time quote via Tastytrade DXLink stream (fallback)."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        tt_client = TastytradeClient()
        if os.path.exists(tt_client.token_path) and ticker not in ["SPX", "VIX", "COMP"]:
            tt_quote = tt_client.get_realtime_quote(ticker)
            if tt_quote and tt_quote.get("price"):
                p = tt_quote["price"]
                bp = tt_quote.get("bid")
                ap = tt_quote.get("ask")
                lines = [
                    f"REAL-TIME QUOTE for {ticker} (source: Tastytrade DXLink):",
                    f"- Last: {p:.2f}",
                    f"- Bid/Ask: {bp} / {ap}",
                    f"- Day Range: {tt_quote.get('day_low', 'n/a')} - {tt_quote.get('day_high', 'n/a')}",
                    f"- Volume: {tt_quote.get('volume', 'n/a')}",
                ]
                return "\n".join(lines)
    except Exception as e:
        logger.debug(f"[{ticker}] _tastytrade_quote skipped or failed: {e}")
    return None


def _yfinance_quote(ticker: str) -> Optional[str]:
    """Real-time quote via yfinance (fallback)."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        info = t.fast_info
        last = info.get("lastPrice")
        day_high = info.get("dayHigh")
        day_low = info.get("dayLow")
        prev_close = info.get("previousClose")
        yr_high = info.get("yearHigh")
        yr_low = info.get("yearLow")
        try:
            vol = int(t.history(period="1d")["Volume"].iloc[-1])
        except Exception:
            vol = None

        lines = [
            f"REAL-TIME QUOTE for {ticker} (source: yfinance):",
            f"- Last: {last}",
            f"- Bid/Ask: {info.get('bid')} / {info.get('ask')}",
            f"- Day Range: {day_low} - {day_high}",
            f"- Previous Close: {prev_close}",
            f"- 52-Week Range: {yr_low} - {yr_high}",
            f"- Volume: {vol}",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[{ticker}] _yfinance_quote failed: {e}")
        return None


def get_realtime_quote(ticker: str) -> Optional[str]:
    """Return a concise REAL-TIME quote + range block.
    Uses QuoteRouter for high-speed quote generation, falling back to legacy providers.
    Used by LLM tools and deep research pipeline for live pricing."""
    ticker = ticker or _ACTIVE_TICKER
    if not ticker:
        logger.warning("get_realtime_quote called without a ticker and no active ticker set.")
        return None

    # 0. High-speed QuoteRouter fast-path (~200ms, multi-provider)
    try:
        from src.clients.quote_router import quote_router

        router_text = quote_router.format_quote_text(ticker)
        if router_text:
            return router_text
    except Exception as e_qr:
        logger.debug(f"[{ticker}] QuoteRouter format_quote_text failed: {e_qr}")

    # 1. Try Alpaca Realtime Quote
    result = _alpaca_quote(ticker)
    if result:
        return result
        
    # 2. Try Tastytrade Realtime Quote
    tt_result = _tastytrade_quote(ticker)
    if tt_result:
        return tt_result

    # 3. Fallback to yfinance
    logger.info(f"[{ticker}] Falling back to yfinance for realtime quote.")
    return _yfinance_quote(ticker)


def fetch_options_chain_tool(
    ticker: str,
    direction: str = "BOTH",
    strike_low: float = None,
    strike_high: float = None,
    min_dte: int = 14,
    max_dte: int = 120,
    expiration: Optional[str] = None,
    **kwargs,
) -> Optional[str]:
    """LLM-facing wrapper around fetch_targeted_chain with in-memory TTL caching (120s).
    Derives a tight near-the-money strike range (±12%) from the live underlying spot
    when the model does not supply one. Default direction is 'BOTH' for unified Call+Put tables."""
    import json
    ticker = (ticker or _ACTIVE_TICKER or "").upper()
    if not ticker:
        logger.warning("fetch_options_chain_tool called without a ticker and no active ticker set.")
        return None

    direction = str(direction or "BOTH").upper()
    cache_k = f"{ticker}_{direction}_{min_dte}_{max_dte}_{strike_low}_{strike_high}_{expiration}"
    if cache_k in _CHAIN_MEM_CACHE:
        cached_ts, cached_table = _CHAIN_MEM_CACHE[cache_k]
        if time.time() - cached_ts < 120:
            logger.info(f"[{ticker}] Options chain retrieved from fast memory cache (0ms)")
            return cached_table

    if expiration:
        try:
            from datetime import datetime, date as dt_date
            exp_clean = str(expiration).strip()[:10]
            exp_d = datetime.strptime(exp_clean, "%Y-%m-%d").date()
            dte = (exp_d - dt_date.today()).days
            if dte >= 0:
                min_dte = max(1, dte - 5)
                max_dte = dte + 5
        except Exception as e_exp:
            logger.debug(f"Could not parse expiration '{expiration}': {e_exp}")

    if strike_low is None or strike_high is None:
        spot = _alpaca_underlying_last(ticker)
        if not spot:
            try:
                import yfinance as yf
                t = yf.Ticker(ticker)
                spot = t.fast_info.get("lastPrice")
            except Exception:
                spot = None
        if spot:
            if strike_low is None:
                strike_low = round(spot * 0.88, 2)
            if strike_high is None:
                strike_high = round(spot * 1.12, 2)

    intent = {
        "direction": direction,
        "strike_low": strike_low if strike_low is not None else 0.0,
        "strike_high": strike_high if strike_high is not None else 1e9,
        "min_dte": min_dte,
        "max_dte": max_dte,
        "expiration": expiration,
        "key_catalyst_date": None,
        "options_rationale": "Tool-invoked live chain request from deep research model.",
    }
    res = fetch_targeted_chain(ticker, intent)
    if res:
        _CHAIN_MEM_CACHE[cache_k] = (time.time(), res)
        try:
            from src import config
            today_str = date.today().strftime("%Y-%m-%d")
            out_dir = config.BASE_DIR / "data" / "raw" / today_str / ticker.upper()
            out_dir.mkdir(parents=True, exist_ok=True)
            snap_file = out_dir / f"{ticker.upper()}_options_chain_{direction}_{min_dte}_{max_dte}.json"
            snap_file.write_text(json.dumps({
                "ticker": ticker.upper(),
                "direction": direction,
                "min_dte": min_dte,
                "max_dte": max_dte,
                "strike_low": strike_low,
                "strike_high": strike_high,
                "table": res,
            }, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to write options chain snapshot: {e}")
    return res


def format_gex_block(ticker: str) -> str:
    """Compute and format a clean Options Gamma Exposure (GEX) & Open Interest
    wall summary for the deep-research prompt. Identifies Put Walls (dealer support),
    Call Walls (dealer resistance), and Put/Call positioning."""
    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return ""

        calls_list, puts_list = [], []
        for exp in expiries[:4]:  # front 4 expirations
            try:
                opt = t.option_chain(exp)
                if not opt.calls.empty:
                    calls_list.append(opt.calls)
                if not opt.puts.empty:
                    puts_list.append(opt.puts)
            except Exception:
                continue

        if not calls_list or not puts_list:
            return ""

        c_df = pd.concat(calls_list)
        p_df = pd.concat(puts_list)

        c_walls = c_df.groupby("strike")["openInterest"].sum().sort_values(ascending=False).head(3)
        p_walls = p_df.groupby("strike")["openInterest"].sum().sort_values(ascending=False).head(3)

        total_call_oi = c_df["openInterest"].sum()
        total_put_oi = p_df["openInterest"].sum()
        pcr = round(total_put_oi / max(total_call_oi, 1), 2)

        lines = [
            f"--- OPTIONS POSITIONING & GAMMA WALLS (GEX) FOR {ticker} ---",
            f"• Put/Call Open Interest Ratio: {pcr}",
            "• Major Put Walls (Dealer Support / Floor Pins):",
        ]
        for s, oi in p_walls.items():
            lines.append(f"  - Strike ${s:.2f}: {int(oi):,} open contracts")

        lines.append("• Major Call Walls (Dealer Resistance / Ceiling Pins):")
        for s, oi in c_walls.items():
            lines.append(f"  - Strike ${s:.2f}: {int(oi):,} open contracts")

        top_put = p_walls.index[0] if len(p_walls) > 0 else "N/A"
        top_call = c_walls.index[0] if len(c_walls) > 0 else "N/A"
        lines.append(
            f"• Dealer Expected Pinning Corridor: ${top_put} (Put Floor) to ${top_call} (Call Ceiling)"
        )
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[{ticker}] format_gex_block failed: {e}")
        return ""

