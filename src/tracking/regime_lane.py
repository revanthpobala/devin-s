"""Descriptive desk context: today's advancing share, its historical quintile, the measured lane expectancy there,
and index insurance (never alpha) on event days. Nothing here predicts direction or changes a gate.

Measured (data-windows/scripts/breadth_quintile_lane.py, full_v4_av2): ZONE_RR R_rel is positive in every
advancing-share quintile (+0.08 to +0.14R) with no ordering, so same-day breadth does not switch the lane on or off.
The earlier -0.2R / +0.7R spread is by FORWARD universe return, which is unknowable live.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src import config

LABELS = ["Q1 narrow", "Q2", "Q3", "Q4", "Q5 broad"]


def _table() -> Dict[str, Any]:
    p = config.BASE_DIR / "data" / "regime_breadth_lane.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def bucket(adv_share: float, edges: List[float]) -> str:
    return LABELS[sum(adv_share > e for e in edges)]


def lane_context(regime: Optional[Dict[str, Any]], table: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    table = table if table is not None else _table()
    b = (regime or {}).get("breadth") or {}
    n = (b.get("advancing") or 0) + (b.get("declining") or 0) + (b.get("flat") or 0)
    if not table or not n:
        return {"available": False}
    share = b["advancing"] / n
    q = bucket(share, table["edges"])
    lanes = {k: v.get(q) for k, v in table["lanes"].items()}
    return {"available": True, "advancing_pct": round(100 * share, 1), "quintile": q, "edges_pct": [round(100 * e, 1) for e in table["edges"]],
            "lanes": lanes,
            "note": "Descriptive. Zone-long R:R expectancy is positive in every breadth quintile with no ordering, so today's breadth is not a reason to resize or skip; "
                    "the forward-universe spread (-0.2R to +0.7R) cannot be known live."}


def index_protection(snapshot: Optional[Dict[str, Any]], releases: List[Dict[str, Any]], opex: Optional[Dict[str, Any]],
                     spy_px: Optional[float]) -> Optional[Dict[str, Any]]:
    """Insurance sized to the book's dollar beta, only when an event can hit the open book. Cost is unmeasured."""
    hedge = (snapshot or {}).get("hedge")
    if not hedge:
        return None
    events = [f"{r['when']}: " + ", ".join(sorted({x['title'] for x in r['releases']})) for r in releases if r.get("exposed")]
    if opex and opex.get("legs"):
        events.append(f"opex {opex['date']} with {len(opex['legs'])} open leg(s) expiring")
    if not events:
        return None
    notional = hedge["spy_notional_for_25pct_cut"]
    contracts = int(notional // (spy_px * 100)) if spy_px else None
    return {"events": events, "beta_dollars": hedge["beta_dollars"], "notional": notional, "spy_px": spy_px, "contracts": contracts,
            "idea": ("SPY put spread or collar" + (f" about {contracts} contract(s)" if contracts else "") +
                     f" to offset 25% of ${hedge['beta_dollars']:,} beta dollars (${notional:,} notional)."),
            "note": "Insurance, not alpha: premium cost is unmeasured. Single-name shorts have no edge. "
                    "Extended holdings (Ext Z >= 2) get covered-call ideas from the income digest."}
