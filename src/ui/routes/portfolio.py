"""
Schwab Portfolio endpoints: summary, positions, and live sync.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger("ui_server")
router = APIRouter(prefix="/api/schwab/portfolio", tags=["portfolio"])


@router.get("/summary")
def get_schwab_portfolio_summary_endpoint():
    """Retrieve aggregate summary cards, total liquidation value, and account breakdown."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_summary
        return get_portfolio_summary()
    except Exception as e:
        logger.error(f"Error fetching Schwab portfolio summary: {e}")
        return {"error": str(e), "total_liquidation_value": 0.0, "accounts": []}


@router.get("/positions")
def get_schwab_portfolio_positions_endpoint(
    account: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
):
    """Query Schwab positions with account, asset type, and search filtering."""
    try:
        from src.tracking.schwab_portfolio_manager import get_portfolio_positions
        positions = get_portfolio_positions(
            account_id=account,
            asset_type=asset_type,
            search=search,
            sort_by=sort,
        )
        return {"positions": positions, "count": len(positions)}
    except Exception as e:
        logger.error(f"Error fetching Schwab positions: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


@router.post("/sync")
def sync_schwab_portfolio_endpoint():
    """Trigger live synchronization of Schwab positions via API and persist to SQLite."""
    try:
        from src.tracking.schwab_portfolio_manager import sync_schwab_positions
        res = sync_schwab_positions()
        return res
    except Exception as e:
        logger.error(f"Error syncing Schwab portfolio: {e}")
        return {"success": False, "error": str(e)}
