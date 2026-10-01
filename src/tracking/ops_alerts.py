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

# Synthetic tickers that exist only in tests and fixtures. They are not coverage gaps.
_TEST_TICKER_MARKERS = ("GOODTICKER", "TEST", "ZZTEST", "FOO", "BAR", "DUMMY", "MOCK", "SAMPLE")

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


def check_core_symbol_coverage(missing_symbols: Optional[List[str]] = None) -> Tuple[bool, str]:
    """True when a core name is queued but untriaged. Test tickers are not coverage gaps."""
    if missing_symbols is None:
        missing_symbols = _read_missing_symbols()
    core_missing = [s for s in missing_symbols if (s or "").upper() in CORE_SYMBOLS]
    if core_missing:
        return True, (
            f"core symbol(s) queued but never triaged: {', '.join(sorted(core_missing))}. "
            f"Alert coverage has a hole."
        )
    noise = sorted({s for s in missing_symbols if _is_test_ticker(s)})
    suffix = f" ({len(noise)} test row(s) ignored)" if noise else ""
    return False, f"all core symbols triaged{suffix}"


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
    """Remove fixture tickers from the suggestions ledger and watch targets.

    NOT called automatically. Row deletion is irreversible and AGENTS.md requires explicit
    permission, so this is a one-shot maintenance action the operator invokes:

        python -c "from src.tracking.ops_alerts import purge_test_rows; print(purge_test_rows(dry_run=False))"

    The desk already excludes these tickers from coverage counts, so the purge is cosmetic
    housekeeping rather than a correctness fix.
    """
    found: Dict[str, List[str]] = {"suggestions": [], "watch_targets": []}
    try:
        from src.tracking.watch_manager import _get_connection, _db_lock
    except Exception as e:
        return {"error": f"watch db unavailable: {e}"}

    with _db_lock:
        with _get_connection() as conn:
            cur = conn.cursor()
            for table, column in (("suggestions", "ticker"), ("watch_targets", "ticker")):
                try:
                    rows = cur.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()
                except Exception:
                    continue
                names = [r[0] for r in rows if _is_test_ticker(r[0] or "")]
                found[table] = sorted(n for n in names if n)
                if dry_run or not names:
                    continue
                for n in names:
                    if not n:
                        continue
                    cur.execute(f"DELETE FROM {table} WHERE UPPER({column}) = ?", (n.upper(),))
            if not dry_run:
                conn.commit()

    found["deleted"] = not dry_run
    return found


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
        notify_ops(
            f"{len(broken)} OPS check(s) failing",
            body,
            dedupe_key="OPS:" + ",".join(sorted(n for n, _ in broken)),
        )

    return results