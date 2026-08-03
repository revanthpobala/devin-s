import json
import logging
import os
from datetime import datetime

from src import config
from src.logic.deterministic_cascade import DeterministicCascade

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (SwingResearch) %(message)s"
)


def run_swing_pipeline(target_date: str | None = None, target_ticker: str | None = None):
    logger.info("=" * 60)
    logger.info("STARTING SWING RESEARCH PIPELINE (Scrape Phase)")
    logger.info("=" * 60)

    today_str = target_date or datetime.now().strftime("%Y-%m-%d")

    # 1. Determine survivors
    if target_ticker:
        logger.info("\n--- PHASE 1: TARGET TICKER OVERRIDE ---")
        target = target_ticker.upper()
        logger.info(f"Resolving existing Trades-sheet row for: {target}")
        # Read the sheet (like the cascade) so the single-ticker survivor keeps
        # its real `_row_index`; without it, local research can't update the
        # sheet and would only save the decision locally.
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

    today_str = target_date or datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "raw" / today_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # Persist the survivor manifest so the separate local-LLM research process
    # (run_local_research.py) knows exactly what to process, including sheet
    # row indices / raw request data needed for Sheets updates and direction.
    # Dedupe-aware merge preserves entries appended by intraday screener alerts.
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
    import shutil

    scraper_workers = int(os.getenv("SCRAPER_WORKERS", "5"))
    num_workers = min(scraper_workers, len(survivors))  # Configurable scraper workers
    base_profile = config.BASE_DIR / "tv_chrome_profile"

    logger.info(f"Setting up {num_workers} parallel workers. Cloning Chrome profiles...")
    for i in range(1, num_workers + 1):
        target_profile = config.BASE_DIR / f"tv_chrome_profile_{i}"
        if not target_profile.exists():
            try:
                shutil.copytree(base_profile, target_profile)
                logger.info(f"Cloned base profile to {target_profile}")
            except Exception as e:
                logger.error(f"Failed to clone chrome profile to {target_profile}: {e}")

    # 3. Process survivors in parallel (Phase 2A: Scraping)
    logger.info(f"\n--- PHASE 2A: SCRAPING TRADINGVIEW DATA ({num_workers} workers) ---")
    from src.logic.process_survivor import scrape_survivor_task

    # We map survivors to worker IDs by simple modulo arithmetic so each gets a consistent profile
    scrape_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        for index, survivor in enumerate(survivors):
            worker_id = (index % num_workers) + 1
            scrape_futures.append(
                executor.submit(scrape_survivor_task, survivor, out_dir, today_str, worker_id)
            )

        try:
            for future in concurrent.futures.as_completed(scrape_futures, timeout=60):
                try:
                    future.result(timeout=60)
                except concurrent.futures.TimeoutError:
                    logger.warning("Scrape worker task timed out (2 min limit hit).")
                except Exception as e:
                    logger.error(f"Scrape worker thread failed: {e}")
        except concurrent.futures.TimeoutError:
            logger.warning("Scrape phase reached 2 min overall timeout limit.")

    logger.info("=" * 60)
    logger.info("SCRAPE PHASE COMPLETE.")
    logger.info("Next: run  python run_local_research.py %s  (or --ticker X)", today_str)
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

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker

    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    import re

    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_swing_pipeline(target_date, target_ticker)
