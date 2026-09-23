"""
src/streaming/edge_scanner_bridge.py

Resilient WebSocket Bridge Daemon between Edge Scanner (port 7777) and
the Stock Trading Ecosystem.

Hooks into ws://localhost:7777/ws/alerts, extracts live intraday breakout
and volume alerts, checks GPU / execution slot availability, and:
  1. DISPATCHES candidate to Deep Research FIRST (run_deep_research.py).
  2. ONLY AFTER research finishes and is validated as APPROVED:
     - Extracts binding tactical levels (Entry Zone, Stop Loss, Targets).
     - Registers 24/7 cloud price alerts with Tastytrade (pushing to mobile app).
     - Updates tactical status in research_watch.db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue as _queue
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config

# Module-only logger — do NOT call logging.basicConfig() here.
# basicConfig() touches the ROOT logger and poisons uvicorn/FastAPI log
# formatting for the entire web server process when this module is imported.
logger = logging.getLogger("edge_scanner_bridge")
if not logger.handlers:
    _fh = logging.FileHandler(config.LOGS_DIR / "edge_scanner_bridge.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] (EdgeScannerBridge) %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)
    # When imported by the web server, uvicorn's root handler already logs to
    # console AND this FileHandler writes to the log file — keep propagation ON
    # so both happen. But when run standalone (python edge_scanner_bridge.py),
    # basicConfig() adds a root StreamHandler which would DUPLICATE every line.
    # Detect import context: if __main__ is set, we're standalone → don't propagate.
    logger.propagate = (__name__ != "__main__")

DEFAULT_WS_URL = "ws://localhost:7777/ws/alerts"

# ── Shared state (consumed by src/ui/routes/edge_scanner.py) ──────────────
from collections import deque

_ALERT_RING_BUFFER: deque = deque(maxlen=500)   # last 500 alerts, newest first
# threading.Queue (not asyncio.Queue) — the bridge runs in its own thread/loop;
# FastAPI's WebSocket handler runs in the main uvicorn event loop. asyncio.Queue
# is NOT thread-safe across event loops. threading.Queue IS.
_WS_SUBSCRIBERS: set = set()                    # set of threading.Queue, one per browser WS
_BRIDGE_RUNNING: bool = False
_BRIDGE_THREAD = None
_ACTIVE_BRIDGE: Optional["EdgeScannerBridge"] = None   # live instance, for manual dispatch

# ── Deep-Research candidate tracking (consumed by the UI) ─────────────────
# Every high-score alert that qualifies for deep research is recorded here so the
# user can SEE all valid candidates and choose which ones to actually run.
# Keyed by symbol; value is a dict with status: PENDING | DISPATCHED | COMPLETED | FAILED.
_CANDIDATES: Dict[str, Dict[str, Any]] = {}
_CANDIDATES_LOCK = threading.Lock()


def _record_candidate(alert: Dict[str, Any]) -> None:
    """Record or update a deep-research candidate in the shared registry."""
    sym = str(alert.get("symbol", "")).upper().strip()
    if not sym:
        return
    score = float(alert.get("score", 0.0))
    now_iso = datetime.now(timezone.utc).isoformat()
    with _CANDIDATES_LOCK:
        existing = _CANDIDATES.get(sym)
        if existing is None:
            _CANDIDATES[sym] = {
                "symbol": sym,
                "direction": str(alert.get("direction", "LONG")).upper(),
                "score": score,
                "trigger": str(alert.get("trigger") or alert.get("entry_trigger") or ""),
                "price": float(alert.get("price", 0.0) or 0.0),
                "received_at": alert.get("received_at", now_iso),
                "status": "PENDING",
                "dispatched_at": None,
                "completed_at": None,
                "error": None,
            }
        else:
            # Update score/price if a newer (higher) alert arrived for the same symbol.
            if score > existing["score"]:
                existing["score"] = score
                existing["trigger"] = str(alert.get("trigger") or alert.get("entry_trigger") or existing["trigger"])
                existing["price"] = float(alert.get("price", 0.0) or existing["price"])
            existing["received_at"] = alert.get("received_at", existing["received_at"])


def _set_candidate_status(sym: str, status: str, error: Optional[str] = None) -> None:
    """Update a candidate's lifecycle status."""
    sym_u = sym.upper().strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _CANDIDATES_LOCK:
        c = _CANDIDATES.get(sym_u)
        if c is None:
            return
        c["status"] = status
        if status == "DISPATCHED":
            c["dispatched_at"] = now_iso
        elif status in ("COMPLETED", "FAILED"):
            c["completed_at"] = now_iso
        if error is not None:
            c["error"] = error


def get_candidates() -> List[Dict[str, Any]]:
    """Return all deep-research candidates, newest first (by score desc)."""
    with _CANDIDATES_LOCK:
        return sorted(_CANDIDATES.values(), key=lambda c: (-c["score"], c["received_at"]), reverse=False)


def clear_candidates() -> None:
    """Clear the candidate registry (e.g. on new trading day or manual reset)."""
    with _CANDIDATES_LOCK:
        _CANDIDATES.clear()


def _fan_out_to_subscribers(alert: Dict[str, Any]) -> None:
    """Push an alert to every connected browser WebSocket subscriber queue.
    Uses threading.Queue so it is safe to call from any thread/loop."""
    dead = set()
    for q in _WS_SUBSCRIBERS:
        try:
            q.put_nowait(alert)  # threading.Queue.put_nowait — never blocks
        except _queue.Full:
            dead.add(q)
    if dead:
        _WS_SUBSCRIBERS.difference_update(dead)


def start_bridge_daemon(min_score: float = 75.0, auto_deep: bool = True, max_deep: int = 3):
    """Start EdgeScannerBridge in a background thread + its own asyncio loop.

    Safe to call multiple times — no-ops if already running."""
    global _BRIDGE_RUNNING, _BRIDGE_THREAD, _ACTIVE_BRIDGE
    if _BRIDGE_RUNNING:
        return
    _BRIDGE_RUNNING = True

    def _worker():
        global _ACTIVE_BRIDGE  # type: ignore
        bridge = EdgeScannerBridge(min_score=min_score, auto_deep=auto_deep, max_deep_per_day=max_deep)
        _ACTIVE_BRIDGE = bridge
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bridge.run_listener())
        finally:
            global _BRIDGE_RUNNING  # type: ignore
            _BRIDGE_RUNNING = False

    _BRIDGE_THREAD = threading.Thread(target=_worker, name="EdgeScannerBridge", daemon=True)
    _BRIDGE_THREAD.start()


class EdgeScannerBridge:
    """
    Subscribes to Edge Scanner WebSocket feed, filters high-conviction intraday setups,
    manages Deep Research slot dispatching, and triggers Tastytrade cloud alerts only upon
    successful PM approval.
    """

    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        min_score: float = 75.0,
        auto_deep: bool = True,
        max_deep_per_day: int = 3,
        dry_run: bool = False,
    ):
        self.ws_url = ws_url
        self.min_score = min_score
        self.auto_deep = auto_deep
        self.max_deep_per_day = max_deep_per_day
        self.dry_run = dry_run
        self.running = True

        # State tracking
        self.dispatched_today: set[str] = set()
        self._today_date: Optional[str] = None
        self._lock = threading.Lock()
        self.alerts_received = 0
        self.dispatches_count = 0

        # Pending queue: high-conviction alerts that could not be dispatched because
        # the deep-research slot was busy or the daily cap was hit. Retried on every
        # subsequent alert so a qualified setup is never silently dropped for the day.
        # Each entry: {"symbol": str, "alert": dict}. Bounded to avoid unbounded growth.
        self._pending_queue: List[Dict[str, Any]] = []
        self._max_pending: int = 20

    def _get_today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def is_slot_available(self) -> bool:
        """Query the RUNNING ContinuousScreenerDaemon singleton for slot availability.

        Must NOT construct a fresh instance: a throwaway daemon has an empty
        _active_research_threads list, so get_active_research_count() would always
        be 0 and this would (incorrectly) report a free slot while the real daemon
        is mid-research — letting two deep-research runs collide on the GPU.
        """
        try:
            from src.screener import continuous_screener_daemon as _csd
            daemon = _csd._daemon_instance
            if daemon is None or not daemon.is_alive():
                # No live screener daemon (e.g. running standalone) — nothing else
                # holding a slot, so treat the slot as free.
                return True
            return daemon.is_slot_available()
        except Exception as e:
            logger.warning(f"Could not query daemon slot state: {e}. Defaulting to True.")
            return True

    def dispatch_to_deep_research(self, alert: Dict[str, Any]) -> bool:
        """
        Asynchronously dispatch a qualified candidate to the Deep Research pipeline.
        Strictly research FIRST: Tastytrade alerts and watch triggers are executed
        only after research completes with an APPROVED verdict.

        If the deep-research slot is busy or the daily cap is hit, the candidate is
        parked in self._pending_queue (not dropped) and retried on the next alert.
        """
        sym = str(alert.get("symbol", "")).upper().strip()
        if not sym:
            return False

        today_str = self._get_today_str()
        with self._lock:
            # Rollover to a new trading day clears both dispatched and pending state.
            if self._today_date != today_str:
                self._today_date = today_str
                self.dispatched_today = set()
                self._pending_queue = []

            # First try to flush anything parked from earlier alerts (highest score first).
            self._try_flush_pending(today_str)

            if sym in self.dispatched_today:
                logger.info(f"⏭️ {sym} was already dispatched to Deep Research today. Skipping duplicate.")
                return False

            if not self._fits_budget():
                # Slot busy or daily cap reached — park it so it isn't lost.
                self._enqueue_pending(sym, alert)
                logger.info(
                    f"⏳ {sym} (score {float(alert.get('score', 0.0)):.1f}) parked for later "
                    f"(slot busy or daily cap reached). Pending queue: {len(self._pending_queue)}."
                )
                return False

            if self.dry_run:
                logger.info(
                    f"[DRY-RUN] Would dispatch {sym} (Score: {float(alert.get('score', 0.0)):.1f}, "
                    f"Trigger: {alert.get('trigger')}) to Deep Research."
                )
                return True

            self._mark_dispatched(sym)
            _set_candidate_status(sym, "DISPATCHED")
            logger.info(
                f"🚀 [EdgeScannerBridge] Dispatched {sym} ({str(alert.get('direction', 'LONG')).upper()}, "
                f"Score: {float(alert.get('score', 0.0)):.1f}, Trigger: {alert.get('trigger')}) to Deep Research."
            )

        self._spawn_research_worker(sym, alert, today_str)
        return True

    def manual_dispatch(self, sym: str, alert: Optional[Dict[str, Any]] = None) -> bool:
        """Manually dispatch a candidate to deep research from the UI.

        Bypasses the daily cap and slot gate so the user can force-run any
        valid candidate they've selected. If `alert` is not provided, looks up
        the candidate in the shared registry.
        """
        sym_u = sym.upper().strip()
        if not sym_u:
            return False

        # Resolve the alert payload from the registry if not supplied.
        if alert is None:
            with _CANDIDATES_LOCK:
                c = _CANDIDATES.get(sym_u)
                if c is None:
                    logger.warning(f"manual_dispatch: no candidate record for {sym_u}.")
                    return False
                alert = {
                    "symbol": sym_u,
                    "direction": c["direction"],
                    "score": c["score"],
                    "trigger": c["trigger"],
                    "price": c["price"],
                }

        today_str = self._get_today_str()
        with self._lock:
            if self._today_date != today_str:
                self._today_date = today_str
                self.dispatched_today = set()
                self._pending_queue = []
            if sym_u in self.dispatched_today:
                logger.info(f"manual_dispatch: {sym_u} already dispatched today.")
                return False
            self._mark_dispatched(sym_u)

        _set_candidate_status(sym_u, "DISPATCHED")
        logger.info(f"🚀 [EdgeScannerBridge] MANUAL dispatch of {sym_u} to Deep Research.")
        threading.Thread(
            target=self._spawn_research_worker, args=(sym_u, alert, today_str),
            name=f"EdgeDeep_Manual_{sym_u}", daemon=True,
        ).start()
        return True

    def _fits_budget(self) -> bool:
        """True if there is room to dispatch another candidate right now (cap + slot)."""
        if len(self.dispatched_today) >= self.max_deep_per_day:
            return False
        return self.is_slot_available()

    def _enqueue_pending(self, sym: str, alert: Dict[str, Any]) -> None:
        """Park a candidate for later. Caller must hold self._lock."""
        if any(p["symbol"] == sym for p in self._pending_queue):
            return  # already queued — keep the first (earliest) sighting
        self._pending_queue.append({"symbol": sym, "alert": alert})
        # Bound the queue: drop the lowest-scored entry if over capacity.
        if len(self._pending_queue) > self._max_pending:
            self._pending_queue.sort(key=lambda p: float(p["alert"].get("score", 0.0)))
            self._pending_queue.pop(0)

    def _try_flush_pending(self, today_str: str) -> None:
        """Dispatch parked candidates while budget allows. Caller must hold self._lock."""
        if not self._pending_queue:
            return
        # Highest score first so the best parked setup gets the slot.
        self._pending_queue.sort(key=lambda p: float(p["alert"].get("score", 0.0)), reverse=True)
        for entry in list(self._pending_queue):
            sym = entry["symbol"]
            if sym in self.dispatched_today:
                self._pending_queue.remove(entry)
                continue
            if not self._fits_budget():
                break  # budget exhausted — keep the rest parked
            self._pending_queue.remove(entry)
            if self.dry_run:
                logger.info(f"[DRY-RUN] (flush) Would dispatch parked {sym} to Deep Research.")
                continue
            self._mark_dispatched(sym)
            logger.info(
                f"🚀 [EdgeScannerBridge] Flushing parked {sym} "
                f"(Score: {float(entry['alert'].get('score', 0.0)):.1f}) to Deep Research."
            )
            # Spawn outside the lock to avoid holding it across thread start.
            threading.Thread(
                target=self._spawn_research_worker, args=(sym, entry["alert"], today_str),
                name=f"EdgeDeep_{sym}", daemon=True,
            ).start()

    def _mark_dispatched(self, sym: str) -> None:
        """Record a dispatch. Caller must hold self._lock."""
        self.dispatched_today.add(sym)
        self.dispatches_count += 1

    def _spawn_research_worker(self, sym: str, alert: Dict[str, Any], today_str: str) -> None:
        """Run the autonomous deep-research pipeline for one candidate (blocking)."""
        direction = str(alert.get("direction", "LONG")).upper()
        score = float(alert.get("score", 0.0))
        trigger = str(alert.get("trigger", ""))
        suggested_stop = alert.get("suggested_stop")

        cand_payload = {
            "symbol": sym,
            "priority_tier": "HIGH_PRIORITY",
            "priority_score": score,
            "side": direction,
            "support_level": suggested_stop if direction == "LONG" else None,
            "ceiling_level": suggested_stop if direction == "SHORT" else None,
            "trigger_reason": f"EdgeScanner:{trigger}",
        }
        try:
            from src.screener.schwab_pre_move_scan import run_autonomous_screener_pipeline
            logger.info(f"🔬 Starting autonomous pipeline for {sym}...")
            run_autonomous_screener_pipeline(
                [cand_payload],
                auto_max=1,
                run_deep=True,
                date_str=today_str,
                headless=True,
            )
            logger.info(f"✅ Autonomous deep research pipeline finished for {sym}.")

            # Verify research verdict and sync watch alerts only if approved
            self._post_research_sync(sym, today_str)
            _set_candidate_status(sym, "COMPLETED")

        except Exception as exc:
            logger.error(f"❌ Error in Deep Research worker for {sym}: {exc}", exc_info=True)
            _set_candidate_status(sym, "FAILED", error=str(exc))

    def _post_research_sync(self, sym: str, date_str: str) -> None:
        """
        Inspect output markdown reports. ONLY if research passed:
        register Tastytrade 24/7 cloud price alerts and mirror to research_watch.db.
        """
        reports_dir = config.BASE_DIR / "reports" / date_str
        summary_file = reports_dir / f"{sym}_summary.md"
        arbitration_file = reports_dir / f"{sym}_arbitration.md"

        if not summary_file.exists():
            logger.warning(f"No summary report found for {sym} at {summary_file}. Skipping Tastytrade alert.")
            return

        # Check verdict
        try:
            content = summary_file.read_text(encoding="utf-8", errors="replace")
            is_approved = any(v in content for v in ("VERDICT: ACTIONABLE", "VERDICT: PASS", "DIRECTIVE: LONG", "DIRECTIVE: SHORT", "VERDICT: APPROVED"))

            if not is_approved:
                logger.info(f"📋 Setup {sym} was NOT approved by Senior PM Arbitration. Tastytrade alert NOT registered.")
                return

            logger.info(f"🎯 Setup {sym} APPROVED by Deep Research! Registering Tastytrade cloud alerts...")

            # Run watch alerts sync to extract levels and push to Tastytrade mobile
            import subprocess
            cmd = [sys.executable, "run_watch_alerts.py", "--sync", "--once"]
            res = subprocess.run(cmd, cwd=str(config.BASE_DIR), capture_output=True, text=True)
            if res.returncode == 0:
                logger.info(f"📱 Tastytrade mobile cloud alerts successfully synced for {sym}!")
            else:
                logger.warning(f"Watch alerts sync notice: {res.stderr[:200]}")

        except Exception as e:
            logger.error(f"Error during post-research sync for {sym}: {e}")

    async def run_listener(self) -> None:
        """Async loop subscribing to ws://localhost:7777/ws/alerts."""
        retry_delay = 2.0
        logger.info(f"Connecting to Edge Scanner WebSocket at {self.ws_url} (min_score: {self.min_score})...")

        while self.running:
            try:
                # Append filter query parameters
                url = f"{self.ws_url}?min_score={int(self.min_score)}"
                async with websockets.connect(url) as ws:
                    logger.info(f"✅ Connected to Edge Scanner unified feed at {url}!")
                    retry_delay = 2.0

                    # First frame is replay of today's alerts
                    init_raw = await ws.recv()
                    init_frame = json.loads(init_raw)
                    if init_frame.get("type") == "replay":
                        replayed = init_frame.get("alerts", [])
                        logger.info(f"Received {len(replayed)} initial historical alerts from Edge Scanner.")

                    while self.running:
                        msg_raw = await ws.recv()
                        msg = json.loads(msg_raw)

                        if msg.get("type") == "alert":
                            alert = msg.get("alert", {})
                            self.alerts_received += 1
                            alert["received_at"] = datetime.now(timezone.utc).isoformat()
                            _ALERT_RING_BUFFER.appendleft(alert)   # newest first
                            _fan_out_to_subscribers(alert)         # fan out to browser WS clients
                            sym = alert.get("symbol")
                            trigger = alert.get("trigger")
                            score = float(alert.get("score", 0.0))
                            price = alert.get("price")
                            direction = alert.get("direction")

                            logger.info(
                                f"⚡ [ALERT] {sym} | {direction} | Score: {score:.1f} | "
                                f"Trigger: {trigger} @ ${price}"
                            )

                            # Record every qualifying candidate so the UI can show
                            # ALL valid deep-research candidates for manual selection.
                            if score >= self.min_score:
                                _record_candidate(alert)

                            if self.auto_deep and score >= self.min_score:
                                self.dispatch_to_deep_research(alert)

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                if self.running:
                    logger.debug(f"⚠️ Edge Scanner WebSocket disconnected ({e}). Retrying in {retry_delay:.1f}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 30.0)
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    def stop(self) -> None:
        logger.info("Stopping Edge Scanner Bridge...")
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Edge Scanner to Deep Research Bridge Daemon")
    parser.add_argument("--url", default=DEFAULT_WS_URL, help=f"WebSocket URL (default: {DEFAULT_WS_URL})")
    parser.add_argument("--min-score", type=float, default=75.0, help="Min priority score to dispatch (default: 75.0)")
    parser.add_argument("--auto-deep", action="store_true", default=True, help="Enable automatic deep research dispatch")
    parser.add_argument("--no-auto-deep", dest="auto_deep", action="store_false", help="Disable automatic deep research")
    parser.add_argument("--max-deep", type=int, default=3, help="Max deep research dispatches per day (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate alerts without dispatching deep research")
    args = parser.parse_args()

    bridge = EdgeScannerBridge(
        ws_url=args.url,
        min_score=args.min_score,
        auto_deep=args.auto_deep,
        max_deep_per_day=args.max_deep,
        dry_run=args.dry_run,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _sig_handler(sig, frame):
        logger.info("Termination signal received. Shutting down...")
        bridge.stop()
        loop.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        loop.run_until_complete(bridge.run_listener())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
