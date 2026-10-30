"""Per-ticker target position sizes and broker-held share counts.

Targets live in data/position_targets.json, e.g. {"GOOGL": 200}. A held name with a target gets
its layered entry sized to the gap (src.logic.layered_entry.cap_layers_to_position); at or above
target it gets "no adds". Held shares come from the synced Schwab positions (EQUITY rows, all
accounts). Unreadable sources return None, so sizing falls back to the normal fresh-trade plan.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from src import config

logger = logging.getLogger(__name__)


def _path():
    return config.BASE_DIR / "data" / "position_targets.json"


def get_targets() -> Dict[str, float]:
    p = _path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {str(k).upper(): float(v) for k, v in raw.items() if float(v) > 0}
    except Exception as e:
        logger.warning(f"position_targets.json unreadable: {e}")
        return {}


def get_target(ticker: str) -> Optional[float]:
    return get_targets().get(str(ticker or "").upper())


def held_shares(ticker: str) -> Optional[float]:
    """Total equity shares held at the broker across accounts; None when unknown."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions

        rows = get_portfolio_positions(asset_type="EQUITY") or []
    except Exception as e:
        logger.debug(f"held_shares({ticker}) unavailable: {e}")
        return None
    t = str(ticker or "").upper()
    qty = sum(float(r.get("quantity") or 0) for r in rows
              if str(r.get("symbol") or r.get("underlying_symbol") or "").upper() == t)
    return qty if qty > 0 else 0.0
