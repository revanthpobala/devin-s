import logging
from datetime import datetime

from src.logic.deep_research import run_deep_research

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (DeepResearch) %(message)s"
)


def run_deep_research_pipeline(
    target_date: str = None, target_ticker: str = None, force_tickers: set = None
):
    logger.info("=" * 60)
    logger.info("STARTING DEEP RESEARCH PIPELINE (Paid Validation Phase)")
    logger.info("=" * 60)

    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    run_deep_research(date_str, target_ticker)

    logger.info("=" * 60)
    logger.info("DEEP RESEARCH PHASE COMPLETE.")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    import re

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Run deep research validation phase")
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
        type=str,
        default="",
        help="Comma-separated tickers to force into deep research "
        "regardless of the local-LLM send_for_deep_research gate.",
    )

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker

    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    force_tickers = {t.strip().upper() for t in args.force.split(",") if t.strip()}

    # --force requires a local-research pass that routes the ticker into the
    # force subdir BEFORE deep research runs. If no explicit target/ticker is
    # given, force the listed tickers through local research first.
    if force_tickers:
        from run_local_research import run_local_research

        run_local_research(target_date, target_ticker, force_tickers)

    run_deep_research_pipeline(target_date, target_ticker)
