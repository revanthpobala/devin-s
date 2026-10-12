"""
src/tracking/level_engine.py — Universal level engine (plan/no-lost-movers.md, A).

STALK is not a decision: it means "these levels are armed". The old watch loop
was a top-down elif cascade that declared a strong stock dead (MISSED_RUNAWAY)
exactly when it was working, and only noticed breakouts after the close. This
module replaces that with events: every armed level on every watched name, a
cross detected when price trades through it, and one alert per level per day.

Level kinds (LONG semantics; SHORT is mirrored):

    ZONE_LOW      entry zone low      cross UP = bounce
    ZONE_TOP      entry zone high     cross UP = zone fill gone / running
    BREAKOUT      breakout level      trigger
    HIGH_20D      prior 20-day high   trigger
    T1            target 1            resistance
    T2            target 2            resistance
    STOP          tactical stop       invalidation
    INVALIDATION  invalidation price  thesis death

Re-arm rules: trigger/resistance levels re-arm after a close back through the
level; STOP and INVALIDATION are single-fire per day.

Volume pace (A3): a cross is confirmed when RVOL (today's print / 20-day average
daily volume) is >= 1.0. When ADV20 is unavailable the cross still fires, tagged
UNMEASURED — never silently dropped.

Absent means absent: a level with no real price is omitted, never fabricated
(the repo's fails-closed convention, same as arbitration's pb_funnel handling).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Level kinds. The alert trigger_type for a cross is "LEVEL_<KIND>".
KIND_ZONE_LOW = "ZONE_LOW"
KIND_ZONE_TOP = "ZONE_TOP"
KIND_BREAKOUT = "BREAKOUT"
KIND_HIGH_20D = "HIGH_20D"
KIND_T1 = "T1"
KIND_T2 = "T2"
KIND_STOP = "STOP"
KIND_INVALIDATION = "INVALIDATION"

# "UP" fires when price crosses from below to at/above; "DOWN" the mirror.
SINGLE_FIRE_KINDS = frozenset({KIND_STOP, KIND_INVALIDATION})
TRIGGER_KINDS = frozenset({KIND_BREAKOUT, KIND_HIGH_20D, KIND_ZONE_TOP})


@dataclass
class ArmedLevel:
    """One armed price level on a watched name."""

    kind: str
    price: float
    side: str = "LONG"
    direction: str = "UP"          # which movement fires it
    armed: bool = True
    last_fired_date: Optional[str] = None
    last_fired_at: Optional[str] = None

    def fires_on(self, prev_price: float, curr_price: float) -> bool:
        """True when price traded through this level between the two polls.

        A touch from either side counts as trading through, but only the level's
        own direction fires: a breakout does not fire when price falls back
        through it from above.
        """
        if prev_price == curr_price:
            return False
        if self.direction == "UP":
            return prev_price < self.price <= curr_price
        return prev_price > self.price >= curr_price


@dataclass
class LevelCross:
    """A recorded cross event for one poll."""

    kind: str
    price: float
    side: str
    prev_price: float
    curr_price: float
    rvol: Optional[float] = None            # today's print / ADV20; None = unmeasured
    volume_confirmed: bool = True           # False when ADV20 exists and RVOL < floor

    @property
    def trigger_type(self) -> str:
        return f"LEVEL_{self.kind}"


def _num(val: Any) -> float:
    """Safely coerce to float; 0.0 on anything missing/unparseable."""
    if val is None or val == "" or val == "N/A":
        return 0.0
    try:
        f = float(val)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_armed_levels(
    plan: Dict[str, Any],
    side: str = "LONG",
    high_20d: Optional[float] = None,
) -> List[ArmedLevel]:
    """Derive the ordered armed-level list from a stored plan (or dossier long_plan).

    Accepts both the watch_targets row vocabulary (entry_zone_low / entry_zone_high
    / breakout_level / tactical_stop / target_1 / target_2 / invalidation_price)
    and the dossier long_plan vocabulary (zone: [low, high] / stop / target).
    Levels with no positive price are omitted — absent means absent.
    """
    side = (side or "LONG").upper()
    is_long = side != "SHORT"

    zone = plan.get("zone")
    z_low = _num(plan.get("entry_zone_low") or plan.get("entry_low"))
    z_high = _num(plan.get("entry_zone_high") or plan.get("entry_high"))
    if (not z_low or not z_high) and isinstance(zone, (list, tuple)) and len(zone) >= 2:
        z_low = z_low or _num(zone[0])
        z_high = z_high or _num(zone[1])
    if z_low and z_high and z_low > z_high:
        z_low, z_high = z_high, z_low

    breakout = _num(plan.get("breakout_level"))
    stop = _num(plan.get("tactical_stop") or plan.get("stop"))
    t1 = _num(plan.get("target_1") or plan.get("target"))
    t2 = _num(plan.get("target_2"))
    invalidation = _num(plan.get("invalidation_price"))

    up, down = ("UP", "DOWN") if is_long else ("DOWN", "UP")

    levels: List[ArmedLevel] = []
    if z_low > 0:
        levels.append(ArmedLevel(KIND_ZONE_LOW, z_low, side, up))
    if z_high > 0:
        levels.append(ArmedLevel(KIND_ZONE_TOP, z_high, side, up))
    if breakout > 0:
        levels.append(ArmedLevel(KIND_BREAKOUT, breakout, side, up))
    if high_20d and high_20d > 0:
        levels.append(ArmedLevel(KIND_HIGH_20D, float(high_20d), side, up))
    if t1 > 0:
        levels.append(ArmedLevel(KIND_T1, t1, side, up))
    if t2 > 0:
        levels.append(ArmedLevel(KIND_T2, t2, side, up))
    if stop > 0:
        levels.append(ArmedLevel(KIND_STOP, stop, side, down))
    if invalidation > 0 and (stop <= 0 or abs(invalidation - stop) > 1e-4):
        levels.append(ArmedLevel(KIND_INVALIDATION, invalidation, side, down))

    # Stable order: cheap to scan, and the engine's output order is the alert order.
    kind_order = {
        KIND_ZONE_LOW: 0, KIND_ZONE_TOP: 1, KIND_BREAKOUT: 2, KIND_HIGH_20D: 3,
        KIND_T1: 4, KIND_T2: 5, KIND_STOP: 6, KIND_INVALIDATION: 7,
    }
    levels.sort(key=lambda lv: (kind_order.get(lv.kind, 99), lv.price))
    return levels


def volume_pace(volume: Optional[float], adv20: Optional[float]) -> Optional[float]:
    """RVOL = today's print / 20-day average daily volume; None when unmeasurable.

    None means absent, never zero: a cross with no ADV20 is tagged UNMEASURED and
    still fires, it is not silently dropped.
    """
    vol = _num(volume)
    adv = _num(adv20)
    if vol <= 0 or adv <= 0:
        return None
    return round(vol / adv, 3)


def rearm_on_close(levels: List[ArmedLevel], close_price: float) -> None:
    """Re-arm trigger/resistance levels after a close back through them.

    STOP / INVALIDATION are single-fire per day and are not re-armed here.
    """
    for lv in levels:
        if lv.kind in SINGLE_FIRE_KINDS or lv.armed:
            continue
        if lv.direction == "UP" and close_price < lv.price:
            lv.armed = True
        elif lv.direction == "DOWN" and close_price > lv.price:
            lv.armed = True


def rearm_for_new_day(levels: List[ArmedLevel], today_str: str) -> None:
    """Day rollover: single-fire STOP / INVALIDATION re-arm on a new date.

    Every level is a new decision tomorrow, so the single-fire guard is per day,
    not per process lifetime.
    """
    for lv in levels:
        if lv.armed:
            continue
        if lv.last_fired_date and lv.last_fired_date != today_str:
            lv.armed = True
            lv.last_fired_date = None
            lv.last_fired_at = None


def _bars_frame(ticker: str, start_date: str):
    """Daily bars via the shared helper, or None. Imported lazily to avoid a cycle."""
    try:
        from src.tracking.execution_validator import get_bars_since_date
        return get_bars_since_date(ticker, start_date)
    except Exception as e:  # pragma: no cover - network/offline path
        logger.debug(f"[level_engine] get_bars_since_date failed for {ticker}: {e}")
        return None


def bars_high_20d_and_adv20(
    ticker: str, as_of: Optional[str] = None
) -> Tuple[Optional[float], Optional[float]]:
    """(prior 20-day high, ADV20) from daily bars; (None, None) when unavailable.

    Same data source as `_compute_atr14_from_bars` (level_validation.py): the
    get_bars_since_date helper. Never fabricates — absent means absent.

    With no explicit `as_of` the window ends at the last COMPLETED session: the live
    session's partial bar is not a prior 20-day high, and today's print is measured
    against an ADV20 that excludes it. An explicit as_of includes bars up to that date
    (the historical-replay contract).
    """
    if not ticker:
        return None, None
    try:
        from datetime import timedelta
        if as_of:
            try:
                d_obj = datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
            except Exception:
                d_obj = datetime.now().date()
        else:
            d_obj = datetime.now().date() - timedelta(days=1)
        bars = _bars_frame(ticker, (d_obj - timedelta(days=45)).strftime("%Y-%m-%d"))
        if bars is None or len(bars) == 0:
            return None, None
        try:
            idx_strs = [str(x)[:10] for x in bars.index]
            sub_mask = [d <= str(as_of)[:10] for d in idx_strs] if as_of else [True] * len(idx_strs)
            bars = bars[sub_mask] if hasattr(bars, "__getitem__") else bars
        except Exception:
            pass
        if bars is None or len(bars) == 0:
            return None, None

        highs = []
        vols = []
        for i in range(len(bars)):
            row = bars.iloc[i]
            h = _num(row.get("High") if hasattr(row, "get") else None)
            v = _num(row.get("Volume") if hasattr(row, "get") else None)
            if h > 0:
                highs.append(h)
            if v > 0:
                vols.append(v)
        high_20d = max(highs[-20:]) if highs else None
        adv20 = (sum(vols[-20:]) / len(vols[-20:])) if vols else None
        return (high_20d if high_20d else None, adv20 if adv20 else None)
    except Exception as e:
        logger.debug(f"[level_engine] 20d stats failed for {ticker}: {e}")
        return None, None


@dataclass
class LevelEngineState:
    """Per-ticker runtime state: armed levels plus the last poll price.

    The durable per-day dedupe is the watch_alerts COUNT in the watch loop; this
    state is the in-process armed/cooldown guard. On a cold start every level
    re-arms (the COUNT still prevents a same-day storm).
    """

    ticker: str
    levels: List[ArmedLevel] = field(default_factory=list)
    last_price: Optional[float] = None
    # The plan the current levels were built from. A changed plan (new thesis,
    # edited levels, new 20-day high) rebuilds and re-arms; an unchanged plan keeps
    # the armed flags, cooldown stamps and in-process last price across polls.
    plan_signature: tuple = ()

    def by_kind(self, kind: str) -> Optional[ArmedLevel]:
        for lv in self.levels:
            if lv.kind == kind:
                return lv
        return None

    def detect_crosses(
        self,
        curr_price: float,
        rvol_floor: float = 1.0,
        rvol: Optional[float] = None,
    ) -> List[LevelCross]:
        """All armed levels whose price traded through between the last poll and now.

        Volume pace: rvol is today's print / ADV20. None (ADV20 unavailable) keeps
        the cross, tagged unmeasured; below the floor the cross is recorded as not
        volume-confirmed so the caller can hold the alert.
        """
        prev = self.last_price
        if prev is None or curr_price <= 0:
            return []
        crosses: List[LevelCross] = []
        for lv in self.levels:
            if not lv.armed:
                continue
            if lv.fires_on(prev, curr_price):
                cross = LevelCross(
                    kind=lv.kind,
                    price=lv.price,
                    side=lv.side,
                    prev_price=prev,
                    curr_price=curr_price,
                    rvol=rvol,
                    volume_confirmed=(rvol is None) or (rvol >= rvol_floor),
                )
                crosses.append(cross)
        return crosses

    def mark_fired(self, kind: str, when: Optional[datetime] = None) -> None:
        """Disarm the level and stamp it. STOP/INVALIDATION stay disarmed for the day."""
        lv = self.by_kind(kind)
        if lv is None:
            return
        now = when or datetime.now()
        lv.armed = False
        lv.last_fired_date = now.strftime("%Y-%m-%d")
        lv.last_fired_at = now.isoformat(timespec="seconds")

    def rearm_on_close(self, close_price: float) -> None:
        """Re-arm trigger/resistance levels after a close back through them."""
        rearm_on_close(self.levels, close_price)


# In-process registry, keyed by ticker. Rebuilt when the plan identity changes.
_ENGINE_STATE: Dict[str, LevelEngineState] = {}


def engine_state_key(ticker: str, plan_identity: str) -> str:
    return f"{ticker.upper()}|{plan_identity}"


def _plan_signature(plan: Dict[str, Any], side: str, high_20d: Optional[float]) -> tuple:
    """A comparable fingerprint of the armed levels a plan produces."""
    return tuple(
        (lv.kind, lv.price, lv.direction)
        for lv in build_armed_levels(plan or {}, side=side, high_20d=high_20d)
    )


def get_engine_state(
    ticker: str,
    plan: Optional[Dict[str, Any]] = None,
    plan_identity: str = "",
    side: str = "LONG",
    high_20d: Optional[float] = None,
    last_price: Optional[float] = None,
) -> LevelEngineState:
    """Get (or rebuild) a ticker's engine state.

    ``plan_identity`` pins the state to one thesis (e.g. f"{ticker}_{date}"). The
    state survives every poll of an unchanged plan — armed flags, cooldown stamps
    and the in-process last price are what make re-arm and the cooldown real. It
    rebuilds only when the levels actually change (new thesis, edited plan, new
    20-day high): those are new levels, so they re-arm.
    """
    key = engine_state_key(ticker, plan_identity)
    state = _ENGINE_STATE.get(key)

    if plan is not None:
        signature = _plan_signature(plan, side, high_20d)
        if state is None or state.plan_signature != signature:
            state = LevelEngineState(
                ticker=ticker.upper(),
                levels=build_armed_levels(plan, side=side, high_20d=high_20d),
                last_price=None,
                plan_signature=signature,
            )
            _ENGINE_STATE[key] = state
    elif state is None:
        state = LevelEngineState(ticker=ticker.upper())
        _ENGINE_STATE[key] = state

    if last_price is not None and state.last_price is None:
        state.last_price = last_price
    return state


def reset_engine_state() -> None:
    """Test/admin hook: drop all in-process level state."""
    _ENGINE_STATE.clear()
