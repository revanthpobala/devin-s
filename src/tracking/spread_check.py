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


def open_verticals(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every open vertical, with payoff figures from the entry prices (average_price) of the two legs."""
    groups: Dict[tuple, Dict[float, Dict[str, float]]] = {}
    for p in positions:
        if p.get("asset_type") != "OPTION" or not p.get("quantity") or p.get("option_strike") is None or not p.get("option_type"):
            continue
        try:
            exp = datetime.strptime(str(p.get("option_expiration") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        key = ((p.get("underlying_symbol") or p.get("symbol") or "").upper(), exp, str(p["option_type"]).upper())
        leg = groups.setdefault(key, {}).setdefault(float(p["option_strike"]), {"qty": 0.0, "cost": 0.0, "mark": 0.0})
        q = float(p["quantity"])
        leg["qty"] += q
        leg["cost"] += q * float(p.get("average_price") or 0.0)
        leg["mark"] += q * float(p.get("current_price") or 0.0)
    out = []
    for (und, exp, typ), legs in groups.items():
        longs = {k: v for k, v in legs.items() if v["qty"] > 0}
        shorts = {k: v for k, v in legs.items() if v["qty"] < 0}
        if len(longs) != 1 or len(shorts) != 1:
            continue
        (lk, lg), (sk, sh) = next(iter(longs.items())), next(iter(shorts.items()))
        if lg["qty"] != -sh["qty"]:
            continue
        qty = lg["qty"]
        net = (lg["cost"] + sh["cost"]) / qty
        mark = (lg["mark"] + sh["mark"]) / qty
        width = abs(lk - sk)
        is_debit = (lk < sk) if typ == "CALL" else (lk > sk)
        if net >= 0 and is_debit:
            max_loss, max_profit = net, width - net
            be = lk + net if typ == "CALL" else lk - net
        elif net < 0 and not is_debit:
            max_loss, max_profit = width + net, -net
            be = sk - net if typ == "CALL" else sk + net
        else:
            continue
        out.append({"underlying": und, "expiry": exp.isoformat(), "type": typ, "long_strike": lk, "short_strike": sk, "qty": int(qty),
                    "kind": "debit" if is_debit else "credit", "net_per_share": round(abs(net), 2), "width": width,
                    "max_loss": round(max_loss * MULTIPLIER * qty, 2), "max_profit": round(max_profit * MULTIPLIER * qty, 2),
                    "breakeven": round(be, 2), "mark_value": round(mark * MULTIPLIER * qty, 2),
                    "skew": round(max_loss / max_profit, 1) if max_profit > 0 else None})
    return out


def vertical_context(positions: List[Dict[str, Any]]) -> str:
    rows = open_verticals(positions)
    if not rows:
        return ""
    lines = "\n".join(
        f"- {v['underlying']} {v['expiry']} {v['long_strike']:g}/{v['short_strike']:g} {v['type'].lower()} {v['kind']} spread x{v['qty']}: "
        f"{v['kind']} ${v['net_per_share']:,.2f}/share, width ${v['width']:g}, max loss ${v['max_loss']:,.2f}, "
        f"max profit ${v['max_profit']:,.2f}, breakeven {v['breakeven']:g}, current mark value ${v['mark_value']:,.2f}"
        + (f". RISK FLAG: risks ${v['max_loss']:,.0f} to make ${v['max_profit']:,.0f}" + (f" ({v['skew']}:1)" if v["skew"] else "") + "; needs a near-certain win to pay"
           if v["skew"] is None or v["skew"] >= RATIO_FLAG else "")
        for v in rows)
    return ("[OPEN VERTICAL SPREADS, computed from the live Schwab legs and entry prices]\n" + lines +
            "\nQuote max loss, max profit and breakeven exactly as given. Never recompute them. A debit spread's max loss is the debit paid; its max profit is width minus debit, times 100 times quantity.")
