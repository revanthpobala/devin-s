"""Control channel between the UI and the detached research backend (run_backend.py).

One JSON file in data/. The backend writes status and heartbeat; the UI writes commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src import config

LOCK_PORT = 47652
STALE_S = 30.0
_lock = threading.Lock()


def _path() -> Path:
    return config.BASE_DIR / "data" / "backend_control.json"


def read() -> Dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def update(**fields: Any) -> Dict[str, Any]:
    with _lock:
        data = read()
        data.update(fields)
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
        return data


def alive(data: Optional[Dict[str, Any]] = None) -> bool:
    data = data if data is not None else read()
    hb = data.get("heartbeat")
    return hb is not None and (time.time() - float(hb)) < STALE_S


def command(**cmd: Any) -> None:
    """UI side: ask the backend to do something on its next tick."""
    update(commands={**read().get("commands", {}), **cmd})


def code_is_newer(started_at: Optional[float]) -> bool:
    """True when any source file changed after the running backend started, so it is on old code."""
    if not started_at:
        return False
    roots = [config.BASE_DIR / "src", *config.BASE_DIR.glob("run_*.py")]
    for root in roots:
        files = root.rglob("*.py") if root.is_dir() else [root]
        if any(f.stat().st_mtime > started_at for f in files):
            return True
    return False


def start_process() -> Dict[str, Any]:
    """Launch run_backend.py detached. A second copy exits on its own (port lock)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"status": "skipped_in_tests"}
    if alive():
        return {"status": "already_running", "pid": read().get("pid")}
    update(stopped_by_user=False, stop_requested=False)
    log_path = config.BASE_DIR / "logs" / "backend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "run_backend.py"],
            cwd=str(config.BASE_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    return {"status": "started", "pid": proc.pid}


def stop_process(grace_s: float = 8.0) -> Dict[str, Any]:
    """Ask for a clean stop, then terminate the process tree. Marks it user-stopped so the UI does not relaunch it."""
    import psutil

    data = update(stopped_by_user=True, stop_requested=True)
    pid = data.get("pid")
    if not pid or not psutil.pid_exists(pid):
        update(heartbeat=None)
        return {"status": "not_running"}
    deadline = time.time() + grace_s
    while time.time() < deadline and psutil.pid_exists(pid):
        time.sleep(0.5)
    killed = False
    if psutil.pid_exists(pid):
        try:
            root = psutil.Process(pid)
            procs = root.children(recursive=True) + [root]
            for p in procs:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            _, still = psutil.wait_procs(procs, timeout=5)
            for p in still:
                try:
                    p.kill()
                except psutil.Error:
                    pass
            killed = True
        except psutil.Error:
            pass
    update(heartbeat=None, pid=None)
    return {"status": "killed" if killed else "stopped"}
