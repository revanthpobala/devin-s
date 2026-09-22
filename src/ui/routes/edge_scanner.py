"""
Edge Scanner endpoints: real-time intraday alert feed (REST + WebSocket),
manual deep-research dispatch, and universe refresh.

Proxies the standalone edge-scanner subprocess (port 7777) which is bridged
in-process by src/streaming/edge_scanner_bridge.py into a shared ring buffer.

The REST routes live under /api/edge-scanner/*; the WebSocket lives at
/ws/edge-scanner-alerts (app-level, not under /api/) for a clean single-port
proxy that is future-auth-ready.
"""

from __future__ import annotations

import asyncio
import logging
import queue as _queue
import socket
import subprocess
import sys
import threading
from typing import List

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from src import config
from src.streaming.edge_scanner_bridge import (
    _ALERT_RING_BUFFER,
    _WS_SUBSCRIBERS,
    get_candidates,
)
from src.ui import state as _state
from src.ui.state import append_log

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["edge-scanner"])

EDGE_SCANNER_DIR = config.BASE_DIR / "edge_scanner_tmp"


def _is_edge_scanner_port_live(port: int = 7777) -> bool:
    """Check whether the edge-scanner HTTP/WS port is accepting connections."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _universe_count() -> int:
    """Count symbols in the edge-scanner universe CSV, if present."""
    try:
        universe = EDGE_SCANNER_DIR / "data" / "universe.csv"
        if not universe.exists():
            return 0
        with open(universe, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        # header + data rows
        return max(0, len(lines) - 1)
    except Exception:
        return 0


@router.get("/api/edge-scanner/status")
def get_edge_scanner_status():
    """Health check: is edge-scanner running, what regime, how many alerts."""
    from src.streaming import edge_scanner_bridge as _bridge

    proc = _state.EDGE_SCANNER_PROCESS   # always read from module, not a stale local copy
    proc_live = (proc is not None and proc.poll() is None)
    port_live = _is_edge_scanner_port_live(7777)
    running = proc_live or port_live

    regime = None
    try:
        import json as _json
        import urllib.request
        # Correct endpoint: /api/regime (not /v2/status which doesn't exist)
        with urllib.request.urlopen("http://127.0.0.1:7777/api/regime", timeout=2.0) as resp:
            if resp.status == 200:
                payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
                regime = payload.get("regime")
    except Exception:
        regime = None

    return {
        "running": running,
        "process_running": proc_live,
        "port_live": port_live,
        "bridge_running": _bridge._BRIDGE_RUNNING,
        "regime": regime or "unknown",
        "universe_count": _universe_count(),
        "alerts_buffered": len(_ALERT_RING_BUFFER),
    }


@router.get("/api/edge-scanner/alerts")
def get_edge_scanner_alerts(limit: int = Query(100, ge=0, le=500), min_score: float = 0.0):
    """Return last N alerts from the ring buffer, optionally filtered by score."""
    alerts: List[dict] = []
    for a in _ALERT_RING_BUFFER:
        if not isinstance(a, dict):
            continue
        try:
            score = float(a.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score < min_score:
            continue
        alerts.append(a)
        if len(alerts) >= limit:
            break
    return {"alerts": alerts, "count": len(alerts)}


@router.get("/api/edge-scanner/candidates")
def get_edge_scanner_candidates():
    """Return ALL valid deep-research candidates (score >= min_score) with their
    lifecycle status (PENDING / DISPATCHED / COMPLETED / FAILED), newest first.

    This is the list the user reviews to decide which setups to actually run."""
    cands = get_candidates()
    # Count by status for the UI summary bar.
    by_status: dict = {}
    for c in cands:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    return {
        "candidates": cands,
        "count": len(cands),
        "by_status": by_status,
    }


class DispatchRequest(BaseModel):
    symbol: str
    direction: str = "LONG"


@router.post("/api/edge-scanner/dispatch")
def dispatch_ticker(req: DispatchRequest):
    """Manually dispatch a ticker to the Deep Research pipeline from the UI.

    Routes through the bridge's manual_dispatch() so the candidate registry is
    updated and the daily cap / slot gate is bypassed (user is explicitly forcing it)."""
    sym = (req.symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    # Route through the live bridge instance (exposed as _ACTIVE_BRIDGE) so the
    # candidate registry is updated and the cap/slot gate is bypassed on purpose.
    ok = _manual_dispatch_via_bridge(sym)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Could not dispatch {sym} (already dispatched today or no candidate record).")
    append_log(f"🚀 [EDGE-SCANNER] Manual deep research dispatch for {sym}.")
    return {"status": "dispatched", "symbol": sym}


def _manual_dispatch_via_bridge(sym: str) -> bool:
    """Call the live bridge instance's manual_dispatch(). Falls back to a direct
    pipeline spawn if no bridge daemon is running (e.g. UI started without scanner)."""
    from src.streaming import edge_scanner_bridge as _bridge_mod

    # Find the live bridge instance. The worker thread holds it; we expose it via a
    # module-level reference set in start_bridge_daemon's worker.
    inst = getattr(_bridge_mod, "_ACTIVE_BRIDGE", None)
    if inst is not None:
        return inst.manual_dispatch(sym)

    # Fallback: no bridge daemon — spawn the pipeline directly and record the candidate.
    from datetime import datetime, timezone
    _bridge_mod._record_candidate({
        "symbol": sym, "direction": "LONG", "score": 75.0,
        "trigger": "ManualDispatch", "price": 0.0,
        "received_at": datetime.now(timezone.utc).isoformat(),
    })

    def _worker():
        try:
            from src.screener.schwab_pre_move_scan import run_autonomous_screener_pipeline
            today_str = datetime.now().strftime("%Y-%m-%d")
            cand_payload = {
                "symbol": sym,
                "priority_tier": "HIGH_PRIORITY",
                "priority_score": 75.0,
                "side": "LONG",
                "trigger_reason": "EdgeScanner:ManualDispatch",
            }
            run_autonomous_screener_pipeline(
                [cand_payload], auto_max=1, run_deep=True, date_str=today_str, headless=True,
            )
            _bridge_mod._set_candidate_status(sym, "COMPLETED")
        except Exception as e:
            logger.error(f"Edge scanner manual dispatch failed for {sym}: {e}", exc_info=True)
            _bridge_mod._set_candidate_status(sym, "FAILED", error=str(e))

    threading.Thread(target=_worker, name=f"EdgeDispatch_{sym}", daemon=True).start()
    return True


@router.post("/api/edge-scanner/start-universe")
def refresh_universe():
    """Re-run generate_edge_universe.py to rebuild the coiled stock list."""
    def _worker():
        try:
            cmd = [sys.executable, "scripts/generate_edge_universe.py", "--limit", "150"]
            proc = subprocess.Popen(
                cmd, cwd=str(config.BASE_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                s = line.strip()
                if s:
                    append_log(f"[EdgeUniverse] {s}")
            proc.wait()
            append_log("✅ [EDGE-SCANNER] Universe refreshed.")
        except Exception as e:
            logger.error(f"Edge scanner universe refresh failed: {e}", exc_info=True)
            append_log(f"❌ [EDGE-SCANNER] Universe refresh error: {e}")

    threading.Thread(target=_worker, daemon=True, name="EdgeUniverseRefresh").start()
    return {"status": "started"}


# ── WebSocket fan-out (app-level path, not under /api/) ─────────────────────
@router.websocket("/ws/edge-scanner-alerts")
async def ws_edge_scanner_alerts(ws: WebSocket):
    """Fan-out WebSocket: sends a replay of the last 50 alerts on connect,
    then streams every new alert in real time.

    Uses threading.Queue (not asyncio.Queue) because the bridge daemon
    pushes alerts from its own thread/event-loop. asyncio.Queue is NOT
    thread-safe across different event loops.
    """
    await ws.accept()
    # threading.Queue with bounded size so a stalled browser can't eat RAM
    q: _queue.Queue = _queue.Queue(maxsize=200)
    _WS_SUBSCRIBERS.add(q)
    try:
        await ws.send_json({"type": "replay", "alerts": list(_ALERT_RING_BUFFER)[:50]})
        while True:
            # Drain the threading.Queue without blocking the event loop
            try:
                alert = await asyncio.to_thread(q.get, True, 30.0)  # block up to 30s
            except _queue.Empty:
                # Send a keepalive ping so the browser doesn't time out
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json({"type": "alert", "alert": alert})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _WS_SUBSCRIBERS.discard(q)
