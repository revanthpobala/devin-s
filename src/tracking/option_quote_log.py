"""Live option quotes for every option structure the pipeline suggests, stored for later scoring.

The suggestion ledger keeps strikes and a target debit but no market prices, so no option trade can be
scored on what it really cost or was worth. For each live OPTIONS/INCOME suggestion (covered call, cash-secured
put, spreads, long call/put) this logs bid, ask, mid, IV, delta, OI and volume per leg:
    SUGGESTED  first capture after the suggestion appears (flag `rth` says whether the market was open)
    MARK       once per day after the close until expiry
`net_mid` is the structure's mid: credit positive (short legs add, long legs subtract).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src import config

logger = logging.getLogger(__name__)

MIN_INTERVAL_S = 300
_last_run = 0.0


def db_path():
    return config.BASE_DIR / "data" / "option_quote_log.db"


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS opt_quote (audit_id INTEGER, ticker TEXT, struct TEXT, date TEXT, phase TEXT, rth INTEGER,"
        " leg TEXT, cp TEXT, strike REAL, expiry TEXT, spot REAL, bid REAL, ask REAL, mid REAL, iv REAL, delta REAL,"
        " oi INTEGER, volume INTEGER, net_mid REAL, target_debit REAL, captured_at TEXT,"
        " UNIQUE(audit_id, date, phase, leg))"
    )


def legs_for(struct: str, long_k: float, short_k: float) -> List[Tuple[str, str, float]]:
    """(leg, C/P, strike) for a suggested structure. Income structures are short-only; long call/put long-only."""
    s = (struct or "").upper()
    cp = "C" if "CALL" in s else "P"
    out = []
    if short_k > 0 and s not in ("LONG_CALL", "LONG_PUT"):
        out.append(("SHORT", cp, short_k))
    if long_k > 0 and s not in ("COVERED_CALL", "CASH_SECURED_PUT"):
        out.append(("LONG", cp, long_k))
    return out


def pick_contract(chain: Dict[str, Any], cp: str, expiry: str, strike: float) -> Optional[Dict[str, Any]]:
    for exp_key, strikes in (chain.get("callExpDateMap" if cp == "C" else "putExpDateMap") or {}).items():
        if exp_key.split(":")[0] != expiry:
            continue
        for k, contracts in strikes.items():
            if abs(float(k) - strike) < 1e-6 and contracts:
                return contracts[0]
    return None


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def build_rows(audit: Dict[str, Any], chain: Dict[str, Any], day: str, phase: str, rth: bool) -> List[Dict[str, Any]]:
    rows, net = [], 0.0
    for leg, cp, strike in legs_for(audit["trade_structure"], audit["long_strike"] or 0.0, audit["short_strike"] or 0.0):
        c = pick_contract(chain, cp, audit["options_expiration"], strike)
        if not c:
            continue
        bid, ask = _num(c.get("bid")), _num(c.get("ask"))
        mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else _num(c.get("mark"))
        net += (mid or 0.0) * (1 if leg == "SHORT" else -1)
        rows.append({"audit_id": audit["id"], "ticker": audit["ticker"], "struct": audit["trade_structure"], "date": day,
                     "phase": phase, "rth": int(rth), "leg": leg, "cp": cp, "strike": strike,
                     "expiry": audit["options_expiration"], "spot": _num(chain.get("underlyingPrice")), "bid": bid,
                     "ask": ask, "mid": mid, "iv": _num(c.get("volatility")), "delta": _num(c.get("delta")),
                     "oi": int(c.get("openInterest") or 0), "volume": int(c.get("totalVolume") or 0),
                     "target_debit": audit["target_debit"]})
    for r in rows:
        r["net_mid"] = round(net, 4) if len(rows) == len(legs_for(audit["trade_structure"], audit["long_strike"] or 0.0, audit["short_strike"] or 0.0)) else None
    return rows


def live_suggestions(day: str) -> List[Dict[str, Any]]:
    from src.tracking.db_locks import no_lock, readonly_connection

    with no_lock:
        with readonly_connection(config.research_watch_db_path(), timeout=2.0) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, ticker, trade_structure, options_expiration, long_strike, short_strike, target_debit FROM suggested_trades_audit "
                "WHERE trade_type IN ('OPTIONS','INCOME') AND options_expiration >= ? AND status != 'INVALID_GEOMETRY' "
                "AND COALESCE(outcome_notes,'') NOT LIKE 'QUARANTINED%' AND (long_strike > 0 OR short_strike > 0)", (day,)
            ).fetchall()]


def fetch_chain(ticker: str, expiry: str) -> Optional[Dict[str, Any]]:
    from datetime import date as _d

    from src.clients.schwab_client import get_schwab_client

    client = get_schwab_client()
    e = _d.fromisoformat(expiry)
    r = client.get_option_chain(ticker.replace(".", "/"), contract_type=client.Options.ContractType.ALL, from_date=e, to_date=e)
    data = r.json() if r.status_code == 200 else {}
    return data if data.get("status") == "SUCCESS" else None


def capture(now_et: datetime, suggestions: Optional[List[Dict[str, Any]]] = None, fetch=fetch_chain) -> int:
    day = now_et.strftime("%Y-%m-%d")
    rth = now_et.weekday() < 5 and (9, 30) <= (now_et.hour, now_et.minute) < (16, 0)
    after_close = now_et.weekday() < 5 and (now_et.hour, now_et.minute) >= (16, 5)
    suggestions = live_suggestions(day) if suggestions is None else suggestions
    if not suggestions:
        return 0
    stored = 0
    with sqlite3.connect(str(db_path()), timeout=30) as conn:
        _init(conn)
        have = {r[0]: r[1] for r in conn.execute("SELECT audit_id, MAX(CASE WHEN phase='MARK' AND date=? THEN 1 ELSE 0 END) FROM opt_quote GROUP BY audit_id", (day,))}
        cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        for a in suggestions:
            if a["id"] not in have:
                phase = "SUGGESTED"
            elif after_close and not have[a["id"]]:
                phase = "MARK"
            else:
                continue
            key = (a["ticker"], a["options_expiration"])
            if key not in cache:
                try:
                    cache[key] = fetch(*key)
                except Exception as e:
                    logger.debug("option quote fetch %s failed: %s", key, e)
                    cache[key] = None
            if not cache[key]:
                continue
            for r in build_rows(a, cache[key], day, phase, rth):
                r["captured_at"] = now_et.isoformat(timespec="seconds")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO opt_quote VALUES (:audit_id,:ticker,:struct,:date,:phase,:rth,:leg,:cp,:strike,:expiry,"
                    ":spot,:bid,:ask,:mid,:iv,:delta,:oi,:volume,:net_mid,:target_debit,:captured_at)", r)
                stored += cur.rowcount
    return stored


def run_cycle() -> int:
    """Called from the watch loop; throttled so the cheap suggestion query and chain fetches run at most every 5 minutes."""
    global _last_run
    if time.time() - _last_run < MIN_INTERVAL_S:
        return 0
    _last_run = time.time()
    return capture(datetime.now(ZoneInfo("America/New_York")))
