"""
scripts/research/test_dashboard_reconstruction.py

Sends the zoomed chart image, the exact Data Window JSON, and Section 4 of the bible
to the local vision LLM (Qwen3.8-27B) to reconstruct and output the complete 12-row on-chart dashboard.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.clients.llm_client import query_local_llm


def test_dashboard_reconstruction(ticker: str = "AMD", date_str: str = "2026-08-16"):
    ticker = ticker.upper()
    chart_dir = BASE_DIR / "data" / "raw" / date_str / ticker
    
    zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"
    dw_json_path = chart_dir / f"{ticker}_datawindow.json"

    if not zoom_chart.exists() or not dw_json_path.exists():
        chart_dir = BASE_DIR / "data" / "raw" / "2026-08-15" / ticker
        zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"
        dw_json_path = chart_dir / f"{ticker}_datawindow.json"

    if not zoom_chart.exists():
        print(f"Error: Zoom chart not found for {ticker}")
        return
    if not dw_json_path.exists():
        print(f"Error: Data Window JSON not found for {ticker}")
        return

    dw_data = json.loads(dw_json_path.read_text(encoding="utf-8"))
    
    # Load Bible Section 4 rules
    bible_path = BASE_DIR / "gems" / "revanth-bible.md"
    bible_text = bible_path.read_text(encoding="utf-8")
    
    # Extract Section 4 (Dashboard Fields)
    sec4 = ""
    if "## 4. Dashboard Fields" in bible_text:
        sec4 = bible_text.split("## 4. Dashboard Fields")[1].split("## 5.")[0]

    system_prompt = (
        "You are an elite quantitative charting engineer and Pine Script compiler expert. "
        "You are provided with: (1) A zoomed TradingView chart image, (2) The exact Data Window JSON values from the chart, "
        "and (3) The technical specification of the 12-row indicator dashboard from the TradingView strategy manual.\n\n"
        "Your task is to synthesize these inputs and perfectly reconstruct the exact 12-Row On-Chart Dashboard Table."
    )

    user_prompt = (
        f"Reconstruct the complete 12-Row On-Chart Indicator Dashboard for {ticker} ({date_str}).\n\n"
        f"### EXACT DATA WINDOW JSON:\n```json\n{json.dumps(dw_data, indent=2)}\n```\n\n"
        f"### DASHBOARD SPECIFICATION (From Technical Manual):\n{sec4}\n\n"
        "### INSTRUCTIONS:\n"
        "Reconstruct the 3-Column x 12-Row Dashboard Table in clean Markdown, showing the exact rendered text for each cell:\n"
        "- Left Column (Long Book)\n"
        "- Center Column (Metric / Label / Status)\n"
        "- Right Column (Short Book Levels)\n\n"
        "Map each Data Window field (Long Entry, Stop, Target, Stage, Buy/Sell Score, Energy, Rev Zone, DMI, Darvas Box) "
        "into its exact rendered cell string according to the manual rules."
    )

    print(f"[{ticker}] Sending Chart Image + Data Window JSON + Bible Rules to Local LLM...")
    response = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_paths=[str(zoom_chart)],
        use_tools=False,
        disable_thinking=True,
        max_tokens=4096,
        use_openrouter=False,
    )

    out_file = chart_dir / f"{ticker}_reconstructed_dashboard.md"
    out_file.write_text(response, encoding="utf-8")

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n" + "=" * 70)
        print(f"RECONSTRUCTED ON-CHART DASHBOARD ({ticker})")
        print("=" * 70)
        print(response)
    except Exception:
        print("\n" + "=" * 70)
        print(f"RECONSTRUCTED ON-CHART DASHBOARD ({ticker}) - (Saved to {out_file})")
        print("=" * 70)
    print(f"\nSaved reconstructed dashboard to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test dashboard reconstruction")
    parser.add_argument("ticker", nargs="?", default="AMD", help="Ticker symbol")
    parser.add_argument("--date", default="2026-08-16", help="Date folder")
    args = parser.parse_args()

    test_dashboard_reconstruction(args.ticker, args.date)
