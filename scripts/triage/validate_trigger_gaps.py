import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
import glob
import os

from src import config
from src.logic.data_window_filter import parse_data_window
from src.logic.trigger_gaps import compute_triggers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def validate_triggers_on_corpus(date_str: str, max_open: int = 1):
    """Run the trigger gaps engine over a whole day's raw corpus to find near-misses
    that didn't get promoted to PASS/WATCH by the deterministic filter.
    """
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str
    
    if not raw_dir.exists():
        logger.error(f"Raw data directory not found: {raw_dir}")
        return
        
    json_files = glob.glob(str(raw_dir / "*_datawindow.json"))
    logger.info(f"Found {len(json_files)} data window files in {raw_dir}")
    
    near_hits = []
    
    for jf in json_files:
        ticker = Path(jf).name.replace("_datawindow.json", "")
        
        try:
            with open(jf, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                
            parsed = parse_data_window(raw_data)
            if not parsed:
                continue
                
            triggers = compute_triggers(parsed)
            if not triggers:
                continue
                
            if triggers.get("open_gates_count") is not None and triggers.get("open_gates_count") <= max_open:
                if not triggers.get("hard_exclusions"):
                    near_hits.append((ticker, triggers))
                    
        except Exception as e:
            logger.warning(f"Error processing {ticker}: {e}")
            
    if not near_hits:
        print(f"\nNo tickers with {max_open} or fewer open gates found in corpus.")
        return
        
    print(f"\n=== {len(near_hits)} TICKERS NEAR TRIGGER IN RAW CORPUS (<= {max_open} OPEN GATES) ===")
    
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
    parser = argparse.ArgumentParser(description="Validate trigger gaps against the raw scraped corpus.")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="Target date (YYYY-MM-DD)")
    parser.add_argument("--max-open", type=int, default=2,
                        help="Maximum open gates")
    
    args = parser.parse_args()
    validate_triggers_on_corpus(args.date, args.max_open)
