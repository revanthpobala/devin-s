"""
scripts/research/test_vision_targeted_levels.py

Tests the local Vision LLM (Qwen3.8-27B with mmproj) on TARGETED structural level reading
(overhead Key Resistances, Swing Highs, and Key Supports) and compares the visual detections
against the deterministic Python swing-peak calculations from the 1-year CSV.
"""

import argparse
import json
import os
import sys
from pathlib import Path
import pandas as pd

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.clients.llm_client import query_local_llm


def test_targeted_vision_and_python(ticker: str = "AMD", date_str: str = "2026-08-16"):
    ticker = ticker.upper()
    chart_dir = BASE_DIR / "data" / "raw" / date_str / ticker
    
    zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"
    csv_path = chart_dir / f"{ticker}_datawindow.csv"

    if not zoom_chart.exists() or not csv_path.exists():
        chart_dir = BASE_DIR / "data" / "raw" / "2026-08-15" / ticker
        zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"
        csv_path = chart_dir / f"{ticker}_datawindow.csv"

    if not zoom_chart.exists():
        print(f"Error: Zoom chart not found for {ticker}")
        return

    # 1. Deterministic Python Swing Analysis from 1-Year CSV
    df = pd.read_csv(csv_path)
    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")
    closes = pd.to_numeric(df["close"], errors="coerce")
    curr_c = closes.iloc[-1]

    # Calculate recent swing peaks (overhead resistances)
    recent_peaks = highs.tail(90)[(highs.tail(90) > highs.tail(90).shift(1)) & (highs.tail(90) > highs.tail(90).shift(-1))]
    overhead_peaks = sorted(list(set([round(float(p), 2) for p in recent_peaks if p > curr_c])))
    
    # Calculate recent swing troughs (underlying supports)
    recent_troughs = lows.tail(90)[(lows.tail(90) < lows.tail(90).shift(1)) & (lows.tail(90) < lows.tail(90).shift(-1))]
    underlying_supports = sorted(list(set([round(float(p), 2) for p in recent_troughs if p < curr_c])))

    max_52w = round(float(highs.max()), 2)

    print("=" * 70)
    print(f"1. DETERMINISTIC PYTHON SWING LEVEL CALCULATION ({ticker})")
    print("=" * 70)
    print(f"Current Close: ${curr_c:.2f}")
    print(f"52-Week High: ${max_52w:.2f}")
    print(f"Calculated Overhead Resistance Peaks (> ${curr_c:.2f}): {overhead_peaks}")
    print(f"Calculated Underlying Support Lows (< ${curr_c:.2f}): {underlying_supports}")

    # 2. Targeted Vision Prompt for Qwen3.8-27B Vision Model
    system_prompt = (
        "You are an expert Chartered Market Technician (CMT). "
        "Your task is to examine the provided zoomed TradingView chart and read the exact "
        "structural resistance levels, supply lines, price tags, and support floors."
    )

    user_prompt = (
        f"Examine the attached zoomed chart for {ticker} (Current Price: ~${curr_c:.2f}).\n\n"
        "Focus specifically on the price levels and plotted tags:\n"
        "1. **Overhead Key Resistances & Supply Tags:**\n"
        f"   - Look at the upper region of the chart (above ${curr_c:.2f}).\n"
        "   - Identify any plotted horizontal lines, swing high peaks, and right-axis labels (e.g. 'KEY RES', 'High', range ceilings).\n"
        "   - Quote the exact numbers and labels visible.\n\n"
        "2. **Underlying Key Supports & Demand Floors:**\n"
        f"   - Look at the lower region of the chart (below ${curr_c:.2f}).\n"
        "   - Identify any plotted horizontal support lines and right-axis tags (e.g. 'KEY SUP', moving average floors).\n\n"
        "3. **Compression & Geometry Verdict:**\n"
        "   - Is price currently compressing between a specific floor and ceiling? What are the boundaries?"
    )

    print("\n" + "=" * 70)
    print(f"2. QUERYING LOCAL VISION LLM ON TARGETED STRUCTURAL LEVELS...")
    print("=" * 70)

    response = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_paths=[str(zoom_chart)],
        use_tools=False,
        disable_thinking=True,
        max_tokens=2048,
        use_openrouter=False,
    )

    print("\n" + "=" * 70)
    print(f"TARGETED VISION MODEL STRUCTURAL REPORT ({ticker})")
    print("=" * 70)
    print(response)

    out_file = chart_dir / f"{ticker}_targeted_vision_levels.md"
    out_file.write_text(response, encoding="utf-8")
    print(f"\nSaved targeted report to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test targeted vision for key resistance levels")
    parser.add_argument("ticker", nargs="?", default="AMD", help="Ticker symbol")
    parser.add_argument("--date", default="2026-08-16", help="Date folder")
    args = parser.parse_args()

    test_targeted_vision_and_python(args.ticker, args.date)
