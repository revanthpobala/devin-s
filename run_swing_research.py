import json
import logging
import os
from datetime import datetime

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

        if target_ticker:
            target = target_ticker.upper()
            matched = [s for s in survivors if s["Ticker"] == target]
            survivors = matched if matched else [{"Ticker": target, "_sheet_type": "spx"}]

        # Batch upload to SWING-SPX Google Sheet tab
        sheets = SheetsTracker()
        logger.info(f"Uploading {len(survivors)} SPX constituents to SWING-SPX sheet for tab '{today_str}'...")
        sheets.batch_upload_spx_survivors(today_str, survivors)

    elif target_ticker:
        logger.info("\n--- PHASE 1: TARGET TICKER OVERRIDE ---")
        target = target_ticker.upper()
        logger.info(f"Resolving existing Trades-sheet row for: {target}")
        cascade = DeterministicCascade(date_str=today_str)
        all_survivors = cascade.run()
        matched = [
            s
            for s in all_survivors
            if (s.get("Ticker") or s.get("Symbol") or s.get("ticker") or "").upper() == target
        ]
        survivors = matched if matched else [{"ticker": target}]
        if matched:
            logger.info(
                f"Found {target} at sheet row {matched[0].get('_row_index')} — Sheets update enabled."
            )
        else:
            logger.warning(
                f"{target} not found in today's Trades sheet; decision will be saved locally only."
            )
    else:
        logger.info("\n--- PHASE 1: DETERMINISTIC CASCADE ---")
        cascade = DeterministicCascade(date_str=today_str)
        survivors = cascade.run()

    if not survivors:
        logger.info("No survivors found today. Pipeline finished.")
        return

    logger.info(f"Pipeline advancing with {len(survivors)} survivor(s).")

    out_dir = config.BASE_DIR / "data" / "raw" / today_str
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "survivors.json"
    if not target_ticker and manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                existing_tickers = {
                    (s.get("Ticker") or s.get("ticker") or s.get("Symbol") or "").upper()
                    for s in survivors
                }
                for item in existing:
                    t = (item.get("Ticker") or item.get("ticker") or item.get("Symbol") or "").upper()
                    if t and t not in existing_tickers:
                        survivors.append(item)
                        existing_tickers.add(t)
        except Exception as e:
            logger.warning(f"Failed to merge existing survivors.json: {e}")

    try:
        manifest_path.write_text(json.dumps(survivors, indent=2), encoding="utf-8")
        logger.info(f"Wrote survivor manifest -> {manifest_path}")
    except Exception as e:
        logger.error(f"Failed to write survivors.json: {e}")

    # 2. Setup ThreadPoolExecutor and Chrome Profiles
    import concurrent.futures

    # Fixed, pre-logged-in Chrome profiles (in the exact order requested). We
    # REUSE these on disk — never clone/overwrite them, or we'd blow away the
    # TradingView logins the user set up once.
    CHROME_PROFILES = [
        "tv_chrome_profile_1",
        "tv_chrome_profile_2",
        "tv_chrome_profile_4",
        "tv_chrome_profile_3",
        "tv_chrome_profile_5",
    ]

    scraper_workers = int(os.getenv("SCRAPER_WORKERS", "5"))
    num_workers = min(scraper_workers, len(survivors), len(CHROME_PROFILES))
    logger.info(f"Using {num_workers} existing Chrome profile(s): {CHROME_PROFILES[:num_workers]}")

    # Just clean stale Singleton locks from each reused profile (do NOT clone).
    for profile_name in CHROME_PROFILES[:num_workers]:
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
    )
