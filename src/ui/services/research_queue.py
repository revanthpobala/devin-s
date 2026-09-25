"""
Research Queue Manager & Sequential Worker Pipeline.
Manages concurrency slots (MAX=2), FIFO queueing, and live process inspection.
"""

from __future__ import annotations

import collections
import logging
import os
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import config
from src.tracking.alert_db import update_research_status
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


def _watch_orphaned_job(jid: str, ticker_sym: str, pid: int, t_date: str):
    """Monitor an alive process that was orphaned by UI restart until completion."""
    import psutil
    import time
    try:
        proc = psutil.Process(pid)
        while proc.is_running():
            time.sleep(5)
    except (psutil.NoSuchProcess, Exception):
        pass

    rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_summary.md"
    arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_arbitration.md"
    new_status = "COMPLETED" if (rep_file.exists() or arb_file.exists()) else "FAILED"
    new_err = None if new_status == "COMPLETED" else "Process exited without generating reports"
    try:
        with get_db() as conn:
            conn.cursor().execute(
                "UPDATE active_research_jobs SET status = ?, stage = CASE WHEN ? = 'COMPLETED' THEN 'DONE' ELSE 'ERROR' END, completed_at = ?, error_message = ? WHERE job_id = ?",
                (new_status, new_status, datetime.now(timezone.utc).isoformat(), new_err, jid),
            )
            conn.commit()
        logger.info(f"⚡ [Queue Recovery] Orphaned job {jid} ({ticker_sym}) resolved to {new_status}.")
    except Exception as e:
        logger.warning(f"Failed updating orphaned job {jid}: {e}")
    dispatch_next_queued_job()


def rehydrate_active_jobs():
    """Check running jobs in DB on startup. Re-attach to alive jobs instead of killing them."""
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
                effective_pid = None
                if pid and psutil.pid_exists(pid):
                    is_alive = True
                    effective_pid = pid
                else:
                    live_pid = find_live_research_pid(ticker_sym, jid)
                    if live_pid:
                        is_alive = True
                        effective_pid = live_pid
                        c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, jid))

                if is_alive and effective_pid:
                    # Spawn watchdog thread to re-attach and await process exit
                    logger.info(f"⚡ [Queue Recovery] Re-attaching to running research job {jid} for {ticker_sym} (PID {effective_pid}).")
                    watcher = threading.Thread(
                        target=_watch_orphaned_job,
                        args=(jid, ticker_sym, effective_pid, t_date),
                        daemon=True,
                    )
                    watcher.start()
                else:
                    rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_summary.md"
                    arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_arbitration.md"
                    if rep_file.exists() or arb_file.exists():
                        new_status = "COMPLETED"
                        new_err = None
                    else:
                        # Check heartbeat: only mark FAILED after stale heartbeat, NOT on server restart
                        stale_seconds = int(os.getenv("RESEARCH_HEARTBEAT_TIMEOUT_S", "600"))
                        log_file_p = Path(r["log_file"]) if r.get("log_file") else None
                        last_active = None
                        if log_file_p and log_file_p.exists():
                            last_active = datetime.fromtimestamp(log_file_p.stat().st_mtime, tz=timezone.utc)
                        elif r.get("started_at"):
                            try:
                                last_active = datetime.fromisoformat(r["started_at"])
                            except Exception:
                                pass

                        is_stale = True
                        if last_active:
                            elapsed = (datetime.now(timezone.utc) - last_active).total_seconds()
                            if elapsed < stale_seconds:
                                is_stale = False

                        if is_stale:
                            new_status = "FAILED"
                            new_err = f"Job failed: process exited and heartbeat stale (> {stale_seconds}s)"
                            c.execute(
                                "UPDATE active_research_jobs SET status = ?, stage = 'ERROR', completed_at = ?, error_message = ? WHERE job_id = ?",
                                (new_status, datetime.now(timezone.utc).isoformat(), new_err, jid),
                            )
                        else:
                            logger.info(f"⚡ [Queue Recovery] Preserving RUNNING state for job {jid} ({ticker_sym}) — heartbeat is not stale.")
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


# Sub-stage markers we watch for in the deep-research subprocess stream
_STAGE_MARKERS = [
    (re.compile(r"\[Debate\]|debate|Bull.*Bear", re.I), "Debate"),
    (re.compile(r"Pass 2|pass2|Gemini|Pine Gem|Model A", re.I), "Pass 2 — Pine Gem"),
    (re.compile(r"Independent|independent_gem|Model B|IND", re.I), "Pass 2-IND — Independent"),
    (re.compile(r"Arbitration|arbitration|PM.*Judge|Ponytail", re.I), "Arbitration — PM Judge"),
    (re.compile(r"Watch.*sync|watch_alerts|Tastytrade.*alert", re.I), "Watch Sync"),
]


def _detect_stage_detail(line: str) -> Optional[str]:
    for pattern, label in _STAGE_MARKERS:
        if pattern.search(line):
            return label
    return None


def run_research_worker(job_id: str, ticker: str, mode: str, date: Optional[str] = None, force: bool = False):
    """Worker thread running sequential research pipeline with SQLite persistence and memory-leak protection."""
    init_db()
    ticker_u = ticker.strip().upper()
    py_exe = sys.executable
    log_file_path = LOGS_DIR / f"{job_id}.log"
    recent_lines: collections.deque[str] = collections.deque(maxlen=25)

    try:
        update_research_status(ticker_u, date or datetime.now().strftime("%Y-%m-%d"), "DEEP_RUNNING")
    except Exception:
        pass

    def _log_both(msg: str):
        append_log(f"[{ticker_u}] {msg}")
        recent_lines.append(msg)
        try:
            with open(log_file_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def _set_stage_detail(detail: str):
        try:
            with get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET stage_detail = ? WHERE job_id = ?",
                    (detail, job_id),
                )
                conn.commit()
        except Exception:
            pass

    # Idle timeout: no output for this many seconds → kill (Playwright/LLM hang guard).
    # The hard cap must exceed the longest legitimate silent run. A local-GPU deep-research
    # pass ingests a ~550k-char prompt and can take 12-18 min to emit its FIRST output token,
    # while debate + two model passes + arbitration run sequentially with long generation gaps.
    IDLE_TIMEOUT_SEC = int(os.getenv("RESEARCH_IDLE_TIMEOUT_SEC", "2700"))  # 45 min hard cap

    def _run_subproc(cmd: list[str], phase_label: str) -> None:
        """Run a subprocess, stream output to log, track sub-stages, raise with tail on failure.
        Uses a reader thread + Event so we can detect hangs on Windows (no select on pipes)."""
        import time as _time
        import queue as _queue
        nonlocal current_subproc
        # Propagate the job id so child scripts can derive a stable, per-job Chrome profile
        # slot (run_swing_research.py) — prevents two concurrent scrape jobs from colliding on
        # the same user-data-dir.
        env = os.environ.copy()
        env["RESEARCH_JOB_ID"] = job_id
        current_subproc = subprocess.Popen(
            cmd,
            cwd=str(config.BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        ACTIVE_RESEARCH_SUBPROCS[job_id] = current_subproc
        with get_db() as conn:
            conn.cursor().execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (current_subproc.pid, job_id))
            conn.commit()

        line_q: _queue.Queue = _queue.Queue()
        last_detail = None

        def _reader():
            try:
                for ln in current_subproc.stdout:
                    line_q.put(ln)
            except Exception:
                pass
            finally:
                line_q.put(None)  # sentinel

        reader_t = threading.Thread(target=_reader, daemon=True)
        reader_t.start()

        last_output_ts = _time.monotonic()
        while True:
            try:
                ln = line_q.get(timeout=30)
            except _queue.Empty:
                # No line for 30s — check if process is still alive and if we've hit idle timeout.
                # A healthy local-GPU inference emits one log line per completed request, so a
                # long single generation (e.g. synthesizing the final report from a ~550k-char
                # prompt) produces no lines for many minutes. The hard cap below is therefore set
                # well above the longest legitimate silent run (see IDLE_TIMEOUT_SEC).
                if current_subproc.poll() is not None:
                    break  # process exited, drain remaining
                elapsed_idle = _time.monotonic() - last_output_ts
                if elapsed_idle >= IDLE_TIMEOUT_SEC:
                    current_subproc.kill()
                    current_subproc.wait()
                    tail = "\n".join(list(recent_lines)[-20:])
                    raise RuntimeError(
                        f"{phase_label} timed out after {IDLE_TIMEOUT_SEC // 60} min with no output.\n"
                        f"Process killed. Last 20 lines before hang:\n{tail}"
                    )
                continue

            if ln is None:
                break  # reader thread finished (pipe closed)
            last_output_ts = _time.monotonic()
            l_str = ln.strip()
            if not l_str:
                continue
            _log_both(l_str)
            detail = _detect_stage_detail(l_str)
            if detail and detail != last_detail:
                last_detail = detail
                _set_stage_detail(detail)

        # Drain any remaining lines in the queue
        while not line_q.empty():
            ln = line_q.get_nowait()
            if ln is None:
                break
            l_str = ln.strip()
            if l_str:
                _log_both(l_str)

        current_subproc.wait()
        if current_subproc.returncode != 0:
            tail = "\n".join(list(recent_lines)[-20:])
            raise RuntimeError(
                f"{phase_label} failed with exit code {current_subproc.returncode}\n--- last 20 lines ---\n{tail}"
            )

    _log_both(f"🚀 Starting Research Job {job_id} for {ticker_u} [Mode: {mode}, Date: {date or 'auto'}, Force: {force}]")
    current_subproc = None

    try:
        # Step 1: Scrape
        if mode in ("full", "scrape_only", "scrape_deep"):
            with get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET stage = 'SCRAPING', status = 'RUNNING', stage_detail = 'Scraping TradingView' WHERE job_id = ?",
                    (job_id,),
                )
                conn.commit()
            _log_both(f"📸 [1/3] Scraping TradingView Charts & Data Window (Headless)...")
            cmd = [py_exe, "run_swing_research.py"]
            if date and date.strip():
                cmd.append(date.strip())
            cmd.extend(["--ticker", ticker_u, "--headless"])
            if force:
                cmd.append("--force")
            _run_subproc(cmd, "Scrape phase")

        # Step 1b: Triage pass for mode in ("full", "scrape_deep", "deep_only")
        proceed_to_deep = True
        if mode in ("full", "scrape_deep", "deep_only") and not force:
            _log_both(f"⚖️ [1b/3] Running Local Triage...")
            triage_cmd = [py_exe, "run_local_research.py"]
            if date and date.strip():
                triage_cmd.append(date.strip())
            triage_cmd.extend(["--ticker", ticker_u])
            _run_subproc(triage_cmd, "Triage phase")

            # Check triage verdict and send_for_deep_research flag
            t_date = date.strip() if (date and date.strip()) else datetime.now().strftime("%Y-%m-%d")
            th_path = config.BASE_DIR / "data" / "triage" / t_date / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_thesis.json"
            if not th_path.exists():
                th_path = config.BASE_DIR / "data" / "triage" / t_date / "force" / ticker_u / f"{ticker_u}_thesis.json"
            if not th_path.exists():
                th_path = config.BASE_DIR / "data" / "raw" / t_date / ticker_u / f"{ticker_u}_thesis.json"
            triage_pass = False
            if th_path.exists():
                try:
                    is_forced = "force" in th_path.parts
                    th_json = json.loads(th_path.read_text(encoding="utf-8"))
                    tr = th_json.get("triage") or {}
                    v = tr.get("triage") if isinstance(tr, dict) else str(tr)
                    send_flag = bool(th_json.get("send_for_deep_research") or (isinstance(tr, dict) and tr.get("send_for_deep_research")) or (isinstance(th_json.get("llm_data"), dict) and th_json["llm_data"].get("send_for_deep_research")))
                    triage_pass = is_forced or ((v == "PASS") and send_flag)
                except Exception:
                    pass
            if not triage_pass:
                _log_both(f"⏹️ [Triage Gate] {ticker_u} did not qualify (PASS + send_for_deep_research). Deep research skipped to preserve slots.")
                proceed_to_deep = False

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

        if proceed_to_deep and mode in ("full", "deep_only", "scrape_deep"):
            with get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET stage = 'DEEP_RESEARCH', status = 'RUNNING', stage_detail = 'Starting' WHERE job_id = ?",
                    (job_id,),
                )
                conn.commit()
            _log_both(f"🔬 [2/3] Running Agentic Deep Research (Pine Gem + Independent Gem + PM Arbitration)...")
            cmd = [py_exe, "run_deep_research.py"]
            if date_to_use:
                cmd.append(date_to_use)
            cmd.extend(["--ticker", ticker_u, "--job-id", job_id])
            _run_subproc(cmd, "Deep research phase")

            # Verify reports exist before declaring success
            rep_chk_date = date_to_use or datetime.now().strftime("%Y-%m-%d")
            rep_file = config.BASE_DIR / "reports" / rep_chk_date / f"{ticker_u}_summary.md"
            arb_file = config.BASE_DIR / "reports" / rep_chk_date / f"{ticker_u}_arbitration.md"
            if not rep_file.exists() and not arb_file.exists():
                raise RuntimeError(f"Deep research completed but generated no report files in reports/{rep_chk_date}/")

        # Step 3: Sync Watch Alerts in Background (Targeted to ticker)
        if proceed_to_deep and mode in ("full", "scrape_deep", "deep_only"):
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
                    if sync_p.returncode != 0:
                        _log_both(f"⚠️ Watch sync exited with code {sync_p.returncode} — Tastytrade alerts may not have registered.")
                except Exception as e_w:
                    _log_both(f"⚠️ Watch sync error: {e_w}")

            threading.Thread(target=_bg_watch_sync, args=(ticker_u, date_to_use), daemon=True).start()

        with get_db() as conn:
            final_stage = "DONE" if proceed_to_deep else "LOCAL_DONE"
            final_detail = None if proceed_to_deep else "Local triage complete; deep research skipped by triage gate"
            conn.cursor().execute(
                "UPDATE active_research_jobs SET status = 'COMPLETED', stage = ?, stage_detail = ?, completed_at = ? WHERE job_id = ?",
                (final_stage, final_detail, datetime.now(timezone.utc).isoformat(), job_id)
            )
            conn.commit()

        # Update lifecycle status in research_queue based on gate status
        is_gate_pass = False
        try:
            with get_db() as c_db:
                row = c_db.cursor().execute(
                    "SELECT gate_status FROM suggestions WHERE LOWER(ticker) = ? AND date >= date('now', '-3 days') ORDER BY id DESC LIMIT 1",
                    (ticker_u.lower(),)
                ).fetchone()
                if row and row["gate_status"] == "PASS":
                    is_gate_pass = True
        except Exception:
            pass

        final_status = ("DEEP_DONE_PASS" if is_gate_pass else "DEEP_DONE_REJECT") if proceed_to_deep else "LOCAL_DONE"
        try:
            update_research_status(ticker_u, date_to_use or datetime.now().strftime("%Y-%m-%d"), final_status)
        except Exception:
            pass

        if proceed_to_deep:
            _log_both(f"✅ Deep Research Complete for {ticker_u}!")
        else:
            _log_both(f"✅ Local Triage Complete for {ticker_u} (Deep Research skipped by triage gate).")

    except Exception as e:
        tb_str = traceback.format_exc()
        _log_both(f"❌ Exception in research worker: {e}")
        _log_both(tb_str)
        try:
            update_research_status(ticker_u, date or datetime.now().strftime("%Y-%m-%d"), "DEEP_DONE_REJECT")
        except Exception:
            pass
        with get_db() as conn:
            c = conn.cursor()
            existing = c.execute("SELECT status FROM active_research_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing and existing["status"] in ("KILLED", "COMPLETED"):
                _log_both(f"Job {job_id} already marked {existing['status']}, skipping error overwrite.")
            else:
                c.execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ?, completed_at = ? WHERE job_id = ?",
                    (f"{str(e)}\n{tb_str}", datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
    finally:
        ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
        ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)
        dispatch_next_queued_job()
