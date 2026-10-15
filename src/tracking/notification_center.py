"""In-app notification store shared by the notifier process (writer) and the UI (reader).

Own SQLite file so a UI restart or crash never loses or blocks notifications. `dedupe_key` is
unique: a state change carries a new key, a re-run of the same sweep does not re-insert.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

SEVERITIES = ("CRITICAL", "WARN", "INFO")
_lock = threading.RLock()


def db_path() -> Path:
    return config.BASE_DIR / "data" / "notifications.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            ticker TEXT,
            title TEXT NOT NULL,
            body TEXT,
            created_at TEXT NOT NULL,
            read_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_notif_created ON notifications(created_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(kind: str, severity: str, title: str, body: str = "", ticker: Optional[str] = None,
        dedupe_key: Optional[str] = None) -> bool:
    """Insert once per dedupe_key. Returns True only when a new row was created."""
    severity = severity if severity in SEVERITIES else "INFO"
    key = dedupe_key or f"{kind}:{ticker or ''}:{title}"
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notifications (dedupe_key, kind, severity, ticker, title, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, kind, severity, (ticker or "").upper() or None, title[:200], body[:2000], _now()),
            )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()


def add_ranked(kind: str, severity: str, title: str, body: str, ticker: Optional[str], dedupe_key: str,
               group: str, rank: int) -> bool:
    """One row per group: a higher rank replaces the stored row (new id, unread), an equal or lower rank is dropped."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"grp:{group}",)).fetchone()
            prev_rank, _, prev_key = (row["value"] if row else "0|").partition("|")
            if int(prev_rank) >= rank:
                return False
            conn.execute("DELETE FROM notifications WHERE dedupe_key = ?", (prev_key,))
            conn.commit()
        finally:
            conn.close()
        if not add(kind, severity, title, body, ticker, dedupe_key):
            return False
        set_meta(f"grp:{group}", f"{rank}|{dedupe_key}")
        return True


def list_recent(limit: int = 100, unread_only: bool = False, before_id: Optional[int] = None) -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            where = (["read_at IS NULL"] if unread_only else []) + (["id < ?"] if before_id else [])
            sql = "SELECT * FROM notifications" + (" WHERE " + " AND ".join(where) if where else "")
            args = ([int(before_id)] if before_id else []) + [int(limit)]
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def list_after(after_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT * FROM notifications WHERE id > ? ORDER BY id LIMIT ?", (int(after_id), int(limit))).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def max_id() -> int:
    with _lock:
        conn = _connect()
        try:
            return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM notifications").fetchone()[0])
        finally:
            conn.close()


def unread_count() -> Dict[str, int]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT severity, COUNT(*) n FROM notifications WHERE read_at IS NULL GROUP BY severity"
            ).fetchall()
            out = {s: 0 for s in SEVERITIES}
            out.update({r["severity"]: r["n"] for r in rows})
            out["total"] = sum(out[s] for s in SEVERITIES)
            return out
        finally:
            conn.close()


def delete(ids: List[int]) -> int:
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(f"DELETE FROM notifications WHERE id IN ({','.join('?' * len(ids))})", ids)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def mark_read(notification_id: Optional[int] = None) -> int:
    """Mark one notification read, or all when no id is given."""
    with _lock:
        conn = _connect()
        try:
            if notification_id is None:
                cur = conn.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (_now(),))
            else:
                cur = conn.execute(
                    "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
                    (_now(), int(notification_id)),
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def set_meta(key: str, value: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
        finally:
            conn.close()


def get_meta(key: str) -> Optional[str]:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()


def heartbeat() -> None:
    set_meta("heartbeat_ts", str(time.time()))


def heartbeat_age_s() -> Optional[float]:
    raw = get_meta("heartbeat_ts")
    try:
        return max(0.0, time.time() - float(raw)) if raw else None
    except ValueError:
        return None
