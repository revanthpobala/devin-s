"""Covered-call postmortem: what you sold, and what each call did after.

Source is your Schwab fills (last 30 days per sync, kept forever once stored). Per call, the option leg's
result per share is `premium - (buyback cost + intrinsic at expiry on what stayed open)`. Positive means
the call beat simply holding the shares; negative is the capped upside you gave away. Strike distance is
in expected-move units from the logged chain IV (blank before the chain log began).

Outcomes: EXPIRED_OTM, CALLED_AWAY, BOUGHT_BACK. A roll is a buyback plus a new sale.
UNMEASURED: the corpus has no options data, so no claim is made below n=MIN_N.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src import config

MIN_N = 30
LOOKBACK_DAYS = 30


def db_path():
    return config.BASE_DIR / "data" / "cc_postmortem.db"


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cc (order_id TEXT, symbol TEXT, underlying TEXT, strike REAL, expiry TEXT, qty INTEGER,"
        " open_date TEXT, premium REAL, spot_open REAL, em_dist REAL, spot_exp REAL, touch_pct REAL, outcome TEXT,"
        " leg_pnl REAL, settled_at TEXT, PRIMARY KEY (order_id, symbol))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cc_close (order_id TEXT, symbol TEXT, qty INTEGER, px REAL, date TEXT,"
        " PRIMARY KEY (order_id, symbol))"
    )


def parse_orders(orders: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """FILLED single-leg call sales (SELL_TO_OPEN) and buybacks (BUY_TO_CLOSE) from Schwab orders."""
    from src.tracking.schwab_portfolio_manager import parse_option_details

    opens, closes = [], []
    for o in orders:
        if o.get("status") != "FILLED":
            continue
        fills: Dict[Any, List[Any]] = {}
        for act in o.get("orderActivityCollection") or []:
            for leg in act.get("executionLegs") or []:
                fills.setdefault(leg.get("legId"), []).append(leg)
        for i, leg in enumerate(o.get("orderLegCollection") or [], 1):
            inst, ins = leg.get("instruction"), leg.get("instrument") or {}
            if ins.get("assetType") != "OPTION" or inst not in ("SELL_TO_OPEN", "BUY_TO_CLOSE"):
                continue
            det = parse_option_details(ins.get("symbol", ""), ins.get("description", ""), ins.get("putCall"))
            if det["option_type"] != "CALL" or not det["strike"] or not det["expiration"]:
                continue
            ex = fills.get(leg.get("legId", i)) or []
            q = sum(float(e.get("quantity") or 0) for e in ex)
            px = (sum(float(e.get("price") or 0) * float(e.get("quantity") or 0) for e in ex) / q) if q else float(o.get("price") or 0)
            qty = int(q or leg.get("quantity") or 0)
            when = (ex[0].get("time") if ex else None) or o.get("closeTime") or o.get("enteredTime") or ""
            row = {"order_id": str(o.get("orderId")), "symbol": ins.get("symbol", "").strip(), "qty": qty,
                   "px": round(px, 4), "date": when[:10]}
            if inst == "SELL_TO_OPEN":
                opens.append({**row, "underlying": (ins.get("underlyingSymbol") or ins.get("symbol", "").split()[0]).upper(),
                              "strike": det["strike"], "expiry": det["expiration"]})
            else:
                closes.append(row)
    return {"opens": opens, "closes": closes}


def allocate_closes(lots: List[Dict[str, Any]], closes: List[Dict[str, Any]]) -> None:
    """FIFO: buybacks close the oldest open lot of the same option. Adds btc_qty and btc_cost to each lot."""
    for lot in lots:
        lot["btc_qty"], lot["btc_cost"] = 0, 0.0
    for c in sorted(closes, key=lambda x: x["date"]):
        left = c["qty"]
        for lot in sorted((l for l in lots if l["symbol"] == c["symbol"] and l["open_date"] <= c["date"]),
                          key=lambda l: l["open_date"]):
            take = min(left, lot["qty"] - lot["btc_qty"])
            if take > 0:
                lot["btc_qty"] += take
                lot["btc_cost"] += take * c["px"]
                left -= take
            if left <= 0:
                break


def settle_lot(lot: Dict[str, Any], spot_exp: Optional[float], today: str) -> Optional[Dict[str, Any]]:
    """Outcome and per-share leg result, or None while the call is still live."""
    rem = lot["qty"] - lot["btc_qty"]
    if rem <= 0:
        return {"outcome": "BOUGHT_BACK", "leg_pnl": round(lot["premium"] - lot["btc_cost"] / lot["qty"], 4)}
    if lot["expiry"] >= today or spot_exp is None:
        return None
    payoff = max(0.0, spot_exp - lot["strike"])
    return {"outcome": "CALLED_AWAY" if payoff > 0 else "EXPIRED_OTM",
            "leg_pnl": round(lot["premium"] - (lot["btc_cost"] + payoff * rem) / lot["qty"], 4)}


def _closes_by_date(sym: str) -> Dict[str, Dict[str, float]]:
    from src.tracking.candle_service import _schwab_daily_candles

    return {b["date"]: b for b in _schwab_daily_candles(sym, 180) if b.get("date")}


def _em_dist(day: str, ticker: str, expiry: str, strike: float, spot: Optional[float]) -> Optional[float]:
    from src.tracking.backend_tasks import chain_db_path

    if not spot or not chain_db_path().exists():
        return None
    with sqlite3.connect(str(chain_db_path()), timeout=10) as conn:
        r = conn.execute("SELECT iv FROM chain WHERE date=? AND ticker=? AND expiry=? AND cp='C' AND iv>0 "
                         "ORDER BY ABS(strike-?) LIMIT 1", (day, ticker, expiry, strike)).fetchone()
    dte = (date.fromisoformat(expiry) - date.fromisoformat(day)).days
    if not r or dte <= 0:
        return None
    return round((strike / spot - 1.0) / (r[0] / 100.0 * math.sqrt(dte / 365.0)), 2)


def sync(today: Optional[str] = None, orders: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Pull recent fills, store new sales and buybacks, settle every call that has finished."""
    today = today or date.today().isoformat()
    if orders is None:
        from src.clients.schwab_client import get_schwab_client

        now = datetime.now(timezone.utc)
        r = get_schwab_client().get_orders_for_all_linked_accounts(
            from_entered_datetime=now - timedelta(days=LOOKBACK_DAYS), to_entered_datetime=now)
        orders = r.json() if r.status_code == 200 else []
    parsed = parse_orders(orders)
    bars: Dict[str, Dict[str, Dict[str, float]]] = {}

    def bars_for(sym: str):
        if sym not in bars:
            bars[sym] = _closes_by_date(sym)
        return bars[sym]

    with sqlite3.connect(str(db_path()), timeout=30) as conn:
        _init(conn)
        for c in parsed["closes"]:
            conn.execute("INSERT OR IGNORE INTO cc_close VALUES (?,?,?,?,?)", (c["order_id"], c["symbol"], c["qty"], c["px"], c["date"]))
        for o in parsed["opens"]:
            if conn.execute("SELECT 1 FROM cc WHERE order_id=? AND symbol=?", (o["order_id"], o["symbol"])).fetchone():
                continue
            spot = (bars_for(o["underlying"]).get(o["date"]) or {}).get("close")
            conn.execute("INSERT INTO cc (order_id,symbol,underlying,strike,expiry,qty,open_date,premium,spot_open,em_dist)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (o["order_id"], o["symbol"], o["underlying"], o["strike"], o["expiry"], o["qty"], o["date"], o["px"],
                          spot, _em_dist(o["date"], o["underlying"], o["expiry"], o["strike"], spot)))
        lots = [dict(zip(("order_id", "symbol", "underlying", "strike", "expiry", "qty", "open_date", "premium"), r))
                for r in conn.execute("SELECT order_id,symbol,underlying,strike,expiry,qty,open_date,premium FROM cc "
                                      "WHERE settled_at IS NULL")]
        allocate_closes(lots, [dict(zip(("symbol", "qty", "px", "date"), r))
                               for r in conn.execute("SELECT symbol,qty,px,date FROM cc_close")])
        settled = 0
        for lot in lots:
            b = bars_for(lot["underlying"])
            res = settle_lot(lot, (b.get(lot["expiry"]) or {}).get("close"), today)
            if not res:
                continue
            end = lot["expiry"]
            hi = [x["high"] for d, x in b.items() if lot["open_date"] <= d <= end]
            touch = round(100 * (max(hi) / lot["strike"] - 1.0), 2) if hi else None
            conn.execute("UPDATE cc SET spot_exp=?, touch_pct=?, outcome=?, leg_pnl=?, settled_at=? WHERE order_id=? AND symbol=?",
                         ((b.get(end) or {}).get("close"), touch, res["outcome"], res["leg_pnl"], today,
                          lot["order_id"], lot["symbol"]))
            settled += 1
    return {"opens": len(parsed["opens"]), "closes": len(parsed["closes"]), "settled": settled}


def summary() -> Dict[str, Any]:
    if not db_path().exists():
        return {"n": 0, "verdict": "no covered calls logged yet"}
    with sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM cc WHERE settled_at IS NOT NULL")]
        live = conn.execute("SELECT COUNT(*) FROM cc WHERE settled_at IS NULL").fetchone()[0]

    def agg(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rs)
        if not n:
            return {"n": 0}
        pct = [100 * r["leg_pnl"] / r["spot_open"] for r in rs if r["spot_open"]]
        return {"n": n,
                "expired_otm_pct": round(100 * sum(r["outcome"] == "EXPIRED_OTM" for r in rs) / n, 1),
                "called_away_pct": round(100 * sum(r["outcome"] == "CALLED_AWAY" for r in rs) / n, 1),
                "bought_back_pct": round(100 * sum(r["outcome"] == "BOUGHT_BACK" for r in rs) / n, 1),
                "mean_leg_pct_of_spot": round(sum(pct) / len(pct), 2) if pct else None,
                "leg_dollars": round(sum(r["leg_pnl"] * 100 * r["qty"] for r in rs), 2)}

    def bucket(r: Dict[str, Any]) -> str:
        d = r["em_dist"]
        return "unknown" if d is None else "<1 EM" if d < 1 else "1-1.5 EM" if d < 1.5 else ">=1.5 EM"

    out = {"all": agg(rows), "open": live,
           "by_strike_distance": {k: agg([r for r in rows if bucket(r) == k]) for k in ("<1 EM", "1-1.5 EM", ">=1.5 EM", "unknown")},
           "recent_called_away": [{k: r[k] for k in ("underlying", "strike", "expiry", "spot_exp", "touch_pct", "leg_pnl")}
                                  for r in rows if r["outcome"] == "CALLED_AWAY"][-5:]}
    out["verdict"] = (f"too few settled calls (n={len(rows)} < {MIN_N}); no claim" if len(rows) < MIN_N
                      else "n sufficient; compare strike-distance buckets, still UNMEASURED vs holding")
    return out
