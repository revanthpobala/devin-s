import json
import logging
import os
import threading
from datetime import datetime

import concurrent.futures
from pathlib import Path
import pandas as pd

from src import config
from src.logic.deterministic_cascade import DeterministicCascade

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
    headless: bool = True,
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

        logger.info(f"Loaded {len(survivors)} SPX constituents (Sheets upload disabled, using local SQLite).")

    elif target_ticker:
        logger.info("\n--- PHASE 1: TARGET TICKER OVERRIDE ---")
        ticker = target_ticker.strip().upper()
        logger.info(f"Single-ticker override: {ticker} (scrape starts immediately)")

        survivors = [
            {
                "Trade ID": f"SWING-{today_str}-{ticker}",
                "Symbol": ticker,
                "Ticker": ticker,
                "source": "cli_override",
                "_sheet_type": "trades",
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
    if not (target_ticker and manifest_path.exists()):
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(survivors, f, indent=4)
        logger.info("Wrote survivor manifest -> %s", manifest_path)
    else:
        logger.info("Preserving existing survivor manifest at %s (single ticker override: %s)", manifest_path, target_ticker)

    # 2. Assign Chrome profiles across survivors
    CHROME_PROFILES = [p.name for p in config.BASE_DIR.glob("tv_chrome_profile_*") if p.is_dir()]
    if not CHROME_PROFILES:
        CHROME_PROFILES = ["tv_chrome_profile_1"]
    logger.info(f"Using {len(CHROME_PROFILES)} existing Chrome profile(s): {CHROME_PROFILES}")

    num_workers = min(len(survivors), len(CHROME_PROFILES), 4)

    import psutil

    def _is_profile_locked(p_name: str) -> bool:
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                if 'chrome' in (proc.name() or '').lower():
                    cmd = ' '.join(proc.cmdline() or [])
                    if p_name in cmd:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return False

    # Remove stale singleton lock files ONLY on idle profiles (never touch active browser profiles)
    for profile_name in CHROME_PROFILES:
        if not _is_profile_locked(profile_name):
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

    import random
    scrape_futures = []

    # Cross-process exclusive lock on a Chrome profile's user-data-dir. Each research job runs
    # in its OWN OS process, so an msvcrt byte-range lock is the only mechanism that reliably
    # prevents two CONCURRENT jobs from grabbing the same profile (a plain MD5 slot can collide;
    # a psutil name-scan misses a browser that hasn't spawned yet). The lock file lives inside
    # the profile dir and is released on process exit, so a killed job auto-frees its profile.
    class _ProfileLock:
        def __init__(self, profile_name: str):
            self.path = config.BASE_DIR / profile_name / "_kilo_job.lock"
            self._fh = None

        def acquire(self) -> bool:
            try:
                import msvcrt
                fh = open(self.path, "a+b")
                fh.seek(0)
                if fh.tell() == 0:
                    fh.write(b"\0")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                self._fh = fh  # hold the handle for the lifetime of the scrape
                return True
            except Exception:
                try:
                    if self._fh:
                        self._fh.close()
                except Exception:
                    pass
                self._fh = None
                return False

        def release(self):
            try:
                import msvcrt
                if self._fh:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    self._fh.close()
            except Exception:
                pass
            self._fh = None

    def _acquire_free_profile(offset: int):
        """Walk profiles from a per-job offset; return (profile_name, lock) of the first free one."""
        for k in range(len(CHROME_PROFILES)):
            p = CHROME_PROFILES[(offset + k) % len(CHROME_PROFILES)]
            if _is_profile_locked(p):
                continue  # another live Chrome already owns it
            lock = _ProfileLock(p)
            if lock.acquire():
                return p, lock
        return None, None

    job_seed = os.environ.get("RESEARCH_JOB_ID") or f"{os.getpid()}-{threading.get_ident()}"
    try:
        import hashlib
        job_offset = int(hashlib.md5(job_seed.encode()).hexdigest(), 16) % len(CHROME_PROFILES)
    except Exception:
        job_offset = random.randrange(len(CHROME_PROFILES))

    held_locks: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        for index, survivor in enumerate(survivors):
            worker_id = (index % num_workers) + 1
            if target_ticker:
                profile, lock = _acquire_free_profile(job_offset)
                if profile is None:
                    # No free profile at all — serialize: reuse the first one by brute force.
                    profile = CHROME_PROFILES[index % len(CHROME_PROFILES)]
                else:
                    held_locks.append(lock)  # keep locked for the whole scrape phase
            else:
                profile = CHROME_PROFILES[index % len(CHROME_PROFILES)]
            logger.info(f"Assigning {profile} to worker {worker_id} for survivor {survivor.get('Ticker', 'UNKNOWN')}...")
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
                    headless=headless,
                )
            )

        try:
            has_error = False
            for future in concurrent.futures.as_completed(scrape_futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Scrape worker thread failed: {e}")
                    has_error = True
            if has_error and target_ticker:
                raise RuntimeError(f"Scrape failed for {target_ticker}")
        finally:
            # Release every profile lock we held once all scrapes are done (or after a crash),
            # so the next job can reuse those profiles. Process exit also auto-releases them.
            for _lk in held_locks:
                _lk.release()

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
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run Playwright browser in headless mode (default: True)",
    )
    parser.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Run Playwright browser with visible GUI window",
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
        headless=args.headless,
    )
