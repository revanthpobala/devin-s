"""market_events.py — one shared grounded scan of market-moving events per date, plus the
code-computed cross-asset tape that transmission chains are tested against.

``fetch_market_events`` makes ONE grounded call per date, disk-cached under
``data/raw/{date}/_MARKET/`` so every ticker in the run shares it and a re-run spends no quota.

``market_tape`` computes 1-day and 5-day % moves for a fixed cross-asset basket from the
cached ``daily_bars`` (yfinance) — no LLM, citable as the "Market tape (code)" section.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.logic.deep_research.grounded_pack import _TAIL

logger = logging.getLogger(__name__)

EVENTS_TITLE = "Market events (last 24h)"

_EVENTS_QUESTION = (
    "Market-moving events in the last 24 hours as of {date}: elections and policy outcomes, "
    "central banks, regulation and legal actions, geopolitics, and major corporate news. "
    "Also report the latest Asia and Europe index closes and US equity futures. " + _TAIL
)

# Fixed cross-asset basket for the tape; transmission channels proxy off these symbols only.
TAPE: Tuple[str, ...] = ("SPY", "QQQ", "IWM", "SOXX", "SMH", "NVDA", "TSM", "EWJ", "EWY",
                         "EWT", "FXI", "EWG", "TLT", "GLD", "USO", "^VIX")

# Fallback significance bar when a symbol's own 20-day median |1d move| (row["med_move"])
# is unavailable — e.g. a hand-built tape in tests.
TAPE_MIN_MOVE = 0.3


def fetch_market_events(date_str: str, cache_root: Path) -> Dict[str, Any]:
    """One grounded market-events scan per date, shared by every ticker. Returns a
    grounded_pack-shaped item: {topic, title, text, citations, date}. An empty scan is
    NOT cached, so a transient grounding failure does not block the scan all day."""
    from src.clients.google_grounding_client import ask_grounded

    path = Path(cache_root) / "_MARKET" / "market_events.json"
    if path.exists():
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(out, dict) and out.get("topic") == "market_events":
                return out
        except Exception:
            pass
    res: Dict[str, Any] = {}
    try:
        res = ask_grounded(_EVENTS_QUESTION.format(date=date_str)) or {}
    except Exception as e:
        logger.debug(f"market events scan failed: {e}")
    out = {"topic": "market_events", "title": EVENTS_TITLE, "date": str(date_str)[:10],
           "text": str(res.get("text") or ""),
           "citations": [c for c in (res.get("citations") or []) if isinstance(c, dict) and c.get("url")]}
    if out["text"]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"market events cache write failed: {e}")
    return out


def _pct(now: float, then: float) -> Optional[float]:
    return round(100 * (now / then - 1), 2) if then > 0 else None


def _med_move(closes: List[float], n: int = 20) -> Optional[float]:
    """Median absolute 1-day % move over the last n closes — the symbol's own significance bar."""
    import statistics

    moves = [abs(100 * (closes[i] / closes[i - 1] - 1)) for i in range(1, len(closes)) if closes[i - 1] > 0]
    return round(statistics.median(moves[-n:]), 2) if len(moves) >= 5 else None


def market_tape(ticker: str, cache_dir: Path, sector_etf: Optional[str] = None) -> Dict[str, Any]:
    """1-day and 5-day % moves for the TAPE basket plus the ticker and its sector ETF.

    Returns a dict: {"rows": [{"symbol", "d1", "d5", "med_move", "last"}], "extras":
    ticker/sector symbols usable as confirms, "partial": True when as_of is today's
    still-forming bar, "extended": optional extended-hours quote, "as_of": last bar date}."""
    from src.clients.quote_router import get_quote_router
    from datetime import date

    from src.logic.deep_research.quant_facts import bars_settled, daily_bars

    rows: List[Dict[str, Any]] = []
    extras = [s for s in (ticker, sector_etf) if s]
    symbols = list(TAPE) + [s for s in extras if s not in TAPE]
    as_of = None
    for sym in symbols:
        try:
            bars, _ = daily_bars(sym, Path(cache_dir))
        except Exception as e:
            logger.debug(f"tape bars for {sym} failed: {e}")
            bars = []
        closes = [b["close"] for b in bars if b.get("close")]
        row: Dict[str, Any] = {"symbol": sym, "last": closes[-1] if closes else None,
                               "d1": _pct(closes[-1], closes[-2]) if len(closes) >= 2 else None,
                               "d5": _pct(closes[-1], closes[-6]) if len(closes) >= 6 else None,
                               "med_move": _med_move(closes)}
        if bars and not as_of:
            as_of = str(bars[-1].get("time") or "")[:10] or None
        rows.append(row)
    partial = bool(as_of and as_of == date.today().isoformat() and not bars_settled())
    out: Dict[str, Any] = {"rows": rows, "as_of": as_of, "partial": partial, "extras": extras}
    try:
        q = get_quote_router().get_watchlist_quote(ticker)
        if q and q.is_extended_hours and q.last_price > 0:
            trow = next((r for r in rows if r["symbol"] == ticker), None)
            base = (trow or {}).get("last")
            out["extended"] = {"symbol": ticker, "price": round(float(q.last_price), 2),
                               "pct": _pct(float(q.last_price), float(base)) if base else None}
    except Exception as e:
        logger.debug(f"[{ticker}] extended-hours tape quote failed: {e}")
    return out


def tape_section(tape: Dict[str, Any]) -> str:
    """The citable 'Market tape (code)' SOURCES section body."""
    rows = [r for r in (tape or {}).get("rows") or [] if r.get("d1") is not None]
    if not rows:
        return ""
    lines = [f"Cross-asset tape, last-session and 5-session % moves, as of {tape.get('as_of') or 'unknown'}"
             + (" (partial, still forming)" if tape.get("partial") else "") + " (code):"]
    lines += [f"- {r['symbol']}: 1d {r['d1']:+.2f}%"
              + (f" · 5d {r['d5']:+.2f}%" if r.get("d5") is not None else " · 5d —")
              + (f" · last {r['last']:,.2f}" if r.get("last") else "")
              for r in rows]
    ext = tape.get("extended")
    if ext:
        lines.append(f"- {ext['symbol']} extended hours: {ext['price']:,.2f}"
                     + (f" ({ext['pct']:+.2f}% vs last close)" if ext.get("pct") is not None else ""))
    return "\n".join(lines)


def tape_status(confirm: List[Dict[str, Any]], tape: Dict[str, Any]) -> str:
    """CONFIRMED when every confirm symbol's last-session move has the stated sign and is at
    least the symbol's own 20-day median |1d move| (``med_move``); CONTRADICTED when any moved
    the opposite way by that much; else UNTESTED."""
    rows = {str(r.get("symbol") or "").upper(): r for r in (tape or {}).get("rows") or []}
    if not confirm:
        return "UNTESTED"
    ok = True
    for c in confirm:
        sym = str(c.get("symbol") or "").upper()
        mv = str(c.get("move") or "").upper()
        row = rows.get(sym) or {}
        d1 = row.get("d1")
        thr = row.get("med_move") or TAPE_MIN_MOVE
        if d1 is None or mv not in ("UP", "DOWN"):
            return "UNTESTED"
        if (mv == "UP" and d1 <= -thr) or (mv == "DOWN" and d1 >= thr):
            return "CONTRADICTED"
        if (mv == "UP" and d1 < thr) or (mv == "DOWN" and d1 > -thr):
            ok = False
    return "CONFIRMED" if ok else "UNTESTED"
