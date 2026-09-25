"""
run_continuous_screener.py

Continuous Schwab 1000 Screener & Autonomous Deep Research Engine.
Continuously screens 983 Schwab 1000 (SCHK) constituents for coiled pre-move swing bases (Long)
and ceiling exhaustion (Prime Short), enriches setups with Tastytrade institutional volatility metrics
and 24/7 cloud price alerts, and autonomously dispatches qualified setups into the
Deep Research pipeline when local execution slots are available.

Usage:
  # Continuous loop mode (scans every 10 min, dispatches 1 in the slot when qualified)
  python run_continuous_screener.py

  # Faster 5-minute polling loop with headless Playwright scraping
  python run_continuous_screener.py --interval 300 --headless

  # Run a single scan pass, report opportunities, dispatch 1 if slot available, and exit
  python run_continuous_screener.py --once
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.screener.continuous_screener_daemon import (
    DEFAULT_SCAN_INTERVAL,
    ContinuousScreenerDaemon,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (AutonomousScanner) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "continuous_screener.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("autonomous_scanner")


def main():
    parser = argparse.ArgumentParser(
        description="Continuous Schwab 1000 Autonomous Screener & Slot-Aware Deep Research Engine"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_SCAN_INTERVAL,
        help="Scan interval in seconds (default: 600s / 10m)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top candidates to track per side (default: 10)",
    )
    parser.add_argument(
        "--auto-deep",
        action="store_true",
        default=True,
        help="Automatically dispatch qualified candidate into Deep Research when slot is available (default: True)",
    )
    parser.add_argument(
        "--no-auto-deep",
        dest="auto_deep",
        action="store_false",
        help="Disable automatic deep research dispatch",
    )
    parser.add_argument(
        "--max-deep",
        type=int,
        default=3,
        help="Max deep research dispatches allowed per day (default: 3)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=60.0,
        help="Minimum priority score to qualify for deep research (default: 60.0)",
    )
    # Tri-state: None (unset) -> use centralized config.SCREENER_MARKET_HOURS_ONLY;
    # --market-hours-only forces True; --always-scan forces False (24/7).
    mho = parser.add_mutually_exclusive_group()
    mho.add_argument(
        "--market-hours-only",
        dest="market_hours_only",
        action="store_const",
        const=True,
        help="Only run scan cycles during active market hours (overrides config)",
    )
    mho.add_argument(
        "--always-scan",
        dest="market_hours_only",
        action="store_const",
        const=False,
        help="Run scan cycles 24/7 including weekends (overrides config)",
    )
    parser.set_defaults(market_hours_only=None)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle, report findings, dispatch 1 if slot is free, and exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run chart scraping headless (no visible browser window)",
    )
    parser.add_argument(
        "--max-slots",
        type=int,
        default=3,
        help="Max concurrent deep research slots (default: 3)",
    )

    args = parser.parse_args()

    if args.headless:
        os.environ["HEADLESS_SCRAPE"] = "1"
    os.environ["CONTINUOUS_MIN_CONVICTION"] = str(args.min_score)
    os.environ["CONTINUOUS_MAX_AUTO_DEEP"] = str(args.max_deep)
    os.environ["CONTINUOUS_MAX_CONCURRENT_SLOTS"] = str(args.max_slots)

    logger.info("=" * 85)
    logger.info("🤖 LAUNCHING SCHWAB 1000 AUTONOMOUS SCREENER & DEEP RESEARCH ENGINE")
    logger.info(f"   Universe:           Schwab 1000 Index (SCHK ETF — 983 stocks)")
    logger.info(f"   Scan Interval:      {args.interval}s ({args.interval / 60:.1f}m)")
    logger.info(f"   Autonomous Deep:    {'ENABLED' if args.auto_deep else 'DISABLED'} (max {args.max_slots} in slot)")
    logger.info(f"   Conviction Gate:    Score >= {args.min_score}")
    logger.info(f"   Daily Deep Cap:     {args.max_deep} setups / day")
    logger.info(f"   Market Hours Only:  {args.market_hours_only}")
    logger.info(f"   Mode:               {'Single Cycle (--once)' if args.once else 'Continuous Autonomous Loop'}")
    logger.info("=" * 85)

    daemon = ContinuousScreenerDaemon(
        poll_interval=args.interval,
        top_n=args.top,
        auto_alerts=False,
        market_hours_only=args.market_hours_only,
        auto_deep_research=args.auto_deep,
        max_concurrent_slots=args.max_slots,
    )

    def handle_shutdown(signum, frame):
        logger.info("\n🛑 Received shutdown signal. Gracefully stopping daemon...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    if args.once:
        logger.info("🔄 Running single autonomous scan pass...")
        res = daemon.run_scan_cycle()
        logger.info(f"✅ Single pass finished: {res}")
    else:
        logger.info("🚀 Starting continuous autonomous background loop...")
        try:
            daemon.run()
        except (KeyboardInterrupt, SystemExit):
            logger.info("🛑 Autonomous daemon stopped.")
            daemon.stop()


if __name__ == "__main__":
    main()
