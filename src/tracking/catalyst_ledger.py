"""Catalyst calendar and mover postmortem.

Scheduled catalysts (earnings, ex-dividend) are knowable in advance; their timing is the only part of a dramatic
move we can see coming. Measured on full_v4_av2 (mover_anatomy.py, 4,168 +15% five-bar moves, 414 names): 82% were
multi-day runs, not one gap, and the best price-state flag caught 6% of them at 0.69% precision. So price patterns
do not predict movers. What this module does instead, all deterministic:

  calendar   yfinance dates for held names, names with a short call, open positions, watch targets and zone plans.
  events     earnings T-5 and T-1 on held names; WARN when any catalyst falls before a short call's expiry
             (the call caps the gap) and when ex-dividend precedes expiry with the call in the money.
  movers     after the close, every tracked name with a >=10% five-bar move gets a row: gap or run, was it tracked
             before the window, did it have a plan zone, did the calendar know a catalyst. Over time these rows
             measure what we actually miss and why.
Nothing here predicts direction.
"""

from __future__ import annotations

import concurrent.futures
import logging
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from src import config

logger = logging.getLogger(__name__)

MOVE_PCT = 10.0
WINDOW = 5
EARNINGS_ALERT_DAYS = (5, 1)


def db_path():
    return config.BASE_DIR / "data" / "catalyst_ledger.db"


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS catalyst (ticker TEXT, kind TEXT, date TEXT, first_seen TEXT, last_seen TEXT,"
                 " PRIMARY KEY (ticker, kind, date))")
    conn.execute("CREATE TABLE IF NOT EXISTS mover (ticker TEXT, date TEXT, dir TEXT, move_pct REAL, gap_pct REAL, gap_driven INTEGER,"
                 " tracked_before INTEGER, catalyst TEXT, logged_at TEXT, PRIMARY KEY (ticker, date, dir))")


def held_book() -> Dict[str, Any]:
    """Held equity underlyings and open short calls from the synced Schwab positions."""
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions

    eq, calls = {}, []
    for p in get_portfolio_positions() or []:
        und = (p.get("underlying_symbol") or p.get("symbol") or "").upper()
        if p.get("asset_type") == "EQUITY" and (p.get("quantity") or 0) > 0:
            eq[und] = float(p.get("current_price") or 0.0)
        elif p.get("asset_type") == "OPTION" and (p.get("quantity") or 0) < 0 and p.get("option_type") == "CALL":
            calls.append({"ticker": und, "strike": float(p.get("option_strike") or 0.0), "expiry": p.get("option_expiration"),
                          "qty": int(-(p.get("quantity") or 0))})
    return {"equity": eq, "short_calls": calls}


def universe(today: str, book: Optional[Dict[str, Any]] = None) -> List[str]:
    from src.tracking import zone_ledger
    from src.tracking.position_state import list_open
    from src.tracking.watch_manager import get_active_watch_targets

    book = book or held_book()
    names = set(book["equity"]) | {c["ticker"] for c in book["short_calls"]} | set(list_open())
    names |= {str(t["ticker"]).upper() for t in get_active_watch_targets() if t.get("ticker")}
    names |= set(zone_ledger.load_plans(today))
    try:
        from src.tracking import thesis_ledger

        names |= set(thesis_ledger.tickers())
    except Exception as e:
        logger.debug("thesis ledger universe failed: %s", e)
    return sorted(n for n in names if n and n.replace(".", "").isalnum())


def _fetch_calendar(ticker: str) -> List[Dict[str, str]]:
    import yfinance as yf

    cal = yf.Ticker(ticker).calendar
    out = []
    if isinstance(cal, dict):
        for kind, key in (("EARNINGS", "Earnings Date"), ("EX_DIV", "Ex-Dividend Date")):
            v = cal.get(key)
            for d in (v if isinstance(v, (list, tuple)) else [v]):
                if hasattr(d, "isoformat"):
                    out.append({"kind": kind, "date": d.isoformat()[:10]})
    return out


def refresh_calendar(today: str, tickers: Iterable[str], fetch=_fetch_calendar) -> int:
    tickers = list(tickers)
    stored = 0

    def one(t: str):
        try:
            return t, fetch(t)
        except Exception as e:
            logger.debug("calendar %s failed: %s", t, e)
            return t, []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, tickers))
    with sqlite3.connect(str(db_path()), timeout=30) as conn:
        _init(conn)
        for t, items in results:
            for it in items:
                if it["date"] < today:
                    continue
                conn.execute("INSERT INTO catalyst VALUES (?,?,?,?,?) ON CONFLICT(ticker,kind,date) DO UPDATE SET last_seen=excluded.last_seen",
                             (t, it["kind"], it["date"], today, today))
                stored += 1
    return stored


def load_catalysts(today: str) -> List[Dict[str, Any]]:
    if not db_path().exists():
        return []
    with sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT ticker, kind, date FROM catalyst WHERE date >= ? AND last_seen >= ?",
                                              (today, (date.fromisoformat(today) - timedelta(days=10)).isoformat()))]


def catalyst_events(today: str, book: Dict[str, Any], catalysts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    t0 = date.fromisoformat(today)
    for c in catalysts:
        days = (date.fromisoformat(c["date"]) - t0).days
        t = c["ticker"]
        if c["kind"] == "EARNINGS" and t in book["equity"] and days in EARNINGS_ALERT_DAYS:
            out.append({"kind": "CATALYST", "severity": "WARN" if days == 1 else "INFO", "ticker": t,
                        "key": f"CAT:{t}:EARNINGS:{c['date']}:T{days}",
                        "title": f"{t}: earnings in {days} day{'s' if days != 1 else ''} ({c['date']})",
                        "body": f"You hold {t}. Earnings are the one scheduled event that moves a stock in one gap. Direction is not predictable; "
                                "decide now whether to hold through it, trim, or leave any short call uncovered by it."})
    for sc in book["short_calls"]:
        for c in catalysts:
            if c["ticker"] != sc["ticker"] or not sc["expiry"] or not (today <= c["date"] <= sc["expiry"]):
                continue
            spot = book["equity"].get(sc["ticker"], 0.0)
            if c["kind"] == "EX_DIV" and not (spot and spot > sc["strike"]):
                continue
            what = ("earnings" if c["kind"] == "EARNINGS" else "ex-dividend")
            tail = ("A gap through the strike hands the stock over at the strike; your upside past it is gone." if c["kind"] == "EARNINGS"
                    else f"Call is in the money (spot ${spot:.2f}): early assignment risk the day before ex-dividend.")
            out.append({"kind": "CATALYST", "severity": "WARN", "ticker": sc["ticker"],
                        "key": f"CAT:{sc['ticker']}:{c['kind']}:{c['date']}:CC{sc['strike']:g}:{sc['expiry']}",
                        "title": f"{sc['ticker']}: {what} {c['date']} is before your ${sc['strike']:g} call expires {sc['expiry']}",
                        "body": tail})
    return out


def classify_move(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """Last WINDOW bars vs the close before them. None unless the move is MOVE_PCT or more."""
    if len(bars) < WINDOW + 1:
        return None
    w = bars[-(WINDOW + 1):]
    ret = 100 * (w[-1]["close"] / w[0]["close"] - 1)
    if abs(ret) < MOVE_PCT:
        return None
    up = ret > 0
    gaps = [100 * (w[i]["open"] / w[i - 1]["close"] - 1) for i in range(1, len(w))]
    gap = max(gaps) if up else min(gaps)
    return {"dir": "UP" if up else "DOWN", "move_pct": round(ret, 2), "gap_pct": round(gap, 2),
            "gap_driven": int(abs(gap) >= 0.5 * abs(ret)), "start": w[1]["date"], "end": w[-1]["date"]}


MOVER_SIGMAS = (1.5, 3.0)
MOVER_MIN_PCT = 3.0
MARKET = ('SPY', 'QQQ', 'IWM')
MARKET_MIN_PCT = 1.5


def min_move(t: str) -> float:
    return MARKET_MIN_PCT if t in MARKET else MOVER_MIN_PCT


def daily_sigma(bars: List[Dict[str, float]], n: int = 20) -> Optional[float]:
    """Typical daily move in percent: stdev of the last n close-to-close returns."""
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < n + 1:
        return None
    r = [100 * (closes[i] / closes[i - 1] - 1) for i in range(len(closes) - n, len(closes))]
    m = sum(r) / n
    return (sum((x - m) ** 2 for x in r) / (n - 1)) ** 0.5


def mover_events(today: str, quotes: Dict[str, float], sigmas: Dict[str, float], book: Dict[str, Any],
                 catalysts: List[Dict[str, Any]], plans: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Live drastic-move alerts: today's move at least MOVER_SIGMAS[0] times the name's normal daily move and
    MOVER_MIN_PCT. Information, never ACT: no price state predicts movers (82% are multi-day runs)."""
    out = []
    for t, pct in quotes.items():
        sg = sigmas.get(t)
        if pct is None or not sg or abs(pct) < min_move(t) or abs(pct) / sg < MOVER_SIGMAS[0]:
            continue
        mult = abs(pct) / sg
        tier = sum(mult >= m for m in MOVER_SIGMAS)
        held = t in book["equity"]
        calls = [c for c in book["short_calls"] if c["ticker"] == t]
        known = sorted({c["kind"] for c in catalysts if c["ticker"] == t and abs((date.fromisoformat(c["date"]) - date.fromisoformat(today)).days) <= 1})
        lines = []
        if t in MARKET:
            lines.append("MARKET move: check the Risk tab. Index options, short premium and beta-heavy holdings move with it.")
        elif calls:
            spot = book["equity"].get(t, 0.0)
            c0 = min(calls, key=lambda c: c["strike"])
            lines.append(f"HELD with a short ${c0['strike']:g} call ({c0['expiry']}): " +
                         (f"{pct:+.1f}% moves spot toward/through the strike, review roll or assignment." if pct > 0 else
                          "a drop cheapens the call; shares carry the loss, review the stop."))
        elif held:
            lines.append("HELD: review the stop and whether the plan still stands.")
        else:
            lines.append("NOT HELD: not an entry signal, no measured edge on a move. Chase nothing; wait for a zone with R:R 2.0+.")
        lines.append("Catalyst: " + (", ".join(known) + " within a day of today." if known else "none on the calendar, so unexplained (news, sector or a run)."))
        lines.append("Tracked plan: " + ("yes, a zone plan exists." if t in plans else "none."))
        lines.append("UNMEASURED: direction after a move this size is not predictable.")
        out.append({"kind": "MOVER", "severity": "WARN" if (held or calls or t in MARKET) else "INFO", "ticker": t,
                    "key": f"MOVER:{t}:{today}:{'UP' if pct > 0 else 'DOWN'}:{tier}",
                    "title": f"{'MARKET' if t in MARKET else 'REVIEW' if held or calls else 'WATCH'} {t}: {pct:+.1f}% today, {mult:.1f}x its normal daily move",
                    "body": "\n".join(lines)})
    return out


def log_movers(today: str, tickers: Iterable[str], bars_for=None) -> int:
    from src.tracking import zone_ledger
    from src.tracking.candle_service import _schwab_daily_candles

    bars_for = bars_for or (lambda t: _schwab_daily_candles(t, 30))
    plans = zone_ledger.load_plans(today)
    cats: Dict[str, List[Dict[str, Any]]] = {}
    if db_path().exists():
        with sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5) as conn:
            for t, k, d in conn.execute("SELECT ticker, kind, date FROM catalyst"):
                cats.setdefault(t, []).append({"kind": k, "date": d})
    n = 0
    with sqlite3.connect(str(db_path()), timeout=30) as conn:
        _init(conn)
        for t in tickers:
            try:
                m = classify_move(bars_for(t))
            except Exception as e:
                logger.debug("mover bars %s failed: %s", t, e)
                continue
            if not m:
                continue
            tracked = any(p["date"] < m["start"] for p in plans.get(t, []))
            known = sorted({c["kind"] for c in cats.get(t, []) if m["start"] <= c["date"] <= m["end"]})
            cur = conn.execute("INSERT OR IGNORE INTO mover VALUES (?,?,?,?,?,?,?,?,?)",
                               (t, m["end"], m["dir"], m["move_pct"], m["gap_pct"], m["gap_driven"], int(tracked),
                                ",".join(known), datetime.now().isoformat(timespec="seconds")))
            n += cur.rowcount
    return n


def run_daily(today: str) -> Dict[str, int]:
    book = held_book()
    names = universe(today, book)
    return {"names": len(names), "catalysts": refresh_calendar(today, names), "movers": log_movers(today, names)}


def mover_summary() -> Dict[str, Any]:
    if not db_path().exists():
        return {"n": 0}
    with sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM mover")]
    n = len(rows)
    if not n:
        return {"n": 0, "note": "no 10%+ five-bar moves among tracked names yet"}
    pct = lambda k: round(100 * sum(1 for r in rows if k(r)) / n, 1)
    return {"n": n, "up": sum(r["dir"] == "UP" for r in rows), "gap_driven_pct": pct(lambda r: r["gap_driven"]),
            "tracked_before_pct": pct(lambda r: r["tracked_before"]), "catalyst_known_pct": pct(lambda r: r["catalyst"]),
            "note": "tracked names only; moves in names never tracked are not seen. UNMEASURED until n>=200."}
