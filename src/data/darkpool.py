"""
src/data/darkpool.py — On-demand Dark Pool & Off-Exchange Order Flow Engine.

Fetches off-exchange prints (FINRA TRF / ADF tape, exchange code 'D') via Alpaca SIP feed,
aggregates volume by price into an institutional level ladder, identifies signature block trades,
and classifies institutional net flow (Accumulation vs Distribution).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("darkpool")

# In-memory thread-safe cache: { "TICKER": (timestamp, data_dict) }
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 60.0  # 1 minute fresh cache


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
    min_block_shares: int = 2500,
    min_block_notional: float = 250000.0,
) -> Dict[str, Any]:
    """Fetch on-demand dark pool trades from Alpaca SIP feed, aggregate levels,

    and detect institutional signature prints and net accumulation flow.
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

    # Fetch latest trades with sort='desc' so we get real-time market data
    today_iso = date.today().isoformat()
    params: Dict[str, Any] = {
        "limit": min(limit, 10000),
        "feed": feed,
        "sort": "desc",
        "start": f"{today_iso}T00:00:00Z",
    }

    trades: List[Dict[str, Any]] = []
    trade_date_str = today_iso

    try:
        url = f"{base_url}/v2/stocks/{sym}/trades"
        resp = requests.get(url, params=params, headers=headers, timeout=12)

        if resp.status_code == 200:
            body = resp.json()
            trades = body.get("trades") or []
            next_token = body.get("next_page_token")
            # If caller requested > 10,000 trades, paginate up to 20,000
            if next_token and limit > 10000 and len(trades) >= 10000:
                try:
                    params_p2 = dict(params)
                    params_p2["page_token"] = next_token
                    params_p2["limit"] = min(limit - len(trades), 10000)
                    resp2 = requests.get(url, params=params_p2, headers=headers, timeout=8)
                    if resp2.status_code == 200:
                        trades.extend(resp2.json().get("trades") or [])
                except Exception as p2_err:
                    logger.debug(f"[{sym}] Page 2 trade fetch error (non-fatal): {p2_err}")
        elif resp.status_code == 429:
            logger.warning(f"[{sym}] Alpaca rate limit encountered (429).")
            return {
                "status": "error",
                "message": "Alpaca API rate limit reached. Please wait a moment before refreshing.",
                "ticker": sym,
            }
        else:
            logger.warning(f"[{sym}] Alpaca trades request returned status {resp.status_code}: {resp.text[:120]}")

        # Fallback if market closed / weekend / holiday (no trades today yet):
        # Fetch the most recent trades without a restrictive 'start' param
        if not trades:
            params_fallback = {"limit": min(limit, 10000), "feed": feed, "sort": "desc"}
            fallback_resp = requests.get(url, params=params_fallback, headers=headers, timeout=12)
            if fallback_resp.status_code == 200:
                body = fallback_resp.json()
                trades = body.get("trades") or []

    except Exception as e:
        logger.error(f"[{sym}] Exception during Alpaca dark pool fetch: {e}")
        return {
            "status": "error",
            "message": f"Network or API error while fetching dark pool trades: {e}",
            "ticker": sym,
        }

    # Since sort='desc' was used to fetch latest trades, reverse into chronological order (oldest -> newest)
    # for valid tick rule direction evaluation and temporal ladder flow
    if trades:
        trades.reverse()
        last_ts = trades[-1].get("t")
        if last_ts and len(last_ts) >= 10:
            trade_date_str = last_ts[:10]

    if not trades:
        res_empty = {
            "status": "ok",
            "ticker": sym,
            "trade_date": trade_date_str,
            "total_trades_scanned": 0,
            "dp_trades_count": 0,
            "dp_total_volume": 0,
            "dp_total_notional": 0.0,
            "dp_buy_volume": 0,
            "dp_sell_volume": 0,
            "dp_buy_pct": 50.0,
            "sentiment": "NEUTRAL / NO ACTIVITY",
            "ladder": [],
            "signature_prints": [],
            "major_shelves": [],
            "message": "No off-exchange trades found for this session.",
        }
        with _CACHE_LOCK:
            _CACHE[sym] = (now, res_empty)
        return res_empty

    # Filter off-exchange (FINRA TRF / ADF trades have exchange code 'D')
    dp_trades = [t for t in trades if t.get("x") == "D"]

    # Price Ladder aggregation: price -> {prints, volume, notional, max_block, buy_vol, sell_vol}
    ladder_map = defaultdict(lambda: {
        "prints": 0,
        "volume": 0,
        "notional": 0.0,
        "max_block": 0,
        "buy_volume": 0,
        "sell_volume": 0,
    })

    dp_buy_vol = 0
    dp_sell_vol = 0
    dp_total_notional = 0.0
    dp_total_vol = 0
    signature_prints: List[Dict[str, Any]] = []

    prev_raw_price: Optional[float] = None
    last_known_direction = "BUY"  # Default assumption if exact tick match

    # Conditions representing non-standard benchmark / derivative / late prints:
    # '4': Derivatively priced, 'W': Average price / VWAP, 'P': Prior reference price, 'Z': Out of sequence
    NON_TICK_CONDITIONS = {"4", "W", "P", "Z"}

    for t in dp_trades:
        raw_price = float(t.get("p", 0.0))
        price = round(raw_price, 2)
        size = int(t.get("s", 0))
        if price <= 0 or size <= 0:
            continue

        conds = set(t.get("c") or [])
        is_deriv_or_benchmark = bool(conds & NON_TICK_CONDITIONS)

        notional = price * size
        dp_total_vol += size
        dp_total_notional += notional

        entry = ladder_map[price]
        entry["prints"] += 1
        entry["volume"] += size
        entry["notional"] += notional
        if size > entry["max_block"]:
            entry["max_block"] = size

        # Tick Rule classification:
        # Non-standard derivative/average-price prints inherit last direction but do not update baseline
        if not is_deriv_or_benchmark:
            if prev_raw_price is not None:
                if raw_price > prev_raw_price:
                    last_known_direction = "BUY"
                elif raw_price < prev_raw_price:
                    last_known_direction = "SELL"
            prev_raw_price = raw_price

        if last_known_direction == "BUY":
            dp_buy_vol += size
            entry["buy_volume"] += size
        else:
            dp_sell_vol += size
            entry["sell_volume"] += size

        # Institutional Signature Print detection
        if size >= min_block_shares or notional >= min_block_notional:
            signature_prints.append({
                "price": price,
                "shares": size,
                "notional": round(notional, 2),
                "timestamp": t.get("t", ""),
                "direction": last_known_direction,
                "condition": t.get("c", []),
            })

    # Sort signature prints descending by notional value
    signature_prints.sort(key=lambda x: x["notional"], reverse=True)
    top_signatures = signature_prints[:15]

    # Find maximum volume at any single level for relative bar width
    max_level_vol = max((d["volume"] for d in ladder_map.values()), default=1)
    avg_level_vol = (dp_total_vol / len(ladder_map)) if ladder_map else 0

    # Build sorted ladder (descending by price, highest price at top like a classic L2 book)
    ladder_list = []
    major_shelves = []

    for price in sorted(ladder_map.keys(), reverse=True):
        info = ladder_map[price]
        vol = info["volume"]
        pct_of_max = round((vol / max_level_vol) * 100.0, 1) if max_level_vol > 0 else 0.0
        is_major = vol >= (avg_level_vol * 1.8) and vol >= 1000

        ladder_item = {
            "price": price,
            "prints": info["prints"],
            "volume": vol,
            "notional": round(info["notional"], 2),
            "max_block": info["max_block"],
            "buy_volume": info["buy_volume"],
            "sell_volume": info["sell_volume"],
            "pct_of_max": pct_of_max,
            "is_major_shelf": is_major,
        }
        ladder_list.append(ladder_item)

        if is_major:
            major_shelves.append({
                "price": price,
                "volume": vol,
                "notional": round(info["notional"], 2),
                "prints": info["prints"],
            })

    # Sort major shelves by volume descending to find primary support/resistance levels
    major_shelves.sort(key=lambda x: x["volume"], reverse=True)

    # Calculate net flow sentiment
    total_classified = dp_buy_vol + dp_sell_vol
    dp_buy_pct = round((dp_buy_vol / total_classified) * 100.0, 1) if total_classified > 0 else 50.0

    if dp_buy_pct >= 58.0:
        sentiment = "ACCUMULATION (BULLISH)"
    elif dp_buy_pct <= 42.0:
        sentiment = "DISTRIBUTION (BEARISH)"
    else:
        sentiment = "BALANCED / CONTESTED"

    total_shares_all = sum(t.get("s", 0) for t in trades)
    dp_share_of_tape_pct = round((dp_total_vol / total_shares_all) * 100.0, 1) if total_shares_all > 0 else 0.0

    result = {
        "status": "ok",
        "ticker": sym,
        "trade_date": trade_date_str,
        "total_trades_scanned": len(trades),
        "dp_trades_count": len(dp_trades),
        "dp_total_volume": dp_total_vol,
        "dp_total_notional": round(dp_total_notional, 2),
        "dp_share_of_tape_pct": dp_share_of_tape_pct,
        "dp_buy_volume": dp_buy_vol,
        "dp_sell_volume": dp_sell_vol,
        "dp_buy_pct": dp_buy_pct,
        "sentiment": sentiment,
        "ladder": ladder_list,
        "signature_prints": top_signatures,
        "major_shelves": major_shelves[:6],
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

    return result


def format_darkpool_block(ticker: str) -> str:
    """Format on-demand dark pool data into a clean, authoritative Markdown context block

    suitable for Deep Research, LLM debate, and PM arbitration.
    """
    data = fetch_darkpool_data(ticker)
    if data.get("status") != "ok":
        return f"--- 🏛️ OFF-EXCHANGE / DARK POOL ORDER FLOW ---\n(Dark pool data unavailable for {ticker}: {data.get('message', 'Unknown error')})"

    sym = data["ticker"]
    dp_vol = data["dp_total_volume"]
    dp_notional = data["dp_total_notional"]
    buy_pct = data["dp_buy_pct"]
    sentiment = data["sentiment"]
    tape_pct = data.get("dp_share_of_tape_pct", 0.0)

    lines = [
        f"--- OFF-EXCHANGE / DARK POOL ORDER FLOW ({sym}) ---",
        f"- Net Flow Sentiment: **{sentiment}** ({buy_pct}% Institutional Buy Flow)",
        f"- Off-Exchange Volume: **{dp_vol:,} shares** (${dp_notional:,.0f} notional across {data['dp_trades_count']:,} TRF prints)",
        f"- Dark Pool Tape Share: **{tape_pct}%** of all observed volume",
    ]

    tt_vol = data.get("tastytrade_volatility")
    if tt_vol and tt_vol.get("iv_rank") is not None:
        ivr = tt_vol["iv_rank"]
        ivp = tt_vol.get("iv_percentile", "N/A")
        hv = tt_vol.get("hv_30d", "N/A")
        spread = tt_vol.get("iv_hv_spread", "N/A")
        spread_str = f"{spread:+} pts" if isinstance(spread, (int, float)) else str(spread)
        lines.append(f"- Institutional Options Volatility (Tastytrade): **IV Rank: {ivr}%** | **IV %ile: {ivp}%** | **30d HV: {hv}%** (IV-HV Spread: {spread_str})")

    lines.append("")
    lines.append("#### Key Institutional Defense / Resistance Shelves (Volume Clusters):")

    shelves = data.get("major_shelves", [])
    if shelves:
        for idx, s in enumerate(shelves[:4], 1):
            p = s["price"]
            v = s["volume"]
            n = s["notional"]
            cnt = s["prints"]
            lines.append(f"  {idx}. **${p:.2f}** -> {v:,} shares across {cnt} prints (${n:,.0f} notional cluster)")
    else:
        lines.append("  (No dense clusters detected; flow is dispersed across price levels)")

    sigs = data.get("signature_prints", [])
    if sigs:
        lines.append("\n#### Top Institutional Signature Block Trades:")
        for s in sigs[:3]:
            px = s["price"]
            sh = s["shares"]
            notional = s["notional"]
            dirn = s["direction"]
            ts = s["timestamp"]
            ts_short = ts.split("T")[-1][:8] if "T" in ts else ts
            lines.append(f"  - **{sh:,} shares** @ **${px:.2f}** (${notional:,.0f} | {dirn} | {ts_short} UTC)")


    lines.append(
        "\n*PM Directive: Anchor structural entry zones and invalidation stops around major dark pool shelves where institutional liquidity was absorbed.*"
    )

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
    """Generate a high-precision deterministic institutional summary of dark pool tape."""
    sym = (ticker or "").upper()
    buy_pct = data.get("dp_buy_pct", 50.0)
    total_vol = data.get("dp_total_volume", 0)
    total_notional = data.get("dp_total_notional", 0.0)
    prints_cnt = data.get("dp_trades_count", 0)
    tape_pct = data.get("dp_share_of_tape_pct", 0.0)
    sentiment = data.get("sentiment", "BALANCED / CONTESTED")
    shelves = data.get("major_shelves", [])
    sigs = data.get("signature_prints", [])

    # 1. Bias
    if buy_pct >= 58.0:
        bias_desc = (
            f"**Aggressive Institutional Accumulation ({buy_pct}% Buy Flow)**. "
            f"Buyers are actively lifting offers on the off-exchange tape across {total_vol:,} shares (${total_notional:,.0f} notional)."
        )
    elif buy_pct <= 42.0:
        bias_desc = (
            f"**Institutional Distribution / Net Liquidation ({100 - buy_pct:.1f}% Sell Flow)**. "
            f"Sellers are actively hitting bids on the off-exchange tape across {total_vol:,} shares (${total_notional:,.0f} notional)."
        )
    else:
        bias_desc = (
            f"**Balanced / Contested Tape ({buy_pct}% Buy / {100 - buy_pct:.1f}% Sell)**. "
            f"Order flow is two-way and evenly matched across {total_vol:,} shares (${total_notional:,.0f} notional). "
            f"No dominant breakout trend is underway; institutions are trading range-bound inventory."
        )

    tape_desc = (
        f"With **{tape_pct}%** of all observed volume executing off-exchange across {prints_cnt:,} prints, institutions are heavily internalizing order flow."
        if tape_pct > 0
        else f"Off-exchange volume accounts for {prints_cnt:,} TRF prints."
    )

    # 2. Signature Trades
    if sigs:
        top_s = sigs[0]
        s_px = top_s.get("price", 0.0)
        s_sh = top_s.get("shares", 0)
        s_notional = top_s.get("notional", 0.0)
        s_dir = top_s.get("direction", "NEUTRAL")
        s_ts = top_s.get("timestamp", "").split("T")[-1][:8] if "T" in str(top_s.get("timestamp", "")) else str(top_s.get("timestamp", ""))

        near_shelf = any(abs(s["price"] - s_px) <= 0.15 for s in shelves)
        if s_dir == "SELL":
            if near_shelf:
                sig_interp = (
                    f"Largest signature print was **{s_sh:,} shares @ ${s_px:.2f}** (${s_notional:,.0f} | {s_ts} UTC). "
                    f"While tagged as a `SELL` (printed on bid/downtick), it executed directly inside a primary volume shelf, "
                    f"indicating **passive limit absorption by buyers** rather than aggressive market dumping."
                )
            else:
                sig_interp = (
                    f"Largest signature print was **{s_sh:,} shares @ ${s_px:.2f}** (${s_notional:,.0f} | {s_ts} UTC), "
                    f"tagged as a `SELL` (seller-initiated on bid). Treat this level as potential overhead supply."
                )
        elif s_dir == "BUY":
            sig_interp = (
                f"Largest signature print was **{s_sh:,} shares @ ${s_px:.2f}** (${s_notional:,.0f} | {s_ts} UTC), "
                f"tagged as a `BUY` (buyer-initiated on ask). Institutions were aggressive at this level, establishing a defense floor."
            )
        else:
            sig_interp = f"Largest signature print was **{s_sh:,} shares @ ${s_px:.2f}** (${s_notional:,.0f} | {s_ts} UTC)."
    else:
        sig_interp = "No single block trades \u2265 2,500 shares or $250k notional detected. Order flow is fragmented across retail and algorithmic sizes."

    # 3. Shelves
    if shelves:
        shelf_items = [f"**${s['price']:.2f}** ({s['volume']:,} shs / ${s['notional']:,.0f})" for s in shelves[:3]]
        shelves_desc = (
            f"Major institutional liquidity clusters are anchored at: {', '.join(shelf_items)}. "
            f"These dense price shelves represent high-volume absorption points where market makers and institutions have accumulated inventory."
        )
        p_prices = [s["price"] for s in shelves]
        min_shelf = min(p_prices)
        max_shelf = max(p_prices)
    else:
        shelves_desc = "Flow is broadly dispersed across price tiers with no single high-density liquidity shelf."
        min_shelf = None
        max_shelf = None

    # 4. Options Volatility Posture (Tastytrade)
    tt_vol = data.get("tastytrade_volatility") or {}
    ivr = tt_vol.get("iv_rank")
    ivp = tt_vol.get("iv_percentile")
    hv = tt_vol.get("hv_30d")
    spread = tt_vol.get("iv_hv_spread")

    if ivr is not None:
        if ivr >= 65.0:
            vol_posture = (
                f"Tastytrade IV Rank is elevated at **{ivr}%** (IV %ile: **{ivp}%**, 30d HV: **{hv}%**). "
                f"Options volatility carries an elevated premium ({'+' if (spread or 0) > 0 else ''}{spread} pts spread vs realized moves), "
                f"signaling that institutions are actively pricing imminent catalyst risk or using options to hedge underlying dark pool inventory."
            )
        elif ivr <= 25.0:
            vol_posture = (
                f"Tastytrade IV Rank is heavily compressed at **{ivr}%** (IV %ile: **{ivp}%**, 30d HV: **{hv}%**). "
                f"Options are historically cheap. Institutional accumulation inside these dark pool shelves represents "
                f"low-cost inventory building ahead of an eventual volatility expansion."
            )
        else:
            vol_posture = (
                f"Tastytrade IV Rank sits in a neutral equilibrium at **{ivr}%** (IV %ile: **{ivp}%**, 30d HV: **{hv}%**). "
                f"Options pricing aligns with historical realized volatility."
            )
    else:
        vol_posture = "Tastytrade IV rank and options volatility metrics unavailable for this session."

    # 5. Tactical Gameplan
    if min_shelf is not None and max_shelf is not None:
        span_pct = (max_shelf - min_shelf) / min_shelf if min_shelf > 0 else 0.0
        if span_pct < 0.015:
            gameplan = (
                f"Price is coiling tightly within a ${min_shelf:.2f} \u2013 ${max_shelf:.2f} pocket ({span_pct * 100:.1f}% span). "
                f"Treat **${min_shelf - 0.05:.2f}** as the primary absorption floor and **${max_shelf + 0.05:.2f}** as the immediate ceiling. "
                f"Expect rotation between these bounds until volume expands significantly."
            )
        else:
            gameplan = (
                f"Key institutional support floor is **${min_shelf:.2f}** (absorption floor). Key overhead resistance is **${max_shelf:.2f}**. "
                f"A confirmed break below ${min_shelf:.2f} invalidates institutional defense, while clearing ${max_shelf:.2f} on volume opens runway."
            )
    else:
        gameplan = f"Flow sentiment is {sentiment}. Anchor tactical stops around the primary defense shelf where institutional liquidity was absorbed."

    return (
        f"* **Net Institutional Bias**: {bias_desc} {tape_desc}\n\n"
        f"* **Signature Block Trades**: {sig_interp}\n\n"
        f"* **Key Defense / Resistance Shelves**: {shelves_desc}\n\n"
        f"* **Options Volatility Posture (Tastytrade)**: {vol_posture}\n\n"
        f"* **Tactical Gameplan**: {gameplan}"
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
            "summary": f"No off-exchange FINRA TRF trades recorded for ${sym} in this session to analyze.",
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
                f"Analyze the following real-time Dark Pool (Alpaca SIP) and Options Volatility (Tastytrade) data for ${sym}.\n"
                "Provide a concise, professional institutional briefing (use clear markdown bullet points with bold headers):\n"
                "1. **Net Institutional Bias**: Accumulation vs. distribution, tape share context, and what the buy% indicates.\n"
                "2. **Signature Block Trades Analysis**: Detail the largest block prints, who was aggressive (bid-hitter vs ask-lifter), and whether large trades represent distribution dumping or passive liquidity absorption.\n"
                "3. **Key Institutional Defense / Resistance Shelves**: Analyze the primary volume clusters, how dense they are, and exact price levels where institutions are defending.\n"
                "4. **Options Volatility Posture (Tastytrade)**: Interpret the IV Rank, IV Percentile, and IV-HV spread relative to the off-exchange stock accumulation/distribution.\n"
                "5. **Tactical Gameplan**: Actionable takeaway (support floors, overhead caps, invalidation levels, and whether to play breakouts or mean-reversion).\n\n"
                f"DATA:\n{block}"
            )
            resp = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert institutional order flow specialist and market maker. Provide an incisive, highly actionable institutional analysis of the off-exchange / dark pool tape."},
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

