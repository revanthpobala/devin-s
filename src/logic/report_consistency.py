"""Cross-check the written research against the levels actually stored, and stamp the binding levels on the report."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_NUM = r"\$?\s?(\d{1,5}(?:,\d{3})*(?:\.\d+)?)"
_MARK = "<!-- binding-levels -->"


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _labelled(text: str, label: str, lo: float, hi: float) -> List[float]:
    vals = []
    for m in re.finditer(label + r"[^\n$\d|]{0,25}" + _NUM, text, re.I):
        v = float(m.group(1).replace(",", ""))
        if lo <= v <= hi:
            vals.append(v)
    return vals


def held_option_strikes(position_block: str) -> List[float]:
    line = next((l for l in (position_block or "").splitlines() if "Options on this name" in l), "")
    return [int(m.group(1)) / 1000 for m in re.finditer(r"\b\d{6}[CP](\d{8})\b", line)]


def check(text: str, watch_data: Dict[str, Any], dw: Dict[str, Any], spot: float, position_block: str = "") -> List[str]:
    sp = watch_data.get("shares_plan") or {}
    stop, t1 = _f(sp.get("tactical_stop")), _f(sp.get("target_1"))
    tol = max(0.0025 * spot, 0.05 * _f(watch_data.get("atr14")), 0.02)
    issues: List[str] = []
    if spot > 0 and stop > 0:
        allowed = [stop, _f(sp.get("breakout_stop")), _f(dw.get("RSI2 Fixed Stop"))]
        bad = sorted({round(v, 2) for v in _labelled(text, r"\bstop(?:[- ]loss)?\b", 0.5 * spot, spot)
                      if not any(a > 0 and abs(v - a) <= tol for a in allowed)})
        if bad:
            issues.append(f"report names stop(s) {', '.join(f'${v:g}' for v in bad)}; stored stop is ${stop:g}")
    if spot > 0 and t1 > 0:
        ok = [t1, _f(sp.get("breakout_target_1"))]
        bad = sorted({round(v, 2) for v in _labelled(text, r"\b(?:T1|Target 1|target_1)\b", 0.9 * spot, 2 * spot)
                      if not any(a > 0 and abs(v - a) <= tol for a in ok)})
        if bad:
            issues.append(f"report names T1 {', '.join(f'${v:g}' for v in bad)}; stored T1 is ${t1:g}")
    # One stop per plan: the sizing stop (tactical_stop) and the invalidation level must agree.
    # If invalidation sits BELOW the stop, the plan's real risk is larger than the share count implies
    # (NOW: stop $103.86 for sizing, but invalidation $99.50 is where the thesis actually breaks).
    inv_level = _f((watch_data.get("invalidation") or {}).get("price_level"))
    if stop > 0 and 0 < inv_level < stop - tol:
        issues.append(f"sizing stop ${stop:g} is above the invalidation ${inv_level:g}: shares are sized to a "
                      f"tighter stop than where the thesis breaks (real risk is larger)")
    for k in held_option_strikes(position_block):
        if not re.search(rf"(?<![\d.]){re.escape(f'{k:g}')}(?:\.0+)?(?!\.?\d)", text):
            issues.append(f"held ${k:g} option never addressed")
    opts = [watch_data.get("options_plan") or {}] + list((watch_data.get("options_menu") or []) if isinstance(watch_data.get("options_menu"), list) else [])
    em, er = _f(dw.get("Exp Move Pct 21b")), str(watch_data.get("next_earnings_date") or "")[:10]
    for o in opts:
        if str(o.get("structure") or "").upper() != "COVERED_CALL":
            continue
        k, exp = _f(o.get("short_strike")), str(o.get("expiration") or "")[:10]
        if k and em and spot and er and exp and er <= exp and k < spot * (1 + em / 100):
            issues.append(f"covered call ${k:g} is inside the 21-bar expected move (${spot * (1 + em / 100):.2f}) with earnings {er} before expiry {exp}")
    return list(dict.fromkeys(issues))


_NON_ACTIONABLE_VERDICTS = ("STALK", "WATCH", "HOLD", "MANAGE", "CASH_SKIP", "NO_LEVELS", "AVOID", "REJECT")


def _plan_not_actionable_now(watch_data: Dict[str, Any]) -> bool:
    """A layered share count must not read as 'buy now' when the plan is not actionable at market.

    True when the level gate rejected the plan, the verdict is a stalk/watch/hold state, or the
    research itself flagged no entry at market (R:R below the layering floor with no starter tranche).
    """
    if watch_data.get("level_gate_rejected"):
        return True
    if not watch_data.get("actionable", True):
        return True
    v = str(watch_data.get("verdict") or "").upper()
    if any(w in v for w in _NON_ACTIONABLE_VERDICTS):
        return True
    lay = (watch_data.get("shares_plan") or {}).get("layers") or {}
    if lay.get("mode") in ("LIMITS_ONLY", "NO_SIZE"):
        return True  # no marketable starter -> nothing to buy at market right now
    return False


def _layers_disclaimer(watch_data: Dict[str, Any]) -> str:
    if watch_data.get("level_gate_rejected"):
        return " *(display only — level gate rejected this plan)*"
    if _plan_not_actionable_now(watch_data):
        return " *(not actionable at market now — resting limits / stalk only; see verdict)*"
    return ""


def binding_block(watch_data: Dict[str, Any], issues: List[str], dw: Optional[Dict[str, Any]] = None) -> str:
    sp = watch_data.get("shares_plan") or {}
    parts = []
    if _f(sp.get("breakout_level")):
        parts.append(f"breakout {_f(sp.get('breakout_level')):g}")
    if _f(sp.get("entry_zone_high")):
        parts.append(f"entry {_f(sp.get('entry_zone_low')):g}–{_f(sp.get('entry_zone_high')):g}" + (" (next open only)" if sp.get("entry_type") == "NEXT_OPEN" else ""))
    dual = _f(sp.get("breakout_target_1")) > 0
    for k, lab in (("tactical_stop", "stop"), ("target_1", "zone T1" if dual else "T1"), ("target_2", "zone T2" if dual else "T2")):
        if _f(sp.get(k)):
            parts.append(f"{lab} {_f(sp.get(k)):g}")
    for k, lab in (("breakout_target_1", "breakout T1"), ("breakout_target_2", "breakout T2")):
        if _f(sp.get(k)):
            parts.append(f"{lab} {_f(sp.get(k)):g}")
    if sp.get("exit_rule"):
        parts.append(f"exit: {sp['exit_rule']}")
    lines = [_MARK, f"> **Binding levels** ({watch_data.get('levels_source') or 'llm'}; these drive alerts and tickets): " + (" · ".join(parts) or "none")]
    if isinstance(sp.get("layers"), dict):
        from src.logic.layered_entry import layers_line

        lines.append("> " + layers_line(sp["layers"]) + _layers_disclaimer(watch_data))
    if issues:
        lines.append("> ⚠️ **Report text disagrees — trust the binding levels:**")
        lines += [f"> - {i}" for i in issues]
    return "\n".join(lines + [_MARK])


def stamp(path: Path, block: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if _MARK in text:
        a, rest = text.split(_MARK, 1)
        text = a + rest.split(_MARK, 1)[-1].lstrip("\n")
    text_stripped = text.lstrip()
    if text_stripped.startswith("```"):
        path.write_text(f"{block}\n\n{text_stripped}", encoding="utf-8")
    else:
        head, sep, body = text.partition("\n")
        path.write_text(f"{head}\n\n{block}\n{sep}{body}" if sep else f"{text}\n\n{block}\n", encoding="utf-8")

