"""Paper thesis ledger: one thesis per seeded name, objective kill criteria, confirmation-driven paper tranches.

Paper only. Conviction is logged, never a trigger (cap_conviction derives it from the verdict). Nothing here has a
measured edge: scores are labelled UNMEASURED until 200 tranches are scored at a horizon. Long side only.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional

from src import config

logger = logging.getLogger(__name__)

CAP = 30
STALE_PER_NIGHT = 6
PAPER_EQUITY = 100_000.0
NAME_CAP_PCT = 0.05
RISK_PCT = 0.01
TRANCHES = 3
MIN_SESSIONS_BETWEEN = 5
UPDATE_FRESH_SESSIONS = 5
EARNINGS_BLOCK_DAYS = 5
CASH_SKIP_KILL = 3
HORIZONS = (5, 21, 63)
SIDE_COST = 0.0005
MIN_SCORED = 200


def db_path():
    return config.BASE_DIR / "data" / "thesis_ledger.db"


def _conn() -> sqlite3.Connection:
    db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS thesis (ticker TEXT PRIMARY KEY, opened TEXT, status TEXT, source TEXT,
            invalidation_px REAL, invalidation_cond TEXT, broken_reason TEXT, broken_date TEXT, exit_px REAL);
        CREATE TABLE IF NOT EXISTS thesis_updates (ticker TEXT, date TEXT, source TEXT, verdict TEXT, conviction REAL,
            rr_at_market REAL, in_zone INTEGER, fade INTEGER, invalidation_px REAL, PRIMARY KEY (ticker, date, source));
        CREATE TABLE IF NOT EXISTS tranches (ticker TEXT, n INTEGER, date TEXT, price REAL, shares REAL, risk_px REAL,
            PRIMARY KEY (ticker, n));
        CREATE TABLE IF NOT EXISTS marks (ticker TEXT, date TEXT, close REAL, spy_close REAL, PRIMARY KEY (ticker, date));
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    return conn


def _rows(sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    if not db_path().exists():
        return []
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def tickers() -> List[str]:
    return [r["ticker"] for r in _rows("SELECT ticker FROM thesis WHERE status = 'ACTIVE'")]


def last_processed() -> Optional[str]:
    r = _rows("SELECT value FROM state WHERE key = 'last_processed'")
    return r[0]["value"] if r else None


def has_active() -> bool:
    return bool(tickers())


def seed(user: Optional[Iterable[str]] = None, book: Optional[Dict[str, Any]] = None, today: Optional[str] = None,
         cap: int = CAP) -> Dict[str, Any]:
    """Held equity names first, then the user's list, up to cap active theses."""
    if book is None:
        from src.tracking.catalyst_ledger import held_book

        book = held_book()
    today = today or datetime.now().strftime("%Y-%m-%d")
    wanted = [(t, "HELD") for t in sorted(book.get("equity", {}))] + [(str(t).strip().upper(), "USER") for t in (user or [])]
    added, skipped = [], []
    with _conn() as conn:
        have = {r["ticker"] for r in conn.execute("SELECT ticker FROM thesis WHERE status = 'ACTIVE'")}
        known = {r["ticker"] for r in conn.execute("SELECT ticker FROM thesis")}
        for t, src in wanted:
            if not t or t in known:
                continue
            if len(have) >= cap:
                skipped.append(t)
                continue
            conn.execute("INSERT INTO thesis (ticker, opened, status, source) VALUES (?,?,?,?)", (t, today, "ACTIVE", src))
            have.add(t)
            known.add(t)
            added.append(t)
    return {"added": added, "skipped_over_cap": skipped, "active": len(have)}


def remove(tickers: Iterable[str]) -> List[str]:
    """Drop USER theses that have no tranches yet. HELD names and anything already tranched stay."""
    gone = []
    with _conn() as conn:
        for t in (str(x).strip().upper() for x in tickers):
            has = conn.execute("SELECT 1 FROM tranches WHERE ticker = ?", (t,)).fetchone()
            cur = conn.execute("DELETE FROM thesis WHERE ticker = ? AND source = 'USER' AND status = 'ACTIVE'", (t,)) if not has else None
            if cur and cur.rowcount:
                for tb in ("thesis_updates", "marks"):
                    conn.execute(f"DELETE FROM {tb} WHERE ticker = ?", (t,))
                conn.execute("DELETE FROM state WHERE key IN (?, ?)", (f"done:{t}", f"queued:{t}"))
                gone.append(t)
    return gone


def record_update(ticker: str, day: str, source: str, verdict: Optional[str], conviction: Optional[float],
                  rr_at_market: Optional[float], in_zone: Optional[int], fade: Optional[int], invalidation_px: Optional[float]) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO thesis_updates VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,date,source) DO UPDATE SET "
            "verdict=excluded.verdict, conviction=excluded.conviction, rr_at_market=excluded.rr_at_market, "
            "in_zone=excluded.in_zone, fade=excluded.fade, invalidation_px=excluded.invalidation_px",
            (ticker, day, source, verdict, conviction, rr_at_market, in_zone, fade, invalidation_px))
        if invalidation_px and invalidation_px > 0:
            conn.execute("UPDATE thesis SET invalidation_px = ?, invalidation_cond = COALESCE(invalidation_cond, 'DAILY_CLOSE_BELOW') "
                         "WHERE ticker = ? AND status = 'ACTIVE'", (float(invalidation_px), ticker))


def fade_active(signal_pack: Optional[float]) -> Optional[int]:
    """Signal Pack bit 2 is inverted: (v//4)%2 == 0 means the fade gate is active."""
    if signal_pack is None:
        return None
    return int((int(signal_pack) // 4) % 2 == 0)


def _plan_doc(ticker: str, day: str) -> Dict[str, Any]:
    safe = ticker.replace(":", "_").upper()
    for p in (config.BASE_DIR / "reports" / day / f"{safe}_watch_levels.json",
              config.BASE_DIR / "data" / "raw" / day / safe / f"{safe}_watch_levels.json"):
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def ingest_research(ticker: str, since: str, suggestions: Optional[List[Dict[str, Any]]] = None) -> int:
    """Turn suggestion rows dated since `since` into thesis_updates. Judge rows win over rule rows for one date."""
    if suggestions is None:
        from src.tracking.suggestions_ledger import ensure_suggestions_schema
        from src.tracking.watch_manager import _get_connection, _db_lock

        with _db_lock:
            with _get_connection() as conn:
                ensure_suggestions_schema(conn)
                conn.row_factory = sqlite3.Row
                suggestions = [dict(r) for r in conn.execute(
                    "SELECT ticker, date, source, verdict, stop, rr_at_market_at_signal, signal_pack, gate_status, entry_low, entry_high, "
                    "spot_at_signal FROM suggestions WHERE ticker = ? AND date >= ? AND source IN ('judge','rule') ORDER BY date",
                    (ticker, since))]
    n = 0
    for s in suggestions:
        if str(s.get("gate_status") or "") == "REJECTED_BY_GATE" and s.get("source") == "judge":
            verdict = "REJECTED"
        else:
            verdict = s.get("verdict")
        day = str(s["date"])[:10]
        doc = _plan_doc(ticker, day) if s.get("source") == "judge" else {}
        inv = None
        if s.get("source") == "judge" and verdict != "REJECTED":
            px = (doc.get("invalidation") or {}).get("price_level") or 0.0
            inv = float(px) if px and float(px) > 0 else (float(s["stop"]) if s.get("stop") else None)
        spot, hi, lo = s.get("spot_at_signal"), s.get("entry_high"), s.get("entry_low")
        in_zone = int(bool(spot and hi and lo and lo <= spot <= hi)) if (hi and lo) else None
        conv = doc.get("conviction")
        record_update(ticker, day, s["source"], verdict, float(conv) if isinstance(conv, (int, float)) else None,
                      s.get("rr_at_market_at_signal"), in_zone, fade_active(s.get("signal_pack")), inv)
        n += 1
    return n


def upsert_marks(ticker: str, bars: List[Dict[str, Any]], spy_bars: List[Dict[str, Any]]) -> int:
    spy = {str(b["date"])[:10]: b["close"] for b in spy_bars}
    n = 0
    with _conn() as conn:
        for b in bars:
            d = str(b["date"])[:10]
            if not b.get("close") or d not in spy:
                continue
            conn.execute("INSERT INTO marks VALUES (?,?,?,?) ON CONFLICT(ticker,date) DO UPDATE SET close=excluded.close, spy_close=excluded.spy_close",
                         (ticker, d, float(b["close"]), float(spy[d])))
            n += 1
    return n


def kill_reason(th: Dict[str, Any], marks: List[Dict[str, Any]], updates: List[Dict[str, Any]], earnings: List[str],
                as_of: str) -> Optional[Dict[str, Any]]:
    """Breach on as_of: close below invalidation (EARNINGS_BREAK when it lands within three days after a report), or
    three consecutive CASH_SKIP verdicts. Only the as_of close is tested, so a later invalidation never kills retroactively."""
    inv = th.get("invalidation_px") or 0
    today = next((m for m in marks if m["date"] == as_of), None)
    if inv > 0 and today and today["close"] < inv:
        d0 = date.fromisoformat(as_of)
        post = any(0 <= (d0 - date.fromisoformat(e)).days <= 3 for e in earnings)
        return {"reason": "EARNINGS_BREAK" if post else "INVALIDATION_BREAK", "date": as_of, "px": today["close"]}
    judged = [u for u in updates if u["source"] == "judge" and u["date"] <= as_of and u.get("verdict")]
    judged.sort(key=lambda u: u["date"])
    if len(judged) >= CASH_SKIP_KILL and all(u["verdict"] == "CASH_SKIP" for u in judged[-CASH_SKIP_KILL:]):
        d = judged[-1]["date"]
        px = next((m["close"] for m in marks if m["date"] == d), None)
        return {"reason": "CASH_SKIP_X3", "date": d, "px": px}
    return None


def target_notional(px: float, inval: float, equity: float = PAPER_EQUITY) -> float:
    if px <= inval or inval <= 0:
        return 0.0
    return min(NAME_CAP_PCT * equity, RISK_PCT * equity * px / (px - inval))


def tranche_decision(th: Dict[str, Any], marks: List[Dict[str, Any]], updates: List[Dict[str, Any]], tranches: List[Dict[str, Any]],
                     earnings: List[str], as_of: str, equity: float = PAPER_EQUITY) -> Optional[Dict[str, Any]]:
    """The next paper tranche to take on as_of, or None. Starter needs no update-freshness; adds need all gates."""
    if th["status"] != "ACTIVE" or len(tranches) >= TRANCHES:
        return None
    inv = th.get("invalidation_px") or 0
    sess = [m for m in marks if m["date"] <= as_of]
    if inv <= 0 or not sess or sess[-1]["date"] != as_of or sess[-1]["close"] <= inv:
        return None
    px = sess[-1]["close"]
    d0 = date.fromisoformat(as_of)
    if any(0 <= (date.fromisoformat(e) - d0).days <= EARNINGS_BLOCK_DAYS for e in earnings):
        return None
    n = len(tranches) + 1
    if n > 1:
        last = max(t["date"] for t in tranches)
        if sum(1 for m in sess if m["date"] > last) < MIN_SESSIONS_BETWEEN:
            return None
        recent = {m["date"] for m in sess[-UPDATE_FRESH_SESSIONS:]}
        fresh = [u for u in updates if u["date"] in recent]
        if not fresh:
            return None
        known = [u for u in fresh if u.get("fade") is not None]
        if not known or max(known, key=lambda u: u["date"])["fade"] != 0:
            return None
        if n == TRANCHES:
            first = min(t["date"] for t in tranches)
            if not any(first < e <= as_of for e in earnings):
                return None
    shares = target_notional(px, inv, equity) / TRANCHES / px
    if shares <= 0:
        return None
    return {"n": n, "date": as_of, "price": px, "shares": shares, "risk_px": inv}


def _earnings_dates(ticker: str) -> List[str]:
    from src.tracking import catalyst_ledger as cl

    if not cl.db_path().exists():
        return []
    with sqlite3.connect(f"file:{cl.db_path()}?mode=ro", uri=True, timeout=5) as conn:
        return [r[0] for r in conn.execute("SELECT date FROM catalyst WHERE ticker = ? AND kind = 'EARNINGS'", (ticker,))]


def process_ticker(ticker: str, as_of: str, earnings: Optional[List[str]] = None, equity: float = PAPER_EQUITY) -> Dict[str, Any]:
    """Replay each stored session after the last processed one up to as_of: kill check, then tranche check. Idempotent."""
    earnings = earnings if earnings is not None else _earnings_dates(ticker)
    out = {"killed": None, "tranches": []}
    with _conn() as conn:
        th = dict(conn.execute("SELECT * FROM thesis WHERE ticker = ?", (ticker,)).fetchone() or {})
        marks = [dict(r) for r in conn.execute("SELECT date, close FROM marks WHERE ticker = ? ORDER BY date", (ticker,))]
        updates = [dict(r) for r in conn.execute("SELECT * FROM thesis_updates WHERE ticker = ? ORDER BY date", (ticker,))]
        tr = [dict(r) for r in conn.execute("SELECT * FROM tranches WHERE ticker = ? ORDER BY n", (ticker,))]
        done = conn.execute("SELECT value FROM state WHERE key = ?", (f"done:{ticker}",)).fetchone()
    if not th or th["status"] != "ACTIVE":
        return out
    start = done[0] if done else th["opened"]
    for m in [m for m in marks if (m["date"] > start if done else m["date"] >= start) and m["date"] <= as_of]:
        d = m["date"]
        th_now = dict(th)
        inv_hist = [u for u in updates if u["date"] <= d and u.get("invalidation_px")]
        th_now["invalidation_px"] = max(inv_hist, key=lambda u: u["date"])["invalidation_px"] if inv_hist else 0
        k = kill_reason(th_now, marks, [u for u in updates if u["date"] <= d], earnings, d)
        if k:
            with _conn() as conn:
                conn.execute("UPDATE thesis SET status='BROKEN', broken_reason=?, broken_date=?, exit_px=? WHERE ticker=?",
                             (k["reason"], k["date"], k["px"], ticker))
                conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"done:{ticker}", d))
            out["killed"] = k
            return out
        t = tranche_decision(th_now, marks, [u for u in updates if u["date"] <= d], tr, earnings, d, equity)
        if t:
            with _conn() as conn:
                conn.execute("INSERT OR IGNORE INTO tranches VALUES (?,?,?,?,?,?)", (ticker, t["n"], t["date"], t["price"], t["shares"], t["risk_px"]))
            tr.append(t)
            out["tranches"].append(t)
    seen = [m["date"] for m in marks if m["date"] <= as_of and m["date"] >= start]
    if seen:
        with _conn() as conn:
            conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"done:{ticker}", seen[-1]))
    return out


def _default_bars(t: str) -> List[Dict[str, Any]]:
    from src.tracking.candle_service import _daily_bars_fallback, _schwab_daily_candles

    return _schwab_daily_candles(t, 130) or _daily_bars_fallback(t, 130)


def stalest(n: int = STALE_PER_NIGHT) -> List[str]:
    rows = _rows("SELECT t.ticker, COALESCE(MAX(u.date), '') AS last, COALESCE(s.value, '') AS queued FROM thesis t "
                 "LEFT JOIN thesis_updates u ON u.ticker = t.ticker LEFT JOIN state s ON s.key = 'queued:' || t.ticker "
                 "WHERE t.status = 'ACTIVE' GROUP BY t.ticker ORDER BY queued, last, t.ticker LIMIT ?", (n,))
    return [r["ticker"] for r in rows]


def run_nightly(as_of: str, bars_for: Callable[[str], List[Dict[str, Any]]] = _default_bars,
                queue: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """Marks from daily bars, research ingest, kill and tranche checks, then queue the stalest names for research."""
    names = tickers()
    if not names:
        return {"names": 0}
    spy = [b for b in bars_for("SPY") if str(b["date"])[:10] <= as_of]
    killed, taken, errors, failed = [], 0, [], 0
    for t in names:
        try:
            bars = [b for b in bars_for(t) if str(b["date"])[:10] <= as_of]
            upsert_marks(t, bars, spy)
            opened = next(r["opened"] for r in _rows("SELECT opened FROM thesis WHERE ticker = ?", (t,)))
            ingest_research(t, opened)
            res = process_ticker(t, as_of)
            if res["killed"]:
                killed.append(t)
            taken += len(res["tranches"])
        except Exception as e:
            errors.append(f"{t}: {e}")
            failed += 1
            logger.warning("thesis ledger %s failed: %s", t, e)
    if queue is None:
        from src.tracking.alert_db import queue_for_research

        def queue(t, _q=queue_for_research):
            return _q(t, source="THESIS_LEDGER", reason="Thesis ledger staggered refresh")
    queued = []
    for t in stalest():
        try:
            if queue(t):
                queued.append(t)
            with _conn() as conn:
                conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"queued:{t}", as_of))
        except Exception as e:
            errors.append(f"queue {t}: {e}")
    if failed < len(names):
        with _conn() as conn:
            conn.execute("INSERT INTO state VALUES ('last_processed',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (as_of,))
    return {"names": len(names), "killed": killed, "tranches": taken, "queued": queued, "errors": errors}


def score(horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    """Per-tranche and per-thesis return minus SPY at h sessions after the fill, 5bps per side."""
    rows = _rows("SELECT * FROM tranches")
    marks: Dict[str, List[Dict[str, Any]]] = {}
    for m in _rows("SELECT ticker, date, close, spy_close FROM marks ORDER BY date"):
        marks.setdefault(m["ticker"], []).append(m)
    out: Dict[str, Any] = {"horizons": {}, "tranches": len(rows)}
    thesis_ex: Dict[int, Dict[str, List[tuple]]] = {h: {} for h in horizons}
    for h in horizons:
        ex = []
        for t in rows:
            ms = marks.get(t["ticker"], [])
            i = next((k for k, m in enumerate(ms) if m["date"] == t["date"]), None)
            if i is None or i + h >= len(ms):
                continue
            a, b = ms[i], ms[i + h]
            r = b["close"] / t["price"] - 1 - 2 * SIDE_COST
            spy = b["spy_close"] / a["spy_close"] - 1
            ex.append(r - spy)
            thesis_ex[h].setdefault(t["ticker"], []).append((t["shares"] * t["price"], r - spy))
        out["horizons"][h] = {
            "n": len(ex), "mean_excess_pct": round(100 * sum(ex) / len(ex), 3) if ex else None,
            "win_pct": round(100 * sum(e > 0 for e in ex) / len(ex), 1) if ex else None,
            "theses": {k: round(100 * sum(w * e for w, e in v) / sum(w for w, _ in v), 3) for k, v in thesis_ex[h].items()},
            "unmeasured": len(ex) < MIN_SCORED}
    return out


def summary() -> Dict[str, Any]:
    th = _rows("SELECT * FROM thesis")
    if not th:
        return {"n": 0, "note": "no theses seeded"}
    sc = score()
    return {"n": len(th), "active": sum(t["status"] == "ACTIVE" for t in th), "broken": sum(t["status"] == "BROKEN" for t in th),
            "tranches": sc["tranches"], "scored": {h: {k: v for k, v in d.items() if k != "theses"} for h, d in sc["horizons"].items()},
            "last_processed": last_processed(),
            "note": f"paper only, UNMEASURED until {MIN_SCORED} tranches are scored at a horizon; conviction is logged, never a trigger."}


def board() -> Dict[str, Any]:
    th = _rows("SELECT * FROM thesis ORDER BY status, ticker")
    tr: Dict[str, List[Dict[str, Any]]] = {}
    for t in _rows("SELECT * FROM tranches ORDER BY n"):
        tr.setdefault(t["ticker"], []).append(t)
    last = {r["ticker"]: r for r in _rows(
        "SELECT u.* FROM thesis_updates u JOIN (SELECT ticker, MAX(date) d FROM thesis_updates GROUP BY ticker) m "
        "ON m.ticker = u.ticker AND m.d = u.date")}
    px = {r["ticker"]: r["close"] for r in _rows(
        "SELECT m.ticker, m.close FROM marks m JOIN (SELECT ticker, MAX(date) d FROM marks GROUP BY ticker) x ON x.ticker = m.ticker AND x.d = m.date")}
    rows = []
    for t in th:
        ts = tr.get(t["ticker"], [])
        sh = sum(x["shares"] for x in ts)
        cost = sum(x["shares"] * x["price"] for x in ts)
        u = last.get(t["ticker"]) or {}
        rows.append({**t, "tranches": len(ts), "paper_shares": round(sh, 2),
                     "paper_pnl_pct": round(100 * (px[t["ticker"]] * sh / cost - 1), 2) if cost and t["ticker"] in px else None,
                     "last_update": u.get("date"), "last_verdict": u.get("verdict"), "close": px.get(t["ticker"])})
    return {"theses": rows, "summary": summary()}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        print(json.dumps(seed(sys.argv[2:])))
    else:
        print(json.dumps(summary(), indent=2))
