"""Layered entry: scale into a research plan instead of one resting limit that may never fill.

Measured Oct 28 2026 on 203 deep-research plans (Aug-Oct 2026, daily bars, plan stop and T1,
stop first on a shared bar; script: /tmp/rev/dr layers replay, numbers in AGENTS.md):

| R:R at market | all at market | one limit at zone top | 1/3 market + 1/3 zone top + 1/3 zone low |
|---|---|---|---|
| >= 1.5 (n=67)  | +0.23R mean / -0.02 median | +0.39R / +0.00, 15% never fill | +0.41R / +0.15, always a fill |
| <  1.5 (n=136) | -0.14R | -0.08R | -0.09R (the market starter does not help) |

So: a market STARTER only when R:R at market >= LAYER_RR_MIN and no earnings inside the block
window; otherwise limits only (zone top, mid, zone low). Total share count is sized so the risk
if EVERY tranche fills equals the risk budget, so averaging down never adds risk beyond the plan.
Unfilled tranches cancel on a daily close below the stop or when T1 prints. n=67 is small: the
lane is UNMEASURED and forward-logged as LAYERED_ENTRY.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

LAYER_RR_MIN = 1.5
STARTER_CAP_ATR = 0.10  # marketable-limit cap above spot for the starter
MAX_TOTAL_SHARES = 500


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def build_layers(
    spot: Any,
    zone_lo: Any,
    zone_hi: Any,
    stop: Any,
    t1: Any,
    atr: Any,
    risk_budget_usd: float,
    earnings_days: Optional[int] = None,
    earnings_block_days: int = 7,
    rr_min: float = LAYER_RR_MIN,
    implied_move_pct: Optional[float] = None,
    equity_usd: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Tranches for a long plan, or None when no valid layering exists (never invents levels).

    Share count = risk_budget / (per-tranche risk if all fill), then capped by account guardrails:
    position notional (<= MAX_POSITION_NOTIONAL_PCT of equity) and, when earnings fall in the window,
    an earnings-gap cap so a 2x-implied-move gap loses at most MAX_EARNINGS_GAP_LOSS_USD. The binding
    constraint is recorded in `size_cap`.
    """
    from src.tracking import trade_rules as _tr

    spot, lo, hi, stop, t1, atr = (_f(x) for x in (spot, zone_lo, zone_hi, stop, t1, atr))
    if min(spot, lo, hi, stop, t1) <= 0 or not (stop < lo <= hi < t1):
        return None
    if not (stop < spot < t1):
        return None
    rr_mkt = (t1 - spot) / (spot - stop)
    earnings_blocked = earnings_days is not None and 0 <= int(earnings_days) <= earnings_block_days

    starter = rr_mkt >= rr_min and not earnings_blocked
    if starter:
        limits = [p for p in (hi, lo) if p < spot - 0.005]
    else:
        limits = [p for p in (hi, (hi + lo) / 2.0, lo) if p < spot - 0.005]
    limits = sorted({round(p, 2) for p in limits}, reverse=True)

    prices: List[tuple] = []
    if starter:
        prices.append(("MARKET", round(spot, 2)))
    prices += [("LIMIT", p) for p in limits]
    if not prices:
        return None

    risk_sum = sum(p - stop for _, p in prices)
    each = int(float(risk_budget_usd) / risk_sum) if risk_sum > 0 else 0
    each = max(1, min(each, MAX_TOTAL_SHARES // len(prices)))

    # Account guardrails cap TOTAL shares, split evenly across tranches. Notional uses the top
    # (most expensive) fill; the gap cap binds only when earnings fall inside the holding window.
    n = len(prices)
    size_cap = None
    notional_total = _tr.max_shares_by_notional(max(p for _, p in prices), equity_usd)
    gap_total = _tr.max_shares_by_earnings_gap(spot, implied_move_pct, earnings_days)
    total_cap = notional_total
    if gap_total is not None and gap_total < total_cap:
        total_cap, size_cap = gap_total, "earnings_gap"
    capped_each = max(0, total_cap // n)
    if capped_each < each:
        each = capped_each
        size_cap = size_cap or "notional"
    if each < 1:
        return {"mode": "NO_SIZE", "tranches": [], "total_shares": 0, "risk_usd_if_all_fill": 0.0,
                "avg_price_if_all_fill": None, "rr_at_market": round(rr_mkt, 2),
                "stop": round(stop, 2), "target_1": round(t1, 2), "size_cap": size_cap or "notional",
                "why": f"account caps allow 0 shares ({size_cap or 'notional'} cap at this price)",
                "cancel_rule": "n/a", "lane": "LAYERED_ENTRY", "label": "UNMEASURED"}

    tranches = []
    for i, (kind, p) in enumerate(prices, 1):
        tr = {
            "n": i,
            "type": kind,
            "price": p,
            "shares": each,
            "rr_at_fill": round((t1 - p) / (p - stop), 2),
        }
        if kind == "MARKET":
            tr["limit_cap"] = round(spot + STARTER_CAP_ATR * atr, 2) if atr > 0 else round(spot, 2)
        tranches.append(tr)

    total = each * len(tranches)
    avg = sum(t["price"] for t in tranches) / len(tranches)
    if starter:
        why = f"R:R at market {rr_mkt:.2f} >= {rr_min:g}: starter now, add on pullbacks to the zone"
    elif earnings_blocked:
        why = f"earnings in {earnings_days} trading days: no starter through the print, limits only"
    else:
        why = f"R:R at market {rr_mkt:.2f} < {rr_min:g}: no starter (measured no help), limits only"
    if size_cap == "earnings_gap":
        why += f"; capped to {total} shares so a 2x-implied-move earnings gap loses <= ${_tr.MAX_EARNINGS_GAP_LOSS_USD:.0f}"
    elif size_cap == "notional":
        why += f"; capped to {total} shares at {_tr.MAX_POSITION_NOTIONAL_PCT:.0%} of equity"
    return {
        "mode": "STARTER_PLUS_LIMITS" if starter else "LIMITS_ONLY",
        "rr_at_market": round(rr_mkt, 2),
        "size_cap": size_cap,
        "tranches": tranches,
        "total_shares": total,
        "avg_price_if_all_fill": round(avg, 2),
        "risk_usd_if_all_fill": round(sum(t["shares"] * (t["price"] - stop) for t in tranches), 2),
        "stop": round(stop, 2),
        "target_1": round(t1, 2),
        "cancel_rule": f"cancel unfilled tranches on a daily close below {stop:.2f} or when T1 {t1:.2f} prints",
        "why": why,
        "lane": "LAYERED_ENTRY",
        "label": "UNMEASURED",
    }


def cap_layers_to_position(
    lay: Optional[Dict[str, Any]], held: Optional[float], target: Optional[float]
) -> Optional[Dict[str, Any]]:
    """Held name with a target position: size the layers as the gap to the target, never past it.

    remaining = target - held (whole shares, rounded). <= 0 -> mode AT_TARGET, no tranches.
    Shares go to the highest-priced tranches first, so a small add fills first at the top layer.
    Held with no target: layers unchanged, the holding is stated.
    """
    if not lay:
        return lay
    if held is None or held <= 0:
        return lay
    lay = {**lay, "held_shares": round(float(held), 4)}
    if not target or target <= 0:
        lay["position_note"] = f"you hold {held:g} shares; no target position set"
        return lay
    gap = float(target) - float(held)
    remaining = int(round(gap))
    lay["target_shares"] = float(target)
    if remaining <= 0:
        return {**lay, "mode": "AT_TARGET", "tranches": [], "total_shares": 0, "risk_usd_if_all_fill": 0.0,
                "avg_price_if_all_fill": None,
                "why": f"at target: hold {held:g} of {target:g} shares, no adds",
                "position_note": f"at target {target:g}"}
    old = lay.get("tranches") or []
    k = min(len(old), remaining)
    base, extra = divmod(remaining, k)
    tranches = []
    for i, t in enumerate(old[:k]):
        tranches.append({**t, "shares": base + (1 if i < extra else 0)})
    stop = float(lay["stop"])
    total = sum(t["shares"] for t in tranches)
    avg = sum(t["shares"] * t["price"] for t in tranches) / total
    return {
        **lay,
        "tranches": tranches,
        "total_shares": total,
        "avg_price_if_all_fill": round(avg, 2),
        "risk_usd_if_all_fill": round(sum(t["shares"] * (t["price"] - stop) for t in tranches), 2),
        "why": f"{lay['why']}; sized to the {gap:.2f}-share gap to your {target:g}-share target (hold {held:g})",
        "position_note": f"add {total} to reach {target:g}",
    }


def layers_line(lay: Dict[str, Any]) -> str:
    """One line for the TLDR / desk card."""
    if lay.get("mode") in ("AT_TARGET", "NO_SIZE") or not lay.get("tranches"):
        return f"🪜 Layered entry: {lay['why']}."
    return f"🪜 Layered entry: {layers_text(lay)} — {lay['why']}."


def layers_text(lay: Dict[str, Any]) -> str:
    parts = []
    for t in lay["tranches"]:
        if t["type"] == "MARKET":
            parts.append(f"{t['shares']} now (cap {t['limit_cap']})")
        else:
            parts.append(f"{t['shares']} @ {t['price']}")
    return (f"LAYERED {lay['total_shares']}: " + " + ".join(parts)
            + f" | stop {lay['stop']} T1 {lay['target_1']} | risk if all fill ${lay['risk_usd_if_all_fill']:.0f}")


def layers_markdown(lay: Dict[str, Any], ticker: str, rejected: bool = False) -> str:
    head = "### 🪜 Layered entry" + (" (display only — level gate rejected this plan)" if rejected else "")
    if lay.get("mode") in ("AT_TARGET", "NO_SIZE") or not lay.get("tranches"):
        return f"{head}\n\n{ticker}: {lay['why']}."
    rows = "\n".join(
        f"| {t['n']} | {'BUY NOW (market, cap ' + str(t['limit_cap']) + ')' if t['type'] == 'MARKET' else 'limit ' + str(t['price'])} "
        f"| {t['shares']} | {t['rr_at_fill']} |"
        for t in lay["tranches"]
    )
    return (
        f"{head}\n\n{ticker}: {lay['why']}.\n\n"
        f"| # | Order | Shares | R:R at fill |\n|---|---|---|---|\n{rows}\n\n"
        f"Average if all fill **{lay['avg_price_if_all_fill']}**, risk if all fill **${lay['risk_usd_if_all_fill']:.0f}** "
        f"(stop {lay['stop']}, T1 {lay['target_1']}). {lay['cancel_rule'].capitalize()}. "
        f"Lane {lay['lane']} [{lay['label']}], forward-logged until n >= 200."
    )
