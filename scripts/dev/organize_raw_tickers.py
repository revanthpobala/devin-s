"""
scripts/dev/organize_raw_tickers.py

Organizes flat {TICKER}_* files inside data/raw/<date>/ into per-ticker subdirectories:
data/raw/<date>/<TICKER>/
"""

import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"

def organize_raw():
    if not RAW_DIR.exists():
        print("data/raw does not exist.")
        return

    for date_dir in sorted(RAW_DIR.iterdir()):
        if not date_dir.is_dir() or date_dir.name.startswith("."):
            continue

        print(f"Processing {date_dir.name}...")
        for item in list(date_dir.iterdir()):
            if item.is_dir():
                continue
            if item.name in ("survivors.json", "manifest.json", "_failures.csv"):
                continue

            # Extract ticker prefix (e.g. AAPL from AAPL_chart.png, NASDAQ_NVDA from NASDAQ_NVDA_chart.png)
            parts = item.name.split("_")
            if len(parts) >= 2:
                ticker = parts[0]
                # If ticker is an exchange prefix like NASDAQ, BATS, NYSE, join with symbol
                if ticker in ("NASDAQ", "BATS", "NYSE", "AMEX", "OTC") and len(parts) >= 3:
                    ticker = f"{parts[0]}_{parts[1]}"
                
                ticker_dir = date_dir / ticker
                ticker_dir.mkdir(parents=True, exist_ok=True)
                dest = ticker_dir / item.name
                shutil.move(str(item), str(dest))
                print(f"  Moved {item.name} -> {ticker}/{item.name}")

if __name__ == "__main__":
    organize_raw()
