"""Deploy planner: turns dry powder into tranches (now, limits, reserve) from the measured gate.

Sizing and gating use the measured R:R gate only: the R:R band at market scales size, bad breadth halves it. VIX, Fear & Greed and the index tide are context:
none were tested on the corpus, so they scale size (half size when the tide is not bullish) but never
raise conviction or pick names.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from src.tracking import rr_config
from src.tracking.risk_snapshot import CASH_LIKE

RISK_PCT = 0.01
NAME_CAP_PCT = 0.05
HELD_CAP_PCT = 0.10
ALIASES = {"GOOG": "GOOGL"}
RR_HI_MULT = 1.5
ONLY_ZONE_FAIL = {"not in the long zone"}
EARNINGS_BLACKOUT_DAYS = 5
BLOCK_MARKERS = ("action code", "fade gate")


def _root(sym: str) -> str:
    s = (sym or "").upper()
    return ALIASES.get(s, s)


def held_weights(positions: List[Dict[str, Any]], total: float) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if total <= 0:
        return out
    for p in positions:
        if (p.get("asset_type") or "").upper() != "EQUITY":
            continue
        k = _root(p.get("underlying_symbol") or p.get("symbol"))
        out[k] = out.get(k, 0.0) + float(p.get("market_value") or 0.0) / total
    return out


def dry_powder(summary: Dict[str, Any], positions: List[Dict[str, Any]]) -> Dict[str, float]:
    cash = float(summary.get("total_cash_balance") or 0.0)
    mm = sum(float(p.get("market_value") or 0.0) for p in positions if (p.get("symbol") or "").upper() in CASH_LIKE)
    return {"cash": round(cash, 2), "money_market": round(mm, 2), "total": round(cash + mm, 2)}


VIX_BANDS = (
    (15.0, "Below 15", "Complacency", "Halted or slow: accumulate cash, build watchlists, no chasing", "Buy cheap protective puts or calls"),
    (25.0, "15-25", "Normal", "Steady and systematic: rebalance laggards, keep existing risk", "Standard covered calls or credit spreads"),
    (35.0, "25-35", "Stress", "Moderate, tiered: deploy first tranche (about 25% of dry powder) into blue chips", "Sell cash-secured puts on high-conviction names"),
    (float("inf"), "Above 35", "Panic", "Rapid: deploy remaining tranches, focus on index ETFs", "Short-term long-volatility hedges"),
)


def vix_pace(vix: Optional[float]) -> Optional[Dict[str, str]]:
    if vix is None:
        return None
    for top, rng, mood, pace, hedge in VIX_BANDS:
        if vix < top:
            return {"range": rng, "sentiment": mood, "pace": pace, "options": hedge}
    return None


def rr_size_mult(rr: float, min_rr: float) -> float:
    """Size by measured R:R at market band: >= hi tier 1.5x, 2.0-3.0 1.0x, floor-2.0 0.5x, under the floor 0."""
    if rr >= rr_config.hi_rr():
        return RR_HI_MULT
    if rr >= rr_config.GREEN_RR:
        return 1.0
    return 0.5 if rr >= min_rr else 0.0


def _order(o: Dict[str, Any], kind: str, entry: float, risk_dollars: float, budget: float,
           total: float, held_w: float, min_rr: float, mult: float = 1.0) -> Dict[str, Any]:
    stop = o.get("tactical_stop")
    tgt = o.get("target_1")
    sym = o["ticker"]
    base = {"ticker": sym, "type": kind, "entry": round(entry, 2), "stop": stop, "target": tgt}
    if not (stop and tgt and entry > stop and tgt > entry):
        return {**base, "veto": "levels invalid"}
    rr = (tgt - entry) / (entry - stop)
    base["rr"] = round(rr, 2)
    band = rr_size_mult(rr, min_rr)
    if band == 0.0:
        return {**base, "veto": f"R:R {rr:.2f} < {min_rr:.2f} floor"}
    base["size_mult"] = band
    if held_w >= HELD_CAP_PCT:
        return {**base, "veto": f"already {held_w*100:.0f}% of portfolio (cap {HELD_CAP_PCT*100:.0f}%)"}
    per_share = entry - stop
    cap_dollars = max(0.0, min(total * NAME_CAP_PCT * mult, total * HELD_CAP_PCT - held_w * total, budget))
    shares = math.floor(min(risk_dollars * band / per_share, cap_dollars / entry))
    if shares < 1:
        return {**base, "veto": "budget too small for 1 share"}
    notional = shares * entry
    return {**base, "shares": shares, "notional": round(notional, 2), "risk": round(shares * per_share, 2),
            "weight_after_pct": round((held_w * total + notional) / total * 100, 1)}


def plan(regime: Dict[str, Any], opps: List[Dict[str, Any]], summary: Dict[str, Any],
         positions: List[Dict[str, Any]], min_rr: Optional[float] = None,
         blocked: Optional[Dict[str, str]] = None, days_to_earnings=None) -> Dict[str, Any]:
    min_rr = rr_config.min_rr() if min_rr is None else min_rr
    total = float(summary.get("total_liquidation_value") or 0.0)
    dp = dry_powder(summary, positions)
    tide = regime.get("tide") or {}
    bullish = bool(tide.get("is_bullish"))
    mult = 1.0 if bullish else 0.5
    now_pct, limit_pct = (0.33, 0.33) if bullish else (0.25, 0.25)
    weights = held_weights(positions, total)
    risk_dollars = total * RISK_PCT * mult

    t_now = {"label": "Now", "pct": now_pct, "budget": round(dp["total"] * now_pct, 2), "orders": []}
    t_lim = {"label": "Limits at zone tops", "pct": limit_pct, "budget": round(dp["total"] * limit_pct, 2), "orders": []}
    left_now, left_lim = t_now["budget"], t_lim["budget"]
    vetoes: List[Dict[str, Any]] = []

    for o in opps or []:
        sym = o.get("ticker")
        if not sym:
            continue
        fails = set(o.get("gate_reasons") or [])
        why = (blocked or {}).get(sym)
        if why is None and days_to_earnings is not None:
            d = days_to_earnings(sym)
            if d is not None and d <= EARNINGS_BLACKOUT_DAYS:
                why = f"earnings in {d}d"
        if why and (o.get("is_actionable") or (fails and fails <= ONLY_ZONE_FAIL)):
            vetoes.append({"ticker": sym, "veto": why})
            continue
        if o.get("is_actionable") and o.get("opportunity_state") == "TAKE_NOW":
            entry = float(o.get("spot_price") or o.get("entry_high") or 0.0)
            od = _order(o, "buy now", entry, risk_dollars, left_now, total, weights.get(_root(sym), 0.0), min_rr, mult)
            if od.get("veto"):
                vetoes.append(od)
            else:
                t_now["orders"].append(od)
                left_now -= od["notional"]
        elif fails and fails <= ONLY_ZONE_FAIL and o.get("entry_high"):
            od = _order(o, "limit buy", float(o["entry_high"]), risk_dollars, left_lim, total,
                        weights.get(_root(sym), 0.0), min_rr, mult)
            if od.get("veto"):
                vetoes.append(od)
            else:
                t_lim["orders"].append(od)
                left_lim -= od["notional"]

    committed = sum(x["notional"] for t in (t_now, t_lim) for x in t["orders"])
    reserve = round(dp["total"] - committed, 2)
    return {
        "context": {
            "vix": (regime.get("vix") or {}).get("price"),
            "fear_greed": (regime.get("fear_greed") or {}).get("score"),
            "fear_greed_rating": (regime.get("fear_greed") or {}).get("rating"),
            "tide": tide.get("trend"),
            "tide_bullish": bullish,
            "breadth": regime.get("breadth"),
            "vix_band": vix_pace((regime.get("vix") or {}).get("price")),
            "unmeasured": ["vix", "fear_greed", "tide"],
        },
        "portfolio": {"total": round(total, 2), "dry_powder": dp,
                      "dry_powder_pct": round(dp["total"] / total * 100, 1) if total else None},
        "sizing": {"risk_pct_per_name": RISK_PCT * mult * 100, "name_cap_pct": NAME_CAP_PCT * mult * 100,
                   "held_cap_pct": HELD_CAP_PCT * 100, "size_multiplier": mult, "min_rr": min_rr},
        "tranches": [t_now, t_lim, {"label": "Reserve", "pct": round(reserve / dp["total"], 3) if dp["total"] else 0,
                                    "budget": reserve, "orders": []}],
        "vetoes": vetoes,
        "income_candidates": sorted((k for k, v in weights.items() if v >= HELD_CAP_PCT), key=lambda k: -weights[k]),
        "concentration": sorted(({"ticker": k, "weight_pct": round(v * 100, 1)} for k, v in weights.items() if v >= 0.05),
                                key=lambda x: -x["weight_pct"]),
        "note": "Names and sizing come from the measured R:R gate. VIX, Fear & Greed and the tide are unmeasured context: they only halve size, never add conviction.",
    }


def build_deploy_plan() -> Dict[str, Any]:
    from src.tracking.schwab_portfolio_manager import get_portfolio_positions, get_portfolio_summary
    from src.ui.services import today_service
    from src.ui.services.action_engine import _days_to_earnings
    from src.ui.services.opportunity_service import collect_active_deep_research_opportunities, get_market_regime

    blocked: Dict[str, str] = {}
    try:
        today = today_service.get_today_payload() or {}
        for sec in ("closest_to_gate", "unmeasured", "needs_you"):
            for r in today.get(sec) or []:
                for reason in r.get("gate_reasons") or []:
                    if any(m in str(reason) for m in BLOCK_MARKERS):
                        blocked[str(r.get("ticker") or "").upper()] = str(reason)
    except Exception:
        pass
    return plan(get_market_regime() or {}, collect_active_deep_research_opportunities(3) or [],
                get_portfolio_summary() or {}, get_portfolio_positions() or [],
                blocked=blocked, days_to_earnings=_days_to_earnings)
