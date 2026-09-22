"""
scripts/generate_edge_universe.py

Compacts the 983 Schwab 1000 index constituents into an active, high-conviction
coiled universe (~80-120 symbols) for the Edge Scanner engine.
Avoids rate limit chokes and websocket subscription drops by feeding only
liquid, compressing, pre-move candidates.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src import config
from src.clients.schwab_client import get_schwab_client
from src.screener.schwab_pre_move_scan import (
    SCHWAB_1000_CSV,
    fetch_all_quotes_batch,
    load_biotech_exclusion_set,
    load_schwab_1000_tickers,
    stage1_fast_filter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (UniverseCompactor) %(message)s",
)
logger = logging.getLogger("universe_compactor")

DEFAULT_OUT = config.BASE_DIR / "data" / "schwab_active_coiled.csv"
EDGE_SCANNER_DIR = BASE_DIR / "edge_scanner_tmp"
EDGE_SCANNER_UNIVERSE = EDGE_SCANNER_DIR / "data" / "universe.csv"


def generate_edge_universe(
    out_path: Path = DEFAULT_OUT,
    copy_to_edge_scanner: bool = True,
    top_limit: int = 150,
) -> List[Dict[str, Any]]:
    """
    Fetch batch quotes for all Schwab 1000 constituents, run Stage 1 filter,
    and output formatted CSV for Edge Scanner.
    """
    logger.info("Starting Schwab 1000 Universe Compaction for Edge Scanner...")
    t0 = time.time()

    tickers = load_schwab_1000_tickers()
    if not tickers:
        logger.error("No tickers found in Schwab 1000 index CSV.")
        return []

    logger.info(f"Loaded {len(tickers)} constituents. Fetching live batch quotes...")
    client = get_schwab_client()
    raw_quotes = fetch_all_quotes_batch(client, tickers)

    if not raw_quotes:
        logger.error("Failed to fetch raw quotes from Schwab.")
        return []

    biotech_set = load_biotech_exclusion_set()
    funnel_tracker = {"rejections": {}, "stage1_passed": {}}

    logger.info("Applying Stage 1 Fast Filter (Price >= $15, Vol >= 800k, Coiling / Momentum)...")
    candidates = stage1_fast_filter(raw_quotes, biotech_set, funnel_tracker)
    logger.info(f"Filtered down to {len(candidates)} active coiled candidates.")

    # Sort candidates by relative strength / proximity to trigger
    def _rank_key(c: Dict[str, Any]) -> float:
        vol_score = min(float(c.get("avg_vol", 0)) / 1_000_000, 10.0)
        pos = float(c.get("pos_52w", 0.5))
        coiling_bonus = 5.0 if 0.25 <= pos <= 0.65 or pos >= 0.80 else 0.0
        return vol_score + coiling_bonus

    candidates.sort(key=_rank_key, reverse=True)

    if top_limit and len(candidates) > top_limit:
        logger.info(f"Trimming candidates from {len(candidates)} to top {top_limit}.")
        candidates = candidates[:top_limit]

    # Format into edge-scanner required schema: symbol,last_price,adv20,dollar_vol_m,atr_pct
    rows = []
    for c in candidates:
        sym = str(c.get("symbol", "")).upper().replace("/", ".")
        last_px = round(float(c.get("price", 0.0)), 2)
        avg_vol = int(c.get("avg_10d_vol") or c.get("volume") or 1_000_000)
        dollar_vol_m = round((last_px * avg_vol) / 1e6, 2)
        # Approximate daily ATR% from 52w spread and volatility
        headroom = float(c.get("headroom_52w", 15.0))
        atr_pct = max(1.2, round(min(headroom * 0.15, 4.5), 2))

        if sym and last_px >= 15.0:
            rows.append({
                "symbol": sym,
                "last_price": last_px,
                "adv20": avg_vol,
                "dollar_vol_m": dollar_vol_m,
                "atr_pct": atr_pct,
            })

    # Always ensure benchmark SPY is included for market alignment / RS
    if not any(r["symbol"] == "SPY" for r in rows):
        rows.append({
            "symbol": "SPY",
            "last_price": 550.0,
            "adv20": 60000000,
            "dollar_vol_m": 33000.0,
            "atr_pct": 1.2,
        })

    out_df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    logger.info(f"Saved {len(rows)} compacted universe symbols to {out_path} in {time.time() - t0:.2f}s.")

    # Copy to edge_scanner_tmp data directory if requested
    if copy_to_edge_scanner and EDGE_SCANNER_DIR.exists():
        EDGE_SCANNER_UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(EDGE_SCANNER_UNIVERSE, index=False)
        logger.info(f"Synchronized universe to {EDGE_SCANNER_UNIVERSE}")

    return rows


def main():
    parser = argparse.ArgumentParser(description="Generate compacted universe for Edge Scanner from Schwab 1000")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output CSV path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=150,
        help="Max candidates to include in the active universe (default: 150)",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Do not copy universe to edge_scanner_tmp/data/universe.csv",
    )
    args = parser.parse_args()

    generate_edge_universe(
        out_path=args.out,
        copy_to_edge_scanner=not args.no_copy,
        top_limit=args.limit,
    )


if __name__ == "__main__":
    main()
