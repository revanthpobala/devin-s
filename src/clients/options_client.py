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
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

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
    markdown table string, or None on failure."""
    key, secret = _alpaca_creds()
    if not key or not secret:
        logger.warning("Alpaca options credentials missing.")
        return None
    try:
        direction = intent.get("direction", "CALL").upper()
        strike_low = float(intent.get("strike_low", 0))
        strike_high = float(intent.get("strike_high", 1e9))
        min_dte = int(intent.get("min_dte", 30))
        max_dte = int(intent.get("max_dte", 120))

        today = date.today()
        params = {
            "limit": 1000,
            "type": "call" if direction == "CALL" else "put",
        }
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
            timeout=30,
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

        rows = []
        for symbol, snap in snapshots.items():
            root, exp_date, otype, strike = _parse_occ_symbol(symbol)
            if not exp_date or not otype:
                continue
            dte = (exp_date - today).days
            q = snap.get("latestQuote", {}) or {}
            bp = _safe_float(q.get("bp"))
            ap = _safe_float(q.get("ap"))
            if bp is None and ap is None:
                continue
            if bp is not None and ap is not None:
                mid = round((bp + ap) / 2, 2)
            else:
                mid = bp or ap
            vol = _safe_int((snap.get("dailyBar", {}) or {}).get("v"))
            g = snap.get("greeks", {}) or {}
            delta = _safe_float(g.get("delta"))
            gamma = _safe_float(g.get("gamma"))
            theta = _safe_float(g.get("theta"))
            vega = _safe_float(g.get("vega"))
            rows.append(
                {
                    "Expiry": exp_date.isoformat(),
                    "DTE": dte,
                    "Strike": strike,
                    "Type": otype,
                    "Bid": round(bp, 2) if bp is not None else None,
                    "Ask": round(ap, 2) if ap is not None else None,
                    "Mid": mid,
                    "Vol": vol,
                    "Delta": round(delta, 3) if delta is not None else None,
                    "Gamma": round(gamma, 4) if gamma is not None else None,
                    "Theta": round(theta, 3) if theta is not None else None,
                    "Vega": round(vega, 3) if vega is not None else None,
                }
            )

        if not rows:
            logger.warning(f"[{ticker}] Alpaca chain came back empty after parsing.")
            return None

        rows.sort(key=lambda r: (r["DTE"], r["Strike"]))

        header = "| Expiry | DTE | Strike | Type | Bid | Ask | Mid | Vol | Delta | Gamma | Theta | Vega |"
        sep = "|--------|-----|--------|------|-----|-----|-----|-----|-------|-------|-------|------|"
        lines = [header, sep]
        for r in rows:

            def cell(v):
                return "" if v is None else str(v)

            lines.append(
                f"| {r['Expiry']} | {r['DTE']} | {r['Strike']} | {r['Type']} "
                f"| {cell(r['Bid'])} | {cell(r['Ask'])} | {r['Mid']} | {r['Vol']} "
                f"| {cell(r['Delta'])} | {cell(r['Gamma'])} | {cell(r['Theta'])} | {cell(r['Vega'])} |"
            )

        logger.info(f"[{ticker}] Alpaca options chain table: {len(rows)} rows")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[{ticker}] _fetch_alpaca_chain failed: {e}")
        return None


# ---------------------------------------------------------------------------
# yfinance fallback (kept for resilience)
# ---------------------------------------------------------------------------
def _fetch_yfinance_chain(ticker: str, intent: dict) -> Optional[str]:
    """Fetch live options chain for strikes/expiries via yfinance. Returns a
    formatted markdown table string, or None on failure."""
    try:
        import yfinance as yf

        direction = intent.get("direction", "CALL").upper()
        strike_low = float(intent.get("strike_low", 0))
        strike_high = float(intent.get("strike_high", 1e9))
        min_dte = int(intent.get("min_dte", 30))
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
        selected_expiries = relevant_expiries[:3]

        if not selected_expiries:
            logger.warning(f"[{ticker}] No expiries found in {min_dte}-{max_dte} DTE window.")
            return None

        rows = []
        for dte, exp_str in selected_expiries:
            try:
                chain = yf_ticker.option_chain(exp_str)
                df = chain.calls if direction == "CALL" else chain.puts
                df = df[(df["strike"] >= strike_low) & (df["strike"] <= strike_high)].copy()
                df = df[(df["openInterest"] > 0) | (df["volume"] > 0)]
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    mid = round((row.get("bid", 0) + row.get("ask", 0)) / 2, 2)
                    iv_pct = round(row.get("impliedVolatility", 0) * 100, 1)
                    rows.append(
                        {
                            "Expiry": exp_str,
                            "DTE": dte,
                            "Strike": row["strike"],
                            "Type": direction,
                            "Bid": round(row.get("bid", 0), 2),
                            "Ask": round(row.get("ask", 0), 2),
                            "Mid": mid,
                            "Volume": _safe_int(row.get("volume")),
                            "OI": _safe_int(row.get("openInterest")),
                            "IV%": iv_pct,
                        }
                    )
            except Exception as e:
                logger.warning(f"[{ticker}] Failed to fetch chain for expiry {exp_str}: {e}")

        if not rows:
            logger.warning(f"[{ticker}] Options chain came back empty after filtering.")
            return None

        header = "| Expiry | DTE | Strike | Type | Bid | Ask | Mid | Volume | OI | IV% |"
        sep = "|--------|-----|--------|------|-----|-----|-----|--------|----|-----|"
        lines = [header, sep]
        for r in rows:
            lines.append(
                f"| {r['Expiry']} | {r['DTE']} | {r['Strike']} | {r['Type']} "
                f"| {r['Bid']} | {r['Ask']} | {r['Mid']} | {r['Volume']} | {r['OI']} | {r['IV%']}% |"
            )

        logger.info(f"[{ticker}] yfinance options chain table: {len(rows)} rows")
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

        lines = [
            f"REAL-TIME QUOTE for {ticker} (source: Alpaca):",
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
    """Return a concise REAL-TIME quote + range block. Primary source: Alpaca;
    falls back to yfinance. Used by the get_realtime_quote LLM tool so Minimax
    can pull live prices. Falls back to the pipeline's active ticker when the
    model omits the arg."""
    ticker = ticker or _ACTIVE_TICKER
    if not ticker:
        logger.warning("get_realtime_quote called without a ticker and no active ticker set.")
        return None
    result = _alpaca_quote(ticker)
    if result:
        return result
    logger.info(f"[{ticker}] Falling back to yfinance for realtime quote.")
    return _yfinance_quote(ticker)


def fetch_options_chain_tool(
    ticker: str,
    direction: str = "CALL",
    strike_low: float = None,
    strike_high: float = None,
    min_dte: int = 30,
    max_dte: int = 120,
) -> Optional[str]:
    """LLM-facing wrapper around fetch_targeted_chain. Derives a strike range from
    the live underlying spot (via Alpaca) when the model does not supply one.
    Falls back to the pipeline's active ticker when the model omits the arg."""
    ticker = ticker or _ACTIVE_TICKER
    if not ticker:
        logger.warning("fetch_options_chain_tool called without a ticker and no active ticker set.")
        return None
    if strike_low is None or strike_high is None:
        spot = _alpaca_underlying_last(ticker)
        if spot:
            if strike_low is None:
                strike_low = round(spot * 0.90, 2)
            if strike_high is None:
                strike_high = round(spot * 1.15, 2)
    intent = {
        "direction": direction,
        "strike_low": strike_low if strike_low is not None else 0.0,
        "strike_high": strike_high if strike_high is not None else 1e9,
        "min_dte": min_dte,
        "max_dte": max_dte,
        "key_catalyst_date": None,
        "options_rationale": "Tool-invoked live chain request from deep research model.",
    }
    return fetch_targeted_chain(ticker, intent)
