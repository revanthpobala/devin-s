"""Scheduled jobs run by the detached backend: daily options-chain log, database backups, universe sweep, the
nightly suggestion scorer (forward record) and the counterfactual scorer.

End-of-day jobs are keyed to the last completed session (not the calendar day), so a machine that was off at the
close catches up the next time the backend starts. `python -m src.tracking.backend_tasks --once` runs whatever is
due and exits (Windows Task Scheduler).

The chain log is irreplaceable history (there is no options data in the corpus), so it starts now and runs
once per trading day after the close. Rows are the raw quotes the premium-selling question needs:
implied vol, bid, ask, delta, open interest, volume near the money, 20-50 days out.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.tracking import backend_control as bc

logger = logging.getLogger("backend_tasks")

DTE_MIN, DTE_MAX, BAND = 20, 50, 0.10
ALWAYS = ["SPY", "QQQ"]
MAX_TICKERS = 40
BACKUP_KEEP = 7
SKIP_DB_PREFIXES = ("notifications",)


def chain_db_path():
    return config.BASE_DIR / "data" / "options_chain_log.db"


def _init_chain_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chain (date TEXT, ticker TEXT, spot REAL, expiry TEXT, dte INTEGER, cp TEXT, strike REAL,"
        " bid REAL, ask REAL, iv REAL, delta REAL, oi INTEGER, volume INTEGER, logged_at TEXT,"
        " UNIQUE(date, ticker, expiry, cp, strike))"
    )


def parse_chain(data: Dict[str, Any], day: str, ticker: str) -> List[Dict[str, Any]]:
    """Near-the-money contracts 20-50 days out from a Schwab chain response."""
    spot = float(data.get("underlyingPrice") or 0.0)
    if spot <= 0:
        return []
    rows = []
    for cp, key in (("C", "callExpDateMap"), ("P", "putExpDateMap")):
        for exp_key, strikes in (data.get(key) or {}).items():
            expiry = exp_key.split(":")[0]
            for strike, contracts in strikes.items():
                k = float(strike)
                if abs(k / spot - 1.0) > BAND:
                    continue
                for c in contracts:
                    dte = int(c.get("daysToExpiration") or 0)
                    if not DTE_MIN <= dte <= DTE_MAX:
                        continue
                    iv = c.get("volatility")
                    rows.append({
                        "date": day, "ticker": ticker, "spot": spot, "expiry": expiry, "dte": dte, "cp": cp, "strike": k,
                        "bid": float(c.get("bid") or 0.0), "ask": float(c.get("ask") or 0.0),
                        "iv": float(iv) if iv not in (None, "NaN") else None,
                        "delta": float(c.get("delta")) if c.get("delta") not in (None, "NaN") else None,
                        "oi": int(c.get("openInterest") or 0), "volume": int(c.get("totalVolume") or 0),
                    })
    return rows


def store_rows(rows: Iterable[Dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(str(chain_db_path()), timeout=30) as conn:
        _init_chain_db(conn)
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO chain VALUES (:date,:ticker,:spot,:expiry,:dte,:cp,:strike,:bid,:ask,:iv,:delta,:oi,:volume,:now)",
            [{**r, "now": now} for r in rows],
        )
        return conn.total_changes - before


def fetch_chain(ticker: str) -> Optional[Dict[str, Any]]:
    from src.clients.schwab_client import get_schwab_client

    client = get_schwab_client()
    today = date.today()
    r = client.get_option_chain(
        ticker.replace(".", "/"),
        contract_type=client.Options.ContractType.ALL,
        from_date=today + timedelta(days=DTE_MIN),
        to_date=today + timedelta(days=DTE_MAX),
    )
    if r.status_code != 200:
        logger.warning("chain %s HTTP %s", ticker, r.status_code)
        return None
    data = r.json()
    return data if data.get("status") == "SUCCESS" else None


def chain_universe() -> List[str]:
    from src.tracking.position_state import list_open
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions
    from src.tracking.watch_manager import get_active_watch_targets

    names: List[str] = list(ALWAYS)
    for p in get_portfolio_positions() or []:
        if p.get("asset_type") == "EQUITY":
            names.append(p.get("underlying_symbol") or p.get("symbol"))
    names += list(list_open())
    names += [t["ticker"] for t in get_active_watch_targets()]
    out: List[str] = []
    for n in names:
        n = (n or "").upper().strip()
        if n and n not in out:
            out.append(n)
    return out[:MAX_TICKERS]


def run_chain_log(day: str, tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    tickers = tickers if tickers is not None else chain_universe()
    stored, failed = 0, []
    for t in tickers:
        try:
            data = fetch_chain(t)
            if data is None:
                failed.append(t)
            else:
                stored += store_rows(parse_chain(data, day, t))
        except Exception as e:
            logger.warning("chain %s failed: %s", t, e)
            failed.append(t)
        time.sleep(0.6)
    return {"date": day, "tickers": len(tickers), "rows": stored, "failed": failed}


def backup_databases(day: str, keep: int = BACKUP_KEEP) -> Dict[str, Any]:
    """Consistent copies of every data/*.db (sqlite backup API) plus positions.json, pruned to `keep` days."""
    data_dir = config.BASE_DIR / "data"
    dest = data_dir / "backups" / day
    dest.mkdir(parents=True, exist_ok=True)
    done, failed = [], []
    for db in sorted(data_dir.glob("*.db")):
        if db.name.startswith(SKIP_DB_PREFIXES):
            continue
        try:
            src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
            dst = sqlite3.connect(str(dest / db.name))
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            done.append(db.name)
        except Exception as e:
            failed.append(f"{db.name}: {e}")
    pos = data_dir / "positions.json"
    if pos.exists():
        shutil.copy2(pos, dest / pos.name)
    days = sorted(p for p in (data_dir / "backups").iterdir() if p.is_dir())
    for old in days[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return {"date": day, "databases": len(done), "failed": failed}


def _et_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


SESSION_CLOSE = (16, 10)
MARKET_OPEN = (9, 30)
_spy_cache: Dict[str, Any] = {"ts": 0.0, "dates": None}


def last_session(now_et: datetime, spy_dates: Optional[Iterable[str]] = None) -> str:
    """Date of the last completed session: today after the close on a weekday, else the previous weekday.
    Holidays are handled by clamping to the latest SPY bar date when it is known."""
    d = now_et.date()
    if not (d.weekday() < 5 and (now_et.hour, now_et.minute) >= SESSION_CLOSE):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    s = d.isoformat()
    prior = [x for x in (spy_dates or []) if x <= s]
    return max(prior) if prior else s


def _refresh_spy() -> None:
    dates = None
    try:
        from src.tracking.bars_store import fetch_daily

        dates = [str(b["date"])[:10] for b in fetch_daily(["SPY"], 15).get("SPY", [])] or None
    except Exception as e:
        logger.warning("session lookup failed: %s", e)
    _spy_cache.update(ts=time.time(), dates=dates or _spy_cache["dates"], busy=False)


def _spy_dates(block: bool = False) -> Optional[List[str]]:
    """Cached SPY dates; a stale cache refreshes in the background so the tick loop never waits on the network."""
    if time.time() - _spy_cache["ts"] >= 3600 and not _spy_cache.get("busy"):
        _spy_cache["busy"] = True
        if block or not _spy_cache["ts"]:
            _refresh_spy()
        else:
            threading.Thread(target=_refresh_spy, daemon=True).start()
    return _spy_cache["dates"]


def current_session(now_et: Optional[datetime] = None) -> str:
    now_et = now_et or _et_now()
    return last_session(now_et, _spy_dates())


def due_jobs(now_et: datetime, ctl: Dict[str, Any], session: Optional[str] = None) -> List[str]:
    day = now_et.strftime("%Y-%m-%d")
    session = session or last_session(now_et)
    now_ts = now_et.timestamp()
    jobs = []
    if ctl.get("last_backup_date") != day:
        jobs.append("backup")
    # Chain quotes are live: only valid for the session while the market is closed since it.
    quotes_current = day == session or now_et.weekday() >= 5 or (now_et.hour, now_et.minute) < MARKET_OPEN
    retry = lambda key: now_ts - float(ctl.get(key) or 0) > 3600
    if quotes_current and ctl.get("last_chain_date") != session and retry("last_chain_attempt_ts"):
        jobs.append("chain")
    if retry("last_thesis_attempt_ts"):
        try:
            from src.tracking import thesis_ledger

            if thesis_ledger.has_active() and thesis_ledger.last_processed() != session:
                jobs.append("thesis")
        except Exception as e:
            logger.warning("thesis due check failed: %s", e)
    if ctl.get("last_sweep_date") != session and retry("last_sweep_attempt_ts"):
        jobs.append("sweep")
    sweeping = "sweep" in jobs
    if not sweeping and ctl.get("last_score_date") != session and retry("last_score_attempt_ts"):
        jobs.append("score")
    if (not sweeping and "score" not in jobs and ctl.get("last_counterfactual_date") != session
            and retry("last_counterfactual_attempt_ts")):
        jobs.append("counterfactual")
    return jobs


_running: set = set()


def run_job(job: str, day: str, session: str) -> Any:
    """Runs one job to completion and records its result in the control file."""
    if job == "backup":
        res = backup_databases(day)
        bc.update(last_backup_date=day, last_backup=res)
    elif job == "sweep":
        from src.tracking import universe_sweep

        bc.update(last_sweep_attempt_ts=time.time())
        res = universe_sweep.run()
        bc.update(last_sweep_date=session if res["with_bars"] else None)
    elif job == "score":
        from src.tracking.suggestion_scorer import evaluate_all_suggestions

        bc.update(last_score_attempt_ts=time.time())
        out = evaluate_all_suggestions()
        res = {"scored": out["scored"], "errors": len(out["errors"])}
        bc.update(last_score_date=session, last_score=res)
    elif job == "counterfactual":
        from src.tracking import counterfactual

        bc.update(last_counterfactual_attempt_ts=time.time())
        res = counterfactual.run_all()
        bc.update(last_counterfactual_date=session, last_counterfactual=res)
    elif job == "thesis":
        from src.tracking import thesis_ledger

        bc.update(last_thesis_attempt_ts=time.time())
        res = thesis_ledger.run_nightly(session)
        bc.update(last_thesis=res)
    else:
        bc.update(last_chain_attempt_ts=time.time())
        res = run_chain_log(session)
        try:
            from src.tracking import cc_postmortem

            logger.info("cc postmortem: %s", cc_postmortem.sync(session))
        except Exception as e_cc:
            logger.warning("cc postmortem failed: %s", e_cc)
        try:
            from src.tracking import catalyst_ledger

            logger.info("catalyst ledger: %s", catalyst_ledger.run_daily(session))
        except Exception as e_cat:
            logger.warning("catalyst ledger failed: %s", e_cat)
        ctl = bc.read()
        tries = ctl.get("chain_tries", {}).get(session, 0) + 1
        done = res["rows"] > 0 or tries >= 3
        bc.update(last_chain=res, chain_tries={session: tries}, **({"last_chain_date": session} if done else {}))
    logger.info("%s done: %s", job, res)
    return res


def tick() -> None:
    """Called from the backend loop. Each due job runs once in its own thread and records its result."""
    now = _et_now()
    day = now.strftime("%Y-%m-%d")
    session = current_session(now)
    for job in due_jobs(now, bc.read(), session):
        if job in _running or (job in ("score", "counterfactual") and "sweep" in _running):
            continue
        _running.add(job)

        def _work(job=job):
            try:
                run_job(job, day, session)
            except Exception as e:
                logger.error("%s failed: %s", job, e)
                bc.update(**{f"last_{job}_error": str(e)})
            finally:
                _running.discard(job)

        threading.Thread(target=_work, name=f"task-{job}", daemon=True).start()


def run_due_once() -> Dict[str, Any]:
    """Runs every due job in order (sweep, then score, then counterfactual) and returns. Skips when the backend is alive."""
    if bc.alive():
        return {"skipped": "backend running; it catches up on its own"}
    now = _et_now()
    day, session = now.strftime("%Y-%m-%d"), current_session(now)
    results: Dict[str, Any] = {"session": session}
    for _ in range(4):
        jobs = [j for j in due_jobs(now, bc.read(), session) if j not in results]
        if not jobs:
            break
        for job in jobs:
            try:
                results[job] = run_job(job, day, session)
            except Exception as e:
                logger.error("%s failed: %s", job, e)
                results[job] = {"error": str(e)}
    return results


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run due end-of-day jobs and exit")
    a = ap.parse_args()
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if a.once:
        print(run_due_once())
