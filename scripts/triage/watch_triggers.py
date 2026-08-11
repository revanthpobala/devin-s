import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from src import config
from src.logic.trigger_gaps import compute_triggers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def load_triage_triggers(date_str: str) -> Dict[str, Any]:
    """Load all triggers for a specific date from triage JSONs."""
    triage_dir = config.BASE_DIR / "data" / "triage" / date_str
    
    if not triage_dir.exists():
        logger.warning(f"Triage directory not found: {triage_dir}")
        return {}

    triggers_data = {}
    
    # Check all _thesis.json files in the triage directory and subdirectories
    for json_file in triage_dir.rglob("*_thesis.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            ticker = data.get("ticker")
            if not ticker:
                continue
                
            triage_record = data.get("triage", {})
            triggers = triage_record.get("triggers")
            
            if triggers:
                triggers_data[ticker] = triggers
                
        except Exception as e:
            logger.warning(f"Failed to read {json_file}: {e}")
            
    return triggers_data

def display_near_triggers(triggers_data: Dict[str, Any], max_open: int = 1):
    """Display tickers that are close to triggering (open_count <= max_open)."""
    
    near_hits = []
    
    for ticker, triggers in triggers_data.items():
        if triggers.get("open_gates_count") is not None and triggers.get("open_gates_count") <= max_open:
            if not triggers.get("hard_exclusions"):
                near_hits.append((ticker, triggers))
                
    if not near_hits:
        print(f"\nNo tickers with {max_open} or fewer open gates found.")
        return
        
    print(f"\n=== {len(near_hits)} TICKERS NEAR TRIGGER (<= {max_open} OPEN GATES) ===")
    
    # Sort by open count (fewest first)
    near_hits.sort(key=lambda x: x[1].get("open_gates_count", 99))
    
    for ticker, triggers in near_hits:
        nearest = triggers.get("nearest_actionable_state", "unknown")
        open_count = triggers.get("open_gates_count", "?")
        
        print(f"\n[ {ticker} ] -> {nearest.upper()} (Gates Open: {open_count})")
        
        state_data = triggers.get(nearest, {})
        for gate in state_data.get("open_gates", []):
            gap_str = f"{gate['gap']:.2f}" if gate['gap'] is not None else "?"
            print(f"    - OPEN: {gate['name']} (Need {gate['comparator']} {gate['ref_level']} | Gap: {gap_str})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watch and query the Buy-Trigger Gap Engine outputs.")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="Target date (YYYY-MM-DD) to query triage triggers.")
    parser.add_argument("--max-open", type=int, default=2,
                        help="Maximum number of open gates to consider a ticker 'near trigger'.")
    
    args = parser.parse_args()
    
    triggers = load_triage_triggers(args.date)
    logger.info(f"Loaded trigger data for {len(triggers)} tickers.")
    
    display_near_triggers(triggers, max_open=args.max_open)
