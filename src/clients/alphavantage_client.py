import json
import logging
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

from src import config

load_dotenv()

logger = logging.getLogger(__name__)

API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
BASE_URL = "https://www.alphavantage.co/query"

DAILY_LIMIT = 25
USAGE_FILE = config.BASE_DIR / "data" / "av_usage.json"


def _check_and_increment_quota() -> bool:
    """Returns True if under quota (and increments), False if limit reached."""
    if not API_KEY:
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)

    usage_data = {}
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE, "r") as f:
                usage_data = json.load(f)
        except Exception:
            pass

    if usage_data.get("date") != today:
        usage_data = {"date": today, "count": 0}

    current_count = usage_data.get("count", usage_data.get("calls", 0))
    if current_count >= DAILY_LIMIT:
        logger.warning(f"Alpha Vantage daily limit ({DAILY_LIMIT}) reached.")
        return False

    usage_data["count"] = current_count + 1
    with open(USAGE_FILE, "w") as f:
        json.dump(usage_data, f)

    return True


def format_av_block(ticker: str) -> str:
    """Fetch OVERVIEW from Alpha Vantage and return a formatted block."""
    if not _check_and_increment_quota():
        return f"--- PER-TICKER FUNDAMENTALS ({ticker} AV) ---\n(Alpha Vantage daily quota exceeded or no key)\n"

    try:
        logger.info(f"[{ticker}] Fetching Alpha Vantage OVERVIEW...")
        params = {"function": "OVERVIEW", "symbol": ticker, "apikey": API_KEY}
        resp = requests.get(BASE_URL, params=params, timeout=10)
        data = resp.json()

        if "Information" in data and "rate limit" in data.get("Information", "").lower():
            logger.warning("Alpha Vantage API rate limit hit.")
            return f"--- PER-TICKER FUNDAMENTALS ({ticker} AV) ---\n(Rate limit hit)\n"

        if not data or "Symbol" not in data:
            return f"--- PER-TICKER FUNDAMENTALS ({ticker} AV) ---\n(No data found)\n"

        block = f"--- PER-TICKER FUNDAMENTALS ({ticker} AV) ---\n"
        block += f"Sector: {data.get('Sector')} | Industry: {data.get('Industry')}\n"
        block += f"Market Cap: {data.get('MarketCapitalization')}\n"
        block += f"PE Ratio: {data.get('PERatio')} | Forward PE: {data.get('ForwardPE')}\n"
        block += f"EPS: {data.get('EPS')} | PEG Ratio: {data.get('PEGRatio')}\n"
        block += f"Analyst Target Price: {data.get('AnalystTargetPrice')} (High/Low: {data.get('52WeekHigh')} / {data.get('52WeekLow')})\n"
        block += f"Short Ratio: {data.get('ShortRatio')} | Beta: {data.get('Beta')}\n"
        return block + "\n"

    except Exception as e:
        logger.error(f"Alpha Vantage request failed: {e}")
        return f"--- PER-TICKER FUNDAMENTALS ({ticker} AV) ---\n(Request failed)\n"
