"""Payoff check for open vertical option spreads.

A vertical near expiry can sit almost at max value: the remaining upside is a few dollars while the remaining
loss is the whole mark. This module only does that arithmetic from the synced Schwab legs. The 5x threshold is a
policy line, not a measured edge.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List

MULTIPLIER = 100.0
RATIO_FLAG = 5.0
DAYS_AHEAD = 7


def _legs(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for p in positions:
        if p.get("asset_type") != "OPTION" or not p.get("quantity") or p.get("option_strike") is None or not p.get("option_type"):
            continue
        try:
            exp = datetime.strptime(str(p.get("option_expiration") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append({"und": (p.get("underlying_symbol") or p.get("symbol") or "").upper(), "exp": exp, "type": str(p["option_type"]).upper(),
                    "strike": float(p["option_strike"]), "qty": float(p["quantity"]), "px": float(p.get("current_price") or 0.0)})
    return out


def spread_flags(positions: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for leg in _legs(positions):
        if today <= leg["exp"] <= today + timedelta(days=DAYS_AHEAD):
            groups.setdefault((leg["und"], leg["exp"], leg["type"]), []).append(leg)
    out = []
    for (und, exp, typ), legs in groups.items():
        longs = [leg for leg in legs if leg["qty"] > 0]
        shorts = [leg for leg in legs if leg["qty"] < 0]
        if len(longs) != 1 or len(shorts) != 1 or abs(longs[0]["qty"]) != abs(shorts[0]["qty"]):
            continue
        lg, sh = longs[0], shorts[0]
        qty = abs(lg["qty"])
        width = abs(lg["strike"] - sh["strike"]) * MULTIPLIER * qty
        value = abs(lg["px"] - sh["px"]) * MULTIPLIER * qty
        debit = (lg["strike"] < sh["strike"]) if typ == "CALL" else (lg["strike"] > sh["strike"])
        upside, downside = (width - value, value) if debit else (value, width - value)
        if downside <= 0 or (upside > 0 and downside / upside < RATIO_FLAG):
            continue
        out.append({"underlying": und, "expiry": exp.isoformat(), "type": typ, "long_strike": lg["strike"], "short_strike": sh["strike"],
                    "qty": int(qty), "upside": round(max(upside, 0.0), 2), "downside": round(downside, 2),
                    "ratio": round(downside / upside, 1) if upside > 0 else None})
    return out


def spread_events(flags: List[Dict[str, Any]], today: str) -> List[Dict[str, Any]]:
    out = []
    for f in flags:
        ks = f"{f['long_strike']:g}/{f['short_strike']:g}"
        out.append({"kind": "SPREAD_RISK", "severity": "WARN", "ticker": None,
                    "key": f"SPREAD:{f['underlying']}:{f['expiry']}:{ks}:{today}",
                    "title": f"{f['underlying']} {f['expiry']} {ks} {f['type'].lower()} spread: risk ${f['downside']:,.0f} to make ${f['upside']:,.0f}",
                    "body": "Remaining loss is far larger than remaining gain. Closing it takes the risk off for the price of the leftover gain."})
    return out


def context_text(flags: List[Dict[str, Any]]) -> str:
    """Prompt block with the computed numbers, so the model quotes them instead of redoing the arithmetic."""
    if not flags:
        return ""
    rows = "\n".join(
        f"- {f['underlying']} {f['expiry']} {f['long_strike']:g}/{f['short_strike']:g} {f['type'].lower()} spread x{f['qty']}: "
        f"remaining loss ${f['downside']:,.0f}, remaining gain ${f['upside']:,.0f}" + (f" ({f['ratio']}:1)." if f["ratio"] else ".")
        for f in flags)
    return ("[FLAGGED OPTION SPREADS, computed from the live Schwab legs]\n" + rows +
            "\nQuote these figures as given. Remaining loss and gain are measured from the current mark, not from the entry price.")
