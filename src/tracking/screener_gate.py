"""
src/tracking/screener_gate.py

Cross-process coordination gate for the Schwab 1000 Screener.
Coordinates broker bandwidth between the high-throughput screener and live trading:
  - When the screener is active, non-screener workloads automatically route to
    secondary feeds (Tastytrade DXLink, Alpaca, Yahoo Direct) to prevent Schwab rate-limit
    choking (120 req/min).
  - Automatically expires busy locks (default 120s) so crashed processes never deadlock the system.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
GATE_FILE = DATA_DIR / "screener_gate.json"

_local_lock = threading.Lock()
_local_busy: bool = False
_local_busy_until: float = 0.0
_local_reason: str = ""


def set_screener_busy(
    busy: bool,
    duration_seconds: float = 120.0,
    reason: str = "Schwab 1000 screener scan active",
) -> None:
    """Mark the screener as busy (or idle) across all processes."""
    global _local_busy, _local_busy_until, _local_reason
    now = time.time()
    until = (now + duration_seconds) if busy else 0.0

    with _local_lock:
        _local_busy = busy
        _local_busy_until = until
        _local_reason = reason if busy else ""

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "is_busy": busy,
            "busy_until": until,
            "updated_at": now,
            "reason": reason if busy else "",
            "pid": os.getpid(),
        }
        # Atomic file write via temporary file
        tmp_file = GATE_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        tmp_file.replace(GATE_FILE)
    except Exception as e:
        logger.debug(f"Failed to persist screener gate state: {e}")


def is_screener_busy() -> bool:
    """Return True if the screener is currently actively scanning Schwab."""
    global _local_busy, _local_busy_until
    now = time.time()

    # Check local process state first
    with _local_lock:
        if _local_busy and now < _local_busy_until:
            return True
        elif _local_busy and now >= _local_busy_until:
            _local_busy = False

    # Check shared cross-process gate file
    if not GATE_FILE.exists():
        return False

    try:
        with open(GATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("is_busy") and now < float(data.get("busy_until", 0.0)):
            return True
    except Exception:
        pass

    return False


def get_screener_gate_status() -> Dict[str, Any]:
    """Return comprehensive status of the screener gate for diagnostics and UI."""
    now = time.time()
    is_busy = False
    remaining = 0.0
    reason = ""

    # Check in-memory
    with _local_lock:
        if _local_busy and now < _local_busy_until:
            is_busy = True
            remaining = round(_local_busy_until - now, 1)
            reason = _local_reason

    # If local is not busy, verify cross-process file
    if not is_busy and GATE_FILE.exists():
        try:
            with open(GATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            until = float(data.get("busy_until", 0.0))
            if data.get("is_busy") and now < until:
                is_busy = True
                remaining = round(until - now, 1)
                reason = data.get("reason", "")
        except Exception:
            pass

    return {
        "is_screener_busy": is_busy,
        "remaining_seconds": max(0.0, remaining),
        "reason": reason,
        "recommendation": "Use Tastytrade / Alpaca for quotes" if is_busy else "Schwab available",
    }
