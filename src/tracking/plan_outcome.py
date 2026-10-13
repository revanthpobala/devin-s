"""
src/tracking/plan_outcome.py — Prior plan outcome (plan/no-lost-movers follow-up).

When new research runs on a ticker that already carries a stored plan — or when the
chat is asked about a name — the answer must come from what the LEVELS actually did,
never from the spot price alone. "Did it break out?" is a question about a level, and
the level has a history.

This module loads the prior plan's levels, entry mode and plan date, pulls the daily
and intraday highs/lows since that date, and computes per level:

    crossed            did price trade through it, and when (first cross)
    held_on_close      did the last close stay beyond it (vs wicking through and back)
    outcome            one of the levels below

Outcomes emitted:

    broke_and_holding                  crossed and the close stayed beyond it
    failed_breakout_closed_back_below  crossed intraday, then the close fell back
    broke_and_pulled_back_retest_entry ran out of the zone and came back to re-test it
    stopped_stop_crossed               the stop was crossed and held below
    invalidated                         the invalidation level was crossed
    not_triggered                      never crossed — levels unchanged
    target_reached                      T1 / T2 crossed

The live price is then judged against those levels: in zone / above / below, R:R at
market to the (rebuilt) T1 and T2, and the at-market outcome — RUNNING_ACTIONABLE or
RUNNING_NO_ROOM, never a bare STALK. The result is injected into the deep, mid and
chat prompts as a structured "PRIOR PLAN OUTCOME" block, and is reachable by the chat
as the get_prior_plan_outcome tool.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from src.tracking.level_engine import (  # noqa: E402
    KIND_BREAKOUT,
    KIND_HIGH_20D,
    KIND_INVALIDATION,
    KIND_STOP,
    KIND_T1,
    KIND_T2,
    KIND_ZONE_TOP,
    build_armed_levels,
)

# Outcome vocabulary.
OUT_BROKE_AND_HOLDING = "broke_and_holding"
OUT_FAILED_BREAKOUT = "failed_breakout_closed_back_below"
OUT_RETEST_ENTRY = "broke_and_pulled_back_retest_entry"
OUT_STOPPED = "stopped_stop_crossed"
OUT_INVALIDATED = "invalidated"
OUT_NOT_TRIGGERED = "not_triggered"
OUT_TARGET_REACHED = "target_reached"

# Which kinds are "up" levels (price must close above for the break to hold).
_UP_KINDS = frozenset({KIND_BREAKOUT, KIND_ZONE_TOP, KIND_T1, KIND_T2, KIND_HIGH_20D})


def _f(v) -> float:
    try:
        f = float(v or 0.0)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def load_prior_plan(ticker: str, date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The most recent stored plan for a ticker: watch_targets first, then reports.

    The stored plan is what the desk actually armed yesterday — that is the plan whose
    levels get judged, not a fresh reconstruction.
    """
    try:
        from src.tracking.watch_manager import get_all_watch_targets

        rows = [r for r in get_all_watch_targets() if str(r.get("ticker") or "").upper() == ticker.upper()]
        if date_str:
            exact = [r for r in rows if str(r.get("date") or "")[:10] == str(date_str)[:10]]
            rows = exact or rows
        if not rows:
            return None
        rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
        row = rows[0]
        try:
            import json as _json

            raw = row.get("raw_json")
            shares = (_json.loads(raw).get("shares_plan") if isinstance(raw, str) and raw else None) or {}
        except Exception:
            shares = {}
        plan_view = {
            "entry_type": shares.get("entry_type") or row.get("entry_type") or "LIMIT",
            "entry_zone_low": row.get("entry_zone_low") or shares.get("entry_zone_low"),
            "entry_zone_high": row.get("entry_zone_high") or shares.get("entry_zone_high"),
            "breakout_level": row.get("breakout_level") or shares.get("breakout_level"),
            "breakout_stop": shares.get("breakout_stop"),
            "tactical_stop": row.get("tactical_stop") or shares.get("tactical_stop"),
            "target_1": row.get("target_1") or shares.get("target_1"),
            "target_2": row.get("target_2") or shares.get("target_2"),
            "invalidation_price": row.get("invalidation_price"),
            "targets_rebuilt": bool(shares.get("targets_rebuilt")),
            "targets_rebuilt_basis": shares.get("targets_rebuilt_basis"),
            "breakout_demoted_reason": shares.get("breakout_demoted_reason"),
            "setup_lane": row.get("verdict") and shares.get("setup_lane") or shares.get("setup_lane"),
        }
        return {
            "ticker": ticker.upper(),
            "plan_date": str(row.get("date") or "")[:10],
            "side": str(row.get("side") or "LONG").upper(),
            "status": row.get("status"),
            "plan": plan_view,
            "source": "watch_targets",
        }
    except Exception as e:
        logger.debug(f"[plan_outcome] no stored plan for {ticker}: {e}")
        return None


def _daily_series(bars) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        idx = [str(x)[:10] for x in bars.index] if hasattr(bars, "index") else []
        for i in range(len(bars)):
            row = bars.iloc[i]
            out.append({
                "date": idx[i] if i < len(idx) else "",
                "open": _f(row.get("Open") if hasattr(row, "get") else None),
                "high": _f(row.get("High") if hasattr(row, "get") else None),
                "low": _f(row.get("Low") if hasattr(row, "get") else None),
                "close": _f(row.get("Close") if hasattr(row, "get") else None),
                "volume": _f(row.get("Volume") if hasattr(row, "get") else None),
            })
    except Exception as e:
        logger.debug(f"[plan_outcome] daily series parse failed: {e}")
    return out


def _intraday_series(ticker: str, lookback_bars: int = 78) -> List[Dict[str, Any]]:
    """Latest-session 5m candles (78 bars ≈ one full session; absent means absent)."""
    try:
        from src.clients.schwab_client import get_intraday_candles

        raw = get_intraday_candles(ticker, frequency=5, lookback_bars=lookback_bars) or []
        out = []
        for c in raw:
            ts = c.get("datetime")
            try:
                ts_str = datetime.fromtimestamp(int(ts) / 1000).isoformat(timespec="minutes") if ts else ""
            except Exception:
                ts_str = str(ts or "")
            out.append({
                "ts": ts_str,
                "high": _f(c.get("high")),
                "low": _f(c.get("low")),
                "close": _f(c.get("close")),
                "volume": _f(c.get("volume")),
            })
        return out
    except Exception as e:
        logger.debug(f"[plan_outcome] intraday candles unavailable for {ticker}: {e}")
        return []


def _level_outcome(
    kind: str,
    level: float,
    daily: List[Dict[str, Any]],
    intraday: List[Dict[str, Any]],
    zone_low: float = 0.0,
    zone_high: float = 0.0,
) -> Dict[str, Any]:
    """Per-level outcome from the daily and intraday highs/lows since the plan date."""
    if level <= 0:
        return {"kind": kind, "level": level, "outcome": OUT_NOT_TRIGGERED, "crossed": False,
                "first_cross": None, "held_on_close": None, "detail": "no level stored"}

    is_up = kind in _UP_KINDS
    window = [b for b in daily if b["close"] > 0 or b["high"] > 0]

    # Daily: first bar that traded through, and whether the last close held.
    first_daily_cross = None
    wick_only_bars: List[str] = []
    for b in window:
        traded = (b["high"] >= level) if is_up else (b["low"] <= level)
        if traded and first_daily_cross is None:
            first_daily_cross = b["date"]
        if traded:
            held = (b["close"] >= level) if is_up else (b["close"] <= level)
            if not held:
                wick_only_bars.append(b["date"])

    closes = [b["close"] for b in window if b["close"] > 0]
    last_close = closes[-1] if closes else 0.0
    if last_close <= 0 and intraday:
        # No daily bar has closed since the plan date: the latest session's last
        # intraday print is the most recent close available.
        last_close = intraday[-1]["close"]
    held_on_close = None
    if last_close > 0:
        held_on_close = (last_close >= level) if is_up else (last_close <= level)

    # Intraday (latest session): first trade-through timestamp.
    first_intraday_cross = None
    for b in intraday:
        traded = (b["high"] >= level) if is_up else (b["low"] <= level)
        if traded and first_intraday_cross is None:
            first_intraday_cross = b["ts"]
            break

    crossed = first_daily_cross is not None or first_intraday_cross is not None

    if not crossed:
        outcome, detail = OUT_NOT_TRIGGERED, "price never traded through this level since the plan"
    elif kind == KIND_STOP:
        outcome = OUT_STOPPED if held_on_close else OUT_NOT_TRIGGERED
        detail = f"stop crossed on {first_daily_cross or first_intraday_cross}; close {'below' if held_on_close else 'back above'} the stop"
    elif kind == KIND_INVALIDATION:
        outcome = OUT_INVALIDATED if held_on_close else OUT_NOT_TRIGGERED
        detail = f"invalidation crossed on {first_daily_cross or first_intraday_cross}"
    elif kind in (KIND_T1, KIND_T2):
        outcome = OUT_TARGET_REACHED
        detail = f"target crossed on {first_daily_cross or first_intraday_cross}"
    elif kind == KIND_ZONE_TOP and zone_low > 0 and zone_high > 0 and last_close <= level and last_close >= zone_low:
        # Ran out of the zone and came back to re-test the entry.
        outcome = OUT_RETEST_ENTRY
        detail = (
            f"crossed the zone top {level:.2f} on {first_daily_cross or first_intraday_cross}, then "
            f"pulled back to ${last_close:.2f} — re-testing the entry zone"
        )
    else:
        # Trigger / zone kinds: the interesting distinction is holding vs failing back.
        if held_on_close:
            outcome = OUT_BROKE_AND_HOLDING
            detail = (
                f"broke {level:.2f} on {first_daily_cross or first_intraday_cross} and the close "
                f"${last_close:.2f} held {'above' if is_up else 'below'} it"
            )
        else:
            outcome = OUT_FAILED_BREAKOUT
            detail = (
                f"traded through {level:.2f} on {first_daily_cross or first_intraday_cross} but the close "
                f"${last_close:.2f} fell back {'below' if is_up else 'above'} it"
            )
            if wick_only_bars:
                detail += f" (wick-only sessions: {', '.join(wick_only_bars[-3:])})"

    return {
        "kind": kind,
        "level": round(level, 4),
        "outcome": outcome,
        "crossed": crossed,
        "first_cross": first_intraday_cross or first_daily_cross,
        "first_cross_daily": first_daily_cross,
        "first_cross_intraday": first_intraday_cross,
        "held_on_close": held_on_close,
        "last_close": round(last_close, 4) if last_close else None,
        "detail": detail,
    }


def judge_at_market(plan: Dict[str, Any], live_price: float, side: str = "LONG") -> Dict[str, Any]:
    """Judge the position at the live price against the plan's levels.

    Reports in-zone / above / below, R:R at market to T1 and T2 (the best of the two is
    what the floor rule sees), and the at-market outcome: RUNNING_ACTIONABLE when the
    stop is a real level below price and the best R:R clears the floor, otherwise
    RUNNING_NO_ROOM — said plainly, never collapsed to a bare STALK.
    """
    try:
        from src.tracking.rr_config import min_rr
    except Exception:
        min_rr = lambda: 2.0  # noqa: E731

    pv = plan or {}
    stop = _f(pv.get("tactical_stop"))
    t1 = _f(pv.get("target_1"))
    t2 = _f(pv.get("target_2"))
    zone_low = _f(pv.get("entry_zone_low"))
    zone_high = _f(pv.get("entry_zone_high"))
    px = _f(live_price)

    rr_t1 = rr_t2 = None
    if px > stop > 0:
        if t1 > px:
            rr_t1 = round((t1 - px) / (px - stop), 4)
        if t2 > px:
            rr_t2 = round((t2 - px) / (px - stop), 4)
    best_rr = max([r for r in (rr_t1, rr_t2) if r is not None], default=None)

    in_zone = bool(zone_low > 0 and zone_high > 0 and zone_low <= px <= zone_high)
    if in_zone:
        position = "IN_ZONE"
    elif zone_high > 0 and px > zone_high:
        position = "ABOVE_ZONE_RUNNING"
    elif zone_low > 0 and px < zone_low:
        position = "BELOW_ZONE"
    else:
        position = "NO_ZONE_STORED"

    floor = min_rr()
    stop_is_real = 0 < stop < px
    actionable = bool(stop_is_real and best_rr is not None and best_rr >= floor)
    outcome = "RUNNING_ACTIONABLE" if actionable else "RUNNING_NO_ROOM"

    if actionable:
        verdict = (
            f"At market ${px:.2f} the stop ${stop:.2f} is a real level below price and R:R to the "
            f"plan targets is {best_rr:.2f} (>= {floor:.2f} floor) — RUNNING_ACTIONABLE."
        )
    elif not stop_is_real:
        verdict = (
            f"At market ${px:.2f} there is no usable stop below price (stop ${stop:.2f}) — "
            f"RUNNING_NO_ROOM, and the level stays armed."
        )
    else:
        verdict = (
            f"At market ${px:.2f} the best R:R to the plan targets is "
            f"{best_rr if best_rr is not None else 0:.2f} against the {floor:.2f} floor — "
            f"RUNNING_NO_ROOM; wait for a pullback to the zone or a new level."
        )

    return {
        "live_price": px,
        "position": position,
        "stop": stop,
        "target_1": t1,
        "target_2": t2,
        "rr_at_market_to_t1": rr_t1,
        "rr_at_market_to_t2": rr_t2,
        "best_rr_at_market": best_rr,
        "rr_floor": floor,
        "outcome": outcome,
        "verdict": verdict,
    }


def build_prior_plan_outcome(
    ticker: str,
    plan: Optional[Dict[str, Any]] = None,
    plan_date: Optional[str] = None,
    live_price: Optional[float] = None,
    side: str = "LONG",
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """The full prior-plan outcome: per-level history + the live-price judgment.

    `plan` is a plan view (entry_zone_low/high, breakout_level, tactical_stop, target_1/2,
    invalidation_price, plus the entry mode/plan date in `plan_date`). When omitted it
    loads the stored plan (load_prior_plan).
    """
    loaded = None
    if plan is None:
        loaded = load_prior_plan(ticker, plan_date)
        if not loaded:
            return {"ticker": ticker.upper(), "error": "no stored plan", "levels": [], "judgment": None}
        plan = loaded["plan"]
        plan_date = plan_date or loaded["plan_date"]
        side = side or loaded["side"]

    plan_date = str(plan_date or "")[:10]
    high_20d = None
    try:
        from src.tracking.level_engine import bars_high_20d_and_adv20

        high_20d, _adv = bars_high_20d_and_adv20(ticker, as_of=as_of or None)
    except Exception:
        high_20d = None

    levels = build_armed_levels(plan, side=side, high_20d=high_20d)
    if not levels:
        return {"ticker": ticker.upper(), "error": "plan has no armed levels", "levels": [], "judgment": None}

    # Daily highs/lows since the plan date + the latest session's intraday bars.
    daily_bars = None
    try:
        from src.tracking.execution_validator import get_bars_since_date

        daily_bars = get_bars_since_date(ticker, plan_date or None)
    except Exception as e:
        logger.debug(f"[plan_outcome] daily bars unavailable for {ticker}: {e}")
    daily = _daily_series(daily_bars) if daily_bars is not None else []
    if plan_date:
        daily = [b for b in daily if b["date"] > plan_date]
    intraday = _intraday_series(ticker)

    level_results = [
        _level_outcome(
            lv.kind,
            lv.price,
            daily,
            intraday,
            zone_low=_f(plan.get("entry_zone_low")),
            zone_high=_f(plan.get("entry_zone_high")),
        )
        for lv in levels
    ]

    if live_price is None:
        try:
            from src.clients.price_client import get_current_price

            live_price = get_current_price(ticker)
        except Exception:
            live_price = None
    judgment = judge_at_market(plan, _f(live_price), side=side) if live_price else None

    def _trigger_outcome() -> Optional[str]:
        bo = next((r for r in level_results if r["kind"] == KIND_BREAKOUT), None)
        if bo:
            return bo["outcome"]
        zt = next((r for r in level_results if r["kind"] == KIND_ZONE_TOP), None)
        return zt["outcome"] if zt else None

    return {
        "ticker": ticker.upper(),
        "plan_date": plan_date,
        "side": side.upper(),
        "entry_mode": str(plan.get("entry_type") or "LIMIT").upper(),
        "targets_rebuilt": bool(plan.get("targets_rebuilt")),
        "targets_rebuilt_basis": plan.get("targets_rebuilt_basis"),
        "as_of": as_of or datetime.now().strftime("%Y-%m-%d"),
        "levels": level_results,
        "trigger_outcome": _trigger_outcome(),
        "judgment": judgment,
        "intraday_coverage": "latest session only" if intraday else "unavailable (daily closes only)",
    }


_OUTCOME_LABEL = {
    OUT_BROKE_AND_HOLDING: "BROKE AND HOLDING",
    OUT_FAILED_BREAKOUT: "FAILED BREAKOUT — CLOSED BACK BELOW",
    OUT_RETEST_ENTRY: "BROKE AND PULLED BACK — RETEST ENTRY",
    OUT_STOPPED: "STOPPED — STOP CROSSED",
    OUT_INVALIDATED: "INVALIDATED",
    OUT_NOT_TRIGGERED: "NOT TRIGGERED — LEVEL UNCHANGED",
    OUT_TARGET_REACHED: "TARGET REACHED",
}


def plan_outcome_markdown(outcome: Dict[str, Any]) -> str:
    """The 'PRIOR PLAN OUTCOME' block for the deep / mid / chat prompts."""
    if not outcome or outcome.get("error"):
        return f"PRIOR PLAN OUTCOME: {outcome.get('error', 'unavailable') if outcome else 'unavailable'}."

    lines = [
        f"PRIOR PLAN OUTCOME — {outcome['ticker']} (plan dated {outcome.get('plan_date') or 'unknown'}, "
        f"entry mode {outcome.get('entry_mode')})",
        "Built from daily and intraday highs/lows since the plan date. Do not answer from the spot price alone.",
    ]
    if outcome.get("targets_rebuilt"):
        lines.append(
            f"Targets were rebuilt above the trigger via {outcome.get('targets_rebuilt_basis') or 'a real level'}; "
            f"the trigger itself was kept armed."
        )
    lines.append("")
    for lv in outcome.get("levels", []):
        label = _OUTCOME_LABEL.get(lv.get("outcome"), str(lv.get("outcome") or "").upper())
        when = lv.get("first_cross") or "never"
        held = lv.get("held_on_close")
        held_txt = "" if held is None else (" | held on close" if held else " | did NOT hold on close")
        lines.append(f"- {lv['kind']} ${lv.get('level')}: {label} | crossed: {'yes' if lv.get('crossed') else 'no'} | when: {when}{held_txt}")
    j = outcome.get("judgment")
    if j:
        lines += [
            "",
            f"JUDGMENT AT MARKET (${j.get('live_price')}): {j.get('position')}",
            f"- {j.get('verdict')}",
            f"- R:R at market: T1 {j.get('rr_at_market_to_t1')} / T2 {j.get('rr_at_market_to_t2')} "
            f"(floor {j.get('rr_floor')})",
        ]
    return "\n".join(lines)


def format_prior_plan_outcome(ticker: str, **kwargs) -> str:
    """Convenience: build the outcome and render the block in one call."""
    return plan_outcome_markdown(build_prior_plan_outcome(ticker, **kwargs))
