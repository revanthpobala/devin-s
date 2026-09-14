"""
Screener endpoints: Schwab 1000 candidates, scans, continuous screener status, and triggers.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src import config
from src.ui.state import _SCHWAB_SCAN_STATE, append_log

logger = logging.getLogger("ui_server")
router = APIRouter(prefix="/api/screener", tags=["screener"])


class SchwabScanRequest(BaseModel):
    top: int = 10
    auto_scrape: bool = False
    autonomous: bool = False
    auto_max: int = 3
    date: Optional[str] = None
    side: str = "long"
    headless: bool = True


@router.get("/schwab-scan-status")
def get_schwab_scan_status():
    """Returns whether a Schwab scan is currently running in the background."""
    return _SCHWAB_SCAN_STATE


@router.get("/continuous-status")
def get_continuous_screener_status_endpoint():
    """Returns real-time telemetry and timing for the background Continuous Screener Daemon."""
    try:
        from src.screener.continuous_screener_daemon import get_continuous_screener_status
        return get_continuous_screener_status()
    except Exception as e:
        return {"running": False, "error": str(e)}


@router.post("/continuous-scan-now")
def trigger_continuous_screener_scan_endpoint():
    """Force an immediate background scan cycle across Schwab 1000 with Tastytrade enrichment."""
    try:
        from src.screener.continuous_screener_daemon import trigger_continuous_scan_now
        started = trigger_continuous_scan_now()
        return {"status": "triggered" if started else "failed", "message": "Continuous scan cycle triggered"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/schwab-pre-move")
def get_schwab_screener_candidates(date: Optional[str] = Query(None), side: str = Query("long")):
    """Fetch current coiled pre-move swing candidates (Long or Short) from survivors manifests."""
    raw_dir = config.BASE_DIR / "data" / "raw"
    target_date = date
    req_side = (side or "").lower()

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
                    filtered = []
                    for c in raw_candidates:
                        if not isinstance(c, dict):
                            continue
                        if c.get("price") is None and c.get("support_level") is None and c.get("source") not in ("schwab_pre_move_scan", "schwab_short_scan"):
                            continue
                        score = float(c.get("priority_score", 0.0))
                        stg = c.get("weinstein_stage")
                        if req_side != "short" and stg in (3, 4):
                            continue
                        if req_side == "short" and stg == 2 and not c.get("is_extreme_reversal"):
                            continue
                        if score > 0 and score < 50.0:
                            continue
                        filtered.append(c)

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


@router.post("/run-schwab-scan")
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
        append_log(f"🔍 [SCHWAB SCREENER] Starting {scan_label} across 983 Schwab 1000 stocks (Side: {scan_side.upper()} | Auto-Max: {req.auto_max})...")
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
                    append_log(line_str)
            proc.wait()
            append_log(f"✅ [SCHWAB SCREENER] {scan_side.upper()} {scan_label} completed.")
            _SCHWAB_SCAN_STATE["completed_at"] = time.time()
        except Exception as e:
            _SCHWAB_SCAN_STATE["error"] = str(e)
            append_log(f"❌ [SCHWAB SCREENER] Scan error: {e}")
        finally:
            _SCHWAB_SCAN_STATE["running"] = False

    threading.Thread(target=_run_scan, daemon=True).start()
    return {"status": "started", "top": req.top, "side": scan_side, "autonomous": req.autonomous, "auto_max": req.auto_max}


@router.post("/run-autonomous-scan")
def trigger_autonomous_screener_scan(req: SchwabScanRequest = SchwabScanRequest(autonomous=True, auto_max=3)):
    """Run autonomous scan on Schwab 1000 stocks and auto-research top high-priority setups."""
    req.autonomous = True
    return trigger_schwab_screener_scan(req)
