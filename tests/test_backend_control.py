import subprocess
import sys
import time

import psutil
import pytest

from src.screener import continuous_screener_daemon as csd
from src.tracking import backend_control as bc


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(csd, "_daemon_instance", None)
    return bc


def test_alive_needs_fresh_heartbeat(ctl):
    assert not ctl.alive()
    ctl.update(heartbeat=time.time())
    assert ctl.alive()
    ctl.update(heartbeat=time.time() - 60)
    assert not ctl.alive()


def test_ui_commands_reach_backend_without_local_daemon(ctl):
    ctl.update(heartbeat=time.time(), screener={"paused": False})
    assert csd.pause_continuous_screener() is True
    assert ctl.read()["commands"]["paused"] is True
    assert csd.toggle_continuous_screener_pause() is True
    assert csd.trigger_continuous_scan_now() is True
    assert ctl.read()["commands"]["scan_now_ts"]
    assert csd.get_continuous_screener_status()["backend"] == "detached"


def test_commands_ignored_when_backend_dead(ctl):
    assert csd.pause_continuous_screener() is False
    assert csd.trigger_continuous_scan_now() is False


def test_kill_terminates_process_and_blocks_autostart(ctl):
    p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        ctl.update(pid=p.pid, heartbeat=time.time())
        res = ctl.stop_process(grace_s=1)
        assert res["status"] == "killed"
        p.wait(timeout=5)
        assert not psutil.pid_exists(p.pid) or p.poll() is not None
        data = ctl.read()
        assert data["stopped_by_user"] is True and not ctl.alive(data)
    finally:
        if p.poll() is None:
            p.kill()


def test_start_clears_user_stop(ctl, monkeypatch):
    ctl.update(stopped_by_user=True)
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 1})())
    assert ctl.start_process()["status"] == "started"
    assert ctl.read()["stopped_by_user"] is False
