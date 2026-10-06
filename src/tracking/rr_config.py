"""
Tunable R:R thresholds, persisted to disk and editable from the desk UI.

Scope, deliberately narrow. What is configurable here is the *actionable* R:R -- the number that
decides whether a row lands in "needs you" and whether an ENTRY push fires. That is a preference:
different account sizes and risk budgets reasonably want a different bar.

What is NOT configurable, and why:

  * the stop-ATR noise floor (0.7 ATR) -- that is the corpus median, not a preference. Loosening it
    promotes rows whose R:R is an artifact of a denominator inside daily noise;
  * MAX_PLAUSIBLE_R (20) in intraday_stats -- a unit-error guard, not a dial;
  * the PB funnel requirement on the ENTRY push -- the one gate that actually measured positive.
    R:R tier is explicitly NOT a substitute for it (R:R 2-3 with PB is +0.072R; R:R >=3 without PB
    is +0.030R).

So the control moves the bar; it cannot remove the reasons the bar exists. Every read returns the
measured lane prior alongside the threshold so the tuning happens with the base rate in view.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from src import config

logger = logging.getLogger(__name__)

# key -> (default, min, max)
SPEC: Dict[str, tuple] = {
    "rr_market_min": (2.0, 1.0, 10.0),   # needs-you gate AND the ENTRY push gate
    "rr_hi_rr": (3.0, 2.0, 25.0),        # the HI-RR / teal tag
}

DEFAULTS: Dict[str, float] = {k: v[0] for k, v in SPEC.items()}

# Measured, NOT tunable, and defined here so there is exactly one copy. The corpus median stop is
# 0.69 ATR and 65% of stops get hit; anything narrower sits inside daily noise and inflates R:R
# without adding information. It lives in this module for proximity to the thresholds that must be
# read next to it, not because it is configurable.
STOP_ATR_MIN = 0.7

# Presets in the notation traders actually say out loud. `label` is what the UI button shows;
# `rr_market_min` is the same number written as reward:risk (1:X), so 1:2 == rr_market_min 2.0.
#
# `prior` is the measured long-side lane prior at that tier, shown next to the button so the pick
# is made against the base rate. Note what the numbers say: raising the floor past 2.0 buys a
# higher expectancy per trade (0.072R -> 0.151R) at a materially worse hit rate (34% -> 26%).
# That trade-off is the whole decision, so it is stated rather than left to be discovered.
PRESETS: Dict[str, Dict[str, Any]] = {
    "1_0": {
        "label": "1:1",
        "rr_market_min": 1.0,
        "rr_hi_rr": 3.0,
        "blurb": "permissive · research mode (negative prior -0.017R)",
        "below_measured_floor": True,
    },
    "1_25": {
        "label": "1:1.25",
        "rr_market_min": 1.25,
        "rr_hi_rr": 3.0,
        "blurb": "permissive · below measured floor (-0.013R)",
        "below_measured_floor": True,
    },
    "1_5": {
        "label": "1:1.5",
        "rr_market_min": 1.5,
        "rr_hi_rr": 3.0,
        "blurb": "research mode · below measured floor (-0.013R)",
        "below_measured_floor": True,
    },
    "2_0": {
        "label": "1:2",
        "rr_market_min": 2.0,
        "rr_hi_rr": 3.0,
        "blurb": "measured default · PB 34% win / +0.089R edge",
        "below_measured_floor": False,
    },
}

# RLock, not Lock: set_rr_config() holds the lock while calling get_rr_config() to merge, so a plain
# Lock deadlocks on the very first write. The read is what keeps the two processes in agreement, so
# merging inside the critical section is the behaviour we want -- the lock just has to allow it.
_lock = threading.RLock()
_cache: Dict[str, float] = {}
_cache_mtime: Optional[float] = None

CONFIG_PATH = config.BASE_DIR / "data" / "rr_config.json"


def _path() -> Path:
    return Path(os.getenv("RR_CONFIG_PATH") or CONFIG_PATH)


def _validate(current: Dict[str, float], updates: Dict[str, Any]) -> Dict[str, float]:
    """Coerce + bound-check one update. Raises ValueError with a message the UI can show verbatim.

    `current` is passed in rather than read here, so the caller can hold the lock across the whole
    validate-then-write sequence.
    """
    out: Dict[str, float] = {}
    for key, raw in updates.items():
        if key not in SPEC:
            raise ValueError(f"unknown R:R setting {key!r}; known: {sorted(SPEC)}")
        if raw is None:
            continue          # leave alone, matching what the API layer does before calling us
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {raw!r}")
        if val != val or val in (float("inf"), float("-inf")):
            raise ValueError(f"{key} must be finite")
        _default, lo, hi = SPEC[key]
        if not (lo <= val <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}, got {val}")
        out[key] = round(val, 2)
    return out


def _coerce(key: str, raw: Any) -> Optional[float]:
    """Read one stored value defensively.

    A hand-edited or partially-written file must not be able to poison the gates. json.loads
    accepts the bare literals `NaN` and `Infinity`, and an unclamped float would silently fail
    every comparison downstream -- a NaN floor makes `rr_at_market >= nan` False forever, so the
    desk would quietly return zero rows with no error anywhere.
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val or val in (float("inf"), float("-inf")):
        return None
    _default, lo, hi = SPEC[key]
    if not (lo <= val <= hi):
        logger.warning("rr_config: %s=%r is outside [%s, %s]; ignoring", key, raw, lo, hi)
        return None
    return val


def get_rr_config() -> Dict[str, float]:
    """Current values, defaults for anything unset or unusable. Re-reads when the file changes.

    The mtime check matters because the alert sweep and the UI are separate processes: a change
    made in the browser has to be visible to the sweep without a restart.
    """
    path = _path()
    try:
        mtime = path.stat().st_mtime_ns      # ns, not float seconds: sub-second rewrites on a
    except OSError:                          # coarse filesystem clock would read as "unchanged"
        mtime = None                         # and leave the stale cache live.

    with _lock:
        if _cache and mtime is not None and mtime == _cache_mtime:
            return dict(_cache)
        values = dict(DEFAULTS)
        if mtime is not None:
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    for k in SPEC:
                        if k in stored:
                            coerced = _coerce(k, stored[k])
                            if coerced is not None:
                                values[k] = coerced
            except Exception as e:
                logger.warning("rr_config: unreadable %s (%s); using defaults", path, e)
        # The cross-field invariant is enforced here too, because a hand-edited file can violate
        # it. Falls back to the default tag rather than leaving an unreachable HI-RR badge.
        if values["rr_hi_rr"] < values["rr_market_min"]:
            values["rr_hi_rr"] = DEFAULTS["rr_hi_rr"]
            if DEFAULTS["rr_hi_rr"] < values["rr_market_min"]:
                values["rr_market_min"] = DEFAULTS["rr_market_min"]
        _cache.clear()
        _cache.update(values)
        globals()["_cache_mtime"] = mtime
        return dict(_cache)


def set_rr_config(**updates: Any) -> Dict[str, float]:
    """Validate, persist, and return the new config. Raises ValueError on bad input.

    Reject unknown keys even when every value is None, so a typo'd field name surfaces instead of
    being treated as a no-op.
    """
    for key in updates:
        if key not in SPEC:
            raise ValueError(f"unknown R:R setting {key!r}; known: {sorted(SPEC)}")

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        # Merge and validate together, inside the lock. _validate used to call get_rr_config()
        # before the lock was held, so two concurrent writers could each validate against a
        # snapshot the other had already changed.
        merged = {**DEFAULTS, **get_rr_config()}
        merged.update(_validate(merged, updates))
        if merged["rr_hi_rr"] < merged["rr_market_min"]:
            raise ValueError(
                f"HI-RR tag ({merged['rr_hi_rr']}) cannot be below the actionable floor "
                f"({merged['rr_market_min']}); nothing would ever be tagged HI-RR"
            )

        # Per-process temp name: a shared ".tmp" lets two writers interleave into the same file
        # and publish a half-written config, defeating the atomicity os.replace is there for.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)          # atomic: a reader never sees a half-written config
        _cache.clear()
        _cache.update(merged)
        globals()["_cache_mtime"] = path.stat().st_mtime_ns
    logger.info("rr_config updated: %s", updates)
    return dict(merged)


def reset_rr_config() -> Dict[str, float]:
    """Back to measured defaults."""
    path = _path()
    with _lock:
        if path.exists():
            path.unlink()
        _cache.clear()
        globals()["_cache_mtime"] = None      # None means "no file", so the next read re-derives
    return get_rr_config()


def apply_preset(name: str) -> Dict[str, float]:
    """Apply a named preset. Raises ValueError on an unknown name."""
    preset = PRESETS.get(str(name or "").strip())
    if preset is None:
        raise ValueError(f"unknown preset {name!r}; known: {sorted(PRESETS)}")
    return set_rr_config(rr_market_min=preset["rr_market_min"], rr_hi_rr=preset["rr_hi_rr"])


def active_preset() -> Optional[str]:
    """Name of the preset the current values match exactly, else None (custom settings)."""
    cfg = get_rr_config()
    for name, p in PRESETS.items():
        if cfg["rr_market_min"] == p["rr_market_min"] and cfg["rr_hi_rr"] == p["rr_hi_rr"]:
            return name
    return None


# --- hot-path accessors. Dict reads, so these are free in the per-row loops. ----------------------

def min_rr() -> float:
    return get_rr_config()["rr_market_min"]


def hi_rr() -> float:
    return get_rr_config()["rr_hi_rr"]


def as_ui_payload() -> Dict[str, Any]:
    """Shape the desk UI and the API both render from."""
    cfg = get_rr_config()
    return {
        "values": cfg,
        "defaults": DEFAULTS,
        "bounds": {k: {"min": v[1], "max": v[2]} for k, v in SPEC.items()},
        "overridden": {k: cfg[k] != DEFAULTS[k] for k in DEFAULTS},
        "presets": [
            {
                "name": name,
                "label": p["label"],
                "blurb": p["blurb"],
                "rr_market_min": p["rr_market_min"],
                "rr_hi_rr": p["rr_hi_rr"],
                "below_measured_floor": p.get("below_measured_floor", False),
                "active": active_preset() == name,
            }
            for name, p in PRESETS.items()
        ],
        "active_preset": active_preset(),
        "labels": {
            "rr_market_min": "Actionable R:R (needs-you + ENTRY push)",
            "rr_hi_rr": "HI-RR tag threshold",
        },
        "hints": {
            "rr_market_min": (
                "Default 2.0, the measured at-market floor. Lowering this puts unmeasured setups "
                "back in the work queue -- it does not make them better."
            ),
            "rr_hi_rr": "Tags a row HI-RR once at-market R:R clears this. Must stay above the floor above.",
        },
        "not_configurable": {
            "stop_atr_floor": "0.7 ATR -- corpus median stop width, not a preference",
            "pb_required_for_entry_push": (
                "PB funnel is the only gate that measured positive era-stable; R:R tier is not "
                "a substitute for it"
            ),
            "max_plausible_r": "20 -- unit-error guard on intraday R scoring",
        },
    }