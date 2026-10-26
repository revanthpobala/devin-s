"""Net dealer gamma exposure (GEX) from option open interest. Descriptive only.

Convention (SpotGamma/SqueezeMetrics): customers are assumed long calls and short puts, so
dealers are long call gamma (+) and short put gamma (-). The sign is an assumption, not observed.
GEX per contract = sign * OI * 100 * gamma * spot^2 * 0.01 (dollars per 1% spot move).
Gamma is Black-Scholes from each contract's own IV, so the flip can be found by re-pricing at other spots.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 120
_LEDGER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gex_ledger.jsonl")
_LEDGER_SEEN: set = set()
LEDGER_BUCKET_MIN = 30
INTRADAY_MAX_DTE = 1
SWING_MAX_DTE = 45
MIN_IV = 0.05  # large-cap floor; drops stale/zero IV quotes whose gamma explodes
MEASURED_VERDICT = (
    "Unmeasured: dealer sign is assumed (customers long calls, short puts), not observed; "
    "no backtest of net GEX or the flip exists in this repo."
)


def _bs_gamma(spot: float, strike: float, iv: float, t_years: float) -> float:
    if spot <= 0 or strike <= 0 or iv <= 0 or t_years <= 0:
        return 0.0
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / vol_t
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * vol_t)


def _t_years(dte: int, now_et: Optional[datetime] = None) -> float:
    if dte > 0:
        return dte / 365.0
    now_et = now_et or datetime.now(_ET)
    close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    minutes_left = max((close - now_et).total_seconds() / 60.0, 15.0)
    return minutes_left / (60.0 * 24.0 * 365.0)


def _net_gex(contracts: List[dict], spot: float, now_et: Optional[datetime]) -> float:
    total = 0.0
    for c in contracts:
        g = _bs_gamma(spot, c["strike"], c["iv"], _t_years(c["dte"], now_et))
        total += (1 if c["side"] == "C" else -1) * c["oi"] * 100 * g * spot * spot * 0.01
    return total


def _find_flip(contracts: List[dict], spot: float, now_et: Optional[datetime]) -> Optional[float]:
    """Spot where net GEX changes sign, nearest to current spot within +/-10%."""
    steps = [spot * (0.90 + 0.005 * i) for i in range(41)]
    vals = [_net_gex(contracts, s, now_et) for s in steps]
    best = None
    for i in range(len(steps) - 1):
        a, b = vals[i], vals[i + 1]
        if a == 0 or a * b < 0:
            x = steps[i] if a == 0 else steps[i] + (steps[i + 1] - steps[i]) * (a / (a - b))
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return round(best, 2) if best is not None else None


def compute_gex(contracts: List[dict], spot: float, max_dte: int, now_et: Optional[datetime] = None) -> Optional[dict]:
    """contracts: dicts with side ('C'/'P'), strike, oi, iv (decimal), dte (int)."""
    cs = [c for c in contracts if c["oi"] > 0 and c["iv"] >= MIN_IV and 0 <= c["dte"] <= max_dte]
    if not cs or spot <= 0:
        return None
    by_strike: Dict[float, Dict[str, float]] = {}
    for c in cs:
        g = _bs_gamma(spot, c["strike"], c["iv"], _t_years(c["dte"], now_et))
        v = c["oi"] * 100 * g * spot * spot * 0.01
        d = by_strike.setdefault(c["strike"], {"C": 0.0, "P": 0.0})
        d[c["side"]] += v
    net = sum(d["C"] - d["P"] for d in by_strike.values())
    call_wall = max(by_strike.items(), key=lambda kv: kv[1]["C"])
    put_wall = max(by_strike.items(), key=lambda kv: kv[1]["P"])
    top = sorted(by_strike.items(), key=lambda kv: abs(kv[1]["C"] - kv[1]["P"]), reverse=True)[:3]
    flip = _find_flip(cs, spot, now_et)
    return {
        "max_dte": max_dte,
        "contracts": len(cs),
        "net_gex_usd_per_1pct": round(net),
        "regime": "LONG_GAMMA" if net > 0 else "SHORT_GAMMA",
        "gamma_flip": flip,
        "call_gex_wall": call_wall[0] if call_wall[1]["C"] > 0 else None,
        "put_gex_wall": put_wall[0] if put_wall[1]["P"] > 0 else None,
        "top_net_strikes": [{"strike": k, "net_gex": round(v["C"] - v["P"])} for k, v in top],
    }


def build_gex(contracts: List[dict], spot: float, source: str, now_et: Optional[datetime] = None) -> dict:
    return {
        "status": "ok",
        "spot": spot,
        "source": source,
        "intraday": compute_gex(contracts, spot, INTRADAY_MAX_DTE, now_et),
        "swing": compute_gex(contracts, spot, SWING_MAX_DTE, now_et),
        "descriptive_only": True,
        "measured_verdict": MEASURED_VERDICT,
    }


def _schwab_contracts(ticker: str) -> Optional[tuple]:
    from src.clients.schwab_client import get_schwab_client
    client = get_schwab_client()
    sym = ticker.upper().replace(".", "/")
    etf = sym in {"SPY", "QQQ", "IWM", "DIA"}
    r = client.get_option_chain(
        sym,
        contract_type=client.Options.ContractType.ALL,
        strike_count=40 if etf else 50,
        to_date=(date.today() + timedelta(days=SWING_MAX_DTE)),
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("status") != "SUCCESS":
        return None
    spot = float(data.get("underlyingPrice") or 0.0)
    out = []
    for side, key in (("C", "callExpDateMap"), ("P", "putExpDateMap")):
        for exp_str, strikes in (data.get(key) or {}).items():
            for strike, cons in strikes.items():
                for c in cons:
                    iv = float(c.get("volatility") or 0.0) / 100.0
                    out.append({
                        "side": side,
                        "strike": float(strike),
                        "oi": int(c.get("openInterest") or 0),
                        "iv": iv if iv > 0 else 0.0,
                        "dte": int(c.get("daysToExpiration") or 0),
                    })
    return (out, spot) if out and spot > 0 else None


def _yfinance_contracts(ticker: str) -> Optional[tuple]:
    import yfinance as yf
    t = yf.Ticker(ticker)
    spot = float(t.fast_info["last_price"])
    today = date.today()
    out = []
    for exp in t.options or []:
        dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte < 0 or dte > SWING_MAX_DTE:
            continue
        chain = t.option_chain(exp)
        for side, df in (("C", chain.calls), ("P", chain.puts)):
            for row in df.itertuples():
                oi = getattr(row, "openInterest", 0)
                iv = getattr(row, "impliedVolatility", 0.0)
                if oi == oi and iv == iv:
                    out.append({"side": side, "strike": float(row.strike), "oi": int(oi), "iv": float(iv), "dte": dte})
    return (out, spot) if out and spot > 0 else None


def _record_ledger(sym: str, res: dict) -> None:
    """Append one snapshot per ticker per 30-min bucket so net GEX / flip / walls can be scored against forward returns."""
    if os.getenv("GEX_NO_LEDGER"):
        return
    now = datetime.now(timezone.utc)
    key = (sym, now.strftime("%Y-%m-%d"), now.hour, now.minute // LEDGER_BUCKET_MIN)
    if key in _LEDGER_SEEN:
        return
    _LEDGER_SEEN.add(key)
    row = {"recorded_utc": now.isoformat(), "ticker": sym, "spot": res["spot"], "source": res["source"],
           "intraday": res["intraday"], "swing": res["swing"]}
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        logger.debug(f"gex ledger write failed: {e}")


def fetch_gex(ticker: str, force_refresh: bool = False) -> dict:
    sym = ticker.upper().strip()
    hit = _CACHE.get(sym)
    if hit and not force_refresh and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    for name, fn in (("schwab", _schwab_contracts), ("yfinance", _yfinance_contracts)):
        try:
            got = fn(sym)
        except Exception as e:
            logger.debug(f"[{sym}] GEX via {name} failed: {e}")
            continue
        if got:
            res = build_gex(got[0], got[1], name)
            if res["intraday"] or res["swing"]:
                _CACHE[sym] = (time.time(), res)
                _record_ledger(sym, res)
                return res
    return {"status": "error", "error": "no option chain with open interest and IV"}


def _fmt_usd(v: float) -> str:
    return f"{'-' if v < 0 else '+'}${abs(v) / 1e6:,.1f}M"


def _scope_line(label: str, g: Optional[dict]) -> Optional[str]:
    if not g:
        return None
    walls = f"call wall {g['call_gex_wall']}, put wall {g['put_gex_wall']}"
    flip = g["gamma_flip"] if g["gamma_flip"] is not None else "none within +/-10%"
    return f"{label}: net {_fmt_usd(g['net_gex_usd_per_1pct'])} per 1% ({g['regime']}), flip {flip}, {walls}"


def gex_summary_line(res: dict) -> str:
    if res.get("status") != "ok":
        return "unavailable"
    g = res.get("intraday") or res.get("swing")
    label = "0-1 DTE" if res.get("intraday") else f"<={SWING_MAX_DTE} DTE"
    flip = g["gamma_flip"] if g["gamma_flip"] is not None else "none"
    return (
        f"{label} net GEX {_fmt_usd(g['net_gex_usd_per_1pct'])}/1% ({g['regime']}), flip {flip}, "
        f"call wall {g['call_gex_wall']}, put wall {g['put_gex_wall']}"
    )


def format_gex_block(ticker: str, intraday: bool = True) -> str:
    res = fetch_gex(ticker)
    if res.get("status") != "ok":
        return ""
    lines = [f"--- NET DEALER GAMMA (GEX) FOR {ticker.upper()} (spot {res['spot']:.2f}, {res['source']}, descriptive only) ---"]
    scopes = [("Swing (<=%d DTE)" % SWING_MAX_DTE, "swing")]
    if intraday:
        scopes.insert(0, ("Intraday (0-1 DTE)", "intraday"))
    for label, key in scopes:
        line = _scope_line(label, res.get(key))
        if line:
            lines.append(f"• {line}")
    lines.append("• LONG_GAMMA = dealers assumed to dampen moves; SHORT_GAMMA = amplify. Flip = spot where net GEX changes sign.")
    lines.append(f"• {res['measured_verdict']}")
    return "\n".join(lines)
