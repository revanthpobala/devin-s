import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import schwab
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load env in case it's run independently
load_dotenv()

SCHWAB_API_CLIENT_ID = os.getenv("SCHWAB_API_CLIENT_ID")
SCHWAB_API_CLIENT_SECRET = os.getenv("SCHWAB_API_CLIENT_SECRET")
SCHWAB_CALLBACK_URL = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "schwab_token.json")

_SCHWAB_CLIENT = None

def get_schwab_client(force_new: bool = False):
    global _SCHWAB_CLIENT
    if not force_new and _SCHWAB_CLIENT is not None:
        return _SCHWAB_CLIENT

    if not SCHWAB_API_CLIENT_ID or not SCHWAB_API_CLIENT_SECRET:
        raise ValueError("SCHWAB_API_CLIENT_ID or SCHWAB_API_CLIENT_SECRET is missing in .env")
        
    try:
        _SCHWAB_CLIENT = schwab.auth.client_from_token_file(
            TOKEN_PATH, 
            SCHWAB_API_CLIENT_ID, 
            SCHWAB_API_CLIENT_SECRET
        )
        return _SCHWAB_CLIENT
    except FileNotFoundError:
        raise Exception("schwab_token.json not found. Please run python setup_schwab.py first.")


_QUOTE_CACHE = {}
_QUOTE_CACHE_TTL = 3.0  # seconds


def get_realtime_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch live real-time equity/ETF quotes from Schwab API for multiple symbols in a single batch request.
    Returns dict mapping symbol -> quote_info dict.
    """
    if not symbols:
        return {}

    now_ts = time.time()
    results = {}
    to_fetch = []

    for s in symbols:
        s_u = str(s).upper().strip().replace(".", "/")
        if not s_u or s_u.startswith("^"):
            continue
        cached = _QUOTE_CACHE.get(s_u)
        if cached and (now_ts - cached[0] < _QUOTE_CACHE_TTL):
            results[s_u] = cached[1]
        else:
            to_fetch.append(s_u)

    if not to_fetch:
        return results

    try:
        client = get_schwab_client()
        # Chunk into slices of 50
        for i in range(0, len(to_fetch), 50):
            chunk = to_fetch[i : i + 50]
            try:
                resp = client.get_quotes(chunk)
            except Exception as ex_call:
                logger.debug(f"Schwab client.get_quotes call failed, re-authenticating: {ex_call}")
                client = get_schwab_client(force_new=True)
                resp = client.get_quotes(chunk)

            if resp.status_code == 401:
                client = get_schwab_client(force_new=True)
                resp = client.get_quotes(chunk)

            if resp.status_code == 200:
                data = resp.json()
                for sym_key, sym_val in data.items():
                    if not isinstance(sym_val, dict):
                        continue
                    q = sym_val.get("quote") or {}
                    last_px = (
                        q.get("lastPrice")
                        or q.get("closePrice")
                        or sym_val.get("regular", {}).get("regularMarketLastPrice")
                    )
                    if last_px is not None and float(last_px) > 0:
                        q_dict = {
                            "symbol": sym_key,
                            "last_price": float(last_px),
                            "bid": float(q.get("bidPrice") or 0.0),
                            "ask": float(q.get("askPrice") or 0.0),
                            "net_change": float(q.get("netChange") or 0.0),
                            "net_percent_change": float(q.get("netPercentChange") or 0.0),
                            "high": float(q.get("highPrice") or 0.0),
                            "low": float(q.get("lowPrice") or 0.0),
                            "open": float(q.get("openPrice") or 0.0),
                            "close": float(q.get("closePrice") or 0.0),
                            "volume": int(q.get("totalVolume") or 0),
                            "quote_time": q.get("quoteTime"),
                            "source": "SCHWAB",
                        }
                        _QUOTE_CACHE[sym_key] = (now_ts, q_dict)
                        results[sym_key] = q_dict
            else:
                logger.debug(f"Schwab get_quotes returned HTTP {resp.status_code}: {resp.text[:150]}")
    except Exception as e:
        logger.debug(f"Error fetching Schwab realtime quotes batch: {e}")

    return results


def get_realtime_quote(symbol: str) -> Optional[dict]:
    """
    Fetch live real-time equity/ETF quote from Schwab API for a single symbol.
    Returns dict with last_price, bid, ask, net_change, net_percent_change,
    high, low, open, close, volume, or None on failure.
    """
    sym = str(symbol).upper().strip().replace(".", "/")
    if not sym or sym.startswith("^"):
        return None
    res = get_realtime_quotes_batch([sym])
    return res.get(sym)


def get_last_price(symbol: str) -> Optional[float]:
    """Fetch single float last price from Schwab API."""
    q = get_realtime_quote(symbol)
    if q and q.get("last_price") and q["last_price"] > 0:
        return q["last_price"]
    return None


def get_unusual_options_flow(ticker: str) -> str:
    """
    Fetches the option chain for the next 90 days and scans for unusual
    options activity (Volume > OI * 1.5 and Volume > 500).
    """
    data = get_unusual_options_flow_data(ticker)
    if data.get("status") == "error":
        return f"Error: {data.get('error')}"

    anomalies = data.get("anomalies", [])
    if not anomalies:
        return f"No unusual options flow detected for {ticker} in the next 90 days."

    output = [f"### Unusual Options Flow for {ticker} (Next 90 Days)"]
    output.append(f"**Flow Bias:** {data.get('sentiment_label')}")
    output.append("| Type | Strike | Expiry | Volume | OI | Delta | Mid Price |")
    output.append("|------|--------|--------|--------|----|-------|-----------|")
    
    for a in anomalies[:15]:
        output.append(f"| {a['type']} | ${a['strike']} | {a['expiry']} | **{a['volume']}** | {a['open_interest']} | {a['delta']} | ${a['mid']} |")
        
    output.append("\n*Note: Volume > Open Interest * 1.5 indicates massive institutional positioning initiated today.*")
    return "\n".join(output)


_OPTIONS_FLOW_CACHE = {}
_CACHE_TTL = 60  # seconds


def get_unusual_options_flow_data(ticker: str, force_refresh: bool = False) -> dict:
    """
    Fetches the 90-day option chain from Schwab API and returns structured
    institutional sweep metrics + anomaly contract table.
    """
    sym = ticker.upper().strip()
    schwab_sym = sym.replace(".", "/")  # Handle share classes like BRK.B -> BRK/B
    now_ts = datetime.now().timestamp()
    
    if not force_refresh and sym in _OPTIONS_FLOW_CACHE:
        cached_ts, cached_data = _OPTIONS_FLOW_CACHE[sym]
        if now_ts - cached_ts < _CACHE_TTL:
            return cached_data

    try:
        client = get_schwab_client()
    except Exception as e:
        logger.error(f"Failed to initialize Schwab client: {e}")
        return {
            "ticker": sym,
            "status": "error",
            "error": f"Schwab API client is not authenticated: {e}",
            "anomalies": []
        }

    now = datetime.now()
    to_date = now + timedelta(days=90)
    
    try:
        # Note: Schwab API rejects from_date if it is today or in the past (HTTP 400).
        # We query up to 90 days out using to_date.date() and let Schwab default to the earliest active expiration.
        r = client.get_option_chain(
            schwab_sym,
            contract_type=client.Options.ContractType.ALL,
            to_date=to_date.date()
        )
        
        # If token was refreshed or 401 returned, force reconnect once
        if r.status_code == 401:
            logger.info("Schwab returned 401, re-authenticating client from token file...")
            client = get_schwab_client(force_new=True)
            r = client.get_option_chain(
                schwab_sym,
                contract_type=client.Options.ContractType.ALL,
                to_date=to_date.date()
            )

        # Fallback if 400 or any issue occurs with to_date
        if r.status_code != 200:
            logger.warning(f"Schwab API returned HTTP {r.status_code} with to_date={to_date.date()}, retrying full option chain query...")
            r = client.get_option_chain(
                schwab_sym,
                contract_type=client.Options.ContractType.ALL
            )

        if r.status_code != 200:
            err_detail = r.text[:300] if r.text else f"HTTP {r.status_code}"
            logger.error(f"Schwab API error for {sym}: HTTP {r.status_code} - {err_detail}")
            return {
                "ticker": sym,
                "status": "error",
                "error": f"Schwab API returned HTTP {r.status_code}: {err_detail}",
                "anomalies": []
            }
        data = r.json()
    except Exception as e:
        logger.error(f"Failed to fetch option chain from Schwab for {sym}: {e}")
        return {
            "ticker": sym,
            "status": "error",
            "error": str(e),
            "anomalies": []
        }

    if data.get("status") != "SUCCESS":
        return {
            "ticker": sym,
            "status": "error",
            "error": f"Schwab returned status {data.get('status')}",
            "anomalies": []
        }

    spot_price = float(data.get("underlyingPrice", 0.0))
    anomalies = []

    for side, exp_map in [("CALL", data.get("callExpDateMap", {})), ("PUT", data.get("putExpDateMap", {}))]:
        for exp_str, strikes in exp_map.items():
            exp_date = exp_str.split(":")[0]
            for strike, contracts in strikes.items():
                for c in contracts:
                    vol = c.get("totalVolume", 0)
                    oi = c.get("openInterest", 0)
                    if vol >= 500 and vol > (oi * 1.5):
                        bid = round(float(c.get("bid", 0.0)), 2)
                        ask = round(float(c.get("ask", 0.0)), 2)
                        mid = round((bid + ask) / 2.0, 2)
                        days = c.get("daysToExpiration", 0)
                        strike_val = float(strike)
                        vol_to_oi = round(vol / max(oi, 1), 1)
                        prem = int(vol * mid * 100)
                        anomalies.append({
                            "type": side,
                            "strike": strike_val,
                            "expiry": exp_date,
                            "dte": days,
                            "volume": vol,
                            "open_interest": oi,
                            "vol_to_oi": vol_to_oi,
                            "delta": round(float(c.get("delta", 0.0)), 3),
                            "bid": bid,
                            "ask": ask,
                            "mid": mid,
                            "notional_premium": prem,
                            "in_the_money": bool(c.get("inTheMoney", False)),
                            "distance_from_spot_pct": round(((strike_val - spot_price) / spot_price) * 100, 1) if spot_price else 0.0
                        })

    anomalies.sort(key=lambda x: x["volume"], reverse=True)

    calls = [a for a in anomalies if a["type"] == "CALL"]
    puts = [a for a in anomalies if a["type"] == "PUT"]

    call_vol = sum(a["volume"] for a in calls)
    put_vol = sum(a["volume"] for a in puts)
    call_prem = sum(a["notional_premium"] for a in calls)
    put_prem = sum(a["notional_premium"] for a in puts)

    if not anomalies:
        sentiment = "NEUTRAL"
        sentiment_label = "No Unusual Institutional Sweeps (Normal Baseline)"
    elif call_prem > put_prem * 1.5:
        sentiment = "BULLISH_SWEEPS"
        sentiment_label = "Bullish Institutional Call Accumulation"
    elif put_prem > call_prem * 1.5:
        sentiment = "BEARISH_SWEEPS"
        sentiment_label = "Bearish Institutional Put Hedging"
    else:
        sentiment = "MIXED"
        sentiment_label = "Balanced / Mixed Institutional Flow"

    result = {
        "ticker": sym,
        "status": "ok",
        "underlying_price": spot_price,
        "total_anomalies_count": len(anomalies),
        "call_sweeps_count": len(calls),
        "put_sweeps_count": len(puts),
        "total_call_volume": call_vol,
        "total_put_volume": put_vol,
        "total_call_premium": call_prem,
        "total_put_premium": put_prem,
        "put_call_volume_ratio": round(put_vol / max(call_vol, 1), 2),
        "put_call_premium_ratio": round(put_prem / max(call_prem, 1), 2),
        "sentiment": sentiment,
        "sentiment_label": sentiment_label,
        "anomalies": anomalies[:35],
        "cached_at": datetime.now().strftime("%I:%M:%S %p")
    }

    _OPTIONS_FLOW_CACHE[sym] = (now_ts, result)
    return result


_SCHWAB_POSITIONS_CACHE: tuple[float, dict] = (0.0, {})

def get_schwab_positions(force_refresh: bool = False) -> dict:
    """
    Fetches real-time account balances and open positions across all linked
    accounts using the authenticated Schwab Trader API with a 30s in-memory cache.
    """
    global _SCHWAB_POSITIONS_CACHE
    now_ts = time.time()
    last_ts, cached_data = _SCHWAB_POSITIONS_CACHE
    if not force_refresh and (now_ts - last_ts < 30.0) and cached_data:
        return cached_data

    try:
        client = get_schwab_client()
        resp = client.get_accounts(fields=client.Account.Fields.POSITIONS)
        if resp.status_code != 200:
            return {
                "status": "error",
                "error": f"Schwab API returned HTTP {resp.status_code}: {resp.text}",
                "accounts": []
            }

        data = resp.json()
        accounts_out = []
        total_positions_count = 0

        for acc in data:
            sec = acc.get("securitiesAccount", {})
            acc_type = sec.get("type", "UNKNOWN")
            acc_num = sec.get("accountNumber", "")
            masked_num = f"***{acc_num[-4:]}" if acc_num else "N/A"
            cur_bal = sec.get("currentBalances", {})

            positions_raw = sec.get("positions", [])
            positions_clean = []
            for p in positions_raw:
                inst = p.get("instrument", {})
                sym = inst.get("symbol")
                qty = p.get("longQuantity", 0) - p.get("shortQuantity", 0)
                avg_price = p.get("averagePrice", 0.0)
                market_val = p.get("marketValue", 0.0)
                day_pnl = p.get("currentDayProfitLoss", 0.0)
                day_pnl_pct = p.get("currentDayProfitLossPercentage", 0.0)

                positions_clean.append({
                    "symbol": sym,
                    "asset_type": inst.get("assetType"),
                    "quantity": qty,
                    "average_price": avg_price,
                    "market_value": market_val,
                    "day_pnl": day_pnl,
                    "day_pnl_pct": day_pnl_pct,
                })

            total_positions_count += len(positions_clean)
            accounts_out.append({
                "account_number": masked_num,
                "type": acc_type,
                "liquidation_value": cur_bal.get("liquidationValue", 0.0),
                "cash_balance": cur_bal.get("cashBalance", 0.0),
                "buying_power": cur_bal.get("buyingPower", 0.0),
                "available_funds": cur_bal.get("availableFunds", 0.0),
                "positions_count": len(positions_clean),
                "positions": positions_clean
            })

        result = {
            "status": "ok",
            "accounts_count": len(accounts_out),
            "total_positions_count": total_positions_count,
            "accounts": accounts_out
        }
        _SCHWAB_POSITIONS_CACHE = (now_ts, result)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch Schwab positions: {e}")
        return {
            "status": "error",
            "error": str(e),
            "accounts": []
        }


def get_active_position_for_ticker(ticker: str) -> dict:
    """
    Checks if the user has an active position in `ticker` by checking:
    1. Authenticated Schwab Trader API positions.
    2. Local positions state (`data/positions.json`).
    
    Returns structured position dictionary if found, else {"has_position": False, "ticker": ticker}.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"has_position": False, "ticker": ""}

    # 1. Check Schwab live positions
    try:
        schwab_data = get_schwab_positions()
        if schwab_data.get("status") == "ok":
            for acc in schwab_data.get("accounts", []):
                for p in acc.get("positions", []):
                    pos_sym = (p.get("symbol") or "").upper().strip()
                    if pos_sym == sym and p.get("quantity") != 0:
                        qty = p.get("quantity", 0)
                        avg_px = p.get("average_price", 0.0)
                        mv = p.get("market_value", 0.0)
                        dp = p.get("day_pnl", 0.0)
                        dp_pct = p.get("day_pnl_pct", 0.0)
                        return {
                            "has_position": True,
                            "source": "SCHWAB",
                            "ticker": sym,
                            "quantity": qty,
                            "average_price": avg_px,
                            "market_value": mv,
                            "day_pnl": dp,
                            "day_pnl_pct": dp_pct,
                            "side": "LONG" if qty > 0 else "SHORT",
                            "account": acc.get("account_number"),
                            "asset_type": p.get("asset_type", "EQUITY")
                        }
    except Exception as e_schwab:
        logger.debug(f"Schwab position check for {sym}: {e_schwab}")

    # 2. Check local positions.json state
    try:
        from src.tracking.position_state import load_state
        state = load_state()
        if sym in state:
            rec = state[sym]
            raw_a = rec.get("raw_alert") or {}
            qty = raw_a.get("shares") or rec.get("shares") or 1
            entry = rec.get("entry_price") or rec.get("alert_price") or 0.0
            side = rec.get("side", "LONG").upper()
            last_px = rec.get("last_price") or entry
            stop = rec.get("stop")
            target = rec.get("target")

            diff = (last_px - entry) if "LONG" in side else (entry - last_px)
            tot_diff = diff * qty
            pct = ((last_px - entry) / entry * 100) if entry > 0 else 0.0

            return {
                "has_position": True,
                "source": "PORTFOLIO",
                "ticker": sym,
                "quantity": qty,
                "average_price": entry,
                "market_value": round(last_px * qty, 2),
                "day_pnl": round(tot_diff, 2),
                "day_pnl_pct": round(pct, 2),
                "side": side,
                "stop": stop,
                "target": target,
                "strategy": rec.get("strategy", "Swing Stalk"),
                "opened_at": rec.get("opened_at"),
                "last_price": last_px
            }
    except Exception as e_local:
        logger.debug(f"Local position check for {sym}: {e_local}")

    return {"has_position": False, "ticker": sym}


def get_all_active_positions() -> list[dict]:
    """
    Returns list of all active positions combined from:
    1. Authenticated Schwab Trader API positions.
    2. Local positions state (data/positions.json).
    """
    seen = set()
    all_pos = []

    # 1. Schwab
    try:
        schwab_data = get_schwab_positions()
        if schwab_data.get("status") == "ok":
            for acc in schwab_data.get("accounts", []):
                for p in acc.get("positions", []):
                    pos_sym = (p.get("symbol") or "").upper().strip()
                    qty = p.get("quantity", 0)
                    if pos_sym and qty != 0 and pos_sym not in seen:
                        seen.add(pos_sym)
                        all_pos.append({
                            "has_position": True,
                            "source": "SCHWAB",
                            "ticker": pos_sym,
                            "quantity": qty,
                            "average_price": p.get("average_price", 0.0),
                            "market_value": p.get("market_value", 0.0),
                            "day_pnl": p.get("day_pnl", 0.0),
                            "day_pnl_pct": p.get("day_pnl_pct", 0.0),
                            "side": "LONG" if qty > 0 else "SHORT",
                            "account": acc.get("account_number"),
                            "asset_type": p.get("asset_type", "EQUITY"),
                        })
    except Exception as e:
        logger.debug(f"Error getting Schwab all positions: {e}")

    # 2. Local positions.json
    try:
        from src.tracking.position_state import load_state
        state = load_state()
        for sym, rec in state.items():
            sym_u = (sym or "").upper().strip()
            if sym_u and sym_u not in seen:
                seen.add(sym_u)
                raw_a = rec.get("raw_alert") or {}
                qty = raw_a.get("shares") or rec.get("shares") or 1
                entry = rec.get("entry_price") or rec.get("alert_price") or 0.0
                side = rec.get("side", "LONG").upper()
                last_px = rec.get("last_price") or entry
                diff = (last_px - entry) if "LONG" in side or "BUY" in side else (entry - last_px)
                pnl_pct = (diff / entry) * 100 if entry > 0 else 0.0
                all_pos.append({
                    "has_position": True,
                    "source": "PORTFOLIO",
                    "ticker": sym_u,
                    "quantity": qty,
                    "average_price": entry,
                    "last_price": last_px,
                    "market_value": round(last_px * qty, 2),
                    "day_pnl": round(diff * qty, 2),
                    "day_pnl_pct": round(pnl_pct, 2),
                    "stop": rec.get("stop"),
                    "target": rec.get("target"),
                    "side": side,
                    "strategy": rec.get("strategy", "Intraday"),
                    "opened_at": rec.get("opened_at"),
                })
    except Exception as e:
        logger.debug(f"Error getting local all positions: {e}")

    return all_pos


if __name__ == "__main__":
    print("Testing Schwab Client Options Flow & Positions...")
    pos = get_schwab_positions()
    print("Positions status:", pos.get("status"))
    print("Accounts:", pos.get("accounts_count"))
    print("Positions count:", pos.get("total_positions_count"))


