"""
System status, hardware telemetry, process monitoring, log buffers, and ticker discovery.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import config
from src.ui.services.daemon_manager import find_running_tracker_pid
from src.ui.state import (
    LOG_BUFFER,
    LOGS_DIR,
    POSITIONS_FILE,
    RESEARCH_STATE,
    get_db,
)

router = APIRouter(tags=["status"])

_COMPANY_NAMES_MAP: Optional[Dict[str, str]] = None


def get_company_names_dict() -> Dict[str, str]:
    global _COMPANY_NAMES_MAP
    if _COMPANY_NAMES_MAP is not None:
        return _COMPANY_NAMES_MAP
    cache_file = config.BASE_DIR / "web" / "static" / "data" / "company_names.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _COMPANY_NAMES_MAP = json.load(f)
                return _COMPANY_NAMES_MAP
        except Exception:
            pass
    _COMPANY_NAMES_MAP = {}
    return _COMPANY_NAMES_MAP


def _get_live_vix() -> Optional[float]:
    """Fetch live VIX spot price with fast caching."""
    try:
        from src.clients.price_client import get_realtime_price
        p = get_realtime_price("^VIX")
        if p and p > 0:
            return round(p, 2)
    except Exception:
        pass
    return None


def _get_gpu_stats() -> List[Dict[str, Any]]:
    """Query nvidia-smi for real-time VRAM usage and temperature."""
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu",
            "--format=csv,noheader,nounits"
        ]
        out = subprocess.check_output(cmd, encoding="utf-8", timeout=2.0)
        gpus = []
        for line in out.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 7:
                total = float(parts[2])
                used = float(parts[3])
                pct = round((used / total) * 100, 1) if total > 0 else 0
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "total_mb": total,
                    "used_mb": used,
                    "free_mb": float(parts[4]),
                    "pct_used": pct,
                    "temp_c": int(parts[5]),
                    "util_pct": int(parts[6]),
                })
        return gpus
    except Exception:
        return []


def _get_active_processes() -> List[Dict[str, Any]]:
    """Return live python background processes related to trading."""
    import psutil
    active = []
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'cpu_percent', 'memory_percent']):
            try:
                name = (proc.info.get('name') or '').lower()
                if not ('python' in name or 'powershell' in name):
                    continue
                cmdline = proc.cmdline()
                if not cmdline:
                    continue
                cmd_str = " ".join(cmdline)
                if any(x in cmd_str for x in ("run_ui.py", "run_deep_research.py", "run_swing_research.py", "main.py", "run_watch_alerts.py", "continuous_screener_daemon.py")):
                    active.append({
                        "pid": proc.pid,
                        "name": proc.info['name'],
                        "cmd": cmd_str,
                        "cpu": proc.cpu_percent(),
                        "mem": round(proc.memory_percent(), 1),
                        "uptime": int(datetime.now().timestamp() - proc.create_time())
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return active


@router.get("/api/status")
def get_system_status():
    """System heartbeat, market hours, GPU VRAM, active tasks, LLM health, orchestrator status, and active counts."""
    from src.ui.state import TRACKER_PROCESS
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    is_weekday = now_et.weekday() < 5

    rth_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    rth_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    pre_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    post_close = now_et.replace(hour=20, minute=0, second=0, microsecond=0)

    if not is_weekday:
        market_phase = "WEEKEND_CLOSED"
        market_open = False
        market_status_text = "🔴 MARKET CLOSED (Weekend)"
    elif rth_open <= now_et <= rth_close:
        market_phase = "REGULAR_OPEN"
        market_open = True
        market_status_text = f"🟢 REGULAR MARKET OPEN ({now_mt.strftime('%I:%M %p')} MT / {now_et.strftime('%I:%M %p')} ET)"
    elif rth_close < now_et <= post_close:
        market_phase = "AFTER_HOURS"
        market_open = False
        market_status_text = f"🟡 AFTER-HOURS ({now_mt.strftime('%I:%M %p')} MT / {now_et.strftime('%I:%M %p')} ET)"
    elif pre_open <= now_et < rth_open:
        market_phase = "PRE_MARKET"
        market_open = False
        market_status_text = f"🟠 PRE-MARKET ({now_mt.strftime('%I:%M %p')} MT / {now_et.strftime('%I:%M %p')} ET)"
    else:
        market_phase = "OVERNIGHT_CLOSED"
        market_open = False
        market_status_text = f"🔴 MARKET CLOSED ({now_mt.strftime('%I:%M %p')} MT)"

    # Local LLM check (port 8000)
    llm_online = False
    try:
        import requests
        r = requests.get("http://127.0.0.1:8000/health", timeout=1.5)
        llm_online = (r.status_code == 200)
    except Exception:
        llm_online = False

    tracker_pid = find_running_tracker_pid()
    tracker_running = tracker_pid is not None

    gpu_stats = _get_gpu_stats()
    active_procs = _get_active_processes()
    vix_price = _get_live_vix()

    watch_count = 0
    try:
        with get_db() as conn:
            watch_count = conn.cursor().execute("SELECT COUNT(*) FROM watch_targets").fetchone()[0]
    except Exception:
        pass

    tasty_count = 0
    tasty_auth = False
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alerts = client.get_quote_alerts()
        tasty_count = len(alerts)
        tasty_auth = True
    except Exception:
        pass

    open_pos_count = 0
    if POSITIONS_FILE.exists():
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                pos_data = json.load(f)
                pos_dict = pos_data.get("positions", pos_data) if isinstance(pos_data, dict) else {}
                today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                open_pos_count = sum(
                    1 for sym, p in pos_dict.items()
                    if isinstance(p, dict)
                    and p.get("opened_at", "")[:10] == today_str
                    and str(p.get("raw_alert", {}).get("subject", "")).lower() != "alert: screener"
                )
        except Exception:
            pass

    if tracker_pid is None and TRACKER_PROCESS is not None and TRACKER_PROCESS.poll() is None:
        tracker_pid = TRACKER_PROCESS.pid
    tracker_running = tracker_pid is not None

    schwab_status = None
    try:
        from src.clients.schwab_client import get_schwab_token_status
        schwab_status = get_schwab_token_status()
    except Exception:
        pass

    return {
        "time_mt": now_mt.strftime("%Y-%m-%d %I:%M:%S %p MT"),
        "time_et": now_et.strftime("%Y-%m-%d %I:%M:%S %p ET"),
        "market_open": market_open,
        "market_phase": market_phase,
        "market_status_text": market_status_text,
        "llm_online": llm_online,
        "tasty_auth": tasty_auth,
        "schwab_status": schwab_status,
        "tracker_running": tracker_running,
        "tracker_pid": tracker_pid,
        "tasty_alert_count": tasty_count,
        "watch_target_count": watch_count,
        "open_position_count": open_pos_count,
        "research_state": RESEARCH_STATE,
        "vix_price": vix_price,
        "gpu_stats": gpu_stats,
        "gpus": gpu_stats,
        "active_processes": active_procs,
    }


@router.get("/api/schwab/status")
def get_schwab_status_endpoint():
    """Returns metadata and expiration time for Schwab OAuth token."""
    from src.clients.schwab_client import get_schwab_token_status
    return get_schwab_token_status()


class KillProcessRequest(BaseModel):
    pid: int


@router.post("/api/processes/kill")
def kill_process(req: KillProcessRequest):
    """Terminate an arbitrary child process by PID."""
    import psutil
    try:
        p = psutil.Process(req.pid)
        for child in p.children(recursive=True):
            child.kill()
        p.kill()
        return {"status": "killed", "pid": req.pid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/orchestrator/start")
def start_orchestrator_endpoint():
    """Start the Email Alert Ingestor (main.py --loop) in a background process."""
    from src.ui.services.daemon_manager import start_orchestrator
    return start_orchestrator()


@router.post("/api/orchestrator/stop")
def stop_orchestrator_endpoint():
    """Stop the Email Alert Ingestor cleanly."""
    from src.ui.services.daemon_manager import stop_orchestrator
    return stop_orchestrator()


@router.get("/api/tickers")
def get_all_tickers_endpoint():
    """Returns all discoverable tickers across reports, watchlist, triage, and SPX constituents."""
    tickers = set()

    # 1. Reports
    rep_dir = config.BASE_DIR / "reports"
    if rep_dir.exists():
        for d in os.listdir(rep_dir):
            day_path = rep_dir / d
            if day_path.is_dir():
                for f in os.listdir(day_path):
                    if f.endswith("_summary.md") or f.endswith("_arbitration.md"):
                        sym = f.split("_")[0].upper()
                        if sym.isalnum():
                            tickers.add(sym)

    # 2. SQLite Watchlist
    try:
        with get_db() as conn:
            rows = conn.cursor().execute("SELECT DISTINCT ticker FROM watch_targets").fetchall()
            for r in rows:
                if r["ticker"]:
                    tickers.add(r["ticker"].upper())
    except Exception:
        pass

    # 3. Triage folders
    triage_dir = config.BASE_DIR / "data" / "triage"
    if triage_dir.exists():
        for d in os.listdir(triage_dir):
            for sub in ("_DEEP_RESEARCH", "force"):
                sp = triage_dir / d / sub
                if sp.exists() and sp.is_dir():
                    for t in os.listdir(sp):
                        if t.isalnum():
                            tickers.add(t.upper())

    # 4. SPX constituents
    spx_file = config.BASE_DIR / "EveryDay" / "SPX-constituents.csv"
    if spx_file.exists():
        try:
            with open(spx_file, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].isalnum() and len(row[0]) <= 5:
                        tickers.add(row[0].strip().upper())
        except Exception:
            pass

    tickers.update(["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "WMT", "AMD", "UBER", "PLTR", "CRWD", "COIN", "AVGO", "COST", "NFLX", "PYPL"])
    return {"tickers": sorted(list(tickers))}


@router.get("/api/company-names")
def get_company_names_endpoint():
    """Return dictionary of ticker -> Company Full Name for tooltips."""
    return get_company_names_dict()


@router.get("/api/logs")
def get_logs(channel: str = "all", job_id: Optional[str] = None):
    """Fetch live log buffer by channel or specific research job log."""
    if job_id:
        log_file = LOGS_DIR / f"{job_id}.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8").splitlines()
                return {"logs": lines[-600:], "channel": f"job:{job_id}"}
            except Exception:
                pass

    with get_db() as conn:
        c = conn.cursor()
        jobs = c.execute("SELECT * FROM active_research_jobs ORDER BY started_at DESC LIMIT 15").fetchall()
        jobs_list = [dict(j) for j in jobs]
    return {"logs": list(LOG_BUFFER), "state": RESEARCH_STATE, "jobs": jobs_list, "channel": channel}
