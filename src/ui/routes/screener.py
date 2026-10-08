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


def _pb_lane_prior(rr: float, pb) -> Optional[tuple]:
    """(win %, R) prior for the row's R:R tier, split by the PB bit when it was measured.

    The tier split follows the same HI-RR threshold the desk and the ENTRY gate use, so one
    change moves all three consistently. A row whose PB state was never measured gets None rather
    than a long-side prior it has no claim to.
    """
    from src.logic.data_window_filter import lane_prior
    from src.tracking.rr_config import hi_rr

    if rr is None or rr <= 0:
        return None
    if pb is None:
        return None          # not measured -> no prior, rather than borrowing the long-side one
    reason = "rr_at_market_lane_strong" if rr >= hi_rr() else "rr_at_market_lane"
    return lane_prior(reason, bool(pb))


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


@router.post("/pause")
def pause_continuous_screener_endpoint():
    """Pause continuous scanning."""
    try:
        from src.screener.continuous_screener_daemon import pause_continuous_screener
        paused = pause_continuous_screener()
        append_log("⏸️ [SCHWAB SCREENER] Continuous screener paused by user.")
        return {"status": "paused" if paused else "not_running", "paused": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/resume")
def resume_continuous_screener_endpoint():
    """Resume continuous scanning."""
    try:
        from src.screener.continuous_screener_daemon import resume_continuous_screener
        resumed = resume_continuous_screener()
        append_log("▶️ [SCHWAB SCREENER] Continuous screener resumed by user.")
        return {"status": "resumed" if resumed else "not_running", "paused": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/toggle-pause")
def toggle_continuous_screener_pause_endpoint():
    """Toggle continuous scanning paused state."""
    try:
        from src.screener.continuous_screener_daemon import toggle_continuous_screener_pause
        new_paused = toggle_continuous_screener_pause()
        state_str = "paused" if new_paused else "resumed"
        append_log(f"{'⏸️' if new_paused else '▶️'} [SCHWAB SCREENER] Continuous screener {state_str} by user.")
        return {"status": "ok", "paused": new_paused}
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
                        if c.get("lane") != "UNMEASURED" and score > 0 and score < 50.0:
                            continue
                        filtered.append(c)

                    candidates = sorted(
                        filtered,
                        key=lambda x: (
                            bool(x.get("pb_funnel")) if req_side != "short" else (x.get("priority_tier") == "HIGH_PRIORITY"),
                            float(x.get("proxy_rr") or x.get("short_rr", x.get("long_rr", 0.0)) or 0.0),
                            bool(x.get("is_extreme_reversal", False)),
                        ),
                        reverse=True,
                    )
        except Exception as e:
            logger.error(f"Error reading {survivors_file}: {e}")

    enriched = []
    for c in candidates:
        c = dict(c)
        # The priority tier is retired as a display signal: MEDIUM/HIGH measured flat-to-negative,
        # and post-2020 non-PB onsets sit at +0.012R (noise). What separates the rows is the PB
        # funnel plus the lane's measured prior, so the table shows those instead.
        pb = c.get("pb_funnel")
        # `atrs_up` is the manifest's name for the stop width in ATR.
        rr = float(c.get("proxy_rr") or c.get("short_rr", c.get("long_rr")) or 0.0) or 0.0
        prior = _pb_lane_prior(rr, pb)
        c["display_prior_win"] = prior[0] if prior else None
        c["display_prior_ev"] = prior[1] if prior else None
        c["pb_bucket"] = "unmeasured" if pb is None else ("PB" if pb else "no PB")
        # A non-PB long is a measured exclusion, so it is greyed rather than shown as equal.
        c["eligible"] = bool(pb) if req_side != "short" else None
        stop_width_atr = c.get("stop_width_atr") or c.get("atrs_up")
        # The scan manifest writes this as `atrs_up`; accept that name too, or the column the
        # table advertises renders blank for every row.
        c["stop_width_atr"] = stop_width_atr
        enriched.append(c)

    spy_bullish = False
    tide_available = False
    try:
        from src.screener.schwab_pre_move_scan import check_market_tide, get_schwab_client
        client = get_schwab_client()
        market_tide = check_market_tide(client)
        # check_market_tide returns "is_bullish". Reading "bullish" always missed and fell back to
        # True, so the API reported bullish:true next to a DEFENSIVE / BEARISH trend_str.
        spy_bullish = bool(market_tide.get("is_bullish", False))
        tide_available = bool(market_tide.get("available", False))
        trend_str = market_tide.get("trend_str", "UNKNOWN")
    except Exception:
        trend_str = "UNKNOWN"

    return {
        "date": target_date,
        "side": (side or "long").upper(),
        "count": len(enriched),
        "eligible_count": sum(1 for c in enriched if c.get("eligible")),
        "candidates": enriched,
        "market_tide": {
            "bullish": spy_bullish,
            "available": tide_available,
            "trend_str": trend_str
        },
        # Display guidance, so the frontend does not have to hardcode measured constants.
        "columns": ["ticker", "price", "stop", "long_rr", "stage", "pb", "lane_prior",
                    "stop_width_atr"],
        "notes": (
            "Priority tier retired as a display signal. PB funnel is the measured gate; "
            "non-PB longs are a measured exclusion."
        ),
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
