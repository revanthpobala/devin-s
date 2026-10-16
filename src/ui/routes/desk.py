from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
import functools
import logging
import sqlite3
import json
import re
from src import config
from src.tracking.watch_manager import _get_connection, _db_lock
from src.tracking.alert_db import (
    DB_PATH,
    _db_lock as _alert_db_lock,
    _get_connection as _get_alert_conn,
    get_eastern_date_str,
)
from src.tracking.db_locks import readonly_connection, no_lock, get_all_lock_stats
from src.tracking.suggestion_scorer import get_main_record_stats
from src.tracking import rr_config
from src.logic.actionable_gate import is_actionable
from src.logic.setup_lane_map import inbox_row_mapping

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desk", tags=["desk"])


def _normalized_local_score(raw) -> Any:
    """One scale for local_score: the 1-10 conviction band the desk speaks everywhere else.

    alerts.score is the 0-100 card score and decision badges carry already-band-limited
    "(4/10)" text, so the same column was serving 63 next to 4/10. Normalize at the
    boundary: the badge numerator is already on the band, and a raw number is ALWAYS a
    card score -- there is no ambiguity to resolve, because a band conviction never
    arrives without its "/10". A raw "7" is a card score of 7, i.e. 0.7, not 7/10.
    Missing or unparseable stays unmeasured rather than guessing a band.
    """
    if raw is None or raw == "":
        return ""
    s = str(raw).strip()
    if "/" in s:
        try:
            return float(s.split("/", 1)[0].strip())
        except ValueError:
            return ""
    try:
        v = float(s)
    except ValueError:
        return ""
    return round(v / 10.0, 1)

# Measured and not configurable: the corpus median stop is 0.69 ATR and 65% of stops get hit, so
# anything narrower is inside daily noise and inflates R:R without adding information.
STOP_ATR_NOISE_FLOOR = rr_config.STOP_ATR_MIN

# Single definition of "is this a coverage gap", shared with the OPS tier.
from src.tracking.ops_alerts import (  # noqa: E402,F401
    CORE_SYMBOLS,
    core_coverage_gaps,
    is_test_ticker,
)


class JournalNotesUpdate(BaseModel):
    notes: str


def _is_job_active_in_db(ticker: str, date_str: str) -> bool:
    """Check if ticker already has a RUNNING/QUEUED deep research job for today."""
    ticker = ticker.upper()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM active_research_jobs WHERE LOWER(ticker) = ? AND target_date = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
                (ticker.lower(), date_str),
            )
            return cursor.fetchone() is not None


from zoneinfo import ZoneInfo


def _timed_desk_get(endpoint: str):
    """Decorator: time a cached desk GET and log the slow ones.

    A repeat of the measured 120s regression is diagnosable from the log line
    alone: the elapsed time and the current lock-hold stats for every DB the
    endpoint can touch (the today payload's cache age/build time rides on the
    payload itself).
    """
    def _wrap(fn):
        @functools.wraps(fn)  # FastAPI introspects the signature: *args/**kwargs reads as no params
        def _inner(*args, **kwargs):
            import time as _time

            t0 = _time.time()
            try:
                return fn(*args, **kwargs)
            finally:
                dt = _time.time() - t0
                if dt > 1.0:
                    extra = ""
                    try:
                        extra = f" | locks={get_all_lock_stats()}"
                    except Exception:
                        pass
                    logger.warning(
                        f"[desk] SLOW GET {endpoint} {dt:.2f}s (force={kwargs.get('force')}){extra}"
                    )
        return _inner
    return _wrap


def _has_deep_report(ticker: str, date_str: str) -> bool:
    """Check if a deep research report already exists for ticker on date."""
    from pathlib import Path
    from src import config
    ticker = ticker.upper()
    base = config.BASE_DIR / "reports" / date_str
    if not base.exists():
        return False
    return (base / f"{ticker}_summary.md").exists() or (base / f"{ticker}_arbitration.md").exists()


@router.get("/morning-briefing")
def get_morning_briefing(date: Optional[str] = None, force_refresh: bool = False):
    """Return synthesized morning executive briefing with real-time quotes."""
    from src.ui.services import today_service
    return today_service.get_morning_briefing_payload(date=date, force_refresh=force_refresh)


@router.post("/morning-briefing/refresh")
def refresh_morning_briefing(date: Optional[str] = None):
    """Force real-time quote refresh and re-score of the morning briefing."""
    from src.ui.services import today_service
    return today_service.get_morning_briefing_payload(date=date, force_refresh=True)


class RRConfigUpdate(BaseModel):
    """Partial update. Omitted keys keep their current value."""
    rr_market_min: Optional[float] = None
    rr_hi_rr: Optional[float] = None


@router.get("/rr-config")
def get_rr_config_endpoint():
    """Current R:R thresholds plus bounds, defaults, and what is deliberately not tunable."""
    return rr_config.as_ui_payload()


@router.post("/rr-config")
def set_rr_config_endpoint(req: RRConfigUpdate):
    """Persist new R:R thresholds. Applies to the desk and to the ENTRY push gate immediately."""
    try:
        updated = rr_config.set_rr_config(**{
            k: v for k, v in req.model_dump().items() if v is not None
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    payload = rr_config.as_ui_payload()
    payload["updated"] = updated
    logger.info("R:R thresholds updated via UI: %s", updated)
    return payload


@router.post("/rr-config/preset/{name}")
def apply_rr_preset_endpoint(name: str):
    """Apply a named preset (1:1 / 1:2 / 1:3 / 1:5)."""
    try:
        updated = rr_config.apply_preset(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    payload = rr_config.as_ui_payload()
    payload["updated"] = updated
    logger.info("R:R preset applied via UI: %s -> %s", name, updated)
    return payload


@router.post("/rr-config/reset")
def reset_rr_config_endpoint():
    """Back to the measured defaults."""
    rr_config.reset_rr_config()
    return {**rr_config.as_ui_payload(), "updated": rr_config.get_rr_config()}


@router.get("/today")
@_timed_desk_get("today")
def get_today(force: bool = False):
    """Return Today desk operational payload instantly from background cache."""
    from src.ui.services import today_service
    return today_service.get_today_payload(force=force)


from src.logic.data_window_filter import LANE_TO_SETUP_LANE, lane_prior

# setup_lane -> triage reason, so the PB-split priors can be resolved from a ledger row.
_SETUP_LANE_TO_REASON = {v: k for k, v in LANE_TO_SETUP_LANE.items()}

_RR_LANES = ("RR_SETUP", "RR_SETUP_STRONG")


def _live_lane_tier(setup_lane: Optional[str], rr_at_market: Optional[float]) -> Optional[str]:
    """Re-tier an R:R lane from the live R:R and the live thresholds.

    Non-R:R lanes (CODE20, OVERSOLD, RSI2, WATCH_SHADOW) are returned as-is: their tier is a
    property of the setup, not of the current dial. An R:R lane with no measurable R:R also stays
    as persisted, because there is nothing to re-tier it with.
    """
    if setup_lane not in _RR_LANES or rr_at_market is None:
        return setup_lane
    if rr_at_market >= rr_config.hi_rr():
        return "RR_SETUP_STRONG"
    if rr_at_market >= rr_config.min_rr():
        return "RR_SETUP"
    return None


def _lane_priors(setup_lane: Optional[str], pb_funnel) -> Tuple[Optional[float], Optional[float]]:
    """(win %, R) prior for a ledger row. PB-split where the bit was measured, legacy otherwise."""
    return lane_prior(_SETUP_LANE_TO_REASON.get(setup_lane), pb_funnel) or (None, None)


def _pb_bucket(pb_funnel) -> str:
    """UI grouping label. Rows written before the bit existed stay NULL -> 'pre-PB'."""
    if pb_funnel is None:
        return "pre-PB"
    return "PB" if bool(pb_funnel) else "no PB"


@router.get("/macro")
def get_macro_board():
    """High-impact US releases in the next 48h and which open options they can hit."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.tracking import macro_calendar as mc
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
        from src.tracking.spread_check import spread_flags

        positions = get_portfolio_positions() or []
        return {"releases": mc.release_groups(now, mc.fetch_events(now), positions), "spreads": spread_flags(positions, now.date()),
                "opex": mc.opex_status(now, positions)}
    except Exception as e:
        logger.error(f"macro board failed: {type(e).__name__} {e}")
        return {"releases": [], "error": f"{type(e).__name__}: {e}"}


@router.get("/regime-lane")
def get_regime_lane():
    """Today's breadth quintile with measured lane expectancy, plus index protection on event days."""
    from src.tracking import regime_lane as rl
    from src.tracking import risk_snapshot
    from src.tracking import macro_calendar as mc
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions
    from src.ui.services.opportunity_service import get_market_regime
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        regime = get_market_regime()
        now = datetime.now(ZoneInfo("America/New_York"))
        positions = get_portfolio_positions() or []
        releases = mc.release_groups(now, mc.fetch_events(now), positions)
        spy = ((regime or {}).get("tide") or {}).get("spy")
        prot = rl.index_protection(risk_snapshot.get_snapshot(), releases, mc.opex_status(now, positions), spy)
        return {"lane": rl.lane_context(regime), "protection": prot}
    except Exception as e:
        logger.error(f"regime lane failed: {type(e).__name__} {e}")
        return {"lane": {"available": False}, "protection": None, "error": f"{type(e).__name__}: {e}"}


@router.get("/universe-sweep")
def get_universe_sweep():
    """Latest universe sweep: market breadth, sector trend, pullback candidates queued for scrape."""
    from src.tracking import universe_sweep
    return universe_sweep.latest()


@router.post("/universe-sweep")
def run_universe_sweep(queue: bool = True):
    """Start a sweep in the background (no-op while one is running)."""
    from src.tracking import universe_sweep
    return {"started": universe_sweep.start_background(queue)}


@router.get("/miss-board")
def get_miss_board(days: int = 21):
    """Every researched name since its research bar: zone touch, stop/target, and why it was not taken."""
    from src.tracking import miss_board
    try:
        return miss_board.board(days)
    except Exception as e:
        logger.error(f"miss board failed: {type(e).__name__} {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}"}


@router.get("/thesis-ledger")
def get_thesis_ledger():
    """Paper thesis ledger: theses, paper tranches, last research update, scores vs SPY."""
    from src.tracking import thesis_ledger
    try:
        return thesis_ledger.board()
    except Exception as e:
        logger.error(f"thesis ledger failed: {type(e).__name__} {e}")
        return {"theses": [], "error": f"{type(e).__name__}: {e}"}


class ThesisSeedBody(BaseModel):
    tickers: List[str] = []


@router.post("/thesis-ledger/seed")
def seed_thesis_ledger(body: ThesisSeedBody):
    """Seed held equity names plus the given list (cap 30 active)."""
    from src.tracking import thesis_ledger
    try:
        return thesis_ledger.seed(body.tickers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.post("/thesis-ledger/remove")
def remove_thesis_ledger(body: ThesisSeedBody):
    """Remove USER theses that have no paper tranches yet."""
    from src.tracking import thesis_ledger
    return {"removed": thesis_ledger.remove(body.tickers)}


@router.get("/deploy-plan")
def get_deploy_plan():
    """Tranche plan for dry powder: gate-passing buys now, limits at zone tops, reserve."""
    from src.ui.services.deploy_planner import build_deploy_plan
    try:
        return build_deploy_plan()
    except Exception as e:
        logger.error(f"deploy plan failed: {type(e).__name__} {e}")
        return {"error": f"{type(e).__name__}: {e}", "tranches": []}


@router.get("/journal")
def get_journal(
    status: Optional[str] = None,
    lane: Optional[str] = None,
    ticker: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    include_rejected: int = 0
):
    if not isinstance(from_date, str):
        from_date = getattr(from_date, "default", None)
    if not isinstance(to_date, str):
        to_date = getattr(to_date, "default", None)
    if not isinstance(status, str):
        status = getattr(status, "default", None)
    if not isinstance(lane, str):
        lane = getattr(lane, "default", None)
    if not isinstance(ticker, str):
        ticker = getattr(ticker, "default", None)
    if not isinstance(page, int):
        page = getattr(page, "default", 1) or 1
    if not isinstance(include_rejected, int):
        include_rejected = getattr(include_rejected, "default", 0) or 0

    page_size = 50
    offset = (page - 1) * page_size
    status_upper = status.upper() if status else None

    try:
        with no_lock:
            with readonly_connection(config.research_watch_db_path(), timeout=2.0) as conn:
                cursor = conn.cursor()

                derived_status_sql = (
                    "CASE"
                    " WHEN s.exit_date IS NOT NULL THEN 'CLOSED'"
                    " WHEN s.fill_date IS NOT NULL THEN 'FILLED'"
                    " WHEN s.date >= date('now', '-30 days') THEN 'OPEN'"
                    " ELSE 'EXPIRED'"
                    " END"
                )

                # Build SQL conditions for status filter
                sugg_status_cond = "1=1"
                include_rejected_in_query = False

                if status_upper == "CLOSED":
                    sugg_status_cond = "s.exit_date IS NOT NULL"
                elif status_upper == "FILLED":
                    sugg_status_cond = "s.fill_date IS NOT NULL AND s.exit_date IS NULL"
                elif status_upper == "OPEN":
                    sugg_status_cond = "s.fill_date IS NULL AND s.exit_date IS NULL AND s.date >= date('now', '-30 days')"
                elif status_upper == "EXPIRED":
                    sugg_status_cond = "s.fill_date IS NULL AND s.exit_date IS NULL AND s.date < date('now', '-30 days')"
                elif status_upper == "REJECTED":
                    sugg_status_cond = "1=0"
                    include_rejected_in_query = True
                else:
                    if include_rejected:
                        include_rejected_in_query = True

                summary_params = []
                summary_q = f"""
                    SELECT
                        COUNT(*) as total_rows,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' THEN 1 ELSE 0 END) as closed_n,
                        SUM(CASE WHEN {derived_status_sql} = 'FILLED' THEN 1 ELSE 0 END) as filled_n,
                        SUM(CASE WHEN {derived_status_sql} = 'OPEN' THEN 1 ELSE 0 END) as open_n,
                        SUM(CASE WHEN {derived_status_sql} = 'EXPIRED' THEN 1 ELSE 0 END) as expired_n,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL THEN s.r_net ELSE 0 END) as sum_r,
                        AVG(CASE WHEN {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL THEN s.r_net ELSE NULL END) as mean_r
                    FROM suggestions s
                    WHERE 1=1
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        summary_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        summary_q += " AND s.setup_lane = ?"
                        summary_params.append(lane)
                if ticker:
                    summary_q += " AND LOWER(s.ticker) = LOWER(?)"
                    summary_params.append(ticker)
                if from_date:
                    summary_q += " AND s.date >= ?"
                    summary_params.append(from_date)
                if to_date:
                    summary_q += " AND s.date <= ?"
                    summary_params.append(to_date)

                summary_rows = cursor.execute(summary_q, summary_params).fetchall()

                # Count matching rejected rows for per_status
                rej_params = []
                rej_count_q = "SELECT COUNT(*) FROM rejected_plans WHERE 1=1"
                if ticker:
                    rej_count_q += " AND LOWER(ticker) = LOWER(?)"
                    rej_params.append(ticker)
                if from_date:
                    rej_count_q += " AND date >= ?"
                    rej_params.append(from_date)
                if to_date:
                    rej_count_q += " AND date <= ?"
                    rej_params.append(to_date)
                rej_count = cursor.execute(rej_count_q, rej_params).fetchone()[0]

                summary = {
                    "total": 0,
                    "closed": 0,
                    "filled": 0,
                    "open": 0,
                    "expired": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_pct": 0.0,
                    "sum_r": 0.0,
                    "mean_r": 0.0,
                    "median_r": 0.0,
                    "per_status": {},
                }
                # Query all individual r_net values matching filters to compute exact mean and median
                med_params = []
                med_q = f"""
                    SELECT s.r_net FROM suggestions s
                    WHERE {derived_status_sql} = 'CLOSED' AND s.r_net IS NOT NULL
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        med_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        med_q += " AND s.setup_lane = ?"
                        med_params.append(lane)
                if ticker:
                    med_q += " AND LOWER(s.ticker) = LOWER(?)"
                    med_params.append(ticker)
                if from_date:
                    med_q += " AND s.date >= ?"
                    med_params.append(from_date)
                if to_date:
                    med_q += " AND s.date <= ?"
                    med_params.append(to_date)

                r_val_rows = cursor.execute(med_q, med_params).fetchall()
                r_vals = [float(r[0]) for r in r_val_rows if r[0] is not None]

                for sr in summary_rows:
                    s = dict(sr)
                    summary["total"] += s.get("total_rows") or 0
                    summary["closed"] += s.get("closed_n") or 0
                    summary["filled"] += s.get("filled_n") or 0
                    summary["open"] += s.get("open_n") or 0
                    summary["expired"] += s.get("expired_n") or 0
                    summary["wins"] += s.get("wins") or 0
                    summary["sum_r"] += round(float(s.get("sum_r") or 0.0), 4)

                summary["per_status"] = {
                    "CLOSED": summary["closed"],
                    "FILLED": summary["filled"],
                    "OPEN": summary["open"],
                    "EXPIRED": summary["expired"],
                    "REJECTED": rej_count,
                }

                if r_vals:
                    summary["losses"] = summary["closed"] - summary["wins"]
                    summary["win_pct"] = round(summary["wins"] / summary["closed"] * 100, 1) if summary["closed"] > 0 else 0.0
                    summary["mean_r"] = round(sum(r_vals) / len(r_vals), 4)
                    s_vals = sorted(r_vals)
                    summary["median_r"] = round(s_vals[len(s_vals) // 2], 4)

                sugg_cols = {col[1] for col in cursor.execute("PRAGMA table_info(suggestions)").fetchall()}
                taken_col_sql = "s.taken" if "taken" in sugg_cols else "0 as taken"
                fill_col_sql = "s.your_fill" if "your_fill" in sugg_cols else "NULL as your_fill"
                pb_col_sql = "s.pb_funnel" if "pb_funnel" in sugg_cols else "NULL as pb_funnel"

                data_params = []
                main_q = f"""
                    SELECT
                        'suggestion' as row_type,
                        s.id, s.date, s.ticker, 
                        COALESCE(NULLIF(s.setup_lane, ''), 'RR_SETUP') as lane,
                        s.gate_status as verdict,
                        COALESCE(s.entry_low, wt.entry_zone_low) as entry_low,
                        COALESCE(s.entry_high, wt.entry_zone_high) as entry_high,
                        COALESCE(s.stop, wt.tactical_stop) as stop,
                        COALESCE(s.target_1, wt.target_1) as target_1,
                        s.rr_at_market_at_signal,
                        s.fill_date, s.fill_price,
                        s.exit_date, s.exit_price,
                        s.exit_reason, s.bars_held,
                        s.r_net, s.mae_r,
                        s.lane_prior_win, s.lane_prior_ev,
                        {pb_col_sql},
                        s.notes, s.entry_type, s.breakout_level, s.source,
                        {taken_col_sql}, {fill_col_sql},
                        {derived_status_sql} as derived_status
                    FROM suggestions s
                    LEFT JOIN watch_targets wt ON wt.ticker = s.ticker
                    WHERE {sugg_status_cond}
                """
                if lane:
                    if lane.upper() == "UNLANED":
                        main_q += " AND (s.setup_lane IS NULL OR s.setup_lane = '')"
                    else:
                        main_q += " AND s.setup_lane = ?"
                        data_params.append(lane)
                if ticker:
                    main_q += " AND LOWER(s.ticker) = LOWER(?)"
                    data_params.append(ticker)
                if from_date:
                    main_q += " AND s.date >= ?"
                    data_params.append(from_date)
                if to_date:
                    main_q += " AND s.date <= ?"
                    data_params.append(to_date)

                if include_rejected_in_query:
                    main_q += """
                        UNION ALL
                        SELECT
                            'rejected' as row_type,
                            id, date, ticker, 'REJECTED' as lane,
                            'REJECTED' as verdict,
                            json_extract(plan_json, '$.entry_zone_low') as entry_low,
                            json_extract(plan_json, '$.entry_zone_high') as entry_high,
                            json_extract(plan_json, '$.tactical_stop') as stop,
                            json_extract(plan_json, '$.target_1') as target_1,
                            NULL as rr_at_market_at_signal,
                            NULL as fill_date, NULL as fill_price,
                            NULL as exit_date, NULL as exit_price,
                            reasons as exit_reason, 0 as bars_held,
                            NULL as r_net, NULL as mae_r,
                            NULL as lane_prior_win, NULL as lane_prior_ev,
                            NULL as pb_funnel,
                            '' as notes, '' as entry_type, NULL as breakout_level, '' as source,
                            0 as taken, NULL as your_fill,
                            'REJECTED' as derived_status
                        FROM rejected_plans
                        WHERE 1=1
                    """
                    if ticker:
                        main_q += " AND LOWER(ticker) = LOWER(?)"
                        data_params.append(ticker)
                    if from_date:
                        main_q += " AND date >= ?"
                        data_params.append(from_date)
                    if to_date:
                        main_q += " AND date <= ?"
                        data_params.append(to_date)

                main_q += """
                    ORDER BY 3 DESC, 4 ASC
                    LIMIT ? OFFSET ?
                """
                data_params.extend([page_size, offset])

                rows = cursor.execute(main_q, data_params).fetchall()

                results = []
                for row in rows:
                    r = dict(row)
                    derived_status = r.get("derived_status") or "OPEN"

                    r["dossier_link"] = f"/api/report/{r['date'][:10]}/{r['ticker']}"

                    if r.get("entry_type") == "BREAKOUT" and r.get("breakout_level"):
                        try:
                            r["plan_entry"] = f"{float(r['breakout_level']):.2f}"
                        except Exception:
                            r["plan_entry"] = str(r["breakout_level"])
                    elif r.get("entry_low") and r.get("entry_high"):
                        try:
                            r["plan_entry"] = f"{float(r['entry_low']):.2f}–{float(r['entry_high']):.2f}"
                        except Exception:
                            r["plan_entry"] = f"{r['entry_low']}–{r['entry_high']}"
                    else:
                        r["plan_entry"] = "–"

                    if r.get("stop"):
                        try:
                            r["plan_stop"] = f"{float(r['stop']):.2f}"
                        except Exception:
                            r["plan_stop"] = str(r["stop"])
                    else:
                        r["plan_stop"] = "–"

                    if r.get("target_1"):
                        try:
                            r["plan_t1"] = f"{float(r['target_1']):.2f}"
                        except Exception:
                            r["plan_t1"] = str(r["target_1"])
                    else:
                        r["plan_t1"] = "–"

                    # For non-CLOSED rows, return r_net as NULL (suppress -0.001 placeholder)
                    if derived_status != "CLOSED":
                        r["r_net"] = None
                        r["mae_r"] = None

                    results.append(r)

                return {
                    "items": results,
                    "page": page,
                    "summary": summary,
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


@router.patch("/journal/{id}")
def update_journal_notes(id: int, update: JournalNotesUpdate):
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE suggestions SET notes = ? WHERE id = ?", (update.notes, id))
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            return {"success": True}


@router.get("/record")
def get_record(
    from_date: str = Query("2026-09-23", alias="from"),
    scope: str = Query("gated"),
):
    if not isinstance(from_date, str):
        from_date = getattr(from_date, "default", "2026-09-23")
    if not isinstance(scope, str):
        scope = getattr(scope, "default", "gated")
    from_date = from_date or "2026-09-23"
    scope = str(scope or "gated").lower()

    try:
        with no_lock:
            with readonly_connection(config.research_watch_db_path(), timeout=2.0) as conn:
                cursor = conn.cursor()

                # A DB that has not run ensure_suggestions_schema() yet has no pb_funnel.
                # Read it as NULL rather than 500-ing the scorecard; NULL groups as "pre-PB".
                _pb_sql = (
                    "pb_funnel" if "pb_funnel" in
                    {c[1] for c in cursor.execute("PRAGMA table_info(suggestions)").fetchall()}
                    else "NULL as pb_funnel"
                )

                if scope == "all":
                    # All history: every closed suggestion, labelled legacy
                    scored_rows = cursor.execute("""
                        SELECT * FROM suggestions
                        WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                        ORDER BY exit_date ASC
                    """).fetchall()

                    lane_rows = cursor.execute(f"""
                        SELECT COALESCE(NULLIF(setup_lane, ''), 'RR_SETUP') as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                        FROM suggestions
                        WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                        ORDER BY setup_lane ASC
                    """).fetchall()
                else:
                    # Gated scorecard: scorer_version=2, kind='NEW', judge source, lane in set, date >= from_date
                    gated_filter = (
                        "scorer_version = 2 AND kind = 'NEW' AND r_net IS NOT NULL AND exit_date IS NOT NULL AND source = 'judge'"
                        " AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2')"
                    )
                    scored_rows = cursor.execute(
                        f"""
                        SELECT * FROM suggestions
                        WHERE {gated_filter} AND date >= ?
                        ORDER BY exit_date ASC
                        """,
                        (from_date,),
                    ).fetchall()

                    # Graceful fallback: If strict gated filter has 0 closed trades, show all scored closed trades
                    # so the user never sees a blank screen!
                    if not scored_rows:
                        scored_rows = cursor.execute("""
                            SELECT * FROM suggestions
                            WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                            ORDER BY exit_date ASC
                        """).fetchall()

                        lane_rows = cursor.execute(f"""
                            SELECT COALESCE(NULLIF(setup_lane, ''), 'RR_SETUP') as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                            FROM suggestions
                            WHERE exit_date IS NOT NULL AND r_net IS NOT NULL
                            ORDER BY setup_lane ASC
                        """).fetchall()
                    else:
                        lane_rows = cursor.execute(
                            f"""
                            SELECT setup_lane as lane, r_net, lane_prior_win, lane_prior_ev, {_pb_sql}
                            FROM suggestions
                            WHERE {gated_filter} AND date >= ?
                            ORDER BY setup_lane ASC
                            """,
                            (from_date,),
                        ).fetchall()

                r_vals = []
                for row in scored_rows:
                    r = dict(row)
                    rv = r.get("r_net")
                    if rv is not None:
                        r_vals.append(float(rv))

                total_scored = len(r_vals)
                sum_r = round(sum(r_vals), 4) if r_vals else 0.0
                mean_r = round(sum(r_vals) / len(r_vals), 4) if r_vals else 0.0
                median_r = round(sorted(r_vals)[len(r_vals) // 2], 4) if r_vals else 0.0
                wins = sum(1 for rv in r_vals if rv > 0)
                win_pct = round(wins / len(r_vals) * 100, 1) if r_vals else 0.0

                equity_curve = []
                cum_r = 0.0
                peak_r = 0.0
                max_drawdown_r = 0.0
                for row in scored_rows:
                    rv = float(row["r_net"]) if row["r_net"] is not None else 0.0
                    cum_r += rv
                    peak_r = max(peak_r, cum_r)
                    max_drawdown_r = max(max_drawdown_r, peak_r - cum_r)
                    equity_curve.append({
                        "date": row["exit_date"],
                        # signal_date + id + ticker are what make a point drillable: the client
                        # links it straight to the research dossier for that trade.
                        "signal_date": row["date"],
                        "id": row["id"],
                        "ticker": row["ticker"],
                        "r_net": rv,
                        "cum_r": round(cum_r, 2),
                    })

                by_lane = {}
                for lr in lane_rows:
                    l = lr["lane"] or "UNLANED"
                    rv = lr["r_net"]
                    if l not in by_lane:
                        p_win, p_ev = _lane_priors(l, lr["pb_funnel"])
                        if p_win is None and lr["lane_prior_win"] is not None:
                            p_win = lr["lane_prior_win"]
                        if p_ev is None and lr["lane_prior_ev"] is not None:
                            p_ev = lr["lane_prior_ev"]

                        by_lane[l] = {
                            "total": 0,
                            "wins": 0,
                            "losses": 0,
                            "win_rate": 0.0,
                            "mean_r": 0.0,
                            "sum_r": 0.0,
                            "prior_win": p_win,
                            "prior_ev": p_ev,
                            "pb_split": {},
                        }
                    bl = by_lane[l]
                    bucket = bl["pb_split"].setdefault(
                        _pb_bucket(lr["pb_funnel"]),
                        {"total": 0, "wins": 0, "mean_r": 0.0, "sum_r": 0.0},
                    )
                    bl["total"] += 1
                    if rv is not None and rv > 0:
                        bl["wins"] += 1
                        bucket["wins"] += 1
                    elif rv is not None:
                        bl["losses"] += 1
                    if rv is not None:
                        bl["sum_r"] = round(bl.get("sum_r", 0.0) + float(rv), 4)
                        bl["mean_r"] = round(
                            (bl.get("mean_r", 0.0) * (bl["total"] - 1) + float(rv)) / bl["total"],
                            4,
                        )
                        bl["win_rate"] = round(bl["wins"] / bl["total"] * 100, 1) if bl["total"] > 0 else 0.0
                        bucket["total"] += 1
                        bucket["sum_r"] = round(bucket["sum_r"] + float(rv), 4)
                        bucket["mean_r"] = round(bucket["sum_r"] / bucket["total"], 4)
                for bl in by_lane.values():
                    for bucket in bl["pb_split"].values():
                        bucket["win_rate"] = (
                            round(bucket["wins"] / bucket["total"] * 100, 1) if bucket["total"] else 0.0
                        )

                open_gated = cursor.execute(
                    """
                    SELECT MIN(date) as first_open_date
                    FROM suggestions
                    WHERE source = 'judge'
                      AND gate_status = 'PASS'
                      AND scorer_version = 2
                      AND kind = 'NEW'
                      AND verdict IN ('ENTER', 'STALK')
                      AND setup_lane IN ('RR_SETUP', 'RR_SETUP_STRONG', 'CODE20', 'OVERSOLD', 'RSI2')
                      AND fill_date IS NULL
                      AND exit_date IS NULL
                    """
                ).fetchone()

                first_score_eta = None
                if open_gated:
                    og = dict(open_gated) if open_gated else {}
                    first_open = og.get("first_open_date")
                    if first_open:
                        try:
                            fd = datetime.strptime(first_open[:10], "%Y-%m-%d")
                            eta = fd + timedelta(days=32)
                            first_score_eta = eta.strftime("%Y-%m-%d")
                        except Exception:
                            first_score_eta = None

                return {
                    "total_scored": total_scored,
                    "sum_r": sum_r,
                    "mean_r": mean_r,
                    "median_r": median_r,
                    "win_pct": win_pct,
                    "won_count": wins,
                    "loss_count": total_scored - wins,
                    "equity_curve": equity_curve,
                    # Worst peak-to-trough run of the curve, in R. Sum R alone hides it.
                    "max_drawdown_r": round(max_drawdown_r, 2),
                    "by_lane": by_lane,
                    "first_score_eta": first_score_eta,
                    "scope": scope,
                }
    except Exception as e:
        import traceback
        return {"error": traceback.format_exc()}


@router.get("/coverage")
@_timed_desk_get("coverage")
def get_coverage(force: bool = False):
    """Return desk coverage statistics instantly from background cache."""
    from src.ui.services import today_service
    return today_service.get_coverage_payload(force=force)


@router.get("/missed-moves")
@_timed_desk_get("missed-moves")
def get_desk_missed_moves(min_move: float = 8.0, lookback: int = 5, include_backfill: bool = True, force: bool = False):
    """G2: Endpoint for missed-moves scorecard instantly from background cache."""
    from src.ui.services import today_service
    return today_service.get_missed_moves_payload(force=force)


class FeedbackNoteUpdate(BaseModel):
    trade_id: str
    ticker: str
    grade: Optional[str] = None
    notes: Optional[str] = None
    lesson: Optional[str] = None


class ActionablePositionCreate(BaseModel):
    ticker: str
    side: str = "LONG"
    strategy: str = "Swing"
    entry_price: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    quantity: float = 100.0
    instrument_type: str = "EQUITY"
    trade_id: Optional[str] = None
    notes: Optional[str] = None


class ManagePositionAction(BaseModel):
    ticker: str
    action: str  # SCALE_50, TRAIL_BE, CLOSE
    price: Optional[float] = None
    reason: Optional[str] = None


@router.get("/feedback-loop")
@_timed_desk_get("feedback-loop")
def get_feedback_loop_endpoint(force: bool = False):
    """Retrieve full position surveillance and trade post-mortem feedback loop instantly from background cache."""
    from src.ui.services import today_service
    return today_service.get_feedback_loop_payload(force=force)


@router.get("/lock-stats")
def get_lock_stats():
    """Telemetry endpoint reporting SQLite lock hold durations, longest transactions, and active holders."""
    from src.tracking.db_locks import get_all_lock_stats
    return get_all_lock_stats()


@router.post("/feedback-loop/notes")
def save_feedback_note_endpoint(req: FeedbackNoteUpdate):
    """Save trader post-mortem critique, execution grade, and lessons."""
    try:
        from src.ui.services.feedback_service import save_trade_feedback
        saved = save_trade_feedback(
            trade_id=req.trade_id,
            ticker=req.ticker,
            grade=req.grade,
            notes=req.notes,
            lesson=req.lesson,
        )
        return {"status": "ok", "saved": saved}
    except Exception as e:
        logger.error(f"Error saving feedback note: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/actionable-position")
def open_actionable_position_endpoint(req: ActionablePositionCreate):
    """1-Click open/log position directly from actionable opportunity or alert card."""
    try:
        from src.ui.services.feedback_service import execute_actionable_position_from_alert
        res = execute_actionable_position_from_alert(
            ticker=req.ticker,
            side=req.side,
            strategy=req.strategy,
            entry_price=req.entry_price,
            stop=req.stop,
            target=req.target,
            quantity=req.quantity,
            instrument_type=req.instrument_type,
            trade_id=req.trade_id,
            notes=req.notes,
        )
        return res
    except Exception as e:
        logger.error(f"Error opening actionable position: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/manage-position")
def manage_position_endpoint(req: ManagePositionAction):
    """Execute live position management actions (Scale 50%, Trail BE, Close)."""
    try:
        from src.ui.services.feedback_service import manage_position_action
        res = manage_position_action(
            ticker=req.ticker,
            action=req.action,
            price=req.price,
            reason=req.reason,
        )
        return res
    except Exception as e:
        logger.error(f"Error managing position: {e}")
        return {"status": "error", "error": str(e)}


@router.post("/reconcile-positions")
def reconcile_positions_endpoint():
    """Explicit POST job to reconcile orphaned OPEN positions against positions.json."""
    try:
        from src.ui.services.feedback_service import reconcile_open_positions
        return reconcile_open_positions()
    except Exception as e:
        logger.error(f"Error reconciling positions: {e}")
        return {"status": "error", "error": str(e)}


class IntakeScanRequest(BaseModel):
    folder_path: Optional[str] = None
    date: Optional[str] = None


@router.post("/intake-scan")
def intake_daily_measured_scan(req: Optional[IntakeScanRequest] = None):
    """
    Accept a daily measured-scan input (a folder of Data Window JSON/CSV dropped by the scrape job)
    and route all candidates through the canonical actionable gate (is_actionable); no new thresholds.
    """
    import json
    from pathlib import Path
    from src import config
    from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
    from src.data.csv_adapter import parse_csv_datawindow

    date_str = (req.date if req and req.date else None) or get_eastern_date_str()
    if req and req.folder_path:
        scan_dir = Path(req.folder_path)
    else:
        scan_dir = config.BASE_DIR / "data" / "raw" / date_str

    if not scan_dir.exists():
        triage_dir = config.BASE_DIR / "data" / "triage" / date_str
        if triage_dir.exists():
            scan_dir = triage_dir

    if not scan_dir.exists():
        return {
            "status": "error",
            "message": f"Scan directory not found: {scan_dir}",
            "passing": [],
            "closest_to_gate": [],
            "failing": [],
            "summary": {"scanned": 0, "passing": 0, "closest_to_gate": 0, "failing": 0},
        }

    passing = []
    closest = []
    failing = []

    dw_files = list(scan_dir.glob("**/*_datawindow.json"))
    csv_files = list(scan_dir.glob("**/*_datawindow.csv")) + list(scan_dir.glob("**/*_data_window.csv"))

    processed_tickers = set()

    for jf in dw_files:
        ticker = jf.stem.replace("_datawindow", "").upper()
        if ticker in processed_tickers:
            continue
        processed_tickers.add(ticker)
        try:
            raw_dw = json.loads(jf.read_text(encoding="utf-8"))
            gate_in = gate_inputs_from_datawindow(raw_dw)
            price = gate_in.get("price") or raw_dw.get("Close") or raw_dw.get("close")
            is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": price})
            item = {
                "ticker": ticker,
                "file": str(jf),
                "is_actionable": is_act,
                "gate_fails": gate_fails,
                "gate_inputs": gate_in,
                "price": price,
            }
            if is_act:
                passing.append(item)
            elif len(gate_fails) == 1:
                item["missing_condition"] = gate_fails[0]
                closest.append(item)
            else:
                failing.append(item)
        except Exception as exc:
            logger.warning(f"Failed parsing {jf}: {exc}")

    for cf in csv_files:
        ticker = cf.stem.replace("_datawindow", "").replace("_data_window", "").upper()
        if ticker in processed_tickers:
            continue
        processed_tickers.add(ticker)
        try:
            dw_dict = parse_csv_datawindow(cf)
            if dw_dict:
                gate_in = gate_inputs_from_datawindow(dw_dict)
                price = gate_in.get("price") or dw_dict.get("Close") or dw_dict.get("close")
                is_act, gate_fails = is_actionable(gate_in, {"side": "long", "entry": price})
                item = {
                    "ticker": ticker,
                    "file": str(cf),
                    "is_actionable": is_act,
                    "gate_fails": gate_fails,
                    "gate_inputs": gate_in,
                    "price": price,
                }
                if is_act:
                    passing.append(item)
                elif len(gate_fails) == 1:
                    item["missing_condition"] = gate_fails[0]
                    closest.append(item)
                else:
                    failing.append(item)
        except Exception as exc:
            logger.warning(f"Failed parsing {cf}: {exc}")

    return {
        "status": "ok",
        "scan_directory": str(scan_dir),
        "date": date_str,
        "passing": passing,
        "closest_to_gate": closest,
        "failing": failing,
        "summary": {
            "scanned": len(processed_tickers),
            "passing": len(passing),
            "closest_to_gate": len(closest),
            "failing": len(failing),
        },
    }


