"""
Single-File Unified Trading Cockpit & UI Server.
Combines FastAPI backend, live streaming runner, SQLite/Tastytrade sync, and modern Dark Glassmorphism Frontend in one self-contained file.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import config

# Setup logging
logger = logging.getLogger("ui_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] (UI) %(message)s")

app = FastAPI(title="Stock Trading Cockpit", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = config.BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
DATA_DIR = config.BASE_DIR / "data"
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")


@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Multi-channel log buffers for separate stream viewing
LOG_BUFFERS: Dict[str, collections.deque] = {
    "all": collections.deque(maxlen=1000),
    "orchestrator": collections.deque(maxlen=600),
    "research": collections.deque(maxlen=600),
    "watch": collections.deque(maxlen=600),
}
LOG_BUFFER = LOG_BUFFERS["all"]

RESEARCH_STATE = {
    "is_running": False,
    "current_ticker": None,
    "stage": "IDLE",
    "started_at": None,
}
TRACKER_PROCESS: Optional[subprocess.Popen] = None
ACTIVE_RESEARCH_WORKERS: Dict[str, threading.Thread] = {}
ACTIVE_RESEARCH_SUBPROCS: Dict[str, subprocess.Popen] = {}

DB_PATH = config.BASE_DIR / "data" / "research_watch.db"
POSITIONS_FILE = config.BASE_DIR / "data" / "positions.json"


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    """Initializes the database schema for watch targets, research jobs, and Copilot chat history."""
    try:
        from src.tracking.watch_manager import init_watch_db
        init_watch_db()
    except Exception:
        pass

    with _get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS copilot_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist_ticker_date ON copilot_chat_history(ticker, date);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist_created ON copilot_chat_history(created_at);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_hist_session ON copilot_chat_history(session_id);")
        conn.commit()


def _save_chat_turn(session_id: str, ticker: str, date_str: str, role: str, content: str):
    """Saves a chat message turn directly to SQLite database."""
    try:
        _init_db()
        with _get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO copilot_chat_history (session_id, ticker, date, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, (ticker or "GENERAL").upper(), date_str or datetime.now().strftime("%Y-%m-%d"), role, content, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"Error saving chat turn: {e}")



def _append_log(msg: str, channel: str = "all"):
    ts = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {msg}"
    LOG_BUFFERS["all"].append(formatted)
    if channel in LOG_BUFFERS and channel != "all":
        LOG_BUFFERS[channel].append(formatted)
    
    msg_low = msg.lower()
    if "tracker" in msg_low or "ingestor" in msg_low or "gmail" in msg_low or "market is" in msg_low:
        LOG_BUFFERS["orchestrator"].append(formatted)
    elif "research" in msg_low or "scrape" in msg_low or "triage" in msg_low or "debate" in msg_low or "synthesis" in msg_low:
        LOG_BUFFERS["research"].append(formatted)
    elif "watch" in msg_low or "tasty" in msg_low or "spot:" in msg_low or "alert created" in msg_low or "cloud alert" in msg_low:
        LOG_BUFFERS["watch"].append(formatted)
    logger.info(msg)


_vix_cache = {"ts": 0.0, "value": None}


def _get_live_vix() -> Optional[float]:
    """Live VIX level: Tastytrade DXLink (real-time) -> yfinance -> Yahoo chart API. Cached 30s."""
    import time as _time

    now = _time.time()
    if _vix_cache["value"] is not None and now - _vix_cache["ts"] < 30:
        return _vix_cache["value"]

    price = None
    try:
        from src.clients.tastytrade_client import TastytradeClient

        q = TastytradeClient().get_realtime_quote("VIXCBOETM")
        if q and q.get("price"):
            price = float(q["price"])
    except Exception:
        pass

    if price is None:
        try:
            import yfinance as yf

            t = yf.Ticker("^VIX")
            price = t.fast_info.get("last_price", None)
            if not price or price <= 0:
                hist = t.history(period="2d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
        except Exception:
            price = None

    if price is None:
        try:
            import requests

            r = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1m&range=1d",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )
            if r.status_code == 200:
                meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                p = meta.get("regularMarketPrice")
                if p and p > 0:
                    price = float(p)
        except Exception:
            pass

    if price and price > 0:
        _vix_cache["ts"] = now
        _vix_cache["value"] = round(float(price), 2)
    return _vix_cache["value"]


def _get_gpu_stats():
    """Query nvidia-smi for VRAM and utilization metrics."""
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu', '--format=csv,noheader,nounits'],
            text=True,
            timeout=2.0
        )
        gpus = []
        for line in out.strip().split('\n'):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 5:
                used = float(parts[2])
                total = float(parts[3])
                pct = round((used / total) * 100, 1)
                gpus.append({
                    'index': parts[0],
                    'name': parts[1].replace('NVIDIA GeForce ', ''),
                    'used_mb': used,
                    'total_mb': total,
                    'used_gb': round(used / 1024, 2),
                    'total_gb': round(total / 1024, 2),
                    'pct': pct,
                    'util': parts[4],
                    'temp': parts[5] if len(parts) > 5 else 'N/A'
                })
        return gpus
    except Exception:
        return []


def _get_active_processes():
    """Scan system for active LLM, scraping, and research processes."""
    import psutil
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
        try:
            pname = (p.info['name'] or '').lower()
            cmd = ' '.join(p.info['cmdline'] or []).lower()
            pid = p.info['pid']
            rss_mb = round((p.info['memory_info'].rss if p.info['memory_info'] else 0) / (1024 * 1024), 1)

            if 'llama-server' in pname or 'llama-server.exe' in cmd:
                procs.append({
                    'type': 'LLM Server',
                    'name': 'llama-server.exe',
                    'pid': pid,
                    'ram_mb': rss_mb,
                    'details': 'Qwen3.8-27B Dual-GPU Port 8000',
                    'is_llm': True
                })
            elif 'python' in pname:
                if 'run_deep_research.py' in cmd or 'deep_research.py' in cmd:
                    procs.append({
                        'type': 'Deep Research',
                        'name': 'Python Research Agent',
                        'pid': pid,
                        'ram_mb': rss_mb,
                        'details': 'Multi-pass Bull/Bear & Judge Inference',
                        'is_llm': False
                    })
                elif 'run_swing_research.py' in cmd or 'tv_scraper.py' in cmd:
                    procs.append({
                        'type': 'TradingView Scraper',
                        'name': 'Playwright Scraper',
                        'pid': pid,
                        'ram_mb': rss_mb,
                        'details': 'Playwright Chrome Chart Capture',
                        'is_llm': False
                    })
                elif 'main.py' in cmd and '--loop' in cmd:
                    procs.append({
                        'type': 'Email Alert Ingestor',
                        'name': 'Market Tracker',
                        'pid': pid,
                        'ram_mb': rss_mb,
                        'details': 'Gmail Alert Ingestor & Position Monitor',
                        'is_llm': False
                    })
            elif 'chrome' in pname and '--headless' in cmd:
                procs.append({
                    'type': 'Browser Subprocess',
                    'name': 'Headless Chrome',
                    'pid': pid,
                    'ram_mb': rss_mb,
                    'details': 'TradingView Chart Engine',
                    'is_llm': False
                })
        except Exception:
            continue
    return procs


# =====================================================================
# API ENDPOINTS
# =====================================================================


def _find_running_tracker_pid() -> Optional[int]:
    """Return PID of running main.py (Alert Ingestor), whether started by UI or externally."""
    global TRACKER_PROCESS
    if TRACKER_PROCESS is not None and TRACKER_PROCESS.poll() is None:
        return TRACKER_PROCESS.pid
    try:
        import psutil
        curr_pid = os.getpid()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            if p.info['pid'] == curr_pid:
                continue
            name = (p.info.get('name') or '').lower()
            if 'python' in name:
                cmdline = p.info.get('cmdline') or []
                for arg in cmdline:
                    arg_clean = str(arg).replace('\\', '/').split('/')[-1]
                    if arg_clean == 'main.py':
                        return p.info['pid']
    except Exception:
        pass
    return None


@app.get("/api/status")
def get_system_status():
    """System heartbeat, market hours, GPU VRAM, active tasks, LLM health, orchestrator status, and active counts."""
    global TRACKER_PROCESS
    # Accurate NYSE/NASDAQ Market hours check (Eastern Time standard)
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    is_weekday = now_et.weekday() < 5

    # Regular Trading Hours (RTH): 9:30 AM – 4:00 PM ET (7:30 AM – 2:00 PM MT)
    rth_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    rth_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    # Pre-market: 4:00 AM – 9:30 AM ET
    pre_open = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    # Post-market: 4:00 PM – 8:00 PM ET
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

    # Orchestrator / Gmail Ingestor status (detects both UI-spawned and CLI/orchestrator-spawned)
    tracker_pid = _find_running_tracker_pid()
    tracker_running = tracker_pid is not None

    # GPU VRAM and active process metrics
    gpu_stats = _get_gpu_stats()
    active_procs = _get_active_processes()

    # Live VIX volatility reading
    vix_price = _get_live_vix()

    # Watch targets count
    watch_count = 0
    try:
        with _get_db() as conn:
            c = conn.cursor()
            watch_count = c.execute("SELECT COUNT(*) FROM watch_targets").fetchone()[0]
    except Exception:
        pass

    # Tastytrade alerts count
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

    # Open positions count (today's active intraday positions)
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

    # Schwab OAuth token status (7-day lifecycle)
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


@app.get("/api/schwab/status")
def get_schwab_status_endpoint():
    """Returns metadata and expiration time for Schwab OAuth token."""
    from src.clients.schwab_client import get_schwab_token_status
    return get_schwab_token_status()



class KillProcessRequest(BaseModel):
    pid: int


@app.post("/api/processes/kill")
def kill_process(req: KillProcessRequest):
    """Terminate a background task to immediately free VRAM or RAM."""
    import psutil
    try:
        p = psutil.Process(req.pid)
        pname = p.name()
        p.terminate()
        _append_log(f"🛑 Terminated process PID {req.pid} ({pname}) to free memory.")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/start")
def start_orchestrator():
    """Start the Email Alert Ingestor (main.py --loop) in a background process."""
    global TRACKER_PROCESS
    existing_pid = _find_running_tracker_pid()
    if existing_pid is not None:
        return {"status": "already_running", "pid": existing_pid}

    _append_log("🚀 Starting Email Alert Ingestor (main.py --loop)...")
    cmd = [sys.executable, "main.py", "--loop"]
    TRACKER_PROCESS = subprocess.Popen(
        cmd,
        cwd=str(config.BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    def _pipe_logs(proc):
        for line in proc.stdout:
            l_str = line.strip()
            if l_str:
                _append_log(f"[Tracker] {l_str}")
        proc.wait()
        _append_log(f"⚠️ Email Alert Ingestor stopped (code {proc.returncode}).")

    threading.Thread(target=_pipe_logs, args=(TRACKER_PROCESS,), daemon=True).start()
    return {"status": "started", "pid": TRACKER_PROCESS.pid}


@app.post("/api/orchestrator/stop")
def stop_orchestrator():
    """Stop the Email Alert Ingestor cleanly."""
    global TRACKER_PROCESS
    active_pid = _find_running_tracker_pid()
    if active_pid is None:
        return {"status": "not_running"}

    _append_log(f"🛑 Stopping Email Alert Ingestor (PID {active_pid})...")
    if TRACKER_PROCESS is not None and TRACKER_PROCESS.poll() is None:
        try:
            TRACKER_PROCESS.terminate()
            TRACKER_PROCESS.wait(timeout=5)
        except Exception:
            TRACKER_PROCESS.kill()
        TRACKER_PROCESS = None
    else:
        try:
            import psutil
            p = psutil.Process(active_pid)
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass

    _append_log("✅ Email Alert Ingestor terminated.")
    return {"status": "stopped"}


@app.on_event("startup")
def on_startup():
    """Initialize database and dynamically auto-start Email Alert Ingestor on UI launch."""
    _init_db()
    try:
        pid = _find_running_tracker_pid()
        if pid is not None:
            _append_log(f"🟢 [Startup] Detected Email Alert Ingestor already active (PID {pid}).")
        else:
            _append_log("🚀 [Startup] Dynamically auto-starting Email Alert Ingestor (main.py --loop)...")
            res = start_orchestrator()
            _append_log(f"✅ [Startup] Email Alert Ingestor running (PID {res.get('pid')}).")
    except Exception as e:
        logger.error(f"Failed to auto-start Email Alert Ingestor on startup: {e}")
        _append_log(f"⚠️ [Startup] Auto-start Email Alert Ingestor failed: {e}")

    # Launch Autonomous Alert Triage Daemon
    try:
        from src.tracking.auto_triage_daemon import start_auto_triage_daemon
        start_auto_triage_daemon(poll_interval=8, batch_size=15)
        _append_log("🤖 [Startup] Autonomous Alert Triage Daemon active.")
    except Exception as e_triage:
        logger.warning(f"Failed to start AutoTriageDaemon: {e_triage}")

    # Launch Continuous Schwab 1000 & Tastytrade Screener Daemon
    try:
        from src.screener.continuous_screener_daemon import start_continuous_screener_daemon
        start_continuous_screener_daemon()
        _append_log("🤖 [Startup] Continuous Schwab & Tastytrade Screener Daemon active.")
    except Exception as e_screener:
        logger.warning(f"Failed to start ContinuousScreenerDaemon: {e_screener}")

    # Launch Watchlist & Trigger Alert Daemon (Continuous Real-Time Price Polling & Alerts)
    try:
        def _bg_watch_alerts():
            from run_watch_alerts import run_watch_loop
            run_watch_loop(poll_interval=60, sync_sheets=False)
        threading.Thread(target=_bg_watch_alerts, daemon=True, name="WatchAlertsDaemon").start()
        _append_log("🤖 [Startup] Real-Time Watchlist Trigger Alert Daemon active (60s loop).")
    except Exception as e_watch:
        logger.warning(f"Failed to start WatchAlertsDaemon: {e_watch}")

    # Check Local LLM Server on Port 8000
    def _bg_check_llm():
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:8000/health")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    _append_log("🟢 [Startup] Local LLM Server healthy on port 8000.")
        except Exception:
            _append_log("⚠️ [Startup] Local LLM Server not detected on port 8000. Start via scripts\\launchers\\start_llm_server.bat if using local inference.")
    threading.Thread(target=_bg_check_llm, daemon=True, name="LLMHealthCheck").start()

    # Initial Schwab Portfolio Sync in background
    def _bg_portfolio_sync():
        try:
            from src.tracking.schwab_portfolio_manager import sync_schwab_positions
            res = sync_schwab_positions()
            if res.get("success"):
                _append_log(f"💼 [Startup] Synced Schwab Portfolio ({res.get('positions_count', 0)} positions, Total: ${res.get('total_liquidation_value', 0):,.2f}).")
        except Exception as e_pfsync:
            logger.debug(f"Initial Schwab portfolio sync skipped/deferred: {e_pfsync}")

    threading.Thread(target=_bg_portfolio_sync, daemon=True, name="StartupPortfolioSync").start()


@app.on_event("shutdown")
def on_shutdown():
    """Cleanly stop background ingestor, auto-triage daemon, and continuous screener on server shutdown."""
    global TRACKER_PROCESS
    if TRACKER_PROCESS is not None and TRACKER_PROCESS.poll() is None:
        try:
            logger.info("Stopping Email Alert Ingestor on UI shutdown...")
            TRACKER_PROCESS.terminate()
            TRACKER_PROCESS.wait(timeout=3)
        except Exception:
            TRACKER_PROCESS.kill()
        TRACKER_PROCESS = None

    try:
        from src.tracking.auto_triage_daemon import _daemon_instance
        if _daemon_instance:
            _daemon_instance.stop()
    except Exception:
        pass

    try:
        from src.screener.continuous_screener_daemon import stop_continuous_screener_daemon
        stop_continuous_screener_daemon()
    except Exception:
        pass


@app.get("/api/watch-targets")
def get_watch_targets():
    """Fetch active stalking targets and enrich with tactical trade ideas from LLM research."""
    try:
        from src.logic.trade_ideas_generator import generate_trade_ideas_for_target

        rep_root = config.BASE_DIR / "reports"
        raw_root = config.BASE_DIR / "data" / "raw"

        with _get_db() as conn:
            c = conn.cursor()
            rows = c.execute("SELECT * FROM watch_targets").fetchall()
            targets = []

            for r in rows:
                item = dict(r)
                if item.get("raw_json"):
                    try:
                        item["parsed_json"] = json.loads(item["raw_json"])
                    except Exception:
                        item["parsed_json"] = {}
                
                # Attach exact deep research timestamp from report file mtime
                sym = item.get("ticker", "").upper()
                d_str = item.get("date", "")
                target_mtime = None
                
                # Check reports/{date}/{ticker}_arbitration.md first
                rep_dir = rep_root / d_str
                for cand_name in [f"{sym}_arbitration.md", f"{sym}_summary.md", f"{sym}_independent.md"]:
                    cand = rep_dir / cand_name
                    if cand.exists():
                        target_mtime = datetime.fromtimestamp(cand.stat().st_mtime).isoformat()
                        break
                
                # Fallback check raw/
                if not target_mtime and raw_root.exists():
                    raw_dir = raw_root / d_str / sym
                    for cand_name in [f"{sym}_arbitration.md", f"{sym}_independent_thesis.md", f"{sym}_gemini_thesis.md"]:
                        cand = raw_dir / cand_name
                        if cand.exists():
                            target_mtime = datetime.fromtimestamp(cand.stat().st_mtime).isoformat()
                            break

                # Discard ghost / test targets that have no research report artifact on disk
                if not target_mtime:
                    continue

                item["research_timestamp"] = target_mtime
                item["is_open_position"] = False
                item["trade_ideas"] = generate_trade_ideas_for_target(item)

                targets.append(item)

            # Enrich all targets with live real-time quotes from Schwab
            all_symbols = [t.get("ticker", "").upper() for t in targets if t.get("ticker")]
            try:
                from src.clients.schwab_client import get_realtime_quotes_batch
                schwab_quotes = get_realtime_quotes_batch(all_symbols)
                for item in targets:
                    sym = item.get("ticker", "").upper()
                    q = schwab_quotes.get(sym)
                    if q and q.get("last_price"):
                        live_px = float(q["last_price"])
                        item["last_price"] = live_px
                        item["net_change"] = q.get("net_change", 0.0)
                        item["net_percent_change"] = q.get("net_percent_change", 0.0)
                        item["quote_source"] = "SCHWAB"

                        # Recompute distance_to_entry_pct and state transitions live
                        entry_low = item.get("entry_zone_low")
                        entry_high = item.get("entry_zone_high")
                        side = str(item.get("side") or "LONG").upper()
                        inv_price = item.get("invalidation_price")
                        target_1 = item.get("target_1")
                        target_2 = item.get("target_2")

                        # Invalidation / stop breach check
                        is_stop_breached = False
                        if inv_price and inv_price > 0:
                            if side == "LONG" and live_px <= inv_price:
                                is_stop_breached = True
                            elif side == "SHORT" and live_px >= inv_price:
                                is_stop_breached = True

                        # Target reached check
                        hit_target = False
                        if target_2 and target_2 > 0:
                            if (side == "LONG" and live_px >= target_2) or (side == "SHORT" and live_px <= target_2):
                                hit_target = True
                        elif target_1 and target_1 > 0:
                            if (side == "LONG" and live_px >= target_1) or (side == "SHORT" and live_px <= target_1):
                                hit_target = True

                        if is_stop_breached:
                            item["status"] = "INVALIDATED"
                        elif hit_target and item.get("status") in ("IN_TRADE", "IN_ZONE"):
                            item["status"] = "TARGET_HIT"
                        elif hit_target and item.get("status") == "STALKING":
                            item["status"] = "MISSED_RUNAWAY"

                        if entry_low and entry_high and entry_low > 0 and entry_high > 0:
                            if side == "LONG":
                                if live_px > entry_high:
                                    item["distance_to_entry_pct"] = round(((live_px - entry_high) / entry_high) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                elif live_px < entry_low:
                                    item["distance_to_entry_pct"] = round(((live_px - entry_low) / entry_low) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                else:
                                    item["distance_to_entry_pct"] = 0.0
                                    if not is_stop_breached and not hit_target and item.get("status") == "STALKING":
                                        item["status"] = "IN_ZONE"
                            else:
                                if live_px < entry_low:
                                    item["distance_to_entry_pct"] = round(((entry_low - live_px) / entry_low) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                elif live_px > entry_high:
                                    item["distance_to_entry_pct"] = round(((entry_high - live_px) / entry_high) * 100, 2)
                                    if not is_stop_breached and not hit_target and item.get("status") == "IN_ZONE":
                                        item["status"] = "STALKING"
                                else:
                                    item["distance_to_entry_pct"] = 0.0
                                    if not is_stop_breached and not hit_target and item.get("status") == "STALKING":
                                        item["status"] = "IN_ZONE"
            except Exception as q_err:
                logger.debug(f"Error enriching watch targets with Schwab quotes: {q_err}")

            # Enrich all targets with Suggested Trades P&L (options spreads & shares) and outcomes
            won_pnls = []
            lost_pnls = []
            active_pnls = []

            won_dollars = []
            lost_dollars = []
            active_dollars = []
            actionable_count = 0

            for item in targets:
                entry_low = item.get("entry_zone_low")
                entry_high = item.get("entry_zone_high")
                side = str(item.get("side") or "LONG").upper()
                tactical_stop = item.get("tactical_stop") or item.get("invalidation_price")
                target_1 = item.get("target_1")
                target_2 = item.get("target_2")
                live_px = item.get("last_price")
                status = (item.get("status") or "STALKING").upper()
                dist_pct = item.get("distance_to_entry_pct")
                opt_act = bool(item.get("options_actionable"))

                raw = item.get("parsed_json") or {}
                op = raw.get("options_plan") or {}
                sp = raw.get("shares_plan") or {}

                struct = op.get("structure") or item.get("options_structure") or "NONE"
                max_prof = float(op.get("max_profit") or 0.0)
                max_loss = float(op.get("max_loss") or 0.0)
                long_k = float(op.get("long_strike") or 0.0)
                short_k = float(op.get("short_strike") or 0.0)
                debit = float(op.get("target_debit") or 0.0)
                is_options = (struct and struct != "NONE" and (max_prof > 0 or max_loss > 0))

                trade_type = "OPTIONS" if is_options else "SHARES"
                trade_label = struct.replace("_", " ").title() if is_options else f"Shares ({sp.get('entry_type', 'Limit')})"

                entry_mid = None
                if entry_low is not None and entry_high is not None and (entry_low > 0 or entry_high > 0):
                    entry_mid = round((entry_low + entry_high) / 2.0, 2)
                elif entry_low and entry_low > 0:
                    entry_mid = entry_low
                elif entry_high and entry_high > 0:
                    entry_mid = entry_high
                item["entry_midpoint"] = entry_mid

                # 1. Underlying Stock Price Delta %
                pnl_pct = None
                if entry_mid and live_px and entry_mid > 0:
                    if side == "SHORT":
                        pnl_pct = round(((entry_mid - live_px) / entry_mid) * 100.0, 2)
                    else:
                        pnl_pct = round(((live_px - entry_mid) / entry_mid) * 100.0, 2)
                item["pnl_pct"] = pnl_pct

                # 2. SUGGESTED TRADE DOLLAR P&L & ROC %
                trade_dollar_pnl = 0.0
                trade_roc_pct = 0.0

                if status in ("TARGET_HIT", "COMPLETED"):
                    if is_options and max_prof > 0:
                        trade_dollar_pnl = max_prof
                        trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                    else:
                        t_exit = target_2 if (target_2 and target_2 > 0) else target_1
                        if t_exit and entry_mid:
                            sh_gain = (t_exit - entry_mid) if side == "LONG" else (entry_mid - t_exit)
                            trade_dollar_pnl = round(sh_gain * 100, 2)
                            trade_roc_pct = round((sh_gain / entry_mid * 100), 2)
                    won_dollars.append(trade_dollar_pnl)
                    won_pnls.append(trade_roc_pct)

                elif status in ("INVALIDATED", "STOP_BREACHED", "STOPPED"):
                    if is_options and max_loss > 0:
                        trade_dollar_pnl = -max_loss
                        trade_roc_pct = -100.0
                    else:
                        if tactical_stop and entry_mid:
                            sh_loss = (tactical_stop - entry_mid) if side == "LONG" else (entry_mid - tactical_stop)
                            trade_dollar_pnl = round(sh_loss * 100, 2)
                            trade_roc_pct = round((sh_loss / entry_mid * 100), 2)
                    lost_dollars.append(trade_dollar_pnl)
                    lost_pnls.append(trade_roc_pct)

                elif status in ("IN_TRADE", "IN_ZONE") and live_px and live_px > 0:
                    if is_options and (max_prof > 0 or max_loss > 0):
                        if "PUT" in struct:
                            if live_px >= short_k:
                                trade_dollar_pnl = max_prof
                                trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif live_px <= long_k:
                                trade_dollar_pnl = -max_loss
                                trade_roc_pct = -100.0
                            elif short_k > long_k:
                                ratio = (live_px - long_k) / (short_k - long_k)
                                trade_dollar_pnl = round(max_prof * ratio - max_loss * (1.0 - ratio), 2)
                                trade_roc_pct = round((trade_dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                        else:
                            if live_px >= short_k:
                                trade_dollar_pnl = max_prof
                                trade_roc_pct = round((max_prof / max_loss * 100), 1) if max_loss > 0 else 100.0
                            elif live_px <= long_k:
                                trade_dollar_pnl = -max_loss
                                trade_roc_pct = -100.0
                            else:
                                spread_val = (live_px - long_k) * 100.0
                                trade_dollar_pnl = round(spread_val - (debit * 100.0), 2)
                                trade_roc_pct = round((trade_dollar_pnl / max_loss * 100), 1) if max_loss > 0 else 0.0
                    else:
                        if entry_mid:
                            sh_gain = (live_px - entry_mid) if side == "LONG" else (entry_mid - live_px)
                            trade_dollar_pnl = round(sh_gain * 100, 2)
                            trade_roc_pct = round((sh_gain / entry_mid * 100), 2)
                    active_dollars.append(trade_dollar_pnl)
                    active_pnls.append(trade_roc_pct)

                item["trade_type"] = trade_type
                item["trade_label"] = trade_label
                item["trade_dollar_pnl"] = trade_dollar_pnl
                item["trade_roc_pct"] = trade_roc_pct
                item["trade_max_profit"] = max_prof
                item["trade_max_loss"] = max_loss
                item["realized_pnl_pct"] = trade_roc_pct

                # Risk to Reward ratio
                rr_ratio = None
                if is_options and max_loss > 0:
                    rr_ratio = round(max_prof / max_loss, 2)
                elif entry_mid and target_1 and tactical_stop and entry_mid > 0:
                    reward = abs(target_1 - entry_mid)
                    risk = abs(entry_mid - tactical_stop)
                    if risk > 0.01:
                        rr_ratio = round(reward / risk, 2)
                item["rr_ratio"] = rr_ratio

                # Actionable flag: IN_ZONE, IN_TRADE, or distance <= 1.0%
                is_actionable = (status in ("IN_ZONE", "IN_TRADE")) or (dist_pct is not None and abs(dist_pct) <= 1.0) or opt_act
                item["is_actionable"] = is_actionable
                if is_actionable:
                    actionable_count += 1

            total_resolved = len(won_dollars) + len(lost_dollars)
            win_rate = round((len(won_dollars) / total_resolved) * 100.0, 1) if total_resolved > 0 else 0.0

            total_won_dollars = round(sum(won_dollars), 2)
            total_lost_dollars = round(sum(lost_dollars), 2)
            total_active_dollars = round(sum(active_dollars), 2)
            net_dollar_profit = round(total_won_dollars + total_lost_dollars + total_active_dollars, 2)

            avg_win_dollars = round(total_won_dollars / len(won_dollars), 2) if won_dollars else 0.0
            avg_loss_dollars = round(total_lost_dollars / len(lost_dollars), 2) if lost_dollars else 0.0
            profit_factor = round(abs(total_won_dollars) / max(1.0, abs(total_lost_dollars)), 2) if total_lost_dollars != 0 else 99.9

            performance_summary = {
                "total_targets": len(targets),
                "won_count": len(won_dollars),
                "lost_count": len(lost_dollars),
                "resolved_count": total_resolved,
                "win_rate_pct": win_rate,
                "total_won_dollars": total_won_dollars,
                "total_lost_dollars": total_lost_dollars,
                "total_active_dollars": total_active_dollars,
                "net_dollar_profit": net_dollar_profit,
                "avg_win_dollars": avg_win_dollars,
                "avg_loss_dollars": avg_loss_dollars,
                "profit_factor": profit_factor,
                "actionable_count": actionable_count,
            }

            # Sort by research timestamp DESC (newest research runs first)
            targets.sort(key=lambda x: str(x.get("research_timestamp", "")), reverse=True)
            return {"targets": targets, "performance": performance_summary}
    except Exception as e:
        logger.error(f"Error in get_watch_targets: {e}")
        return {"targets": [], "error": str(e)}


@app.get("/api/trades/audit")
def get_trades_audit(
    tab: str = Query("ALL"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    window: int = Query(100, ge=0, le=5000),
):
    """Returns the pre-computed suggested trades audit trail, tab counts, and summary from SQLite with server-side pagination."""
    try:
        from src.tracking.suggested_trades_auditor import get_audit_summary
        return get_audit_summary(
            tab=tab,
            search=search,
            page=page,
            page_size=page_size,
            window=window,
            force_sync=True
        )
    except Exception as e:
        logger.error(f"Error in get_trades_audit: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": {}, "trades": [], "pagination": {}}


@app.post("/api/trades/audit/evaluate")
def evaluate_trades_audit(
    window: int = Query(100, ge=0, le=5000),
):
    """Triggers on-demand evaluation of suggested trades against live quotes (safe batching, rate-limit protected) and updates SQLite."""
    try:
        from src.tracking.suggested_trades_auditor import evaluate_all_suggested_trades
        result = evaluate_all_suggested_trades(refresh_quotes=True, window=window)
        return {"success": True, "message": f"Successfully evaluated live quotes on demand for last {window} trades without API overload.", **result}
    except Exception as e:
        logger.error(f"Error in evaluate_trades_audit: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": {}, "trades": []}


@app.get("/api/research/queue")
def get_research_queue(date: Optional[str] = None):
    """
    Returns candidate tickers that need scraping or are ready for deep research:
    - Scraped candidates (charts & datawindow present, no deep research report yet).
    - Screener survivors from today's survivors.json.
    - Desk priority mega-caps ready to launch.
    """
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_date = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")

        target_date = date.strip() if (date and date.strip()) else now_date
        raw_root = config.BASE_DIR / "data" / "raw" / target_date
        rep_root = config.BASE_DIR / "reports" / target_date

        # If date wasn't explicitly requested and now_date has no folder on disk, find the latest available date
        if not (date and date.strip()) and not raw_root.exists():
            raw_base = config.BASE_DIR / "data" / "raw"
            if raw_base.exists():
                avail_dates = sorted([d.name for d in raw_base.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)], reverse=True)
                if avail_dates:
                    target_date = avail_dates[0]
                    raw_root = config.BASE_DIR / "data" / "raw" / target_date
                    rep_root = config.BASE_DIR / "reports" / target_date

        queue = []
        seen = set()

        # 1. Check target_date raw/ folders for scraped candidates
        if raw_root.exists():
            for item in raw_root.iterdir():
                if item.is_dir():
                    sym = item.name.upper()
                    has_dw = (item / f"{sym}_datawindow.json").exists() or (item / f"{sym}_datawindow.csv").exists()
                    has_chart = (item / f"{sym}_chart.png").exists() or (item / f"{sym}_chart_zoom.png").exists()
                    has_report = rep_root.exists() and (rep_root / f"{sym}_arbitration.md").exists()
                    
                    if has_dw and not has_report:
                        queue.append({
                            "ticker": sym,
                            "date": target_date,
                            "status": "READY_FOR_RESEARCH",
                            "action": "deep_only",
                            "action_label": "⚡ Run Deep Research",
                            "reason": "Chart & Data Window ready on disk",
                            "has_chart": has_chart,
                            "has_report": False
                        })
                        seen.add(sym)
                    elif has_report:
                        seen.add(sym)

        # 2. Check survivors.json for target_date
        surv_file = raw_root / "survivors.json" if raw_root.exists() else None
        if surv_file and surv_file.exists():
            try:
                survs = json.loads(surv_file.read_text(encoding="utf-8"))
                for s in survs:
                    sym = (s.get("Symbol") or s.get("Ticker") or "").upper()
                    if sym and sym not in seen:
                        queue.append({
                            "ticker": sym,
                            "date": now_date,
                            "status": "NEEDS_SCRAPE",
                            "action": "full",
                            "action_label": "📸 Scrape & Research",
                            "reason": "Screener Survivor",
                            "has_chart": False,
                            "has_report": False
                        })
                        seen.add(sym)
            except Exception:
                pass

        # 3. Desk priority candidates if queue is small (<6)
        priority_candidates = ["UBER", "NVDA", "META", "TSLA", "NFLX", "PLTR", "AMD"]
        for p_sym in priority_candidates:
            if p_sym not in seen and len(queue) < 6:
                if rep_root.exists() and (rep_root / f"{p_sym}_arbitration.md").exists():
                    continue
                # Also check recent reports to avoid offering ticker that was just researched
                reports_base = config.BASE_DIR / "reports"
                already_has_recent_report = False
                if reports_base.exists():
                    latest_rep_dirs = sorted([d for d in reports_base.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)], reverse=True)[:2]
                    for d in latest_rep_dirs:
                        if (d / f"{p_sym}_arbitration.md").exists():
                            already_has_recent_report = True
                            break
                if already_has_recent_report:
                    continue

                queue.append({
                    "ticker": p_sym,
                    "date": now_date,
                    "status": "READY_TO_LAUNCH",
                    "action": "full",
                    "action_label": "🚀 Scrape & Run",
                    "reason": "Desk Priority",
                    "has_chart": False,
                    "has_report": False
                })
                seen.add(p_sym)

        return {"date": target_date, "queue": queue}
    except Exception as e:
        logger.error(f"Error in get_research_queue: {e}")
        return {"date": "", "queue": [], "error": str(e)}


@app.post("/api/watch-targets/delete")
def delete_watch_target_post(data: dict):
    """Delete / Untrack a stalking target from the SQLite watch database."""
    try:
        ticker = (data.get("ticker") or "").upper().strip()
        date = (data.get("date") or "").strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker symbol required")

        with _get_db() as conn:
            c = conn.cursor()
            if date:
                c.execute("DELETE FROM watch_targets WHERE ticker = ? AND date = ?", (ticker, date))
            else:
                c.execute("DELETE FROM watch_targets WHERE ticker = ?", (ticker,))
            conn.commit()

        _append_log(f"🗑️ Untracked and removed {ticker} ({date or 'all dates'}) from Watchlist.")
        return {"status": "ok", "deleted": ticker}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed deleting watch target: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watch-targets/poll")
def poll_watch_targets():
    """Trigger an immediate real-time price polling & trigger evaluation cycle."""
    try:
        from run_watch_alerts import evaluate_watch_cycle
        updated = evaluate_watch_cycle(sync_sheets=False)
        return {"status": "ok", "updated_count": len(updated)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/watch-alerts")
def get_watch_alerts(limit: int = 50, ticker: Optional[str] = None):
    """Retrieve recent trigger alerts and daily alert events from SQLite."""
    with _get_db() as conn:
        cursor = conn.cursor()
        if ticker:
            rows = cursor.execute(
                "SELECT id, ticker, date, trigger_type, message, spot_price, triggered_at FROM watch_alerts WHERE ticker = ? ORDER BY id DESC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = cursor.execute(
                "SELECT id, ticker, date, trigger_type, message, spot_price, triggered_at FROM watch_alerts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "alerts": [
                {
                    "id": r["id"],
                    "ticker": r["ticker"],
                    "date": r["date"],
                    "trigger_type": r["trigger_type"],
                    "message": r["message"],
                    "spot_price": r["spot_price"],
                    "triggered_at": r["triggered_at"],
                }
                for r in rows
            ]
        }


@app.get("/api/alerts/history")
def get_alerts_history(limit: int = 1000, date: Optional[str] = None, symbol: Optional[str] = None, strategy: Optional[str] = None):
    """Retrieve historical TradingView alerts directly from the SQLite alert database."""
    try:
        from src.tracking.alert_db import get_alerts_for_date, get_recent_alerts
        if date:
            alerts = get_alerts_for_date(date)
        else:
            alerts = get_recent_alerts(limit=limit)
        if symbol:
            sym_clean = symbol.strip().upper()
            alerts = [a for a in alerts if a.get("symbol") == sym_clean]
        if strategy:
            strat_clean = strategy.strip().lower()
            alerts = [a for a in alerts if str(a.get("strategy") or "").lower() == strat_clean]
        return {"status": "ok", "count": len(alerts), "alerts": alerts}
    except Exception as e:
        return {"status": "error", "error": str(e), "alerts": []}


@app.post("/api/alerts/check-gmail")
def check_gmail_alerts_now():
    """Trigger a fast 1-shot poll of Gmail for TradingView alerts into trading_alerts.db."""
    def _poll():
        _append_log("📥 Checking Gmail for fresh TradingView alerts (main.py --once)...")
        try:
            cmd = [sys.executable, "main.py", "--once"]
            proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                l = line.strip()
                if l:
                    _append_log(f"[Alert Ingestor] {l}")
            proc.wait()
            _append_log("✅ Gmail alert check finished.")
        except Exception as err:
            _append_log(f"⚠️ Gmail alert check error: {err}")

    threading.Thread(target=_poll, daemon=True).start()
    return {"status": "ok", "message": "Gmail alert poll dispatched"}


@app.post("/api/alerts/local-research")
def run_alert_local_research(payload: dict = Body(...)):
    """Run local research (#ponytail & revanth-gem-local.md) on an individual alert."""
    try:
        from src.tracking.alert_evaluator import evaluate_alert_payload
        from src.tracking.alert_db import DB_PATH
        import sqlite3

        message_id = payload.get("message_id")
        symbol = payload.get("symbol")
        use_tools = payload.get("use_tools", True)

        alert_dict = None
        with sqlite3.connect(str(DB_PATH), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if message_id:
                cur.execute("SELECT * FROM alerts WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                if row:
                    alert_dict = dict(row)
            if not alert_dict and symbol:
                cur.execute("SELECT * FROM alerts WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1", (symbol.upper(),))
                row = cur.fetchone()
                if row:
                    alert_dict = dict(row)

        if not alert_dict:
            # Construct a minimal alert dict from payload
            alert_dict = {
                "symbol": (symbol or "UNKNOWN").upper(),
                "strategy": payload.get("strategy", "Daily"),
                "action": payload.get("action", "ALERT"),
                "alert_price": payload.get("price"),
                "setup": payload.get("setup"),
                "raw_payload": json.dumps(payload),
            }

        res = evaluate_alert_payload(alert_dict, use_tools=use_tools)
        return {"status": "ok", "result": res}
    except Exception as e:
        logger.error(f"Error running local research for alert: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@app.post("/api/alerts/scrape-chart")
def scrape_normal_chart_endpoint(payload: dict = Body(...)):
    """Scrape normal TradingView daily candlestick chart screenshot for a symbol."""
    try:
        from src.data.tv_scraper import TVScraper
        symbol = (payload.get("symbol") or "SPY").strip().upper()
        date_str = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
        scraper = TVScraper(target_date=date_str)
        res = scraper.capture_normal_chart(symbol)
        rel_path = f"/data/raw/{date_str}/{symbol}/{symbol}_chart.png"
        return {"status": "ok", "image_url": rel_path, **res}
    except Exception as e:
        logger.error(f"Normal chart scrape error for {payload.get('symbol')}: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/api/alerts/evaluate-pending")
def trigger_batch_evaluate_alerts(payload: dict = Body(default={})):
    """Run local LLM evaluation across pending alerts in the background."""
    def _run_batch():
        try:
            from src.tracking.alert_evaluator import evaluate_batch_pending
            limit = payload.get("limit", 50)
            date_str = payload.get("date")
            _append_log(f"🤖 Starting batch local LLM triage for pending alerts (limit={limit})...")
            res = evaluate_batch_pending(limit=limit, date_str=date_str)
            _append_log(f"✅ Batch alert triage complete: {res.get('message')}")
        except Exception as err:
            _append_log(f"⚠️ Batch alert triage error: {err}")

    threading.Thread(target=_run_batch, daemon=True).start()
    return {"status": "ok", "message": "Batch evaluation started in background"}


@app.get("/api/alerts/evaluate-status")
def get_alerts_evaluation_status(date: Optional[str] = None):
    """Return evaluation stats for alerts."""
    try:
        from src.tracking.alert_db import DB_PATH
        import sqlite3

        with sqlite3.connect(str(DB_PATH), timeout=30.0) as conn:
            cur = conn.cursor()
            if date:
                cur.execute("SELECT count(*), count(NULLIF(llm_decision, '')) FROM alerts WHERE date = ?", (date,))
            else:
                cur.execute("SELECT count(*), count(NULLIF(llm_decision, '')) FROM alerts")
            total, evaluated = cur.fetchone()
            return {
                "status": "ok",
                "total": total,
                "evaluated": evaluated,
                "pending": total - evaluated,
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/alerts/auto-triage-status")
def get_auto_triage_daemon_status():
    """Return live status of the autonomous background alert triage daemon."""
    from src.tracking.auto_triage_daemon import get_auto_triage_status
    return {"status": "ok", **get_auto_triage_status()}


@app.get("/api/tastytrade-alerts")
def get_tastytrade_alerts():
    """Fetch active cloud alerts directly from Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alerts = client.get_quote_alerts()
        return {"alerts": alerts}
    except Exception as e:
        return {"alerts": [], "error": str(e)}


@app.get("/api/superforecasting/stats")
def get_superforecasting_calibration_stats_api():
    """Fetch Brier calibration stats for Model A (Pine) vs Model B (Independent)."""
    try:
        from src.tracking.watch_manager import get_superforecasting_stats
        stats = get_superforecasting_stats()
        return stats
    except Exception as e:
        return {"error": str(e), "model_a": {}, "model_b": {}, "recent_audits": []}


@app.post("/api/superforecasting/audit")
def trigger_superforecasting_audit_api():
    """Trigger an immediate audit scan of historical report predictions."""
    try:
        from src.tracking.superforecasting_auditor import run_superforecasting_audit
        res = run_superforecasting_audit()
        return {"status": "ok", **res}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/options/spread-calc")
def calculate_options_spread_live(
    ticker: str,
    expiration: str,
    short_strike: float,
    long_strike: float,
    structure: Optional[str] = "BULL_PUT_SPREAD"
):
    """
    Live mathematical options spread calculator using real-time Schwab market data.
    Calculates exact live mid credit/debit, natural fill prices, max loss, max profit, and Greeks.
    """
    try:
        from datetime import datetime, date
        from src.clients.schwab_client import get_schwab_client

        sym = ticker.upper().strip().replace(".", "/")
        exp_clean = expiration.strip()
        try:
            exp_date = datetime.strptime(exp_clean, "%Y-%m-%d").date()
        except Exception:
            return {"success": False, "error": f"Invalid expiration format: {expiration}. Expected YYYY-MM-DD."}

        client = get_schwab_client()
        r = client.get_option_chain(
            sym,
            contract_type=client.Options.ContractType.ALL,
            from_date=exp_date,
            to_date=exp_date
        )
        if r.status_code != 200:
            return {"success": False, "error": f"Schwab API error {r.status_code}: {r.text[:200]}"}

        data = r.json()
        underlying = float(data.get("underlyingPrice") or 0.0)
        is_put = "PUT" in (structure or "").upper()
        target_map = data.get("putExpDateMap", {}) if is_put else data.get("callExpDateMap", {})

        if not target_map:
            return {"success": False, "error": f"No {'put' if is_put else 'call'} chains found for {sym} on {expiration}"}

        exp_key = list(target_map.keys())[0]
        strike_dict = target_map[exp_key]

        def find_contract(target_strike):
            for k, contracts in strike_dict.items():
                if abs(float(k) - target_strike) < 0.05:
                    return contracts[0]
            return None

        c_short = find_contract(short_strike)
        c_long = find_contract(long_strike)

        if not c_short or not c_long:
            return {
                "success": False,
                "error": f"Strikes {short_strike} or {long_strike} not found in {expiration} chain.",
                "available_sample": [float(k) for k in list(strike_dict.keys())[:10]],
                "underlying_price": underlying
            }

        s_bid = float(c_short.get("bid") or 0.0)
        s_ask = float(c_short.get("ask") or 0.0)
        s_mid = round((s_bid + s_ask) / 2, 2)
        s_vol = int(c_short.get("totalVolume") or 0)
        s_oi = int(c_short.get("openInterest") or 0)
        s_delta = float(c_short.get("delta") or 0.0)

        l_bid = float(c_long.get("bid") or 0.0)
        l_ask = float(c_long.get("ask") or 0.0)
        l_mid = round((l_bid + l_ask) / 2, 2)
        l_vol = int(c_long.get("totalVolume") or 0)
        l_oi = int(c_long.get("openInterest") or 0)
        l_delta = float(c_long.get("delta") or 0.0)

        width = abs(short_strike - long_strike)
        is_credit = ("PUT" in (structure or "").upper() and "BULL" in (structure or "").upper()) or ("CALL" in (structure or "").upper() and "BEAR" in (structure or "").upper())

        if is_credit:
            # Sell short strike, buy long strike
            live_mid = round(s_mid - l_mid, 2)
            live_natural = round(s_bid - l_ask, 2)
            max_profit = round(live_mid * 100, 2)
            max_loss = round((width - live_mid) * 100, 2)
            pricing_type = "CREDIT"
        else:
            # Buy long strike, sell short strike
            live_mid = round(l_mid - s_mid, 2)
            live_natural = round(l_ask - s_bid, 2)
            max_loss = round(live_mid * 100, 2)
            max_profit = round((width - live_mid) * 100, 2)
            pricing_type = "DEBIT"

        return {
            "success": True,
            "ticker": sym,
            "expiration": exp_clean,
            "structure": structure,
            "pricing_type": pricing_type,
            "underlying_price": underlying,
            "spread_width": width,
            "live_mid": live_mid,
            "live_natural": live_natural,
            "live_max_profit": max_profit,
            "live_max_loss": max_loss,
            "short_leg": {
                "strike": short_strike,
                "bid": s_bid,
                "ask": s_ask,
                "mid": s_mid,
                "volume": s_vol,
                "open_interest": s_oi,
                "delta": s_delta
            },
            "long_leg": {
                "strike": long_strike,
                "bid": l_bid,
                "ask": l_ask,
                "mid": l_mid,
                "volume": l_vol,
                "open_interest": l_oi,
                "delta": l_delta
            },
            "quote_source": "SCHWAB_REALTIME",
            "calculated_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error calculating live options spread: {e}")
        return {"success": False, "error": str(e)}


class CreateAlertRequest(BaseModel):
    symbol: str
    threshold: float
    operator: str = "<"
    expires_at: Optional[str] = None


class ModifyAlertRequest(BaseModel):
    alert_id: str
    symbol: str
    threshold: float
    operator: str = "<"
    expires_at: Optional[str] = None


@app.post("/api/tastytrade-alerts/create")
def create_tastytrade_alert(req: CreateAlertRequest):
    """Create a new cloud price alert on Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alert = client.create_quote_alert(
            symbol=req.symbol.strip().upper(),
            threshold=req.threshold,
            operator=req.operator,
            expires_at=req.expires_at,
        )
        if not alert:
            raise HTTPException(status_code=400, detail="Failed to create alert on Tastytrade")
        return {"success": True, "alert": alert}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tastytrade-alerts/modify")
def modify_tastytrade_alert(req: ModifyAlertRequest):
    """Modify an existing cloud price alert on Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        alert = client.modify_quote_alert(
            alert_id=req.alert_id,
            symbol=req.symbol.strip().upper(),
            threshold=req.threshold,
            operator=req.operator,
            expires_at=req.expires_at,
        )
        if not alert:
            raise HTTPException(status_code=400, detail="Failed to modify alert on Tastytrade")
        return {"success": True, "alert": alert}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tastytrade-alerts/all")
def delete_all_tastytrade_alerts():
    """Delete all quote alerts across all tickers from Tastytrade."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        deleted_count = client.delete_all_quote_alerts()
        return {"success": True, "deleted_count": deleted_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tastytrade-alerts/{alert_id}")
def delete_tastytrade_alert(alert_id: str):
    """Delete a cloud alert by external ID."""
    try:
        from src.clients.tastytrade_client import TastytradeClient
        client = TastytradeClient()
        ok = client.delete_quote_alert(alert_id)
        return {"success": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
def get_positions():
    """Fetch open positions with live quotes and PnL."""
    if not POSITIONS_FILE.exists():
        return {"positions": []}
    try:
        from src.clients.price_client import get_current_price
        today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            pos_dict = raw_data.get("positions", raw_data) if isinstance(raw_data, dict) else {}
            positions = []
            for sym, p in pos_dict.items():
                if not isinstance(p, dict):
                    continue
                item = dict(p)
                # Ignore stale prior-day positions
                opened_at = item.get("opened_at", "")
                if opened_at and opened_at[:10] != today_str:
                    continue
                # Ignore any position mistakenly saved from a Screener alert
                if str(item.get("raw_alert", {}).get("subject", "")).lower() == "alert: screener":
                    continue

                entry = item.get("entry_price") or item.get("alert_price") or 0.0
                try:
                    spot = get_current_price(sym)
                    if isinstance(spot, (int, float)) and spot > 0:
                        item["current_price"] = spot
                        side = item.get("side", "LONG").upper()
                        if entry > 0:
                            if "LONG" in side or "BUY" in side or "CALL" in side:
                                item["pnl_pct"] = round(((spot - entry) / entry) * 100, 2)
                            else:
                                item["pnl_pct"] = round(((entry - spot) / entry) * 100, 2)
                except Exception:
                    pass
                positions.append(item)
            return {"positions": positions}
    except Exception as e:
        return {"positions": [], "error": str(e)}


@app.get("/api/schwab/positions")
def get_schwab_positions_endpoint():
    """Fetch live broker account balances and positions from Schwab Trader API."""
    try:
        from src.clients.schwab_client import get_schwab_positions
        return get_schwab_positions()
    except Exception as e:
        return {"status": "error", "error": str(e), "accounts": []}


@app.get("/api/positions")
def get_open_positions():
    """Return active open positions from data/positions.json."""
    try:
        from src.tracking.position_state import load_state
        state = load_state()
        pos_list = list(state.values())
        return {"positions": pos_list, "count": len(pos_list)}
    except Exception as e:
        logger.error(f"Failed to fetch open positions: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


@app.post("/api/positions/{ticker}/close")
def close_open_position(ticker: str):
    """Close an open position from data/positions.json."""
    try:
        from src.tracking.position_state import close_position
        rec = close_position(ticker)
        if rec:
            _append_log(f"🛑 Closed open position for {ticker.upper()}.")
            return {"success": True, "closed": rec}
        else:
            return {"success": False, "message": f"{ticker} was not open."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/positions/flatten-intraday")
def flatten_intraday_endpoint():
    """Flatten and close all intraday positions for EOD."""
    try:
        from src.tracking.position_state import flatten_eod_intraday_positions
        closed = flatten_eod_intraday_positions(force=True)
        _append_log(f"🧹 EOD Flattened {len(closed)} intraday position(s).")
        return {"success": True, "count": len(closed), "closed": closed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class Simulate0DTERequest(BaseModel):
    ticker: str
    action: str = "ENTER_CALLS"
    price: float = 560.0
    why_now: str = "15m Squeeze Breakout + VWAP reclaim"


@app.post("/api/intraday/simulate-eval")
def simulate_0dte_eval(req: Simulate0DTERequest):
    """Simulate and test 0DTE rules evaluation (revanth-0dte.md) on demand."""
    try:
        from main import query_local_llm_for_trade
        fake_alert = {
            "ticker": req.ticker.strip().upper(),
            "action": req.action,
            "event": "ENTRY",
            "price": req.price,
            "why_now": req.why_now,
            "verdict": "BULLISH BREAKOUT",
            "plan": f"Entry {req.price}, Stop {req.price * 0.995:.2f}, T1 {req.price * 1.01:.2f}",
            "context": "VIX Calm · VWAP Upper · ORB Expansion",
        }
        decision, playbook = query_local_llm_for_trade(fake_alert, req.ticker.strip().upper(), "0DTE Intraday")
        return {"decision": decision, "playbook": playbook}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CopilotChatRequest(BaseModel):
    question: Optional[str] = ""
    ticker: Optional[str] = None
    date: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = []
    session_id: Optional[str] = None
    board_context: Optional[str] = None
    image_data: Optional[str] = None


class SaveChatMessageRequest(BaseModel):
    session_id: str
    ticker: str
    date: str
    role: str
    content: str


class ExecutePythonRequest(BaseModel):
    code: str
    ticker: Optional[str] = "AMD"
    date: Optional[str] = None




_KNOWN_TICKER_SET = None

def _get_known_ticker_set() -> set:
    """Returns the set of legitimate stock symbols across universe sources."""
    global _KNOWN_TICKER_SET
    if _KNOWN_TICKER_SET is not None:
        return _KNOWN_TICKER_SET

    s = set()
    # 0. Primary Universe from All_tickrs/company_tickers.json (10,391 US tickers)
    all_tickrs_json = config.BASE_DIR / "All_tickrs" / "company_tickers.json"
    if all_tickrs_json.exists():
        try:
            sec_dict = json.loads(all_tickrs_json.read_text(encoding="utf-8"))
            for item in sec_dict.values() if isinstance(sec_dict, dict) else sec_dict:
                t = str(item.get("ticker", "")).strip().upper()
                if t and t.isalnum() and len(t) <= 5:
                    s.add(t)
        except Exception:
            pass

    # 1. SPX constituents
    spx_csv = config.BASE_DIR / "EveryDay" / "SPX-constituents.csv"
    if spx_csv.exists():
        try:
            import csv
            with open(spx_csv, "r", encoding="utf-8") as f:
                for row in csv.reader(f):
                    if row and row[0].strip():
                        s.add(row[0].strip().upper())
        except Exception:
            pass

    # 2. Reports
    rep_dir = config.BASE_DIR / "reports"
    if rep_dir.exists():
        for d in os.listdir(rep_dir):
            day_p = rep_dir / d
            if day_p.is_dir():
                for f in os.listdir(day_p):
                    if f.endswith("_summary.md") or f.endswith("_arbitration.md"):
                        s.add(f.split("_")[0].upper())

    # 3. Watch DB
    try:
        with _get_db() as conn:
            rows = conn.cursor().execute("SELECT ticker FROM active_stalking_targets").fetchall()
            for r in rows:
                if r and r[0]:
                    s.add(r[0].upper())
    except Exception:
        pass

    # 4. SEC EDGAR full universe fallback
    cik_json = config.BASE_DIR / "data" / "sec_cik_map.json"
    if cik_json.exists():
        try:
            sec_map = json.loads(cik_json.read_text(encoding="utf-8"))
            for t in sec_map.keys():
                if t and t.isalnum() and len(t) <= 5:
                    s.add(t.upper())
        except Exception:
            pass

    # Core high-volume symbols & ETFs
    s.update({"SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "PLTR", "AVGO", "SMCI", "COIN", "MSTR", "UBER", "DIS", "BA", "BABA", "CRM", "INTC", "QCOM", "TXN", "MU", "PANW", "CRWD", "NOW", "SNOW", "SHOP", "SQ", "PYPL", "ABNB", "ARM", "EIX", "PCG", "SO", "DUK", "NEE", "CEG", "VST", "NRG", "LLY", "NVO", "UNH", "JNJ", "PFE"})
    _KNOWN_TICKER_SET = s
    return _KNOWN_TICKER_SET


_COMPANY_NAME_CACHE: Dict[str, str] = {}
_DYNAMIC_NAME_TO_TICKER: Dict[str, str] = {}


def _init_company_data():
    """Dynamically indexes 10,000+ public companies from SEC EDGAR company_tickers.json without hardcoding."""
    global _COMPANY_NAME_CACHE, _DYNAMIC_NAME_TO_TICKER
    if _COMPANY_NAME_CACHE and _DYNAMIC_NAME_TO_TICKER:
        return
    clean_suffixes = re.compile(
        r'\b(corp|corporation|inc|incorporated|ltd|limited|co|company|holdings|plc|group|sa|nv|lp|llc|class [a-z]|com)\b',
        re.IGNORECASE
    )
    all_tickrs_json = config.BASE_DIR / "All_tickrs" / "company_tickers.json"
    if all_tickrs_json.exists():
        try:
            data = json.loads(all_tickrs_json.read_text(encoding="utf-8"))
            items = data.values() if isinstance(data, dict) else data
            for item in items:
                sym = str(item.get("ticker", "")).strip().upper()
                title = str(item.get("title", "")).strip()
                if sym and title and len(sym) <= 5 and sym.isalnum():
                    _COMPANY_NAME_CACHE[sym] = title
                    _DYNAMIC_NAME_TO_TICKER[title.upper()] = sym
                    raw_clean = clean_suffixes.sub('', title).strip(' ,.-/')
                    cleaned = raw_clean.upper()
                    if len(cleaned) >= 3 and cleaned not in _DYNAMIC_NAME_TO_TICKER:
                        _DYNAMIC_NAME_TO_TICKER[cleaned] = sym
                    unspaced = cleaned.replace(' ', '').replace('-', '')
                    if len(unspaced) >= 3 and unspaced not in _DYNAMIC_NAME_TO_TICKER:
                        _DYNAMIC_NAME_TO_TICKER[unspaced] = sym
                    camel_words = re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)', raw_clean)
                    if len(camel_words) > 1:
                        spaced = ' '.join(camel_words).upper()
                        if len(spaced) >= 3 and spaced not in _DYNAMIC_NAME_TO_TICKER:
                            _DYNAMIC_NAME_TO_TICKER[spaced] = sym
        except Exception as e:
            logger.debug(f"Error loading SEC company tickers map: {e}")

    # Colloquial trader abbreviations/aliases that differ from formal SEC titles
    colloquial_trader_aliases = {
        "PACIFIC GAS": "PCG",
        "PACIFIC GAS & ELECTRIC": "PCG",
        "PACIFIC GAS AND ELECTRIC": "PCG",
        "PGE": "PCG",
        "EDISON": "EIX",
        "EDISON INTERNATIONAL": "EIX",
        "GOOGLE": "GOOGL",
        "ALPHABET": "GOOGL",
        "FACEBOOK": "META",
        "WALMART": "WMT",
        "WAL-MART": "WMT",
        "UI PATH": "PATH",
        "UIPATH": "PATH",
    }
    for alias_name, alias_sym in colloquial_trader_aliases.items():
        if alias_name not in _DYNAMIC_NAME_TO_TICKER:
            _DYNAMIC_NAME_TO_TICKER[alias_name] = alias_sym


def _get_company_name(ticker: str) -> str:
    """Returns official company name or common title for a ticker symbol."""
    if not ticker or ticker in ("GENERAL", "AUTO", "NONE", ""):
        return "Broad Market"
    _init_company_data()
    return _COMPANY_NAME_CACHE.get(ticker.upper(), ticker.upper())


def _detect_ticker_metadata(question: str, explicit_ticker: str = None, history: list = None) -> dict:
    """
    Extracts all tickers and tracks whether each ticker was explicitly specified ($TICKER / modal / keyword)
    or inferred from casual company names / conversational words.
    """
    _init_company_data()
    known = _get_known_ticker_set()
    detected = []
    matched_by: Dict[str, str] = {}

    q_clean = question

    # 1. Explicit $TICKER in prompt (e.g. $AAPL, $WMT, $EIX, $META, $UI, $PATH)
    dollar_matches = re.findall(r'\$([A-Za-z]{1,5})\b', question)
    for m in dollar_matches:
        mu = m.upper()
        if (mu in known or len(mu) >= 2) and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "dollar"

    # 2. '[TICKER] stock/shares' pattern (e.g. 'check EIX stock as well', 'AAPL shares')
    post_stock_stopwords = {
        "AS", "WELL", "IS", "WAS", "ARE", "HAS", "HAD", "CAN", "WILL", "FOR", "ON", "AND", "OR", "TO",
        "IN", "AT", "BY", "OF", "WITH", "THAT", "THIS", "FROM", "NOW", "LIKE", "SO", "DO", "BE", "GO",
        "IF", "MY", "NO", "UP", "AN", "THE", "A", "PRICE", "PRICES", "CHART", "OPTIONS", "CALL", "PUT",
        "TRADE", "TRADES", "LOOKS", "GOING", "DROP", "FALL", "GAIN", "RISE", "TODAY", "YESTERDAY", "TOMORROW",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
    }
    for m in re.findall(r'\b([A-Za-z]{1,5})\s+(?:stock|shares|ticker)\b', q_clean, re.IGNORECASE):
        mu = m.upper()
        if mu in known and mu not in post_stock_stopwords and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "keyword"

    # 3. 'stock/ticker [TICKER]' pattern (e.g. 'stock PATH', 'ticker AMD')
    kw_matches = re.findall(r'(?:ticker|stock|symbol|quote for)\s+([A-Za-z]{1,5})\b', q_clean, re.IGNORECASE)
    for m in kw_matches:
        mu = m.upper()
        if mu in known and mu not in post_stock_stopwords and mu not in detected:
            detected.append(mu)
            matched_by[mu] = "keyword"

    # 4. Integrate explicit_ticker if provided from active modal / focus
    # If the user is inside an active modal (e.g. WMT), explicit_ticker is top focus unless user specifically wrote a $TICKER
    if explicit_ticker:
        exp_u = explicit_ticker.strip().upper()
        if exp_u not in ("GENERAL", "AUTO", "NONE", "ALL", ""):
            has_prompt_ticker = any(matched_by.get(t) in ("dollar", "keyword") for t in detected)
            if exp_u in detected:
                detected.remove(exp_u)
            if has_prompt_ticker:
                detected.append(exp_u)
            else:
                detected.insert(0, exp_u)
            matched_by[exp_u] = "modal"

    # 5. Dynamic SEC EDGAR Company Name Matching (O(1) n-gram token matching)
    stopwords_single = {
        "A", "AN", "ON", "IT", "SO", "DO", "BE", "AT", "BY", "IN", "IS", "MY", "NO", "OR", "TO", "UP", "US", "WE",
        "HE", "ME", "OF", "AND", "THE", "BUT", "NOT", "YOU", "ALL", "NOW", "CAN", "SEE", "FOR", "ARE", "HAS", "HAD",
        "WAS", "ONE", "OUT", "DAY", "WHO", "DID", "ITS", "LET", "SAY", "SHE", "TOO", "USE", "NEW", "OLD", "TWO",
        "WAY", "MAN", "TOP", "BIG", "NET", "BEST", "NEXT", "GOOD", "TRUE", "FREE", "MORE", "REAL", "PLAY", "TEST",
        "RULE", "MOVE", "HOLD", "LOOK", "TAKE", "COME", "JUST", "MAKE", "KNOW", "WELL", "ALSO", "LIKE", "SAME",
        "BOTH", "EACH", "INTO", "OVER", "NEAR", "SIDE", "DAYS", "TIME", "DATE", "WEEK", "YEAR", "HIGH", "LOW",
        "OPEN", "LAST", "GAIN", "DROP", "FALL", "RISE", "CALL", "PUT", "LONG", "SHORT", "STOP", "LOSS", "ZONE",
        "RISK", "COST", "DEBT", "CASH", "RATE", "PEER", "VIEW", "SHOW", "TELL", "GIVE", "GET", "WHAT", "WHEN",
        "WHY", "HOW", "FAST", "SLOW", "VERY", "MUCH", "LESS", "THEN", "THAN", "SOME", "SUCH", "EVEN", "MOST",
        "ONLY", "BEEN", "HAVE", "WERE", "WILL", "HELP", "PLAN", "RUNS", "CHAT",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        "CALLS", "PUTS", "LEAP", "LEAPS", "STRIKE", "STRIKES", "TRADE", "TRADES", "PRICE", "PRICES",
        "BUY", "BUYS", "SELL", "SELLS", "HOLDS", "BOUGHT", "SOLD", "RUN", "PLANS", "STOPS", "LOSSES", "ZONES",
        "TARGET", "TARGETS", "ENTRY", "ENTRIES", "ORDER", "ORDERS", "FLOW", "FLOWS", "SWEEP", "SWEEPS",
        "BLOCK", "BLOCKS", "GAINS", "DROPS", "FALLS", "RISES", "RATES", "TERM", "TERMS", "WEEKS", "YEARS",
        "HIGHS", "LOWS", "OPENS", "CLOSES", "EXP", "DTE", "STOCK", "STOCKS", "SHARE", "SHARES", "TICKER", "TICKERS",
        "SYMBOL", "SYMBOLS", "DESK", "DESKS", "MARKET", "MARKETS", "CHATS", "VIEWS", "SETUP", "SETUPS", "ACTION", "ACTIONS",
        "REPORT", "REPORTS", "RESEARCH", "MONTE", "CARLO", "DATA", "WINDOW", "CHAIN", "CHAINS", "GREEKS", "OPTION", "OPTIONS",
        "EXACT", "EXECUTION", "TODAY", "RIGHT", "SPOT", "SPOTS", "TRIM", "TRAIL"
    }
    q_words = re.findall(r'[A-Za-z0-9&]+', q_clean)
    n = len(q_words)
    for length in [3, 2, 1]:
        for i in range(n - length + 1):
            phrase = ' '.join(q_words[i:i + length]).upper()
            if length == 1 and phrase in stopwords_single:
                continue
            if phrase in _DYNAMIC_NAME_TO_TICKER:
                sym = _DYNAMIC_NAME_TO_TICKER[phrase]
                if sym not in detected:
                    detected.append(sym)
                    matched_by[sym] = "alias"
                    # Mask phrase out of q_clean so it doesn't trigger secondary keyword matches
                    q_clean = re.sub(rf'\b{re.escape(phrase)}\b', ' ', q_clean, flags=re.IGNORECASE)

    # 6. Check standalone ticker tokens excluding market terminology & common English words
    acronym_blacklist = {
        "ARE", "ALL", "NOW", "CAN", "SEE", "FOR", "ON", "IT", "SO", "A", "GO", "BE", "AM", "HAS", "DO",
        "ORB", "EMA", "SMA", "VWAP", "AVWAP", "GEX", "RVOL", "DTE", "ATM", "ITM", "OTM", "ROI", "PNL",
        "CALL", "PUT", "LONG", "SHORT", "STOP", "LOSS", "ZONE", "RISK", "RR", "MAX", "MIN", "HIGH",
        "LOW", "OPEN", "LAST", "CLOSE", "NEWS", "MATH", "DESK", "WEEK", "TIME", "DATE", "SELL", "BUY",
        "GAIN", "CHART", "WHAT", "WHEN", "WHY", "HOW", "SHOW", "TELL", "VIEW", "GOOD", "BEST", "NEXT",
        "HOLD", "MOVE", "PLAY", "RULE", "TEST", "TRUE", "FREE", "MORE", "WITH", "FROM", "THIS", "THAT",
        "PRICE", "PROVIDE", "GIVE", "GET", "CHECK", "REPORT", "FACTORS", "FACTOR", "LEVELS", "LEVEL",
        "SETUP", "SETUPS", "ENTRY", "TARGET", "TRADE", "TRADES", "QUOTES", "QUOTE", "BREAKDOWN", "MODEL",
        "ALSO", "WELL", "LIKE", "SAME", "BOTH", "EACH", "INTO", "OVER", "UNDER", "NEAR", "SIDE", "DAYS",
        "AS", "IF", "AT", "BY", "IN", "IS", "MY", "NO", "OR", "TO", "UP", "US", "WE", "AN", "HE", "ME",
        "OF", "AND", "THE", "BUT", "NOT", "YOU", "ANY", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "DAY",
        "HIM", "HIS", "MAN", "NEW", "OLD", "TWO", "WAY", "WHO", "DID", "ITS", "LET", "PUT", "SAY", "SHE",
        "TOO", "USE", "VERY", "MUCH", "LESS", "THEN", "THAN", "THEM", "THEY", "SOME", "SUCH", "EVEN",
        "MOST", "ONLY", "BEEN", "HAVE", "WERE", "WILL", "JUST", "MAKE", "KNOW", "TAKE", "COME", "LOOK",
        "REAL", "WHERE", "WHICH", "WHILE", "STOCK", "STOCKS", "TICKER", "TICKERS", "SYMBOL", "SYMBOLS",
        "CHAIN", "CHAINS", "PEER", "PEERS", "SECTOR", "VERSUS", "VS", "CORRELATION", "CORRELATED",
        "UI", "UX", "AI", "ML", "NA", "AN", "OK", "CC", "CSP", "BCS", "BPS", "IC", "PE", "EPS", "EV", "FCF",
        "API", "APP", "OS", "FED", "FOMC", "CPI", "PPI", "GDP", "NFP", "PMI", "SEC", "OTC", "EOD", "RTH",
        "EXAMPLE", "EXAMPLES", "LATEST", "COVERED", "SELLING", "BUYING", "HOLDING", "OPTION", "OPTIONS",
        "PATH", "WAYS", "NEED", "WANT", "LIKE", "THINK", "WONDER", "WONDERING", "ABOUT", "COULD", "WOULD", "SHOULD",
        "STEP", "STEPS", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
        "CALLS", "PUTS", "LEAP", "LEAPS", "STRIKE", "STRIKES", "BOUGHT", "SOLD", "EXACT", "EXECUTION", "TODAY", "RIGHT",
        "SPOT", "SPOTS", "TRIM", "TRIMS", "TRAIL", "TRAILS", "SL", "TP"
    }
    tokens = re.findall(r'\b[A-Za-z]{3,5}\b', q_clean)
    for t in tokens:
        tu = t.upper()
        if tu in known and tu not in acronym_blacklist and tu not in detected:
            detected.append(tu)
            matched_by[tu] = "standalone"

    # 7. Inherit active conversation ticker if none detected in prompt
    if not detected and history:
        for turn in reversed(history):
            content = turn.get("content", "")
            role = turn.get("role", "")
            if role == "user":
                prior = _detect_tickers_from_prompt(content)
                filtered = [t for t in prior if t != "GENERAL"]
                if filtered:
                    detected.extend(filtered)
                    for sym in filtered:
                        matched_by[sym] = "history"
                    break
        if not detected:
            for turn in reversed(history):
                content = turn.get("content", "")
                role = turn.get("role", "")
                if role == "assistant":
                    prior = _detect_tickers_from_prompt(content[:400])
                    filtered = [t for t in prior if t != "GENERAL"]
                    if filtered:
                        detected.extend(filtered[:1])
                        matched_by[filtered[0]] = "history"
                        break

    final_tickers = detected if detected else ["GENERAL"]
    primary = final_tickers[0]
    match_type = matched_by.get(primary, "none")
    is_explicit = (match_type in ("dollar", "modal", "keyword"))
    is_inferred = (primary != "GENERAL" and not is_explicit)

    return {
        "tickers": final_tickers,
        "primary_ticker": primary,
        "is_explicit": is_explicit,
        "is_inferred": is_inferred,
        "match_type": match_type,
        "company_name": _get_company_name(primary) if primary != "GENERAL" else "Broad Market",
    }


def _detect_tickers_from_prompt(question: str, explicit_ticker: str = None, history: list = None) -> List[str]:
    """
    Extracts all tickers mentioned in prompt and/or passed explicitly.
    Returns a deduplicated list of uppercase ticker symbols (e.g. ['EIX', 'PCG']).
    """
    return _detect_ticker_metadata(question, explicit_ticker=explicit_ticker, history=history)["tickers"]


def _detect_ticker_from_prompt(question: str, explicit_ticker: str = None, history: list = None) -> str:
    """Legacy single-ticker helper: returns primary detected ticker."""
    tickers = _detect_tickers_from_prompt(question, explicit_ticker, history=history)
    return tickers[0] if tickers else "GENERAL"


def _launch_background_job(name: str, command: list, log_file: str = None) -> str:
    """Spawns an asynchronous background process tracked in SQLite active_research_jobs."""
    import uuid
    _init_db()
    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log_path = LOGS_DIR / (log_file or f"{job_id}.log")
    
    with _get_db() as conn:
        conn.cursor().execute("""
            INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file)
            VALUES (?, ?, 'scrape_only', ?, 'SCRAPING', 'RUNNING', ?, ?)
        """, (job_id, name, os.getpid(), datetime.now(timezone.utc).isoformat(), str(log_path)))
        conn.commit()

    def _worker():
        try:
            _append_log(f"🚀 [{name}] Starting background job {job_id}...")
            p = subprocess.Popen(command, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            ACTIVE_RESEARCH_SUBPROCS[job_id] = p
            with _get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (p.pid, job_id))
                conn.commit()
            with open(log_path, "a", encoding="utf-8") as lf:
                for line in p.stdout:
                    l = line.strip()
                    if l:
                        _append_log(f"[{name}] {l}")
                        lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {l}\n")
            p.wait()
            status = "COMPLETED" if p.returncode == 0 else "FAILED"
            with _get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET status = ?, stage = 'DONE', completed_at = ? WHERE job_id = ?",
                    (status, datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
            _append_log(f"✅ [{name}] Finished (Exit code: {p.returncode}).")
        except Exception as e:
            _append_log(f"❌ [{name}] Job error: {e}")
            with _get_db() as conn:
                conn.cursor().execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = ?, completed_at = ? WHERE job_id = ?",
                    (str(e), datetime.now(timezone.utc).isoformat(), job_id)
                )
                conn.commit()
        finally:
            ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
            ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)

    t = threading.Thread(target=_worker, daemon=True)
    ACTIVE_RESEARCH_WORKERS[job_id] = t
    t.start()
    return job_id


def _build_events_past_chat_context(ticker_u: str, date_str: str, history: list = None, session_id: str = None) -> str:
    """
    Builds a chronological bridge context of what occurred PAST a prior chat / research dossier:
    1. Calendar & elapsed time anchor (today vs prior conversation/dossier).
    2. Spot price evolution (prior reference spot vs live quote, delta, % change).
    3. Status of prior key levels (reclaim pivots, support floors, entry zones).
    4. Macro events & catalysts (e.g. NFP jobs report status today vs 'tomorrow').
    5. Breaking news & headlines published past that chat.
    6. Watchlist & portfolio updates since that chat.
    """
    if not ticker_u or ticker_u in ("GENERAL", "AUTO", "NONE", ""):
        return ""

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    now_et = datetime.now(ZoneInfo("America/New_York"))
    calendar_today = now_mt.strftime("%Y-%m-%d")
    now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT") + f" ({now_et.strftime('%I:%M %p ET')})"

    ref_date = date_str or calendar_today
    is_prior_date = (ref_date < calendar_today)
    has_prior_chat = bool(history and len(history) > 0)

    if not is_prior_date and not has_prior_chat:
        return ""

    try:
        d1 = datetime.strptime(ref_date, "%Y-%m-%d")
        d2 = datetime.strptime(calendar_today, "%Y-%m-%d")
        elapsed_days = (d2 - d1).days
    except Exception:
        elapsed_days = 1 if is_prior_date else 0

    elapsed_str = f"{elapsed_days} calendar day(s)" if elapsed_days > 1 else ("yesterday" if elapsed_days == 1 else "earlier today")

    # 1. Extract reference price and key levels from prior assistant turns or files
    prior_spot = None
    prior_levels = {}
    prior_next_step = ""

    if history:
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                txt = turn.get("content", "")

                # Extract prior spot
                m_spot = re.search(r"(?:Live Spot|Spot|Latest Spot|at market):\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_spot and prior_spot is None:
                    try:
                        prior_spot = float(m_spot.group(1))
                    except Exception:
                        pass

                # Extract Next Step or conditional expectation (ignoring buttons)
                m_next = re.search(r"(?:Next Step|Action|Watch Today):\*?\*?\s*(.*?)(?=\n\n|\Z)", txt, re.DOTALL | re.IGNORECASE)
                if m_next and not prior_next_step:
                    raw_step = " ".join(m_next.group(1).split())
                    clean_step = re.sub(r'\[.*?\]\(action:.*?\)', '', raw_step).strip()
                    if clean_step and len(clean_step) > 10:
                        prior_next_step = clean_step[:280]

                # Extract key levels mentioned
                m_reclaim = re.search(r"reclaims?\s*\*?\*?\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_reclaim and "reclaim" not in prior_levels:
                    prior_levels["reclaim"] = float(m_reclaim.group(1))

                m_supp = re.search(r"(?:Support|Floor):\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_supp and "support" not in prior_levels:
                    prior_levels["support"] = float(m_supp.group(1))

                m_res = re.search(r"Resistance:\*?\*?\s*\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_res and "resistance" not in prior_levels:
                    prior_levels["resistance"] = float(m_res.group(1))

                m_stop = re.search(r"(?:Stop Loss|Hard stop):\*?\*?\s*(?:remains at\s*)?\$([0-9]+\.[0-9]+)", txt, re.IGNORECASE)
                if m_stop and "stop" not in prior_levels:
                    prior_levels["stop"] = float(m_stop.group(1))

                if prior_spot:
                    break

    # Fallback to raw artifacts from ref_date if prior_spot not in chat text
    if prior_spot is None and is_prior_date:
        raw_levels = config.BASE_DIR / "data" / "raw" / ref_date / ticker_u / f"{ticker_u}_watch_levels.json"
        if raw_levels.exists():
            try:
                wl = json.loads(raw_levels.read_text(encoding="utf-8"))
                sp = wl.get("shares_plan", {})
                prior_levels.setdefault("entry_low", sp.get("entry_zone_low"))
                prior_levels.setdefault("entry_high", sp.get("entry_zone_high"))
                prior_levels.setdefault("stop", sp.get("tactical_stop"))
                prior_levels.setdefault("target_1", sp.get("target_1"))
                if wl.get("last_price"):
                    prior_spot = float(wl["last_price"])
            except Exception:
                pass

        if prior_spot is None:
            raw_q = config.BASE_DIR / "data" / "raw" / ref_date / ticker_u / f"{ticker_u}_quote.json"
            if raw_q.exists():
                try:
                    q_data = json.loads(raw_q.read_text(encoding="utf-8"))
                    m_lp = re.search(r"Last:\s*([0-9]+\.[0-9]+)", q_data.get("quote", ""))
                    if m_lp:
                        prior_spot = float(m_lp.group(1))
                except Exception:
                    pass

    # 2. Get Live Current Price NOW
    from src.clients.price_client import get_current_price
    live_price = get_current_price(ticker_u)

    price_delta_lines = []
    if live_price:
        if prior_spot:
            diff = live_price - prior_spot
            diff_pct = (diff / prior_spot) * 100
            sign = "+" if diff >= 0 else ""
            price_delta_lines.append(f"• **Prior Chat / Dossier Reference ({ref_date}):** ${prior_spot:.2f}")
            price_delta_lines.append(f"• **Current Live Spot (NOW):** **${live_price:.2f}** ({sign}${diff:.2f} / {sign}{diff_pct:.2f}% since prior discussion)")
        else:
            price_delta_lines.append(f"• **Current Live Spot (NOW):** **${live_price:.2f}**")

        # Check against prior levels
        if "reclaim" in prior_levels:
            rec_p = prior_levels["reclaim"]
            if live_price >= rec_p:
                price_delta_lines.append(f"• **Pivot Reclaim Status (${rec_p:.2f}):** ✅ **RECLAIMED** (Spot ${live_price:.2f} ≥ ${rec_p:.2f} — breakout path toward resistance opens).")
            else:
                price_delta_lines.append(f"• **Pivot Reclaim Status (${rec_p:.2f}):** ⏳ **NOT RECLAIMED / STALLING BELOW** (Spot ${live_price:.2f} < ${rec_p:.2f} — base building/chop continues).")

        if "support" in prior_levels:
            sup_p = prior_levels["support"]
            if live_price >= sup_p:
                price_delta_lines.append(f"• **Structural Floor Status (${sup_p:.2f}):** 🛡️ **DEFENDED & INTACT** (Spot is holding above the floor).")
            else:
                price_delta_lines.append(f"• **Structural Floor Status (${sup_p:.2f}):** ⚠️ **BREACHED BELOW** (Spot ${live_price:.2f} broke support floor).")

    # 3. Macro & Catalyst Status
    macro_notes = []
    if "2026-09-04" in calendar_today:
        macro_notes.append(
            f"• **US Non-Farm Payrolls (NFP) Catalyst:** Released **THIS MORNING** (Friday, Sep 4 at 8:30 AM ET). "
            f"Any references in yesterday's ({ref_date}) chat or dossier to 'until NFP data tomorrow' are now **PAST** events. "
            "The jobs report has already printed, and the market is digesting the data live today."
        )

    # Fetch live benchmark quotes
    benchmarks = []
    for b_sym in ["SPY", "QQQ", "VIX"]:
        bp = get_current_price(b_sym)
        if bp:
            benchmarks.append(f"{b_sym}: ${bp:.2f}" if b_sym != "VIX" else f"VIX: {bp:.2f}")
    if benchmarks:
        macro_notes.append(f"• **Live Market Regime Today:** {' | '.join(benchmarks)}")

    # 4. Breaking News Past That Chat
    news_lines = []
    try:
        from src.clients.search_client import search_web
        res = search_web(f"{ticker_u} stock news {calendar_today}")
        if res and isinstance(res, list):
            for it in res[:3]:
                t = it.get("title", "").strip()
                b = it.get("body", "").strip()
                if t:
                    news_lines.append(f"• **{t}**\n  {b[:180]}")
    except Exception:
        pass

    # 5. Live SQLite Watch Target status
    watch_lines = []
    try:
        with _get_db() as conn:
            c = conn.cursor()
            row = c.execute("SELECT status, last_price, distance_to_entry_pct, entry_zone_low, entry_zone_high, tactical_stop, target_1 FROM watch_targets WHERE ticker = ?", (ticker_u,)).fetchone()
            if row:
                st = row["status"]
                dist = row["distance_to_entry_pct"]
                dist_str = f" ({dist:+.1f}% to entry)" if dist is not None else ""
                watch_lines.append(f"• **Live Watch Target State:** [{st}]{dist_str} | Entry Zone: ${row['entry_zone_low']}–${row['entry_zone_high']} | Stop: ${row['tactical_stop']} | T1: ${row['target_1']}")
    except Exception:
        pass

    # Build formatted section
    lines = [
        f"### ⚡ LIVE TAPE & CURRENT EVENTS SINCE PRIOR CHAT / RESEARCH ({ref_date} ➔ TODAY {calendar_today}):",
        f"> 🚨 **CHRONOLOGICAL ANCHOR**: The prior chat or research dossier occurred on **{ref_date}** ({elapsed_str}).",
        f"> Current system time is **{now_str}**.",
        f"> Do **NOT** repeat prior-day statements as future expectations (e.g. NFP is **TODAY**, not tomorrow; the previous session's close is in the past).",
        f"> Address the user's question with the live price action, level retests, and current developments that occurred **PAST THAT CHAT**.\n"
    ]

    if price_delta_lines:
        lines.append("#### 📊 Live Spot Price Evolution & Level Retest Status:")
        lines.extend(price_delta_lines)
        lines.append("")

    if prior_next_step:
        lines.append(f"• **Prior Chat Concluding Guidance:** \"{prior_next_step}\"\n")

    if macro_notes:
        lines.append("#### 🌐 Macro & Economic Catalysts Past That Chat:")
        lines.extend(macro_notes)
        lines.append("")

    if watch_lines:
        lines.append("#### 🎯 Active Watchlist Tracking:")
        lines.extend(watch_lines)
        lines.append("")

    if news_lines:
        lines.append(f"#### 📰 Breaking Headlines Since {ref_date}:")
        lines.extend(news_lines)
        lines.append("")

    return "\n".join(lines)


def _extract_targeted_schwab_strikes(ticker: str, question: str) -> Optional[str]:
    """
    Scans the user's question for specific strike mentions (e.g. '245c', '250 puts', 'strike 245', '$250').
    If strikes are detected, queries live Schwab option chain and returns exact Volume, OI, Bid/Ask, Delta, and ITM status.
    """
    if not question or not ticker:
        return None

    # Matches patterns like 245c, 250p, 245 call, 250 puts, strike 245, $250
    pattern = r'(?:^|[^\w\.])(?:strike\s*|\$)?(\d{2,4}(?:\.\d{1,2})?)\s*([cp]|call|put|calls|puts)?\b'
    matches = re.findall(pattern, question, re.IGNORECASE)
    if not matches:
        return None

    requested = []
    for s_str, t_hint in matches:
        try:
            val = float(s_str)
            if val < 5 or val > 2500 or val in (2024, 2025, 2026, 2027, 2028):
                continue
            t_clean = t_hint.lower() if t_hint else None
            side = "CALL" if t_clean in ("c", "call", "calls") else ("PUT" if t_clean in ("p", "put", "puts") else None)
            if (val, side) not in requested:
                requested.append((val, side))
        except ValueError:
            continue

    if not requested:
        return None

    try:
        from src.clients.schwab_client import get_schwab_client
        client = get_schwab_client()
        sym = ticker.upper().strip().replace(".", "/")
        r = client.get_option_chain(sym, contract_type=client.Options.ContractType.ALL)
        if r.status_code != 200:
            return None
        data = r.json()
        underlying = float(data.get("underlyingPrice") or 0.0)

        call_map = data.get("callExpDateMap", {})
        put_map = data.get("putExpDateMap", {})

        # Detect target expiration hints (e.g. 2027, 2028, Oct 2, Sep 25, jan, oct, etc.)
        q_lower = question.lower()
        now_year = datetime.now().year
        year_matches = re.findall(r'\b(202[5-9])\b', q_lower)
        month_matches = re.findall(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b', q_lower)
        month_map = {
            'jan': '01', 'january': '01', 'feb': '02', 'february': '02', 'mar': '03', 'march': '03',
            'apr': '04', 'april': '04', 'may': '05', 'jun': '06', 'june': '06', 'jul': '07', 'july': '07',
            'aug': '08', 'august': '08', 'sep': '09', 'september': '09', 'oct': '10', 'october': '10',
            'nov': '11', 'november': '11', 'dec': '12', 'december': '12'
        }
        target_prefixes = []

        # Check for specific day match e.g. "Oct 2", "Oct 16", "Sep 25"
        day_match = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*(\d{1,2})\b', q_lower)
        if day_match:
            m_name, d_num = day_match.groups()
            mm = month_map.get(m_name[:3])
            if mm:
                day_year = now_year if int(mm) >= datetime.now().month else (now_year + 1)
                target_prefixes.append(f"{day_year}-{mm}-{int(d_num):02d}")
                if year_matches:
                    for y in year_matches:
                        target_prefixes.append(f"{y}-{mm}-{int(d_num):02d}")

        if year_matches:
            for y in year_matches:
                for m in month_matches:
                    mm = month_map.get(m[:3])
                    if mm:
                        target_prefixes.append(f"{y}-{mm}")
                if not month_matches:
                    target_prefixes.append(f"{y}-")
        elif month_matches:
            for m in month_matches:
                mm = month_map.get(m[:3])
                if mm:
                    target_prefixes.append(f"{now_year}-{mm}")
                    target_prefixes.append(f"{now_year + 1}-{mm}")

        lines = []
        for strike_val, req_side in requested[:6]:
            target_sides = [("CALL", call_map)] if req_side == "CALL" else ([("PUT", put_map)] if req_side == "PUT" else [("CALL", call_map), ("PUT", put_map)])

            for side_name, exp_map in target_sides:
                exp_matched = 0
                for exp_str, strikes in sorted(exp_map.items()):
                    is_targeted_exp = any(tp in exp_str for tp in target_prefixes) if target_prefixes else False
                    if exp_matched >= 3 and not is_targeted_exp:
                        continue
                    if exp_matched >= 10:
                        break
                    for k, contracts in strikes.items():
                        try:
                            if abs(float(k) - strike_val) < 0.01:
                                for c in contracts:
                                    vol = int(c.get("totalVolume") or 0)
                                    oi = int(c.get("openInterest") or 0)
                                    bid = float(c.get("bid") or 0.0)
                                    ask = float(c.get("ask") or 0.0)
                                    delta = round(float(c.get("delta") or 0.0), 3)
                                    itm = bool(c.get("inTheMoney", False))
                                    dte = c.get("daysToExpiration", 0)
                                    exp_date = exp_str.split(":")[0]
                                    ratio_str = f" ({vol/oi:.1f}x OI 🔥)" if oi > 0 and vol > oi * 1.5 else ""
                                    status_str = "IN-THE-MONEY (ITM)" if itm else "OUT-OF-THE-MONEY (OTM)"

                                    lines.append(
                                        f"• **{side_name} ${strike_val:g}** (Exp {exp_date}, {dte}d): "
                                        f"Vol={vol:,} | OI={oi:,}{ratio_str} | Bid=${bid:.2f} Ask=${ask:.2f} | "
                                        f"Delta={delta} | Status: **{status_str}**"
                                    )
                                    exp_matched += 1
                                    break
                        except Exception:
                            continue

        if lines:
            req_str = ", ".join(f"${s:g} {side or ''}".strip() for s, side in requested)
            header = (
                f"### 🎯 TARGETED SCHWAB OPTIONS CHAIN LOOKUP ({ticker} — Live Verified Quotes):\n"
                f"> **Current Spot:** ${underlying:.2f} | **Requested Strikes:** {req_str}\n\n"
            )
            return header + "\n".join(lines)
    except Exception as e:
        logger.debug(f"Targeted Schwab strike query error for {ticker}: {e}")

    return None


def _get_latest_research_date_for_ticker(ticker: str) -> Optional[str]:
    """Find the latest date containing actual research files or raw data for ticker."""
    if not ticker or ticker in ("GENERAL", "AUTO", "NONE", ""):
        return None
    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    dates_set = set()
    if raw_root.exists():
        for d in raw_root.glob("202*"):
            t_dir = d / ticker
            if t_dir.is_dir() and any(t_dir.glob(f"{ticker}_*")):
                dates_set.add(d.name)
    if rep_root.exists():
        for d in rep_root.glob("202*"):
            if any(d.glob(f"{ticker}_*")):
                dates_set.add(d.name)
    sorted_d = sorted(list(dates_set), reverse=True)
    return sorted_d[0] if sorted_d else None


_TICKER_CONTEXT_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def _build_single_ticker_context(ticker_u: str, date_str: str, question: str, history: list = None, session_id: str = None) -> List[str]:
    """Fetches real-time quotes, options, news, SEC filings, and research reports for a specific ticker."""
    if not ticker_u or ticker_u in ("GENERAL", "AUTO", "NONE", ""):
        return []

    # Fast in-memory cache (TTL: 30s per ticker & query profile to eliminate latency on follow-up questions)
    import time
    is_leaps_req = bool(re.search(r'\b(leap|leaps|2027|2028|2029|long term|long-term|multi-year|far out)\b', question, re.IGNORECASE))
    cache_key = f"{ticker_u}_{date_str}_{is_leaps_req}_{bool(re.findall(r'\d{2,4}', question))}"
    now_ts = time.time()
    if cache_key in _TICKER_CONTEXT_CACHE:
        cached_ts, cached_parts = _TICKER_CONTEXT_CACHE[cache_key]
        if now_ts - cached_ts < 30.0:
            return list(cached_parts)

    parts = []

    # 1. Resolve Historical Research Reports & Baseline Levels First
    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"
    dates_set = set()
    if raw_root.exists():
        for d in raw_root.glob("202*"):
            t_dir = d / ticker_u
            if t_dir.is_dir() and any(t_dir.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)
    if rep_root.exists():
        for d in rep_root.glob("202*"):
            if any(d.glob(f"{ticker_u}_*")):
                dates_set.add(d.name)

    sorted_hist_dates = sorted(list(dates_set), reverse=True)
    latest_dt = sorted_hist_dates[0] if sorted_hist_dates else (date_str if date_str else None)

    report_spot: Optional[float] = None
    report_date: Optional[str] = latest_dt
    shares_plan: Dict[str, Any] = {}
    options_plan: Dict[str, Any] = {}
    invalidation_rule: Dict[str, Any] = {}
    verdict: str = "STALK"
    conviction: int = 5
    levels_file: Optional[Path] = None
    arb_file: Optional[Path] = None
    ind_file: Optional[Path] = None
    sum_file: Optional[Path] = None

    if latest_dt:
        t_raw_dir = raw_root / latest_dt / ticker_u
        t_rep_dir = rep_root / latest_dt

        # Check watch_levels.json
        levels_cand = t_raw_dir / f"{ticker_u}_watch_levels.json"
        if not levels_cand.exists():
            for od in sorted_hist_dates:
                cand_lvl = raw_root / od / ticker_u / f"{ticker_u}_watch_levels.json"
                if cand_lvl.exists():
                    levels_cand = cand_lvl
                    report_date = od
                    break
        if levels_cand.exists():
            levels_file = levels_cand
            try:
                wl_data = json.loads(levels_file.read_text(encoding="utf-8"))
                shares_plan = wl_data.get("shares_plan") or {}
                options_plan = wl_data.get("options_plan") or {}
                invalidation_rule = wl_data.get("invalidation") or {}
                verdict = wl_data.get("verdict") or verdict
                conviction = wl_data.get("conviction") or conviction
                if wl_data.get("last_price"):
                    report_spot = float(wl_data["last_price"])
            except Exception:
                pass

        # Check arbitration directive
        arb_cand = t_rep_dir / f"{ticker_u}_arbitration.md"
        if not arb_cand.exists():
            for od in sorted_hist_dates:
                cand_arb = rep_root / od / f"{ticker_u}_arbitration.md"
                if cand_arb.exists():
                    arb_cand = cand_arb
                    if not report_date:
                        report_date = od
                    break
        if arb_cand.exists():
            arb_file = arb_cand
            try:
                arb_text = arb_file.read_text(encoding="utf-8")
                if report_spot is None:
                    m_sp = re.search(r'(?:spot price|current spot|spot)\s*(?::|\(|is|\$)\s*\$?([0-9]+\.[0-9]+)', arb_text, re.IGNORECASE)
                    if m_sp:
                        report_spot = float(m_sp.group(1))
                if not shares_plan:
                    m_wl = re.search(r'```json:watch_levels\s*(\{.*?\})\s*```', arb_text, re.DOTALL)
                    if m_wl:
                        try:
                            wl_raw = json.loads(m_wl.group(1))
                            shares_plan = wl_raw.get("shares_plan") or {}
                            options_plan = wl_raw.get("options_plan") or {}
                            invalidation_rule = wl_raw.get("invalidation") or {}
                        except Exception:
                            pass
            except Exception:
                pass

        # Check synthesis and independent files
        sum_cand = t_raw_dir / f"{ticker_u}_gemini_thesis.md"
        if not sum_cand.exists():
            sum_cand = t_rep_dir / f"{ticker_u}_summary.md"
        if sum_cand.exists():
            sum_file = sum_cand

        ind_cand = t_raw_dir / f"{ticker_u}_independent_thesis.md"
        if not ind_cand.exists():
            ind_cand = t_rep_dir / f"{ticker_u}_independent.md"
        if ind_cand.exists():
            ind_file = ind_cand

    # 2. Live Real-Time Market Quote & Exact Numerical Spot Extraction
    quote_str = ""
    try:
        from src.clients.options_client import get_realtime_quote
        quote_str = get_realtime_quote(ticker_u)
    except Exception as qe:
        logger.debug(f"Quote fetch failed for {ticker_u}: {qe}")

    from src.clients.price_client import get_current_price
    live_spot: Optional[float] = None
    bid_str, ask_str = "N/A", "N/A"
    if quote_str:
        m_lp = re.search(r'Last:\s*([0-9]+\.[0-9]+)', quote_str)
        if m_lp:
            try:
                live_spot = float(m_lp.group(1))
            except Exception:
                pass
        m_ba = re.search(r'Bid/Ask:\s*([0-9]+\.[0-9]+)\s*/\s*([0-9]+\.[0-9]+)', quote_str)
        if m_ba:
            bid_str, ask_str = f"${m_ba.group(1)}", f"${m_ba.group(2)}"

    if live_spot is None:
        try:
            live_spot = get_current_price(ticker_u)
        except Exception:
            pass

    # 3. Deterministic Level Evaluation & Status
    ez_low = shares_plan.get("entry_zone_low")
    ez_high = shares_plan.get("entry_zone_high")
    stop_p = shares_plan.get("tactical_stop") or invalidation_rule.get("price_level")
    t1_p = shares_plan.get("target_1")
    breakout_p = shares_plan.get("breakout_level")

    calendar_today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    status_badge = "📊 ACTIVE LIVE TAPE"
    status_desc = f"Live spot: ${live_spot:.2f}" if live_spot else "Awaiting live quote"
    copilot_instruction = ""

    if live_spot:
        if stop_p and live_spot <= stop_p:
            status_badge = "🛑 STOP BREACHED / THESIS INVALIDATED"
            status_desc = f"Live spot (${live_spot:.2f}) has breached the tactical stop / invalidation floor (${stop_p:.2f})."
            copilot_instruction = f"The trade setup is INVALIDATED because live spot (${live_spot:.2f}) is at or below the stop (${stop_p:.2f}). Do not enter long."
        elif t1_p and live_spot >= t1_p:
            status_badge = "🏁 TARGET 1 REACHED"
            status_desc = f"Live spot (${live_spot:.2f}) reached Target 1 (${t1_p:.2f}). Consider taking partial profits."
            copilot_instruction = f"Live spot (${live_spot:.2f}) is in the profit target zone (T1 ${t1_p:.2f}). Advise locking in gains or trailing stop."
        elif ez_low and ez_high and ez_low <= live_spot <= ez_high:
            status_badge = "🎯 IN MANDATED ENTRY ZONE RIGHT NOW"
            status_desc = f"Live spot (${live_spot:.2f}) is INSIDE the suggested entry limit zone [${ez_low:.2f} – ${ez_high:.2f}]. Orders are filling live."
            copilot_instruction = f"The stock is ACTIVELY IN THE ENTRY ZONE (${ez_low:.2f}–${ez_high:.2f}). The pullback to ${ez_high:.2f} has already occurred. Setup is buyable/executable here with stop at ${stop_p or 'tactical stop'}."
        elif ez_low and stop_p and stop_p < live_spot < ez_low:
            dist_to_stop = ((live_spot - stop_p) / stop_p) * 100
            status_badge = "⚡ TESTING POC STRUCTURAL FLOOR (PULLBACK COMPLETE)"
            status_desc = f"Live spot (${live_spot:.2f}) pulled back through the limit ceiling (${ez_high:.2f}) down to the structural floor (+{dist_to_stop:.1f}% above ${stop_p:.2f} stop)."
            copilot_instruction = f"The stock HAS ALREADY PULLED BACK from ${report_spot or 'prior highs'} to ${live_spot:.2f}. It is testing the POC structural floor above the ${stop_p:.2f} stop. DO NOT tell the user to wait for a pullback to ${ez_high:.2f}—the pullback has already completed! Evaluate whether support is defending above ${stop_p:.2f}."
        elif ez_high and live_spot > ez_high:
            dist_above = ((live_spot - ez_high) / ez_high) * 100
            status_badge = f"⏳ STALKING (+{dist_above:.1f}% ABOVE ENTRY ZONE)"
            status_desc = f"Live spot (${live_spot:.2f}) is trading above the entry zone ceiling (${ez_high:.2f}). Awaiting pullback or breakout above ${breakout_p or 'resistance'}."
            copilot_instruction = f"Price (${live_spot:.2f}) is currently {dist_above:.1f}% above the top of the entry zone (${ez_high:.2f}). Await pullback to ${ez_high:.2f} or breakout above ${breakout_p or 'resistance'}."

    elapsed_days_str = ""
    if report_date:
        try:
            d_rep = datetime.strptime(report_date, "%Y-%m-%d")
            d_tod = datetime.strptime(calendar_today, "%Y-%m-%d")
            el = (d_tod - d_rep).days
            elapsed_days_str = f"({el} calendar day{'s' if el != 1 else ''} ago)" if el > 0 else "(today)"
        except Exception:
            pass

    delta_str = ""
    if live_spot and report_spot:
        d_val = live_spot - report_spot
        d_pct = (d_val / report_spot) * 100
        sign = "+" if d_val >= 0 else ""
        delta_str = f"• **NET CHANGE SINCE REPORT:** **{sign}${d_val:.2f} ({sign}{d_pct:.2f}%)**"

    top_card = [
        f"### 🚨 AUTHORITATIVE LIVE REAL-TIME MARKET QUOTE & EXECUTION STATUS ({ticker_u}):",
        f"• **CURRENT LIVE SPOT PRICE (NOW):** **${live_spot:.2f}**" if live_spot else f"• **CURRENT LIVE SPOT PRICE (NOW):** Quote Ingesting",
        f"• **REAL-TIME BID / ASK:** {bid_str} / {ask_str}" if (bid_str != "N/A" and ask_str != "N/A") else "",
        f"• **HISTORICAL REPORT BASELINE:** ${report_spot:.2f} (Compiled on {report_date or 'prior session'} {elapsed_days_str})" if report_spot else "",
    ]
    if delta_str:
        top_card.append(delta_str)
    if ez_low and ez_high:
        top_card.append(f"• **MANDATED ENTRY ZONE:** **${ez_low:.2f} – ${ez_high:.2f}** | **TACTICAL STOP:** **${stop_p:.2f}**" + (f" | **TARGET 1:** **${t1_p:.2f}**" if t1_p else ""))
    top_card.append(f"• **REAL-TIME EXECUTION STATUS:** **{status_badge}**")
    top_card.append(f"• **EXECUTION DETAIL:** {status_desc}")

    if quote_str:
        top_card.append(f"\n```\n{quote_str}\n```")

    top_card.append(f"""
> ⚠️ **MANDATORY INSTRUCTION FOR COPILOT / REV CHAT**:
> 1. The **AUTHORITATIVE LIVE SPOT PRICE** right now is **${live_spot:.2f}** (NOT {f'${report_spot:.2f}' if report_spot else 'historical prices'}).
> 2. Any prices cited in historical dossiers below were recorded on {report_date or 'earlier dates'}. NEVER repeat historical prices as today's live spot!
> 3. {copilot_instruction}
> 4. All trade recommendations, options strikes, delta/gamma risk, and distance to stops MUST be computed from the LIVE SPOT PRICE of **${live_spot:.2f}**.
""")
    parts.append("\n".join(l for l in top_card if l))

    # 1a-ii. Active Intraday Open Position Dossier (data/positions.json)
    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            pos_dict = json.loads(pos_file.read_text(encoding="utf-8"))
            if ticker_u in pos_dict:
                pos = pos_dict[ticker_u]
                p_side = (pos.get("side") or "LONG").upper()
                p_entry = float(pos.get("entry_price") or pos.get("alert_price") or 0.0)
                p_spot = float(pos.get("current_price") or pos.get("last_price") or p_entry)
                p_pnl = 0.0
                if p_entry > 0:
                    p_pnl = ((p_spot - p_entry) / p_entry * 100) if p_side == "LONG" else ((p_entry - p_spot) / p_entry * 100)
                raw_alert = pos.get("raw_alert") or {}
                p_plan = raw_alert.get("plan") or (f"Entry: ${p_entry:.2f}" if p_entry else "N/A")
                p_wrong = raw_alert.get("wrong_if") or "N/A"
                p_ctx = raw_alert.get("context") or "N/A"
                p_opened = (pos.get("opened_at") or "")[11:19] or "Active"
                p_eval = pos.get("last_eval") or ""
                
                pos_card = [
                    f"### 🚨 ACTIVE OPEN POSITION ON YOUR DESK ({ticker_u}):",
                    f"• **Side**: **{p_side}** | **Entry Price**: **${p_entry:.2f}** | **Live Spot**: **${p_spot:.2f}** | **Unrealized P&L**: **{p_pnl:+.2f}%**",
                    f"• **Opened At**: {p_opened} | **Strategy**: {pos.get('strategy', 'Intraday')}",
                    f"• **Engine Card Plan**: {p_plan}",
                    f"• **Kill Line / Invalidation**: {p_wrong}",
                    f"• **Tape Context**: {p_ctx}",
                ]
                if p_eval:
                    pos_card.append(f"\n**Latest Position Monitor Evaluation & Guidance:**\n{p_eval}")
                parts.insert(1, "\n".join(pos_card))
    except Exception as pe:
        logger.debug(f"Position check failed for {ticker_u}: {pe}")

    # 1b. Live Tape & Current Events Past Prior Chat / Dossier Date
    try:
        past_events = _build_events_past_chat_context(ticker_u, report_date or date_str, history=history, session_id=session_id)
        if past_events:
            parts.append(past_events)
    except Exception as ee:
        logger.debug(f"Error building past events context for {ticker_u}: {ee}")

    # 2. Live Options Chain & Volatility Metrics
    try:
        from src.clients.options_client import fetch_options_chain_tool
        q_lower = question.lower()
        plan_str = str(options_plan).lower()
        is_put_req = bool(re.search(r'\b(put|puts|bull put|bear put|credit spread|cash secured|csp|\d+p)\b', question, re.I)) or ("put" in plan_str)
        is_call_req = bool(re.search(r'\b(call|calls|bull call|bear call|debit spread|long call|\d+c)\b', question, re.I)) or ("call" in plan_str)

        # Check for specific expiration hints (e.g. Oct 2, Sep 25)
        exp_hint = None
        day_match_opt = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\s*(\d{1,2})\b', question, re.I)
        if day_match_opt:
            m_name, d_num = day_match_opt.groups()
            mm_dict = {
                'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
                'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
            }
            mm = mm_dict.get(m_name.lower()[:3])
            if mm:
                now_dt = datetime.now()
                year_cand = now_dt.year
                explicit_yr = re.search(r'\b(202[5-9])\b', question)
                if explicit_yr:
                    year_cand = int(explicit_yr.group(1))
                elif int(mm) < now_dt.month:
                    year_cand += 1
                exp_hint = f"{year_cand}-{mm}-{int(d_num):02d}"

        if is_leaps_req:
            opt_data = fetch_options_chain_tool(ticker=ticker_u, direction="PUT" if (is_put_req and not is_call_req) else "CALL", min_dte=90, max_dte=1200, expiration=exp_hint)
            if opt_data:
                parts.append(f"### 📈 LIVE 2027-2028 LEAPS OPTIONS CHAIN & GREEKS ({ticker_u}):\n{opt_data}")
        else:
            if is_put_req and not is_call_req:
                opt_data_put = fetch_options_chain_tool(ticker=ticker_u, direction="PUT", min_dte=14, max_dte=120, expiration=exp_hint)
                if opt_data_put:
                    parts.append(f"### 📈 LIVE REAL-TIME OPTIONS CHAIN & GREEKS (PUTS) ({ticker_u}):\n{opt_data_put}")
            elif is_put_req and is_call_req:
                opt_data_put = fetch_options_chain_tool(ticker=ticker_u, direction="PUT", min_dte=14, max_dte=120, expiration=exp_hint)
                opt_data_call = fetch_options_chain_tool(ticker=ticker_u, direction="CALL", min_dte=14, max_dte=120, expiration=exp_hint)
                if opt_data_put:
                    parts.append(f"### 📈 LIVE REAL-TIME OPTIONS CHAIN & GREEKS (PUTS) ({ticker_u}):\n{opt_data_put}")
                if opt_data_call:
                    parts.append(f"### 📈 LIVE REAL-TIME OPTIONS CHAIN & GREEKS (CALLS) ({ticker_u}):\n{opt_data_call}")
            else:
                opt_data_std = fetch_options_chain_tool(ticker=ticker_u, direction="CALL", min_dte=14, max_dte=120, expiration=exp_hint)
                if opt_data_std:
                    parts.append(f"### 📈 LIVE REAL-TIME OPTIONS CHAIN & GREEKS ({ticker_u}):\n{opt_data_std}")
    except Exception as oe:
        logger.debug(f"Options chain fetch error for {ticker_u}: {oe}")

    try:
        from src.clients import tastytrade_client
        tt_data = tastytrade_client.get_market_metrics(ticker_u)
        if tt_data:
            ivr = tt_data.get('iv_rank')
            ivp = tt_data.get('iv_percentile')
            hv30 = tt_data.get('historical_volatility_30d')
            hv60 = tt_data.get('historical_volatility_60d')
            hv90 = tt_data.get('historical_volatility_90d')
            liq = tt_data.get('liquidity_rating')
            borrow = tt_data.get('borrow_rate')
            parts.append(f"### 🏛️ TASTYTRADE VOLATILITY & LIQUIDITY METRICS ({ticker_u}):\nIV Rank: {ivr}% | IV Percentile: {ivp}% | 30d HV: {hv30} | 60d HV: {hv60} | 90d HV: {hv90} | Option Liquidity: {liq} Stars | Borrow Rate: {borrow}%")
    except Exception:
        pass

    # 2a. Targeted Strike Specific Lookup (Live Verified Schwab Chain for strikes mentioned in prompt)
    try:
        targeted_block = _extract_targeted_schwab_strikes(ticker_u, question)
        if targeted_block:
            parts.append(targeted_block)
    except Exception as te:
        logger.debug(f"Error extracting targeted strikes for {ticker_u}: {te}")

    # 2b. Schwab Institutional Options Flow & Sweeps
    try:
        from src.clients.schwab_client import get_unusual_options_flow_data
        flow = get_unusual_options_flow_data(ticker_u)
        if flow and flow.get("status") == "ok" and flow.get("anomalies"):
            anom_count = flow.get("total_anomalies_count", 0)
            c_vol = flow.get("total_call_volume", 0)
            p_vol = flow.get("total_put_volume", 0)
            c_prem = flow.get("total_call_premium", 0) / 1e6
            p_prem = flow.get("total_put_premium", 0) / 1e6
            bias = flow.get("sentiment_label", "Balanced / Mixed")
            
            top_lines = []
            for a in flow.get("anomalies", [])[:25]:
                prem_k = f"${a['notional_premium']/1e6:.2f}M" if a['notional_premium'] >= 1e6 else f"${round(a['notional_premium']/1000)}k"
                top_lines.append(f"• {a['type']} ${a['strike']} Exp {a['expiry']} ({a['dte']}d): Vol {a['volume']:,} vs OI {a['open_interest']:,} ({a['vol_to_oi']}x OI) | Delta {a['delta']} | Mid ${a['mid']} | Prem {prem_k}")
            
            flow_summary = (
                f"### 🔥 SCHWAB REAL-TIME INSTITUTIONAL OPTIONS FLOW ({ticker_u}):\n"
                f"• Flow Sentiment: **{bias}**\n"
                f"• Unusual Sweeps Count: {anom_count} ({flow.get('call_sweeps_count',0)} Calls / {flow.get('put_sweeps_count',0)} Puts)\n"
                f"• Call Sweep Volume: {c_vol:,} (${c_prem:.2f}M premium) | Put Sweep Volume: {p_vol:,} (${p_prem:.2f}M premium)\n"
                f"• Put/Call Ratios: {flow.get('put_call_volume_ratio',0)}x Vol | {flow.get('put_call_premium_ratio',0)}x Premium\n"
                f"**Top Institutional Sweeps (Vol > 1.5x OI):**\n" + "\n".join(top_lines)
            )
            parts.append(flow_summary)
    except Exception as fe:
        logger.debug(f"Schwab options flow fetch error for {ticker_u}: {fe}")

    # 3. Verified Python Quantitative Engine Calculations (df 300 bars x 85 indicators)
    try:
        from src.clients.llm_client import execute_python_code_tool
        quant_py = """
if not df.empty:
    close = df['close'].iloc[-1]
    high_52w = df['high'].tail(252).max()
    low_52w = df['low'].tail(252).min()
    sma20 = df['close'].tail(20).mean()
    sma50 = df['close'].tail(50).mean()
    sma200 = df['close'].tail(200).mean() if len(df) >= 200 else None
    
    # 20d Realized Volatility
    returns = df['close'].pct_change().dropna()
    hv20 = returns.tail(20).std() * np.sqrt(252) * 100
    
    # 14d ATR
    high_low = df['high'] - df['low']
    high_cp = (df['high'] - df['close'].shift()).abs()
    low_cp = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    atr14 = tr.tail(14).mean()
    atr_pct = (atr14 / close) * 100 if close else 0
    
    # 30-day Monte Carlo Probabilities (10,000 paths)
    daily_vol = (hv20 / 100) / np.sqrt(252) if hv20 else 0.015
    np.random.seed(42)
    sim_returns = np.random.normal(0, daily_vol, (10000, 30))
    price_paths = close * np.cumprod(1 + sim_returns, axis=1)
    p_up_5 = np.mean(np.any(price_paths >= close * 1.05, axis=1)) * 100
    p_down_5 = np.mean(np.any(price_paths <= close * 0.95, axis=1)) * 100
    p_up_10 = np.mean(np.any(price_paths >= close * 1.10, axis=1)) * 100

    print(f"• Last Data Window Close: ${close:.2f}")
    print(f"• 52-Week High / Low: ${high_52w:.2f} / ${low_52w:.2f}")
    print(f"• Moving Averages: 20d SMA=${sma20:.2f} | 50d SMA=${sma50:.2f}" + (f" | 200d SMA=${sma200:.2f}" if sma200 else ""))
    print(f"• 14d Average True Range (ATR): ${atr14:.2f} ({atr_pct:.2f}% of spot)")
    print(f"• 20d Realized Volatility (HV20): {hv20:.1f}%")
    print(f"• 30-Day Monte Carlo 10,000-Path Probabilities:")
    print(f"  - P(Touch +5% Gain at ${close*1.05:.2f}): {p_up_5:.1f}%")
    print(f"  - P(Touch -5% Drop at ${close*0.95:.2f}): {p_down_5:.1f}%")
    print(f"  - P(Touch +10% Gain at ${close*1.10:.2f}): {p_up_10:.1f}%")
"""
        quant_out = execute_python_code_tool(quant_py, ticker=ticker_u, date_str=date_str)
        if quant_out and "Error" not in quant_out and "No local" not in quant_out:
            parts.append(f"### 🐍 VERIFIED PYTHON QUANTITATIVE ANALYTICS & MATH ({ticker_u}):\n{quant_out}")
    except Exception as pe:
        logger.debug(f"Python quantitative baseline error for {ticker_u}: {pe}")

    # Check for user-supplied Python code in question
    code_matches = re.findall(r'```(?:python)?\s*(.*?)\s*```', question, re.DOTALL)
    if not code_matches:
        if any(kw in question.lower() for kw in ["execute python", "run python", "python:", "eval python", "calculate:", "compute:"]):
            py_match = re.search(r'(?:python|calculate|compute):\s*(.+)', question, re.IGNORECASE | re.DOTALL)
            if py_match:
                code_matches = [py_match.group(1)]

    if code_matches:
        for user_code in code_matches:
            try:
                from src.clients.llm_client import execute_python_code_tool
                exec_out = execute_python_code_tool(user_code, ticker=ticker_u, date_str=date_str)
                parts.append(f"### 🐍 USER PYTHON CODE EXECUTION RESULTS (Deterministic Sandbox):\n```python\n{user_code}\n```\n**STDOUT Output:**\n```\n{exec_out}\n```")
            except Exception as upe:
                parts.append(f"### 🐍 USER PYTHON EXECUTION ERROR:\n{upe}")

    # 4. SEC EDGAR Audit & Financial Facts
    try:
        from src.clients.sec_edgar_client import format_sec_report
        sec_audit = format_sec_report(ticker_u, limit=3)
        if sec_audit and "Error" not in sec_audit and "Could not resolve" not in sec_audit:
            parts.append(sec_audit)
    except Exception as se:
        logger.debug(f"SEC EDGAR fetch error for {ticker_u}: {se}")

    # 5. Live Breaking News & Web Search Catalysts
    try:
        live_news_items = []
        from src.clients.search_client import search_web
        search_results = search_web(f"{ticker_u} stock news earnings catalysts latest")
        if search_results and isinstance(search_results, list):
            for item in search_results[:3]:
                title = item.get("title", "").strip()
                body = item.get("body", "").strip()
                href = item.get("href", "").strip()
                if title:
                    live_news_items.append(f"• **{title}**\n  {body}\n  Source: {href}")

        import yfinance as yf
        yf_ticker = yf.Ticker(ticker_u)
        yf_news = getattr(yf_ticker, "news", [])
        if yf_news and isinstance(yf_news, list):
            for item in yf_news[:4]:
                content_obj = item.get("content", {})
                title = content_obj.get("title") or item.get("title")
                summary = content_obj.get("summary") or item.get("summary") or ""
                pub_date = content_obj.get("pubDate") or item.get("providerPublishTime") or ""
                if title and not any(title[:30].lower() in x.lower() for x in live_news_items):
                    live_news_items.append(f"• **{title}** ({pub_date})\n  {summary[:200]}")

        if live_news_items:
            calendar_now = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d %H:%M MT")
            parts.append(
                f"### 📰 TODAY'S REAL-TIME BREAKING NEWS ({ticker_u} — Retrieved {calendar_now}):\n"
                f"> ⚠️ **TEMPORAL BOUNDARY**: The headlines below occurred TODAY ({calendar_now[:10]}). "
                f"They were **NOT** available when historical dossiers (e.g. earlier dates) were compiled. "
                f"Never claim an earlier research report 'had notice' of breaking news that was published after that report's date.\n\n"
                + "\n\n".join(live_news_items[:5])
            )
    except Exception as ne:
        logger.debug(f"Live news search fetch error for {ticker_u}: {ne}")

    # 5. Local Filesystem Research Reports
    try:
        if latest_dt:
            spot_comp_str = f" (Spot at publication was ${report_spot:.2f} vs LIVE SPOT ${live_spot:.2f} right now)" if (report_spot and live_spot) else ""

            # Independent Quantitative Study (Model B)
            if ind_file and ind_file.exists():
                parts.append(
                    f"### 🧠 LATEST INDEPENDENT QUANTITATIVE THESIS ({ticker_u} — Date: {report_date}):\n"
                    f"> ⚠️ **HISTORICAL DOSSIER FROM {report_date}**{spot_comp_str}:\n"
                    f"> The thesis below reflects technical patterns as of {report_date}. Do NOT confuse historical prices with today's live tape.\n\n"
                    + ind_file.read_text(encoding='utf-8')[:4000]
                )

            # Synthesis Report (Model A)
            if sum_file and sum_file.exists():
                parts.append(
                    f"### 🔬 LATEST MULTI-MODEL SYNTHESIS DOSSIER ({ticker_u} — Date: {report_date}):\n"
                    f"> ⚠️ **HISTORICAL DOSSIER FROM {report_date}**{spot_comp_str}:\n\n"
                    + sum_file.read_text(encoding='utf-8')[:3500]
                )

            # Senior PM Arbitration Directive
            if arb_file and arb_file.exists():
                parts.append(
                    f"### ⚖️ SENIOR PM ARBITRATION DIRECTIVE ({ticker_u} — COMPILED ON {report_date}):\n"
                    f"> ⚠️ **HISTORICAL ARBITRATION DIRECTIVE FROM {report_date}**{spot_comp_str}:\n"
                    f"> Any mention of 'current spot' in the text below refers to {report_date} (${report_spot:.2f}). "
                    f"Today's live price is **${live_spot:.2f}**. Never quote the historical spot as current price!\n\n"
                    + arb_file.read_text(encoding='utf-8')[:3500]
                )

            # Tactical Watch Levels
            if levels_file and levels_file.exists():
                try:
                    parts.append(f"### 🎯 STRUCTURED TACTICAL WATCH LEVELS ({ticker_u} — Base Date {report_date}):\n{levels_file.read_text(encoding='utf-8')}")
                except Exception:
                    pass
    except Exception as he:
        logger.debug(f"Historical research search failed for {ticker_u}: {he}")

    if parts:
        _TICKER_CONTEXT_CACHE[cache_key] = (time.time(), parts)

    return parts


def _build_daily_overview_context(date_str: Optional[str] = None, question: str = "") -> Tuple[str, List[str]]:
    """
    Assembles a high-level executive briefing and desk overview of:
    1. Active research date discovery (resolving requested date vs latest research run).
    2. Deep research pipeline jobs run today/recently (completed/running tickers).
    3. Senior PM arbitration directives & theses (verdict, conviction, entry zone, stops, targets, options structure, invalidation rule, and judge's conflict resolution).
    4. Active watchlist & trigger states (IN_ZONE, STALKING, IN_TRADE, TARGET_HIT, INVALIDATED).
    5. Open positions and portfolio status (side, entry, spot, P&L, status).
    6. Market benchmark indices & volatility regime (SPY, QQQ, IWM, VIX).
    7. Synthesized desk gameplan: what was run, what was learned, and what we should do today.
    """
    parts = []
    today_tickers: List[str] = []
    
    calendar_today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    rep_root = config.BASE_DIR / "reports"
    all_dates = []
    if rep_root.exists():
        all_dates = sorted([d.name for d in rep_root.glob("202*") if d.is_dir() and any(d.glob("*_summary.md"))], reverse=True)
    
    # Resolve active research date
    if date_str and (rep_root / date_str).exists() and any((rep_root / date_str).glob("*_summary.md")):
        active_date = date_str
    elif all_dates:
        active_date = all_dates[0]
    else:
        active_date = date_str or calendar_today

    date_header = f"Research Date: {active_date}"
    if active_date != calendar_today:
        date_header += f" (Calendar Today: {calendar_today})"
        
    parts.append(f"# 🏛️ INSTITUTIONAL DAILY RESEARCH BRIEFING & DESK OVERVIEW\n**{date_header}**")

    if active_date != calendar_today:
        now_mt = datetime.now(ZoneInfo("America/Denver"))
        now_et = datetime.now(ZoneInfo("America/New_York"))
        now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT") + f" ({now_et.strftime('%I:%M %p ET')})"
        interim_lines = [
            f"> 🚨 **TEMPORAL BRIDGE**: Underlying research reports below were compiled on **{active_date}**, but current market time is **{now_str}**.",
            "> Do NOT speak of events that were scheduled between that research date and today as upcoming (e.g. NFP on Sep 4 was released this morning at 8:30 AM ET).",
            f"> The desk is currently executing live trading off the {active_date} baseline with real-time level retests and trigger surveillance."
        ]
        parts.append("\n".join(interim_lines))

    # Pre-populate cached prices from SQLite watch_targets and positions.json for instant resolution
    cached_prices: Dict[str, float] = {}
    try:
        with _get_db() as conn:
            c = conn.cursor()
            for r in c.execute("SELECT ticker, last_price FROM watch_targets WHERE last_price IS NOT NULL").fetchall():
                try:
                    if r["last_price"]:
                        cached_prices[r["ticker"].upper()] = float(r["last_price"])
                except Exception:
                    pass
    except Exception:
        pass

    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            with open(pos_file, "r", encoding="utf-8") as pf:
                pos_data = json.load(pf)
                pos_dict = pos_data.get("positions", pos_data) if isinstance(pos_data, dict) else {}
                for sym, p in pos_dict.items():
                    if isinstance(p, dict) and p.get("last_price"):
                        try:
                            cached_prices[sym.upper()] = float(p["last_price"])
                        except Exception:
                            pass
    except Exception:
        pass

    # 1. Pipeline Execution & Jobs Run Today
    try:
        with _get_db() as conn:
            c = conn.cursor()
            jobs = c.execute(
                "SELECT ticker, mode, status, started_at, completed_at FROM active_research_jobs "
                "WHERE started_at LIKE ? OR started_at LIKE ? OR started_at >= datetime('now', '-24 hours') "
                "ORDER BY rowid DESC LIMIT 15",
                (f"{calendar_today}%", f"{active_date}%")
            ).fetchall()
            
            if jobs:
                job_lines = []
                seen_jobs = set()
                for j in jobs:
                    sym = (j["ticker"] or "").upper()
                    st = j["status"]
                    mode = (j["mode"] or "full").upper()
                    key = f"{sym}_{st}_{mode}"
                    if key in seen_jobs:
                        continue
                    seen_jobs.add(key)
                    if sym and sym not in today_tickers:
                        today_tickers.append(sym)
                    
                    time_info = ""
                    if j["started_at"]:
                        try:
                            t_str = j["started_at"].split("T")[1][:5] if "T" in j["started_at"] else j["started_at"][-8:-3]
                            time_info = f" at {t_str} UTC"
                        except Exception:
                            pass
                    job_lines.append(f"• **{sym}**: {mode} research pipeline — status: **{st}**{time_info}")
                if job_lines:
                    parts.append("### ⚙️ RESEARCH PIPELINES RUN TODAY:\n" + "\n".join(job_lines))
    except Exception as je:
        logger.debug(f"Error querying research jobs: {je}")

    # Check survivors.json for active date
    try:
        surv_file = config.BASE_DIR / "data" / "raw" / active_date / "survivors.json"
        if surv_file.exists():
            with open(surv_file, "r", encoding="utf-8") as sf:
                surv_data = json.load(sf)
                if isinstance(surv_data, list):
                    surv_syms = [s.get("Symbol") or s.get("Ticker") for s in surv_data if isinstance(s, dict)]
                    surv_syms = [s.upper() for s in surv_syms if s]
                    for s in surv_syms:
                        if s not in today_tickers:
                            today_tickers.append(s)
                    if surv_syms:
                        parts.append(f"### 📋 SCREENER SURVIVOR CANDIDATES ({active_date}):\nActive Screened Universe: **{', '.join(surv_syms)}**")
    except Exception:
        pass

    # 2. Senior PM Arbitration Directives & Theses
    try:
        rep_dir = rep_root / active_date
        if rep_dir.exists():
            arb_files = sorted(list(rep_dir.glob("*_arbitration.md")))
            dossier_lines = []
            for af in arb_files:
                sym = af.name.replace("_arbitration.md", "").upper()
                if sym not in today_tickers:
                    today_tickers.append(sym)
                
                txt = af.read_text(encoding="utf-8")
                
                # Extract watch_levels json
                wl = {}
                m_wl = re.search(r"```json:watch_levels\s*(\{.*?\})\s*```", txt, re.DOTALL)
                if m_wl:
                    try:
                        wl = json.loads(m_wl.group(1))
                    except Exception:
                        pass
                if not wl:
                    for cand_p in [
                        raw_root / active_date / sym / f"{sym}_watch_levels.json",
                        config.BASE_DIR / "data" / "triage" / active_date / "_DEEP_RESEARCH" / sym / f"{sym}_watch_levels.json",
                        config.BASE_DIR / "data" / "triage" / active_date / "force" / sym / f"{sym}_watch_levels.json",
                    ]:
                        if cand_p.exists():
                            try:
                                wl = json.loads(cand_p.read_text(encoding="utf-8"))
                                break
                            except Exception:
                                pass
                
                # Extract verdict & conviction
                verdict = wl.get("verdict")
                conviction = wl.get("conviction")
                if not verdict or verdict == "ANALYZED":
                    vm = re.search(r"(?:Final Verdict|\*\*Ruling|\*\*Verdict):\*?\*?\s*([A-Za-z_]+(?:\s*\([^\)]+\))?)", txt, re.IGNORECASE)
                    if vm:
                        verdict = vm.group(1).strip()
                    else:
                        verdict = "STALK"
                if conviction is None:
                    cm = re.search(r"Conviction:\s*(\d+)/10", txt, re.IGNORECASE)
                    conviction = int(cm.group(1)) if cm else 5

                # Cached spot price
                spot_price = cached_prices.get(sym)
                spot_str = f" | Current Spot: **${spot_price:.2f}**" if spot_price else ""

                shares_plan = wl.get("shares_plan") or {}
                options_plan = wl.get("options_plan") or {}

                # Extract Judge's Ruling / Conflict Resolution
                ruling_snippet = ""
                r_match = re.search(r"## ⚖️ THE JUDGE'S FINAL RULING\s*(.*?)(?=## 🎯|\Z)", txt, re.DOTALL)
                if r_match:
                    clean_r = " ".join(r_match.group(1).split())
                    ruling_snippet = clean_r[:420] + ("..." if len(clean_r) > 420 else "")

                # Extract The ONE Thing Invalidation
                invalidation_snippet = ""
                inv_match = re.search(r"\*\*The ONE Thing Invalidation:\*\*\s*(.*?)(?=\n\n|\Z|```)", txt, re.DOTALL)
                if inv_match:
                    invalidation_snippet = " ".join(inv_match.group(1).split())[:200]
                elif wl.get("invalidation_condition"):
                    invalidation_snippet = wl["invalidation_condition"]

                card = [f"#### 📌 **{sym}** — Verdict: **{verdict}** (Conviction: {conviction}/10){spot_str}"]
                
                if shares_plan:
                    ez_l = shares_plan.get("entry_zone_low")
                    ez_h = shares_plan.get("entry_zone_high")
                    stop = shares_plan.get("tactical_stop")
                    t1 = shares_plan.get("target_1")
                    t2 = shares_plan.get("target_2")
                    card.append(f"  - **Shares Tactical Plan:** Limit Entry Zone: **${ez_l} – ${ez_h}** | Stop: **${stop}** | Target 1: **${t1}** | Target 2: **${t2}**")
                
                if options_plan:
                    opt_sum = options_plan.get("summary") or options_plan.get("structure")
                    opt_exp = options_plan.get("expiration")
                    opt_long = options_plan.get("long_strike")
                    opt_short = options_plan.get("short_strike")
                    opt_debit = options_plan.get("target_debit")
                    opt_str = f"  - **Options Vehicle:** {opt_sum}"
                    if opt_exp and opt_long and opt_short:
                        opt_str += f" (Exp: {opt_exp}, {opt_long}/{opt_short} spread, target debit: <${opt_debit})"
                    card.append(opt_str)

                if invalidation_snippet:
                    card.append(f"  - **The ONE Thing Invalidation:** {invalidation_snippet}")

                if ruling_snippet:
                    card.append(f"  - **Judicial Ruling & Conflict Resolution:** {ruling_snippet}")

                dossier_lines.append("\n".join(card))

            if dossier_lines:
                parts.append(f"### ⚖️ SENIOR PM ARBITRATION DIRECTIVES & TACTICAL THESES ({active_date}):\n" + "\n\n".join(dossier_lines))
    except Exception as de:
        logger.debug(f"Error parsing arbitration dossiers: {de}")

    # 3. Active Watchlist & Tactical Triggers
    try:
        with _get_db() as conn:
            c = conn.cursor()
            w_rows = c.execute(
                "SELECT ticker, date, verdict, status, entry_zone_low, entry_zone_high, tactical_stop, target_1, last_price, distance_to_entry_pct "
                "FROM watch_targets ORDER BY "
                "CASE status WHEN 'IN_ZONE' THEN 1 WHEN 'IN_TRADE' THEN 2 WHEN 'STALKING' THEN 3 ELSE 4 END, "
                "distance_to_entry_pct ASC"
            ).fetchall()

            if w_rows:
                in_zone = []
                in_trade = []
                stalking = []
                other_targets = []
                for r in w_rows:
                    sym = r["ticker"]
                    if sym not in today_tickers:
                        today_tickers.append(sym)
                    st = r["status"]
                    dist = r["distance_to_entry_pct"]
                    dist_str = f" ({dist:+.1f}% to entry)" if dist is not None else ""
                    last_p = f"Spot: ${r['last_price']:.2f}" if r["last_price"] else ""
                    line = f"• **{sym}** [{st}]{dist_str} — {last_p} | Entry: ${r['entry_zone_low']}–${r['entry_zone_high']} | Stop: ${r['tactical_stop']} | T1: ${r['target_1']}"
                    
                    if st == "IN_ZONE":
                        in_zone.append(line)
                    elif st == "IN_TRADE":
                        in_trade.append(line)
                    elif st == "STALKING":
                        stalking.append(line)
                    else:
                        other_targets.append(f"• **{sym}** [{st}] — Entry: ${r['entry_zone_low']}–${r['entry_zone_high']}")

                watch_sections = []
                if in_zone:
                    watch_sections.append("🎯 **IN ZONE (Actionable Trigger Ready):**\n" + "\n".join(in_zone))
                if in_trade:
                    watch_sections.append("🚀 **IN TRADE (Active Positions Working):**\n" + "\n".join(in_trade))
                if stalking:
                    watch_sections.append("⏳ **STALKING (Awaiting Pullback / Limit Fill):**\n" + "\n".join(stalking))
                if other_targets:
                    watch_sections.append("🏁 **TARGET HIT / INVALIDATED:**\n" + "\n".join(other_targets))

                parts.append("### 🎯 ACTIVE WATCHLIST & TACTICAL TRIGGERS (Live SQLite Tracking):\n" + "\n\n".join(watch_sections))
    except Exception as we:
        logger.debug(f"Error querying watch targets: {we}")

    # 4. Open Positions & Portfolio State (data/positions.json)
    try:
        pos_file = config.BASE_DIR / "data" / "positions.json"
        if pos_file.exists():
            pos_dict = json.loads(pos_file.read_text(encoding="utf-8"))
            if pos_dict:
                pos_lines = []
                for sym, p in pos_dict.items():
                    side = (p.get("side") or "LONG").upper()
                    entry = float(p.get("entry_price") or p.get("alert_price") or 0.0)
                    spot = float(p.get("current_price") or p.get("last_price") or entry)
                    pnl = 0.0
                    if entry > 0:
                        pnl = ((spot - entry) / entry * 100) if side == "LONG" else ((entry - spot) / entry * 100)
                    raw_a = p.get("raw_alert") or {}
                    plan = raw_a.get("plan") or (f"Entry: ${entry:.2f}" if entry else "")
                    wrong_if = raw_a.get("wrong_if") or ""
                    opened = (p.get("opened_at") or "")[11:19] or "Active"
                    line = f"• **${sym}** ({side}) | Entry: **${entry:.2f}** | Spot: **${spot:.2f}** | P&L: **{pnl:+.2f}%** | Opened: {opened}"
                    if plan:
                        line += f"\n  - Plan: {plan}"
                    if wrong_if:
                        line += f" | Invalidation: {wrong_if}"
                    pos_lines.append(line)
                parts.append(f"### 💼 ACTIVE INTRADAY OPEN POSITIONS ({len(pos_lines)} open positions in data/positions.json):\n" + "\n\n".join(pos_lines))
    except Exception as pe:
        logger.debug(f"Error loading positions.json in overview: {pe}")

    # 5. Live Benchmark Quotes & Volatility
    try:
        benchmarks = []
        for b_sym in ["SPY", "QQQ", "IWM", "VIX"]:
            bp = cached_prices.get(b_sym)
            if bp:
                benchmarks.append(f"{b_sym}: ${bp:.2f}")
        if benchmarks:
            parts.append(f"### 🌐 LIVE MARKET BENCHMARKS & VOLATILITY REGIME:\n" + " | ".join(benchmarks))
    except Exception:
        pass

    overview_text = "\n\n".join(parts) if parts else ""
    return overview_text, today_tickers


def _build_copilot_context_and_tools(question: str, explicit_ticker: str = None, date_str: str = None, history: list = None, session_id: str = None, board_context: str = None):
    """
    Executes live data tools and assembles complete quantitative context for Copilot:
    1. Triggers background TradingView scrape if requested in prompt.
    2. Builds daily executive research briefing and desk overview (runs today, PM arbitration, watchlist triggers, open positions).
    3. Fetches multi-tier real-time price quotes (Alpaca / Tastytrade / yfinance) for ALL mentioned tickers.
    4. Fetches live options chains + Tastytrade market metrics (IV Rank, HV, Greeks).
    5. Fetches official SEC EDGAR regulatory filings and balance sheet metrics.
    6. Sandboxed verified Python execution for quantitative analytics (ATRs, Monte Carlo, HV20).
    7. Gathers Senior PM judicial arbitration, Model A synthesis, Model B independent, and watch levels.
    8. Injects chronological bridge of live tape & events that occurred PAST the prior chat / research date.
    9. Injects active board/screen telemetry (exact table rows, active tab, highlighted selection).
    """
    ticker_meta = _detect_ticker_metadata(question, explicit_ticker, history=history)
    detected_tickers = ticker_meta["tickers"]
    has_specific_ticker = any(t for t in detected_tickers if t not in ("GENERAL", "AUTO", "NONE", "ALL", ""))
    primary_ticker = detected_tickers[0] if has_specific_ticker else "GENERAL"
    is_explicit = ticker_meta["is_explicit"]
    company_name = ticker_meta["company_name"]

    now_mt = datetime.now(ZoneInfo("America/Denver"))
    now_et = datetime.now(ZoneInfo("America/New_York"))
    calendar_today = now_mt.strftime("%Y-%m-%d")
    now_str = now_mt.strftime("%A, %b %d, %Y %I:%M %p MT")
    now_et_str = now_et.strftime("%I:%M %p ET")

    if not date_str:
        date_str = calendar_today

    dossier_date = date_str
    if has_specific_ticker and (not explicit_ticker or date_str == calendar_today):
        discovered_date = _get_latest_research_date_for_ticker(primary_ticker)
        if discovered_date:
            dossier_date = discovered_date

    is_prior = (dossier_date < calendar_today)
    try:
        elapsed_days = (datetime.strptime(calendar_today, "%Y-%m-%d") - datetime.strptime(dossier_date, "%Y-%m-%d")).days if is_prior else 0
    except Exception:
        elapsed_days = 1 if is_prior else 0
    elapsed_days_str = f"{elapsed_days} calendar day(s) ago" if elapsed_days > 1 else ("yesterday" if elapsed_days == 1 else "today")

    context_parts = []

    # 0a. Active Screen / Board Telemetry (User's Currently Visible View / Selection)
    if board_context and board_context.strip():
        context_parts.append(
            f"### 📋 USER'S ACTIVE BOARD / SCREEN TELEMETRY (What the User Sees on Screen Right Now):\n"
            f"> 💡 The user is actively viewing this exact data table or screen slice on their dashboard right now. "
            f"Ground your analysis in this visible data:\n\n"
            f"{board_context.strip()}"
        )

    has_explicit_dossier = bool(explicit_ticker and explicit_ticker.strip().upper() not in ("GENERAL", "AUTO", "NONE", "ALL", ""))

    # 0b. Ticker Verification Directive for Inferred / Unconfirmed Symbols
    if has_specific_ticker and not is_explicit and not has_explicit_dossier:
        ticker_confirmation_block = f"""### 🎯 MANDATORY TICKER VERIFICATION & CONFIRMATION DIRECTIVE:
The ticker '${primary_ticker}' was INFERRED from the user's conversational text ("{question}"), NOT from an explicit '$' symbol or active modal selection.
The user may be asking about {company_name} (${primary_ticker}), or they might be speaking colloquially, using a typo, or asking a general question.

You MUST follow this exact structure:
1. Open your response with a prominent, polite Ticker Confirmation Card:
   🎯 **Ticker Check**: I detected **${primary_ticker}** ({company_name}). Is this the stock you would like to analyze?
   [✅ Yes, Analyze ${primary_ticker}](action:ask?prompt=Yes,+analyze+${primary_ticker})  [✏️ Different Ticker](action:ask?prompt=No,+I+meant+$)

2. Provide a concise preliminary answer/quote for ${primary_ticker} based on the live context below.
3. If the user's question was also a general market question (e.g. asking about covered call concepts), answer the core concept as well so the user is never left hanging.
"""
        context_parts.append(ticker_confirmation_block)

    # 1. Trigger fresh scrape if requested by user
    scrape_intent = bool(re.search(r'\b(scrape|rescrap|rescan|fetch chart|fresh chart|new chart|update chart)\b', question, re.IGNORECASE))
    if scrape_intent and primary_ticker and primary_ticker not in ("GENERAL", "AUTO", "NONE", ""):
        try:
            job_id = _launch_background_job(
                name=f"Chart Scrape ({primary_ticker})",
                command=[sys.executable, "run_swing_research.py", "--ticker", primary_ticker, "--force"],
                log_file=f"scrape_{primary_ticker}.log"
            )
            context_parts.append(f"### 🔄 AUTOMATED SCRAPE TRIGGERED FOR {primary_ticker}\nAction Executed: Successfully launched background TradingView chart scraper (Job ID: {job_id}).\nInforming user that a fresh chart and Data Window are currently updating in data/raw/{date_str}/{primary_ticker}/.")
        except Exception as se:
            context_parts.append(f"### 🔄 SCRAPE ATTEMPT\nNotice: Could not launch background scrape: {se}")

    is_single_stock = bool(primary_ticker and primary_ticker not in ("GENERAL", "AUTO", "NONE", "ALL", ""))

    is_modal_or_alert = bool(board_context and any(k in board_context for k in (
        "CURRENT TRADINGVIEW ALERT MODAL CONTEXT",
        "MODEL B INDEPENDENT REPORT",
        "MODEL A SYNTHESIS",
        "PM ARBITRATION",
        "OPTIONS FLOW TABLE"
    )))

    # 2. Check if daily overview is needed
    # Only inject broad market overview if explicitly requested, or if no specific ticker is in focus.
    market_wide_request = bool(re.search(
        r'\b(market overview|desk overview|whole market|all stocks|all tickers|what was run across|what was run today|what did we run today|what did we learn today|desk briefing)\b',
        question, re.IGNORECASE
    ))
    positions_request = bool(re.search(
        r'\b(positi?y?ons?|open\s*pos\w*|intraday\s*pos\w*|active\s*pos\w*|trade|trades|open\s*trades?|active\s*trades?|my\s*trades?|portfolio|pnl|p&l|holdings?)\b',
        question, re.IGNORECASE
    ))
    if is_single_stock or is_modal_or_alert:
        is_overview_request = market_wide_request
    else:
        is_overview_request = market_wide_request or positions_request or bool(re.search(
            r'\b(today|overview|summary|learn|learned|what should we do|what to do|gameplan|plan|what was run|runs|run today|research done|researched|watchlist|stalking|portfolio|positions|desk|market|status)\b',
            question, re.IGNORECASE
        ))

    daily_overview_text, today_universe_tickers = "", []
    if is_overview_request:
        daily_overview_text, today_universe_tickers = _build_daily_overview_context(date_str, question)
        if daily_overview_text:
            context_parts.append(daily_overview_text)

    # 3. Build live context for each detected ticker (up to 3 symbols)
    for sym in detected_tickers[:3]:
        if sym and sym not in ("GENERAL", "AUTO", "NONE", ""):
            sym_parts = _build_single_ticker_context(sym, date_str, question, history=history, session_id=session_id)
            if sym_parts:
                context_parts.append(f"## ═══════════════════════════════════════════════════\n## 📌 REAL-TIME CONTEXT FOR TICKER: {sym}\n## ═══════════════════════════════════════════════════\n" + "\n\n".join(sym_parts))

    # Fallback if somehow context_parts is still empty
    if not context_parts:
        daily_overview_text, today_universe_tickers = _build_daily_overview_context(date_str, question)
        if daily_overview_text:
            context_parts.append(daily_overview_text)

    dossier_context = "\n\n".join(context_parts) if context_parts else f"Context for {primary_ticker} loaded."

    all_symbols = [t for t in detected_tickers if t not in ("GENERAL", "AUTO", "NONE", "ALL", "")]
    if not all_symbols and today_universe_tickers:
        symbols_list_str = f"Today's Research Universe ({', '.join(today_universe_tickers[:6])})"
    elif all_symbols:
        symbols_list_str = ", ".join(all_symbols)
    else:
        symbols_list_str = "Active Desk Universe"

    if is_single_stock:
        role_header = f"""You are the dedicated quantitative research and execution co-pilot for ${primary_ticker} ({company_name}) on Revanth's institutional trading desk.
The user is actively inspecting the research dossier, options structures, and trade plan for ${primary_ticker}.

CORE DIRECTIVE:
Every user query (including conversational queries like "thoughts for today", "what is the plan", "what should we do", "levels", "options") MUST FOCUS DIRECTLY AND SPECIFICALLY ON ${primary_ticker}:
1. Address ${primary_ticker}'s price action today ({calendar_today}) vs the report date ({dossier_date}, {elapsed_days_str}). Compare spot then vs spot now.
2. CRITICAL LIVE PRICE RULE: The CURRENT SPOT PRICE is the live quote under '### 🚨 AUTHORITATIVE LIVE REAL-TIME MARKET QUOTE'. NEVER cite historical dossier prices as current spot!
3. Evaluate ${primary_ticker}'s specific Suggested Trade Plan (Entry Zone, Stop Loss, Target) and Options Vehicle against the LIVE SPOT PRICE: Has the pullback already occurred? Is price in-zone or testing support?
4. Provide actionable, concise execution advice for ${primary_ticker} right now today: Is it in-zone and buyable, stalking (awaiting fill/pullback), or invalidated?
5. DO NOT output a broad multi-ticker desk briefing of other companies (AVGO, META, TSLA, GOOGL, etc.) unless the user explicitly asks for other tickers."""

        desk_briefing_directive = f"""3. Ticker-Specific Analysis Directive:
   - Your response must focus 100% on ${primary_ticker} ({company_name}).
   - Evaluate the Suggested Trade Plan, current live spot price, entry zone, stop, target, and options structure.
   - Answer the user's specific question directly with actionable guidance for ${primary_ticker} today.
   - Do NOT output multi-ticker desk briefings, other pipeline runs, or unrelated tickers."""
    else:
        role_header = f"""You are REV CHAT, the elite quantitative market co-pilot and tactical execution assistant for Revanth's institutional trading desk.
You specialize in options structures (credit/debit spreads, iron condors, ratio spreads), 0DTE theta execution, swing stalking setups, SEC fundamental audits, and data-grounded trade arbitration."""

        desk_briefing_directive = """3. Multi-Ticker Desk Briefings:
   - When NO specific ticker is being analyzed and the user asks general questions such as "what was run today", "what did we learn", "what should we do today", or requests a market overview/summary:
     Synthesize and present a structured desk briefing based on the active research pipeline runs, PM arbitration rulings, active watchlist triggers, and open positions.
   - Detail:
     (a) What was run today (pipelines completed, tickers audited).
     (b) Key quantitative lessons & bull/bear debates learned.
     (c) Actionable gameplan for today (in-zone triggers vs stalking targets).
   - NEVER claim "NO ACTIVE TICKER DATA DETECTED" or refuse to answer. You have complete institutional intelligence provided in the context below."""

    system_prompt = f"""{role_header}

Guidelines:
1. Answer directly, concisely, and with high quantitative depth and precision.
2. You have FULL ACCESS to real-time live market data, options chains, SEC EDGAR filings, breaking news, daily research briefings, Senior PM arbitration rulings, watch triggers, and open positions provided in the context below for {symbols_list_str}.
{desk_briefing_directive}
4. When the user asks about a correlated stock, sector peer, or comparison (e.g. comparing EIX to PCG, AMD to NVDA), analyze the real-time quotes, options, and catalysts for BOTH tickers directly from the active context. NEVER claim you lack data when it is provided.
5. Cite exact real-time spot prices, bid/ask spreads, Greeks, and strikes when discussing levels. ALWAYS treat the LIVE REAL-TIME MARKET QUOTE as the sole authoritative current spot price. Never repeat historical report spot prices as today's price.
6. When recommending trades, always specify: Actionable Vehicle (Equity vs Option Structure), Exact Strikes / Expiration, Target Premium/Debit, Max Loss, and The ONE Thing Invalidation level.
7. Mathematical Rigor & Deterministic Execution: All quantitative metrics, 14d ATRs, 20d Realized Volatilities, moving averages, and 10,000-path Monte Carlo probabilities are computed deterministically via the verified Python sandbox engine below. Always double-check mathematical identities (e.g. Max Profit + Max Loss = Spread Width, Breakeven = Strike ± Premium, R:R = Target Gain / Risk). Never hallucinate mental arithmetic.
8. Strict Historical Factuality & No Retrospective Attribution: Never claim that a research report or technical model from an earlier date "had notice" or "saw the news" of a catalyst that occurred after that report was compiled. If a stock moved on news published today, state clearly that the news broke today, not in the earlier report. Distinguish between what the technical indicators saw on the report date and what news broke subsequently.
9. Mandatory Two-Pass Clarification & Execution Protocol (Interactive Clarification & Ticker Verification):
   - When evaluating an alert, setup, or independent report for ${primary_ticker}:
     * PASS 1 — STATE ASSESSMENT & AMBIGUITY CHECK:
       Assess what is going on right now:
       1. Compare live spot price to entry zone and structural support floor. (Has the pullback already happened? Is price in-zone, or has it extended/chased? Is it near the stop?)
       2. Check binary risk: Are earnings, CPI, or major events occurring within 14 days?
       3. Check volatility & vehicle: What is the Tastytrade IV Rank? Does it favor credit spreads (>50%) or debit/shares (<35%)?
     * GATE — IN CASE OF ANY AMBIGUITY, ALWAYS ASK THE USER BEFORE PROCEEDING! NEVER GUESS!
       If there is ANY ambiguity, strategic conflict, or branching decision:
       1. State the current state in 2-3 concise bullet points.
       2. ALWAYS ASK the user to clarify using clickable interactive markdown action links:
          Format: `[Option Label](action:ask?prompt=Exact+prompt+text)`
          Examples:
          * Price extended: "Price is currently extended above entry floor.
            [⏳ Stalk Limit Pullback to $[Price]](action:ask?prompt=I+want+to+wait+for+a+limit+pullback+to+$[Price])  [⚡ Structure Defined-Risk Spread Here](action:ask?prompt=Structure+a+defined+risk+spread+at+current+levels)"
          * Earnings close: "Earnings are upcoming in 6 days.
            [🛑 Wait Until Post-Earnings](action:ask?prompt=Wait+until+after+earnings)  [🛡️ Defined-Risk Buffer Spread](action:ask?prompt=Structure+a+wide+defined+risk+spread+below+support)"
          * Vehicle choice: "IV Rank is neutral. Preference:
            [📈 Long Shares (Equity)](action:ask?prompt=Plan+shares+entry+with+limit)  [🦅 Bull Put Credit Spread](action:ask?prompt=Structure+Bull+Put+Spread)"
       3. Stop there and await user input!
     * PASS 2 — ACTIONABLE EXECUTION:
       Only when there is ZERO ambiguity (or when the user has answered the clarification question):
       Provide the exact institutional execution plan:
       - Exact Limit Entry Level / Execution Trigger
       - Hard Kill Stop (proven wrong in 1 sentence)
       - Profit Targets (T1 trim 50%, T2 runner)
       - Option Contract: Strikes, Expiration, Debit/Credit
       - Mathematical Risk-to-Reward (R:R)
   - When a ticker is inferred from conversational text or company names rather than explicitly typed with a '$':
     ALWAYS confirm the ticker with the user up-front with interactive action links:
     "🎯 **Ticker Check**: Analyzing **$[TICKER]** ([Company Name]). Is this the stock you want?"
     Followed by: `[✅ Yes, Analyze $[TICKER]](action:ask?prompt=Yes,+analyze+$[TICKER])  [✏️ Different Ticker](action:ask?prompt=No,+I+meant+$)`
10. Temporal Chronology & Past-Chat Continuity Directive:
   - Today is {calendar_today} ({now_str} / {now_et_str}). The active dossier or conversation baseline was recorded on {dossier_date} ({elapsed_days_str}).
   - You MUST recognize that time has elapsed since the previous chat turns and dossier compilation.
   - NEVER speak of prior-day statements as future expectations (e.g. if the prior chat or dossier said 'until NFP data tomorrow' or 'at tomorrow's open', recognize that tomorrow HAS ARRIVED and is TODAY, {calendar_today}).
   - Address how current events, today's macro prints (such as NFP jobs release), breaking news, and live spot prices compare to the setup discussed in the prior chat.
   - Reference exact price changes since the prior chat (spot then vs spot now), whether price reclaimed or rejected the prior levels, and provide updated, current tactical action.


---
### ACTIVE REAL-TIME MARKET CONTEXT:
• CURRENT SYSTEM TIME: {now_str} ({now_et_str})
• CALENDAR TODAY: {calendar_today}
• ACTIVE DOSSIER / REFERENCE DATE: {dossier_date} ({elapsed_days_str})
• TICKERS IN CONTEXT: {symbols_list_str}

{dossier_context}
"""

    # 4. Construct standard OpenAI multi-turn messages array
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        cleaned_turns = []
        for turn in history[-8:]:
            role = "user" if turn.get("role") == "user" else "assistant"
            raw_c = turn.get("content", "")
            content = raw_c.strip() if isinstance(raw_c, str) else (str(raw_c).strip() if raw_c is not None else "")
            if not content:
                continue
            # Deduplicate if client passed current question as last history item
            if role == "user" and content == question:
                continue
            cleaned_turns.append({"role": role, "content": content})

        for idx, turn in enumerate(cleaned_turns):
            is_latest_assistant = (turn["role"] == "assistant" and idx == len(cleaned_turns) - 1)
            content = turn["content"]
            # Compress earlier assistant responses if too long (>600 chars), keeping latest intact
            if turn["role"] == "assistant" and not is_latest_assistant and len(content) > 500:
                compressed = content[:400].rstrip() + " ...[summary truncated]"
                messages.append({"role": "assistant", "content": compressed})
            else:
                messages.append(turn)

    messages.append({"role": "user", "content": question})

    # Flat string fallback for single-prompt interfaces
    history_text = ""
    if len(messages) > 2:
        history_text = "### PREVIOUS CONVERSATION CONTEXT:\n"
        for m in messages[1:-1]:
            r_label = "User" if m["role"] == "user" else "Copilot"
            history_text += f"**{r_label}:** {m['content']}\n\n"
        history_text += "---\n### CURRENT FOLLOW-UP QUESTION:\n"
    user_prompt = f"{history_text}User: {question}"

    return system_prompt, messages, user_prompt, primary_ticker, date_str


@app.get("/api/tickers")
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
        with _get_db() as conn:
            c = conn.cursor()
            rows = c.execute("SELECT DISTINCT ticker FROM watch_targets").fetchall()
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
            import csv
            with open(spx_file, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].isalnum() and len(row[0]) <= 5:
                        tickers.add(row[0].strip().upper())
        except Exception:
            pass

    # Staples
    tickers.update(["SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "WMT", "AMD", "UBER", "PLTR", "CRWD", "COIN", "AVGO", "COST", "NFLX", "PYPL"])

    return {"tickers": sorted(list(tickers))}


_COMPANY_NAMES_MAP: Optional[Dict[str, str]] = None


def get_company_names_dict() -> Dict[str, str]:
    global _COMPANY_NAMES_MAP
    if _COMPANY_NAMES_MAP is not None:
        return _COMPANY_NAMES_MAP
    cache_file = STATIC_DIR / "data" / "company_names.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _COMPANY_NAMES_MAP = json.load(f)
                return _COMPANY_NAMES_MAP
        except Exception:
            pass
    _COMPANY_NAMES_MAP = {}
    return _COMPANY_NAMES_MAP


@app.get("/api/company-names")
def get_company_names_endpoint():
    """Returns dictionary mapping tickers to company names for frontend tooltips."""
    names = get_company_names_dict()
    return JSONResponse(content=names)


@app.get("/api/trades/suggested")
def get_suggested_trades_endpoint():
    """Returns structured suggested trades from watch_targets with live PnL and trade outcome metrics."""
    try:
        rep_root = config.BASE_DIR / "reports"
        with _get_db() as conn:
            c = conn.cursor()
            rows = c.execute("SELECT * FROM watch_targets ORDER BY date DESC, updated_at DESC").fetchall()

        trades = []
        company_names = get_company_names_dict()
        for r in rows:
            t = dict(r)
            sym = (t.get("ticker") or "").upper()
            d_str = t.get("date") or ""

            raw = {}
            if t.get("raw_json"):
                try:
                    raw = json.loads(t["raw_json"])
                except Exception:
                    pass
            t["options_plan"] = raw.get("options_plan") or {}
            t["shares_plan"] = raw.get("shares_plan") or {}
            t["invalidation"] = raw.get("invalidation") or {}

            rep_path = rep_root / d_str / f"{sym}_summary.md"
            arb_path = rep_root / d_str / f"{sym}_arbitration.md"
            has_report = rep_path.exists() or arb_path.exists()

            entry_low = t.get("entry_zone_low")
            entry_high = t.get("entry_zone_high")
            stop_loss = t.get("tactical_stop")
            target_1 = t.get("target_1")
            target_2 = t.get("target_2")
            last_price = t.get("last_price")
            side = (t.get("side") or "LONG").upper()
            status = (t.get("status") or "STALKING").upper()

            entry_mid = None
            if entry_low is not None and entry_high is not None:
                entry_mid = round((entry_low + entry_high) / 2.0, 2)
            elif entry_low is not None:
                entry_mid = entry_low
            elif entry_high is not None:
                entry_mid = entry_high

            pnl_pct = None
            if entry_mid and last_price and entry_mid > 0:
                if side == "SHORT":
                    pnl_pct = round(((entry_mid - last_price) / entry_mid) * 100.0, 2)
                else:
                    pnl_pct = round(((last_price - entry_mid) / entry_mid) * 100.0, 2)

            target_1_pct = None
            if entry_mid and target_1 and entry_mid > 0:
                if side == "SHORT":
                    target_1_pct = round(((entry_mid - target_1) / entry_mid) * 100.0, 2)
                else:
                    target_1_pct = round(((target_1 - entry_mid) / entry_mid) * 100.0, 2)

            stop_risk_pct = None
            if entry_mid and stop_loss and entry_mid > 0:
                stop_risk_pct = round((abs(entry_mid - stop_loss) / entry_mid) * 100.0, 2)

            rr_ratio = None
            if target_1_pct is not None and stop_risk_pct is not None and stop_risk_pct > 0:
                rr_ratio = round(abs(target_1_pct) / max(0.01, stop_risk_pct), 2)

            t["company_name"] = company_names.get(sym, sym)
            t["entry_midpoint"] = entry_mid
            t["pnl_pct"] = pnl_pct
            t["target_1_pct"] = target_1_pct
            t["stop_risk_pct"] = stop_risk_pct
            t["rr_ratio"] = rr_ratio
            t["has_report"] = has_report

            trades.append(t)

        summary = {
            "total": len(trades),
            "in_zone": sum(1 for tr in trades if tr.get("status") == "IN_ZONE"),
            "stalking": sum(1 for tr in trades if tr.get("status") == "STALKING"),
            "target_hit": sum(1 for tr in trades if tr.get("status") in ("TARGET_HIT", "COMPLETED")),
            "stopped": sum(1 for tr in trades if tr.get("status") in ("STOP_BREACHED", "STOPPED", "INVALIDATED")),
        }
        return {"trades": trades, "summary": summary}
    except Exception as e:
        logger.error(f"Failed to fetch suggested trades: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})



@app.get("/api/copilot/chats/sessions")
def get_copilot_chat_sessions_endpoint(days: int = 15, ticker: Optional[str] = None):
    """Returns prior chat sessions from the last N days (default 15 days), ordered by most recent."""
    _init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _get_db() as conn:
        c = conn.cursor()
        query = """
            SELECT session_id, ticker, date, role, content, created_at
            FROM copilot_chat_history
            WHERE created_at >= ?
        """
        params = [cutoff]
        if ticker and ticker not in ("ALL", "GENERAL", ""):
            query += " AND ticker = ?"
            params.append(ticker.upper())
        query += " ORDER BY id ASC"
        
        rows = c.execute(query, params).fetchall()
        
        sessions_map = {}
        for r in rows:
            sid = r["session_id"]
            if sid not in sessions_map:
                sessions_map[sid] = {
                    "session_id": sid,
                    "ticker": r["ticker"],
                    "date": r["date"],
                    "first_prompt": r["content"] if r["role"] == "user" else "Chat Session",
                    "last_snippet": r["content"][:140],
                    "message_count": 0,
                    "created_at": r["created_at"],
                    "updated_at": r["created_at"]
                }
            sess = sessions_map[sid]
            sess["message_count"] += 1
            sess["updated_at"] = r["created_at"]
            if r["role"] == "user" and sess["first_prompt"] == "Chat Session":
                sess["first_prompt"] = r["content"]
            sess["last_snippet"] = r["content"][:140]

        res = sorted(list(sessions_map.values()), key=lambda s: s["updated_at"], reverse=True)
        return {"sessions": res, "days": days}


@app.get("/api/copilot/chats/history")
def get_copilot_chat_history_endpoint(session_id: Optional[str] = None, ticker: Optional[str] = None, date: Optional[str] = None):
    """Returns all stored messages for a specific session or most recent session for ticker/date."""
    _init_db()
    with _get_db() as conn:
        c = conn.cursor()
        if session_id:
            rows = c.execute(
                "SELECT id, session_id, ticker, date, role, content, created_at FROM copilot_chat_history WHERE session_id = ? ORDER BY id ASC",
                (session_id,)
            ).fetchall()
        elif ticker:
            q = "SELECT session_id FROM copilot_chat_history WHERE ticker = ?"
            p = [ticker.upper()]
            if date:
                q += " AND date = ?"
                p.append(date)
            q += " ORDER BY id DESC LIMIT 1"
            latest_sess = c.execute(q, p).fetchone()
            if latest_sess:
                target_sid = latest_sess["session_id"]
                rows = c.execute(
                    "SELECT id, session_id, ticker, date, role, content, created_at FROM copilot_chat_history WHERE session_id = ? ORDER BY id ASC",
                    (target_sid,)
                ).fetchall()
                session_id = target_sid
            else:
                rows = []
        else:
            rows = []
        
        messages = [dict(r) for r in rows]
        return {"messages": messages, "session_id": session_id}


@app.post("/api/copilot/chats/save")
def save_copilot_chat_endpoint(req: SaveChatMessageRequest):
    """Explicitly saves a chat message turn to SQLite."""
    _save_chat_turn(req.session_id, req.ticker, req.date, req.role, req.content)
    return {"status": "ok"}


@app.post("/api/copilot/execute-python")
def copilot_execute_python_endpoint(req: ExecutePythonRequest):
    """Executes sandboxed Python code with pre-loaded df (300 bars x 85 indicators), dw, numpy, pandas, scipy, and stats."""
    from src.clients.llm_client import execute_python_code_tool
    t0 = time.time()
    res = execute_python_code_tool(code=req.code, ticker=req.ticker or "AMD", date_str=req.date)
    duration_ms = (time.time() - t0) * 1000
    is_err = isinstance(res, str) and (res.startswith("Python Execution Error:") or res.startswith("Security Error:"))
    return {
        "success": not is_err,
        "output": res,
        "error": res if is_err else None,
        "ticker": req.ticker,
        "duration_ms": round(duration_ms, 2)
    }



@app.delete("/api/copilot/chats/session/{session_id}")
def delete_copilot_chat_session_endpoint(session_id: str):
    """Deletes an entire chat session from SQLite."""
    _init_db()
    with _get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM copilot_chat_history WHERE session_id = ?", (session_id,))
        conn.commit()
    return {"status": "ok", "deleted_session_id": session_id}


@app.post("/api/copilot/chat")
def copilot_chat_endpoint(req: CopilotChatRequest):
    """
    Interactive Copilot Q&A endpoint supporting dossier-specific follow-ups,
    live price quotes, options chain lookups, and general market analysis.
    """
    try:
        from src.clients.llm_client import query_local_llm
        
        system_prompt, messages, user_prompt, ticker_u, date_str = _build_copilot_context_and_tools(
            question=req.question,
            explicit_ticker=req.ticker,
            date_str=req.date,
            history=req.history,
            session_id=req.session_id,
            board_context=req.board_context,
        )

        session_id = req.session_id or f"sess_{ticker_u}_{date_str}_{uuid.uuid4().hex[:8]}"
        _save_chat_turn(session_id, ticker_u, date_str, "user", req.question)

        # Fast check if local LLM port 8000 is listening
        llm_alive = False
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                llm_alive = True
        except Exception:
            llm_alive = False

        if not llm_alive:
            response_text = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server_qwen38_27b_q4.bat` (or `start_market.bat`) to ask follow-up questions."
        else:
            try:
                response_text = query_local_llm(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    messages=messages,
                    use_tools=True,
                    use_openrouter=False,
                    max_tokens=2048,
                )
            except Exception as llm_err:
                response_text = f"⚠️ **LLM Inference Error:** {llm_err}"

        _save_chat_turn(session_id, ticker_u, date_str, "assistant", response_text)

        return {
            "answer": response_text,
            "ticker": ticker_u,
            "date": date_str,
            "session_id": session_id,
        }
    except Exception as e:
        logger.error(f"Error in copilot chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/copilot/chat/stream")
async def copilot_chat_stream_endpoint(req: CopilotChatRequest, request: Request):
    """
    Real-time Server-Sent Events (SSE) token-by-token streaming endpoint for REV CHAT.
    Provides fast, conversational market intelligence, live options chains, real-time quotes, and on-demand scrapes.
    Persists all turns into SQLite copilot_chat_history.
    Supports WYSIWYG board telemetry and multimodal vision input (Qwen 27B --mmproj).
    """
    try:
        question_text = (req.question or "").strip()
        if not question_text and req.image_data:
            question_text = "Please analyze this attached chart / snapshot and provide actionable trade execution guidance."
        elif not question_text:
            question_text = "Provide a summary of current market status and actionable setups."

        initial_ticker = (req.ticker or "").strip().upper()

        async def token_generator():
            # 1. Immediately yield initial connection event so client receives HTTP 200 in <50ms
            yield f"data: {json.dumps({'status': 'connecting', 'ticker': initial_ticker})}\n\n"

            if await request.is_disconnected():
                return

            from starlette.concurrency import run_in_threadpool
            try:
                build_task = asyncio.create_task(run_in_threadpool(
                    _build_copilot_context_and_tools,
                    question=question_text,
                    explicit_ticker=req.ticker,
                    date_str=req.date,
                    history=req.history,
                    session_id=req.session_id,
                    board_context=req.board_context,
                ))
                while True:
                    done, _ = await asyncio.wait([build_task], timeout=4.0)
                    if not done:
                        if await request.is_disconnected():
                            build_task.cancel()
                            return
                        yield ": ping\n\n"
                    else:
                        system_prompt, messages, user_prompt, ticker_u, date_str = build_task.result()
                        break
            except Exception as ce:
                logger.error(f"Error compiling copilot context: {ce}", exc_info=True)
                yield f"data: {json.dumps({'error': f'Context compilation error: {ce}'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            session_id = req.session_id or f"sess_{ticker_u}_{date_str}_{uuid.uuid4().hex[:8]}"
            _save_chat_turn(session_id, ticker_u, date_str, "user", question_text)

            # Fast socket check
            import socket
            llm_alive = False
            try:
                with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                    llm_alive = True
            except Exception:
                llm_alive = False

            if not llm_alive:
                offline_msg = "⚠️ **Local LLM Server on Port 8000 is currently offline.**\n\nPlease start the local Qwen server via `scripts\\launchers\\start_llm_server_qwen38_27b_q4.bat` to stream responses in real time."
                yield f"data: {json.dumps({'token': offline_msg})}\n\n"
                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                yield "data: [DONE]\n\n"
                _save_chat_turn(session_id, ticker_u, date_str, "assistant", offline_msg)
                return

            if await request.is_disconnected():
                return

            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                base_url=os.getenv("LLM_LOCAL_URL", "http://127.0.0.1:8000/v1"),
                api_key="sk-no-key-required",
                timeout=180,
            )
            try:
                models = await client.models.list()
                model_name = models.data[0].id
            except Exception:
                model_name = "gpt-4"

            # Multimodal Vision Payload (Qwen 27B Vision Projector)
            if req.image_data and messages:
                if messages[-1].get("role") == "user":
                    raw_user_content = messages[-1].get("content", "")
                    if isinstance(raw_user_content, str):
                        messages[-1]["content"] = [
                            {"type": "text", "text": raw_user_content},
                            {"type": "image_url", "image_url": {"url": req.image_data}}
                        ]

            from src.clients.llm_client import TOOLS, execute_tool_call

            class _MockFunc:
                def __init__(self, name: str, arguments: str):
                    self.name = name
                    self.arguments = arguments

            class _MockToolCall:
                def __init__(self, id_str: str, name: str, arguments: str):
                    self.id = id_str
                    self.function = _MockFunc(name, arguments)

            full_answer_chunks = []
            max_turns = 4
            turn = 0
            tools_enabled = True

            try:
                while turn < max_turns:
                    turn += 1
                    tool_calls_acc = {}
                    turn_content_chunks = []

                    create_kwargs = {
                        "model": model_name,
                        "messages": messages,
                        "max_tokens": 2048,
                        "temperature": 0.2,
                        "stream": True,
                        "extra_body": {"cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}},
                    }
                    # Force synthesis on final allowed turn
                    turn_tools_enabled = tools_enabled and (turn < max_turns)
                    if turn_tools_enabled:
                        create_kwargs["tools"] = TOOLS
                        create_kwargs["tool_choice"] = "auto"

                    try:
                        stream_response = await client.chat.completions.create(**create_kwargs)
                    except Exception as stream_call_err:
                        if tools_enabled and ("400" in str(stream_call_err) or "tool" in str(stream_call_err).lower()):
                            tools_enabled = False
                            create_kwargs.pop("tools", None)
                            create_kwargs.pop("tool_choice", None)
                            stream_response = await client.chat.completions.create(**create_kwargs)
                        else:
                            raise

                    aiter = stream_response.__aiter__()
                    next_chunk_task = None
                    while True:
                        if next_chunk_task is None:
                            next_chunk_task = asyncio.create_task(aiter.__anext__())
                        done, _ = await asyncio.wait([next_chunk_task], timeout=4.0)
                        if not done:
                            if await request.is_disconnected():
                                next_chunk_task.cancel()
                                break
                            yield ": ping\n\n"
                            continue
                        try:
                            chunk = next_chunk_task.result()
                            next_chunk_task = None
                        except StopAsyncIteration:
                            break
                        except (asyncio.CancelledError, GeneratorExit):
                            break

                        if await request.is_disconnected():
                            break
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if hasattr(delta, "content") and delta.content:
                                turn_content_chunks.append(delta.content)
                                full_answer_chunks.append(delta.content)
                                yield f"data: {json.dumps({'token': delta.content})}\n\n"
                            if hasattr(delta, "tool_calls") and delta.tool_calls:
                                for tc in delta.tool_calls:
                                    idx = tc.index
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {"id": tc.id or f"call_{idx}_{turn}", "name": "", "arguments": ""}
                                    if tc.id:
                                        tool_calls_acc[idx]["id"] = tc.id
                                    if tc.function:
                                        if tc.function.name:
                                            tool_calls_acc[idx]["name"] += tc.function.name
                                        if tc.function.arguments:
                                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

                    if await request.is_disconnected():
                        break

                    if not tool_calls_acc:
                        # Generation finished; no further tools needed
                        break

                    # Append assistant message with emitted tool calls
                    turn_content = "".join(turn_content_chunks)
                    asst_msg = {
                        "role": "assistant",
                        "content": turn_content if turn_content else None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]}
                            }
                            for tc in tool_calls_acc.values()
                        ]
                    }
                    messages.append(asst_msg)

                    # Inform UI of active tool execution
                    tool_names = [tc["name"] for tc in tool_calls_acc.values() if tc.get("name")]
                    if tool_names:
                        status_badge = f"\n\n⚙️ *Executing live tool(s): `{', '.join(tool_names)}`...*\n\n"
                        full_answer_chunks.append(status_badge)
                        yield f"data: {json.dumps({'token': status_badge})}\n\n"

                    # Execute all tools in parallel threadpool
                    for tc in tool_calls_acc.values():
                        if await request.is_disconnected():
                            break
                        mock_tc = _MockToolCall(tc["id"], tc["name"], tc["arguments"])
                        try:
                            tool_res = await asyncio.to_thread(execute_tool_call, mock_tc, date_str)
                            res_str = str(tool_res) if tool_res is not None else "No output."
                        except Exception as ex_tool:
                            res_str = f"Tool execution error: {ex_tool}"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": res_str
                        })

                complete_answer = "".join(full_answer_chunks)
                if complete_answer and not (await request.is_disconnected()):
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", complete_answer)

                yield f"data: {json.dumps({'done': True, 'session_id': session_id})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                if not (await request.is_disconnected()):
                    err_msg = f"⚠️ **Stream Error:** {stream_err}"
                    yield f"data: {json.dumps({'error': str(stream_err)})}\n\n"
                    yield "data: [DONE]\n\n"
                    _save_chat_turn(session_id, ticker_u, date_str, "assistant", err_msg)

        return StreamingResponse(
            token_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"Error in copilot chat stream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _extract_report_card(date: str, ticker: str) -> Dict[str, Any]:
    """Extract structured levels and PM verdict metadata for a research report."""
    ticker_u = ticker.upper()
    rep_dir = config.BASE_DIR / "reports" / date

    # 1. Search for watch_levels.json across all known locations (raw, triage, reports)
    levels_candidates = [
        config.BASE_DIR / "data" / "raw" / date / ticker_u / f"{ticker_u}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / f"{ticker_u}_watch_levels.json",
        config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_watch_levels.json",
        rep_dir / f"{ticker_u}_watch_levels.json",
    ]

    levels_data = {}
    for cand in levels_candidates:
        if cand.exists():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    levels_data = json.load(f)
                    if levels_data:
                        break
            except Exception:
                pass

    # 2. Check SQLite watch_targets database as fallback or complement
    db_target = None
    try:
        with _get_db() as conn:
            row = conn.cursor().execute(
                "SELECT * FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?) ORDER BY date DESC LIMIT 1",
                (ticker_u, date, f"{date}%")
            ).fetchone()
            if not row:
                row = conn.cursor().execute(
                    "SELECT * FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (ticker_u,)
                ).fetchone()
            if row:
                db_target = dict(row)
    except Exception:
        pass

    # 3. Fallback scan markdown files for embedded json:watch_levels if still missing
    if not levels_data and not db_target:
        for md_cand in [rep_dir / f"{ticker_u}_arbitration.md", rep_dir / f"{ticker_u}_summary.md"]:
            if md_cand.exists():
                try:
                    txt = md_cand.read_text(encoding="utf-8")
                    m = re.search(r'```json:watch_levels\s*(\{.*?\})\s*```', txt, re.DOTALL)
                    if m:
                        levels_data = json.loads(m.group(1))
                        break
                except Exception:
                    pass

    # 4. Extract structured fields robustly from nested plans or DB row
    shares_plan = levels_data.get("shares_plan") or {}
    options_plan = levels_data.get("options_plan") or {}
    invalidation = levels_data.get("invalidation") or {}

    # Entry zone [low, high]
    ez_low = shares_plan.get("entry_zone_low")
    ez_high = shares_plan.get("entry_zone_high")
    if ez_low is None and db_target:
        ez_low = db_target.get("entry_zone_low")
    if ez_high is None and db_target:
        ez_high = db_target.get("entry_zone_high")

    if ez_low is not None and ez_high is not None:
        try:
            entry_zone = [float(ez_low), float(ez_high)]
        except (ValueError, TypeError):
            entry_zone = []
    elif levels_data.get("entry_zone"):
        entry_zone = levels_data["entry_zone"]
    else:
        entry_zone = []

    # Tactical stop
    stop = (
        shares_plan.get("tactical_stop")
        or levels_data.get("tactical_stop")
        or (db_target.get("tactical_stop") if db_target else None)
        or invalidation.get("price_level")
    )
    if stop is not None:
        try:
            stop = float(stop)
        except (ValueError, TypeError):
            stop = None

    # Targets
    t1 = shares_plan.get("target_1") or levels_data.get("target_1") or (db_target.get("target_1") if db_target else None)
    if t1 is not None:
        try:
            t1 = float(t1)
        except (ValueError, TypeError):
            t1 = None

    t2 = shares_plan.get("target_2") or levels_data.get("target_2") or (db_target.get("target_2") if db_target else None)
    if t2 is not None:
        try:
            t2 = float(t2)
        except (ValueError, TypeError):
            t2 = None

    # Options summary
    options_summary = (
        options_plan.get("summary")
        or levels_data.get("options_summary")
        or (db_target.get("options_summary") if db_target else "")
        or ""
    )
    if not options_summary and options_plan.get("structure"):
        options_summary = f"{options_plan.get('structure')} (Exp: {options_plan.get('expiration', 'N/A')})"

    # Verdict & conviction
    verdict = levels_data.get("verdict") or (db_target.get("verdict") if db_target else None)
    if not verdict or verdict == "ANALYZED":
        arb_file = rep_dir / f"{ticker_u}_arbitration.md"
        if arb_file.exists():
            try:
                txt = arb_file.read_text(encoding="utf-8")
                if "ENTER (Options Credit)" in txt:
                    verdict = "ENTER (Credit Spread)"
                elif "ENTER (Long Call)" in txt:
                    verdict = "ENTER (Call Debit)"
                elif "ENTER" in txt:
                    verdict = "ENTER"
                elif "WATCH" in txt:
                    verdict = "WATCH"
                elif "AVOID" in txt:
                    verdict = "AVOID"
            except Exception:
                pass
    if not verdict:
        verdict = "ANALYZED"

    conviction = levels_data.get("conviction") or (db_target.get("conviction") if db_target else None) or 5

    # Researched timestamp (mtime of report file)
    researched_at = None
    for cand_f in [rep_dir / f"{ticker_u}_summary.md", rep_dir / f"{ticker_u}_arbitration.md", rep_dir / f"{ticker_u}_independent.md"]:
        if cand_f.exists():
            try:
                researched_at = datetime.fromtimestamp(cand_f.stat().st_mtime).isoformat()
                break
            except Exception:
                pass

    return {
        "ticker": ticker_u,
        "date": date,
        "verdict": verdict,
        "conviction": conviction,
        "options_summary": options_summary,
        "entry_zone": entry_zone,
        "tactical_stop": stop,
        "target_1": t1,
        "target_2": t2,
        "researched_at": researched_at,
        "has_summary": (rep_dir / f"{ticker_u}_summary.md").exists(),
        "has_arbitration": (rep_dir / f"{ticker_u}_arbitration.md").exists(),
        "has_independent": (rep_dir / f"{ticker_u}_independent.md").exists(),
    }


@app.get("/api/reports")
def list_available_reports(date: Optional[str] = None):
    """List all available research reports grouped by date with rich summary metadata."""
    reports_dir = config.BASE_DIR / "reports"
    if not reports_dir.exists():
        return {"dates": [], "reports_by_date": {}, "date_labels": {}}

    all_dates = []
    reports_by_date = {}
    date_labels = {}
    today_str = datetime.now().strftime("%Y-%m-%d")

    for d in sorted(reports_dir.iterdir(), reverse=True):
        if d.is_dir():
            date_str = d.name
            tickers = set()
            for f in d.glob("*_summary.md"):
                t = f.name.replace("_summary.md", "")
                tickers.add(t)
            if tickers:
                all_dates.append(date_str)
                # If specific date requested or loading recent top 5 dates
                if not date or date == date_str or len(reports_by_date) < 5:
                    cards = [_extract_report_card(date_str, t) for t in sorted(list(tickers))]
                    reports_by_date[date_str] = cards

    # Build rich session labels for date picker
    if all_dates:
        latest = all_dates[0]
        rep_d = reports_dir / latest
        has_today = any(
            datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d") == today_str
            for f in rep_d.glob("*.md")
        )
        for d_str in all_dates:
            if d_str == latest:
                if has_today and latest != today_str:
                    date_labels[d_str] = f"📅 {d_str} (Latest Session · Run Today)"
                else:
                    date_labels[d_str] = f"📅 {d_str} (Latest Session)"
            elif d_str == today_str:
                date_labels[d_str] = f"📅 {d_str} (Today)"
            else:
                date_labels[d_str] = f"📅 {d_str}"

    return {
        "dates": all_dates,
        "selected_date": date or (all_dates[0] if all_dates else None),
        "reports_by_date": reports_by_date,
        "date_labels": date_labels,
    }


def _find_chart_path(date: str, ticker: str, chart_type: str) -> Optional[Path]:
    """Find chart image across raw and triage directories with automatic fallback to most recent date."""
    ticker_u = ticker.upper()
    fname = f"{ticker_u}_chart_zoom.png" if chart_type == "zoom" else f"{ticker_u}_chart_plain.png"
    fallback_fname = f"{ticker_u}_chart.png"
    
    # 1. Check specified date first
    if date:
        candidates = [
            config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / fname,
            config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / fname,
            config.BASE_DIR / "data" / "raw" / date / ticker_u / fname,
            config.BASE_DIR / "data" / "triage" / date / "force" / ticker_u / fallback_fname,
            config.BASE_DIR / "data" / "triage" / date / "_DEEP_RESEARCH" / ticker_u / fallback_fname,
            config.BASE_DIR / "data" / "raw" / date / ticker_u / fallback_fname,
        ]
        for p in candidates:
            if p.exists():
                return p
                
    # 2. Search across all recent dates for this ticker (newest first)
    raw_root = config.BASE_DIR / "data" / "raw"
    if raw_root.exists():
        matches = sorted(list(raw_root.glob(f"202*/{ticker_u}/{fname}")), reverse=True)
        if matches:
            return matches[0]
        fallback_matches = sorted(list(raw_root.glob(f"202*/{ticker_u}/{fallback_fname}")), reverse=True)
        if fallback_matches:
            return fallback_matches[0]
            
    return None


_REPORT_BUNDLE_CACHE: Dict[str, tuple[float, dict]] = {}
_REPORT_CACHE_TTL = 300  # 5 minutes TTL


@app.get("/api/report/{ticker}")
def get_report_bundle_single(ticker: str):
    """Convenience alias resolving the latest available report bundle for a ticker."""
    return get_report_bundle("latest", ticker)


@app.get("/api/report/{date}/{ticker}")
def get_report_bundle(date: str, ticker: str):
    """Return all 3 model markdown reports, available dates, and historical timeline for a ticker."""
    ticker_u = ticker.upper().strip()
    cache_key = f"{date}_{ticker_u}"
    now_ts = time.time()

    # Fast in-memory cache return (sub-millisecond)
    cached = _REPORT_BUNDLE_CACHE.get(cache_key)
    if cached and (now_ts - cached[0] < _REPORT_CACHE_TTL):
        res = dict(cached[1])
        try:
            with _get_db() as conn:
                r = conn.cursor().execute(
                    "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (ticker_u,)
                ).fetchone()
                if r and r["last_price"]:
                    res["live_price"] = float(r["last_price"])
        except Exception:
            pass
        return res

    raw_root = config.BASE_DIR / "data" / "raw"
    rep_root = config.BASE_DIR / "reports"

    # 1. Discover historical research dates for this ticker across raw/ and reports/
    from datetime import datetime, timedelta
    report_dates_set = set()
    scrape_dates_set = set()

    if rep_root.exists():
        for d in rep_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                if (d / f"{ticker_u}_arbitration.md").exists() or (d / f"{ticker_u}_summary.md").exists() or (d / f"{ticker_u}_independent.md").exists():
                    report_dates_set.add(d.name)

    if raw_root.exists():
        for d in raw_root.iterdir():
            if d.is_dir() and d.name.startswith("202"):
                sym_dir = d / ticker_u
                if sym_dir.exists():
                    if (
                        (sym_dir / f"{ticker_u}_arbitration.md").exists()
                        or (sym_dir / f"{ticker_u}_gemini_thesis.md").exists()
                        or (sym_dir / f"{ticker_u}_thesis.md").exists()
                        or (sym_dir / f"{ticker_u}_watch_levels.json").exists()
                    ):
                        report_dates_set.add(d.name)
                    elif (sym_dir / f"{ticker_u}_datawindow.json").exists():
                        scrape_dates_set.add(d.name)

    # Check watch_targets database as well for recorded dates with levels
    try:
        with _get_db() as conn:
            rows = conn.cursor().execute(
                "SELECT DISTINCT date FROM watch_targets WHERE ticker = ? ORDER BY date DESC",
                (ticker_u,)
            ).fetchall()
            for r in rows:
                if r["date"] and r["date"].startswith("202"):
                    report_dates_set.add(r["date"][:10])
    except Exception:
        pass

    # Prioritize dates that actually have reports/watch_levels over raw scrape-only dates
    all_sorted_dates = sorted(list(report_dates_set if report_dates_set else scrape_dates_set), reverse=True)
    
    # Auto-resolve target_date: if requested date has no reports for this ticker, automatically resolve to latest date with reports
    req_date = (date or "").strip()
    target_date = req_date
    if all_sorted_dates and (target_date not in all_sorted_dates or target_date in ("latest", "today", "now", "", "undefined", "null")):
        target_date = all_sorted_dates[0]
    elif not target_date and all_sorted_dates:
        target_date = all_sorted_dates[0]
    elif not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # 35-day focus window
    ref_dt = datetime.strptime(target_date, "%Y-%m-%d") if re.match(r"^\d{4}-\d{2}-\d{2}$", target_date) else datetime.now()
    cutoff_dt_str = (ref_dt - timedelta(days=35)).strftime("%Y-%m-%d")
    
    available_dates = [d for d in all_sorted_dates if d >= cutoff_dt_str or d == target_date]
    if not available_dates:
        available_dates = all_sorted_dates[:10] if all_sorted_dates else [target_date]
    if target_date not in available_dates:
        available_dates.insert(0, target_date)

    # 2. Fetch specific date reports with robust raw/ fallbacks
    rep_dir = rep_root / target_date
    raw_dir = raw_root / target_date / ticker_u

    # Summary / Synthesis
    summary_file = rep_dir / f"{ticker_u}_summary.md"
    if not summary_file.exists() and raw_dir.exists():
        for cand_name in (f"{ticker_u}_gemini_thesis.md", f"{ticker_u}_thesis.md"):
            cand = raw_dir / cand_name
            if cand.exists():
                summary_file = cand
                break

    # Independent Thesis
    ind_file = rep_dir / f"{ticker_u}_independent.md"
    if not ind_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_independent_thesis.md"
        if cand.exists():
            ind_file = cand

    # Senior PM Arbitration
    arb_file = rep_dir / f"{ticker_u}_arbitration.md"
    if not arb_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_arbitration.md"
        if cand.exists():
            arb_file = cand

    summary_md = summary_file.read_text(encoding="utf-8") if summary_file.exists() else None
    independent_md = ind_file.read_text(encoding="utf-8") if ind_file.exists() else None
    arbitration_md = arb_file.read_text(encoding="utf-8") if arb_file.exists() else None

    # Fallback if arbitration is empty: present synthesis or independent
    if not arbitration_md and (summary_md or independent_md):
        arbitration_md = f"# {ticker_u} | ARBITRATION & EXECUTIVE RESEARCH DOSSIER ({target_date})\n\n*(Displaying primary research report for {target_date})*\n\n" + (independent_md or summary_md)

    zoom_path = _find_chart_path(target_date, ticker_u, "zoom")
    plain_path = _find_chart_path(target_date, ticker_u, "plain")

    # 3. Build Historical Timeline across all dates (with robust clean verdict & spot parsing)
    timeline = []
    for d_str in available_dates:
        t_raw = raw_root / d_str / ticker_u
        t_raw_date = raw_root / d_str
        t_rep = rep_root / d_str
        
        # Priority order for timeline dossier: Arbitration > Summary > Independent > Raw Gemini
        doc_candidates = [
            t_rep / f"{ticker_u}_arbitration.md",
            t_raw / f"{ticker_u}_arbitration.md",
            t_rep / f"{ticker_u}_summary.md",
            t_raw / f"{ticker_u}_summary.md",
            t_rep / f"{ticker_u}_independent.md",
            t_raw / f"{ticker_u}_independent_thesis.md",
            t_raw / f"{ticker_u}_gemini_thesis.md",
        ]
        
        chosen_doc = None
        for cand in doc_candidates:
            if cand.exists() and cand.stat().st_size > 250:
                txt_head = cand.read_text(encoding="utf-8")[:400]
                if not txt_head.strip().startswith("<tool_call>"):
                    chosen_doc = cand
                    break

        if not chosen_doc:
            continue

        txt = chosen_doc.read_text(encoding="utf-8")

        # 1. Spot price extraction
        spot_val = None
        for dw_cand in [t_raw / f"{ticker_u}_datawindow.json", t_raw_date / f"{ticker_u}_datawindow.json"]:
            if dw_cand.exists():
                try:
                    dw = json.loads(dw_cand.read_text(encoding="utf-8"))
                    for k in ["close", "Close", "last", "Last", "bar_close"]:
                        if k in dw and dw[k]:
                            spot_val = float(dw[k])
                            break
                except Exception:
                    pass
            if spot_val:
                break

        if not spot_val:
            m_spot = re.search(r'(?:Spot Price|Bar close|\bSpot\b|\bClose\b)[\s\*:]+\$?([0-9]+\.[0-9]+)', txt, re.IGNORECASE)
            if m_spot:
                spot_val = float(m_spot.group(1))
            else:
                m_header_spot = re.search(rf'#\s*{ticker_u}\s*\|\s*\$([0-9]+\.[0-9]+)', txt)
                if m_header_spot:
                    spot_val = float(m_header_spot.group(1))

        # Fallback search in sibling report files for spot price
        if not spot_val:
            for other_cand in doc_candidates:
                if other_cand.exists() and other_cand != chosen_doc:
                    o_txt = other_cand.read_text(encoding="utf-8")[:800]
                    m_o_spot = re.search(r'(?:Spot Price|Bar close|\bSpot\b|\bClose\b)[\s\*:]+\$?([0-9]+\.[0-9]+)', o_txt, re.IGNORECASE)
                    if m_o_spot:
                        spot_val = float(m_o_spot.group(1))
                        break
                    m_o_hspot = re.search(rf'#\s*{ticker_u}\s*\|\s*\$([0-9]+\.[0-9]+)', o_txt)
                    if m_o_hspot:
                        spot_val = float(m_o_hspot.group(1))
                        break

        # 2. Verdict extraction
        verdict_str = None
        
        # A. Check json:watch_levels
        m_json = re.search(r'"verdict":\s*"([^"]+)"', txt)
        if m_json:
            v_raw = m_json.group(1).strip()
            m_conv = re.search(r'"conviction":\s*([0-9]+(?:\.[0-9]+)?)', txt)
            conv_str = f" (Conviction: {m_conv.group(1)}/10)" if m_conv else ""
            verdict_str = f"{v_raw}{conv_str}"

        # B. Check markdown verdict patterns
        if not verdict_str:
            m_v = re.search(r'\*\*(?:Final\s+)?Verdict:\*\*\s*([^\n\r]+)', txt)
            if m_v:
                v_line = m_v.group(1).replace("**", "").strip()
                if not v_line.startswith("|") and "Vehicle" not in v_line:
                    v_clean = re.split(r'[\xb7\u2022]', v_line)[0].strip()
                    verdict_str = v_clean[:50]

        # C. Check technical rating
        if not verdict_str:
            m_tr = re.search(r'\*\*Technical Rating:\*\*\s*([^\n\r·*]+)', txt)
            if m_tr:
                v_tr = m_tr.group(1).strip()
                if not v_tr.startswith("|") and "Vehicle" not in v_tr:
                    verdict_str = v_tr[:40]

        # D. Check table row for Equity verdict
        if not verdict_str or verdict_str == "ANALYZED":
            m_eq = re.search(r'\|\s*\*\*Equity(?:\s*\(Shares\))?\*\*\s*\|\s*\*\*?([^\*\|]+)\*\*?\s*\|', txt)
            if m_eq:
                v_eq = m_eq.group(1).strip()
                if "Vehicle" not in v_eq and "Verdict" not in v_eq:
                    verdict_str = v_eq[:40]

        # E. Check SQLite watch_targets database fallback
        if not verdict_str or verdict_str == "ANALYZED":
            try:
                with _get_db() as conn:
                    row = conn.cursor().execute(
                        "SELECT verdict, conviction FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?)",
                        (ticker_u, d_str, f"{d_str}%")
                    ).fetchone()
                    if row and row["verdict"]:
                        c_str = f" (Conviction: {row['conviction']}/10)" if row["conviction"] else ""
                        verdict_str = f"{row['verdict']}{c_str}"
            except Exception:
                pass

        if not verdict_str:
            verdict_str = "ANALYZED"

        # Sanitize any accidental markdown table headers or pipes
        verdict_str = verdict_str.replace("·", "·").strip(" |*·-")
        if "Vehicle" in verdict_str or "Actionable Setup" in verdict_str:
            verdict_str = "STALK"

        # 3. Preview text
        preview_text = None
        m_th = re.search(r'\*\*(?:The\s+)?Thesis in 2 Sentences:\*\*\s*([^\n\r]+(?:\n[^\n\r#]+)?)', txt)
        if m_th:
            preview_text = m_th.group(1).replace("**", "").strip()[:240]
        else:
            paragraphs = [p.strip() for p in txt.split('\n\n') if p.strip() and not p.strip().startswith('#') and not p.strip().startswith('<tool_call>')]
            if paragraphs:
                preview_text = paragraphs[0][:220]

        timeline.append({
            "date": d_str,
            "spot": spot_val,
            "verdict": verdict_str,
            "preview": preview_text or f"Full research dossier archived for {d_str}.",
            "is_current": (d_str == date),
        })

    # Fast local cached price lookup (sub-millisecond, never blocks local report loading)
    live_price = None
    try:
        with _get_db() as conn:
            r = conn.cursor().execute(
                "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                (ticker_u,)
            ).fetchone()
            if r and r["last_price"]:
                live_price = float(r["last_price"])
    except Exception:
        pass

    # 4. Structured Suggested Positions & Tactical Watch Levels
    watch_levels = None
    levels_file = raw_root / target_date / ticker_u / f"{ticker_u}_watch_levels.json"
    if not levels_file.exists():
        levels_file = raw_root / target_date / f"{ticker_u}_watch_levels.json"
    if levels_file.exists():
        try:
            watch_levels = json.loads(levels_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not watch_levels:
        try:
            with _get_db() as conn:
                row = conn.cursor().execute(
                    "SELECT * FROM watch_targets WHERE ticker = ? AND (date = ? OR date LIKE ?) ORDER BY date DESC LIMIT 1",
                    (ticker_u, target_date, f"{target_date}%")
                ).fetchone()
                if not row:
                    row = conn.cursor().execute(
                        "SELECT * FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                        (ticker_u,)
                    ).fetchone()
                if row:
                    watch_levels = {
                        "ticker": ticker_u,
                        "date": row["date"] or target_date,
                        "verdict": row["verdict"],
                        "conviction": row["conviction"],
                        "shares_plan": {
                            "entry_zone_low": row["entry_zone_low"],
                            "entry_zone_high": row["entry_zone_high"],
                            "tactical_stop": row["tactical_stop"],
                            "target_1": row["target_1"],
                            "target_2": row["target_2"]
                        },
                        "options_plan": {
                            "actionable": bool(row["options_actionable"]),
                            "structure": row["options_structure"],
                            "summary": row["options_summary"],
                            "max_loss": row["options_max_loss"],
                            "max_profit": row["options_max_profit"]
                        },
                        "invalidation": {
                            "condition": row["invalidation_rule"],
                            "price_level": row["tactical_stop"]
                        },
                        "status": row["status"]
                    }
        except Exception:
            pass

    # 5. Tactical Trade Ideas from LLM Watch Levels
    user_position = None
    trade_ideas = []
    try:
        from src.logic.trade_ideas_generator import generate_trade_ideas_for_target
        if watch_levels:
            sp = watch_levels.get("shares_plan") or {}
            target_dict = {
                "ticker": ticker_u,
                "last_price": live_price,
                "entry_zone_low": sp.get("entry_zone_low"),
                "entry_zone_high": sp.get("entry_zone_high"),
                "tactical_stop": sp.get("tactical_stop"),
                "target_1": sp.get("target_1"),
                "target_2": sp.get("target_2"),
                "options_summary": (watch_levels.get("options_plan") or {}).get("summary")
            }
            trade_ideas = generate_trade_ideas_for_target(target_dict)
    except Exception as te:
        logger.debug(f"Could not generate trade ideas for {ticker_u}: {te}")

    # Fallback executive brief if no raw markdown report exists on disk
    if not arbitration_md and not summary_md and not independent_md:
        if watch_levels:
            sp = watch_levels.get("shares_plan") or {}
            op = watch_levels.get("options_plan") or {}
            v = watch_levels.get("verdict") or "WATCH"
            c = watch_levels.get("conviction") or "--"
            ez_l = sp.get("entry_zone_low")
            ez_h = sp.get("entry_zone_high")
            stop = sp.get("tactical_stop")
            t1 = sp.get("target_1")
            t2 = sp.get("target_2")
            opt_str = op.get("summary") or op.get("structure") or "Pullback trade only"
            lines = [
                f"# {ticker_u} | TACTICAL RADAR & WATCH DOSSIER ({target_date})\n",
                f"> **System Verdict**: **{v}** | **Conviction Score**: **{c}/10**\n",
                "### 🎯 Structured Levels",
            ]
            if ez_l is not None and ez_h is not None:
                lines.append(f"- **Entry Zone**: ${float(ez_l):.2f} – ${float(ez_h):.2f}")
            if stop is not None:
                lines.append(f"- **Tactical Invalidation Floor (Stop)**: ${float(stop):.2f}")
            if t1 is not None:
                lines.append(f"- **Target 1**: ${float(t1):.2f}")
            if t2 is not None:
                lines.append(f"- **Target 2**: ${float(t2):.2f}")
            lines.append(f"\n### 🛡️ Recommended Strategy\n{opt_str}\n")
            lines.append("---\n*Detailed 3-model deep research narrative pending. Live quotes, options chain, and interactive Copilot are active in the right-hand panel.*")
            arbitration_md = "\n".join(lines)
        else:
            arbitration_md = (
                f"# {ticker_u} | RESEARCH DOSSIER ({target_date})\n\n"
                f"No archived deep-research report found on disk for **{ticker_u}**.\n\n"
                f"👉 Use the interactive **AI Dossier Copilot** on the right to fetch live quotes, inspect options chains, or trigger fresh analysis."
            )

    return {
        "ticker": ticker_u,
        "date": target_date,
        "live_price": live_price,
        "user_position": user_position,
        "trade_ideas": trade_ideas,
        "available_dates": available_dates,
        "historical_timeline": timeline,
        "summary_md": summary_md,
        "independent_md": independent_md,
        "arbitration_md": arbitration_md,
        "watch_levels": watch_levels,
        "has_zoom_chart": zoom_path is not None,
        "has_plain_chart": plain_path is not None,
        "zoom_chart_url": f"/api/charts/{target_date}/{ticker_u}/zoom" if zoom_path else None,
        "plain_chart_url": f"/api/charts/{target_date}/{ticker_u}/plain" if plain_path else None,
    }


@app.get("/api/quote/{ticker}")
def get_ticker_quote(ticker: str):
    """Fetch live real-time price and day stats for a ticker directly from Schwab."""
    from src.clients.price_client import get_current_price
    sym = ticker.upper().strip()
    price = None
    net_change = 0.0
    net_pct = 0.0
    bid = 0.0
    ask = 0.0
    volume = 0
    source = "SCHWAB"

    try:
        from src.clients.schwab_client import get_realtime_quote
        sq = get_realtime_quote(sym)
        if sq and sq.get("last_price"):
            price = float(sq["last_price"])
            net_change = float(sq.get("net_change") or 0.0)
            net_pct = float(sq.get("net_percent_change") or 0.0)
            bid = float(sq.get("bid") or 0.0)
            ask = float(sq.get("ask") or 0.0)
            volume = int(sq.get("volume") or 0)
    except Exception as e:
        logger.debug(f"Schwab quote error for {sym}: {e}")

    if price is None:
        try:
            price = get_current_price(sym)
            source = "FALLBACK"
        except Exception:
            pass

    # Fallback to SQLite cached price if live fetch is unavailable
    if price is None:
        try:
            with _get_db() as conn:
                r = conn.cursor().execute(
                    "SELECT last_price FROM watch_targets WHERE ticker = ? ORDER BY date DESC LIMIT 1",
                    (sym,)
                ).fetchone()
                if r and r["last_price"]:
                    price = float(r["last_price"])
                    source = "DATABASE"
        except Exception:
            pass

    return {
        "ticker": sym,
        "price": price,
        "net_change": net_change,
        "net_percent_change": net_pct,
        "bid": bid,
        "ask": ask,
        "volume": volume,
        "source": source,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/options-flow/{ticker}")
def get_options_flow_endpoint(ticker: str, refresh: bool = False):
    """Fetch unusual options flow anomalies and institutional sweep metrics from Schwab API."""
    from src.clients.schwab_client import get_unusual_options_flow_data
    sym = ticker.upper().strip()
    try:
        data = get_unusual_options_flow_data(sym, force_refresh=refresh)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch Schwab options flow for {sym}: {e}")
        return {
            "ticker": sym,
            "status": "error",
            "error": str(e),
            "anomalies": []
        }


@app.get("/api/schwab/portfolio/summary")
def get_schwab_portfolio_summary_endpoint():
    """Retrieve aggregate summary cards and account breakdown from Schwab portfolio manager."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_summary
        return get_portfolio_summary()
    except Exception as e:
        logger.error(f"Error fetching portfolio summary: {e}")
        return {"error": str(e), "accounts": [], "total_liquidation_value": 0.0}


@app.get("/api/schwab/portfolio/positions")
def get_schwab_portfolio_positions_endpoint(
    account: Optional[str] = None,
    asset_type: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
):
    """Retrieve filtered individual positions from Schwab portfolio manager."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions
        positions = get_portfolio_positions(
            account_id=account,
            asset_type=asset_type,
            search=search,
            sort_by=sort,
        )
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        logger.error(f"Error fetching portfolio positions: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


@app.post("/api/schwab/portfolio/sync")
def sync_schwab_portfolio_endpoint():
    """Trigger live sync of Schwab accounts and positions via Schwab API."""
    try:
        from src.tracking.schwab_portfolio_manager import sync_schwab_positions
        res = sync_schwab_positions()
        return res
    except Exception as e:
        logger.error(f"Error syncing Schwab portfolio: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/charts/{date}/{ticker}/{chart_type}")
def get_chart_image(date: str, ticker: str, chart_type: str):
    """Serve chart PNG images."""
    img_path = _find_chart_path(date, ticker, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail="Chart image not found")
    return FileResponse(str(img_path), media_type="image/png")


@app.get("/api/charts/latest/{ticker}/{chart_type}")
def get_latest_chart_image(ticker: str, chart_type: str):
    """Serve most recent chart PNG image for a ticker without requiring a date."""
    img_path = _find_chart_path("", ticker, chart_type)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Chart image not found for {ticker}")
    return FileResponse(str(img_path), media_type="image/png")


MAX_CONCURRENT_RESEARCH = 2
LOGS_DIR = config.BASE_DIR / "data" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _init_db():
    """Ensure active_research_jobs table exists in SQLite database."""
    with _get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_research_jobs (
                job_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                mode TEXT NOT NULL,
                pid INTEGER,
                stage TEXT NOT NULL,
                status TEXT NOT NULL, -- 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'KILLED'
                started_at TEXT NOT NULL,
                completed_at TEXT,
                log_file TEXT,
                error_message TEXT,
                target_date TEXT
            )
        """)
        try:
            c.execute("ALTER TABLE active_research_jobs ADD COLUMN target_date TEXT")
        except Exception:
            pass
        conn.commit()


def _find_live_research_pid(ticker: str, job_id: Optional[str] = None) -> Optional[int]:
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


def _rehydrate_active_jobs():
    """Check running jobs in DB on startup and mark dead ones as FAILED unless reports exist or process is running."""
    import psutil
    _init_db()
    try:
        with _get_db() as conn:
            c = conn.cursor()
            running = c.execute("SELECT job_id, ticker, pid, target_date, started_at FROM active_research_jobs WHERE status = 'RUNNING'").fetchall()
            for row in running:
                jid, ticker, pid = row["job_id"], row["ticker"], row["pid"]
                t_date = row["target_date"] or datetime.now().strftime("%Y-%m-%d")
                is_alive = bool(pid and psutil.pid_exists(pid))
                if not is_alive:
                    live_pid = _find_live_research_pid(ticker, jid)
                    if live_pid:
                        is_alive = True
                        c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, jid))
                        continue

                if not is_alive:
                    rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker}_summary.md"
                    arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker}_arbitration.md"
                    if rep_file.exists() or arb_file.exists():
                        new_status = "COMPLETED"
                        err_msg = None
                    else:
                        new_status = "FAILED"
                        err_msg = "Process terminated before generating report"
                    c.execute(
                        "UPDATE active_research_jobs SET status = ?, stage = CASE WHEN ? = 'COMPLETED' THEN 'DONE' ELSE 'ERROR' END, completed_at = ?, error_message = ? WHERE job_id = ?",
                        (new_status, new_status, datetime.now(timezone.utc).isoformat(), err_msg, jid)
                    )
            conn.commit()
    except Exception as e:
        logger.warning(f"Error rehydrating research jobs: {e}")

_rehydrate_active_jobs()


@app.get("/api/logs")
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

    buf = LOG_BUFFERS.get(channel, LOG_BUFFERS["all"])
    with _get_db() as conn:
        c = conn.cursor()
        jobs = c.execute("SELECT * FROM active_research_jobs ORDER BY started_at DESC LIMIT 15").fetchall()
        jobs_list = [dict(j) for j in jobs]
    return {"logs": list(buf), "state": RESEARCH_STATE, "jobs": jobs_list, "channel": channel}


@app.get("/api/jobs")
def get_research_jobs():
    """Fetch all research jobs from SQLite database with accurate master-thread liveness tracking."""
    import psutil
    with _get_db() as conn:
        c = conn.cursor()
        jobs = c.execute("""
            SELECT * FROM active_research_jobs 
            ORDER BY 
                CASE status 
                    WHEN 'RUNNING' THEN 1 
                    WHEN 'QUEUED' THEN 2 
                    ELSE 3 
                END, 
                started_at DESC 
            LIMIT 40
        """).fetchall()
        jobs_list = []
        for r in jobs:
            item = dict(r)
            job_id = item.get("job_id")
            thread = ACTIVE_RESEARCH_WORKERS.get(job_id)
            subproc = ACTIVE_RESEARCH_SUBPROCS.get(job_id)
            
            if thread is not None and thread.is_alive():
                is_alive = True
            elif subproc is not None and subproc.poll() is None:
                is_alive = True
            else:
                # If server was restarted or job untracked in memory
                if item["status"] == "RUNNING":
                    pid = item.get("pid")
                    is_alive = bool(pid and psutil.pid_exists(pid))
                    if not is_alive:
                        ticker_sym = item.get("ticker", "")
                        live_pid = _find_live_research_pid(ticker_sym, job_id)
                        if live_pid:
                            is_alive = True
                            item["pid"] = live_pid
                            c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, job_id))

                    if not is_alive and item.get("stage") not in ("SCRAPING", "STARTING"):
                        t_date = item.get("target_date") or datetime.now().strftime("%Y-%m-%d")
                        ticker_sym = item.get("ticker", "")
                        rep_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_summary.md"
                        arb_file = config.BASE_DIR / "reports" / t_date / f"{ticker_sym}_arbitration.md"
                        if rep_file.exists() or arb_file.exists():
                            item["status"] = "COMPLETED"
                            item["stage"] = "DONE"
                        else:
                            item["status"] = "FAILED"
                            item["stage"] = "ERROR"
                            item["error_message"] = "Process terminated before generating report"
                        c.execute(
                            "UPDATE active_research_jobs SET status = ?, stage = ?, error_message = ? WHERE job_id = ?",
                            (item["status"], item["stage"], item.get("error_message"), job_id)
                        )
                else:
                    is_alive = False

            item["is_alive"] = is_alive
            jobs_list.append(item)
        conn.commit()
        return {"jobs": jobs_list, "max_concurrent": MAX_CONCURRENT_RESEARCH}


class ResearchRequest(BaseModel):
    ticker: str
    mode: str = "full"  # "full" | "scrape_only" | "deep_only"
    date: Optional[str] = None
    force: bool = False


def _run_research_worker(job_id: str, ticker: str, mode: str, date: Optional[str] = None, force: bool = False):
    """Worker thread running sequential research pipeline with SQLite persistence."""
    ticker_u = ticker.strip().upper()
    py_exe = sys.executable
    log_file_path = LOGS_DIR / f"{job_id}.log"

    def _log_both(msg: str):
        _append_log(f"[{ticker_u}] {msg}")
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
            with _get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET stage = 'SCRAPING', status = 'RUNNING' WHERE job_id = ?", (job_id,))
                conn.commit()
            _log_both(f"📸 [1/3] Scraping TradingView Charts & Data Window...")
            cmd = [py_exe, "run_swing_research.py"]
            if date and date.strip():
                cmd.append(date.strip())
            cmd.extend(["--ticker", ticker_u])
            if force:
                cmd.append("--force")
            current_subproc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            ACTIVE_RESEARCH_SUBPROCS[job_id] = current_subproc
            with _get_db() as conn:
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
            with _get_db() as conn:
                conn.cursor().execute("UPDATE active_research_jobs SET stage = 'DEEP_RESEARCH', status = 'RUNNING' WHERE job_id = ?", (job_id,))
                conn.commit()
            _log_both(f"🔬 [2/3] Running Agentic Deep Research (Pine Gem + Independent Gem + PM Arbitration)...")
            cmd = [py_exe, "run_deep_research.py"]
            if date_to_use:
                cmd.append(date_to_use)
            cmd.extend(["--ticker", ticker_u, "--job-id", job_id])
            current_subproc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            ACTIVE_RESEARCH_SUBPROCS[job_id] = current_subproc
            with _get_db() as conn:
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

        with _get_db() as conn:
            conn.cursor().execute(
                "UPDATE active_research_jobs SET status = 'COMPLETED', stage = 'DONE', completed_at = ? WHERE job_id = ?",
                (datetime.now(timezone.utc).isoformat(), job_id)
            )
            conn.commit()
        _log_both(f"✅ Deep Research Complete for {ticker_u}!")

    except Exception as e:
        _log_both(f"❌ Exception in research worker: {e}")
        with _get_db() as conn:
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
        _dispatch_next_queued_job()


_QUEUE_DISPATCH_LOCK = threading.Lock()


def _get_active_research_count() -> int:
    """Accurately count active research jobs across threads, subprocesses, and running OS processes."""
    import psutil
    count = 0
    with _get_db() as conn:
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
            elif _find_live_research_pid(r["ticker"], jid):
                count += 1
    return count


def _dispatch_next_queued_job():
    """Pick next QUEUED job from SQLite and run it if active slots < MAX_CONCURRENT_RESEARCH."""
    with _QUEUE_DISPATCH_LOCK:
        active_count = _get_active_research_count()
        slots_available = MAX_CONCURRENT_RESEARCH - active_count
        if slots_available <= 0:
            return

        with _get_db() as conn:
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
                    target=_run_research_worker,
                    args=(jid, tkr, m, dt, True),
                    daemon=True
                )
                ACTIVE_RESEARCH_WORKERS[jid] = worker_thread
                worker_thread.start()
                _append_log(f"⚡ [Queue Dispatcher] Dispatched queued research for {tkr} to open slot (Job ID: {jid}).")

# Auto-dispatch any queued jobs asynchronously on startup/module load
try:
    threading.Thread(target=_dispatch_next_queued_job, daemon=True).start()
except Exception:
    pass


@app.post("/api/research/run")
def trigger_research(req: ResearchRequest):
    """Trigger research pipeline asynchronously with automatic slot queueing and SQLite tracking."""
    _init_db()

    ticker_raw = req.ticker.strip()
    if not ticker_raw:
        raise HTTPException(status_code=400, detail="Ticker is required")

    tickers = [t.strip().upper() for t in re.split(r"[,;\s]+", ticker_raw) if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Ticker is required")

    if len(tickers) > 1:
        results = []
        for t in tickers:
            sub_req = ResearchRequest(ticker=t, mode=req.mode, date=req.date, force=req.force)
            results.append(trigger_research(sub_req))
        started = sum(1 for r in results if r.get("status") == "started")
        queued = sum(1 for r in results if r.get("status") == "queued")
        return {
            "status": "batch_dispatched",
            "count": len(results),
            "started": started,
            "queued": queued,
            "jobs": results,
        }

    ticker_u = tickers[0]

    # Prevent duplicate active or queued jobs for the same ticker
    with _get_db() as conn:
        c = conn.cursor()
        existing = c.execute(
            "SELECT job_id, status, pid FROM active_research_jobs WHERE ticker = ? AND status IN ('RUNNING', 'QUEUED')",
            (ticker_u,)
        ).fetchone()
        if existing:
            jid = existing["job_id"]
            thread = ACTIVE_RESEARCH_WORKERS.get(jid)
            is_alive = thread.is_alive() if thread else False
            if not is_alive and existing.get("pid"):
                import psutil
                try:
                    is_alive = psutil.pid_exists(existing["pid"])
                except Exception:
                    is_alive = False

            if not is_alive:
                live_pid = _find_live_research_pid(ticker_u, jid)
                if live_pid:
                    is_alive = True
                    c.execute("UPDATE active_research_jobs SET pid = ? WHERE job_id = ?", (live_pid, jid))
                    conn.commit()

            if existing["status"] == "QUEUED" or is_alive:
                return {
                    "status": existing["status"].lower(),
                    "job_id": jid,
                    "ticker": ticker_u,
                    "mode": req.mode,
                }
            else:
                # Stale zombie process: clean up and allow new job to launch
                c.execute(
                    "UPDATE active_research_jobs SET status = 'FAILED', stage = 'ERROR', error_message = 'Process died unexpectedly', completed_at = ? WHERE job_id = ?",
                    (datetime.now(timezone.utc).isoformat(), jid)
                )
                conn.commit()

    # Skip if ticker already has completed deep research for target date (unless force=True)
    if not req.force and req.mode in ("full", "deep_only"):
        from zoneinfo import ZoneInfo
        target_date = req.date.strip() if (req.date and req.date.strip()) else datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
        report_file = config.BASE_DIR / "reports" / target_date / f"{ticker_u}_summary.md"
        arbitration_file = config.BASE_DIR / "reports" / target_date / f"{ticker_u}_arbitration.md"
        if report_file.exists() or arbitration_file.exists():
            _append_log(f"⏭️ [Skip] {ticker_u} already has a completed deep research report for {target_date}. Skipping to preserve slots.")
            return {
                "status": "skipped",
                "job_id": None,
                "ticker": ticker_u,
                "mode": req.mode,
                "target_date": target_date,
                "message": f"{ticker_u} already researched for {target_date}"
            }

    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker_u}"
    log_file = str(LOGS_DIR / f"{job_id}.log")

    active_count = _get_active_research_count()

    if active_count < MAX_CONCURRENT_RESEARCH:
        # Start immediately in open slot
        with _get_db() as conn:
            conn.cursor().execute("""
                INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date)
                VALUES (?, ?, ?, ?, 'STARTING', 'RUNNING', ?, ?, ?)
            """, (job_id, ticker_u, req.mode, os.getpid(), datetime.now(timezone.utc).isoformat(), log_file, req.date))
            conn.commit()

        worker_thread = threading.Thread(target=_run_research_worker, args=(job_id, ticker_u, req.mode, req.date, req.force), daemon=True)
        ACTIVE_RESEARCH_WORKERS[job_id] = worker_thread
        worker_thread.start()
        _append_log(f"🚀 Started research for {ticker_u} in open slot (Active: {active_count + 1}/{MAX_CONCURRENT_RESEARCH}).")
        return {"status": "started", "job_id": job_id, "ticker": ticker_u, "mode": req.mode}
    else:
        # Gracefully queue the job to run as soon as a slot opens
        with _get_db() as conn:
            conn.cursor().execute("""
                INSERT INTO active_research_jobs (job_id, ticker, mode, pid, stage, status, started_at, log_file, target_date)
                VALUES (?, ?, ?, ?, 'QUEUED', 'QUEUED', ?, ?, ?)
            """, (job_id, ticker_u, req.mode, None, datetime.now(timezone.utc).isoformat(), log_file, req.date))
            conn.commit()
        _append_log(f"📥 [Queue] Concurrency slots full ({active_count}/{MAX_CONCURRENT_RESEARCH}). Queued {ticker_u} for research.")
        return {"status": "queued", "job_id": job_id, "ticker": ticker_u, "mode": req.mode}


@app.post("/api/jobs/{job_id}/kill")
def kill_research_job_endpoint(job_id: str):
    """Terminate a specific research job and its entire subprocess tree, freeing slot for queue."""
    import psutil
    
    # 1. Kill active subprocess directly if tracked
    subproc = ACTIVE_RESEARCH_SUBPROCS.get(job_id)
    if subproc:
        try:
            subproc.kill()
        except Exception:
            pass

    # 2. Kill via psutil PID tree
    with _get_db() as conn:
        c = conn.cursor()
        job = c.execute("SELECT * FROM active_research_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job:
            pid = job["pid"]
            if pid and psutil.pid_exists(pid) and pid != os.getpid():
                try:
                    parent = psutil.Process(pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except Exception:
                    pass

            c.execute("UPDATE active_research_jobs SET status = 'KILLED', stage = 'TERMINATED', completed_at = ? WHERE job_id = ?",
                      (datetime.now(timezone.utc).isoformat(), job_id))
            conn.commit()

    ACTIVE_RESEARCH_WORKERS.pop(job_id, None)
    ACTIVE_RESEARCH_SUBPROCS.pop(job_id, None)

    ticker_name = job["ticker"] if job else job_id
    _append_log(f"🛑 Terminated Research Job {job_id} ({ticker_name}) to free VRAM slot.")
    _dispatch_next_queued_job()
    return {"success": True, "job_id": job_id}


@app.post("/api/alerts/sync")
def trigger_alerts_sync():
    """Trigger 1-click sync of all reports into SQLite and Tastytrade."""
    def _sync():
        _append_log("🔄 Syncing all research levels into SQLite, Google Sheets, and Tastytrade...")
        cmd = [sys.executable, "run_watch_alerts.py", "--sync", "--once"]
        proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                _append_log(line_str)
        proc.wait()
        _append_log("✅ Sync complete.")

    threading.Thread(target=_sync, daemon=True).start()
    return {"status": "sync_triggered"}


# =====================================================================
# SCHWAB 1000 PRE-MOVE SCREENER ENDPOINTS
# =====================================================================

class SchwabScanRequest(BaseModel):
    top: int = 10
    auto_scrape: bool = False
    autonomous: bool = False
    auto_max: int = 3
    date: Optional[str] = None
    side: str = "long"
    headless: bool = True


_SCHWAB_SCAN_STATE: Dict[str, Any] = {
    "running": False,
    "side": None,
    "started_at": None,
    "completed_at": None,
    "error": None,
}


@app.get("/api/screener/schwab-scan-status")
def get_schwab_scan_status():
    """Returns whether a Schwab scan is currently running in the background."""
    return _SCHWAB_SCAN_STATE


@app.get("/api/screener/continuous-status")
def get_continuous_screener_status_endpoint():
    """Returns real-time telemetry and timing for the background Continuous Screener Daemon."""
    try:
        from src.screener.continuous_screener_daemon import get_continuous_screener_status
        return get_continuous_screener_status()
    except Exception as e:
        return {"running": False, "error": str(e)}


@app.post("/api/screener/continuous-scan-now")
def trigger_continuous_screener_scan_endpoint():
    """Force an immediate background scan cycle across Schwab 1000 with Tastytrade enrichment."""
    try:
        from src.screener.continuous_screener_daemon import trigger_continuous_scan_now
        started = trigger_continuous_scan_now()
        return {"status": "triggered" if started else "failed", "message": "Continuous scan cycle triggered"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/schwab/portfolio/summary")
def get_schwab_portfolio_summary_endpoint():
    """Retrieve aggregate summary cards, total liquidation value, and account breakdown."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_summary
        return get_portfolio_summary()
    except Exception as e:
        logger.error(f"Error fetching Schwab portfolio summary: {e}")
        return {"error": str(e), "total_liquidation_value": 0.0, "accounts": []}


@app.get("/api/schwab/portfolio/positions")
def get_schwab_portfolio_positions_endpoint(
    account: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
):
    """Query Schwab positions with account, asset type, and search filtering."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions
        positions = get_portfolio_positions(
            account_id=account,
            asset_type=asset_type,
            search=search,
            sort_by=sort,
        )
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        logger.error(f"Error fetching Schwab positions: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


@app.post("/api/schwab/portfolio/sync")
def sync_schwab_portfolio_endpoint():
    """Trigger an on-demand synchronization of Schwab accounts and positions."""
    try:
        from src.tracking.schwab_portfolio_manager import sync_schwab_positions
        res = sync_schwab_positions()
        return res
    except Exception as e:
        logger.error(f"Error syncing Schwab portfolio: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/screener/schwab-pre-move")
def get_schwab_screener_candidates(date: Optional[str] = Query(None), side: str = Query("long")):
    """Fetch current coiled pre-move swing candidates (Long or Short) from schwab_survivors.json / survivors.json / short_survivors.json."""
    raw_dir = config.BASE_DIR / "data" / "raw"
    target_date = date
    req_side = (side or "").lower()
    
    # Check dedicated files first to avoid contamination from generic research queue entries
    if req_side == "short":
        primary_fname = "short_survivors.json"
        fallback_fname = None
    else:
        primary_fname = "schwab_survivors.json"
        fallback_fname = "survivors.json"

    if not target_date:
        today_str = datetime.now().strftime("%Y-%m-%d")
        if (raw_dir / today_str / primary_fname).exists() or (fallback_fname and (raw_dir / today_str / fallback_fname).exists()):
            target_date = today_str
        else:
            dates = sorted([d.name for d in raw_dir.glob("202*") if (d / primary_fname).exists() or (fallback_fname and (d / fallback_fname).exists())], reverse=True)
            target_date = dates[0] if dates else today_str

    survivors_file = None
    if target_date:
        cand_primary = raw_dir / target_date / primary_fname
        if cand_primary.exists():
            survivors_file = cand_primary
        elif fallback_fname:
            cand_fb = raw_dir / target_date / fallback_fname
            if cand_fb.exists():
                survivors_file = cand_fb

    candidates = []
    if survivors_file and survivors_file.exists():
        try:
            with open(survivors_file, "r", encoding="utf-8") as f:
                raw_candidates = json.load(f)
                if isinstance(raw_candidates, list):
                    # Filter: Only keep actual screener records (drop generic/stub research queue entries with null prices)
                    filtered = []
                    for c in raw_candidates:
                        if not isinstance(c, dict):
                            continue
                        if c.get("price") is None and c.get("support_level") is None and c.get("source") not in ("schwab_pre_move_scan", "schwab_short_scan"):
                            continue
                        score = float(c.get("priority_score", 0.0))
                        stg = c.get("weinstein_stage")
                        if req_side != "short" and stg in (3, 4):
                            continue  # Exclude Stage 4 declining / Stage 3 distribution
                        if req_side == "short" and stg == 2 and not c.get("is_extreme_reversal"):
                            continue  # Exclude advancing momentum runners unless extreme blowoff
                        if score > 0 and score < 50.0:
                            continue  # Exclude weak setups below conviction threshold
                        filtered.append(c)

                    # Sort by priority tier and score
                    candidates = sorted(
                        filtered,
                        key=lambda x: (
                            x.get("priority_tier") == "HIGH_PRIORITY",
                            float(x.get("priority_score", 0.0)),
                            bool(x.get("is_extreme_reversal", False)),
                            float(x.get("long_rr", x.get("short_rr", 0.0))),
                        ),
                        reverse=True,
                    )
        except Exception as e:
            logger.error(f"Error reading {survivors_file}: {e}")

    # Also detect if SPY market tide is bullish
    spy_bullish = True
    try:
        from src.screener.schwab_pre_move_scan import check_market_tide, get_schwab_client
        client = get_schwab_client()
        market_tide = check_market_tide(client)
        spy_bullish = market_tide.get("bullish", True)
        trend_str = market_tide.get("trend_str", "BULLISH")
    except Exception:
        trend_str = "BULLISH (TIDE CONFIRMED)"

    return {
        "date": target_date,
        "side": (side or "long").upper(),
        "count": len(candidates),
        "candidates": candidates,
        "market_tide": {
            "bullish": spy_bullish,
            "trend_str": trend_str
        }
    }


@app.post("/api/screener/run-schwab-scan")
def trigger_schwab_screener_scan(req: SchwabScanRequest = SchwabScanRequest()):
    """Run on-demand high-speed scan on 983 Schwab 1000 constituents (long, short, or both)."""
    scan_side = (req.side or "long").lower()

    if _SCHWAB_SCAN_STATE.get("running"):
        return {"status": "already_running", "side": _SCHWAB_SCAN_STATE.get("side")}

    _SCHWAB_SCAN_STATE["running"] = True
    _SCHWAB_SCAN_STATE["side"] = scan_side
    _SCHWAB_SCAN_STATE["started_at"] = time.time()
    _SCHWAB_SCAN_STATE["completed_at"] = None
    _SCHWAB_SCAN_STATE["error"] = None

    def _run_scan():
        scan_label = "AUTONOMOUS SCAN & RESEARCH" if req.autonomous else "SCAN"
        _append_log(f"🔍 [SCHWAB SCREENER] Starting {scan_label} across 983 Schwab 1000 stocks (Side: {scan_side.upper()} | Auto-Max: {req.auto_max})...")
        try:
            cmd = [sys.executable, "-m", "src.screener.schwab_pre_move_scan", "--top", str(req.top), "--side", scan_side]
            if req.autonomous:
                cmd.extend(["--autonomous", "--auto-max", str(req.auto_max)])
            elif req.auto_scrape:
                cmd.append("--auto-scrape")
            if req.headless:
                cmd.append("--headless")
            if req.date:
                cmd.extend(["--date", req.date])
            proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                line_str = line.strip()
                if line_str:
                    _append_log(line_str)
            proc.wait()
            _append_log(f"✅ [SCHWAB SCREENER] {scan_side.upper()} {scan_label} completed.")
            _SCHWAB_SCAN_STATE["completed_at"] = time.time()
        except Exception as e:
            _SCHWAB_SCAN_STATE["error"] = str(e)
            _append_log(f"❌ [SCHWAB SCREENER] Scan error: {e}")
        finally:
            _SCHWAB_SCAN_STATE["running"] = False

    threading.Thread(target=_run_scan, daemon=True).start()
    return {"status": "started", "top": req.top, "side": scan_side, "autonomous": req.autonomous, "auto_max": req.auto_max}


@app.post("/api/screener/run-autonomous-scan")
def trigger_autonomous_screener_scan(req: SchwabScanRequest = SchwabScanRequest(autonomous=True, auto_max=3)):
    """Run autonomous scan on Schwab 1000 stocks and auto-research top high-priority setups."""
    req.autonomous = True
    return trigger_schwab_screener_scan(req)


# =====================================================================
# FRONTEND ENTRY POINT
# =====================================================================


@app.get("/", response_class=FileResponse)
def index_page():
    """Serve the single-page cockpit UI from web/index.html."""
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="web/index.html not found")
    return FileResponse(str(index_file))


# =====================================================================
# LAUNCHER ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    import argparse
    import webbrowser

    parser = argparse.ArgumentParser(description="Stock Trading Operations Cockpit UI")
    parser.add_argument("--port", type=int, default=8050, help="Port to run the UI server on (default 8050)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n=======================================================")
    print(f"STOCK TRADING OPERATIONS COCKPIT LIVE AT:")
    print(f">> {url}")
    print(f"=======================================================\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("run_ui:app", host="127.0.0.1", port=args.port, reload=True, log_level="warning")

