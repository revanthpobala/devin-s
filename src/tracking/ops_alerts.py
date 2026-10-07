"""
OPS tier: the machine telling you it is broken.

Every check here exists because the corresponding failure was silent:
  * Schwab refresh tokens have a 7-day hard expiry. Nothing alerted on it, so an expired token
    looked identical to a working one until a positions call 400'd mid-session.
  * A wedged llama-server still holds port 8000 and still passes a connect() check, so triage
    failures were indistinguishable from "no setups today".
  * The ingester stopping during market hours produces no alerts and no error -- it just goes
    quiet, which is indistinguishable from a slow tape.
  * A stale scrape looks exactly like a fresh one until you notice the date on it.
  * A core name sitting untriaged in `missing_symbols` is invisible in a list of 81 inbox rows.

Each check returns (is_broken, detail). They are deliberately independent and individually
testable, so one failing probe cannot mask the others.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src import config
from src.tracking.notify import notify_ops

logger = logging.getLogger(__name__)

LLM_HEALTH_URL = "http://127.0.0.1:8000/health"
LLM_DOWN_GRACE_MINUTES = 10.0

SCHWAB_TOKEN_WARN_HOURS = 24.0

# Names that must never sit untriaged. These are the liquid, high-signal names; if one is in the
# queue with no triage decision, the pipeline skipped it and nobody noticed.
CORE_SYMBOLS = ("SPY", "QQQ", "AMZN", "WMT", "HOOD", "CRM")

# Window a suggestion has to fall inside to count as "queued". Matches the desk's own working
# window, so the coverage check and the board agree on what is in play.
CORE_COVERAGE_WINDOW_DAYS = 21

# Synthetic tickers that exist only in tests and fixtures. They are not coverage gaps.
_TEST_TICKER_MARKERS = ("GOODTICKER", "BADTICKER", "ACME", "TEST", "ZZTEST", "FOO", "BAR", "DUMMY", "MOCK", "SAMPLE")

# How stale a daily artifact may be before it is called out. One session, per the spec.
STALE_ARTIFACT_SESSIONS = 1


def _is_test_ticker(ticker: str) -> bool:
    """Fixture tickers are not coverage gaps. Shared with src.ui.routes.desk."""
    t = (ticker or "").upper().strip()
    if not t:
        return True
    return any(m in t for m in _TEST_TICKER_MARKERS)


# Public alias: the desk route imports this so there is one definition of a test ticker.
is_test_ticker = _is_test_ticker


# --- individual checks ---------------------------------------------------------------------------

def check_schwab_token() -> Tuple[bool, str]:
    """True when the Schwab OAuth refresh token expires within the warning window."""
    try:
        from src.clients.schwab_client import get_schwab_token_status
        status = get_schwab_token_status()
    except Exception as e:
        return True, f"Schwab token status unreadable: {e}"

    hours = status.get("hours_remaining")
    if hours is None:
        if not status.get("valid", False):
            return True, "Schwab refresh token is EXPIRED or unreadable"
        return False, f"valid, {status.get('expires_at')}"
    if hours < 0:
        return True, f"Schwab refresh token EXPIRED {abs(hours):.1f}h ago — run `python setup_schwab.py`"
    if hours < SCHWAB_TOKEN_WARN_HOURS:
        return True, (
            f"Schwab refresh token expires in {hours:.1f}h "
            f"({datetime.now():%Y-%m-%d %H:%M}). Re-auth: `python setup_schwab.py`"
        )
    return False, f"expires in {hours:.1f}h"


def check_llm_server() -> Tuple[bool, str]:
    """True when the local llama-server has been unreachable for longer than the grace period.

    "Longer than the grace" is measured against a persisted first-failed timestamp: a single failed
    probe during a model swap is not an incident, ten minutes of failure is.
    """
    state_path = config.BASE_DIR / "data" / "llm_health_state.json"
    reachable = False
    detail = ""
    try:
        import requests

        r = requests.get(LLM_HEALTH_URL, timeout=5.0)
        reachable = r.status_code == 200
        detail = f"HTTP {r.status_code}"
    except Exception as e:
        detail = str(e)[:120]

    now = datetime.now().timestamp()
    if reachable:
        try:
            if state_path.exists():
                state_path.unlink()
        except Exception:
            pass
        return False, f"healthy ({detail})"

    first_failed = now
    try:
        if state_path.exists():
            first_failed = json.loads(state_path.read_text(encoding="utf-8")).get("first_failed_ts", now)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"first_failed_ts": first_failed, "detail": detail}),
            encoding="utf-8",
        )
    except Exception:
        pass

    down_minutes = (now - first_failed) / 60.0
    if down_minutes >= LLM_DOWN_GRACE_MINUTES:
        return True, (
            f"LLM server on :8000 unreachable for {down_minutes:.0f} min ({detail}). "
            f"Deep research and triage are down."
        )
    return False, f"unreachable {down_minutes:.0f}/{LLM_DOWN_GRACE_MINUTES:.0f} min, within grace"


def check_ingester_running() -> Tuple[bool, str]:
    """True when the alert ingester is absent during market hours."""
    now = _market_time()
    if not _in_market_hours(now):
        return False, "outside market hours, ingester not expected"

    alive = False
    try:
        import psutil
    except ImportError:
        # Without psutil we cannot prove it is down, and a false OPS page is worse than silence.
        return False, "psutil unavailable; ingester state unverified"

    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "main.py" in cmd and "--loop" in cmd:
                alive = True
                break
    except Exception as e:
        return False, f"process scan failed: {e}"

    if alive:
        return False, "running"
    return True, (
        "Email alert ingester (main.py --loop) is NOT running during market hours. "
        "TradingView alerts are not being polled."
    )


def check_daily_artifacts_fresh() -> Tuple[bool, str]:
    """True when the newest scrape or screener artifact predates the previous session."""
    raw_dir = config.BASE_DIR / "data" / "raw"
    if not raw_dir.exists():
        return True, f"no data/raw directory at {raw_dir}"
    today = datetime.now().date()
    # A session directory dated in the future is a fixture, not an artifact. data/raw currently
    # carries one (2029-01-01), and letting it through would make this check permanently green.
    dates = sorted(
        (d.name for d in raw_dir.glob("202*")
         if d.is_dir() and (parsed := _parse_date(d.name)) is not None and parsed.date() <= today),
        reverse=True,
    )
    if not dates:
        return True, "no dated session artifacts under data/raw (only fixtures?)"
    newest = dates[0]
    newest_dt = _parse_date(newest)
    if newest_dt is None:
        return True, f"unparseable artifact date {newest!r}"
    age_sessions = _sessions_since(newest_dt)
    if age_sessions > STALE_ARTIFACT_SESSIONS:
        return True, (
            f"newest scrape/screener artifact is {newest} ({age_sessions} sessions old). "
            f"Desk and screener are serving stale data."
        )
    return False, f"newest artifact {newest}"


def core_coverage_gaps() -> Tuple[List[str], List[str]]:
    """(genuine gaps, core symbols that are actually covered).

    A gap is a core name with a suggestion inside the working window that has NEVER received an
    llm_decision. Deliberately not derived from the desk's `missing_symbols`: that list is
    inbox-derived, so it flags every core name absent from TODAY's alerts. Measured on the live
    data, all six core names had 6-54 triaged alerts and live watch_targets while the derived list
    called all six gaps.
    """
    from src.tracking.alert_db import _get_connection as _alert_conn
    import src.tracking.watch_manager as wm

    cutoff = _now_minus_days(CORE_COVERAGE_WINDOW_DAYS).strftime("%Y-%m-%d")
    with wm._get_connection() as sconn:
        recent = {
            (r[0] or "").upper()
            for r in sconn.execute(
                "SELECT DISTINCT ticker FROM suggestions WHERE date >= ?", (cutoff,)
            ).fetchall()
        }
    triaged: set = set()
    with _alert_conn() as aconn:
        for r in aconn.execute(
            "SELECT DISTINCT UPPER(symbol) FROM alerts "
            "WHERE llm_decision IS NOT NULL AND llm_decision != ''"
        ).fetchall():
            if r[0]:
                triaged.add(r[0])
    gaps = [s for s in CORE_SYMBOLS if s in recent and s not in triaged]
    covered = [s for s in CORE_SYMBOLS if s not in gaps]
    return gaps, covered


def check_core_symbol_coverage(missing_symbols: Optional[List[str]] = None) -> Tuple[bool, str]:
    """True when a core name is queued but has never been triaged. Test tickers are not gaps.

    An OPS tier that cries wolf on its first run gets ignored, and then it misses the real gap.
    """
    fixture_rows = sorted(s for s in (missing_symbols or []) if _is_test_ticker(s))
    suffix = f" ({len(fixture_rows)} test row(s) ignored)" if fixture_rows else ""

    try:
        gaps, covered = core_coverage_gaps()
    except Exception as e:
        logger.debug("ops: coverage probe failed (%s)", e)
        return False, f"coverage unverified ({e}){suffix}"

    if gaps:
        return True, (
            f"core symbol(s) queued but never triaged: {', '.join(gaps)}. "
            f"Alert coverage has a hole."
        )
    return False, f"all {len(covered)} core symbols have a triage decision{suffix}"


def _now_minus_days(days: int):
    from datetime import timedelta
    return datetime.now() - timedelta(days=days)


# --- helpers ---------------------------------------------------------------------------------------

def _parse_date(name: str) -> Optional[datetime]:
    try:
        return datetime.strptime(name, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _sessions_since(then: datetime) -> int:
    """Weekday sessions elapsed since a date. 1 = yesterday's session still counts as current."""
    cursor = then.date()
    today = datetime.now().date()
    count = 0
    while cursor < today:
        cursor = _next_weekday(cursor)
        if cursor <= today:
            count += 1
        if count > 60:
            break
    return count


def _next_weekday(d):
    from datetime import timedelta
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt = nxt + timedelta(days=1)
    return nxt


def _market_time() -> datetime:
    from datetime import timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Denver"))
    except Exception:
        return datetime.now(timezone.utc) - timedelta(hours=6)


def _in_market_hours(now: Optional[datetime] = None) -> bool:
    now = now or _market_time()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    start = config.MARKET_OPEN_HOUR * 60 + config.MARKET_OPEN_MINUTE
    end = config.MARKET_CLOSE_HOUR * 60 + config.MARKET_CLOSE_MINUTE
    return start <= mins <= end


def _read_missing_symbols() -> List[str]:
    """Symbols sitting in the desk queue with no triage decision."""
    try:
        from src.ui.routes.desk import get_today
        return list(get_today().get("missing_symbols") or [])
    except Exception as e:
        logger.debug("ops: could not read missing_symbols: %s", e)
        return []


# --- runner -------------------------------------------------------------------------------------

def purge_test_rows(dry_run: bool = True) -> Dict[str, Any]:
    """Remove fixture tickers from the suggestions ledger, watch targets and rejected plans.

    GOODTICKER sat in `suggestions` AND twice in `rejected_plans`, which is what put it in the
    desk's missing_symbols list next to SPY and QQQ. The desk now excludes test tickers from its
    counts, so this is housekeeping rather than a correctness fix.

    NOT called automatically. Row deletion is irreversible and AGENTS.md requires explicit
    permission, so this is a one-shot maintenance action the operator invokes:

        python -c "from src.tracking.ops_alerts import purge_test_rows; print(purge_test_rows(dry_run=True))"
        python -c "from src.tracking.ops_alerts import purge_test_rows; print(purge_test_rows(dry_run=False))"
    """
    found: Dict[str, List[str]] = {}
    deleted: Dict[str, int] = {}
    try:
        from src.tracking.watch_manager import _get_connection, _db_lock
    except Exception as e:
        return {"error": f"watch db unavailable: {e}"}

    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            for table in ("suggestions", "watch_targets", "rejected_plans"):
                try:
                    rows = cur.execute(f"SELECT DISTINCT ticker FROM {table}").fetchall()
                except Exception:
                    found[table] = ["<table unavailable>"]
                    continue
                names = [r[0] for r in rows if r[0] and _is_test_ticker(r[0])]
                found[table] = sorted(names)
                if dry_run or not names:
                    continue
                removed = 0
                for n in names:
                    cur.execute(f"DELETE FROM {table} WHERE UPPER(ticker) = ?", (n.upper(),))
                    removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                deleted[table] = removed
            if not dry_run:
                conn.commit()

    result: Dict[str, Any] = {
        "found": found,
        "dry_run": dry_run,
        "deleted_rows": deleted,
        "total_deleted": sum(deleted.values()),
    }
    if dry_run:
        result["next_step"] = "re-run with dry_run=False to delete"
    return result


def check_local_research_coverage(grace_minutes: int = 20) -> Tuple[bool, str]:
    """True when a forward alert is sitting without a local research verdict.

    The requirement is that every alert gets a local research pass. That was true by luck, not by
    construction: the enrichment worker caught a failure, logged it and moved on, leaving the row
    NULL with no retry and nothing to notice it. This is the check that makes the requirement
    observable.

    `grace_minutes` exists because an alert that arrived thirty seconds ago has not been graded
    yet -- that is not a failure, it is the queue working. Only alerts older than the grace window
    count as missed.
    """
    try:
        from datetime import timedelta

        from src.tracking.alert_db import _get_connection, get_eastern_date_str, get_eastern_now

        today = get_eastern_date_str()
        cutoff = (get_eastern_now() - timedelta(minutes=grace_minutes)).strftime("%Y-%m-%d %H:%M:%S")

        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN llm_decision IS NULL OR TRIM(llm_decision) = ''
                                THEN 1 ELSE 0 END) AS missing,
                       SUM(CASE WHEN COALESCE(routing_stage, '') = 'ARCHIVED'
                                THEN 1 ELSE 0 END) AS archived
                FROM alerts
                WHERE date = ?
                  AND COALESCE(routing_stage, '') != 'ARCHIVED'
                  AND (timestamp IS NULL OR timestamp <= ?)
                """,
                (today, cutoff),
            ).fetchone()
    except Exception as e:
        logger.debug("ops: research-coverage probe failed (%s)", e)
        return False, f"coverage unverified ({e})"

    total = row["total"] or 0
    missing = row["missing"] or 0
    if missing:
        pct = (total - missing) / total * 100 if total else 0.0
        return True, (
            f"{missing} of {total} alert(s) for {today} have NO local research "
            f"({pct:.1f}% covered, older than a {grace_minutes}min grace window). "
            f"The enrichment worker retries {_ENRICH_RETRY_ATTEMPTS}x; these exhausted it."
        )
    if total:
        return False, f"all {total} forward alert(s) today have a local research verdict"
    return False, "no forward alerts yet today"


# Mirrors main.py; the message names the retry budget so the alert is self-explanatory.
_ENRICH_RETRY_ATTEMPTS = 3


def run_ops_checks(
    missing_symbols: Optional[List[str]] = None,
    notify: bool = True,
) -> Dict[str, Any]:
    """Run every OPS check and push one alert per distinct failure.

    Returns the full report regardless of whether anything was sent, so the UI can render it.
    """
    checks: List[Tuple[str, Any]] = [
        ("schwab_token", check_schwab_token),
        ("llm_server", check_llm_server),
        ("daily_artifacts", check_daily_artifacts_fresh),
        ("ingester", check_ingester_running),
        ("core_coverage", lambda: check_core_symbol_coverage(missing_symbols)),
        ("local_research_coverage", check_local_research_coverage),
    ]

    results: Dict[str, Any] = {}
    broken: List[Tuple[str, str]] = []
    for name, fn in checks:
        try:
            is_broken, detail = fn()
        except Exception as e:
            is_broken, detail = True, f"check raised: {e}"
        results[name] = {"broken": bool(is_broken), "detail": detail}
        if is_broken:
            broken.append((name, detail))

    results["ok"] = not broken
    results["broken_count"] = len(broken)

    if broken and notify:
        body = "\n".join(f"- **{n}**: {d}" for n, d in broken)
        today = datetime.now().strftime("%Y-%m-%d")
        notify_ops(
            f"{len(broken)} OPS check(s) failing",
            body,
            dedupe_key=f"OPS:{today}:" + ",".join(sorted(n for n, _ in broken)),
        )

    return results