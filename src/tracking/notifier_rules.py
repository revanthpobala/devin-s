"""Pure rules the notifier applies to positions and setups. No I/O, so each rule is unit-tested."""

from __future__ import annotations

import re
from datetime import date, timedelta

from src.tracking import rr_config
from typing import Any, Dict, Iterable, List, Optional

NEAR_STOP_FRACTION = 0.25  # within 25% of the entry-to-stop distance


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def position_events(ticker: str, rec: Dict[str, Any], price: Optional[float]) -> List[Dict[str, Any]]:
    """Events for one open position. `key` is stable per position (ticker + opened_at) and per level."""
    ticker = ticker.upper()
    side = str(rec.get("side") or "LONG").upper()
    long = "LONG" in side
    entry, stop, target = _num(rec.get("entry_price")), _num(rec.get("stop")), _num(rec.get("target"))
    ident = f"{ticker}:{rec.get('opened_at') or entry}"
    out: List[Dict[str, Any]] = []

    if stop is None or target is None:
        missing = " and ".join(n for n, v in (("stop", stop), ("target", target)) if v is None)
        out.append({"kind": "NO_LEVELS", "severity": "WARN", "ticker": ticker, "key": f"NO_LEVELS:{ident}",
                    "title": f"{ticker}: position has no {missing}",
                    "body": "It cannot be checked for stop breach or target hit. Set levels on the Trades page."})
    if price is None or stop is None:
        return out

    breached = price <= stop if long else price >= stop
    if breached:
        out.append({"kind": "STOP_BREACH", "severity": "CRITICAL", "ticker": ticker, "key": f"STOP_BREACH:{ident}",
                    "title": f"{ticker}: stop breached at {price:.2f} (stop {stop:.2f})",
                    "body": f"{side} entry {entry}. Price is through the plan stop. Exit or accept the loss deliberately."})
    elif entry is not None:
        risk = (entry - stop) if long else (stop - entry)
        room = (price - stop) if long else (stop - price)
        if risk > 0 and room / risk <= NEAR_STOP_FRACTION:
            out.append({"kind": "NEAR_STOP", "severity": "WARN", "ticker": ticker, "key": f"NEAR_STOP:{ident}",
                        "title": f"{ticker}: {price:.2f} is within {int(NEAR_STOP_FRACTION*100)}% of risk from stop {stop:.2f}",
                        "body": f"{side} entry {entry}, risk {risk:.2f}, room left {room:.2f}."})

    if target is not None and (price >= target if long else price <= target):
        out.append({"kind": "TARGET_HIT", "severity": "INFO", "ticker": ticker, "key": f"TARGET_HIT:{ident}",
                    "title": f"{ticker}: target {target:.2f} reached at {price:.2f}",
                    "body": "Plan target hit. Take profit or trail the stop on purpose."})
    return out


def closed_positions(previous_open: List[str], current_open: List[str]) -> List[str]:
    return sorted(set(previous_open) - set(current_open))


def setup_events(opps: Iterable[Dict[str, Any]], date: str,
                 earnings_days: Optional[Dict[str, Optional[int]]] = None) -> List[Dict[str, Any]]:
    """TAKE_NOW only: in zone and the actionable gate passes. Earnings within 7 days blocks the entry notice
    (event risk is unmeasured here, so it is a policy block, not a model output)."""
    out = []
    for o in opps:
        if o.get("opportunity_state") != "TAKE_NOW":
            continue
        t = str(o.get("ticker") or "").upper()
        d = (earnings_days or {}).get(t)
        if d is not None and 0 <= d <= 7:
            out.append({"kind": "EARNINGS_BLOCK", "severity": "INFO", "ticker": t, "key": f"TAKE_NOW_EARN:{t}:{date}",
                        "title": f"{t}: gate passed but earnings in {d} days, entry blocked",
                        "body": "Wait for the report. Event risk dominates a 21-day window."})
            continue
        note = "" if d is not None else " Earnings date unknown: check before entering."
        out.append({"kind": "TAKE_NOW", "severity": "WARN", "ticker": t, "key": f"TAKE_NOW:{t}:{date}", "push": "ENTRY",
                    "title": f"BUY {t}: in zone and gate passed",
                    "body": f"Entry {o.get('entry_low')}-{o.get('entry_high')}, stop {o.get('tactical_stop')}, "
                            f"T1 {o.get('target_1')}, R:R at market {o.get('live_rr')}.{note}"})
    return out


def digest_event(date: str, opps: Iterable[Dict[str, Any]], risk: Dict[str, Any], backend_alive: bool) -> Optional[Dict[str, Any]]:
    if not risk or risk.get("loading") or "book" not in risk:
        return None
    states = [o.get("opportunity_state") for o in opps]
    take, armed = states.count("TAKE_NOW"), states.count("ARMED")
    dial = (risk.get("regime_dial") or {}).get("multiplier")
    lines = [f"Take now {take}, armed {armed}. Size dial {dial}x ({(risk.get('regime_dial') or {}).get('why')}).",
             f"Open risk {risk.get('open_risk_pct')}% of {risk.get('risk_cap_pct')}% cap, {risk.get('tracked')}/{risk.get('position_cap')} positions, "
             f"beta-weighted exposure {risk.get('beta_weighted_pct')}% of book.",
             f"Research backend {'running' if backend_alive else 'NOT running'}."]
    lines += [f"Limit: {f}" for f in risk.get("flags", [])]
    return {"kind": "DIGEST", "severity": "WARN" if risk.get("flags") else "INFO", "ticker": None, "key": f"DIGEST:{date}",
            "title": f"Morning digest: {take} take-now, {armed} armed, risk {risk.get('open_risk_pct')}% of book",
            "body": " ".join(lines)}


def trust_event(report: Dict[str, Any], date: str) -> Optional[Dict[str, Any]]:
    g = report.get("gated") or {}
    if not g.get("n"):
        return None
    t = report.get("taken") or {}
    taken = f"{t['n']} scored" if t.get("n") else f"none scored ({report.get('taken_total', 0)} taken)"
    return {"kind": "TRUST", "severity": "INFO", "ticker": None, "key": f"TRUST:{date}",
            "title": f"Trust: {g['n']} scored, win {g['win_rate']}%, mean {g['mean_r']}R. {g['verdict']}",
            "body": (f"Median {g['median_r']}R. Mean without top 2 winners {g['mean_ex_top2']}R. "
                     f"Mean interval {g['mean_ci'][0]} to {g['mean_ci'][1]}R. Pending {report.get('pending_gated', 0)}. "
                     f"Your taken trades: {taken}.")}


WATCH_TYPES = {
    "ENTRY_ACTIONABLE_BUY": ("WARN", "entry gate passed, act now"),
    "ENTRY_TRIGGERED": ("WARN", "entry triggered"),
    "BREAKOUT_ENTERED": ("WARN", "broke its trigger"),
    "LEVEL_BREAKOUT": ("WARN", "broke a level"),
    "LEVEL_HIGH_20D": ("INFO", "new 20-day high"),
    "TESTING_SUPPORT": ("INFO", "testing support"),
    "TARGET_1_REACHED": ("INFO", "target 1 reached"),
    "TARGET_2_REACHED": ("INFO", "target 2 reached"),
    "LEVEL_T2": ("INFO", "target 2 level hit"),
    "TARGET_HIT_NO_FILL": ("INFO", "target hit before any entry"),
    "STOP_BREACHED": ("WARN", "stop breached, plan is dead"),
    "LEVEL_STOP": ("WARN", "stop level hit"),
    "ZONE_TOUCH": ("WARN", "touching a tracked entry zone"),
    "ZONE_APPROACH": ("INFO", "approaching a tracked entry zone"),
    "ZONE_REBOUND": ("INFO", "rebounded off a tracked zone"),
    "SCENARIO_ARRIVAL": ("WARN", "reached a scenario trigger"),
}


GROUP_OVER = 3
STOP_SEVERITY = {"TESTING_SUPPORT": 1, "LEVEL_STOP": 2, "STOP_BREACHED": 3}

# What the reader should do. Only a zone touch with R:R at market >= the floor (1.5 yellow, 2.0 green) is the measured edge;
# level breaks carry no measured edge, so they never say ACT.
VERDICT = {
    "ENTRY_ACTIONABLE_BUY": ("ACT", "gate passed: place the entry"),
    "ENTRY_TRIGGERED": ("CHECK", "trigger hit: confirm R:R at market is 2.0+ before buying"),
    "ZONE_TOUCH": ("ACT", "in the zone with R:R at market over the floor: buy limit here, stop below the zone"),
    "ZONE_APPROACH": ("WAIT", "set a buy limit at the zone top, do not chase"),
    "ZONE_REBOUND": ("SKIP", "entry was missed: do not chase, re-check R:R"),
    "BREAKOUT_ENTERED": ("CHECK", "no measured edge on breaks: act only if you armed this level"),
    "LEVEL_BREAKOUT": ("CHECK", "no measured edge on breaks: act only if you armed this level"),
    "LEVEL_HIGH_20D": ("IGNORE", "info only, no measured edge"),
    "TESTING_SUPPORT": ("WAIT", "needs a hold, then re-check R:R"),
    "STOP_BREACHED": ("EXIT", "plan is dead: cancel the entry, exit if you hold it"),
    "LEVEL_STOP": ("EXIT", "stop level hit: exit if you hold it"),
    "TARGET_1_REACHED": ("TAKE PROFIT", "if you hold it: sell the planned share, move the stop up"),
    "TARGET_2_REACHED": ("TAKE PROFIT", "if you hold it: close it"),
    "LEVEL_T2": ("TAKE PROFIT", "if you hold it: close it"),
    "TARGET_HIT_NO_FILL": ("SKIP", "target hit with no fill: plan is over"),
    "SCENARIO_ARRIVAL": ("CHECK", "scenario trigger reached: the arrival re-research decides; unmeasured"),
}


# No action follows from these, so they never notify.
SILENT = {"LEVEL_HIGH_20D", "ZONE_REBOUND", "TARGET_HIT_NO_FILL"}
# Only actionable for a holder. A stop on a name you do not hold means: cancel any resting buy.
HOLDER_ONLY = {"TESTING_SUPPORT", "LEVEL_STOP", "STOP_BREACHED", "TARGET_1_REACHED", "TARGET_2_REACHED", "LEVEL_T2"}
TOUCH_CHECK = "near the zone, not in it, or R:R at market under 2.0: unmeasured, buy only if you planned it"


def _touch_is_measured(message: str) -> bool:
    """In-zone and R:R at market >= 2.0 is the measured lane; a near-zone touch or a yellow R:R is not."""
    num = r"(\d+(?:\.\d+)?)"
    rr = re.search(r"R:R at market " + num, message or "")
    z = re.search(rf"zone \${num}-\${num}.*? at \${num}", message or "")
    in_zone = not z or float(z.group(1)) <= float(z.group(3)) <= float(z.group(2))
    return bool(rr) and float(rr.group(1)) >= rr_config.GREEN_RR and in_zone


def verdict_line(kind: str, message: str = "") -> str:
    v = VERDICT.get(kind)
    if not v:
        return ""
    label, do = v
    if kind == "ZONE_TOUCH" and not _touch_is_measured(message):
        label, do = "CHECK", TOUCH_CHECK
    m = re.search(r"R:R at market ([\d.]+)", message or "")
    t = rr_config.tier(float(m.group(1))) if m else None
    return f"{label}: {do}" + (f" (R:R {m.group(1)}{', ' + t if t else ''})" if m and kind.startswith("ZONE") else "")


def watch_alert_events(rows: Iterable[Dict[str, Any]], held: Optional[set] = None) -> List[Dict[str, Any]]:
    """Level and plan events from the watch loop. MOVING, THESIS_EXPIRED, MISSED_* and RUNNING_NO_ROOM are
    too repetitive to notify on; ZONE_TOUCH_LOW_RR stays in the watch log only. One notification per ticker, type and day,
    and one per ticker and day across the stop family (the most severe wins). With `held` known, stop and target
    events on names you do not hold are dropped, except a breached stop, which becomes a cancel notice."""
    rows = [r for r in rows if r.get("trigger_type") not in SILENT]
    if held is not None:
        rows = [r for r in rows if r.get("trigger_type") not in HOLDER_ONLY or r.get("trigger_type") == "STOP_BREACHED"
                or str(r.get("ticker") or "").upper() in held]
    worst = {}
    for r in rows:
        sev = STOP_SEVERITY.get(r.get("trigger_type"))
        k = (str(r.get("ticker") or "").upper(), r.get("date"))
        if sev and sev > STOP_SEVERITY.get((worst.get(k) or {}).get("trigger_type"), 0):
            worst[k] = r
    out, groups = [], {}
    for r in rows:
        meta = WATCH_TYPES.get(r.get("trigger_type"))
        if not meta:
            continue
        t = str(r.get("ticker") or "").upper()
        if r.get("trigger_type") in STOP_SEVERITY and worst[(t, r.get("date"))] is not r:
            continue
        spot = r.get("spot_price")
        msg = str(r.get("message") or "")
        line = verdict_line(r["trigger_type"], msg)
        label, sev = line.partition(":")[0], meta[0]
        ticket = msg.partition(" -> ")[2].strip()
        if r["trigger_type"] == "STOP_BREACHED" and held is not None and t not in held:
            label, sev, line = "CANCEL", "INFO", "CANCEL: plan is dead, cancel any resting buy order"
        elif label == "CHECK":
            sev = "INFO"
        ev = {"kind": r["trigger_type"], "severity": sev, "ticker": t,
              "key": f"WA:{r['trigger_type']}:{t}:{r.get('date')}",
              "title": f"{label} {t}: {meta[1]}".strip() + (f" @ ${float(spot):.2f}" if spot else ""),
              "body": "\n".join(x for x in (line, ticket if label == "ACT" else "", msg) if x)}
        if label == "ACT":
            ev["push"] = "ENTRY"
        elif label in ("EXIT", "TAKE PROFIT"):
            ev["push"] = "RISK"
        if r["trigger_type"] in STOP_SEVERITY:
            ev["group"], ev["rank"] = f"WA:STOP:{t}:{r.get('date')}", STOP_SEVERITY[r["trigger_type"]]
        out.append(ev)
        groups.setdefault(r["trigger_type"], []).append((ev, r))
    for kind, items in groups.items():
        if len(items) <= GROUP_OVER or items[0][0]["severity"] != "INFO":
            continue
        for ev, _ in items:
            out.remove(ev)
        out.append({"kind": kind, "severity": "INFO", "ticker": None, "key": f"WA:{kind}:GROUP:{items[0][1].get('id')}",
                    "title": f"{VERDICT.get(kind, ('',))[0]} {len(items)} names: {WATCH_TYPES[kind][1]}".strip() + (f" — {VERDICT[kind][1]}" if kind in VERDICT else ""),
                    "body": "\n".join(ev["body"] for ev, _ in items)})
    return out


def portfolio_events(positions: Iterable[Dict[str, Any]], total_day_pct: Optional[float], date: str,
                     total_value: float = 0.0) -> List[Dict[str, Any]]:
    """Your holdings in trouble. positions: symbol, underlying, asset_type, market_value, day_pct (live if known).
    Equities: material (>= $2,000 or >= 1.5% of the book) and down 5% WARN / 8% CRITICAL on the day.
    Options: contract down 35% WARN / 50% CRITICAL on material money. Each severity notifies once per day."""
    out = []
    if total_day_pct is not None and total_day_pct <= -3.0:
        sev = "CRITICAL" if total_day_pct <= -5.0 else "WARN"
        out.append({"kind": "PORTFOLIO_DROP", "severity": sev, "ticker": None, "key": f"PF:{date}:{sev}",
                    "title": f"Portfolio {total_day_pct:+.1f}% today", "body": "Whole-account drawdown for the session."})
    for p in positions:
        mv = float(p.get("market_value") or 0.0)
        pct = p.get("day_pct")
        if pct is None:
            continue
        material = p.get("asset_type") == "SPREAD" or mv >= 2000.0 or (total_value > 0 and mv / total_value >= 0.015)
        if not material:
            continue
        sym, und = p.get("symbol"), p.get("underlying") or p.get("symbol")
        if p.get("asset_type") == "SPREAD":
            if pct <= -35.0:
                sev = "CRITICAL" if pct <= -50.0 else "WARN"
                out.append({"kind": "HOLDING_DROP", "severity": sev, "ticker": und, "key": f"HD:{sym}:{date}:{sev}",
                            "title": f"EXIT? {und}: spread down {abs(pct):.0f}% of its width today",
                            "body": f"{sym}: net loss on both legs is {abs(pct):.0f}% of the spread width (the most it can lose, before the credit or debit). Decide: close it or let it expire."})
        elif p.get("asset_type") == "OPTION":
            if pct <= -35.0:
                sev = "CRITICAL" if pct <= -50.0 else "WARN"
                out.append({"kind": "HOLDING_DROP", "severity": sev, "ticker": und, "key": f"HD:{sym}:{date}:{sev}",
                            "title": f"{und}: option {sym} {pct:+.0f}% today", "body": f"Market value ${mv:,.0f}."})
        elif pct <= -5.0:
            sev = "CRITICAL" if pct <= -8.0 else "WARN"
            out.append({"kind": "HOLDING_DROP", "severity": sev, "ticker": sym, "key": f"HD:{sym}:{date}:{sev}",
                        "title": f"{sym}: {pct:+.1f}% today", "body": f"Position worth ${mv:,.0f}. Decide: hold, trim or exit."})
    return out


def rsi2_events(rows: Iterable[Dict[str, Any]], now_et: Any) -> List[Dict[str, Any]]:
    """One order per RSI2 ENTER signal, live until the next session's open, the only time it can be taken."""
    from src.tracking.rsi2_scan import entry_open_passed, MAX_HOLD_BARS, next_open_day

    out = []
    for r in rows:
        t, day = str(r.get("ticker") or "").upper(), str(r.get("date") or "")[:10]
        ceil, stop, target = _num(r.get("entry_high")), _num(r.get("stop")), _num(r.get("target_1"))
        if not (t and day and ceil and stop and target and ceil > stop) or entry_open_passed(day, now_et):
            continue
        opens = next_open_day(day)
        win, ev_r = r.get("lane_prior_win"), r.get("lane_prior_ev")
        last, n = date.fromisoformat(str(opens)), MAX_HOLD_BARS
        while n > 1:
            last += timedelta(days=1)
            n -= last.weekday() < 5
        er = str(r.get("next_earnings") or "")[:10]
        er_in = bool(er) and str(opens) <= er <= str(last)
        out.append({
            "kind": "ORDER", "severity": "WARN", "ticker": t, "key": f"RSI2:{t}:{day}", "push": "ENTRY",
            "title": ("EARNINGS IN HOLD · " if er_in else "") + f"BUY {t} at the {opens} open, only if it opens at or below ${ceil:.2f}",
            "body": "\n".join(x for x in (
                f"{t} BUY 100 MARKET-ON-OPEN {opens}. Skip if the open is above {ceil:.2f} or at/below the stop.",
                f"Stop {stop:.2f} · target {target:.2f} · risk at the ceiling ${100 * (ceil - stop):,.0f}",
                f"Exit at the next open after a daily close above EMA5, or at the open after {MAX_HOLD_BARS} bars.",
                f"Earnings {er} falls inside the {MAX_HOLD_BARS}-bar hold (ends {last}): a gap can jump the stop. Skip or size down." if er_in else "",
                f"RSI2 lane [MEASURED]: {win:.0f}% win, {ev_r:+.3f}R per trade in this QQQ band. A base rate, not a forecast."
                if win is not None and ev_r is not None else "",
            ) if x)})
    return out
