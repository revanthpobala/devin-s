import json
import logging
import os
from datetime import datetime

import pandas as pd

from src import config
from src.tracking.sheets_tracker import SheetsTracker

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class DeterministicCascade:
    def __init__(self, date_str: str | None = None):
        self.date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        self.sheets = SheetsTracker()

        self.alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        self.alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        base_url = (
            os.getenv("ALPACA_DATA_URL")
            or os.getenv("ALPACA_API_URL")
            or "https://data.alpaca.markets"
        )
        base_url = base_url.strip().rstrip("/")
        if "paper-api.alpaca.markets" in base_url:
            base_url = base_url.replace("paper-api.alpaca.markets", "data.alpaca.markets")
        elif "api.alpaca.markets" in base_url:
            base_url = base_url.replace("api.alpaca.markets", "data.alpaca.markets")
        if "/v2" not in base_url:
            base_url = f"{base_url}/v2"
        self.alpaca_data_url = base_url

        self._load_sector_map()

    def _load_sector_map(self):
        """Load SP500.csv for sector mapping."""
        self.sector_map = {}
        csv_path = str(config.BASE_DIR / "data" / "SP500.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                for _, row in df.iterrows():
                    self.sector_map[row["Symbol"]] = row["GICS Sector"]
                logger.info(f"Loaded {len(self.sector_map)} symbols from sector map.")
            except Exception as e:
                logger.error(f"Failed to load sector map: {e}")
        else:
            logger.warning("SP500.csv not found for sector mapping.")

    def run(self):
        # 1. Fetch raw rows
        logger.info(f"Fetching raw alerts from Trades Tracker for {self.date_str}...")
        if not self.sheets.connect():
            logger.error("Failed to connect to Google Sheets.")
            return []

        worksheet = self.sheets.get_trades_worksheet_for_date(self.date_str)
        if not worksheet:
            logger.error(f"Worksheet for {self.date_str} not found.")
            return []

        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:
            logger.warning("No data rows found.")
            return []

        headers = all_values[0]
        rows = all_values[1:]

        # Parse rows into dicts
        parsed_alerts = []
        for idx, row in enumerate(rows):
            alert = {"_row_index": idx + 2}  # 1-indexed, plus 1 for header
            for i, header in enumerate(headers):
                if i < len(row):
                    alert[header] = row[i]

            # Parse Raw Request JSON
            raw_str = alert.get("Raw Request", "")
            if raw_str:
                try:
                    alert["_raw"] = json.loads(raw_str)
                except:
                    alert["_raw"] = {}
            else:
                alert["_raw"] = {}

            parsed_alerts.append(alert)

        logger.info(f"Loaded {len(parsed_alerts)} raw alerts.")

        # --- USER REQUEST: BYPASS ALL CASCADING FILTERS ---
        # The user explicitly requested ALL 150+ symbols in the sheet to be processed by the LLM queues.
        # We will bypass the strict stage 1-4 rank and cap filters entirely.
        survivors = parsed_alerts

        # Log final results
        logger.info(
            f"Cascade bypassed. Sending ALL {len(survivors)} symbols to the Scrape & LLM Pipeline."
        )
        return survivors


if __name__ == "__main__":
    cascade = DeterministicCascade()
    cascade.run()
