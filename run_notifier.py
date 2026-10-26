"""Separate notifier process. Survives UI restarts and writes to data/notifications.db.

    python run_notifier.py            # loop every 30 s
    python run_notifier.py --once     # one pass, then exit
"""

from __future__ import annotations

import argparse
import logging
import socket
import sqlite3
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src.tracking import notification_center as nc
from src.tracking.notifier_rules import (closed_positions, digest_event, portfolio_events, position_events, rsi2_events,
                                         setup_events, trust_event, watch_alert_events)

logger = logging.getLogger("notifier")
NO_PRICE_CYCLES = 3
LOCK_PORT = 47651  # bound for the process lifetime; a second instance fails to bind and exits


def _emit(ev: dict) -> None:
    if ev.get("group"):
        added = nc.add_ranked(ev["kind"], ev["severity"], ev["title"], ev.get("body", ""), ev.get("ticker"), ev["key"], ev["group"], ev["rank"])
    else:
        added = nc.add(ev["kind"], ev["severity"], ev["title"], ev.get("body", ""), ev.get("ticker"), ev["key"])
    if added:
        logger.info("notified: %s", ev["title"])
    if ev.get("push"):
        from src.tracking.notify import is_quiet_hours, notify

        if not is_quiet_hours():
            notify(ev["push"], ev["title"], ev.get("body", ""), dedupe_key=ev["key"])


def held_tickers() -> "set | None":
    """Tickers you hold (tracked positions plus broker holdings); None when the broker returns nothing."""
    from src.tracking.position_state import list_open
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions

    try:
        held = get_portfolio_positions()
    except Exception as e:
        logger.debug("holdings read failed: %s", e)
        held = None
    if not held:
        return None
    return {str(p.get("underlying_symbol") or p.get("symbol") or "").upper() for p in held} | {t.upper() for t in list_open()}


def check_positions() -> None:
    from src.clients.price_client import get_current_prices_batch
    from src.tracking.position_state import list_open

    positions = list_open()
    try:
        prev = [t for t in (nc.get_meta("open_set") or "").split(",") if t]
        for t in closed_positions(prev, list(positions)):
            _emit({"kind": "CLOSED", "severity": "INFO", "ticker": t, "key": f"CLOSED:{t}:{int(time.time() // 3600)}",
                   "title": f"{t}: position no longer open", "body": "Closed or cancelled. Review it on the Trades page."})
    finally:
        nc.set_meta("open_set", ",".join(sorted(positions)))
    if not positions:
        return
    try:
        prices = get_current_prices_batch(list(positions), context="execution") or {}
    except Exception as e:
        logger.warning("price batch failed: %s", e)
        prices = {}
    for t, rec in positions.items():
        for ev in position_events(t, rec, prices.get(t)):
            _emit(ev)
        miss = int(nc.get_meta(f"noprice:{t}") or 0) + 1 if prices.get(t) is None else 0
        nc.set_meta(f"noprice:{t}", str(miss))
        if miss >= NO_PRICE_CYCLES:  # a single empty batch right after a Schwab token reload is routine
            _emit({"kind": "NO_PRICE", "severity": "WARN", "ticker": t,
                   "key": f"NO_PRICE:{t}:{datetime.now():%Y-%m-%d %H}",
                   "title": f"{t}: no live price", "body": "Stop and target are not being checked right now."})


def check_setups(date: str) -> None:
    from src.ui.services.action_engine import _days_to_earnings
    from src.ui.services.opportunity_service import collect_active_deep_research_opportunities

    opps = collect_active_deep_research_opportunities(3)
    earnings = {str(o.get("ticker") or "").upper(): _days_to_earnings(o.get("ticker"))
                for o in opps if o.get("opportunity_state") == "TAKE_NOW"}
    for ev in setup_events(opps, date, earnings):
        _emit(ev)


def check_catalysts(date: str) -> None:
    from src.tracking import catalyst_ledger as cl

    for ev in cl.catalyst_events(date, cl.held_book(), cl.load_catalysts(date)):
        _emit(ev)


def check_macro(date: str) -> None:
    from src.tracking import macro_calendar as mc
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions

    now = datetime.now(ZoneInfo("America/New_York"))
    for ev in mc.macro_events(now, mc.fetch_events(now), get_portfolio_positions() or []):
        _emit(ev)


def check_spreads(date: str) -> None:
    from datetime import date as _d

    from src.tracking import spread_check as sc
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions

    for ev in sc.spread_events(sc.spread_flags(get_portfolio_positions() or [], _d.fromisoformat(date)), date):
        _emit(ev)


MOVER_EVERY_S = 300


def check_movers(date: str) -> None:
    from src.clients.quote_router import quote_router
    from src.tracking import catalyst_ledger as cl, zone_ledger
    from src.tracking.candle_service import _schwab_daily_candles

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    if now_mt.weekday() >= 5 or not (7, 30) <= (now_mt.hour, now_mt.minute) < (14, 0):
        return
    if time.time() - float(nc.get_meta("movers_at") or 0) < MOVER_EVERY_S:
        return
    nc.set_meta("movers_at", str(time.time()))
    book = cl.held_book()
    plans = zone_ledger.load_plans(date)
    quotes, sigmas = {}, {}
    names = sorted(set(cl.universe(date, book)) | set(cl.MARKET))
    live = quote_router.get_watchlist_quotes_batch(names)
    for t in names:
        try:
            q = live.get(t)
            pct = q.net_percent_change if q else None
            if pct is None or abs(pct) < cl.min_move(t):
                continue
            cached = nc.get_meta(f"sigma:{t}:{date}")
            sg = float(cached) if cached else cl.daily_sigma(_schwab_daily_candles(t, 40))
            if sg:
                nc.set_meta(f"sigma:{t}:{date}", str(sg))
                quotes[t], sigmas[t] = pct, sg
        except Exception as e:
            logger.debug("mover check %s failed: %s", t, e)
    for ev in cl.mover_events(date, quotes, sigmas, book, cl.load_catalysts(date), plans):
        _emit(ev)


AGENT_EVERY_S = 120
UI_PLANS_URL = "http://127.0.0.1:8050/api/trades/suggested"


def check_agent(date: str) -> None:
    import json as _json
    import urllib.request

    from src.clients.llm_client import query_local_llm
    from src.tracking import agent_trader as ag, catalyst_ledger as cl
    from src.tracking.schwab_portfolio_manager import get_portfolio_summary

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    if now_mt.weekday() >= 5 or not (7, 30) <= (now_mt.hour, now_mt.minute) < (14, 0):
        return
    if time.time() - float(nc.get_meta("agent_at") or 0) < AGENT_EVERY_S:
        return
    nc.set_meta("agent_at", str(time.time()))
    with urllib.request.urlopen(UI_PLANS_URL, timeout=20) as r:
        plans = _json.loads(r.read().decode("utf-8")).get("trades") or []
    equity = float((get_portfolio_summary() or {}).get("total_liquidation_value") or 0.0)
    cats = cl.load_catalysts(date)

    def earnings_days(t: str):
        d = [(datetime.strptime(c["date"], "%Y-%m-%d").date() - datetime.strptime(date, "%Y-%m-%d").date()).days
             for c in cats if c["ticker"] == t and c["kind"] == "EARNINGS"]
        d = [x for x in d if x >= 0]
        return min(d) if d else None

    ask = lambda sys_p, usr: query_local_llm(sys_p, usr, json_mode=True, max_tokens=500, disable_thinking=True)
    for ev in ag.manage({p["id"]: p["last_price"] for p in plans if p.get("last_price")}) + ag.propose(plans, equity, earnings_days, ask):
        _emit(ev)


_agent_thread = None


def start_agent(date: str) -> None:
    """The LLM call can wait for a free GPU slot; run it off the notifier loop so the heartbeat never stalls."""
    global _agent_thread
    if _agent_thread is not None and _agent_thread.is_alive():
        return
    import threading

    def run():
        try:
            check_agent(date)
        except Exception as e:
            logger.warning("check agent failed: %s", e)

    _agent_thread = threading.Thread(target=run, daemon=True)
    _agent_thread.start()


def check_digest(date: str) -> None:
    from src.tracking import backend_control as bc
    from src.tracking.risk_snapshot import get_snapshot
    from src.ui.services.opportunity_service import collect_active_deep_research_opportunities

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    if now_mt.weekday() >= 5 or not 7 <= now_mt.hour < 12 or nc.get_meta("digest_date") == date:
        return
    ev = digest_event(date, collect_active_deep_research_opportunities(3), get_snapshot(), bc.alive())
    if ev:
        _emit(ev)
        nc.set_meta("digest_date", date)


def check_trust(date: str) -> None:
    from src.tracking.trust_report import build_trust_report, load_rows

    ev = trust_event(build_trust_report(load_rows()), date)
    if ev:
        _emit(ev)


def check_watch_alerts() -> None:
    from src.tracking.watch_manager import _get_connection, init_watch_db

    init_watch_db()
    last = int(nc.get_meta("watch_alert_id") or 0)
    with _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        if not last:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM watch_alerts").fetchone()
            nc.set_meta("watch_alert_id", str(row[0]))
            return
        rows = [dict(r) for r in conn.execute(
            "SELECT id, ticker, date, trigger_type, message, spot_price FROM watch_alerts WHERE id > ? ORDER BY id", (last,))]
    if rows:
        nc.set_meta("watch_alert_id", str(rows[-1]["id"]))
    for ev in watch_alert_events(rows, held_tickers() if rows else None):
        _emit(ev)


def _db_path():
    from src import config

    return config.research_watch_db_path()


def check_rsi2(date: str) -> None:
    from src.tracking.watch_manager import _get_connection

    with _get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker, date, entry_high, stop, target_1, lane_prior_win, lane_prior_ev FROM suggestions "
            "WHERE source = 'rsi2_scan' AND verdict = 'ENTER' AND gate_status = 'PASS' AND date >= date(?, '-5 days')", (date,))]
    held = (held_tickers() or set()) | {r[0] for r in sqlite3.connect(str(_db_path())).execute(
        "SELECT ticker FROM watch_targets WHERE status = 'IN_TRADE'")} if rows else set()
    rows = [r for r in rows if str(r["ticker"]).upper() not in held]
    for r in rows:
        try:
            from src.clients.earnings_client import resolve_next_earnings_date

            r["next_earnings"] = str(resolve_next_earnings_date(r["ticker"])[0] or "")
        except Exception:
            r["next_earnings"] = ""
    for ev in rsi2_events(rows, datetime.now(ZoneInfo("America/New_York"))):
        _emit(ev)


def _equity_day_pct(live_pct, broker_pct, max_gap: float = 2.0):
    """Live quote % unless it disagrees with the broker's own day % by more than max_gap points."""
    if live_pct is None:
        return broker_pct
    if broker_pct is not None and abs(float(live_pct) - float(broker_pct)) > max_gap:
        return broker_pct
    return live_pct


def check_portfolio(date: str) -> None:
    from src.clients.quote_router import quote_router
    from src.tracking.position_state import list_open
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions, get_portfolio_summary

    summary = get_portfolio_summary() or {}
    held = get_portfolio_positions() or []
    total = float(summary.get("total_value") or summary.get("total_liquidation_value") or 0.0)
    live: dict = {}
    for sym in sorted({(p.get("underlying_symbol") or p.get("symbol")) for p in held if p.get("asset_type") != "OPTION"} | set(list_open())):
        try:
            q = quote_router.get_watchlist_quote(sym)
            live[sym] = q.net_percent_change if q else None
        except Exception:
            live[sym] = None
    rows = []
    legs = {}
    for p in held:
        if p.get("asset_type") == "OPTION":
            legs.setdefault((p.get("underlying_symbol"), p.get("option_expiration"), p.get("option_type")), []).append(p)
    spreads = {k for k, v in legs.items() if {(x.get("quantity") or 0) > 0 for x in v} == {True, False}}
    for (und, exp, typ) in spreads:
        g = legs[(und, exp, typ)]
        qty = min(sum(x["quantity"] for x in g if x["quantity"] > 0), -sum(x["quantity"] for x in g if x["quantity"] < 0))
        strikes = [float(x.get("option_strike") or 0.0) for x in g]
        width = (max(strikes) - min(strikes)) * 100 * qty
        if width > 0:
            net = sum(float(x.get("day_profit_loss") or 0.0) for x in g)
            rows.append({"symbol": f"{und} {exp} {typ} spread", "underlying": und, "asset_type": "SPREAD", "market_value": width, "day_pct": 100 * net / width})
    for p in held:
        is_opt = p.get("asset_type") == "OPTION"
        und = p.get("underlying_symbol") or p.get("symbol")
        if is_opt and (und, p.get("option_expiration"), p.get("option_type")) in spreads:
            continue
        rows.append({"symbol": p.get("symbol"), "underlying": und, "asset_type": p.get("asset_type"),
                     "market_value": p.get("market_value"),
                     "day_pct": p.get("day_profit_loss_pct") if is_opt else _equity_day_pct(live.get(und), p.get("day_profit_loss_pct"))})
    for sym in list_open():
        if sym not in {r["symbol"] for r in rows}:
            rows.append({"symbol": sym, "underlying": sym, "asset_type": "EQUITY", "market_value": 2000.0, "day_pct": live.get(sym)})
    for ev in portfolio_events(rows, summary.get("total_day_pnl_pct"), date, total):
        _emit(ev)


def run_cycle() -> None:
    date = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    for name, fn in (("positions", check_positions), ("portfolio", lambda: check_portfolio(date)),
                     ("levels", check_watch_alerts), ("setups", lambda: check_setups(date)), ("rsi2", lambda: check_rsi2(date)),
                     ("catalysts", lambda: check_catalysts(date)), ("macro", lambda: check_macro(date)), ("spreads", lambda: check_spreads(date)), ("movers", lambda: check_movers(date)), ("agent", lambda: start_agent(date)), ("digest", lambda: check_digest(date)), ("trust", lambda: check_trust(date))):
        t0 = time.time()
        try:
            fn()
        except Exception as e:
            logger.warning("check %s failed: %s", name, e)
        if time.time() - t0 > 20:
            logger.warning("check %s slow: %.0fs", name, time.time() - t0)
        nc.heartbeat()


def acquire_single_instance() -> "socket.socket | None":
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        s.close()
        return None
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--parent", type=int, default=0)
    args = ap.parse_args()
    _lock = acquire_single_instance()
    if _lock is None and not args.once:
        logger.info("notifier already running; exiting")
        sys.exit(0)
    while True:
        if args.parent:
            import psutil
            if not psutil.pid_exists(args.parent):
                logger.info("server gone; notifier exiting")
                break
        run_cycle()
        if args.once:
            break
        time.sleep(args.interval)
