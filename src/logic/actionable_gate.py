"""
Single actionable gate for long setups.

A row passes only if ALL of:
  - long_in_zone == 1
  - Long RR At Market >= rr_config.min_rr() (unrounded comparison)
  - stop width (entry - stop) / ATR >= STOP_ATR_MIN (0.7)
  - Signal Pack bit 5 (PB funnel) set
  - Fade off: Signal Pack bit 2 set (Pine NOT-fade, i.e. fade_long == 0.0)
  - Action code not in (17, 18) (PARABOLIC / TOXIC RISK)
  - Ext Z Self Relative < 2.5
  - Long side

Any missing or failing field produces a reason string.  The caller uses the boolean
to decide whether the row is actionable; the reasons are surfaced in the UI so the
reader sees exactly why a setup did not clear.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from src.tracking import rr_config

logger = logging.getLogger(__name__)

# Re-export for callers that want the constant without importing rr_config.
STOP_ATR_MIN: float = rr_config.STOP_ATR_MIN


def is_actionable(dw: Dict[str, Any], plan: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Return (passes, reasons_for_failure).

    `dw` is the parsed data-window dict (output of ``parse_data_window``).
    `plan` is the side plan dict (output of ``_plan`` or equivalent).
    """
    fails: List[str] = []

    # Long side only.
    side = plan.get("side") if isinstance(plan, dict) else None
    if side != "long":
        fails.append(f"not a long plan (side={side!r})")

    # in-zone bit.
    in_zone = dw.get("long_in_zone")
    if in_zone != 1:
        fails.append("not in the long zone")

    # Long RR At Market >= min_rr (unrounded).
    rr_at_market = dw.get("long_rr_at_market")
    if rr_at_market is None:
        fails.append("long_rr_at_market missing")
    else:
        floor = rr_config.min_rr()
        if rr_at_market < floor:
            fails.append(f"RR@mkt {rr_at_market} < {floor} floor")

    # Stop width (entry - stop) / ATR >= STOP_ATR_MIN.
    # ``entry`` is the planned entry price; when the caller does not populate it,
    # fall back to the signal close so the width check still has a reference.
    entry = plan.get("entry") if isinstance(plan, dict) else None
    if entry is None:
        entry = dw.get("price")
    stop = dw.get("long_stop_loss")
    atr = dw.get("atr14")
    if entry is None or stop is None or atr is None:
        fails.append("missing entry/stop/ATR for stop-width check")
    elif atr <= 0:
        fails.append("ATR <= 0")
    else:
        width = (entry - stop) / atr
        if width < STOP_ATR_MIN:
            fails.append(f"stop width {width:.3f} ATR < {STOP_ATR_MIN} floor")

    # PB bit (Signal Pack bit 5).
    sig_pack = dw.get("signal_pack")
    if sig_pack is None:
        fails.append("signal_pack missing")
    else:
        try:
            m = int(round(float(sig_pack)))
        except (TypeError, ValueError):
            fails.append("signal_pack unparseable")
        else:
            if not (m & 32):
                fails.append("not PB funnel")

    # Fade off: Signal Pack bit 2 set (Pine NOT-fade, i.e. fade_long == 0.0).
    # bit 2 == 0 -> fade active (bad); bit 2 == 1 -> fade off (good).
    # parse_data_window stores fade_long = 0.0 when bit 2 is set.
    fade_long = dw.get("fade_long")
    if fade_long is None:
        fails.append("fade_long missing")
    elif fade_long:
        fails.append("fade gate active (do not chase)")

    # Action codes 17/18 off.
    action_code_raw = dw.get("action_long")
    if action_code_raw is None:
        fails.append("action_long missing")
    else:
        try:
            act_code = int(round(float(action_code_raw)))
        except (TypeError, ValueError):
            fails.append("action_long unparseable")
        else:
            if act_code in (17, 18):
                fails.append(f"action code {act_code} (PARABOLIC/TOXIC)")

    # Ext Z < 2.5.
    ext_z = dw.get("ext_z_self")
    if ext_z is None:
        fails.append("ext_z_self missing")
    elif ext_z >= 2.5:
        fails.append(f"ext_z_self {ext_z} >= 2.5")

    return (not fails), fails
