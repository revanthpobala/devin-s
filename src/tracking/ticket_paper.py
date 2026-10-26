"""Paper ledger for measured tickets (ZONE_RR / OVERSOLD / CODE20 / RSI2) and scenario plans (SCENARIO_*).

Every measured ticket is recorded at creation with the signal-close spot, then scored nightly on daily
bars with the honest replay contract: enter next open only if stop < open < target_1, planned risk =
signal close - stop, stop-first walk over 21 bars (fill bar = bar 1), gap through stop exits at the
open, 5 bps per side. Open trades carry the latest spot and unrealized R.

Scenario plans rest at their trigger: LIMIT (pullback) fills when low <= trigger at min(trigger, open),
STOP (breakout) fills when high >= trigger at max(trigger, open), only on bars from not_before through exit_by
(the window end). Planned risk = trigger - stop. p_touch_pct / median_touch_sessions are the model's odds at
record time, kept so the touch model can be calibrated against actual fills.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from src.tracking.watch_manager import _db_lock, _get_connection, _now_iso

logger = logging.getLogger(__name__)

FEES_BPS = 5.0
HORIZON = 21

_COLS = {
    "ticker": "TEXT NOT NULL", "date": "TEXT NOT NULL", "lane": "TEXT NOT NULL", "levels_source": "TEXT",
    "entry_type": "TEXT", "signal_close": "REAL", "rr_at_market": "REAL", "stop": "REAL", "target_1": "REAL",
    "limit_price": "REAL", "exit_by": "TEXT", "qty": "INTEGER", "filled_date": "TEXT", "filled_price": "REAL",
    "filled_via": "TEXT", "exit_date": "TEXT", "exit_price": "REAL", "exit_via": "TEXT", "r_multiple": "REAL",
    "max_favorable_pct": "REAL", "max_adverse_pct": "REAL", "bars_held": "INTEGER", "outcome": "TEXT",
    "mature": "INTEGER DEFAULT 0", "last_price": "REAL", "last_price_date": "TEXT", "unrealized_r": "REAL",
    "fees_bps": "REAL", "created_at": "TEXT", "scored_at": "TEXT", "paper_only": "INTEGER DEFAULT 1",
    "not_before": "TEXT", "superseded_on": "TEXT", "zone_low": "REAL", "p_touch_pct": "REAL",
    "median_touch_sessions": "INTEGER", "origin": "TEXT",
}


def _init_schema(conn: sqlite3.Connection) -> None:
    cols = ", ".join(f"{k} {v}" for k, v in _COLS.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS ticket_paper_trades ({cols}, PRIMARY KEY (ticker, date, lane))")
    have = {r[1] for r in conn.execute("PRAGMA table_info(ticket_paper_trades)")}
    for k, v in _COLS.items():
        if k not in have:
            conn.execute(f"ALTER TABLE ticket_paper_trades ADD COLUMN {k} {v.replace('NOT NULL', '')}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tpt_mature ON ticket_paper_trades(mature)")


def _fee(entry: float, exit_px: float) -> float:
    return FEES_BPS / 1e4 * (entry + exit_px)


def fill_ticket(signal_close: float, stop: float, target_1: float, bars: List[Dict[str, Any]],
                entry_type: str = "NEXT_OPEN", limit_price: Optional[float] = None,
                not_before: Optional[str] = None, fill_by: Optional[str] = None) -> Dict[str, Any]:
    """bars[0] is the signal bar, bars[1:] follow. Returns fill/exit fields; mature=0 while still open.
    Resting orders (LIMIT/STOP) may fill on bars from not_before through fill_by (else the first 21 bars)."""
    out: Dict[str, Any] = {"filled_date": None, "filled_price": None, "filled_via": None, "exit_date": None,
                           "exit_price": None, "exit_via": None, "r_multiple": None, "max_favorable_pct": None,
                           "max_adverse_pct": None, "bars_held": 0, "outcome": "PENDING", "mature": 0,
                           "last_price": None, "last_price_date": None, "unrealized_r": None}
    ref = signal_close if entry_type == "NEXT_OPEN" else float(limit_price or signal_close)
    risk = ref - stop
    if risk <= 0 or target_1 <= ref:
        out.update(outcome="INVALID_LEVELS", mature=1)
        return out
    if len(bars) < 2:
        return out
    fill_idx, fill_px = None, None
    if entry_type in ("STOP", "LIMIT"):
        window = [(i, b) for i, b in enumerate(bars[1:], start=1)
                  if (not fill_by or str(b["date"]) <= fill_by) and (fill_by or i <= HORIZON)]
        for i, b in window:
            if not_before and str(b["date"]) < not_before:
                continue
            o = float(b["open"])
            if entry_type == "STOP" and float(b["high"]) >= ref:
                if o >= target_1:
                    out.update(outcome="GAP_ABOVE_TARGET", mature=1)
                    return out
                fill_idx, fill_px, out["filled_via"] = i, max(ref, o), "STOP"
                break
            if entry_type == "LIMIT" and float(b["low"]) <= ref:
                fill_idx, fill_px, out["filled_via"] = i, min(ref, o), "LIMIT"
                break
        if fill_idx is None:
            last = len(bars) - 1
            if (fill_by and str(bars[-1]["date"]) > fill_by) or (not fill_by and last > HORIZON):
                out.update(outcome="NOT_FILLED", mature=1)
            return out
    elif entry_type == "NEXT_OPEN":
        o = float(bars[1]["open"])
        if o <= stop:
            out.update(outcome="GAP_BELOW_STOP", mature=1)
            return out
        if o >= target_1:
            out.update(outcome="GAP_ABOVE_TARGET", mature=1)
            return out
        fill_idx, fill_px, out["filled_via"] = 1, o, "NEXT_OPEN"
    else:
        out.update(outcome="INVALID_LEVELS", mature=1)
        return out
    out["filled_date"], out["filled_price"] = str(bars[fill_idx]["date"]), round(fill_px, 4)
    walk = bars[fill_idx:fill_idx + HORIZON]
    hi, lo = fill_px, fill_px
    for j, b in enumerate(walk):
        o, h, l = float(b["open"]), float(b["high"]), float(b["low"])
        hi, lo = max(hi, h), min(lo, l)
        exit_px = via = None
        if l <= stop:
            exit_px, via = (min(o, stop) if j > 0 else min(fill_px, stop)), "STOP"
        elif h >= target_1:
            exit_px, via = (max(o, target_1) if j > 0 else target_1), "TARGET"
        if via:
            out.update(exit_date=str(b["date"]), exit_price=round(exit_px, 4), exit_via=via, bars_held=j + 1,
                       outcome="STOP_HIT" if via == "STOP" else "TARGET_HIT", mature=1)
            break
    else:
        last = walk[-1]
        if len(walk) >= HORIZON:
            out.update(exit_date=str(last["date"]), exit_price=round(float(last["close"]), 4), exit_via="HORIZON",
                       bars_held=len(walk), outcome="EXPIRED", mature=1)
        else:
            px = float(last["close"])
            out.update(outcome="OPEN", bars_held=len(walk), last_price=px, last_price_date=str(last["date"]),
                       unrealized_r=round((px - fill_px - _fee(fill_px, px)) / risk, 4))
    out["max_favorable_pct"] = round((hi - fill_px) / fill_px * 100, 3)
    out["max_adverse_pct"] = round((fill_px - lo) / fill_px * 100, 3)
    if out["exit_price"] is not None:
        out["r_multiple"] = round((out["exit_price"] - fill_px - _fee(fill_px, out["exit_price"])) / risk, 4)
        out["last_price"], out["last_price_date"] = out["exit_price"], out["exit_date"]
    return out


def record_ticket(ticket: Dict[str, Any]) -> bool:
    """Insert a PENDING row for a measured ticket. False when (ticker, date, lane) already exists."""
    row = {"ticker": ticket["ticker"], "date": ticket["date"], "lane": ticket["lane"],
           "levels_source": ticket.get("levels_source"), "entry_type": ticket.get("entry_method", "NEXT_OPEN"),
           "signal_close": ticket.get("signal_close"), "rr_at_market": ticket.get("rr_at_market"),
           "stop": ticket["stop"], "target_1": ticket["target_1"], "limit_price": ticket.get("limit_price"),
           "exit_by": ticket.get("exit_by"), "qty": ticket.get("qty"), "outcome": "PENDING", "mature": 0,
           "last_price": ticket.get("signal_close"), "last_price_date": ticket["date"], "fees_bps": FEES_BPS,
           "created_at": _now_iso(), "not_before": ticket.get("not_before"), "zone_low": ticket.get("zone_low"),
           "p_touch_pct": ticket.get("p_touch_pct"), "median_touch_sessions": ticket.get("median_touch_sessions"),
           "origin": ticket.get("origin")}
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        cur = conn.execute(f"INSERT OR IGNORE INTO ticket_paper_trades ({', '.join(row)}) "
                           f"VALUES ({', '.join('?' * len(row))})", list(row.values()))
        conn.commit()
        return cur.rowcount > 0


def has_open(ticker: str, lane: str) -> bool:
    """True when an unmatured paper trade exists for this ticker and lane (one position per lane)."""
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        return conn.execute("SELECT 1 FROM ticket_paper_trades WHERE ticker = ? AND lane = ? AND mature = 0",
                            (ticker.upper(), lane)).fetchone() is not None


def has_position(ticker: str, lane: str) -> bool:
    """True when a filled, unmatured paper trade exists for this ticker and lane."""
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        return conn.execute("SELECT 1 FROM ticket_paper_trades WHERE ticker = ? AND lane = ? AND mature = 0 "
                            "AND filled_date IS NOT NULL", (ticker.upper(), lane)).fetchone() is not None


def cancel_unfilled(ticker: str, lane: str, today: str) -> None:
    """A newer plan replaces an unfilled resting one: same-day rows are deleted; older ones may still fill through
    today (scoring is nightly, so a fill earlier today is not on file yet), then close as SUPERSEDED."""
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        q = "WHERE ticker = ? AND lane = ? AND mature = 0 AND filled_date IS NULL"
        conn.execute(f"DELETE FROM ticket_paper_trades {q} AND date = ?", (ticker.upper(), lane, today))
        conn.execute(f"UPDATE ticket_paper_trades SET superseded_on = ?, exit_by = ? {q} "
                     f"AND superseded_on IS NULL AND (exit_by IS NULL OR exit_by > ?)",
                     (today, today, ticker.upper(), lane, today))
        conn.commit()


def pending_tickers() -> List[str]:
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        return [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ticket_paper_trades WHERE mature = 0")]


def score_pending(bars_by_ticker: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """Re-walk every unmatured row on completed daily bars; updates fill, exit, spot and R."""
    counts = {"scored": 0, "matured": 0, "no_bars": 0}
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM ticket_paper_trades WHERE mature = 0")]
        for r in rows:
            et = r["entry_type"] or "NEXT_OPEN"
            allb = bars_by_ticker.get(r["ticker"], [])
            if et in ("LIMIT", "STOP"):
                # Resting plans are recorded at research time (weekends, intraday): anchor on the last bar at or before it.
                bars = [b for b in allb if str(b["date"]) <= r["date"]][-1:] + [b for b in allb if str(b["date"]) > r["date"]]
            else:
                bars = [b for b in allb if str(b["date"]) >= r["date"]]
                if bars and str(bars[0]["date"]) != r["date"]:
                    bars = []
            if not bars:
                counts["no_bars"] += 1
                continue
            res = fill_ticket(r["signal_close"], r["stop"], r["target_1"], bars, et, r["limit_price"],
                              r.get("not_before"), r["exit_by"] if et in ("LIMIT", "STOP") else None)
            if res["outcome"] == "NOT_FILLED" and r.get("superseded_on"):
                res["outcome"] = "SUPERSEDED"
            if res["last_price"] is None:
                res["last_price"], res["last_price_date"] = float(bars[-1]["close"]), str(bars[-1]["date"])
            res["scored_at"] = _now_iso()
            conn.execute(f"UPDATE ticket_paper_trades SET {', '.join(f'{k} = ?' for k in res)} "
                         f"WHERE ticker = ? AND date = ? AND lane = ?",
                         [*res.values(), r["ticker"], r["date"], r["lane"]])
            counts["scored"] += 1
            counts["matured"] += res["mature"]
        conn.commit()
    return counts


def lane_summary() -> Dict[str, Dict[str, Any]]:
    """Per lane: signals, fills, open, mature filled n, mean/median R, win %."""
    with _db_lock, _get_connection() as conn:
        _init_schema(conn)
        rows = conn.execute("SELECT lane, outcome, r_multiple, mature, filled_price FROM ticket_paper_trades").fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for lane, outcome, r, mature, filled in rows:
        s = out.setdefault(lane, {"signals": 0, "filled": 0, "open": 0, "r": [], "outcomes": {}})
        s["signals"] += 1
        s["filled"] += filled is not None
        s["open"] += outcome == "OPEN"
        s["outcomes"][outcome] = s["outcomes"].get(outcome, 0) + 1
        if mature and r is not None:
            s["r"].append(r)
    for s in out.values():
        rs = sorted(s.pop("r"))
        s["n_scored"] = len(rs)
        s["mean_r"] = round(sum(rs) / len(rs), 4) if rs else None
        s["median_r"] = rs[len(rs) // 2] if rs else None
        s["win_pct"] = round(100 * sum(x > 0 for x in rs) / len(rs), 1) if rs else None
    return out
