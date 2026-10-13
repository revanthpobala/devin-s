import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path
import pandas as pd
import concurrent.futures

# Set up project path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import config
from src.data.tv_scraper import TVScraper, rank_profiles_signed_in_first, usable_profiles
from src.data.csv_adapter import MIN_COLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (Rescrape) %(message)s"
)
logger = logging.getLogger("rescrape")


def find_bad_data_tickers(today_str: str) -> list[str]:
    """Find all tickers that were cut due to bad data or have invalid CSVs."""
    raw_dir = config.BASE_DIR / "data" / "raw" / today_str
    if not raw_dir.exists():
        return []

    # 1. Active screener survivors
    survivors_file = raw_dir / "survivors.json"
    priority_tickers = []
    if survivors_file.exists():
        try:
            with open(survivors_file) as f:
                s_data = json.load(f)
                for item in s_data:
                    sym = item.get("Ticker") or item.get("Symbol")
                    if sym and sym.upper() not in ("CONSOLIDATE", "XYZUNKNOWN", "UNKNOWN"):
                        priority_tickers.append(sym.upper())
        except Exception:
            pass

    # 2. Tickers explicitly triaged today with bad_data / CUT
    triage_cut_tickers = []
    for triage_file in raw_dir.glob("*_triage.json"):
        try:
            with open(triage_file) as f:
                t = json.load(f)
                sym = t.get("ticker", "").upper()
                if not sym or sym in ("CONSOLIDATE", "XYZUNKNOWN", "UNKNOWN"):
                    continue
                # If marked bad_data, it must be rescraped
                if t.get("bad_data") or t.get("reason") == "bad_data":
                    triage_cut_tickers.append(sym)
                else:
                    # Also verify CSV has >= 60 cols; if corrupted, rescrape
                    csv_p = raw_dir / sym / f"{sym}_datawindow.csv"
                    if csv_p.exists():
                        try:
                            df = pd.read_csv(csv_p, nrows=2)
                            if len(df.columns) < MIN_COLS:
                                triage_cut_tickers.append(sym)
                        except Exception:
                            triage_cut_tickers.append(sym)
        except Exception:
            pass

    # Combine: priority screener candidates first, then all triaged bad-data tickers
    all_targets = []
    for p in priority_tickers:
        # Check if priority ticker is already valid
        csv_p = raw_dir / p / f"{p}_datawindow.csv"
        json_p = raw_dir / p / f"{p}_datawindow.json"
        is_valid = False
        if csv_p.exists() and json_p.exists():
            try:
                df = pd.read_csv(csv_p, nrows=2)
                if len(df.columns) >= MIN_COLS:
                    is_valid = True
            except Exception:
                pass
        if not is_valid and p not in all_targets:
            all_targets.append(p)

    for b in sorted(triage_cut_tickers):
        if b not in all_targets:
            all_targets.append(b)

    return all_targets


def scrape_one_ticker(ticker: str, today_str: str, profile_name: str, headless: bool = True) -> bool:
    """Scrapes a single ticker and validates 87-column data integrity."""
    logger.info(f"[{ticker}] Starting scrape using profile {profile_name}...")
    try:
        scraper = TVScraper(
            chrome_profile=profile_name,
            target_date=today_str,
            headless=headless
        )
        scraper.capture_ticker(ticker, lookback_days=90)

        # Verify output
        raw_dir = config.BASE_DIR / "data" / "raw" / today_str
        csv_path = raw_dir / ticker / f"{ticker}_datawindow.csv"
        json_path = raw_dir / ticker / f"{ticker}_datawindow.json"

        if csv_path.exists() and json_path.exists():
            df = pd.read_csv(csv_path, nrows=2)
            cols = len(df.columns)
            if cols >= MIN_COLS:
                logger.info(f"[{ticker}] SUCCESS: Scraped {cols} columns.")
                return True
            else:
                logger.error(f"[{ticker}] FAILED: Scraped only {cols} columns (< {MIN_COLS}).")
                return False
        else:
            logger.error(f"[{ticker}] FAILED: Output files missing.")
            return False
    except Exception as e:
        logger.error(f"[{ticker}] Scraping error: {e}")
        return False


def retriage_ticker(ticker: str, today_str: str) -> None:
    """Runs local research on the ticker to update triage."""
    import subprocess
    import sqlite3
    cmd = [sys.executable, "run_local_research.py", today_str, "--ticker", ticker]
    logger.info(f"[{ticker}] Re-running local triage: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    if res.returncode == 0:
        logger.info(f"[{ticker}] Local triage completed successfully.")
    else:
        logger.warning(f"[{ticker}] Local triage returned exit code {res.returncode}")

    # Immediately sync SQLite DB active_research_jobs so UI reflects fresh status
    try:
        raw_dir = config.BASE_DIR / "data" / "raw" / today_str
        triage_dir = config.BASE_DIR / "data" / "triage" / today_str
        th_p = raw_dir / ticker / f"{ticker}_thesis.json"
        if not th_p.exists():
            th_p = triage_dir / "_DEEP_RESEARCH" / ticker / f"{ticker}_thesis.json"
        if not th_p.exists():
            th_p = triage_dir / "force" / ticker / f"{ticker}_thesis.json"
        if not th_p.exists():
            th_p = raw_dir / f"{ticker}_triage.json"
        if th_p.exists():
            with open(th_p, "r", encoding="utf-8") as f:
                d = json.load(f)
            tr = d.get("triage") or {}
            verdict = tr.get("triage") if isinstance(tr, dict) else d.get("triage")
            reason = tr.get("reason") if isinstance(tr, dict) else d.get("reason")
            is_in_deep_dir = "_DEEP_RESEARCH" in str(th_p) or "force" in str(th_p)
            send_flag = bool(
                is_in_deep_dir
                or d.get("send_for_deep_research")
                or (isinstance(tr, dict) and (tr.get("send_for_deep_research") or tr.get("pursue")))
                or (isinstance(d.get("llm_data"), dict) and d["llm_data"].get("send_for_deep_research"))
            )
            if verdict:
                if send_flag:
                    detail = f"{verdict} (Satisfied -> Enqueued for Deep)"
                else:
                    detail = f"{verdict} ({reason or 'Filtered Out'})"
                db_p = config.BASE_DIR / "data" / "research_watch.db"
                with sqlite3.connect(db_p) as conn:
                    conn.cursor().execute(
                        "UPDATE active_research_jobs SET stage_detail = ?, status = 'COMPLETED', completed_at = ? WHERE ticker = ? AND mode = 'local_only'",
                        (detail, datetime.now(timezone.utc).isoformat(), ticker)
                    )
                    conn.commit()
                logger.info(f"[{ticker}] Synced UI job stage_detail -> {detail}")
    except Exception as e_db:
        logger.debug(f"[{ticker}] DB sync skipped: {e_db}")


def main():
    parser = argparse.ArgumentParser(description="Rescrape bad data tickers and retriage")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date")
    parser.add_argument("--tickers", nargs="*", help="Specific tickers to rescrape")
    parser.add_argument("--workers", type=int, default=1, help="Max parallel scraping workers (default: 1 for maximum TV reliability)")
    parser.add_argument("--skip-triage", action="store_true", help="Skip retriage after scraping")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless")
    args = parser.parse_args()

    today_str = args.date
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        tickers = find_bad_data_tickers(today_str)

    logger.info(f"Found {len(tickers)} ticker(s) requiring rescrape on {today_str}: {tickers}")
    if not tickers:
        logger.info("No tickers need rescraping!")
        return

    # Existing profiles only, signed-in ones first: the saved chart layout is
    # private to the TradingView account, so a signed-out profile is served
    # "Can't open this chart layout" (no canvas) and the rescrape fails. A
    # profile that is provably signed out is never assigned.
    existing_profiles = [p.name for p in config.BASE_DIR.glob("tv_chrome_profile_*") if p.is_dir()]
    usable = usable_profiles(existing_profiles)
    profiles = rank_profiles_signed_in_first(usable) or usable or ["tv_chrome_profile_1"]
    signed_out = [n for n in existing_profiles if n not in set(usable)]
    if signed_out:
        logger.warning(f"Not signed into TradingView, never assigned: {signed_out}")

    successful = []
    failed = []

    # Sequential execution ensures zero TradingView multi-session rate limits
    if args.workers <= 1:
        for idx, ticker in enumerate(tickers, 1):
            prof = profiles[(idx - 1) % len(profiles)]
            logger.info(f"\n[{idx}/{len(tickers)}] Rescraping {ticker} (profile: {prof})...")
            ok = scrape_one_ticker(ticker, today_str, prof, headless=args.headless)
            if ok:
                successful.append(ticker)
                if not args.skip_triage:
                    retriage_ticker(ticker, today_str)
            else:
                failed.append(ticker)
            # Brief pause between scrapes
            time.sleep(3)
    else:
        # Multi-worker execution
        max_w = min(args.workers, len(profiles), len(tickers))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as executor:
            future_to_ticker = {}
            for idx, ticker in enumerate(tickers):
                prof = profiles[idx % len(profiles)]
                f = executor.submit(scrape_one_ticker, ticker, today_str, prof, headless=args.headless)
                future_to_ticker[f] = ticker

            for f in concurrent.futures.as_completed(future_to_ticker):
                ticker = future_to_ticker[f]
                try:
                    ok = f.result()
                    if ok:
                        successful.append(ticker)
                        if not args.skip_triage:
                            retriage_ticker(ticker, today_str)
                    else:
                        failed.append(ticker)
                except Exception as e:
                    logger.error(f"[{ticker}] Worker failed: {e}")
                    failed.append(ticker)

    logger.info("=" * 60)
    logger.info(f"RESCRAPE & RETRIAGE COMPLETE.")
    logger.info(f"Successful ({len(successful)}): {successful}")
    logger.info(f"Failed ({len(failed)}): {failed}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
