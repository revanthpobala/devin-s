"""
src/data/darkpool.py — On-demand Dark Pool & Off-Exchange Order Flow Engine.

Fetches off-exchange prints (FINRA TRF / ADF tape, exchange code 'D') via Alpaca SIP feed,
aggregates volume by price into a level ladder, lists large prints, and infers net buy/sell lean.
DESCRIPTIVE ONLY: direction is inferred and no level or flow measure here has a measured edge.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("darkpool")

# In-memory thread-safe cache: { "TICKER": (timestamp, data_dict) }
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 60.0  # 1 minute fresh cache

_ET = ZoneInfo("America/New_York")
_LEDGER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "darkpool_ledger.jsonl")
_LEDGER_SEEN: set = set()

# Conditions whose price carries no information about where the market traded (benchmark / derivative /
# contingent / late / bunched / cash / next-day / price-variation / official-close prints).
NON_PRICE_CONDITIONS = {"4", "W", "P", "Z", "7", "V", "B", "G", "C", "N", "R", "H", "M", "9"}
EXTENDED_CONDITIONS = {"T", "U"}
ODD_LOT_CONDITION = "I"
MEASURED_VERDICT = (
    "Descriptive only: inferred buy%, block direction and volume clusters showed no era-stable, ticker-half-stable edge "
    "(2026-10 backtests: 3,190 + 876 intraday events; 35k R:R events x FINRA off-exchange share / short-volume ratio, 2018-2026)."
)
MIN_CLASSIFIED_PCT = 50.0  # sentiment label is withheld below this share of classified ladder volume


def _parse_ts(ts: str) -> Optional[float]:
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", ts or "")
    if not m:
        return None
    base = datetime.fromisoformat(m.group(1) + "+00:00").timestamp()
    return base + float(m.group(2) or 0.0)


def _is_regular_session(epoch: float) -> bool:
    et = datetime.fromtimestamp(epoch, _ET)
    mins = et.hour * 60 + et.minute
    return et.weekday() < 5 and 9 * 60 + 30 <= mins < 16 * 60


def _session_windows(now_et: datetime) -> List[Tuple[str, Optional[str]]]:
    """(start, end) windows to try, newest first. Live session: no end. Closed: prior regular close,
    then up to 3 earlier weekdays (holidays)."""
    mins = now_et.hour * 60 + now_et.minute
    d = now_et.date()
    out: List[Tuple[str, Optional[str]]] = []
    if now_et.weekday() < 5 and 9 * 60 + 30 <= mins < 16 * 60:
        out.append((datetime(d.year, d.month, d.day, 4, 0, tzinfo=_ET).isoformat(), None))
        d = d - timedelta(days=1)
    elif not (now_et.weekday() < 5 and mins >= 16 * 60):
        d = d - timedelta(days=1)
    tries = 0
    while tries < 4:
        if d.weekday() < 5:
            out.append((datetime(d.year, d.month, d.day, 4, 0, tzinfo=_ET).isoformat(),
                        datetime(d.year, d.month, d.day, 16, 0, tzinfo=_ET).isoformat()))
            tries += 1
        d = d - timedelta(days=1)
    return out


def _fetch_quotes(sym: str, base_url: str, headers: Dict[str, str], feed: str, start: str, end: str,
                  max_pages: int = 6) -> Tuple[List[float], List[float], List[float]]:
    """NBBO quotes (ascending) for [start, end]; may be truncated at max_pages."""
    times: List[float] = []
    bids: List[float] = []
    asks: List[float] = []
    token = None
    for _ in range(max_pages):
        params: Dict[str, Any] = {"limit": 10000, "feed": feed, "start": start, "end": end}
        if token:
            params["page_token"] = token
        try:
            r = requests.get(f"{base_url}/v2/stocks/{sym}/quotes", params=params, headers=headers, timeout=12)
            if r.status_code != 200:
                break
            body = r.json()
        except Exception as e:
            logger.debug(f"[{sym}] quote fetch error (non-fatal): {e}")
            break
        for q in body.get("quotes") or []:
            ts = _parse_ts(q.get("t", ""))
            if ts is not None:
                times.append(ts)
                bids.append(float(q.get("bp") or 0.0))
                asks.append(float(q.get("ap") or 0.0))
        token = body.get("next_page_token")
        if not token:
            break
    return times, bids, asks


def _quote_side(price: float, bid: float, ask: float) -> Optional[str]:
    if bid <= 0 or ask <= 0 or ask <= bid:
        return None
    if price >= ask:
        return "BUY"
    if price <= bid:
        return "SELL"
    mid = (bid + ask) / 2.0
    if abs(price - mid) <= 0.1 * (ask - bid):
        return None  # midpoint print: no aggressor can be inferred
    return "BUY" if price > mid else "SELL"


def _record_ledger(res: Dict[str, Any]) -> None:
    """Append one structured snapshot per (ticker, window_end) so shelves / dp_buy_pct can be scored later."""
    if os.getenv("DARKPOOL_NO_LEDGER"):
        return
    key = (res.get("ticker"), res.get("window_end"))
    if key in _LEDGER_SEEN:
        return
    _LEDGER_SEEN.add(key)
    sigs = res.get("signature_prints") or []
    row = {
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "ticker": res.get("ticker"),
        "session_date": res.get("trade_date"),
        "window_start": res.get("window_start"),
        "window_end": res.get("window_end"),
        "window_minutes": res.get("window_minutes"),
        "window_is_full_session": res.get("window_is_full_session"),
        "dp_buy_pct": res.get("dp_buy_pct"),
        "dp_classified_pct": res.get("dp_classified_pct"),
        "direction_method": res.get("direction_method"),
        "dp_total_volume": res.get("dp_total_volume"),
        "dp_share_of_tape_pct": res.get("dp_share_of_tape_pct"),
        "shelves": [[s["price"], s["volume"]] for s in res.get("major_shelves") or []],
        "n_blocks": len(sigs),
        "n_blocks_buy": sum(1 for s in sigs if s.get("direction") == "BUY"),
        "n_blocks_sell": sum(1 for s in sigs if s.get("direction") == "SELL"),
    }
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        logger.debug(f"darkpool ledger write failed: {e}")


def _get_alpaca_creds() -> Tuple[Optional[str], Optional[str], str]:
    """Retrieve Alpaca API credentials safely without logging or exposing values."""
    key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY")
    url = (os.getenv("ALPACA_DATA_URL") or "https://data.alpaca.markets").strip().rstrip("/")
    clean_url = url.replace("paper-api.", "data.").replace("//api.", "//data.")
    return key, secret, clean_url


def fetch_darkpool_data(
    ticker: str,
    limit: int = 10000,
    force_refresh: bool = False,
    min_block_shares: int = 1000,
    min_block_notional: float = 250000.0,
) -> Dict[str, Any]:
    """Off-exchange (FINRA TRF, exchange code 'D') tape from Alpaca SIP. DESCRIPTIVE ONLY.

    Window: the latest ``limit`` (<=20,000) trades of the last regular session (09:30-16:00 ET); after hours the
    window ends at that session's 16:00 close. It is a tail of the session, NOT the whole session
    (see ``window_start`` / ``window_end`` / ``window_minutes`` / ``window_is_full_session``).
    Extended-hours prints are dropped. Odd lots (<100 sh or cond 'I') and price-uninformative conditions
    (NON_PRICE_CONDITIONS) stay in total volume but are excluded from the ladder, shelves, blocks and direction.
    Direction: NBBO quote rule when quotes cover the print (midpoint prints unclassified), else tick rule;
    first print / unresolved ties are unclassified. Block = >= min_block_shares AND >= min_block_notional.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"status": "error", "message": "Ticker symbol is required."}

    now = time.time()
    with _CACHE_LOCK:
        if not force_refresh and sym in _CACHE:
            cached_time, cached_val = _CACHE[sym]
            if now - cached_time < CACHE_TTL_SECONDS:
                return cached_val

    key, secret, base_url = _get_alpaca_creds()
    if not key or not secret:
        return {
            "status": "error",
            "message": "Alpaca API credentials (ALPACA_API_KEY / ALPACA_SECRET_KEY) are not configured.",
            "ticker": sym,
        }

    feed = os.getenv("ALPACA_BARS_FEED", "sip")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = f"{base_url}/v2/stocks/{sym}/trades"

    trades: List[Dict[str, Any]] = []
    win_start = win_end = None
    try:
        for w_start, w_end in _session_windows(datetime.now(_ET)):
            params: Dict[str, Any] = {"limit": min(limit, 10000), "feed": feed, "sort": "desc", "start": w_start}
            if w_end:
                params["end"] = w_end
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == 429:
                logger.warning(f"[{sym}] Alpaca rate limit encountered (429).")
                return {"status": "error", "message": "Alpaca API rate limit reached. Please wait a moment before refreshing.", "ticker": sym}
            if resp.status_code != 200:
                logger.warning(f"[{sym}] Alpaca trades request returned status {resp.status_code}: {resp.text[:120]}")
                continue
            body = resp.json()
            got = body.get("trades") or []
            next_token = body.get("next_page_token")
            if next_token and limit > 10000 and len(got) >= 10000:
                try:
                    p2 = dict(params)
                    p2["page_token"] = next_token
                    p2["limit"] = min(limit - len(got), 10000)
                    resp2 = requests.get(url, params=p2, headers=headers, timeout=8)
                    if resp2.status_code == 200:
                        got.extend(resp2.json().get("trades") or [])
                except Exception as p2_err:
                    logger.debug(f"[{sym}] Page 2 trade fetch error (non-fatal): {p2_err}")
            got = [t for t in got if (lambda e: e is not None and _is_regular_session(e))(_parse_ts(t.get("t", "")))
                   and not (set(t.get("c") or []) & EXTENDED_CONDITIONS)]
            if got:
                trades = got
                win_start, win_end = w_start, w_end
                break
    except Exception as e:
        logger.error(f"[{sym}] Exception during Alpaca dark pool fetch: {e}")
        return {"status": "error", "message": f"Network or API error while fetching dark pool trades: {e}", "ticker": sym}

    trades.sort(key=lambda t: _parse_ts(t.get("t", "")) or 0.0)
    trade_date_str = date.today().isoformat()
    first_ep = last_ep = None
    if trades:
        first_ep = _parse_ts(trades[0]["t"])
        last_ep = _parse_ts(trades[-1]["t"])
        trade_date_str = datetime.fromtimestamp(last_ep, _ET).date().isoformat()
    window_minutes = round((last_ep - first_ep) / 60.0, 1) if trades else 0.0
    first_et = datetime.fromtimestamp(first_ep, _ET) if trades else None
    window_is_full_session = bool(trades) and (first_et.hour * 60 + first_et.minute) <= 9 * 60 + 31 and len(trades) < min(limit, 10000)
    window_meta = {
        "window_start": trades[0]["t"] if trades else None,
        "window_end": trades[-1]["t"] if trades else None,
        "window_minutes": window_minutes,
        "window_is_full_session": window_is_full_session,
        "descriptive_only": True,
        "measured_verdict": MEASURED_VERDICT,
    }

    if not trades:
        res_empty = {
            "status": "ok", "ticker": sym, "trade_date": trade_date_str, "total_trades_scanned": 0,
            "dp_trades_count": 0, "dp_total_volume": 0, "dp_total_notional": 0.0, "dp_buy_volume": 0,
            "dp_sell_volume": 0, "dp_buy_pct": 50.0, "dp_classified_pct": 0.0, "sentiment": "NEUTRAL / NO ACTIVITY",
            "ladder": [], "signature_prints": [], "major_shelves": [],
            "message": "No regular-session off-exchange trades found.", **window_meta,
        }
        with _CACHE_LOCK:
            _CACHE[sym] = (now, res_empty)
        return res_empty

    dp_trades = [t for t in trades if t.get("x") == "D"]

    q_times: List[float] = []
    q_bids: List[float] = []
    q_asks: List[float] = []
    if dp_trades:
        q_times, q_bids, q_asks = _fetch_quotes(sym, base_url, headers, feed, trades[0]["t"], trades[-1]["t"])

    ladder_map = defaultdict(lambda: {"prints": 0, "volume": 0, "notional": 0.0, "max_block": 0, "buy_volume": 0, "sell_volume": 0})
    dp_buy_vol = dp_sell_vol = dp_unclassified_vol = 0
    dp_total_vol = dp_ladder_vol = dp_odd_lot_vol = dp_nonprice_vol = 0
    dp_total_notional = 0.0
    quote_classified = tick_classified = 0
    signature_prints: List[Dict[str, Any]] = []
    prev_price: Optional[float] = None
    last_tick_dir: Optional[str] = None

    for t in dp_trades:
        raw_price = float(t.get("p", 0.0))
        price = round(raw_price, 2)
        size = int(t.get("s", 0))
        if price <= 0 or size <= 0:
            continue
        conds = set(t.get("c") or [])
        dp_total_vol += size
        dp_total_notional += price * size

        if ODD_LOT_CONDITION in conds or size < 100:
            dp_odd_lot_vol += size
            continue
        if conds & NON_PRICE_CONDITIONS:
            dp_nonprice_vol += size
            continue

        notional = price * size
        dp_ladder_vol += size
        entry = ladder_map[price]
        entry["prints"] += 1
        entry["volume"] += size
        entry["notional"] += notional
        if size > entry["max_block"]:
            entry["max_block"] = size

        # tick rule state (zero-tick inherits the previous non-zero tick; none before the first tick)
        if prev_price is not None:
            if raw_price > prev_price:
                last_tick_dir = "BUY"
            elif raw_price < prev_price:
                last_tick_dir = "SELL"
        prev_price = raw_price

        direction: Optional[str] = None
        ep = _parse_ts(t.get("t", ""))
        if q_times and ep is not None and q_times[0] <= ep <= q_times[-1]:
            i = bisect.bisect_right(q_times, ep) - 1
            if i >= 0:
                direction = _quote_side(raw_price, q_bids[i], q_asks[i])
                if direction:
                    quote_classified += size
                else:
                    direction = "MID"  # priced inside the spread's midpoint band: stays unclassified
        if direction is None:
            direction = last_tick_dir
            if direction:
                tick_classified += size
        if direction == "BUY":
            dp_buy_vol += size
            entry["buy_volume"] += size
        elif direction == "SELL":
            dp_sell_vol += size
            entry["sell_volume"] += size
        else:
            dp_unclassified_vol += size
            direction = "UNCLASSIFIED"

        if size >= min_block_shares and notional >= min_block_notional:
            signature_prints.append({
                "price": price, "shares": size, "notional": round(notional, 2),
                "timestamp": t.get("t", ""), "direction": direction, "condition": t.get("c", []),
            })

    signature_prints.sort(key=lambda x: x["notional"], reverse=True)
    top_signatures = signature_prints[:15]

    max_level_vol = max((d["volume"] for d in ladder_map.values()), default=1)
    avg_level_vol = (dp_ladder_vol / len(ladder_map)) if ladder_map else 0

    ladder_list = []
    major_shelves = []
    for price in sorted(ladder_map.keys(), reverse=True):
        info = ladder_map[price]
        vol = info["volume"]
        is_major = vol >= (avg_level_vol * 1.8) and vol >= 1000
        ladder_list.append({
            "price": price, "prints": info["prints"], "volume": vol, "notional": round(info["notional"], 2),
            "max_block": info["max_block"], "buy_volume": info["buy_volume"], "sell_volume": info["sell_volume"],
            "pct_of_max": round((vol / max_level_vol) * 100.0, 1) if max_level_vol > 0 else 0.0,
            "is_major_shelf": is_major,
        })
        if is_major:
            major_shelves.append({"price": price, "volume": vol, "notional": round(info["notional"], 2), "prints": info["prints"]})
    major_shelves.sort(key=lambda x: x["volume"], reverse=True)

    total_classified = dp_buy_vol + dp_sell_vol
    dp_buy_pct = round((dp_buy_vol / total_classified) * 100.0, 1) if total_classified > 0 else 50.0
    dp_classified_pct = round((total_classified / dp_ladder_vol) * 100.0, 1) if dp_ladder_vol > 0 else 0.0

    if dp_classified_pct < MIN_CLASSIFIED_PCT:
        sentiment = "INSUFFICIENT CLASSIFIED FLOW"
    elif dp_buy_pct >= 58.0:
        sentiment = "BUY-SIDE LEAN (BULLISH, descriptive only)"
    elif dp_buy_pct <= 42.0:
        sentiment = "SELL-SIDE LEAN (BEARISH, descriptive only)"
    else:
        sentiment = "BALANCED / CONTESTED"

    total_shares_all = sum(t.get("s", 0) for t in trades)
    dp_share_of_tape_pct = round((dp_total_vol / total_shares_all) * 100.0, 1) if total_shares_all > 0 else 0.0
    method = "quote" if quote_classified >= tick_classified and quote_classified > 0 else "tick"

    result = {
        "status": "ok", "ticker": sym, "trade_date": trade_date_str,
        "total_trades_scanned": len(trades), "dp_trades_count": len(dp_trades),
        "dp_total_volume": dp_total_vol, "dp_total_notional": round(dp_total_notional, 2),
        "dp_ladder_volume": dp_ladder_vol, "dp_odd_lot_volume": dp_odd_lot_vol, "dp_nonprice_volume": dp_nonprice_vol,
        "dp_share_of_tape_pct": dp_share_of_tape_pct,
        "dp_buy_volume": dp_buy_vol, "dp_sell_volume": dp_sell_vol, "dp_unclassified_volume": dp_unclassified_vol,
        "dp_buy_pct": dp_buy_pct, "dp_classified_pct": dp_classified_pct, "direction_method": method,
        "sentiment": sentiment, "ladder": ladder_list, "signature_prints": top_signatures,
        "major_shelves": major_shelves[:6], **window_meta,
    }

    # Enrich with Tastytrade Institutional Volatility Metrics (IV Rank, IV Percentile, 30d HV, Beta)
    try:
        from src.clients.tastytrade_client import get_tastytrade_client
        tt = get_tastytrade_client()
        metrics = tt.get_market_metrics([sym])
        if metrics:
            m = metrics[0]
            iv_r = m.get("implied-volatility-index-rank")
            iv_p = m.get("implied-volatility-percentile")
            hv30 = m.get("historical-volatility-30-day")
            beta = m.get("beta")

            iv_val = round(float(iv_r) * 100.0, 1) if iv_r is not None else None
            iv_pct_val = round(float(iv_p) * 100.0, 1) if iv_p is not None else None
            hv_val = round(float(hv30), 1) if hv30 is not None else None
            spread = round(iv_val - hv_val, 1) if (iv_val is not None and hv_val is not None) else None

            result["tastytrade_volatility"] = {
                "iv_rank": iv_val,
                "iv_percentile": iv_pct_val,
                "hv_30d": hv_val,
                "iv_hv_spread": spread,
                "beta": round(float(beta), 2) if beta is not None else None,
            }
    except Exception as tt_err:
        logger.debug(f"[{sym}] Tastytrade metrics fetch error (non-fatal): {tt_err}")

    with _CACHE_LOCK:
        _CACHE[sym] = (now, result)
    _record_ledger(result)

    return result


def _window_line(data: Dict[str, Any]) -> str:
    cov = "full session" if data.get("window_is_full_session") else f"LAST {data.get('window_minutes', 0)} MIN ONLY (tail of session, not session-wide)"
    return (f"- Window: {cov}; {data.get('trade_date')} regular hours; {data.get('total_trades_scanned', 0):,} trades "
            f"({str(data.get('window_start') or '')[11:19]}-{str(data.get('window_end') or '')[11:19]} UTC)")


def format_darkpool_block(ticker: str) -> str:
    """Descriptive off-exchange (FINRA TRF) tape context. Not a forecast: shelves and buy% are unmeasured."""
    data = fetch_darkpool_data(ticker)
    if data.get("status") != "ok":
        return f"--- OFF-EXCHANGE (FINRA TRF) TAPE ---\n(Off-exchange data unavailable for {ticker}: {data.get('message', 'Unknown error')})"

    sym = data["ticker"]
    lines = [
        f"--- OFF-EXCHANGE (FINRA TRF) TAPE ({sym}) - DESCRIPTIVE ONLY ---",
        _window_line(data),
        f"- Off-exchange volume: {data['dp_total_volume']:,} shares (${data['dp_total_notional']:,.0f}, {data['dp_trades_count']:,} prints; "
        f"{data.get('dp_odd_lot_volume', 0):,} odd-lot shares excluded from levels/direction); {data.get('dp_share_of_tape_pct', 0.0)}% of window volume",
        f"- Direction ({data.get('direction_method', 'tick')} rule, midpoint prints unclassified): {data['dp_buy_pct']}% buy over "
        f"{data.get('dp_classified_pct', 0.0)}% of level volume classified -> {data['sentiment']}",
    ]
    tt_vol = data.get("tastytrade_volatility")
    if tt_vol and tt_vol.get("iv_rank") is not None:
        spread = tt_vol.get("iv_hv_spread")
        spread_str = f"{spread:+} pts" if isinstance(spread, (int, float)) else str(spread)
        lines.append(f"- Options volatility (Tastytrade): IV Rank {tt_vol['iv_rank']}% | IV %ile {tt_vol.get('iv_percentile', 'N/A')}% | 30d HV {tt_vol.get('hv_30d', 'N/A')}% (IV-HV {spread_str})")

    lines.append("")
    lines.append("#### Off-exchange volume clusters in window (price levels with most volume; NOT validated support/resistance):")
    shelves = data.get("major_shelves", [])
    if shelves:
        for idx, s in enumerate(shelves[:4], 1):
            lines.append(f"  {idx}. ${s['price']:.2f} -> {s['volume']:,} shares, {s['prints']} prints (${s['notional']:,.0f})")
    else:
        lines.append("  (No dense clusters; volume is dispersed across price levels)")

    sigs = data.get("signature_prints", [])
    if sigs:
        lines.append("\n#### Largest prints (>= 1,000 sh and >= $250k):")
        for s in sigs[:3]:
            ts = s["timestamp"]
            ts_short = ts.split("T")[-1][:8] if "T" in ts else ts
            lines.append(f"  - {s['shares']:,} sh @ ${s['price']:.2f} (${s['notional']:,.0f} | {s['direction']} | {ts_short} UTC)")

    lines.append(f"\nMeasured verdict: {MEASURED_VERDICT}")
    lines.append("\n*Caveat: TRF prints are mostly retail-wholesaler and internalized flow, not identifiable institutions. Direction is inferred and unmeasured; clusters have no measured predictive edge. Do not anchor stops or entries on them.*")
    return "\n".join(lines)


# In-memory thread-safe cache for AI summaries: { "TICKER": (timestamp, summary_dict) }
_AI_SUMMARY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_AI_CACHE_LOCK = threading.Lock()
AI_SUMMARY_CACHE_TTL = 300.0  # 5 minutes
_AI_DISK_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "darkpool_ai_cache.json"
)


def _load_disk_ai_cache() -> None:
    """Load unexpired entries from disk cache into in-memory cache."""
    if not os.path.exists(_AI_DISK_CACHE_PATH):
        return
    try:
        with open(_AI_DISK_CACHE_PATH, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        now = time.time()
        for sym, entry in disk_data.items():
            ts = entry.get("ts", 0)
            if now - ts < AI_SUMMARY_CACHE_TTL:
                _AI_SUMMARY_CACHE[sym] = (ts, entry.get("data", {}))
    except Exception as e:
        logger.debug(f"Could not load dark pool disk cache: {e}")


def _save_disk_ai_cache(sym: str, res_data: Dict[str, Any], ts: float) -> None:
    """Persist AI summary to disk cache."""
    try:
        os.makedirs(os.path.dirname(_AI_DISK_CACHE_PATH), exist_ok=True)
        disk_data = {}
        if os.path.exists(_AI_DISK_CACHE_PATH):
            try:
                with open(_AI_DISK_CACHE_PATH, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
            except Exception:
                disk_data = {}
        disk_data[sym] = {"ts": ts, "data": res_data}
        with open(_AI_DISK_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(disk_data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not write dark pool disk cache: {e}")


# Preload disk cache on module import
_load_disk_ai_cache()


def _generate_rules_based_summary(ticker: str, data: Dict[str, Any]) -> str:
    """Deterministic descriptive summary of the off-exchange tape (no support/accumulation claims)."""
    buy_pct = data.get("dp_buy_pct", 50.0)
    shelves = data.get("major_shelves", [])
    sigs = data.get("signature_prints", [])
    cov = "full session" if data.get("window_is_full_session") else f"only the last {data.get('window_minutes', 0)} min of the session"
    flow = (
        f"Inferred direction is {buy_pct}% buy over {data.get('dp_classified_pct', 0.0)}% of classified volume "
        f"({data.get('direction_method', 'tick')} rule): {data.get('sentiment')}."
    )
    tape = (
        f"{data.get('dp_trades_count', 0):,} off-exchange prints, {data.get('dp_total_volume', 0):,} shares "
        f"({data.get('dp_share_of_tape_pct', 0.0)}% of window volume); window covers {cov}."
    )
    if sigs:
        s = sigs[0]
        big = f"Largest print: {s['shares']:,} sh @ ${s['price']:.2f} (${s['notional']:,.0f}), inferred {s['direction']}."
    else:
        big = "No prints >= 1,000 sh and >= $250k in the window."
    if shelves:
        lv = ", ".join(f"${x['price']:.2f} ({x['volume']:,} sh)" for x in shelves[:3])
        lv_txt = f"Highest-volume off-exchange price levels: {lv}. These are volume clusters only; no support or resistance edge has been measured."
    else:
        lv_txt = "Volume is dispersed; no dominant off-exchange price level."
    return (
        f"* **Inferred Flow**: {flow} {tape}\n\n"
        f"* **Largest Prints**: {big}\n\n"
        f"* **Volume Clusters**: {lv_txt}\n\n"
        f"* **Caveat**: Descriptive only. TRF flow is mostly retail-wholesaler/internalized; direction is inferred and unmeasured."
    )


def generate_darkpool_ai_summary(ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Generate an incisive, institutional AI summary of off-exchange / dark pool tape.

    Uses the local LLM if online/idle, with an expert deterministic rules-engine
    fallback if offline or busy. Caches result for 5 minutes.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"status": "error", "ticker": "", "message": "Ticker symbol is required."}

    now = time.time()
    with _AI_CACHE_LOCK:
        if not force_refresh and sym in _AI_SUMMARY_CACHE:
            cached_time, cached_val = _AI_SUMMARY_CACHE[sym]
            if now - cached_time < AI_SUMMARY_CACHE_TTL:
                return cached_val

    # Fetch underlying dark pool data
    dp_data = fetch_darkpool_data(sym, force_refresh=force_refresh)
    if dp_data.get("status") != "ok" or not dp_data.get("dp_total_volume"):
        empty_res = {
            "status": "ok",
            "ticker": sym,
            "summary": f"No regular-session off-exchange FINRA TRF trades recorded for ${sym} to analyze.",
            "provider": "none",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return empty_res

    block = format_darkpool_block(sym)

    # Check if local LLM port 8000 is open
    llm_alive = False
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
            llm_alive = True
    except Exception:
        llm_alive = False

    summary_text = None
    provider = "rules_engine"

    if llm_alive:
        try:
            from openai import OpenAI
            client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy", timeout=4.0, max_retries=0)
            prompt = (
                f"Summarize this off-exchange (FINRA TRF) tape for ${sym} in 4 short bullets: inferred flow, largest prints, volume clusters, caveats.\n"
                "Rules: descriptive only. Do not claim accumulation, distribution, institutions, defense, absorption, support or resistance. "
                "State the window coverage and that direction is inferred.\n\n"
                f"DATA:\n{block}"
            )
            resp = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a careful tape analyst. Describe the off-exchange tape factually; make no predictions."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=600,
                temperature=0.2,
                timeout=4.0,
            )
            llm_out = (resp.choices[0].message.content if resp.choices else "").strip()
            if llm_out:
                summary_text = llm_out
                provider = "local_llm"
        except Exception as e:
            logger.warning(f"[{sym}] Local LLM dark pool summary generation failed ({e}); falling back to deterministic rules engine.")

    if not summary_text:
        summary_text = _generate_rules_based_summary(sym, dp_data)
        provider = "rules_engine"

    res = {
        "status": "ok",
        "ticker": sym,
        "summary": summary_text,
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with _AI_CACHE_LOCK:
        _AI_SUMMARY_CACHE[sym] = (now, res)
        _save_disk_ai_cache(sym, res, now)

    return res


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(f"Testing on-demand Dark Pool fetch for {target}...")
    res = fetch_darkpool_data(target, force_refresh=True)
    print(f"Status: {res.get('status')}")
    print(f"Total DP Volume: {res.get('dp_total_volume'):,}")
    print(f"Sentiment: {res.get('sentiment')} ({res.get('dp_buy_pct')}%)")
    print(f"Ladder Levels: {len(res.get('ladder', []))}")
    print("\nFormatted Block:\n")
    try:
        print(format_darkpool_block(target))
    except UnicodeEncodeError:
        print(format_darkpool_block(target).encode("ascii", "replace").decode("ascii"))

