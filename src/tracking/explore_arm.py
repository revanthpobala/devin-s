"""Exploration arm: a small random daily sample of gate-blocked names sent through paid deep research anyway.

Names the gates would skip never get a judge verdict, so nothing can tell whether the gates or the judge add value.
The sample is recorded in data/triage/<date>/explore.json; the deep-research pipeline bypasses its pre-gates for them.
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict

from src import config

PICK_RATE = 0.15


def _file(date_str: str):
    return config.BASE_DIR / "data" / "triage" / date_str / "explore.json"


def members(date_str: str) -> set:
    try:
        return {str(t).upper() for t in json.loads(_file(date_str).read_text(encoding="utf-8"))}
    except Exception:
        return set()


def has_plan(triage: Dict[str, Any]) -> bool:
    p = (triage or {}).get("long_plan") or {}
    return bool(all(v is not None for v in (p.get("zone") or [None])) and p.get("stop") is not None and p.get("target") is not None)


def pick(symbol: str, date_str: str, triage: Dict[str, Any]) -> bool:
    """True when `symbol` is in today's exploration sample (already, or newly drawn while the daily budget lasts)."""
    sym = symbol.upper()
    cur = members(date_str)
    if sym in cur:
        return True
    if config.EXPLORE_SAMPLE_N <= 0 or len(cur) >= config.EXPLORE_SAMPLE_N:
        return False
    if (triage or {}).get("triage") not in ("PASS", "WATCH") or not has_plan(triage):
        return False
    if random.Random(f"{date_str}{sym}").random() >= PICK_RATE:
        return False
    cur.add(sym)
    f = _file(date_str)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(cur)), encoding="utf-8")
    return True
