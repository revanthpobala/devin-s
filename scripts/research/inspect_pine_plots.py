import re
import pandas as pd
from pathlib import Path

pine_path = Path("pine-script-indicators/revanth-enhanced-indicator.pine")
text = pine_path.read_text(encoding="utf-8")

plots = re.findall(r"plot(?:shape|char|candle)?\s*\([^,]+,\s*['\"]([^'\"]+)['\"]", text)
print(f"Total plots defined in Pine Script: {len(plots)}")
print("Plots defined in Pine:")
for p in plots:
    print(f"  • {p}")

# Also check AMZN_datawindow.csv headers
csv_path = Path("data/raw/2026-08-18/AMZN/AMZN_datawindow.csv")
if csv_path.exists():
    df = pd.read_csv(csv_path)
    print(f"\nTotal columns in CSV: {len(df.columns)}")
    print("First 15 CSV columns:", list(df.columns[:15]))
    pattern_cols = [c for c in df.columns if any(k in c.lower() for k in ["pin", "engulf", "inside", "pattern", "trap", "cross", "reversal", "gap", "breakout"])]
    print("\nPattern-related columns in CSV:")
    for c in pattern_cols:
        print(f"  • {c}")
