"""
Background Daemons Lifecycle & Process Supervisor.
Spins and cleanly manages all background services directly from the WebUI.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from src import config
from src.ui.state import append_log

logger = logging.getLogger("ui_server")


def find_running_tracker_pid() -> Optional[int]:
    """Inspect OS processes to see if main.py --loop or run_market_orchestrator is running."""
    import psutil
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = (proc.info.get('name') or '').lower()
                if not ('python' in name or 'powershell' in name):
                    continue
                cmdline = proc.cmdline()
                if not cmdline:
                    continue
                cmd_str = " ".join(cmdline).lower()
                if ("main.py" in cmd_str and "--loop" in cmd_str) or ("run_market_orchestrator.py" in cmd_str):
                    return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return None


def start_orchestrator():
    """Start the Email Alert Ingestor (main.py --loop) asynchronously."""
    from src.ui import state
    active_pid = find_running_tracker_pid()
    if active_pid is not None:
        return {"status": "already_running", "pid": active_pid}

    append_log("🚀 Starting Email Alert Ingestor (main.py --loop)...")
    cmd = [sys.executable, "main.py", "--loop"]
    state.TRACKER_PROCESS = subprocess.Popen(
        cmd,
        cwd=str(config.BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def _pipe_logs(proc):
        try:
            for line in proc.stdout:
                l_str = line.strip()
                if l_str:
                    append_log(f"[Tracker] {l_str}")
        finally:
            proc.wait()
            append_log(f"⚠️ Email Alert Ingestor stopped (code {proc.returncode}).")

    threading.Thread(target=_pipe_logs, args=(state.TRACKER_PROCESS,), daemon=True).start()
    return {"status": "started", "pid": state.TRACKER_PROCESS.pid}


def stop_orchestrator():
    """Stop the Email Alert Ingestor cleanly."""
    from src.ui import state
    active_pid = find_running_tracker_pid()
    if active_pid is None:
        return {"status": "not_running"}

    append_log(f"🛑 Stopping Email Alert Ingestor (PID {active_pid})...")
    if state.TRACKER_PROCESS is not None and state.TRACKER_PROCESS.poll() is None:
        try:
            state.TRACKER_PROCESS.terminate()
            state.TRACKER_PROCESS.wait(timeout=5)
        except Exception:
            state.TRACKER_PROCESS.kill()
        state.TRACKER_PROCESS = None
    else:
        try:
            import psutil
            p = psutil.Process(active_pid)
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass

    append_log("✅ Email Alert Ingestor terminated.")
    return {"status": "stopped"}


def start_all_daemons():
    """Initialize and start all background daemons on UI server launch."""
    # 1. Email Alert Ingestor
    try:
        pid = find_running_tracker_pid()
        if pid is not None:
            append_log(f"🟢 [Startup] Detected Email Alert Ingestor already active (PID {pid}).")
        else:
            append_log("🚀 [Startup] Dynamically auto-starting Email Alert Ingestor (main.py --loop)...")
            res = start_orchestrator()
            append_log(f"✅ [Startup] Email Alert Ingestor running (PID {res.get('pid')}).")
    except Exception as e:
        logger.error(f"Failed to auto-start Email Alert Ingestor on startup: {e}")
        append_log(f"⚠️ [Startup] Auto-start Email Alert Ingestor failed: {e}")

    # 2. Autonomous Alert Triage Daemon
    try:
        from src.tracking.auto_triage_daemon import start_auto_triage_daemon
        start_auto_triage_daemon(poll_interval=8, batch_size=15)
        append_log("🤖 [Startup] Autonomous Alert Triage Daemon active.")
    except Exception as e_triage:
        logger.warning(f"Failed to start AutoTriageDaemon: {e_triage}")

    # 3. Continuous Schwab 1000 & Tastytrade Screener Daemon
    try:
        from src.screener.continuous_screener_daemon import start_continuous_screener_daemon
        start_continuous_screener_daemon()
        append_log("🤖 [Startup] Continuous Schwab & Tastytrade Screener Daemon active.")
    except Exception as e_screener:
        logger.warning(f"Failed to start ContinuousScreenerDaemon: {e_screener}")

    # 4. Watchlist & Trigger Alert Daemon
    try:
        def _bg_watch_alerts():
            from run_watch_alerts import run_watch_loop
            run_watch_loop(poll_interval=60, sync_sheets=False)
        threading.Thread(target=_bg_watch_alerts, daemon=True, name="WatchAlertsDaemon").start()
        append_log("🤖 [Startup] Real-Time Watchlist Trigger Alert Daemon active (60s loop).")
    except Exception as e_watch:
        logger.warning(f"Failed to start WatchAlertsDaemon: {e_watch}")

    # 5. Local LLM Server Healthcheck on port 8000
    def _bg_check_llm():
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:8000/health")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    append_log("🟢 [Startup] Local LLM Server healthy on port 8000.")
        except Exception:
            append_log("⚠️ [Startup] Local LLM Server not detected on port 8000. Start via scripts\\launchers\\start_llm_server.bat if using local inference.")
    threading.Thread(target=_bg_check_llm, daemon=True, name="LLMHealthCheck").start()

    # 6. Initial Schwab Portfolio Sync in background
    def _bg_portfolio_sync():
        try:
            from src.tracking.schwab_portfolio_manager import sync_schwab_positions
            res = sync_schwab_positions()
            if res.get("success"):
                append_log(f"💼 [Startup] Synced Schwab Portfolio ({res.get('positions_count', 0)} positions, Total: ${res.get('total_liquidation_value', 0):,.2f}).")
        except Exception as e_pfsync:
            logger.debug(f"Initial Schwab portfolio sync skipped/deferred: {e_pfsync}")
    threading.Thread(target=_bg_portfolio_sync, daemon=True, name="StartupPortfolioSync").start()


def stop_all_daemons():
    """Cleanly stop background ingestor, auto-triage daemon, and continuous screener on shutdown."""
    from src.ui import state
    if state.TRACKER_PROCESS is not None and state.TRACKER_PROCESS.poll() is None:
        try:
            logger.info("Stopping Email Alert Ingestor on UI shutdown...")
            state.TRACKER_PROCESS.terminate()
            state.TRACKER_PROCESS.wait(timeout=3)
        except Exception:
            state.TRACKER_PROCESS.kill()
        state.TRACKER_PROCESS = None

    try:
        from src.tracking.auto_triage_daemon import _daemon_instance
        if _daemon_instance:
            _daemon_instance.stop()
    except Exception:
        pass

    try:
        from src.screener.continuous_screener_daemon import stop_continuous_screener_daemon
        stop_continuous_screener_daemon()
    except Exception:
        pass
