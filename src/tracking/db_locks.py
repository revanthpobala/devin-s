"""
Database Lock Instrumentation & Read-Only Concurrency Engine.
Provides non-blocking read-only SQLite WAL connections and telemetry for write locks.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("db_locks")


class InstrumentedLock:
    """
    Instrumented threading RLock that tracks hold durations, lock holders,
    longest open write transactions, and logs warnings on lock hogging.
    """

    def __init__(self, name: str = "db_lock", warn_threshold_s: float = 0.5):
        self._lock = threading.RLock()
        self.name = name
        self.warn_threshold_s = warn_threshold_s
        self.holder_info: Optional[str] = None
        self.acquire_time: Optional[float] = None
        self.longest_hold_s: float = 0.0
        self.longest_holder: Optional[str] = None
        self.total_acquisitions: int = 0
        self._depth: int = 0

    def acquire(self, blocking: bool = True, timeout: float = -1.0) -> bool:
        start_wait = time.time()
        acquired = self._lock.acquire(blocking, timeout)
        wait_duration = time.time() - start_wait
        if wait_duration > 0.2:
            logger.debug(f"⏳ [{self.name}] Waited {wait_duration:.3f}s to acquire lock.")

        if acquired:
            self._depth += 1
            if self._depth == 1:
                self.acquire_time = time.time()
                self.total_acquisitions += 1
                try:
                    stack = inspect.stack()
                    caller = stack[1] if len(stack) > 1 else None
                    if caller:
                        file_name = Path(caller.filename).name
                        self.holder_info = f"{file_name}:{caller.lineno} in {caller.function}()"
                    else:
                        self.holder_info = "unknown"
                except Exception:
                    self.holder_info = "unresolved_stack"
        return acquired

    def release(self):
        if self._depth == 1 and self.acquire_time is not None:
            duration = time.time() - self.acquire_time
            if duration > self.longest_hold_s:
                self.longest_hold_s = duration
                self.longest_holder = self.holder_info

            if duration >= self.warn_threshold_s:
                logger.warning(
                    f"⚠️ [{self.name}] Lock held for {duration:.3f}s by {self.holder_info}! "
                    f"(Longest historical hold: {self.longest_hold_s:.3f}s by {self.longest_holder})"
                )
            self.acquire_time = None
            self.holder_info = None

        if self._depth > 0:
            self._depth -= 1
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def get_stats(self) -> Dict[str, Any]:
        is_held = self.acquire_time is not None
        held_duration = round(time.time() - self.acquire_time, 3) if is_held else 0.0
        return {
            "name": self.name,
            "is_locked": is_held,
            "current_holder": self.holder_info if is_held else None,
            "current_held_s": held_duration,
            "longest_hold_s": round(self.longest_hold_s, 3),
            "longest_holder": self.longest_holder,
            "total_acquisitions": self.total_acquisitions,
        }


# Global instrumented lock singletons
watch_db_lock = InstrumentedLock("watch_db_lock", warn_threshold_s=0.5)
alert_db_lock = InstrumentedLock("alert_db_lock", warn_threshold_s=0.5)


def get_all_lock_stats() -> Dict[str, Any]:
    """Return live lock telemetry for monitoring and health endpoints."""
    return {
        "watch_db_lock": watch_db_lock.get_stats(),
        "alert_db_lock": alert_db_lock.get_stats(),
    }


def get_readonly_connection(db_path: Path | str, timeout: float = 2.0) -> sqlite3.Connection:
    """
    Open a dedicated read-only SQLite WAL connection that never waits on writers.
    Uses query_only=ON and short busy_timeout (2s) so readers never hang.
    """
    path_obj = Path(db_path).resolve()
    if not path_obj.exists():
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        # Touch file if doesn't exist so uri ro doesn't fail
        conn = sqlite3.connect(str(path_obj), timeout=timeout, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.close()

    uri_path = f"file:{path_obj.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri_path, uri=True, timeout=timeout, check_same_thread=False)
    except Exception:
        # Fallback to standard connect if URI mode fails
        conn = sqlite3.connect(str(path_obj), timeout=timeout, check_same_thread=False)

    # A read-only connection cannot change journal mode: PRAGMA journal_mode=WAL on a
    # mode=ro handle raises "attempt to write a readonly database" and took the whole
    # desk read path down with it. The writer already set WAL; the reader only needs
    # its short busy timeout and query_only.
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)};")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA query_only=ON;")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


from contextlib import contextmanager, nullcontext

@contextmanager
def readonly_connection(db_path: Path | str, timeout: float = 2.0):
    """
    Context manager yielding a read-only WAL connection that is automatically closed on exit.
    Does not acquire any write lock.
    """
    conn = get_readonly_connection(db_path, timeout=timeout)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

no_lock = nullcontext()
