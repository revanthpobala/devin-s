"""Detached research backend: triage, screener and watch daemons, independent of any UI page.

    python run_backend.py

Single instance (port lock). The UI stops it through src.tracking.backend_control.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time

from src.tracking import backend_control as bc
from src.tracking import backend_tasks

logger = logging.getLogger("backend")
TICK_S = 3


def acquire_single_instance() -> "socket.socket | None":
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", bc.LOCK_PORT))
    except OSError:
        s.close()
        return None
    return s


def start_daemons() -> None:
    from src.tracking.auto_triage_daemon import start_auto_triage_daemon
    from src.screener.continuous_screener_daemon import start_continuous_screener_daemon

    start_auto_triage_daemon(poll_interval=8, batch_size=15)
    start_continuous_screener_daemon()

    def _watch():
        from run_watch_alerts import run_watch_loop

        run_watch_loop(poll_interval=60, sync_sheets=False)

    threading.Thread(target=_watch, daemon=True, name="WatchAlertsDaemon").start()


def apply_commands(seen: dict) -> None:
    from src.screener import continuous_screener_daemon as csd

    cmds = bc.read().get("commands", {})
    daemon = csd._daemon_instance
    if daemon is None:
        return
    paused = bool(cmds.get("paused", False))
    if paused != bool(getattr(daemon, "paused", False)):
        daemon.pause() if paused else daemon.resume()
    scan_ts = cmds.get("scan_now_ts")
    if scan_ts and scan_ts != seen.get("scan_now_ts"):
        seen["scan_now_ts"] = scan_ts
        daemon.trigger_now()


def snapshot() -> dict:
    from src.screener import continuous_screener_daemon as csd
    from src.tracking import auto_triage_daemon as atd

    return {"screener": csd.get_continuous_screener_status(), "triage": atd.get_auto_triage_status()}


def shutdown() -> None:
    try:
        from src.screener.continuous_screener_daemon import stop_continuous_screener_daemon

        stop_continuous_screener_daemon()
    except Exception:
        pass
    try:
        from src.tracking.auto_triage_daemon import _daemon_instance

        if _daemon_instance:
            _daemon_instance.stop()
    except Exception:
        pass


def main() -> None:
    lock = acquire_single_instance()
    if lock is None:
        logger.info("backend already running; exiting")
        return
    bc.update(pid=os.getpid(), started_at=time.time(), stop_requested=False, heartbeat=time.time())
    from src.clients import net_health

    net_health.install_dns_cache()
    net_health.start_probe()
    start_daemons()
    logger.info("backend daemons started (pid %s)", os.getpid())
    seen: dict = {}
    while True:
        data = bc.read()
        if data.get("stop_requested"):
            logger.info("stop requested; shutting down")
            shutdown()
            bc.update(heartbeat=None, pid=None)
            return
        try:
            apply_commands(seen)
            backend_tasks.tick()
            bc.update(heartbeat=time.time(), pid=os.getpid(), **snapshot())
        except Exception as e:
            logger.warning("tick failed: %s", e)
        time.sleep(TICK_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
    sys.exit(0)
