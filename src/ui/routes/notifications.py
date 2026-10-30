"""Notification center API: reads the store the separate notifier process writes."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.tracking import notification_center as nc
from src.tracking.trust_report import build_trust_report, component_scorecard, load_component_rows, load_rows

router = APIRouter(tags=["notifications"])

NOTIFIER_STALE_S = 180.0
STREAM_TICK_S = 2.0


def _notifier_health() -> dict:
    age = nc.heartbeat_age_s()
    return {"alive": age is not None and age < NOTIFIER_STALE_S, "heartbeat_age_s": None if age is None else round(age)}


_LIST_CACHE: dict = {}
LIST_TTL_S = 5.0
TRUST_TTL_S = 120.0
_TRUST_CACHE: dict = {"at": 0.0, "rep": None}


@router.get("/api/notifications")
def list_notifications(limit: int = 10, unread_only: bool = False, before_id: Optional[int] = None):
    limit = max(1, min(int(limit), 100))
    key = (limit, unread_only, before_id)
    mx = nc.max_id()
    hit = _LIST_CACHE.get(key)
    if hit and hit["max_id"] == mx and time.time() - hit["at"] < LIST_TTL_S:
        return {**hit["body"], "notifier": _notifier_health()}
    rows = nc.list_recent(limit=limit + 1, unread_only=unread_only, before_id=before_id)
    body = {"items": rows[:limit], "has_more": len(rows) > limit, "unread": nc.unread_count()}
    if len(_LIST_CACHE) > 50:
        _LIST_CACHE.clear()
    _LIST_CACHE[key] = {"at": time.time(), "max_id": mx, "body": body}
    return {**body, "notifier": _notifier_health()}


@router.get("/api/notifications/stream")
async def stream(request: Request):
    """Server-sent events: each new notification is pushed within STREAM_TICK_S of the notifier writing it."""
    last = request.headers.get("last-event-id")
    after = int(last) if last and last.isdigit() else await asyncio.to_thread(nc.max_id)

    async def gen():
        nonlocal after
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            for n in await asyncio.to_thread(nc.list_after, after):
                after = n["id"]
                yield f"id: {n['id']}\ndata: {json.dumps(n)}\n\n"
            yield ": ping\n\n"
            await asyncio.sleep(STREAM_TICK_S)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/notifications/unread")
def unread():
    return {"unread": nc.unread_count(), "notifier": _notifier_health()}


class ReadBody(BaseModel):
    id: Optional[int] = None


@router.post("/api/notifications/read")
def mark_read(body: ReadBody):
    _LIST_CACHE.clear()
    return {"marked": nc.mark_read(body.id), "unread": nc.unread_count()}


class DeleteBody(BaseModel):
    ids: list[int]


@router.post("/api/notifications/delete")
def delete_notifications(body: DeleteBody):
    n = nc.delete(body.ids)
    _LIST_CACHE.clear()
    return {"deleted": n, "unread": nc.unread_count()}


@router.get("/api/notifications/trust")
def trust():
    if _TRUST_CACHE["rep"] is not None and time.time() - _TRUST_CACHE["at"] < TRUST_TTL_S:
        return _TRUST_CACHE["rep"]
    rep = _build_trust()
    if "error" not in rep:
        _TRUST_CACHE.update(at=time.time(), rep=rep)
    return rep


def _build_trust():
    try:
        rep = build_trust_report(load_rows())
        try:
            from src.tracking.counterfactual import policy_report

            rep["components"] = {"scorecard": component_scorecard(load_component_rows()), "entry_policy": policy_report()}
            try:
                from src.tracking.research_scorecard import report as research_report
                rep["components"]["research_scorecard"] = research_report()
            except Exception as e_rs:
                rep["components"]["research_scorecard"] = {"error": str(e_rs)}
        except Exception as e_comp:
            rep["components"] = {"error": str(e_comp)}
        try:
            from src.tracking.cc_postmortem import summary as cc_summary

            rep["covered_calls"] = cc_summary()
        except Exception as e_cc:
            rep["covered_calls"] = {"error": str(e_cc)}
        try:
            from src.tracking.catalyst_ledger import mover_summary

            rep["movers"] = mover_summary()
        except Exception as e_mv:
            rep["movers"] = {"error": str(e_mv)}
        try:
            from src.tracking.thesis_ledger import summary as thesis_summary

            rep["thesis_ledger"] = thesis_summary()
        except Exception as e_th:
            rep["thesis_ledger"] = {"error": str(e_th)}
        try:
            from src.tracking.alert_setups import summary as alert_setups_summary

            rep["alert_setups"] = alert_setups_summary()
        except Exception as e_as:
            rep["alert_setups"] = {"error": str(e_as)}
        try:
            from src.tracking.agent_trader import summary as agent_summary

            rep["agent"] = agent_summary()
        except Exception as e_ag:
            rep["agent"] = {"error": str(e_ag)}
        try:
            from src.tracking.decision_log import summary as decision_summary

            rep["decisions"] = decision_summary()
            from src.tracking import agent_trader, decision_log
            from src.ui.routes.trades import get_suggested_trades_endpoint

            skipped = [i for i, d in decision_log.latest_by_plan().items() if d["decision"] != "TAKE"]
            rep["decisions"]["scored"] = decision_log.score(get_suggested_trades_endpoint().get("trades", []), skipped, agent_trader.passed_plan_ids())
        except Exception as e_dec:
            rep["decisions"] = {"error": str(e_dec)}
        return rep
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/backend/status")
def backend_status():
    from src.tracking import backend_control as bc

    data = bc.read()
    scr, tri = data.get("screener") or {}, data.get("triage") or {}
    return {
        "alive": bc.alive(data),
        "pid": data.get("pid"),
        "stopped_by_user": bool(data.get("stopped_by_user")),
        "last_backup": data.get("last_backup"), "last_chain": data.get("last_chain"),
        "last_chain_error": data.get("last_chain_error"), "last_backup_error": data.get("last_backup_error"),
        "uptime_s": round(time.time() - data["started_at"]) if bc.alive(data) and data.get("started_at") else None,
        "screener": {k: scr.get(k) for k in ("paused", "is_scanning", "last_scan_time", "scan_count", "last_status", "last_error", "auto_deep_count_today")},
        "triage": {k: tri.get(k) for k in ("is_processing", "total_triaged", "pending_count", "last_run_time", "llm_online")},
    }


@router.post("/api/backend/start")
def backend_start():
    from src.tracking import backend_control as bc

    return bc.start_process()


@router.post("/api/backend/stop")
def backend_stop():
    from src.tracking import backend_control as bc

    return bc.stop_process()


@router.get("/api/risk/snapshot")
def risk_snapshot():
    from src.tracking.risk_snapshot import get_snapshot

    return get_snapshot()
