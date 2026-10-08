"""
forward_log.py — Forward logging registry for unmeasured trading lanes and mover triggers.

Enforces repo rule: No new threshold or lane ships as 'measured' without an empirical
corpus replay. Unmeasured lanes ship labeled UNMEASURED and get forward-logged with a frozen
ship date and require n >= 200 observed live instances before any edge claim.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

SHIP_DATE = "2026-10-08"
MIN_FORWARD_OBSERVATIONS = 200

# Registered unmeasured rules
UNMEASURED_RULES = {
    "MOMENTUM_BREAKOUT": {
        "ship_date": SHIP_DATE,
        "description": "Stage 2 momentum breakout above entry zone with momentum_rr >= 2.0 using tight_stop",
        "status": "UNMEASURED",
        "min_observations": MIN_FORWARD_OBSERVATIONS,
    },
    "COIL": {
        "ship_date": SHIP_DATE,
        "description": "Stage 1/2 base compression with low IV rank + squeeze + tight range (VST-type coil)",
        "status": "UNMEASURED",
        "min_observations": MIN_FORWARD_OBSERVATIONS,
    },
    "MOVER_TRIGGER": {
        "ship_date": SHIP_DATE,
        "description": "Live move >= 3.0% from dossier spot or zone-top break with volume pace > 1.5x avg",
        "status": "UNMEASURED",
        "min_observations": MIN_FORWARD_OBSERVATIONS,
    },
}


def get_forward_log_path() -> Path:
    log_dir = config.BASE_DIR / "data" / "tracking"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "forward_log_ledger.json"


def record_forward_observation(rule_name: str, ticker: str, data: Dict[str, Any]) -> None:
    """Record a live observation of an unmeasured lane or mover trigger."""
    rule_name = rule_name.upper()
    path = get_forward_log_path()
    ledger: Dict[str, List[Dict[str, Any]]] = {}

    if path.exists():
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            ledger = {}

    if rule_name not in ledger:
        ledger[rule_name] = []

    observation = {
        "ticker": ticker.upper(),
        "timestamp": datetime.now().isoformat(),
        "rule": rule_name,
        "ship_date": SHIP_DATE,
        "data": data,
    }
    ledger[rule_name].append(observation)

    try:
        path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Failed to persist forward observation for {ticker}: {e}")


def get_rule_forward_stats(rule_name: str) -> Dict[str, Any]:
    """Get observation count and qualification status for a rule."""
    rule_name = rule_name.upper()
    path = get_forward_log_path()
    count = 0

    if path.exists():
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
            count = len(ledger.get(rule_name, []))
        except Exception:
            count = 0

    return {
        "rule": rule_name,
        "ship_date": SHIP_DATE,
        "observations": count,
        "required": MIN_FORWARD_OBSERVATIONS,
        "is_measured": False,
        "tag": "UNMEASURED",
        "status": "UNMEASURED",
        "edge_claim_allowed": count >= MIN_FORWARD_OBSERVATIONS,
    }


def get_forward_summary() -> Dict[str, Any]:
    """Get forward log summary for all unmeasured rules."""
    return {
        "rules": {name: get_rule_forward_stats(name) for name in UNMEASURED_RULES},
        "ship_date": SHIP_DATE,
        "min_observations": MIN_FORWARD_OBSERVATIONS,
    }
