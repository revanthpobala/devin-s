"""
src/tracking/thesis_evaluator.py

Thesis capture + paper-trade evaluation.

Every time a thesis is generated (a watch target is persisted with a real plan), the spot
price at that moment is frozen and a synthetic 100-share paper position is opened at it.
The position is then carried forward on live quotes until the thesis resolves on its own
terms:

  TARGET_HIT  spot reached T1 before the stop  -> thesis was CORRECT
  STOP_HIT    stop breached before T1         -> thesis was WRONG
  EXPIRED     plan aged out without either    -> UNRESOLVED
  OPEN        still live                      -> not yet measurable

This closes the attribution loop the pipeline has been missing: the screen, the thesis and
the outcome all live in one table, so a lane's edge can be measured from the tape instead
of from a conviction score. Paper trades are explicitly labelled as paper - they never
claim to be the user's fills.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.tracking.watch_manager import _get_connection, _db_lock, _now_iso

logger = logging.getLogger(__name__)

# Deterministic paper position size: 100 shares at the spot captured at thesis time.
PAPER_QTY = 100

_CACHE: Dict[str, Any] = {}
_CACHE_T: float = 0.0
_CACHE_TTL: float = 20.0


def init_thesis_paper_db() -> None:
    """Create the paper-trade ledger if it does not exist yet."""
    try:
        with _db_lock:
            with _get_connection() as conn:
                conn.cursor().execute(
                    """
                    CREATE TABLE IF NOT EXISTS thesis_paper_trades (
                        ticker TEXT NOT NULL,
                        thesis_id TEXT NOT NULL,
                        side TEXT DEFAULT 'LONG',
                        source TEXT DEFAULT 'watch_target',
                        created_date TEXT,
                        created_at TEXT,
                        thesis_spot REAL,
                        thesis_entry_low REAL,
                        thesis_entry_high REAL,
                        thesis_stop REAL,
                        thesis_target_1 REAL,
                        thesis_target_2 REAL,
                        invalidation_price REAL,
                        expires_on TEXT,
                        qty INTEGER DEFAULT 100,
                        status TEXT DEFAULT 'OPEN',
                        resolved_at TEXT,
                        exit_spot REAL,
                        pnl_dollars REAL,
                        r_multiple REAL,
                        outcome TEXT,
                        thesis_correct INTEGER,
                        max_favorable_pct REAL DEFAULT 0.0,
                        max_adverse_pct REAL DEFAULT 0.0,
                        bars_seen INTEGER DEFAULT 0,
                        last_spot REAL,
                        last_spot_at TEXT,
                        PRIMARY KEY (ticker, thesis_id)
                    )
                    """
                )
                conn.cursor().execute(
                    "CREATE INDEX IF NOT EXISTS idx_tpt_status ON thesis_paper_trades(status)"
                )
    except Exception as e:
        logger.debug(f"thesis paper db init failed: {e}")


def record_thesis_paper_trade(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Open a paper position for a freshly generated thesis. Idempotent per (ticker, thesis_id):
    re-running the same thesis updates nothing except the last-seen spot.
    """
    try:
        ticker = str(data.get("ticker") or "").strip().upper()
        if not ticker:
            return None

        shares = data.get("shares_plan") or {}
        # A thesis without a stop and a target cannot be scored, so it is not paper-traded.
        stop = shares.get("tactical_stop")
        target = shares.get("target_1")
        if stop is None or target is None:
            return None

        date_str = str(data.get("date") or datetime.now().strftime("%Y-%m-%d"))
        created_date = str(data.get("created_date") or date_str)
        thesis_id = str(data.get("thesis_id") or f"{ticker}_{created_date}")
        side = str(data.get("side") or shares.get("side") or "LONG").upper()
        entry_low = shares.get("entry_zone_low")
        entry_high = shares.get("entry_zone_high")

        spot = _latest_spot(ticker)
        if spot is None or spot <= 0:
            # Fall back to the entry zone midpoint, then give up: the thesis is only
            # meaningful when anchored to a real market price.
            if entry_high:
                try:
                    spot = float(entry_high)
                except Exception:
                    return None
            else:
                return None

        # Degenerate plan (stop == entry, a known data defect in some dossiers) can never
        # produce a meaningful R multiple, so it is rejected at capture rather than being
        # carried as a row that resolves to 0.00R.
        try:
            if abs(float(spot) - float(stop)) <= 0:
                logger.debug(f"[PAPER] {ticker} thesis {thesis_id} skipped: degenerate stop == entry")
                return None
        except Exception:
            return None

        invalidation = data.get("invalidation") or {}
        init_thesis_paper_db()
        with _db_lock:
            with _get_connection() as conn:
                conn.cursor().execute(
                    """
                    INSERT INTO thesis_paper_trades (
                        ticker, thesis_id, side, source, created_date, created_at,
                        thesis_spot, thesis_entry_low, thesis_entry_high,
                        thesis_stop, thesis_target_1, thesis_target_2, invalidation_price,
                        expires_on, qty, status, last_spot, last_spot_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
                    ON CONFLICT(ticker, thesis_id) DO UPDATE SET
                        last_spot = excluded.last_spot,
                        last_spot_at = excluded.last_spot_at
                    """,
                    (
                        ticker, thesis_id, side, str(data.get("source") or "watch_target"),
                        created_date, _now_iso(), float(spot),
                        float(entry_low) if entry_low else None,
                        float(entry_high) if entry_high else None,
                        float(stop), float(target),
                        float(shares.get("target_2")) if shares.get("target_2") else None,
                        float(invalidation.get("price")) if invalidation.get("price") else None,
                        str(data.get("expires_on") or "")[:10],
                        PAPER_QTY, float(spot), _now_iso(),
                    ),
                )
                row = conn.cursor().execute(
                    "SELECT * FROM thesis_paper_trades WHERE ticker = ? AND thesis_id = ?",
                    (ticker, thesis_id),
                ).fetchone()

        if row is not None:
            out = dict(row)
            logger.info(
                f"[PAPER] {ticker} thesis {thesis_id} captured at ${spot:.2f} "
                f"(stop ${float(stop):.2f} → T1 ${float(target):.2f})"
            )
            return out
        return None
    except Exception as e:
        logger.debug(f"record_thesis_paper_trade failed: {e}")
        return None


def _latest_spot(ticker: str) -> Optional[float]:
    """Live spot from the same quote router the desk uses."""
    try:
        from src.clients.price_client import get_current_prices_batch
        px = get_current_prices_batch([ticker], context="paper") or {}
        for k, v in (px.items() if isinstance(px, dict) else []):
            try:
                f = float(v)
            except Exception:
                continue
            if f > 0:
                return f
    except Exception as e:
        logger.debug(f"paper spot fetch failed for {ticker}: {e}")
    return None


def _schwab_quotes(symbols: List[str]) -> Dict[str, float]:
    """Batch spot prices with change %, used to refresh every open paper trade cheaply."""
    if not symbols:
        return {}
    try:
        from src.clients.schwab_client import get_schwab_client
        client = get_schwab_client()
        out: Dict[str, float] = {}
        for i in range(0, len(symbols), 400):
            chunk = symbols[i:i + 400]
            resp = client.get_quotes(chunk)
            if resp.status_code != 200:
                continue
            for sym, val in resp.json().items():
                if not isinstance(val, dict):
                    continue
                q = val.get("quote") or {}
                px = q.get("mark") or q.get("lastPrice") or val.get("mark")
                try:
                    px_f = float(px)
                except Exception:
                    continue
                if px_f > 0:
                    out[sym.replace("/", ".").upper()] = px_f
        return out
    except Exception as e:
        logger.debug(f"paper batch quotes failed: {e}")
        return {}


def _evaluate_with_bars(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Score one paper position by walking the real daily bars with the same lifecycle validator
    the suggestion engine uses, filling at the next open after the thesis date. That way the
    stop/target ambiguity is resolved conservatively (stop first) on the actual tape instead of
    being guessed from today's spot, which silently assumed months of holding.
    """
    try:
        from src.tracking.execution_validator import evaluate_setup_lifecycle_bars, get_bars_since_date

        ticker = row.get("ticker")
        created = str(row.get("created_date") or row.get("created_at") or "")[:10]
        if not ticker or len(created) < 10:
            return None

        try:
            d_obj = datetime.strptime(created, "%Y-%m-%d")
        except Exception:
            return None
        warm_start = (d_obj - timedelta(days=40)).strftime("%Y-%m-%d")

        bars = get_bars_since_date(ticker, warm_start)
        if bars is None or getattr(bars, "empty", True):
            return None

        spot = float(row.get("thesis_spot") or 0.0)
        stop = row.get("thesis_stop")
        t1 = row.get("thesis_target_1")
        if spot <= 0 or not stop or not t1:
            return None
        # Degenerate plan (stop == entry, a known data defect) cannot produce a meaningful R.
        risk = abs(spot - float(stop))
        if risk <= 0:
            return None

        res = evaluate_setup_lifecycle_bars(
            bars=bars,
            setup_date=created,
            side=str(row.get("side") or "LONG").upper(),
            entry_type="NEXT_OPEN",
            entry_low=spot,
            entry_high=spot,
            stop_loss=float(stop),
            target_1=float(t1),
            target_2=float(row.get("thesis_target_2")) if row.get("thesis_target_2") else 0.0,
            live_price=0.0,
            max_holding_bars=MAX_HOLDING_BARS,
            skip_setup_bar=True,
        )
        return res
    except Exception as e:
        logger.debug(f"paper bar evaluation failed for {row.get('ticker')}: {e}")
        return None


MAX_HOLDING_BARS = 10


def refresh_thesis_paper_trades(force: bool = False) -> Dict[str, Any]:
    """
    Resolve every OPEN paper position whose thesis has now played out, using real daily bars.
    Positions whose thesis is still live are left OPEN and simply keep their last-seen spot.
    """
    init_thesis_paper_db()
    try:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.cursor().execute(
                "SELECT * FROM thesis_paper_trades WHERE status = 'OPEN'"
            ).fetchall()]
    except Exception as e:
        logger.debug(f"paper open rows failed: {e}")
        return {"refreshed": 0, "resolved": 0}

    if not rows:
        return {"refreshed": 0, "resolved": 0}

    # Only positions with enough history since the thesis can be scored on bars.
    today = datetime.now(ZoneInfo("America/New_York")).date()
    scoreable = []
    live_only = []
    for r in rows:
        created = str(r.get("created_date") or "")[:10]
        try:
            age = (today - datetime.strptime(created, "%Y-%m-%d").date()).days
        except Exception:
            age = 99
        (scoreable if age >= 1 else live_only).append(r)

    resolved = 0
    evaluated = 0
    for r in scoreable:
        res = _evaluate_with_bars(r)
        if not res:
            continue
        evaluated += 1
        filled = bool(res.get("was_filled"))
        hit_t1 = bool(res.get("hit_t1"))
        hit_stop = bool(res.get("hit_stop"))
        terminal = bool(res.get("is_terminal"))
        if not terminal or (not hit_t1 and not hit_stop):
            # Thesis still live or never filled: keep carrying it, just refresh the last-seen spot.
            _touch_paper_row(r, filled, res)
            continue

        fill_px = float(res.get("fill_price") or r.get("thesis_spot") or 0.0)
        exit_px = float(res.get("exit_price") or fill_px)
        qty = float(r.get("qty") or PAPER_QTY)
        pnl = (exit_px - fill_px) * qty
        if "SHORT" in str(r.get("side") or "LONG").upper():
            pnl = -pnl
        risk = abs(fill_px - float(r.get("thesis_stop"))) * qty
        r_mult = round(pnl / risk, 3) if risk > 0 else None
        outcome = "TARGET_HIT" if hit_t1 else "STOP_HIT"
        correct = 1 if hit_t1 else 0
        with _db_lock:
            with _get_connection() as conn:
                conn.cursor().execute(
                    """
                    UPDATE thesis_paper_trades SET
                        status='RESOLVED', resolved_at=?, exit_spot=?, pnl_dollars=?,
                        r_multiple=?, outcome=?, thesis_correct=?, thesis_spot=?,
                        last_spot=?, last_spot_at=?, max_favorable_pct=?, max_adverse_pct=?,
                        bars_seen=?
                    WHERE ticker=? AND thesis_id=?
                    """,
                    (
                        res.get("exit_date") or _now_iso(), round(exit_px, 4), round(pnl, 2),
                        r_mult, outcome, correct, round(fill_px, 4),
                        exit_px, _now_iso(),
                        round(float(res.get("max_high") or 0.0) and ((float(res["max_high"]) - fill_px) / fill_px * 100.0 if fill_px else 0.0), 3),
                        round(float(res.get("min_low") or 0.0) and ((fill_px - float(res["min_low"])) / fill_px * 100.0 if fill_px else 0.0), 3),
                        int(res.get("bars_held") or 0), r.get("ticker"), r.get("thesis_id"),
                    ),
                )
        resolved += 1

    # Live-only rows still get a fresh spot so OPEN positions display a real price.
    if live_only:
        quotes = _schwab_quotes([r["ticker"] for r in live_only if r.get("ticker")])
        for r in live_only:
            px = quotes.get((r.get("ticker") or "").upper())
            if px is None:
                continue
            with _db_lock:
                with _get_connection() as conn:
                    conn.cursor().execute(
                        "UPDATE thesis_paper_trades SET last_spot=?, last_spot_at=? WHERE ticker=? AND thesis_id=?",
                        (px, _now_iso(), r.get("ticker"), r.get("thesis_id")),
                    )

    return {"refreshed": len(rows), "evaluated": evaluated, "resolved": resolved, "open": len(rows) - resolved}


def _touch_paper_row(r: Dict[str, Any], filled: bool, res: Dict[str, Any]) -> None:
    """Keep a non-terminal paper position current without resolving it."""
    try:
        px = res.get("exit_price") or r.get("last_spot") or r.get("thesis_spot")
        fill_px = float(res.get("fill_price") or r.get("thesis_spot") or 0.0)
        mfe = float(res.get("max_high") or 0.0)
        mae = float(res.get("min_low") or 0.0)
        with _db_lock:
            with _get_connection() as conn:
                conn.cursor().execute(
                    """
                    UPDATE thesis_paper_trades SET
                        last_spot=?, last_spot_at=?, max_favorable_pct=?, max_adverse_pct=?,
                        bars_seen=?, thesis_spot=?
                    WHERE ticker=? AND thesis_id=?
                    """,
                    (
                        px, _now_iso(),
                        round((mfe - fill_px) / fill_px * 100.0, 3) if (fill_px and mfe) else 0.0,
                        round((fill_px - mae) / fill_px * 100.0, 3) if (fill_px and mae) else 0.0,
                        int(res.get("bars_held") or 0),
                        round(fill_px, 4) if filled else r.get("thesis_spot"),
                        r.get("ticker"), r.get("thesis_id"),
                    ),
                )
    except Exception as e:
        logger.debug(f"paper touch failed for {r.get('ticker')}: {e}")


def get_paper_trade_stats(force: bool = False) -> Dict[str, Any]:
    """
    Aggregate the paper ledger. Deliberately separate from the real-trade scorecard: these
    are simulated entries at the thesis spot, reported as their own pool.
    """
    global _CACHE, _CACHE_T
    now_ts = time.time()
    if not force and _CACHE and (now_ts - _CACHE_T) < _CACHE_TTL:
        return _CACHE

    out: Dict[str, Any] = {"available": False}
    try:
        # READ PATH: never create the table here. A GET that reaches this aggregate
        # would otherwise CREATE the ledger mid-request — a GET-purity violation the
        # desk purity test catches by snapshotting sqlite_master around the call.
        # Creation belongs to the writers (record_thesis_paper_trade, refresh).
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.cursor().execute(
                "SELECT * FROM thesis_paper_trades"
            ).fetchall()]
    except Exception as e:
        logger.debug(f"paper stats unavailable (ledger not created yet): {e}")
        return out

    if not rows:
        return out

    resolved = [r for r in rows if r.get("status") == "RESOLVED"]
    opens = [r for r in rows if r.get("status") == "OPEN"]

    def _agg(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
        hits = [r for r in pool if r.get("outcome") == "TARGET_HIT"]
        stops = [r for r in pool if r.get("outcome") == "STOP_HIT"]
        expired = [r for r in pool if r.get("outcome") == "EXPIRED"]
        r_vals = [float(r["r_multiple"]) for r in pool if r.get("r_multiple") is not None]
        pnls = [float(r["pnl_dollars"]) for r in pool if r.get("pnl_dollars") is not None]
        wins = [v for v in r_vals if v > 0]
        losses = [abs(v) for v in r_vals if v < 0]
        return {
            "resolved": len(pool),
            "target_hit": len(hits),
            "stop_hit": len(stops),
            "expired": len(expired),
            "thesis_correct_rate_pct": round((len(hits) / len(pool)) * 100.0, 1) if pool else None,
            "avg_r": round(sum(r_vals) / len(r_vals), 3) if r_vals else None,
            "total_pnl_dollars": round(sum(pnls), 2) if pnls else 0.0,
            "avg_pnl_dollars": round(sum(pnls) / len(pnls), 2) if pnls else None,
            "profit_factor": round(sum(wins) / sum(losses), 2) if sum(losses) > 0 else None,
            "avg_mfe_pct": round(sum(float(r.get("max_favorable_pct") or 0) for r in pool) / len(pool), 2) if pool else None,
            "avg_mae_pct": round(sum(float(r.get("max_adverse_pct") or 0) for r in pool) / len(pool), 2) if pool else None,
        }

    by_side = {"LONG": [], "SHORT": []}
    for r in resolved:
        by_side.setdefault(str(r.get("side") or "LONG").upper(), []).append(r)

    out = {
        "available": True,
        "paper_qty": PAPER_QTY,
        "open": len(opens),
        "open_symbols": [r.get("ticker") for r in opens][:12],
        "aggregate": _agg(resolved),
        "by_side": {k: _agg(v) for k, v in by_side.items() if v},
        "recent": [
            {
                "ticker": r.get("ticker"),
                "thesis_id": r.get("thesis_id"),
                "side": r.get("side"),
                "thesis_spot": r.get("thesis_spot"),
                "exit_spot": r.get("exit_spot"),
                "outcome": r.get("outcome"),
                "r_multiple": r.get("r_multiple"),
                "pnl_dollars": r.get("pnl_dollars"),
                "thesis_correct": r.get("thesis_correct"),
                "created_date": r.get("created_date"),
                "resolved_at": (r.get("resolved_at") or "")[:10],
            }
            for r in sorted(resolved, key=lambda x: str(x.get("resolved_at") or ""), reverse=True)[:10]
        ],
    }
    _CACHE, _CACHE_T = out, time.time()
    return out


def backfill_paper_trades(limit: int = 400) -> Dict[str, Any]:
    """
    Seed the paper ledger from the existing watch_targets history so the metric is useful
    immediately instead of only for theses generated from now on.
    """
    try:
        with _get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.cursor().execute(
                """
                SELECT ticker, date, created_date, thesis_id, side, entry_zone_low, entry_zone_high,
                       tactical_stop, target_1, target_2, invalidation_price, expires_on, last_price
                FROM watch_targets
                WHERE tactical_stop IS NOT NULL AND target_1 IS NOT NULL
                ORDER BY date DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()]
    except Exception as e:
        logger.debug(f"paper backfill read failed: {e}")
        return {"seeded": 0, "skipped": 0, "exists": 0}

    seeded = skipped = exists = 0
    for r in rows:
        spot = r.get("last_price") or r.get("entry_zone_high") or r.get("entry_zone_low")
        if not spot:
            skipped += 1
            continue
        payload = {
            "ticker": r.get("ticker"),
            "date": r.get("date"),
            "created_date": r.get("created_date"),
            "thesis_id": r.get("thesis_id"),
            "side": r.get("side"),
            "expires_on": r.get("expires_on"),
            "shares_plan": {
                "entry_zone_low": r.get("entry_zone_low"),
                "entry_zone_high": r.get("entry_zone_high"),
                "tactical_stop": r.get("tactical_stop"),
                "target_1": r.get("target_1"),
                "target_2": r.get("target_2"),
            },
            "invalidation": {"price": r.get("invalidation_price")},
            "source": "backfill",
        }
        before = _count_rows()
        res = record_thesis_paper_trade(payload)
        after = _count_rows()
        if after > before:
            seeded += 1
        elif res is None:
            skipped += 1
        else:
            exists += 1
    return {"seeded": seeded, "skipped": skipped, "exists": exists}


def _count_rows() -> int:
    try:
        with _get_connection() as conn:
            return int(conn.cursor().execute("SELECT COUNT(*) FROM thesis_paper_trades").fetchone()[0])
    except Exception:
        return 0
