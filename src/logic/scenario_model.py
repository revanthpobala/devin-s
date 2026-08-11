"""
src/logic/scenario_model.py

Deterministic scenario model for exact number generation.
Rolls MAs one step and derives zone/stop/target based on candidate prices.
"""

from typing import Any, Dict, List, Optional
from src.logic.trigger_gaps import LANE_EDGE_MAP

def build_scenario(
    dw_row: Dict[str, Any],
    price_series: Optional[List[float]] = None,
    candidate_prices: Optional[List[float]] = None
) -> List[Dict[str, Any]]:
    """
    Project one-step technical scenarios for given candidate prices.
    
    Args:
        dw_row: Parsed Data Window row/dict containing current static levels.
        price_series: Optional list of recent prices (used for MA approximations).
        candidate_prices: List of prices to project scenarios for.
        
    Returns:
        List of dicts representing the deterministic scenario for each candidate price.
    """
    if candidate_prices is None:
        candidate_prices = []
        
    scenarios = []
    
    def _safe_float(key: str) -> float:
        val = dw_row.get(key)
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    # Extract base static levels
    ma50_base = _safe_float("ma50")
    ma200_base = _safe_float("ma200")
    ztop = _safe_float("long_target") # approx
    zbot = _safe_float("long_stop_loss") # approx

    for P in candidate_prices:
        # Approximate MA roll: EMA update ema += k*(P-ema)
        k50 = 2 / (50 + 1)
        k200 = 2 / (200 + 1)
        
        ma50_proj = ma50_base + k50 * (P - ma50_base) if ma50_base else P
        ma200_proj = ma200_base + k200 * (P - ma200_base) if ma200_base else P
        
        # Compute extension %
        ext_pct = ((P - ma200_proj) / ma200_proj * 100) if ma200_proj else 0.0
        
        # Classify lane
        if ma200_proj and P < ma200_proj:
            lane = "reversal"
            lane_edge = LANE_EDGE_MAP.get("code20_reversal", "")
        elif ma50_proj and P > ma50_proj * 1.1: # Approximate 52wH logic or high extension
            lane = "breakout-chase"
            lane_edge = "flat/negative"
        else:
            lane = "pullback-flat"
            lane_edge = LANE_EDGE_MAP.get("stage2_prime", "")
            
        scenario = {
            "candidate_price": round(P, 2),
            "ma50_proj": round(ma50_proj, 2),
            "ma200_proj": round(ma200_proj, 2),
            "ext_pct": round(ext_pct, 2),
            "lane": lane,
            "lane_edge": lane_edge,
            "zone_top": round(ztop, 2) if ztop else None,
            "zone_bot": round(zbot, 2) if zbot else None,
        }
        scenarios.append(scenario)
        
    return scenarios
