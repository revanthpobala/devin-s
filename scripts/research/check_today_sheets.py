"""Read-only: show what the Alerts and Trades sheets have for today."""
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tracking.sheets_tracker import SheetsTracker

TODAY = "2026-08-26"


def show(ws, max_rows=100):
    rows = ws.get_all_values()
    if not rows:
        print(f"  (empty)")
        return
    header = rows[0]
    print(f"  cols: {len(header)}")
    print(f"  header: {header}")
    for r in rows[1:max_rows + 1]:
        # Trim long cells
        trimmed = [c[:40] + "..." if len(c) > 43 else c for c in r]
        print(f"  {trimmed}")
    if len(rows) - 1 > max_rows:
        print(f"  ... {len(rows) - 1 - max_rows} more rows")


def main():
    st = SheetsTracker()
    if not st.connect():
        print("Could not connect to Google Sheets")
        sys.exit(1)

    print("=== Alerts spreadsheet ===")
    print(f"Title: {st.sheet.title}")
    print(f"Tabs: {[s.title for s in st.sheet.worksheets()]}")
    ws = st.sheet.worksheet(TODAY)
    print(f"\n--- Alerts tab '{TODAY}' ---")
    show(ws)

    print("\n=== Trades spreadsheet ===")
    print(f"Title: {st.trades_sheet.title}")
    print(f"Tabs: {[s.title for s in st.trades_sheet.worksheets()]}")
    tws = st.trades_sheet.worksheet(TODAY)
    print(f"\n--- Trades tab '{TODAY}' ---")
    show(tws)


if __name__ == "__main__":
    main()
