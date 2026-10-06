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


def gate_inputs_from_datawindow(dw: Dict[str, Any]) -> Dict[str, Any]:
    """Map parsed or raw Data Window fields to canonical gate input keys.

    Keys returned:
      - atr14: float | None
      - fade_long: float | None (0.0 = fade off, 1.0 = fade active)
      - long_in_zone: float | None (1.0 = in zone, 0.0 = not in zone)
      - signal_pack: float | None
      - action_long: float | None
      - ext_z_self: float | None
      - price: float | None
      - long_rr_at_market: float | None
      - long_stop_loss: float | None

    Fails closed: missing fields return None, never synthetic defaults.
    """
    if not isinstance(dw, dict):
        return {
            "atr14": None,
            "fade_long": None,
            "long_in_zone": None,
            "signal_pack": None,
            "action_long": None,
            "ext_z_self": None,
            "price": None,
            "long_rr_at_market": None,
            "long_stop_loss": None,
        }

    def _to_float(val: Any) -> Optional[float]:
        if val is None or val == "" or val == "N/A" or val == "null":
            return None
        try:
            s = str(val).replace(",", "").replace("$", "").strip()
            return float(s)
        except (ValueError, TypeError):
            return None

    def _lookup(*needles: str) -> Optional[Any]:
        for n in needles:
            if n in dw and dw[n] is not None:
                return dw[n]
        norm_map = {str(k).lower().replace(" ", "").replace("_", ""): v for k, v in dw.items()}
        for n in needles:
            nk = n.lower().replace(" ", "").replace("_", "")
            if nk in norm_map and norm_map[nk] is not None:
                return norm_map[nk]
        return None

    # 1. atr14 (from rsi2_atr14, RSI2 ATR14, atr14, ATR14, or computed ATR14)
    atr14_raw = _lookup("rsi2_atr14", "RSI2 ATR14", "atr14", "ATR14", "ATR 14", "atr_14", "atr_at_signal", "atr")
    atr14 = _to_float(atr14_raw)

    # 2. signal_pack
    sig_pack_raw = _lookup("signal_pack", "Signal Pack", "signal_pack_num")
    signal_pack = _to_float(sig_pack_raw)

    # 3. fade_long (explicit in dw, or Signal Pack bit 2 inverted)
    # Pine exports 'not fadeZoneLong ? 4 : 0'. Bit 2 (4) set means NOT-fade (fade off -> 0.0).
    # Bit 2 unset means fade ACTIVE (fade on -> 1.0).
    fade_long: Optional[float] = None
    if "fade_long" in dw and dw["fade_long"] is not None:
        val = dw["fade_long"]
        fade_long = 1.0 if val in (1, 1.0, True, "1", "true", "True") else (0.0 if val in (0, 0.0, False, "0", "false", "False") else None)
    elif "fade_gate" in dw and dw["fade_gate"] is not None:
        val = dw["fade_gate"]
        fade_long = 1.0 if val in (1, 1.0, True, "1", "true", "True") else (0.0 if val in (0, 0.0, False, "0", "false", "False") else None)
    elif signal_pack is not None:
        try:
            m = int(round(signal_pack))
            fade_long = 0.0 if (m & 4) else 1.0
        except Exception:
            fade_long = None

    # 4. long_in_zone (Zone RR Flags Pack bit 0)
    # Bit 0 (1) set means long_in_zone == 1.0
    long_in_zone: Optional[float] = None
    zr_flags_raw = _lookup("zone_rr_flags", "Zone RR Flags Pack", "zone_rr_flags_pack")
    if zr_flags_raw is not None:
        zr_num = _to_float(zr_flags_raw)
        if zr_num is not None:
            try:
                m_zr = int(round(zr_num))
                long_in_zone = 1.0 if (m_zr & 1) else 0.0
            except Exception:
                long_in_zone = None
    if long_in_zone is None:
        iz_raw = _lookup("long_in_zone", "Long in Zone", "in_zone")
        if iz_raw is not None:
            if iz_raw in (1, 1.0, True, "1", "1.0", "true", "True"):
                long_in_zone = 1.0
            elif iz_raw in (0, 0.0, False, "0", "0.0", "false", "False"):
                long_in_zone = 0.0

    # 5. action_long (action_long or Action Long Code or Context Action Pack % 32)
    act_raw = _lookup("action_long", "Action Long Code", "action_code")
    action_long = _to_float(act_raw)
    if action_long is None:
        cap_raw = _lookup("context_action_pack", "Context Action Pack")
        if cap_raw is not None:
            cap_num = _to_float(cap_raw)
            if cap_num is not None:
                try:
                    action_long = float(int(round(cap_num)) % 32)
                except Exception:
                    action_long = None

    # 6. ext_z_self
    ext_z_raw = _lookup("ext_z_self", "Ext Z Self Relative", "ext_z", "ext_z_self_relative")
    ext_z_self = _to_float(ext_z_raw)

    # 7. price
    px_raw = _lookup("price", "close", "Close", "last_price", "spot")
    price = _to_float(px_raw)

    # 8. long_rr_at_market
    rr_mkt_raw = _lookup("long_rr_at_market", "Long RR At Market", "rr_at_market", "rr_at_market_at_signal")
    long_rr_at_market = _to_float(rr_mkt_raw)

    # 9. long_stop_loss
    stop_raw = _lookup("long_stop_loss", "Long Stop Loss", "tactical_stop", "stop")
    long_stop_loss = _to_float(stop_raw)

    return {
        "atr14": atr14,
        "fade_long": fade_long,
        "long_in_zone": long_in_zone,
        "signal_pack": signal_pack,
        "action_long": action_long,
        "ext_z_self": ext_z_self,
        "price": price,
        "long_rr_at_market": long_rr_at_market,
        "long_stop_loss": long_stop_loss,
    }


def is_actionable(dw: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """Return (passes, reasons_for_failure).

    `dw` is either normalized gate inputs or the parsed data-window dict.
    `plan` is the side plan dict (output of ``_plan`` or equivalent).
    Fails closed: missing fields emit named rejection reasons; no defaults anywhere.
    """
    g = gate_inputs_from_datawindow(dw)
    fails: List[str] = []
    plan_dict = plan if isinstance(plan, dict) else {}

    # Long side only.
    side = plan_dict.get("side") or dw.get("side")
    if not side:
        fails.append("missing side")
    elif str(side).lower() != "long":
        fails.append(f"not a long plan (side={side!r})")

    # in-zone bit.
    in_zone = g.get("long_in_zone")
    if in_zone is None:
        fails.append("long_in_zone missing")
    elif in_zone != 1 and in_zone != 1.0:
        fails.append("not in the long zone")

    # Long RR At Market >= min_rr (unrounded).
    rr_at_market = g.get("long_rr_at_market")
    if rr_at_market is None:
        fails.append("long_rr_at_market missing")
    else:
        floor = rr_config.min_rr()
        if rr_at_market < floor:
            fails.append(f"RR@mkt {rr_at_market} < {floor} floor")

    # Stop width (entry - stop) / ATR >= STOP_ATR_MIN.
    entry = plan_dict.get("entry") if plan_dict.get("entry") is not None else g.get("price")
    stop = g.get("long_stop_loss")
    atr = g.get("atr14")

    if entry is None:
        fails.append("price missing for stop-width check")
    if stop is None:
        fails.append("long_stop_loss missing for stop-width check")
    if atr is None:
        fails.append("atr14 missing for stop-width check")
    elif atr <= 0:
        fails.append("ATR <= 0")

    if entry is not None and stop is not None and atr is not None and atr > 0:
        width = (entry - stop) / atr
        if width < STOP_ATR_MIN:
            fails.append(f"stop width {width:.3f} ATR < {STOP_ATR_MIN} floor")

    # PB bit (Signal Pack bit 5).
    sig_pack = g.get("signal_pack")
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
    fade_long = g.get("fade_long")
    if fade_long is None:
        fails.append("fade_long missing")
    elif fade_long:
        fails.append("fade gate active (do not chase)")

    # Action codes 17/18 off.
    action_code_raw = g.get("action_long")
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
    ext_z = g.get("ext_z_self")
    if ext_z is None:
        fails.append("ext_z_self missing")
    elif ext_z >= 2.5:
        fails.append(f"ext_z_self {ext_z} >= 2.5")

    return (not fails), fails
