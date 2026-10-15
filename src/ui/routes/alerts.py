"""
TradingView Alerts history, Gmail 1-shot ingestor polling, normal chart scraping, and local LLM triage endpoints.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body

from src import config
from src.ui.state import append_log

logger = logging.getLogger("ui_server")

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts/history")
def get_alerts_history(
    limit: int = 1000,
    date: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
):
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


@router.get("/api/alerts/today-board")
def get_alerts_today_board(date: Optional[str] = None):
    """Today's TradingView alerts grouped by ticker, with how far each name got through research."""
    import json

    from src import config
    from src.tracking.alert_db import get_alerts_for_date, get_eastern_date_str, get_research_queue
    from src.tracking.explore_arm import members

    try:
        d = get_eastern_date_str(date)
        raw = config.BASE_DIR / "data" / "raw" / d
        queue = {q["symbol"]: q for q in get_research_queue(d)}
        explore = members(d)

        def _load(path):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}

        groups = {}
        for a in get_alerts_for_date(d):
            g = groups.setdefault(a["symbol"], {"symbol": a["symbol"], "alerts": 0, "actions": set(), "strategies": set(), "price": a.get("alert_price"), "last": a.get("timestamp"), "setup": a.get("setup")})
            g["alerts"] += 1
            g["actions"].add(a.get("action"))
            g["strategies"].add(a.get("strategy"))
        rows = []
        for sym, g in groups.items():
            fdir = raw / sym
            tri = _load(fdir / f"{sym}_triage.json")
            wl = _load(fdir / f"{sym}_watch_levels.json")
            if wl:
                stage = "DEEP"
            elif tri:
                stage = "LOCAL"
            elif (fdir / f"{sym}_datawindow.json").exists():
                stage = "DATA"
            else:
                stage = "QUEUED"
            reasons = wl.get("gate_reasons") or []
            rows.append({
                "symbol": sym, "alerts": g["alerts"], "actions": sorted(x for x in g["actions"] if x),
                "strategies": sorted(x for x in g["strategies"] if x), "price": g["price"], "last": g["last"],
                "stage": stage, "triage": tri.get("triage"), "triage_reason": tri.get("reason"), "flags": tri.get("flags") or [],
                "deep_verdict": wl.get("verdict"), "gate_rejected": bool(wl.get("level_gate_rejected")),
                "gate_reason": reasons[0] if reasons else None, "explore": sym in explore,
                "queue_status": (queue.get(sym) or {}).get("status"),
            })
        order = {"DEEP": 0, "LOCAL": 1, "DATA": 2, "QUEUED": 3}
        rows.sort(key=lambda r: (order[r["stage"]], r["symbol"]))
        counts = {k: sum(1 for r in rows if r["stage"] == k) for k in order}
        return {"status": "ok", "date": d, "count": len(rows), "counts": counts, "rows": rows}
    except Exception as e:
        logger.error(f"alerts today-board failed: {type(e).__name__} {e}")
        return {"status": "error", "error": str(e), "rows": []}


@router.post("/api/alerts/check-gmail")
def check_gmail_alerts_now():
    """Trigger a fast 1-shot poll of Gmail for TradingView alerts into trading_alerts.db."""

    def _poll():
        append_log("📥 Checking Gmail for fresh TradingView alerts (main.py --once)...")
        try:
            cmd = [sys.executable, "main.py", "--once"]
            proc = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                line_str = line.strip()
                if line_str:
                    append_log(f"[Alert Ingestor] {line_str}")
            proc.wait()
            append_log("✅ Gmail alert check finished.")
        except Exception as err:
            append_log(f"⚠️ Gmail alert check error: {err}")

    threading.Thread(target=_poll, daemon=True).start()
    return {"status": "ok", "message": "Gmail alert poll dispatched"}


@router.post("/api/alerts/local-research")
def run_alert_local_research(payload: dict = Body(...)):
    """Run local research (#ponytail & revanth-gem-local.md) on an individual alert."""
    try:
        from src.tracking.alert_db import DB_PATH
        from src.tracking.alert_evaluator import evaluate_alert_payload

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
                cur.execute(
                    "SELECT * FROM alerts WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                    (symbol.upper(),),
                )
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


@router.post("/api/alerts/scrape-chart")
def scrape_normal_chart_endpoint(payload: dict = Body(...)):
    """Scrape normal TradingView daily candlestick chart screenshot for a symbol."""
    try:
        from src.data.tv_scraper import TVScraper

        symbol = (payload.get("symbol") or "SPY").strip().upper()
        if not re.match(r'^[A-Z0-9.\-_/]{1,12}$', symbol):
            return {"status": "error", "error": "Invalid symbol format"}
        date_str = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(date_str).strip()):
            return {"status": "error", "error": "Invalid date format"}
        date_str = str(date_str).strip()
        scraper = TVScraper(target_date=date_str)
        res = scraper.capture_normal_chart(symbol)
        rel_path = f"/api/chart/{date_str}/{symbol}"
        return {"status": "ok", "image_url": rel_path, **res}
    except Exception as e:
        logger.error(f"Normal chart scrape error for {payload.get('symbol')}: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/api/alerts/evaluate-pending")
def trigger_batch_evaluate_alerts(payload: dict = Body(default={})):
    """Run local LLM evaluation across pending alerts in the background."""

    def _run_batch():
        try:
            from src.tracking.alert_evaluator import evaluate_batch_pending

            limit = payload.get("limit", 50)
            date_str = payload.get("date")
            append_log(f"🤖 Starting batch local LLM triage for pending alerts (limit={limit})...")
            res = evaluate_batch_pending(limit=limit, date_str=date_str)
            append_log(f"✅ Batch alert triage complete: {res.get('message')}")
        except Exception as err:
            append_log(f"⚠️ Batch alert triage error: {err}")

    threading.Thread(target=_run_batch, daemon=True).start()
    return {"status": "ok", "message": "Batch evaluation started in background"}


@router.get("/api/alerts/evaluate-status")
def get_alerts_evaluation_status(date: Optional[str] = None):
    """Return evaluation stats for alerts."""
    try:
        from src.tracking.alert_db import DB_PATH

        with sqlite3.connect(str(DB_PATH), timeout=30.0) as conn:
            cur = conn.cursor()
            if date:
                cur.execute(
                    "SELECT count(*), count(NULLIF(llm_decision, '')) FROM alerts WHERE date = ?",
                    (date,),
                )
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


@router.get("/api/alerts/auto-triage-status")
def get_auto_triage_daemon_status():
    """Return live status of the autonomous background alert triage daemon."""
    from src.tracking.auto_triage_daemon import get_auto_triage_status

    return {"status": "ok", **get_auto_triage_status()}


@router.get("/api/alerts/actionable")
def get_actionable_alerts_endpoint(limit: int = 30):
    """Retrieve filtered, evaluated, live-proximity actionable alerts."""
    try:
        from src.ui.services.opportunity_service import get_actionable_alerts_stream

        alerts = get_actionable_alerts_stream(limit=limit)
        return {
            "status": "ok",
            "count": len(alerts),
            "alerts": alerts,
        }
    except Exception as e:
        logger.error(f"Error fetching actionable alerts: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "alerts": []}

