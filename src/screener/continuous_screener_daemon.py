"""
Continuous Schwab 1000 & Tastytrade Autonomous Screener Daemon.
Runs in a background thread to continuously screen the 983 Schwab constituents for:
  1. Ground-Floor Basing Coils (Long)
  2. Ceiling Exhaustion & Rejection (Prime Short)
Enriches setups with Tastytrade institutional volatility metrics (IV Rank, IV Percentile, 30d HV, IV-HV spread)
and automatically registers 24/7 cloud price alerts with mobile push notifications to the Tastytrade Mobile app.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.clients.schwab_client import get_schwab_client
from src.clients.tastytrade_client import TastytradeClient

logger = logging.getLogger("continuous_screener")

# Default scan interval in seconds (default 5 minutes)
DEFAULT_SCAN_INTERVAL = int(os.getenv("CONTINUOUS_SCREENER_INTERVAL", "600"))

_daemon_instance: Optional[ContinuousScreenerDaemon] = None


def enrich_candidates_with_tastytrade(
    candidates: List[Dict[str, Any]],
    auto_alerts: bool = False,
) -> int:
    """Enrich candidates with Tastytrade IV metrics. Note: Cloud price alerts are registered only after deep research completes."""
    if not candidates:
        return 0

    symbols = list({str(p.get("symbol") or p.get("Symbol") or p.get("Ticker")).upper() for p in candidates if p.get("symbol") or p.get("Symbol") or p.get("Ticker")})
    if not symbols:
        return 0

    tt_client = TastytradeClient()
    headers = tt_client.get_auth_headers()
    if not headers:
        logger.warning("[ContinuousScreener] Tastytrade client not authenticated. Skipping TT enrichment.")
        return 0

    metrics_list = tt_client.get_market_metrics(symbols)
    metrics_by_sym: Dict[str, Dict[str, Any]] = {}
    for m in metrics_list:
        sym = m.get("symbol")
        if sym:
            metrics_by_sym[sym.upper()] = m

    alerts_created = 0
    # Fetched lazily (once per pass) only when auto_alerts is enabled.
    _existing_alerts: List[Dict[str, Any]] = []
    _existing_alerts_fetched = False

    for p in candidates:
        sym = str(p.get("symbol") or p.get("Symbol") or p.get("Ticker")).upper()
        m = metrics_by_sym.get(sym, {})

        def _to_float(val, mult=1.0, default=None):
            if val is None or val in ("NaN", "nan", "None", ""):
                return default
            try:
                return round(float(val) * mult, 2)
            except (ValueError, TypeError):
                return default

        iv_rank = _to_float(m.get("tos-implied-volatility-index-rank") or m.get("implied-volatility-index-rank"), 100.0)
        iv_perc = _to_float(m.get("implied-volatility-percentile"), 100.0)
        hv30 = _to_float(m.get("historical-volatility-30-day"))
        hv60 = _to_float(m.get("historical-volatility-60-day"))
        hv90 = _to_float(m.get("historical-volatility-90-day"))
        iv_hv_diff = _to_float(m.get("iv-hv-30-day-difference"))
        borrow_rate = _to_float(m.get("borrow-rate"))
        beta = _to_float(m.get("beta"))
        liq_rating = m.get("liquidity-rating") or 1
        lendability = m.get("lendability") or "Unknown"

        p["tastytrade"] = {
            "connected": True,
            "iv_rank": iv_rank,
            "iv_percentile": iv_perc,
            "hv30": hv30,
            "hv60": hv60,
            "hv90": hv90,
            "iv_hv_diff": iv_hv_diff,
            "liquidity_rating": liq_rating,
            "borrow_rate": borrow_rate,
            "lendability": lendability,
            "beta": beta,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Auto-Register 24/7 Tastytrade Cloud Quote Alerts for High/Medium Priority Setups.
        # Idempotent: existing cloud alerts are checked first so repeated scans do not
        # duplicate registrations, and `tastytrade_alert_active` is only set when at
        # least one alert was actually created or already exists on the cloud.
        if auto_alerts:
            # Cloud alerts follow the same gate as autonomous dispatch: PB funnel for longs, short conviction for shorts.
            side = str(p.get("side", "LONG")).upper()
            if side == "LONG":
                is_qualified = bool(p.get("pb_funnel"))
            else:
                from src.config import SCREENER_MIN_CONVICTION
                tier = str(p.get("priority_tier") or "MONITOR")
                score = float(p.get("priority_score") or 0.0)
                is_qualified = (tier == "HIGH_PRIORITY" or score >= SCREENER_MIN_CONVICTION)

            if is_qualified:
                side = str(p.get("side", "LONG")).upper()
                try:
                    # Build the desired alert set for this candidate (dedup key: symbol+op+threshold)
                    desired: List[Dict[str, Any]] = []
                    if side == "LONG":
                        sup = float(p.get("support_level", 0.0))
                        if sup > 0:
                            desired.append({"symbol": sym, "operator": "<=", "threshold": round(sup, 2)})
                        tgt = float(p.get("target_level", 0.0))
                        if tgt > 0:
                            desired.append({"symbol": sym, "operator": ">=", "threshold": round(tgt, 2)})
                    else:  # SHORT
                        ceil_lvl = float(p.get("ceiling_level", 0.0))
                        if ceil_lvl > 0:
                            desired.append({"symbol": sym, "operator": ">=", "threshold": round(ceil_lvl, 2)})
                        tgt = float(p.get("target_level", 0.0))
                        if tgt > 0:
                            desired.append({"symbol": sym, "operator": "<=", "threshold": round(tgt, 2)})

                    # Fetch existing cloud alerts once per enrichment pass (cached)
                    if not _existing_alerts_fetched:
                        try:
                            _existing_alerts.extend(tt_client.get_quote_alerts())
                        except Exception as e_fetch:
                            logger.warning(f"[ContinuousScreener] Could not fetch existing Tastytrade alerts: {e_fetch}")
                        finally:
                            _existing_alerts_fetched = True

                    existing_keys = set()
                    for a in _existing_alerts:
                        a_sym = str(a.get("symbol") or "").upper()
                        a_op = str(a.get("operator") or "")
                        if a_op == "<":
                            a_op = "<="
                        elif a_op == ">":
                            a_op = ">="
                        try:
                            a_thr = round(float(a.get("threshold", 0.0)), 2)
                        except (ValueError, TypeError):
                            a_thr = 0.0
                        existing_keys.add((a_sym, a_op, a_thr))

                    created_here = 0
                    for d in desired:
                        key = (d["symbol"], d["operator"], d["threshold"])
                        if key in existing_keys:
                            continue  # already registered on the cloud — skip (idempotent)
                        alert = tt_client.create_quote_alert(
                            symbol=d["symbol"],
                            threshold=d["threshold"],
                            operator=d["operator"],
                            field="Last",
                            expires_days=30,
                        )
                        if alert:
                            created_here += 1
                            alerts_created += 1
                            # Track it so later candidates in the same pass don't double-create
                            existing_keys.add(key)

                    p["tastytrade_alert_active"] = created_here > 0 or any(
                        (d["symbol"], d["operator"], d["threshold"]) in existing_keys for d in desired
                    )
                except Exception as e_alert:
                    logger.error(f"[ContinuousScreener] Error creating Tastytrade alert for {sym}: {e_alert}")
                    p["tastytrade_alert_active"] = False

    return alerts_created


class ContinuousScreenerDaemon(threading.Thread):
    """Background daemon that continuously scans Schwab 1000 and registers Tastytrade cloud alerts."""

    def __init__(
        self,
        poll_interval: int = DEFAULT_SCAN_INTERVAL,
        top_n: int = 10,
        auto_alerts: bool = False,
        market_hours_only: Optional[bool] = None,
        auto_deep_research: Optional[bool] = None,
        max_concurrent_slots: int = 3,
    ):
        super().__init__(name="ContinuousScreenerDaemon", daemon=True)
        self.poll_interval = max(60, poll_interval)
        self.top_n = top_n
        self.auto_alerts = auto_alerts
        # Centralized scheduling: default to config, allow per-run CLI override.
        self.market_hours_only = (
            config.SCREENER_MARKET_HOURS_ONLY if market_hours_only is None else market_hours_only
        )
        self.auto_deep_research = auto_deep_research if auto_deep_research is not None else (
            os.getenv("CONTINUOUS_AUTO_DEEP_RESEARCH", "1").lower() in ("1", "true", "yes")
        )
        self.max_auto_deep_per_day = int(os.getenv("CONTINUOUS_MAX_AUTO_DEEP", "0"))
        # Display-only. The dispatch gate is pb_funnel (long side); see
        # evaluate_and_dispatch_deep_research and config.SCREENER_MIN_CONVICTION.
        self.min_conviction_score = config.SCREENER_MIN_CONVICTION
        self.max_concurrent_slots = max(1, int(os.getenv("CONTINUOUS_MAX_CONCURRENT_SLOTS", str(max_concurrent_slots))))
        self._active_research_threads: List[threading.Thread] = []
        self.running = True
        self.paused = False
        self._wake_event = threading.Event()
        self._lock = threading.Lock()

        # State tracking
        self.is_scanning = False
        self.last_scan_time: Optional[str] = None
        self.next_scan_time: Optional[str] = None
        self.scan_count = 0
        self.long_count = 0
        self.short_count = 0
        self.last_status = "Initialized"
        self.last_error: Optional[str] = None
        self.tastytrade_connected = False
        self.active_alerts_registered = 0
        self.last_market_tide: Dict[str, Any] = {}
        self._today_date: Optional[str] = None
        self.auto_deep_dispatched_today: set[str] = set()
        self.movers_alerted_today: set[str] = set()
        self.last_dispatched_ticker: Optional[str] = None

    def is_market_hours(self) -> bool:
        """Check if current Mountain Time is within configured market hours."""
        now_mt = datetime.now(ZoneInfo("America/Denver"))
        if now_mt.weekday() >= 5:  # Weekend
            return False
        start_time = now_mt.replace(
            hour=getattr(config, "MARKET_OPEN_HOUR", 7),
            minute=getattr(config, "MARKET_OPEN_MINUTE", 15),
            second=0,
            microsecond=0,
        )
        end_time = now_mt.replace(
            hour=getattr(config, "MARKET_CLOSE_HOUR", 20),
            minute=getattr(config, "MARKET_CLOSE_MINUTE", 0),
            second=0,
            microsecond=0,
        )
        return start_time <= now_mt <= end_time

    def should_run_initial_scan(self, date_str: str) -> bool:
        """Determine if today's files are missing, empty, or stale, requiring an immediate initial scan."""
        raw_dir = config.BASE_DIR / "data" / "raw" / date_str
        long_file = raw_dir / "schwab_survivors.json"
        short_file = raw_dir / "short_survivors.json"

        if not long_file.exists() or not short_file.exists():
            return True

        try:
            with open(long_file, "r", encoding="utf-8") as f:
                long_data = json.load(f)
            with open(short_file, "r", encoding="utf-8") as f:
                short_data = json.load(f)
            # If both files are completely empty lists, check file age
            if not long_data and not short_data:
                mtime = min(long_file.stat().st_mtime, short_file.stat().st_mtime)
                if time.time() - mtime > self.poll_interval:
                    return True
        except Exception:
            return True

        return False

    def enrich_with_tastytrade(
        self,
        long_picks: List[Dict[str, Any]],
        short_picks: List[Dict[str, Any]],
    ) -> int:
        """Enrich candidates with Tastytrade IV metrics and auto-register 24/7 cloud price alerts."""
        all_picks = long_picks + short_picks
        count = enrich_candidates_with_tastytrade(all_picks, auto_alerts=self.auto_alerts)
        self.tastytrade_connected = bool(all_picks and all_picks[0].get("tastytrade", {}).get("connected"))
        return count

    def run_scan_cycle(self) -> Dict[str, Any]:
        """Execute one complete screener pass across both Long and Short sides with Tastytrade enrichment."""
        from src.screener.schwab_pre_move_scan import (
            check_market_tide,
            run_schwab_pre_move_scan,
            save_short_manifest,
            save_survivors_manifest,
        )

        with self._lock:
            self.is_scanning = True
            self.last_status = "Scanning 983 Schwab 1000 constituents live..."

        date_str = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
        start_t = time.time()
        logger.info(f"🔄 [ContinuousScreener] Initiating background scan cycle (Date: {date_str}, Top: {self.top_n})...")

        try:
            # Check market tide
            client = get_schwab_client()
            tide = check_market_tide(client)
            self.last_market_tide = tide

            # Run Schwab Scan for both sides
            results = run_schwab_pre_move_scan(
                top_n=self.top_n,
                side="both",
                target_date=date_str,
                autonomous=False,
                auto_scrape=False,
                headless=True,
            )

            long_picks = results.get("long", []) if isinstance(results, dict) else []
            short_picks = results.get("short", []) if isinstance(results, dict) else []

            # Enrich with Tastytrade metrics and register cloud alerts
            alerts_created = self.enrich_with_tastytrade(long_picks, short_picks)

            # Re-save enriched manifests
            if long_picks:
                save_survivors_manifest(long_picks, date_str)
            if short_picks:
                save_short_manifest(short_picks, date_str)

            # Autonomous Deep Research Dispatch (gated, capped, deduplicated)
            dispatched_syms = self.evaluate_and_dispatch_deep_research(long_picks + short_picks, date_str)

            # Live re-score loop (Section B): trailing 10 sessions dossiers re-evaluated at live price
            try:
                self.rescore_recent_dossiers(date_str)
            except Exception as e_rescore:
                logger.error(f"[ContinuousScreener] Error in rescore_recent_dossiers: {e_rescore}")

            # Synchronize Schwab Portfolio & Positions in background
            try:
                from src.tracking.schwab_portfolio_manager import sync_schwab_positions
                sync_schwab_positions(client=client)
            except Exception as e_pf:
                logger.debug(f"[ContinuousScreener] Portfolio background sync notice: {e_pf}")

            elapsed = time.time() - start_t
            now_iso = datetime.now().isoformat()

            with self._lock:
                self.last_scan_time = now_iso
                self.next_scan_time = (datetime.now() + timedelta(seconds=self.poll_interval)).isoformat()
                self.scan_count += 1
                self.long_count = len(long_picks)
                self.short_count = len(short_picks)
                self.active_alerts_registered += alerts_created
                disp_txt = f" | Auto-Deep: {', '.join(dispatched_syms)}" if dispatched_syms else ""
                self.last_status = (
                    f"Scan #{self.scan_count} completed in {elapsed:.1f}s: "
                    f"{len(long_picks)} Longs, {len(short_picks)} Shorts. "
                    f"Tastytrade: {'Active' if self.tastytrade_connected else 'Offline'} ({alerts_created} alerts synced)"
                    f"{disp_txt}"
                )
                self.last_error = None
                self.is_scanning = False

            logger.info(f"✅ [ContinuousScreener] {self.last_status}")
            return {
                "success": True,
                "date": date_str,
                "long_count": len(long_picks),
                "short_count": len(short_picks),
                "elapsed": elapsed,
                "alerts_created": alerts_created,
            }

        except Exception as e:
            err_msg = str(e)
            logger.error(f"❌ [ContinuousScreener] Scan cycle failed: {err_msg}", exc_info=True)
            with self._lock:
                self.last_error = err_msg
                self.last_status = f"Error in scan cycle: {err_msg[:80]}"
                self.is_scanning = False
                self.next_scan_time = (datetime.now() + timedelta(seconds=min(self.poll_interval, 120))).isoformat()
            return {"success": False, "error": err_msg}

    def run(self):
        """Continuous background thread execution loop."""
        logger.info(f"🚀 [ContinuousScreener] Background daemon started (Interval: {self.poll_interval}s).")

        # Step 1: Initial scan if today's files are missing or stale.
        # Gated on market hours so a weekend/after-hours start does NOT fire a wasted
        # full-universe sweep; the loop's gate below catches it and idles instead.
        date_str = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
        in_hours = not self.market_hours_only or self.is_market_hours()
        if in_hours and self.should_run_initial_scan(date_str):
            logger.info("[ContinuousScreener] Today's screener results missing or empty. Running immediate initial scan...")
            self.run_scan_cycle()

        # Step 2: Continuous loop
        while self.running:
            # Check if paused by user
            if self.paused:
                with self._lock:
                    self.last_status = "Paused by user"
                self._wake_event.wait(timeout=2.0)
                continue

            # Check market hours gating if enabled
            if self.market_hours_only and not self.is_market_hours():
                # Drop any stale next_scan_time so we don't resume on a misaligned
                # (already-past) cadence; the first in-hours scan re-establishes it.
                with self._lock:
                    self.last_status = "Idling outside market hours (Resumes at 7:15 AM MT)"
                    self.next_scan_time = None
                self._wake_event.wait(timeout=60)
                continue

            # Wait until next scan interval or manual trigger
            wait_sec = self.poll_interval
            with self._lock:
                if self.next_scan_time:
                    try:
                        delta = (datetime.fromisoformat(self.next_scan_time) - datetime.now()).total_seconds()
                        wait_sec = max(1.0, min(delta, self.poll_interval))
                    except Exception:
                        wait_sec = self.poll_interval

            # Sleep in chunks to allow responsive shutdown or manual wake
            wake_triggered = self._wake_event.wait(timeout=wait_sec)
            if wake_triggered:
                self._wake_event.clear()

            if not self.running:
                break

            if self.paused:
                continue

            # Execute periodic or triggered scan cycle
            self.run_scan_cycle()

        logger.info("🛑 [ContinuousScreener] Background daemon terminated.")

    def pause(self):
        """Pause continuous scanning."""
        with self._lock:
            self.paused = True
            self.last_status = "Paused by user"
        logger.info("⏸️ [ContinuousScreener] Screener paused by user.")
        self._wake_event.set()

    def resume(self):
        """Resume continuous scanning."""
        with self._lock:
            self.paused = False
            self.last_status = "Resumed"
            self.next_scan_time = (datetime.now() + timedelta(seconds=self.poll_interval)).isoformat()
        logger.info("▶️ [ContinuousScreener] Screener resumed by user.")
        self._wake_event.set()

    def toggle_pause(self) -> bool:
        """Toggle pause state and return the new paused status."""
        if self.paused:
            self.resume()
        else:
            self.pause()
        return self.paused

    def trigger_now(self):
        """Force an immediate scan cycle without waiting for the timer."""
        logger.info("⚡ [ContinuousScreener] Manual trigger received. Waking up daemon...")
        self._wake_event.set()

    def stop(self):
        """Stop the background screener daemon."""
        self.running = False
        self._wake_event.set()

    def get_status(self) -> Dict[str, Any]:
        """Return thread-safe telemetry and status snapshot."""
        with self._lock:
            # Calculate remaining seconds until next scan
            rem_sec = 0
            if self.next_scan_time:
                try:
                    delta = (datetime.fromisoformat(self.next_scan_time) - datetime.now()).total_seconds()
                    rem_sec = max(0, int(delta))
                except Exception:
                    rem_sec = self.poll_interval

            return {
                "running": self.running,
                "paused": self.paused,
                "is_scanning": self.is_scanning,
                "poll_interval": self.poll_interval,
                "last_scan_time": self.last_scan_time,
                "next_scan_time": self.next_scan_time,
                "seconds_until_next_scan": rem_sec,
                "scan_count": self.scan_count,
                "long_count": self.long_count,
                "short_count": self.short_count,
                "last_status": self.last_status,
                "last_error": self.last_error,
                "tastytrade_connected": self.tastytrade_connected,
                "active_alerts_registered": self.active_alerts_registered,
                "market_hours": self.is_market_hours(),
                "market_tide": self.last_market_tide,
                "auto_deep_research": self.auto_deep_research,
                "max_auto_deep_per_day": self.max_auto_deep_per_day,
                "auto_deep_count_today": len(self.auto_deep_dispatched_today),
                "auto_deep_dispatched_today": sorted(list(self.auto_deep_dispatched_today)),
                "last_dispatched_ticker": self.last_dispatched_ticker,
            }

    def get_active_research_count(self) -> int:
        """Count currently running deep research jobs across threads, SQLite, and external processes.

        Reconciles stale RUNNING/QUEUED rows before counting: if a row's PID is
        dead (or never recorded) and the job started more than STALE_THRESHOLD
        ago, the row is marked FAILED so it no longer blocks the slot.
        """
        self._reconcile_stale_research_jobs()

        count = 0
        if hasattr(self, "_active_research_threads"):
            self._active_research_threads = [t for t in self._active_research_threads if t.is_alive()]
            count += len(self._active_research_threads)

        db_path = config.research_watch_db_path()
        if db_path.exists():
            try:
                import sqlite3
                with sqlite3.connect(str(db_path), timeout=5.0) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM active_research_jobs WHERE status IN ('RUNNING', 'QUEUED')"
                    ).fetchone()
                    if row and row[0] > 0:
                        count = max(count, int(row[0]))
            except Exception:
                pass

        try:
            import psutil
            external_procs = 0
            for proc in psutil.process_iter(["name", "cmdline"]):
                cmdline = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmdline)
                if "run_deep_research.py" in cmd_str and proc.pid != os.getpid():
                    external_procs += 1
            if external_procs > 0:
                count = max(count, external_procs)
        except Exception:
            pass

        return count

    def _reconcile_stale_research_jobs(self) -> None:
        """Mark RUNNING/QUEUED research jobs as FAILED when their process is
        dead and the job has exceeded STALE_THRESHOLD (30 min) since start.

        This prevents permanent slot starvation after a hard crash (OOM,
        kill -9, reboot) where the SQLite row survives but the process does
        not. Called at the start of get_active_research_count so every slot
        availability check sees accurate state.
        """
        db_path = config.research_watch_db_path()
        if not db_path.exists():
            return
        try:
            import sqlite3
            from datetime import datetime, timedelta, timezone
            now_utc = datetime.now(timezone.utc)
            stale_cutoff = now_utc - timedelta(minutes=30)
            with sqlite3.connect(str(db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT job_id, pid, started_at FROM active_research_jobs WHERE status IN ('RUNNING', 'QUEUED')"
                ).fetchall()
                for row in rows:
                    pid = row["pid"]
                    started_at_str = row["started_at"]
                    pid_alive = False
                    if pid is not None:
                        try:
                            import psutil
                            pid_alive = psutil.pid_exists(pid)
                        except Exception:
                            pid_alive = False
                    started_dt = None
                    if started_at_str:
                        try:
                            started_dt = datetime.fromisoformat(started_at_str)
                            if started_dt.tzinfo is None:
                                started_dt = started_dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            started_dt = None
                    is_stale = (not pid_alive) and (started_dt is None or started_dt < stale_cutoff)
                    if is_stale:
                        conn.execute(
                            "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ? WHERE job_id = ?",
                            ("Stale job: process dead and no activity for 30+ min", row["job_id"]),
                        )
                conn.commit()
        except Exception:
            pass

    def is_slot_available(self) -> bool:
        """Check if at least one deep research slot is free."""
        return self.get_active_research_count() < self.max_concurrent_slots

    def _is_job_active_in_db(self, sym: str) -> bool:
        """Check if ticker currently has an active RUNNING or QUEUED research job in SQLite."""
        db_path = config.research_watch_db_path()
        if not db_path.exists():
            return False
        try:
            import sqlite3
            with sqlite3.connect(str(db_path), timeout=5.0) as conn:
                c = conn.cursor()
                row = c.execute(
                    "SELECT job_id FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING', 'QUEUED')",
                    (sym.upper(),)
                ).fetchone()
                return bool(row)
        except Exception:
            return False

    def dispatch_candidate_research(
        self, sym: str, target_date: str, candidate_dict: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Dispatch research job asynchronously via Cockpit UI slot manager or background worker."""
        import sys
        sym_u = sym.upper().strip()
        if "run_ui" in sys.modules:
            try:
                run_ui = sys.modules["run_ui"]
                req = run_ui.ResearchRequest(ticker=sym_u, mode="full", date=target_date, force=False)
                res = run_ui.trigger_research(req)
                logger.info(f"🤖 [ContinuousScreener] Dispatched {sym_u} to Cockpit research manager: {res}")
                return True
            except Exception as e_ui:
                logger.warning(f"[ContinuousScreener] Could not dispatch via run_ui: {e_ui}")

        # Fallback to standalone background worker thread
        cand_payload = candidate_dict or {"symbol": sym_u, "priority_tier": "HIGH_PRIORITY", "priority_score": 75.0}

        def _bg_worker():
            try:
                from src.screener.schwab_pre_move_scan import run_autonomous_screener_pipeline
                run_autonomous_screener_pipeline(
                    [cand_payload],
                    auto_max=1,
                    run_deep=True,
                    date_str=target_date,
                    headless=True,
                )
            except Exception as e_bg:
                logger.error(f"[ContinuousScreener] Error in background autonomous pipeline for {sym_u}: {e_bg}")

        t = threading.Thread(target=_bg_worker, name=f"AutoDeep_{sym_u}", daemon=True)
        if not hasattr(self, "_active_research_threads"):
            self._active_research_threads = []
        self._active_research_threads.append(t)
        t.start()
        return True

    def rescore_recent_dossiers(self, target_date: str) -> List[Dict[str, Any]]:
        """B1/B2/B3: Re-evaluate dossiers from the trailing 10 sessions against live quotes.
        
        Overlays the live quote on stored levels and checks for mover triggers:
        - Price move >= 3.0% (UNMEASURED default) from dossier spot
        - Or price breaks above zone top
        Fires push alerts and re-queues movers for local re-triage / deep research bypassing cap.
        """
        logger.info(f"🔎 [ContinuousScreener] Checking trailing 10-session dossiers for movers at live price...")
        from src.clients.price_client import get_current_prices_batch, get_current_price
        from src.tracking.watch_manager import log_trigger_alert
        from src.tracking.alert_db import queue_for_research

        # Reset daily alerted set on date rollover
        with self._lock:
            if self._today_date != target_date:
                self._today_date = target_date
                self.movers_alerted_today = set()

        # Step 1: Discover recent dossiers across last 10 session dates
        base_dir = config.BASE_DIR / "data"
        recent_dates = []
        for d in (base_dir / "raw", base_dir / "triage"):
            if not d.exists():
                continue
            for dt_dir in d.iterdir():
                if dt_dir.is_dir() and len(dt_dir.name) == 10 and dt_dir.name <= target_date:
                    if dt_dir.name not in recent_dates:
                        recent_dates.append(dt_dir.name)
        recent_dates = sorted(recent_dates, reverse=True)[:10]

        dossiers_by_ticker: Dict[str, Dict[str, Any]] = {}
        for dt_str in reversed(recent_dates):  # oldest to newest so newest overwrites
            # Check raw and triage directories
            for sub_dir in (base_dir / "raw" / dt_str, base_dir / "triage" / dt_str):
                if not sub_dir.exists():
                    continue
                for th_path in sub_dir.glob("**/*_thesis.json"):
                    sym = th_path.name.replace("_thesis.json", "").upper()
                    try:
                        with open(th_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        dossiers_by_ticker[sym] = {"date": dt_str, "path": th_path, "data": data}
                    except Exception:
                        pass
                for tr_path in sub_dir.glob("**/*_triage.json"):
                    sym = tr_path.name.replace("_triage.json", "").upper()
                    if sym not in dossiers_by_ticker:
                        try:
                            with open(tr_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            dossiers_by_ticker[sym] = {"date": dt_str, "path": tr_path, "data": {"triage": data}}
                        except Exception:
                            pass

        if not dossiers_by_ticker:
            logger.info("ℹ️ [ContinuousScreener] No recent dossiers found in trailing 10 sessions.")
            return []

        tickers = list(dossiers_by_ticker.keys())
        logger.info(f"🔎 [ContinuousScreener] Found {len(tickers)} recent candidate dossiers to check.")

        # Batch-fetch live prices
        live_prices: Dict[str, float] = {}
        try:
            live_prices = get_current_prices_batch(tickers)
        except Exception as e_batch:
            logger.debug(f"[ContinuousScreener] Batch price fetch failed: {e_batch}")

        triggered_movers = []
        for sym, d_info in dossiers_by_ticker.items():
            live_p = live_prices.get(sym)
            if live_p is None or live_p <= 0:
                try:
                    live_p = get_current_price(sym)
                except Exception:
                    continue
            if live_p is None or live_p <= 0:
                continue

            record = d_info["data"]
            triage = record.get("triage", {}) if isinstance(record, dict) else {}
            llm_d = record.get("llm_data", {}) if isinstance(record, dict) else {}
            long_p = triage.get("long_plan") or {}

            dossier_spot = (
                triage.get("spot")
                or triage.get("price")
                or llm_d.get("price")
                or record.get("bar_spot")
                or record.get("live_spot")
            )
            if dossier_spot is None and isinstance(long_p, dict):
                z = long_p.get("zone")
                if isinstance(z, (list, tuple)) and len(z) >= 2 and z[0] is not None and z[1] is not None:
                    try:
                        dossier_spot = (float(z[0]) + float(z[1])) / 2.0
                    except (ValueError, TypeError):
                        pass

            if dossier_spot is None:
                # Check for sibling datawindow.json
                th_p = d_info.get("path")
                if th_p:
                    dw_p = th_p.parent / f"{sym}_datawindow.json"
                    if dw_p.exists():
                        try:
                            with open(dw_p, "r", encoding="utf-8") as f_dw:
                                dw_data = json.load(f_dw)
                            dossier_spot = dw_data.get("close") or dw_data.get("price")
                        except Exception:
                            pass

            if dossier_spot is None:
                continue
            try:
                dossier_spot = float(dossier_spot)
            except (ValueError, TypeError):
                continue
            if dossier_spot <= 0:
                continue

            move_pct = ((live_p - dossier_spot) / dossier_spot) * 100.0

            # Stored levels
            z = long_p.get("zone") or [None, None]
            e_high = z[1] if isinstance(z, (list, tuple)) and len(z) > 1 else long_p.get("entry_high")
            stop = long_p.get("stop")
            target_1 = long_p.get("target") or long_p.get("target_1")
            setup_lane = triage.get("setup_lane") or llm_d.get("setup_lane") or "MOMENTUM_BREAKOUT"
            lane_label = "MEASURED" if setup_lane in ("CODE20", "RR_SETUP", "RSI2") else "UNMEASURED"

            # Check mover conditions (Plan task B):
            # 1. Price moved >= 5.0% from dossier spot (5% placeholder until measurement run)
            # 2. Or price broke above zone top
            is_mover = False
            mover_reason = ""
            e_high_flt = None
            if e_high is not None:
                try:
                    e_high_flt = float(e_high)
                except (ValueError, TypeError):
                    pass

            if move_pct >= 5.0:
                is_mover = True
                mover_reason = f"+{move_pct:.1f}% from dossier spot (${dossier_spot:.2f})"
            elif e_high_flt is not None and live_p > e_high_flt:
                is_mover = True
                mover_reason = f"broke zone top (${e_high_flt:.2f})"

            if is_mover:
                triggered_movers.append({"symbol": sym, "move_pct": move_pct, "live_price": live_p, "reason": mover_reason})

                # Compute tactical R:R at live price
                rr_str = "N/A"
                stop_flt = None
                target_flt = None
                try:
                    if stop is not None:
                        stop_flt = float(stop)
                    if target_1 is not None:
                        target_flt = float(target_1)
                except (ValueError, TypeError):
                    pass

                if stop_flt is not None and target_flt is not None and live_p > stop_flt:
                    try:
                        live_rr = (target_flt - live_p) / (live_p - stop_flt)
                        rr_str = f"{live_rr:.2f}@mkt"
                    except Exception:
                        pass

                levels_str = ""
                if stop_flt is not None and target_flt is not None:
                    levels_str = f" | stop ${stop_flt:.2f} | T1 ${target_flt:.2f} | R:R at live price {rr_str}"

                msg = (
                    f"MOVING: {sym} +{move_pct:.1f}% since dossier, BUY 100 at market "
                    f"| now ${live_p:.2f}{levels_str} | lane {setup_lane} [{lane_label}]"
                )

                # Deduplicate push alerts per ticker per session
                dedup_key = f"{sym}_{target_date}"
                if dedup_key not in self.movers_alerted_today:
                    self.movers_alerted_today.add(dedup_key)
                    try:
                        log_trigger_alert(sym, "MOVING", msg, live_p)
                        logger.info(f"🚨 [ContinuousScreener] {msg}")
                    except Exception as e_alert:
                        logger.debug(f"[ContinuousScreener] Failed to log trigger alert: {e_alert}")

                    # B3: Re-queue the name for local re-triage and mid deep research, bypassing the daily cap
                    try:
                        queue_for_research(
                            symbol=sym,
                            date_str=target_date,
                            setup=f"MOVER_{setup_lane}",
                            source="mover_trigger",
                            reason=f"Live mover: {mover_reason} ({msg})",
                            force=True,
                        )
                        logger.info(f"📥 [ContinuousScreener] Re-queued mover {sym} for research (bypassing daily cap).")
                        # Dispatch into deep research slot if slot is free
                        if self.is_slot_available():
                            self.dispatch_candidate_research(
                                sym, target_date, candidate_dict={
                                    "symbol": sym,
                                    "priority_tier": "HIGH_PRIORITY",
                                    "priority_score": 85.0,
                                    "price": live_p,
                                    "side": "LONG",
                                    "setup": f"MOVER_{setup_lane}",
                                    "pb_funnel": True,
                                }
                            )
                    except Exception as e_q:
                        logger.debug(f"[ContinuousScreener] Failed to queue mover {sym}: {e_q}")

        return triggered_movers

    def evaluate_and_dispatch_deep_research(
        self,
        candidates: List[Dict[str, Any]],
        target_date: str,
    ) -> List[str]:
        """
        Evaluate high-priority coiled candidates and automatically dispatch into the
        Deep Research pipeline (strictly 1 in the slot) when slot is free.
        """
        if not self.auto_deep_research or not candidates:
            return []

        # Reset daily set on date rollover under lock
        with self._lock:
            if self._today_date != target_date:
                self._today_date = target_date
                self.auto_deep_dispatched_today = set()
            dispatched_today_snap = set(self.auto_deep_dispatched_today)

        # Detect already-completed tickers from reports/ directory (today + trailing 5 sessions) to prevent duplicate research
        reports_root = config.BASE_DIR / "reports"
        already_completed_recent = set()
        if reports_root.exists():
            date_dirs = sorted([d for d in reports_root.glob("202*") if d.is_dir()], reverse=True)[:5]
            for d in date_dirs:
                for f in d.glob("*_summary.md"):
                    t = f.name.replace("_summary.md", "").upper()
                    already_completed_recent.add(t)

        if self.max_auto_deep_per_day > 0:
            slots_left = self.max_auto_deep_per_day - len(dispatched_today_snap)
            if slots_left <= 0:
                logger.info(f"🤖 [ContinuousScreener] Auto Deep Research daily cap ({self.max_auto_deep_per_day}) reached for {target_date}.")
                return []
        else:
            slots_left = 999999

        eligible = []
        for c in candidates:
            sym = str(c.get("symbol") or c.get("Symbol") or c.get("Ticker") or "").upper().strip()
            if not sym or sym in already_completed_recent or sym in dispatched_today_snap:
                continue

            price = float(c.get("price") or 0.0)
            if price < 1.0:
                continue
            # Liquidity gate replaces flat price floor — a $12 stock with
            # institutional-grade spread/volume beats a $50 stock with none.
            tt = c.get("tastytrade") or {}
            liq_rating = tt.get("liquidity_rating") if tt.get("connected") else None
            avg_vol = float(c.get("volume") or 0.0)
            if liq_rating is not None:
                if liq_rating < 2 and avg_vol < 800_000:
                    continue
            elif avg_vol > 0 and avg_vol < 500_000:
                continue

            score = float(c.get("priority_score") or 0.0)
            tier = str(c.get("priority_tier") or "MONITOR")

            # Shared gate with run_autonomous_screener_pipeline so the two cannot drift:
            # PB funnel, screener intake, long side. Every no-PB band measures era-stable
            # NEGATIVE (MEDIUM -0.164R, score 50-65 -0.128R, MONITOR -0.151R) and
            # HIGH_PRIORITY's +0.417R is 99% PB with a -1R median -- a proxy-RR payoff artifact,
            # not tier skill. min_conviction_score is display-only.
            from src.screener.schwab_pre_move_scan import _dispatch_eligible
            ok, basis = _dispatch_eligible(c)
            if not ok:
                continue

            # Check if active job already running or queued in SQLite
            if self._is_job_active_in_db(sym):
                continue

            eligible.append(c)
            try:
                from src.tracking.alert_db import queue_for_research
                queue_for_research(
                    symbol=sym,
                    date_str=target_date,
                    setup=c.get("setup") or tier or "",
                    source="schwab_screener",
                    reason=f"Schwab screener candidate: {basis}, proxy_rr={c.get('proxy_rr')} (legacy {tier} score={score})",
                )
            except Exception as e_q:
                logger.debug(f"[ContinuousScreener] Failed to queue {sym} in research_queue: {e_q}")

        if not eligible:
            return []

        # Check if research slot is free before dispatching
        if not self.is_slot_available():
            active_cnt = self.get_active_research_count()
            logger.info(
                f"⏳ [ContinuousScreener] Deep research slot is OCCUPIED ({active_cnt}/{self.max_concurrent_slots} active). "
                f"Holding {len(eligible)} qualified candidate(s) until slot frees up."
            )
            return []

        # Sort: PB funnel first (for longs) / HIGH_PRIORITY (for shorts), then proxy/directional R:R.
        eligible.sort(
            key=lambda x: (
                bool(x.get("pb_funnel")) if str(x.get("side") or "LONG").upper() == "LONG" else (x.get("priority_tier") == "HIGH_PRIORITY"),
                float(x.get("proxy_rr") or x.get("short_rr", x.get("long_rr", 0.0)) or 0.0),
            ),
            reverse=True,
        )

        available_slots = max(0, self.max_concurrent_slots - self.get_active_research_count())
        take_n = min(available_slots, slots_left)
        selected = eligible[:take_n]
        dispatched_syms = []

        for p in selected:
            sym = str(p.get("symbol") or p.get("Symbol") or p.get("Ticker")).upper().strip()
            success = self.dispatch_candidate_research(sym, target_date, candidate_dict=p)
            if success:
                with self._lock:
                    self.auto_deep_dispatched_today.add(sym)
                    self.last_dispatched_ticker = sym
                    dispatched_count = len(self.auto_deep_dispatched_today)
                dispatched_syms.append(sym)
                logger.info(f"🚀 [ContinuousScreener] Auto-dispatched {sym} for Autonomous Deep Research ({dispatched_count}/{self.max_auto_deep_per_day} today).")

        return dispatched_syms


def start_continuous_screener_daemon(
    poll_interval: int = DEFAULT_SCAN_INTERVAL,
    top_n: int = 10,
    auto_alerts: bool = False,
    market_hours_only: bool = False,
    auto_deep_research: Optional[bool] = None,
    max_concurrent_slots: int = 3,
) -> ContinuousScreenerDaemon:
    """Start or retrieve the singleton continuous screener daemon."""
    global _daemon_instance
    if _daemon_instance is None or not _daemon_instance.is_alive():
        _daemon_instance = ContinuousScreenerDaemon(
            poll_interval=poll_interval,
            top_n=top_n,
            auto_alerts=auto_alerts,
            market_hours_only=market_hours_only,
            auto_deep_research=auto_deep_research,
            max_concurrent_slots=max_concurrent_slots,
        )
        _daemon_instance.start()
        logger.info("Continuous Screener Daemon successfully started.")
    return _daemon_instance


def stop_continuous_screener_daemon():
    """Stop the continuous screener daemon if active."""
    global _daemon_instance
    if _daemon_instance is not None:
        _daemon_instance.stop()
        _daemon_instance = None


def trigger_continuous_scan_now():
    """Trigger an immediate scan cycle on the active daemon."""
    global _daemon_instance
    if _daemon_instance is not None and _daemon_instance.is_alive():
        _daemon_instance.trigger_now()
        return True
    return False


def pause_continuous_screener() -> bool:
    """Pause the continuous screener daemon if active."""
    global _daemon_instance
    if _daemon_instance is not None:
        _daemon_instance.pause()
        return True
    return False


def resume_continuous_screener() -> bool:
    """Resume the continuous screener daemon if active."""
    global _daemon_instance
    if _daemon_instance is not None:
        _daemon_instance.resume()
        return True
    return False


def toggle_continuous_screener_pause() -> bool:
    """Toggle paused state of the continuous screener daemon. Returns new paused state."""
    global _daemon_instance
    if _daemon_instance is not None:
        return _daemon_instance.toggle_pause()
    return False


def get_continuous_screener_status() -> Dict[str, Any]:
    """Retrieve telemetry status for the continuous screener daemon."""
    global _daemon_instance
    if _daemon_instance is not None:
        return _daemon_instance.get_status()
    return {
        "running": False,
        "paused": False,
        "is_scanning": False,
        "last_status": "Daemon not started",
        "tastytrade_connected": False,
        "scan_count": 0,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Continuous Schwab 1000 Autonomous Screener & Research Daemon")
    parser.add_argument("--interval", type=int, default=DEFAULT_SCAN_INTERVAL, help="Scan interval in seconds (default 600)")
    parser.add_argument("--top", type=int, default=10, help="Top N candidates to track per side")
    parser.add_argument("--auto-deep", action="store_true", default=True, help="Automatically dispatch 1 qualified candidate into deep research when slot is free")
    parser.add_argument("--no-auto-deep", dest="auto_deep", action="store_false", help="Disable autonomous deep research")
    parser.add_argument("--max-deep", type=int, default=0, help="Max deep research runs per day (default: 0 = uncapped)")
    parser.add_argument("--max-slots", type=int, default=3, help="Max concurrent deep research slots (default 3)")
    parser.add_argument("--min-score", type=float, default=60.0, help="Minimum priority score for deep research")
    parser.add_argument("--market-hours-only", action="store_true", help="Only scan during market hours")
    parser.add_argument("--once", action="store_true", help="Run a single scan cycle and exit")
    parser.add_argument("--headless", action="store_true", help="Run Playwright in headless mode")
    args = parser.parse_args()

    if args.headless:
        os.environ["HEADLESS_SCRAPE"] = "1"
    os.environ["CONTINUOUS_MIN_CONVICTION"] = str(args.min_score)
    os.environ["CONTINUOUS_MAX_AUTO_DEEP"] = str(args.max_deep)

    daemon = ContinuousScreenerDaemon(
        poll_interval=args.interval,
        top_n=args.top,
        auto_alerts=False,
        market_hours_only=args.market_hours_only,
        auto_deep_research=args.auto_deep,
        max_concurrent_slots=args.max_slots,
    )

    if args.once:
        print(">> 🔄 Running single continuous screener cycle...")
        res = daemon.run_scan_cycle()
        print(f">> ✅ Cycle finished: {res}")
    else:
        print(f">> 🚀 Starting Continuous Screener Daemon (Interval: {args.interval}s, Auto-Deep: {args.auto_deep}, Max Slots: 1)...")
        try:
            daemon.run()
        except KeyboardInterrupt:
            print("\n>> 🛑 Stopping daemon...")
            daemon.stop()
