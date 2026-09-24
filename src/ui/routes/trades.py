"""
Suggested trades, trade auditing, superforecasting calibration, and live options spread calculations.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src import config
from src.ui.routes.status import get_company_names_dict
from src.ui.state import get_db

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["trades"])

_SUGGESTED_CACHE: dict = {}
_SUGGESTED_CACHE_TTL = 30  # 30 seconds


@router.get("/api/trades/audit")
def get_trades_audit(
    tab: str = Query("ALL"),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    window: int = Query(100, ge=0, le=5000),
):
    """Returns the pre-computed suggested trades audit trail, tab counts, and summary from SQLite."""
    try:
        from src.tracking.suggested_trades_auditor import get_audit_summary
        return get_audit_summary(
            tab=tab,
            search=search,
            page=page,
            page_size=page_size,
            window=window,
            force_sync=False
        )
    except Exception as e:
        logger.error(f"Error in get_trades_audit: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": {}, "trades": [], "pagination": {}}


@router.post("/api/trades/audit/evaluate")
def evaluate_trades_audit(
    window: int = Query(100, ge=0, le=5000),
):
    """Triggers on-demand evaluation of suggested trades against live quotes and updates SQLite."""
    try:
        from src.tracking.suggested_trades_auditor import evaluate_all_suggested_trades, _AUDIT_CACHE
        result = evaluate_all_suggested_trades(refresh_quotes=True, window=window)
        _AUDIT_CACHE.clear()
        _SUGGESTED_CACHE.clear()
        return {"success": True, "message": f"Successfully evaluated live quotes on demand for last {window} trades.", **result}
    except Exception as e:
        logger.error(f"Error in evaluate_trades_audit: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": {}, "trades": []}


@router.post("/api/trades/suggestions/evaluate")
def evaluate_suggestions_endpoint():
    """Triggers on-demand honest 21-bar R scoring for all suggestions."""
    try:
        from src.tracking.suggestion_scorer import evaluate_all_suggestions
        result = evaluate_all_suggestions()
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Error in evaluate_suggestions_endpoint: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/api/trades/suggested")
def get_suggested_trades_endpoint():
    """Returns structured suggested trades from watch_targets with live PnL and outcome metrics."""
    try:
        _cache_key = "suggested_trades"
        _now = time.time()
        if _cache_key in _SUGGESTED_CACHE:
            _cached_ts, _cached_result = _SUGGESTED_CACHE[_cache_key]
            if _now - _cached_ts < _SUGGESTED_CACHE_TTL:
                return _cached_result

        rep_root = config.BASE_DIR / "reports"
        with get_db() as conn:
            c = conn.cursor()
            try:
                rows = c.execute(
                    """
                    SELECT wt.rowid as id, wt.*, 
                           s.gate_status, s.setup_lane, s.kind, s.atr_at_signal, s.rr_at_market_at_signal
                    FROM watch_targets wt
                    LEFT JOIN suggestions s ON s.id = wt.suggestion_id
                    WHERE wt.is_active IS NULL OR wt.is_active = 1
                    ORDER BY wt.date DESC, wt.updated_at DESC
                    """
                ).fetchall()
            except Exception:
                rows = c.execute(
                    "SELECT rowid as id, * FROM watch_targets WHERE is_active IS NULL OR is_active = 1 ORDER BY date DESC, updated_at DESC"
                ).fetchall()

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
            t["gate_status"] = t.get("gate_status") or "UNKNOWN"
            t["setup_lane"] = t.get("setup_lane") or "DEFAULT"
            t["kind"] = t.get("kind") or "NEW"
            atr_val = t.get("atr_at_signal") or (raw.get("indicators", {}).get("RSI2 ATR14") if raw else None)
            try:
                atr_num = float(atr_val) if atr_val is not None else 0.0
            except (ValueError, TypeError):
                atr_num = 0.0
            if atr_num > 0 and stop_loss is not None and entry_mid is not None:
                t["stop_in_atr"] = round(abs(entry_mid - stop_loss) / atr_num, 2)
            else:
                t["stop_in_atr"] = None
            t["rr_at_market"] = t.get("rr_at_market_at_signal")
            trades.append(t)

        summary = {
            "total": len(trades),
            "in_zone": sum(1 for tr in trades if tr.get("status") == "IN_ZONE"),
            "stalking": sum(1 for tr in trades if tr.get("status") == "STALKING"),
            "target_hit": sum(1 for tr in trades if tr.get("status") in ("TARGET_HIT", "COMPLETED")),
            "stopped": sum(1 for tr in trades if tr.get("status") in ("STOP_BREACHED", "STOPPED", "INVALIDATED")),
        }
        result = {"trades": trades, "summary": summary}
        _SUGGESTED_CACHE[_cache_key] = (_now, result)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch suggested trades: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/superforecasting/stats")
def get_superforecasting_calibration_stats_api():
    """Fetch Brier calibration stats for Model A (Pine) vs Model B (Independent)."""
    try:
        from src.tracking.watch_manager import get_superforecasting_stats
        stats = get_superforecasting_stats()
        return stats
    except Exception as e:
        return {"error": str(e), "model_a": {}, "model_b": {}, "recent_audits": []}


@router.post("/api/superforecasting/audit")
def trigger_superforecasting_audit_api():
    """Trigger an immediate audit scan of historical report predictions."""
    try:
        from src.tracking.superforecasting_auditor import run_superforecasting_audit
        res = run_superforecasting_audit()
        return {"status": "ok", **res}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/api/options/spread-calc")
def calculate_options_spread_live(
    ticker: str,
    expiration: str,
    short_strike: float,
    long_strike: float,
    structure: Optional[str] = "BULL_PUT_SPREAD"
):
    """Live mathematical options spread calculator using real-time Schwab market data."""
    try:
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
            live_mid = round(s_mid - l_mid, 2)
            live_natural = round(s_bid - l_ask, 2)
            max_profit = round(live_mid * 100, 2)
            max_loss = round((width - live_mid) * 100, 2)
            pricing_type = "CREDIT"
        else:
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


@router.get("/api/trades/sources")
def get_trades_per_source():
    """Return performance metrics aggregated per research source from suggestions ledger."""
    try:
        from src.tracking.suggestions_ledger import get_per_source_stats
        return {"success": True, "sources": get_per_source_stats()}
    except Exception as e:
        logger.error(f"Error fetching per-source stats: {e}", exc_info=True)
        return {"success": False, "error": str(e), "sources": []}


@router.post("/api/trades/suggestions/{suggestion_id}/taken")
def update_suggestion_taken_endpoint(
    suggestion_id: int,
    taken: bool = Query(...),
    your_fill: Optional[float] = Query(None),
    notes: Optional[str] = Query(None),
):
    """Toggle taken flag and optionally update your_fill/notes on a suggestion row."""
    try:
        from src.tracking.suggestions_ledger import update_suggestion_user_input
        ok = update_suggestion_user_input(suggestion_id, taken=taken, your_fill=your_fill, notes=notes)
        return {"success": ok}
    except Exception as e:
        logger.error(f"Error updating suggestion {suggestion_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/api/scoreboard")
def get_scoreboard(since: Optional[str] = None):
    """Return unified empirical scoreboard for Intraday and Swing suggestions."""
    try:
        from src.tracking.intraday_stats import generate_postmortem_stats
        from src.tracking.suggestions_ledger import get_per_source_stats, get_main_record_stats

        stats = generate_postmortem_stats(since=since)
        intraday_data = {
            "by_grade": stats.get("by_grade", {}),
            "by_hour": stats.get("by_hour", {}),
            "by_score": stats.get("by_score", {}),
            "by_llm": stats.get("by_llm", {}),
            "n_scored": stats.get("n_scored", 0),
            "go_no_go": stats.get("go_no_go", {}),
        }

        swing_data = get_per_source_stats(since=since)
        main_record_data = get_main_record_stats()
        return {
            "intraday": intraday_data,
            "swing": swing_data,
            "main_record": main_record_data,
        }
    except Exception as e:
        logger.error(f"Error generating scoreboard: {e}", exc_info=True)
        return {
            "intraday": {
                "by_grade": {},
                "by_hour": {},
                "by_score": {},
                "by_llm": {},
                "n_scored": 0,
                "go_no_go": {},
            },
            "swing": [],
            "error": str(e),
        }
