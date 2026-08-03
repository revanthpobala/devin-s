"""
Regenerate empty news research dossiers for a given date.

The first batch run hammered DuckDuckGo with ~36 concurrent searches
(4 news workers x 9 domains), which tripped 429 rate-limiting and left
157/160 dossiers empty ("No URLs discovered" / "no usable content").

This driver replays news research ONE TICKER AT A TIME so DDGS concurrency
stays at <=9 (the per-ticker domain-search fan-out), which keeps us under the
rate limit. Tickers that already have real content (a '### [' summary block)
are skipped.

Usage:
    python -m regenerate_news 2026-07-13
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from src import config
from src.clients.news_researcher import run_news_research

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("regenerate_news")


def _is_real(dossier_path: Path) -> bool:
    try:
        txt = dossier_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "### [" in txt


def _artifact_dir(date_str: str, ticker: str) -> Path:
    """Return the directory holding a ticker's artifacts. After local-research
    segregation, deep-research-flagged tickers live in data/triage/<date>/_DEEP_RESEARCH
    (or force for ad-hoc runs); all other tickers stay in data/raw/<date>."""
    safe = ticker.replace(":", "_")
    triage_dir = config.BASE_DIR / "data" / "triage" / date_str
    for d in ("_DEEP_RESEARCH", "force"):
        cand = triage_dir / d
        if (cand / f"{safe}_news_research.md").exists() or (cand / f"{safe}_thesis.json").exists():
            return cand
    return config.BASE_DIR / "data" / "raw" / date_str


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str
    survivors_path = raw_dir / "survivors.json"
    survivors = json.loads(survivors_path.read_text(encoding="utf-8"))

    empty, skipped, failed = [], [], []
    for i, s in enumerate(survivors, 1):
        ticker = s["ticker"]
        out_dir = _artifact_dir(date_str, ticker)
        dossier = out_dir / f"{ticker}_news_research.md"
        if dossier.exists() and _is_real(dossier):
            skipped.append(ticker)
            continue

        # Force regeneration: remove the hollow dossier.
        if dossier.exists():
            dossier.unlink()

        logger.info(f"[{i}/{len(survivors)}] Regenerating news for {ticker} ...")
        try:
            out = run_news_research(ticker, date_str, out_dir)
            if out and _is_real(out):
                empty.append(ticker)
            else:
                failed.append(ticker)
                logger.warning(f"{ticker}: regenerated dossier still empty")
        except Exception as e:
            failed.append(ticker)
            logger.error(f"{ticker}: news research crashed: {e}")

        # Small breathing room for DDGS between tickers.
        time.sleep(1.5)

    logger.info("=" * 60)
    logger.info(f"DONE. regenerated={len(empty)} skipped(real)={len(skipped)} failed={len(failed)}")
    if failed:
        logger.info(f"FAILED: {failed}")


if __name__ == "__main__":
    main()
