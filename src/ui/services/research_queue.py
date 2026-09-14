"""
Research Queue Manager & Sequential Worker Pipeline.
Manages concurrency slots (MAX=2), FIFO queueing, and live process inspection.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import config
from src.ui.state import (
    ACTIVE_RESEARCH_SUBPROCS,
    ACTIVE_RESEARCH_WORKERS,
    LOGS_DIR,
    MAX_CONCURRENT_RESEARCH,
    _QUEUE_DISPATCH_LOCK,
    append_log,
    get_db,
    init_db,
)

logger = logging.getLogger("ui_server")


def find_live_research_pid(ticker: str, job_id: Optional[str] = None) -> Optional[int]:
    """Fast scan of python processes only to see if a research or scrape process is actively running."""
    import psutil
    t_upper = ticker.strip().upper()
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = (proc.info.get('name') or '').lower()
                if not ('python' in name or 'powershell' in name):
                    continue
                cmdline = proc.cmdline()
                if not cmdline:
                    continue
                cmd_str = " ".join(cmdline).upper()
                if "RUN_DEEP_RESEARCH.PY" in cmd_str or "RUN_SWING_RESEARCH.PY" in cmd_str:
                    if job_id and job_id.upper() in cmd_str:
                        return proc.pid
                    if f"--TICKER {t_upper}" in cmd_str or f"-T {t_upper}" in cmd_str or f" {t_upper} " in cmd_str or cmd_str.endswith(f" {t_upper}"):
                        return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return None


def rehydrate_active_jobs():
    """Check running jobs in DB on startup and mark dead ones as FAILED unless reports exist or process is running."""
    import psutil
    init_db()
    try:
        with get_db() as conn:
            c = conn.cursor()
            running = c.execute("SELECT job_id, ticker, pid, target_date, started_at FROM active_research_jobs WHERE status = 'RUNNING'").fetchall()
            for r in running:
                pid = r["pid"]
                jid = r["job_id"]
                ticker_sym = r["ticker"]
                t_date = r["target_date"] or datetime.now().strftime("%Y-%m-%d")

                is_alive = False
                if pid and psutil.pid_exists(pid):
                    is_alive = True
                else:
                    live_pid = find_live_research_pid(ticker_sym, jid)
                    if live_pid:
                        is_alive = True
                        c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, jid))

                if not is_alive:
                    rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_summary.md"
                    arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_arbitration.md"
                    if rep_file.exists() or arb_file.exists():
                        new_status = "COMPLETED"
                        new_err = None
                    else:
                        new_status = "FAILED"
                        new_err = "Server restarted while job was running"
                    c.execute(
                        "UPDATE active_research_jobs SET status = ?, stage = CASE WHEN ? = 'COMPLETED' THEN 'DONE' ELSE 'ERROR' END, completed_at = ?, error_message = ? WHERE job_id = ?",
                        (new_status, new_status, datetime.now(timezone.utc).isoformat(), new_err, jid),
                    )
            conn.commit()
    except Exception as e:
        logger.warning(f"Error rehydrating jobs from DB: {e}")


def get_active_research_count() -> int:
    """Accurately count active research jobs across threads, subprocesses, and running OS processes."""
    import psutil
    count = 0
    with get_db() as conn:
        c = conn.cursor()
        running_rows = c.execute("SELECT job_id, ticker, pid FROM active_research_jobs WHERE status = 'RUNNING'").fetchall()
        for r in running_rows:
            jid = r["job_id"]
            thread = ACTIVE_RESEARCH_WORKERS.get(jid)
            if thread is not None and thread.is_alive():
                count += 1
            elif jid in ACTIVE_RESEARCH_SUBPROCS and ACTIVE_RESEARCH_SUBPROCS[jid].poll() is None:
                count += 1
            elif r["pid"] and psutil.pid_exists(r["pid"]):
                count += 1
            elif find_live_research_pid(r["ticker"], jid):
                count += 1
    return count


def dispatch_next_queued_job():
    """Pick next QUEUED job from SQLite in FIFO order and run it if active slots < MAX_CONCURRENT_RESEARCH."""
    with _QUEUE_DISPATCH_LOCK:
        active_count = get_active_research_count()
        slots_available = MAX_CONCURRENT_RESEARCH - active_count
        if slots_available <= 0:
            return

        with get_db() as conn:
            c = conn.cursor()
            queued_jobs = c.execute(
                "SELECT job_id, ticker, mode, target_date FROM active_research_jobs WHERE status = 'QUEUED' ORDER BY started_at ASC LIMIT ?",
                (slots_available,)
            ).fetchall()

            for job in queued_jobs:
                jid = job["job_id"]
                tkr = job["ticker"]
                m = job["mode"]
                dt = job["target_date"]
                c.execute(
                    "UPDATE active_research_jobs SET status = 'RUNNING', stage = 'STARTING', started_at = ? WHERE job_id = ?",
                    (datetime.now(timezone.utc).isoformat(), jid)
                )
                conn.commit()

                worker_thread = threading.Thread(
                    target=run_research_worker,
                    args=(jid, tkr, m, dt, True),
                    daemon=True
                )
                ACTIVE_RESEARCH_WORKERS[jid] = worker_thread
                worker_thread.start()
                append_log(f"⚡ [Queue Dispatcher] Dispatched queued research for {tkr} to open slot (Job ID: {jid}).")


def run_research_worker(job_id: str, ticker: str, mode: str, date: Optional[str] = None, force: bool = False):
    """Worker thread running sequential research pipeline with SQLite persistence and memory-leak protection."""
    ticker_u = ticker.strip().upper()
    py_exe = sys.executable
    log_file_path = LOGS_DIR / f"{job_id}.log"

    def _log_both(msg: str):
        append_log(f"[{ticker_u}] {msg}")
        try:
            with open(log_file_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    _log_both(f"🚀 Starting Research Job {job_id} for {ticker_u} [Mode: {mode}, Date: {date or 'auto'}, Force: {force}]")
    current_subproc = None

    try:
        # Step 1: Scrape
        if mode in ("full", "scrape_only", "scrape_deep"):
            with get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET stage = 'SCRAPING', status = 'RUNNING' WHERE job_id = ?", (job_id,))
                conn.commit()
            _log_both(f"📸 [1/3] Scraping TradingView Charts & Data Window...")
            cmd = [py_exe, "run_swing_research.py"]
            if date and date.strip():
                cmd.append(date.strip())
            cmd.extend(["--ticker", ticker_u])
            if force:
                cmd.append("--force")
            current_subproc = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ACTIVE_RESEARCH_SUBPROCS[job_id] = current_subproc
            with get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (current_subproc.pid, job_id))
                conn.commit()
            for line in current_subproc.stdout:
                l_str = line.strip()
                if l_str:
                    _log_both(l_str)
            current_subproc.wait()
            if current_subproc.returncode != 0:
                raise RuntimeError(f"Scrape phase failed with exit code {current_subproc.returncode}")

        # Step 2: Deep Research (Model A & Model B Parallel + PM Arbitration)
        date_to_use = date.strip() if (date and date.strip()) else None
        if not date_to_use:
            raw_base = config.BASE_DIR / "data" / "raw"
            if raw_base.exists():
                for d_cand in sorted(raw_base.iterdir(), reverse=True):
                    if d_cand.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d_cand.name):
                        if (d_cand / ticker_u).exists() or (d_cand / f"{ticker_u}_datawindow.json").exists():
                            date_to_use = d_cand.name
                            break
        if not date_to_use:
            date_to_use = datetime.now().strftime("%Y-%m-%d")

        if mode in ("full", "deep_only", "scrape_deep"):
            with get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET stage = 'DEEP_RESEARCH', status = 'RUNNING' WHERE job_id = ?", (job_id,))
                conn.commit()
            _log_both(f"🔬 [2/3] Running Agentic Deep Research (Pine Gem + Independent Gem + PM Arbitration)...")
            cmd = [py_exe, "run_deep_research.py"]
            if date_to_use:
                cmd.append(date_to_use)
            cmd.extend(["--ticker", ticker_u, "--job-id", job_id])
            current_subproc = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ACTIVE_RESEARCH_SUBPROCS[job_id] = current_subproc
            with get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (current_subproc.pid, job_id))
                conn.commit()
            for line in current_subproc.stdout:
                l_str = line.strip()
                if l_str:
                    _log_both(l_str)
            current_subproc.wait()
            if current_subproc.returncode != 0:
                raise RuntimeError(f"Deep research phase failed with exit code {current_subproc.returncode}")

            # Verify reports exist before declaring success
            rep_chk_date = date_to_use or datetime.now().strftime("%Y-%m-%d")
            rep_file = config.BASE_DIR / "reports" / rep_chk_date / f"{ticker_u}_summary.md"
            arb_file = config.BASE_DIR / "reports" / rep_chk_date / f"{ticker_u}_arbitration.md"
            if not rep_file.exists() and not arb_file.exists():
                raise RuntimeError(f"Deep research completed but generated no report files in reports/{rep_chk_date}/")

        # Step 3: Sync Watch Alerts in Background (Targeted to ticker)
        if mode in ("full", "scrape_deep", "deep_only"):
            _log_both(f"🔔 [3/3] Syncing Watch Levels & Tastytrade Cloud Alerts for {ticker_u}...")
            def _bg_watch_sync(t_sym, d_sync):
                try:
                    sync_cmd = [py_exe, "run_watch_alerts.py", "--sync", "--once", "--ticker", t_sym]
                    if d_sync:
                        sync_cmd.extend(["--date", d_sync])
                    sync_p = subprocess.Popen(
                        sync_cmd,
                        cwd=str(config.BASE_DIR),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    for line in sync_p.stdout:
                        l = line.strip()
                        if l:
                            _log_both(l)
                    sync_p.wait()
                except Exception as e_w:
                    _log_both(f"Watch sync background notice: {e_w}")

            threading.Thread(target=_bg_watch_sync, args=(ticker_u, date_to_use), daemon=True).start()

        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE active_research_jobs SET status = 'COMPLETED', stage = 'DONE', completed_at = ? WHERE job_id = ?",
                (datetime.now(timezone.utc).isoformat(), job_id)
            )
            conn.commit()
        _log_both(f"✅ Deep Research Complete for {ticker_u}!")

    except Exception as e:
        _log_both(f"❌ Exception in research worker: {e}")
        with get_db() as conn:
            c = conn.cursor()
            existing = c.execute("SELECT status FROM active_research_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing and existing["status"] in ("KILLED", "COMPLETED"):
                _log_both(f"Job {job_id} already marked {existing['status']}, skipping error overwrite.")
            else:
                c.execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ?, completed_at = ? WHERE job_id = ?",
                    (str(e), datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
    finally:
        ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
        ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)
        dispatch_next_queued_job()
