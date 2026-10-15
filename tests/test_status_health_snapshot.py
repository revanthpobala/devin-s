import json
import time

from src.ui.routes import status


def _probes(**over):
    base = {
        "llm_online": True, "tracker_pid": 7, "gpu_stats": [], "active_procs": [],
        "vix_price": 17.5, "tasty_count": 2, "tasty_auth": True, "schwab_status": None,
    }
    base.update(over)
    return base


def test_fresh_snapshot_is_served_without_running_probes(tmp_path, monkeypatch):
    snap = tmp_path / "health.json"
    snap.write_text(json.dumps({"ts": time.time(), "probes": _probes()}), encoding="utf-8")
    monkeypatch.setattr(status, "HEALTH_SNAPSHOT", snap)
    monkeypatch.setattr(status, "_probe_llm_online", lambda: (_ for _ in ()).throw(AssertionError("probed")))
    assert status.get_system_status()["vix_price"] == 17.5


def test_stale_snapshot_falls_back_to_inline_probes(tmp_path, monkeypatch):
    snap = tmp_path / "health.json"
    snap.write_text(json.dumps({"ts": time.time() - 999, "probes": _probes(vix_price=1.0)}), encoding="utf-8")
    monkeypatch.setattr(status, "HEALTH_SNAPSHOT", snap)
    monkeypatch.setattr(status, "_PROBE_CACHE", {})
    monkeypatch.setattr(status, "_get_live_vix", lambda: 22.2)
    for name in ("_probe_llm_online", "_get_gpu_stats", "_get_active_processes"):
        monkeypatch.setattr(status, name, lambda: False if name == "_probe_llm_online" else [])
    monkeypatch.setattr(status, "_probe_tastytrade", lambda: (0, False))
    monkeypatch.setattr(status, "_probe_schwab_status", lambda: None)
    monkeypatch.setattr(status, "find_running_tracker_pid", lambda: None)
    assert status.get_system_status()["vix_price"] == 22.2
