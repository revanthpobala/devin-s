"""Static slim bible for the Pass 2 system prompt.

Sections that explain how the indicator is built (architecture, clustering math, opacity, formulas, verification log)
do not help read a chart or Data Window. Dropping them is a fixed cut, identical on every run, so llama.cpp's prefix
cache still hits. Per-ticker retrieval would change the prompt each run and throw that cache away.
"""

from __future__ import annotations

import os
import re

DROP_HEADINGS = (
    "Table of Contents",
    "1. System Architecture",
    "5. Clustering Algorithm",
    "7. Visual Opacity Logic",
    "15. Appendix: Formula Reference",
    "Verification Record",
)


def slim_bible(text: str) -> str:
    if os.getenv("BIBLE_FULL", "").strip() in ("1", "true", "yes"):
        return text
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p for p in parts if not any(p.startswith(f"## {h}") for h in DROP_HEADINGS)]
    return "".join(keep)


# Router: sections every read needs, plus sections that apply only when the Data Window shows their trigger.
ALWAYS_SECTIONS = (3, 4, 6, 8, 10, 12, 13, 14, 18)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def route_sections(dw: dict) -> set:
    """Bible section numbers the Data Window calls for, beyond ALWAYS_SECTIONS. Deterministic, no LLM."""
    from src.logic.actionable_gate import gate_inputs_from_datawindow

    g = gate_inputs_from_datawindow(dw or {})
    extra = set()
    action = g.get("action_long")
    ext_z = g.get("ext_z_self")
    rr = g.get("long_rr_at_market")
    fade = bool(g.get("fade_long"))
    if (_num((dw or {}).get("Long Rev Zone")) or 0) >= 1 or action == 20:
        extra.add(9)
    if g.get("long_in_zone") or fade or (rr is not None and rr >= 2) or (ext_z is not None and ext_z >= 1.5) \
            or action in (11, 12, 16, 17, 20):
        extra.add(16)
    if fade or (ext_z is not None and ext_z >= 1.5) or (_num((dw or {}).get("Energy IV Rank Pct")) or 0) >= 50:
        extra.add(17)
    if fade or (ext_z is not None and ext_z >= 1.5) or (g.get("long_in_zone") and rr is not None and rr < 2):
        extra.add(11)
    return extra


def route_bible(text: str, dw: dict) -> str:
    """Verbatim bible sections for this ticker: the always-on core plus the triggered ones.
    BIBLE_FULL=1 returns everything; BIBLE_ROUTE=0 falls back to the fixed slim cut."""
    if os.getenv("BIBLE_FULL", "").strip() in ("1", "true", "yes"):
        return text
    if os.getenv("BIBLE_ROUTE", "1").strip() in ("0", "false", "no"):
        return slim_bible(text)
    want = set(ALWAYS_SECTIONS) | route_sections(dw)
    out = []
    for part in re.split(r"(?m)^(?=## )", text):
        m = re.match(r"## (\d+)\.", part)
        if m is None and part.startswith("## "):
            continue
        if m is None or int(m.group(1)) in want:
            out.append(part)
    return "".join(out)
