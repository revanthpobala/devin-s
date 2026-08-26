import json
import logging
import os
from datetime import datetime

import concurrent.futures
from pathlib import Path
import pandas as pd

from src import config
from src.logic.deterministic_cascade import DeterministicCascade
from src.tracking.sheets_tracker import SheetsTracker

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (SwingResearch) %(message)s"
)


def run_swing_pipeline(
    target_date: str | None = None,
    target_ticker: str | None = None,
    spx_mode: bool = False,
    spx_csv: str | None = None,
    force: bool = False,
):
    logger.info("=" * 60)
    logger.info("STARTING SWING RESEARCH PIPELINE (Scrape Phase)")
    if spx_mode:
        logger.info("MODE: SPX Constituents (1-Year History -> SWING-SPX Sheet)")
    logger.info("=" * 60)

    today_str = target_date or datetime.now().strftime("%Y-%m-%d")
    lookback_days = 365 if spx_mode else 90

    # 1. Determine survivors
    if spx_mode:
        logger.info("\n--- PHASE 1: SPX CONSTITUENTS LOADING ---")
        csv_path = Path(spx_csv) if spx_csv else config.SPX_CONSTITUENTS_CSV
        if not csv_path.exists():
            logger.error(f"SPX constituents CSV not found at {csv_path}")
            return

        spx_df = pd.read_csv(csv_path)
        logger.info(f"Loaded {len(spx_df)} constituents from {csv_path.name}")

        survivors = []
        for idx, row in spx_df.iterrows():
            sym = str(row.get("Symbol", "")).strip().upper()
            if not sym or sym == "NAN":
                continue
            survivors.append(
                {
                    "Trade ID": f"SPX-{today_str}-{sym}",
                    "Symbol": sym,
                    "Ticker": sym,
                    "Security": str(row.get("Security", "")).strip(),
                    "GICS Sector": str(row.get("GICS Sector", "")).strip(),
                    "GICS Sub-Industry": str(row.get("GICS Sub-Industry", "")).strip(),
                    "source": "SPX",
                    "_sheet_type": "spx",
                }
            )

        # Batch upload to SWING-SPX Google Sheet tab
        sheets = SheetsTracker()
        logger.info(f"Uploading {len(survivors)} SPX constituents to SWING-SPX sheet for tab '{today_str}'...")
        sheets.batch_upload_spx_survivors(today_str, survivors)

    elif target_ticker:
        logger.info("\n--- PHASE 1: TARGET TICKER OVERRIDE ---")
        ticker = target_ticker.strip().upper()
        logger.info(f"Resolving existing Trades-sheet row for: {ticker}")

        row_idx = None
        sheet_type = "trades"
        try:
            tracker = SheetsTracker()
            tracker.connect()
            date_tab = today_str
            worksheet = tracker.get_trades_worksheet_for_date(date_tab)
            all_rows = tracker._get_all_rows(worksheet)
            # Column C (index 2) holds the Symbol; row index is 1-based sheet row.
            for idx, r in enumerate(all_rows[1:], start=2):
                if len(r) >= 3 and str(r[2]).strip().upper() == ticker:
                    row_idx = idx
                    logger.info(f"Found {ticker} on '{date_tab}' Trades tab at row {row_idx}")
                    break
            if row_idx is None:
                logger.warning(
                    f"{ticker} not found in today's Trades sheet; it will be created "
                    f"during the local-research phase and pushed to Sheets then."
                )
        except Exception as e:
            logger.warning(f"Failed to check Trades sheet for {ticker}: {e}; continuing local-only.")

        survivors = [
            {
                "Trade ID": f"SWING-{today_str}-{ticker}",
                "Symbol": ticker,
                "Ticker": ticker,
                "source": "cli_override",
                "_row_index": row_idx,
                "_sheet_type": sheet_type,
            }
        ]
    else:
        logger.info("\n--- PHASE 1: RUNNING DETERMINISTIC CASCADE ---")
        cascade = DeterministicCascade(date_str=today_str)
        survivors = cascade.run()

    if not survivors:
        logger.info("No survivors found. Pipeline terminating.")
        return

    logger.info(f"Pipeline advancing with {len(survivors)} survivor(s).")
    out_dir = config.BASE_DIR / "data" / "raw" / today_str
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "survivors.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(survivors, f, indent=4)
    logger.info("Wrote survivor manifest -> %s", manifest_path)

    # 2. Assign Chrome profiles across survivors
    CHROME_PROFILES = [p.name for p in config.BASE_DIR.glob("tv_chrome_profile_*") if p.is_dir()]
    if not CHROME_PROFILES:
        CHROME_PROFILES = ["tv_chrome_profile_1"]
    logger.info(f"Using {len(CHROME_PROFILES)} existing Chrome profile(s): {CHROME_PROFILES}")

    num_workers = min(len(survivors), len(CHROME_PROFILES), 4)

    # Remove stale singleton lock files before launching parallel workers
    for profile_name in CHROME_PROFILES:
        target_profile = config.BASE_DIR / profile_name
        if target_profile.exists():
            for item in target_profile.rglob("*"):
                if item.is_file() and item.name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
                    try:
                        item.unlink()
                    except Exception:
                        pass

    # 3. Process survivors in parallel (Phase 2A: Scraping)
    logger.info(
        f"\n--- PHASE 2A: SCRAPING TRADINGVIEW DATA ({num_workers} workers, lookback={lookback_days}d) ---"
    )
    from src.logic.process_survivor import scrape_survivor_task

    is_force = force or bool(target_ticker)

    scrape_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        for index, survivor in enumerate(survivors):
            worker_id = (index % num_workers) + 1
            profile = CHROME_PROFILES[index % num_workers]
            scrape_futures.append(
                executor.submit(
                    scrape_survivor_task,
                    survivor,
                    out_dir,
                    today_str,
                    worker_id,
                    lookback_days=lookback_days,
                    chrome_profile=profile,
                    force=is_force,
                )
            )

        try:
            for future in concurrent.futures.as_completed(scrape_futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Scrape worker thread failed: {e}")
        except Exception as e:
            logger.error(f"Scrape phase exception: {e}")

    logger.info("=" * 60)
    logger.info("SCRAPE PHASE COMPLETE.")
    logger.info("Next: run  python run_local_research.py %s", today_str)
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run swing research scrape phase")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD). If a non-date token is given, "
        "it is treated as --ticker using today's date.",
    )
    parser.add_argument("--ticker", type=str, help="Run only on a specific ticker")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-scrape even if screenshots/datawindow already exist",
    )
    parser.add_argument(
        "--spx",
        action="store_true",
        help="Run scrape phase for all S&P 500 constituents with 1-year history logged to SWING-SPX sheet",
    )
    parser.add_argument(
        "--spx-csv",
        type=str,
        default=None,
        help="Custom path to SPX constituents CSV (default: EveryDay/SPX-constituents.csv)",
    )

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker

    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    import re

    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_swing_pipeline(
        target_date=target_date,
        target_ticker=target_ticker,
        spx_mode=args.spx,
        spx_csv=args.spx_csv,
        force=args.force,
    )
