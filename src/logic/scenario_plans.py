"""Conditional scenario plans: price is at A today; if it reaches B inside the window, re-research, then trade.

The judge proposes 2-3 scenarios (PULLBACK into support, BREAKOUT over resistance, POST_EARNINGS re-entry).
Code owns the numbers: stop no tighter than STOP_ATR_MIN ATR below the trigger, R:R at the trigger >= GREEN_RR,
driftless-GBM touch odds and median days to touch (reflection principle, sigma from the 21-bar expected move),
and the 3-tier options menu repriced as if spot were at the trigger on the expected arrival day.

Every kept scenario rests in the paper ledger as SCENARIO_<KIND> (LIMIT for pullback, STOP for breakout), so
the conditional plans are measured whether or not anyone trades them. No measured edge until that ledger says so.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.tracking.trade_rules import GREEN_RR, STOP_ATR_MIN, T1_MAX_EXPECTED_MOVE_MULT

logger = logging.getLogger(__name__)

KINDS = ("PULLBACK", "BREAKOUT", "POST_EARNINGS")
WINDOW_SESSIONS = 21
HARD_VETOES = ("HARD_FUNDAMENTAL",)
# Corpus touch odds over 21 bars: a limit 2 ATR below the close fills 46%, 3 ATR 29%.
MAX_TRIGGER_ATR_BELOW_SPOT = 2.0


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _sessions(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _sessions_between(a: date, b: date) -> int:
    return sum(1 for i in range(1, (b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def daily_sigma(spot: float, em_pct: float = 0.0, atr: float = 0.0) -> float:
    if em_pct > 0:
        return em_pct / 100 / math.sqrt(21)
    return atr / spot / 1.25 if spot > 0 and atr > 0 else 0.0


def touch_odds(spot: float, trigger: float, sigma_d: float, sessions: int) -> Tuple[Optional[float], Optional[int]]:
    """(P(touch within sessions) %, median sessions to first touch) for driftless GBM; (None, None) without vol."""
    if spot <= 0 or trigger <= 0 or sigma_d <= 0 or sessions <= 0:
        return None, None
    d = abs(math.log(trigger / spot))
    if d == 0:
        return 100.0, 0
    p = 2 * (1 - 0.5 * (1 + math.erf(d / (sigma_d * math.sqrt(sessions)) / math.sqrt(2))))
    return round(100 * min(p, 1.0), 1), max(1, round((d / (sigma_d * 0.6745)) ** 2))


def _default_confirm(kind: str, stop: float, trigger: float) -> List[str]:
    base = [f"daily close holds above stop {stop:.2f}", "no new fundamental negative since this research"]
    if kind == "BREAKOUT":
        return [f"daily close above {trigger:.2f} on RVOL >= 1.0"] + base
    if kind == "POST_EARNINGS":
        return ["earnings reaction does not close below the stop", "guidance intact"] + base
    return ["pullback on volume below the 20-day average (no distribution)"] + base


def seed_from_plan(sp: Dict[str, Any], spot: float) -> List[Dict[str, Any]]:
    """Scenarios implied by the plan's own levels, used when the judge wrote none."""
    out = []
    hi, lo, stop, t1 = _f(sp.get("entry_zone_high")), _f(sp.get("entry_zone_low")), _f(sp.get("tactical_stop")), _f(sp.get("target_1"))
    if 0 < hi < spot and stop and t1:
        out.append({"kind": "PULLBACK", "trigger": hi, "zone_low": lo, "stop": stop, "target": t1, "origin": "seeded"})
    bo, bs = _f(sp.get("breakout_level")), _f(sp.get("breakout_stop")) or stop
    bt = _f(sp.get("breakout_target_1")) or (t1 if t1 > bo else 0)
    if bo > spot and bs and bt:
        out.append({"kind": "BREAKOUT", "trigger": bo, "stop": bs, "target": bt, "origin": "seeded"})
    return out


def _rr_floor() -> float:
    """The level gate judges a resting zone against the user's at-market floor; never below the measured 2.0."""
    try:
        from src.tracking.rr_config import min_rr

        return max(GREEN_RR, float(min_rr()))
    except Exception:
        return GREEN_RR


def normalize(s: Dict[str, Any], spot: float, atr: float, em_pct: float, today: date,
              earnings: Optional[str], pine_stop: float = 0.0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Same stop and target rules the level gate applies to judge plans, so an armed scenario survives it."""
    kind = str(s.get("kind") or "").upper().replace(" ", "_")
    trig, stop, tgt = _f(s.get("trigger")), _f(s.get("stop")), _f(s.get("target"))
    if kind not in KINDS:
        return None, f"unknown kind {kind!r}"
    if not (trig > 0 and tgt > trig and spot > 0 and atr > 0):
        return None, f"{kind}: needs trigger, target above it, spot and ATR"
    if kind == "PULLBACK" and trig >= spot:
        return None, f"PULLBACK trigger {trig} not below spot {spot}"
    if kind == "BREAKOUT" and trig <= spot:
        return None, f"BREAKOUT trigger {trig} not above spot {spot}"
    notes = []
    if em_pct > 0:
        t_cap = round(spot * (1 + T1_MAX_EXPECTED_MOVE_MULT * em_pct / 100), 2)
        if tgt > t_cap + 0.05:
            notes.append(f"target {tgt} clamped to {t_cap} ({T1_MAX_EXPECTED_MOVE_MULT:g}x the 21-bar expected move)")
            tgt = t_cap
            if tgt <= trig:
                return None, f"{kind}: expected-move cap {t_cap} leaves no room above the trigger {trig}"
    entry = "LIMIT" if trig < spot else "STOP"
    zlo = _f(s.get("zone_low"))
    if entry == "LIMIT":
        zlo = zlo if trig - 1.5 * atr < zlo < trig else round(trig - 0.25 * atr, 2)
        fill = zlo
    else:
        zlo, fill = trig, trig

    def _stop_cap(f: float) -> float:
        c = round(f - STOP_ATR_MIN * atr, 2)
        return round(pine_stop, 2) if 0 < pine_stop < c else c

    if not stop or stop > _stop_cap(fill):
        notes.append(f"stop {stop or 'missing'} widened to {_stop_cap(fill)} ({STOP_ATR_MIN:g} ATR below the fill or the Pine stop)")
        stop = _stop_cap(fill)
    floor = _rr_floor()
    if entry == "LIMIT" and stop < zlo and (tgt - trig) / (trig - stop) < floor:
        # The idea is fine, the price is not: the trade starts where R:R reaches the floor (the same repair the plan gate makes).
        old, t = trig, trig
        # Lowering the trigger lowers the stop cap, which lowers the stop, which lowers the trigger again: it converges
        # at a 2/3 rate, so iterate to the fixed point and keep a cent of margin against rounding below the floor.
        for _ in range(80):
            t = math.floor(((tgt + (floor + 0.01) * stop) / (1 + floor + 0.01)) * 100) / 100
            zl = round(t - 0.25 * atr, 2)
            if stop <= _stop_cap(zl):
                break
            stop = _stop_cap(zl)
        if (spot - t) / atr > MAX_TRIGGER_ATR_BELOW_SPOT or t <= stop or zl <= stop:
            return None, f"{kind} {old}: R:R floor {floor:g} needs a trigger at {t}, {(spot - t) / atr:.1f} ATR below spot; unlikely to fill"
        notes.append(f"trigger lowered {old} to {t}: R:R at the original trigger was below {floor:g}")
        trig, zlo = t, zl
    if stop >= zlo:
        return None, f"{kind}: stop {stop} not below the zone"
    rr = (tgt - trig) / (trig - stop)
    if rr < floor:
        return None, f"{kind} {trig}: R:R {rr:.2f} at the trigger below {floor:g}"
    not_before = None
    if kind == "POST_EARNINGS":
        if not earnings:
            return None, "POST_EARNINGS without an earnings date"
        ed = date.fromisoformat(earnings[:10])
        not_before = str(_sessions(ed, 1))
        end_default = _sessions(ed, WINDOW_SESSIONS)
    else:
        end_default = _sessions(today, WINDOW_SESSIONS)
    try:
        end = date.fromisoformat(str(s.get("window_end"))[:10])
        if end <= today:
            end = end_default
    except (TypeError, ValueError):
        end = end_default
    p, med = touch_odds(spot, trig, daily_sigma(spot, em_pct, atr), _sessions_between(today, end))
    out = {
        "kind": kind, "trigger": round(trig, 2), "zone_low": round(zlo, 2),
        "stop": stop, "target": round(tgt, 2), "rr_at_trigger": round(rr, 2), "window_end": str(end),
        "not_before": not_before, "p_touch_pct": p, "median_sessions_to_touch": med,
        "entry": entry,
        "confirm": [str(c) for c in (s.get("confirm") or [])][:4] or _default_confirm(kind, stop, trig),
        "thesis": str(s.get("thesis") or "")[:240], "notes": notes, "status": "ARMED",
        "origin": s.get("origin") or "judge",
    }
    return out, None


def _arrival_day(sc: Dict[str, Any], today: date) -> date:
    if sc.get("not_before"):
        return date.fromisoformat(sc["not_before"])
    end = date.fromisoformat(sc["window_end"])
    return min(_sessions(today, int(sc.get("median_sessions_to_touch") or 1)), end)


def price_options(ticker: str, scenarios: List[Dict[str, Any]], spot: float, em_pct: float,
                  earnings: Optional[str], today: date, chain=None) -> None:
    """Attach each scenario's 3-tier menu priced at its trigger on its expected arrival day. One chain fetch."""
    from src.logic.options_menu import build_options_menu, load_chain, shifted_chain

    try:
        chain = chain or load_chain(ticker)
    except Exception as e:
        logger.warning(f"[{ticker}] scenario options: chain unavailable: {e}")
        return
    for sc in scenarios:
        day = _arrival_day(sc, today)
        earn = earnings if earnings and str(day) <= earnings[:10] else None
        try:
            exps, fetch = shifted_chain(chain, spot, sc["trigger"], today, day)
            menu = build_options_menu(ticker, sc["trigger"], {"tactical_stop": sc["stop"], "target_1": sc["target"]},
                                      em_pct, earn, today=day, chain=(exps, fetch, f"{chain[2] if len(chain) > 2 else 'chain'} repriced"),
                                      tactical_lo=21)
            menu["priced_at"] = {"spot": sc["trigger"], "on": str(day)}
            sc["options"] = menu
        except Exception as e:
            sc["options"] = {"error": f"{type(e).__name__}: {e}"}


def _arm(sp: Dict[str, Any], scenarios: List[Dict[str, Any]]) -> Optional[str]:
    """Write the best pullback into the entry zone and the best breakout into the trigger when the plan has none.
    POST_EARNINGS is never armed live (the watch loop would fire before the print); the ledger still tracks it."""
    armed = None
    scenarios = [s for s in scenarios if not s.get("not_before")]
    if not _f(sp.get("entry_zone_high")):
        pb = max((s for s in scenarios if s["entry"] == "LIMIT"), key=lambda s: s["p_touch_pct"] or 0, default=None)
        if pb:
            sp.update(entry_type="LIMIT", entry_zone_low=pb["zone_low"], entry_zone_high=pb["trigger"],
                      tactical_stop=pb["stop"], target_1=pb["target"], from_scenario=pb["kind"])
            armed = pb["kind"]
    if not _f(sp.get("breakout_level")):
        bo = max((s for s in scenarios if s["entry"] == "STOP"), key=lambda s: s["p_touch_pct"] or 0, default=None)
        if bo:
            sp.update(breakout_level=bo["trigger"], breakout_stop=bo["stop"], breakout_target_1=bo["target"])
            if not _f(sp.get("target_1")):
                sp["target_1"], sp["tactical_stop"] = bo["target"], bo["stop"]
                # Only here does the plan's own stop/target come from a scenario; a judge zone keeps its gate checks.
                sp.setdefault("from_scenario", bo["kind"])
            if str(sp.get("entry_type") or "").upper() == "NO_ENTRY":
                sp["entry_type"] = "BREAKOUT"
            armed = armed or bo["kind"]
    return armed


def cancel_unfilled(ticker: str, today: str) -> None:
    from src.tracking import ticket_paper

    for kind in KINDS:
        try:
            ticket_paper.cancel_unfilled(ticker, f"SCENARIO_{kind}", today)
        except Exception as e:
            logger.warning(f"[{ticker}] scenario cancel failed ({kind}): {e}")


def record(ticker: str, today: str, spot: float, scenarios: List[Dict[str, Any]]) -> int:
    from src.tracking import ticket_paper

    n = 0
    for sc in scenarios:
        lane = f"SCENARIO_{sc['kind']}"
        try:
            if ticket_paper.has_position(ticker, lane):
                continue
            n += ticket_paper.record_ticket({
                "ticker": ticker.upper(), "date": today, "lane": lane, "levels_source": "scenario",
                "entry_method": sc["entry"], "signal_close": spot, "stop": sc["stop"], "target_1": sc["target"],
                "limit_price": sc["trigger"], "exit_by": sc["window_end"], "not_before": sc.get("not_before"),
                "rr_at_market": sc["rr_at_trigger"], "zone_low": sc["zone_low"], "p_touch_pct": sc.get("p_touch_pct"),
                "median_touch_sessions": sc.get("median_sessions_to_touch"), "origin": sc.get("origin")})
        except Exception as e:
            logger.warning(f"[{ticker}] scenario ledger write failed ({lane}): {e}")
    return n


def attach_scenarios(watch_data: Dict[str, Any], ticker: str, spot: float, atr: float, em_pct: float = 0.0,
                     earnings: Optional[str] = None, today: Optional[date] = None, chain=None,
                     persist: bool = True, allow_unskip: bool = True, pine_stop: float = 0.0) -> List[Dict[str, Any]]:
    """Normalize, price, arm and ledger the judge's scenarios. A non-hard CASH_SKIP with a valid scenario
    becomes ARMED: "no trade today" is a plan for where price may go, not a dead end.
    A newer research replaces every unfilled resting scenario of the ticker, whatever it concludes."""
    today = today or date.today()
    if persist:
        cancel_unfilled(ticker, str(today))
    if str(watch_data.get("side") or "LONG").upper() != "LONG" or spot <= 0:
        return []
    veto = str(watch_data.get("veto") or "NONE").upper()
    if veto in HARD_VETOES:
        watch_data["scenarios"] = []
        return []
    sp = watch_data.setdefault("shares_plan", {})
    raw = [s for s in (watch_data.get("scenarios") or []) if isinstance(s, dict)] or seed_from_plan(sp, spot)
    kept, rejected = [], []
    for s in raw:
        sc, why = normalize(s, spot, atr, em_pct, today, earnings, pine_stop)
        if sc:
            kept.append(sc)
        else:
            rejected.append(why)
    watch_data["scenarios"], watch_data["scenarios_rejected"] = kept, rejected
    if not kept:
        return []
    price_options(ticker, kept, spot, em_pct, earnings, today, chain)
    skipped = "CASH_SKIP" in str(watch_data.get("verdict") or "").upper()
    if skipped and not allow_unskip:
        return kept
    armed = _arm(sp, kept)
    if armed:
        watch_data["armed_from_scenario"] = armed
        from src.tracking.trade_rules import normalize_stance

        if sp.get("from_scenario") and earnings and not normalize_stance(watch_data.get("earnings_stance")) and earnings[:10] <= max(s["window_end"] for s in kept):
            # The level gate rejects a plan with earnings in its window and no stance; a code-armed scenario never had one.
            watch_data["earnings_stance"] = "EXIT_BEFORE"
            watch_data.setdefault("earnings_rationale", "code-armed scenario: flat before the print")
    if skipped:
        watch_data["verdict"] = "ARMED"
        watch_data["verdict_note"] = "CASH_SKIP without a hard veto: scenarios armed"
    if persist:
        watch_data["scenarios_ledgered"] = record(ticker, str(today), spot, kept)
    return kept


def scenario_markdown(scenarios: List[Dict[str, Any]], spot: float) -> str:
    if not scenarios:
        return ""
    lines = [f"## 🧭 CONDITIONAL SCENARIOS (spot {spot:.2f}; paper, no measured edge)"]
    for s in scenarios:
        odds = f"{s['p_touch_pct']}% touch odds, median {s['median_sessions_to_touch']} sessions" if s.get("p_touch_pct") is not None else "touch odds n/a"
        lines.append(
            f"- **{s['kind']}** {'buy limit' if s['entry'] == 'LIMIT' else 'buy stop'} {s['trigger']} "
            f"(zone {s['zone_low']}-{s['trigger']}) · stop {s['stop']} · target {s['target']} · R:R {s['rr_at_trigger']} · "
            f"window to {s['window_end']}{' from ' + s['not_before'] if s.get('not_before') else ''} · {odds}")
        if s.get("thesis"):
            lines.append(f"  - Thesis: {s['thesis']}")
        lines.append(f"  - On arrival re-research must confirm: {'; '.join(s['confirm'])}")
        for k, label in (("tactical_spread", "Spread"), ("leaps", "LEAPS"), ("income_or_csp", "CSP")):
            tier = (s.get("options") or {}).get(k) or {}
            if tier.get("summary"):
                lines.append(f"  - {label} @ trigger: {tier['summary']}")
        for n in s.get("notes") or []:
            lines.append(f"  - Note: {n}")
    return "\n".join(lines)


def prior_scenarios_block(ticker: str, today: str) -> str:
    """Ground-truth line for the confirm research: the armed scenarios of the latest earlier plan."""
    try:
        from src.logic.order_ticket import _latest_watch_levels

        wd = _latest_watch_levels(ticker, today) or {}
    except Exception:
        return ""
    live = [s for s in (wd.get("scenarios") or []) if str(s.get("window_end") or "") >= today]
    if not live:
        return ""
    rows = "; ".join(
        f"{s['kind']} trigger {s['trigger']} stop {s['stop']} target {s['target']} to {s['window_end']} "
        f"(confirm: {', '.join(s.get('confirm') or [])})" for s in live)
    return (f"- ARMED SCENARIOS from the {wd.get('date')} plan: {rows}. If price has reached a trigger, this run is "
            "the arrival check: verify each confirm item from the reports. All pass and no hard veto: keep that "
            "scenario's levels and rule ENTER or ARMED. Any item fails: say which, and re-plan the scenarios.\n")
