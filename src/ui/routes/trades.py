"""
Suggested trades, trade auditing, superforecasting calibration, and live options spread calculations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src import config
from src.ui.routes.status import get_company_names_dict
from src.ui.state import get_db

logger = logging.getLogger("ui_server")
router = APIRouter(tags=["trades"])


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
            force_sync=True
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
        from src.tracking.suggested_trades_auditor import evaluate_all_suggested_trades
        result = evaluate_all_suggested_trades(refresh_quotes=True, window=window)
        return {"success": True, "message": f"Successfully evaluated live quotes on demand for last {window} trades.", **result}
    except Exception as e:
        logger.error(f"Error in evaluate_trades_audit: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": {}, "trades": []}


@router.get("/api/trades/suggested")
def get_suggested_trades_endpoint():
    """Returns structured suggested trades from watch_targets with live PnL and outcome metrics."""
    try:
        rep_root = config.BASE_DIR / "reports"
        with get_db() as conn:
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
