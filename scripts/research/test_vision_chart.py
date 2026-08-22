"""
scripts/research/test_vision_chart.py

Sends ONLY the zoomed chart image (no JSON / text data) to the local Vision LLM
(Qwen3.8-27B with mmproj) to evaluate its pure visual reading and OCR/signal counting abilities.
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.clients.llm_client import query_local_llm


def test_pure_vision_zoom(ticker: str = "AMD", date_str: str = "2026-08-16"):
    ticker = ticker.upper()
    chart_dir = BASE_DIR / "data" / "raw" / date_str / ticker
    
    zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"
    if not zoom_chart.exists():
        # Fallback to 2026-08-15 if 2026-08-16 folder does not exist
        chart_dir = BASE_DIR / "data" / "raw" / "2026-08-15" / ticker
        zoom_chart = chart_dir / f"{ticker}_chart_zoom.png"

    if not zoom_chart.exists():
        print(f"Error: Zoomed chart not found: {zoom_chart}")
        return

    print(f"[{ticker}] Sending ONLY zoomed chart: {zoom_chart}")

    system_prompt = (
        "You are an expert visual technical chart reader and OCR inspection engine. "
        "Analyze the provided zoomed TradingView chart image directly from the pixels. "
        "Do not invent facts. Report strictly what is visually observable on the canvas."
    )

    user_prompt = (
        f"Inspect the attached zoomed chart for {ticker} and output a detailed visual markdown breakdown:\n\n"
        "1. **On-Chart Dashboard & Text Panels:**\n"
        "   - Transcribe every text box, indicator table, dashboard, or scoreboard visible on the chart canvas (read all numbers, labels, percentages, and statuses exactly as shown).\n\n"
        "2. **Indicator Signals & Visual Marker Counts:**\n"
        "   - Count and identify any specific signal labels, markers, or shapes on the chart (e.g. Bull Traps, Bear Warnings, Reversal pattern markers, Buy/Sell signals, Breakout/Ignition tags).\n"
        "   - Where are they located along the price action?\n\n"
        "3. **Candle Coloring & Squeeze Release Bars:**\n"
        "   - Identify the color sequences of the candlesticks/bars across the chart (e.g., green, red, cyan, yellow, gray, or purple squeeze release bars).\n"
        "   - How many cyan or squeeze release bars are visible in recent action?\n\n"
        "4. **Plotted Lines & Dynamic Bands:**\n"
        "   - What lines/curves are plotted over the price action? (Note their colors, slopes, and whether price is above/below them).\n\n"
        "5. **Recent Right-Edge Price Action:**\n"
        "   - Describe the exact candle shapes, wick lengths, and bar colors for the final 5–10 bars on the far right."
    )

    response = query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        image_paths=[str(zoom_chart)],
        use_tools=False,
        disable_thinking=True,
        max_tokens=4096,
        use_openrouter=False,
    )

    print("\n" + "=" * 70)
    print(f"RAW LOCAL VISION LLM OUTPUT ({ticker})")
    print("=" * 70)
    print(response)

    out_file = chart_dir / f"{ticker}_pure_vision_zoom.md"
    out_file.write_text(response, encoding="utf-8")
    print(f"\nSaved raw vision output to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test pure vision on zoomed chart")
    parser.add_argument("ticker", nargs="?", default="AMD", help="Ticker symbol")
    parser.add_argument("--date", default="2026-08-16", help="Date folder")
    args = parser.parse_args()

    test_pure_vision_zoom(args.ticker, args.date)
