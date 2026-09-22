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
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src import config
from src.clients.schwab_client import get_schwab_client
from src.clients.tastytrade_client import TastytradeClient

logger = logging.getLogger("continuous_screener")

# Default scan interval in seconds (default 5 minutes)
DEFAULT_SCAN_INTERVAL = int(os.getenv("CONTINUOUS_SCREENER_INTERVAL", "300"))

_daemon_instance: Optional[ContinuousScreenerDaemon] = None


def enrich_candidates_with_tastytrade(
    candidates: List[Dict[str, Any]],
    auto_alerts: bool = True,
) -> int:
    """Enrich candidates with Tastytrade IV metrics and auto-register 24/7 cloud price alerts."""
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
            prio_score = float(p.get("priority_score", 0.0))
            prio_tier = str(p.get("priority_tier", "MONITOR"))
            is_qualified = prio_tier in ("HIGH_PRIORITY", "MEDIUM_PRIORITY") or prio_score >= 55.0

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
        auto_alerts: bool = True,
        market_hours_only: Optional[bool] = None,
        auto_deep_research: Optional[bool] = None,
        max_concurrent_slots: int = 1,
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
        self.max_auto_deep_per_day = int(os.getenv("CONTINUOUS_MAX_AUTO_DEEP", "3"))
        # Shared with run_autonomous_screener_pipeline via config so both gates agree.
        self.min_conviction_score = config.SCREENER_MIN_CONVICTION
        self.max_concurrent_slots = max(1, int(os.getenv("CONTINUOUS_MAX_CONCURRENT_SLOTS", str(max_concurrent_slots))))
        self._active_research_threads: List[threading.Thread] = []
        self.running = True
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

            # Execute periodic or triggered scan cycle
            self.run_scan_cycle()

        logger.info("🛑 [ContinuousScreener] Background daemon terminated.")

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

        db_path = config.BASE_DIR / "data" / "research_watch.db"
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
        db_path = config.BASE_DIR / "data" / "research_watch.db"
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
        db_path = config.BASE_DIR / "data" / "research_watch.db"
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

        # Reset daily set on date rollover
        if self._today_date != target_date:
            self._today_date = target_date
            self.auto_deep_dispatched_today = set()

        # Seed already-completed tickers from reports/ directory
        reports_dir = config.BASE_DIR / "reports" / target_date
        if reports_dir.exists():
            for f in reports_dir.glob("*_summary.md"):
                t = f.name.replace("_summary.md", "").upper()
                self.auto_deep_dispatched_today.add(t)

        slots_left = self.max_auto_deep_per_day - len(self.auto_deep_dispatched_today)
        if slots_left <= 0:
            logger.info(f"🤖 [ContinuousScreener] Auto Deep Research daily cap ({self.max_auto_deep_per_day}) reached for {target_date}.")
            return []

        eligible = []
        for c in candidates:
            sym = str(c.get("symbol") or c.get("Symbol") or c.get("Ticker") or "").upper().strip()
            if not sym or sym in self.auto_deep_dispatched_today:
                continue

            price = float(c.get("price") or 0.0)
            if price < 15.0:
                continue

            score = float(c.get("priority_score") or 0.0)
            tier = str(c.get("priority_tier") or "MONITOR")
            if tier != "HIGH_PRIORITY" and score < self.min_conviction_score:
                continue

            # Options liquidity check if Tastytrade is available
            tt = c.get("tastytrade") or {}
            if tt.get("connected"):
                liq = tt.get("liquidity_rating") or 1
                if liq < 2 and float(c.get("volume") or 0.0) < 800_000:
                    continue

            # Check if active job already running or queued in SQLite
            if self._is_job_active_in_db(sym):
                continue

            eligible.append(c)

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

        # Sort: HIGH_PRIORITY first, highest score, highest R:R
        eligible.sort(
            key=lambda x: (
                x.get("priority_tier") == "HIGH_PRIORITY",
                float(x.get("priority_score", 0.0)),
                float(x.get("long_rr", x.get("short_rr", 0.0))),
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
                self.auto_deep_dispatched_today.add(sym)
                self.last_dispatched_ticker = sym
                dispatched_syms.append(sym)
                logger.info(f"🚀 [ContinuousScreener] Auto-dispatched {sym} for Autonomous Deep Research ({len(self.auto_deep_dispatched_today)}/{self.max_auto_deep_per_day} today).")

        return dispatched_syms


def start_continuous_screener_daemon(
    poll_interval: int = DEFAULT_SCAN_INTERVAL,
    top_n: int = 10,
    auto_alerts: bool = True,
    market_hours_only: bool = False,
    auto_deep_research: Optional[bool] = None,
    max_concurrent_slots: int = 1,
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


def get_continuous_screener_status() -> Dict[str, Any]:
    """Retrieve telemetry status for the continuous screener daemon."""
    global _daemon_instance
    if _daemon_instance is not None:
        return _daemon_instance.get_status()
    return {
        "running": False,
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
    parser.add_argument("--max-deep", type=int, default=3, help="Max deep research runs per day")
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
        auto_alerts=True,
        market_hours_only=args.market_hours_only,
        auto_deep_research=args.auto_deep,
        max_concurrent_slots=1,
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
