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


def _is_port_live(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def start_edge_scanner_process():
    """Launch the edge-scanner subprocess (port 7777) from edge_scanner_tmp/.

    No-ops if a scanner is already listening on port 7777. Returns a status dict."""
    from src.ui import state
    if _is_port_live(7777):
        return {"status": "already_running"}

    edge_dir = config.BASE_DIR / "edge_scanner_tmp"
    run_live = edge_dir / "scripts" / "run_live.py"
    if not run_live.exists():
        append_log(f"⚠️ [Startup] Edge Scanner not started: {run_live} not found.")
        return {"status": "missing", "path": str(run_live)}

    universe = edge_dir / "data" / "universe.csv"

    def _launch_scanner():
        cmd = [sys.executable, "scripts/run_live.py", "--log-level", "WARNING"]
        state.EDGE_SCANNER_PROCESS = subprocess.Popen(
            cmd,
            cwd=str(edge_dir),
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
                        append_log(f"[EdgeScanner] {l_str}")
            finally:
                proc.wait()
                append_log(f"⚠️ Edge Scanner stopped (code {proc.returncode}).")

        threading.Thread(target=_pipe_logs, args=(state.EDGE_SCANNER_PROCESS,), daemon=True).start()

    if universe.exists():
        # Universe exists: launch Edge Scanner immediately with zero startup delay
        _launch_scanner()
        # If universe is older than 4 hours, refresh silently in the background
        try:
            if (time.time() - os.path.getmtime(universe)) > 14400:
                def _bg_refresh():
                    try:
                        subprocess.run(
                            [sys.executable, "scripts/generate_edge_universe.py", "--limit", "150"],
                            cwd=str(config.BASE_DIR), capture_output=True, text=True, timeout=120,
                        )
                    except Exception as e_bg:
                        logger.debug(f"Background universe refresh skipped: {e_bg}")
                threading.Thread(target=_bg_refresh, daemon=True, name="EdgeUniverseBackgroundRefresh").start()
        except Exception:
            pass
        return {"status": "started", "pid": state.EDGE_SCANNER_PROCESS.pid if state.EDGE_SCANNER_PROCESS else None}
    else:
        # First-time initial generation: run in background thread and launch when done
        def _bg_init_and_launch():
            append_log("⏳ [Startup] Edge Scanner universe missing; generating in background...")
            try:
                subprocess.run(
                    [sys.executable, "scripts/generate_edge_universe.py", "--limit", "150"],
                    cwd=str(config.BASE_DIR), capture_output=True, text=True, timeout=120,
                )
            except Exception as e_uni:
                append_log(f"⚠️ [Startup] Universe pre-gen failed ({e_uni}).")
            if universe.exists():
                _launch_scanner()
                append_log("⚡ [Startup] Edge Scanner started on port 7777.")

        threading.Thread(target=_bg_init_and_launch, daemon=True, name="EdgeScannerInitLaunch").start()
        return {"status": "initializing_in_background"}


def stop_edge_scanner_process():
    """Terminate the edge-scanner subprocess if it is still running."""
    from src.ui import state
    if state.EDGE_SCANNER_PROCESS is not None and state.EDGE_SCANNER_PROCESS.poll() is None:
        try:
            logger.info("Stopping Edge Scanner on UI shutdown...")
            state.EDGE_SCANNER_PROCESS.terminate()
            state.EDGE_SCANNER_PROCESS.wait(timeout=5)
        except Exception:
            state.EDGE_SCANNER_PROCESS.kill()
        state.EDGE_SCANNER_PROCESS = None


def start_all_daemons():
    """Initialize and start all background daemons on UI server launch."""
    # 0. Multi-Threaded Today Desk Background Service (15s cache refresh)
    try:
        from src.ui.services.today_service import start_today_service
        start_today_service()
        append_log("⚡ [Startup] Multi-Threaded Today Desk Background Service active (15s cache refresh).")
    except Exception as e_today:
        logger.warning(f"Failed to start TodayBackgroundService: {e_today}")

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

    # 2-4. Triage, screener and watch daemons run in the detached backend process (survives UI restarts).
    # PINE_INPROCESS_DAEMONS=1 keeps the old in-UI behaviour.
    if os.environ.get("PINE_INPROCESS_DAEMONS") == "1":
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

    else:
        try:
            from src.tracking import backend_control as bc

            if bc.alive() and bc.code_is_newer(bc.read().get("started_at")):
                append_log("♻️ [Startup] Research backend is on old code; restarting it.")
                bc.stop_process()
            res = bc.start_process()
            append_log(f"🧠 [Startup] Research backend: {res.get('status')} (PID {res.get('pid')}).")
        except Exception as e_backend:
            logger.warning(f"Research backend failed to start: {e_backend}")

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

    # 7. Edge Scanner (port 7777) + Bridge Daemon
    try:
        start_edge_scanner_process()       # launches edge_scanner_tmp as subprocess
        from src.streaming.edge_scanner_bridge import start_bridge_daemon
        start_bridge_daemon(min_score=75, auto_deep=True)
        append_log("⚡ [Startup] Edge Scanner starting on port 7777 (warms up in ~90s).")
    except Exception as e_edge:
        logger.warning(f"Edge Scanner failed to start: {e_edge}")
        append_log(f"⚠️ [Startup] Edge Scanner not started: {e_edge}")

    # 8. Notifier: child of this server, stops with it
    try:
        start_notifier_process()
        append_log("🔔 [Startup] Notifier started (stops with the server).")
    except Exception as e_notif:
        logger.warning(f"Notifier failed to start: {e_notif}")


_NOTIFIER = None


def stop_notifier_processes():
    """Kill every run_notifier.py process (stale ones from older code included)."""
    import psutil
    global _NOTIFIER
    _NOTIFIER = None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if "python" in (proc.info.get("name") or "").lower() and any("run_notifier.py" in a for a in proc.cmdline()):
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def start_notifier_process():
    """Launch run_notifier.py as a child of this server: replaces any stale copy, exits when this process ends."""
    global _NOTIFIER
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    stop_notifier_processes()
    time.sleep(0.5)
    log_path = config.BASE_DIR / "logs" / "notifier.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with open(log_path, "ab") as log:
        _NOTIFIER = subprocess.Popen(
            [sys.executable, "run_notifier.py", "--parent", str(os.getpid())],
            cwd=str(config.BASE_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )


def stop_all_daemons():
    """Cleanly stop background ingestor, auto-triage daemon, and continuous screener on shutdown."""
    from src.ui import state
    try:
        stop_notifier_processes()
    except Exception:
        pass
    if state.TRACKER_PROCESS is not None and state.TRACKER_PROCESS.poll() is None:
        try:
            logger.info("Stopping Email Alert Ingestor on UI shutdown...")
            state.TRACKER_PROCESS.terminate()
            state.TRACKER_PROCESS.wait(timeout=3)
        except Exception:
            state.TRACKER_PROCESS.kill()
        state.TRACKER_PROCESS = None

    try:
        from src.ui.services.today_service import stop_today_service
        stop_today_service()
    except Exception:
        pass

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

    # 7. Edge Scanner subprocess + bridge daemon
    try:
        from src.streaming.edge_scanner_bridge import _BRIDGE_THREAD
        if _BRIDGE_THREAD is not None and _BRIDGE_THREAD.is_alive():
            _BRIDGE_THREAD.join(timeout=3)
    except Exception:
        pass
    stop_edge_scanner_process()
