"""
Single outbound notification channel for every alert tier.

Before this, the only things that left the machine were Tastytrade's broker-hosted quote alerts
and a console logger. Nothing in `src/tracking/` could tell the phone anything, so "alerts" in the
spec had no transport. This module is that transport:

    notify("ENTRY", "GLW in zone", "...", dedupe_key="ENTRY:GLW:2026-09-30")

Design constraints that are not negotiable:
  * A dedupe key of (tier, ticker, date) is enforced, not optional. Re-running the sweep must not
    re-page; a genuinely new state change carries a different key.
  * Quiet hours outside 07:00-16:30 MT swallow everything except OPS. OPS is the tier that reports
    the machine is broken -- a dead LLM at 3 AM still needs to be actionable in the morning, and
    burying it is how an expired Schwab token goes unnoticed for a day.
  * Nothing is ever sent to an unconfigured channel. An unconfigured channel logs and returns.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sqlite3
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

# --- tiers -------------------------------------------------------------------------------------
# PUSH = interrupts. DIGEST = deferred to the after-close summary. The distinction is the whole
# point: a phone that buzzes 40 times a day for unmeasured setups trains you to ignore the buzzer.
PUSH_TIERS = ("ENTRY", "RISK", "INCOME", "OPS")
DIGEST_TIERS = ("DIGEST",)

# The push window in Mountain time. Pushes are allowed inside it; OPS is exempt by tier, not by
# flag, so a caller cannot accidentally silence the machine-health channel.
PUSH_START_HOUR = int(os.getenv("NOTIFY_QUIET_START_HOUR", "7"))     # 07:00 MT
PUSH_END_HOUR = int(os.getenv("NOTIFY_QUIET_END_HOUR", "16"))       # 16:30 MT
PUSH_END_MINUTE = int(os.getenv("NOTIFY_QUIET_END_MINUTE", "30"))

_lock = threading.RLock()
_DEDUPE_DB: Optional[sqlite3.Connection] = None


def _dedupe_db_path() -> Path:
    return config.BASE_DIR / "data" / "notify_dedupe.db"


def _get_dedupe_db() -> Optional[sqlite3.Connection]:
    """Lazily-opened dedupe ledger. Returns None if the file cannot be created."""
    global _DEDUPE_DB
    with _lock:
        if _DEDUPE_DB is not None:
            return _DEDUPE_DB
        path = _dedupe_db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sent (
                    dedupe_key TEXT PRIMARY KEY,
                    tier TEXT NOT NULL,
                    title TEXT,
                    sent_at TEXT NOT NULL,
                    sent_at_ts REAL NOT NULL,
                    channel TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_sent_ts ON sent(sent_at_ts)")
            conn.commit()
            _DEDUPE_DB = conn
        except Exception as e:
            logger.warning("notify: dedupe ledger unavailable (%s); alerts may repeat", e)
            return None
        return _DEDUPE_DB


def _already_sent(dedupe_key: str) -> bool:
    conn = _get_dedupe_db()
    if conn is None:
        return False
    # The lock must cover the QUERY, not just connection creation. This connection is a single
    # shared object with check_same_thread=False, and the FastAPI threadpool and the alert sweep
    # both reach it. Without the lock, 8 concurrent callers lost 51 of 400 records to
    # "bad parameter or other API misuse" -- silently, because the except below swallows it. That
    # is duplicate phone pushes, which is the one thing this ledger exists to prevent.
    with _lock:
        try:
            cur = conn.execute("SELECT 1 FROM sent WHERE dedupe_key = ?", (dedupe_key,))
            return cur.fetchone() is not None
        except Exception as e:
            logger.warning("notify: dedupe lookup failed (%s)", e)
            return False


def _claim_key(dedupe_key: str, tier: str, title: str) -> bool:
    """Atomically take ownership of a dedupe key. False means another caller already owns it.

    The claim is the INSERT, not a SELECT followed by an INSERT. Read-then-write is a race: two
    threads both read "not sent", both dispatch, and the phone buzzes twice -- which is precisely
    what this ledger exists to prevent. INSERT OR IGNORE makes exactly one caller win.

    Returns True when the ledger is unavailable, so a broken ledger degrades to "may repeat"
    instead of "silently never sends".
    """
    conn = _get_dedupe_db()
    if conn is None:
        return True
    now = datetime.now()
    with _lock:
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO sent (dedupe_key, tier, title, sent_at, sent_at_ts, channel) "
                "VALUES (?, ?, ?, ?, ?, '')",
                (dedupe_key, tier, title[:200], now.isoformat(timespec="seconds"), time.time()),
            )
            conn.commit()
            return cur.rowcount == 1
        except Exception as e:
            logger.warning("notify: dedupe claim failed (%s); alert may repeat", e)
            return True


def _set_channel(dedupe_key: str, channel: str) -> None:
    """Record which channels accepted the alert. Cosmetic; never re-dispatches."""
    conn = _get_dedupe_db()
    if conn is None or not channel:
        return
    with _lock:
        try:
            conn.execute("UPDATE sent SET channel = ? WHERE dedupe_key = ?", (channel, dedupe_key))
            conn.commit()
        except Exception as e:
            logger.warning("notify: channel update failed (%s)", e)


def _release_key(dedupe_key: str) -> None:
    """Give a claim back because nothing was delivered.

    Without this, a misconfigured channel permanently suppressed the alert for the rest of the
    session: a broken NTFY_TOKEN at 09:00 meant that even after it was fixed at 10:00 the dedupe
    ledger said "already sent" for an alert nobody ever received.
    """
    conn = _get_dedupe_db()
    if conn is None:
        return
    with _lock:
        try:
            conn.execute("DELETE FROM sent WHERE dedupe_key = ?", (dedupe_key,))
            conn.commit()
        except Exception as e:
            logger.warning("notify: dedupe release failed (%s)", e)


def _record_sent(dedupe_key: str, tier: str, title: str, channel: str) -> None:
    conn = _get_dedupe_db()
    if conn is None:
        return
    now = datetime.now()
    with _lock:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sent (dedupe_key, tier, title, sent_at, sent_at_ts, channel) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (dedupe_key, tier, title[:200], now.isoformat(timespec="seconds"), time.time(), channel),
            )
            conn.commit()
        except Exception as e:
            logger.warning("notify: dedupe record failed (%s)", e)


def _mountain_now() -> datetime:
    """Wall clock in Mountain Time. ZoneInfo rather than a fixed -6 offset, so the quiet-hours
    boundary lands on 16:30 local all year instead of drifting an hour twice."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Denver")).replace(tzinfo=None)
    except Exception:
        from datetime import timedelta
        return datetime.now() - timedelta(hours=6)


def is_quiet_hours(now: Optional[datetime] = None) -> bool:
    """True outside the 07:00-16:30 MT push window (weekends included).

    16:30 itself is quiet. The push window is [07:00, 16:30) and the digest takes the boundary, so
    the two never both fire on the same minute -- which otherwise meant a 16:30 sweep sent a push
    *and* the close summary.
    """
    now = now or _mountain_now()
    if now.weekday() >= 5:
        return True
    minutes = now.hour * 60 + now.minute
    return minutes < PUSH_START_HOUR * 60 or minutes >= PUSH_END_HOUR * 60 + PUSH_END_MINUTE


def is_digest_time(now: Optional[datetime] = None) -> bool:
    """At or after the 16:30 MT close, when the digest is emitted."""
    now = now or _mountain_now()
    if now.weekday() >= 5:
        return True
    minutes = now.hour * 60 + now.minute
    return minutes >= PUSH_END_HOUR * 60 + PUSH_END_MINUTE


# --- channels -----------------------------------------------------------------------------------

def _ntfy_configured() -> bool:
    return bool(os.getenv("NTFY_TOPIC") or os.getenv("NTFY_URL"))


def _send_ntfy(title: str, body: str, tier: str) -> bool:
    """HTTP POST to ntfy.sh. The phone app subscribes to the topic; no local runtime needed."""
    import requests

    topic = os.getenv("NTFY_TOPIC", "")
    base = os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/")
    url = f"{base}/{topic}" if topic else base
    payload = (
        f"<b>{tier}</b> · {title}\n\n{body}\n\n"
        f"<i>{_mountain_now():%Y-%m-%d %H:%M} MT · measured lanes only</i>"
    )
    token = os.getenv("NTFY_TOKEN", "")
    headers = {"Title": f"[{tier}] {title}"[:100], "Markdown": "yes"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(url, data=payload.encode("utf-8"), headers=headers, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.warning("notify: ntfy POST failed: %s", e)
        return False


def _smtp_configured() -> bool:
    return bool(config.GMAIL_EMAIL and config.GMAIL_APP_PASSWORD)


def _send_email(subject: str, body: str, tier: str) -> bool:
    """Send from the same Gmail account the ingester reads."""
    recipient = os.getenv("NOTIFY_EMAIL_TO") or config.GMAIL_EMAIL
    if not recipient:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_EMAIL
    msg["To"] = recipient
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(config.GMAIL_EMAIL, config.GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:
        logger.warning("notify: SMTP send failed: %s", e)
        return False


def _dispatch(tier: str, title: str, body: str) -> List[str]:
    """Fan out to every configured channel. Returns the names that accepted the message."""
    sent: List[str] = []
    if _ntfy_configured():
        if _send_ntfy(title, body, tier):
            sent.append("ntfy")
    if _smtp_configured():
        if _send_email(f"[{tier}] {title}", body, tier):
            sent.append("smtp")
    if not sent:
        logger.info("notify [%s] %s -- no channel accepted it, logged only\n%s", tier, title, body)
    return sent


# --- public API ---------------------------------------------------------------------------------

def notify(
    tier: str,
    title: str,
    body: str,
    dedupe_key: Optional[str] = None,
    force: bool = False,
) -> bool:
    """Send one notification. Returns True only if a channel actually accepted it this call.

    False means one of: unknown tier, outside the push window, suppressed as a duplicate,
    deferred because the digest is not due yet, or nothing was delivered. Nothing-delivered does
    NOT consume the dedupe key -- a misconfigured channel must not silence the rest of the session.

    tier: ENTRY | RISK | INCOME | OPS (push) or DIGEST (deferred).
    dedupe_key: stable identity for this alert. Defaults to (tier, title, today's date MT), which
        is already correct for the common case of one state per ticker per day.
    force: bypass dedupe and the push window. For tests and for a genuinely new state change.
    """
    tier = (tier or "").upper().strip()
    if tier not in PUSH_TIERS + DIGEST_TIERS:
        logger.warning("notify: unknown tier %r, refusing to send", tier)
        return False

    key = dedupe_key or f"{tier}:{title}:{_mountain_now():%Y-%m-%d}"

    if tier == "DIGEST":
        # The digest is not a push. It is emitted once, after the close, by build_digest().
        if not force and not is_digest_time():
            logger.debug("notify: DIGEST suppressed before the close")
            return False
    elif not force and is_quiet_hours() and tier != "OPS":
        logger.info("notify [%s] %s suppressed (outside the %02d:00-%02d:%02d MT push window)",
                    tier, title, PUSH_START_HOUR, PUSH_END_HOUR, PUSH_END_MINUTE)
        return False

    # Do not CLAIM the key when there is nowhere to deliver. Claiming here permanently suppressed
    # the alert for the rest of the session, so a channel that was misconfigured at 09:00 stayed
    # silent forever even after it was fixed at 10:00.
    claimed = force or _claim_key(key, tier, title)
    if not claimed:
        logger.debug("notify [%s] %s suppressed (duplicate %s)", tier, title, key)
        return False
    if force:
        # Still logged, but without claiming exclusivity: force means "send again regardless".
        _record_sent(key, tier, title, "")

    sent = _dispatch(tier, title, body)
    if sent:
        _set_channel(key, ",".join(sent))
        return True

    # Nothing was delivered, so nothing should be suppressed later.
    if not force:
        _release_key(key)
    return False


def notify_ops(title: str, body: str, dedupe_key: Optional[str] = None, force: bool = False) -> bool:
    """OPS never respects quiet hours. Thin wrapper so the intent is visible at the call site."""
    return notify("OPS", title, body, dedupe_key=dedupe_key, force=force)


def sent_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Recent notifications, newest first. Feeds the UI and makes dedupe auditable."""
    conn = _get_dedupe_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT dedupe_key, tier, title, sent_at, channel FROM sent "
            "ORDER BY sent_at_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except Exception:
        return []
    return [dict(zip(("dedupe_key", "tier", "title", "sent_at", "channel"), r)) for r in rows]


def purge_dedupe(older_than_days: int = 30) -> int:
    """Drop dedupe rows old enough that they can no longer suppress anything."""
    conn = _get_dedupe_db()
    if conn is None:
        return 0
    cutoff = time.time() - (older_than_days * 86400)
    try:
        cur = conn.execute("DELETE FROM sent WHERE sent_at_ts < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    except Exception:
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    notify_ops(
        "notify channel self-test",
        "One-off manual test. If this arrived, ntfy and/or SMTP are wired.",
        dedupe_key="OPS:SELFTEST:manual",
        force=True,
    )