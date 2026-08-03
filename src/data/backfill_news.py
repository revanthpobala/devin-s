import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from src.clients.news_client import get_ticker_news
from src.tracking.sheets_tracker import SheetsTracker

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load env variables
load_dotenv()


def backfill_news_for_date(date_str: str):
    logger.info(f"=== Starting News Backfill for date: {date_str} ===")

    sheets = SheetsTracker()
    if not sheets.connect():
        logger.error("Could not connect to Google Sheets.")
        return

    # 1. Backfill Spreadsheet 1: TradingView Alerts Tracker (Intraday Alerts)
    try:
        logger.info(f"Checking Alerts Tracker tab '{date_str}'...")
        worksheet = sheets.get_worksheet_for_date(date_str)
        all_rows = sheets._get_all_rows(worksheet)

        if len(all_rows) > 1:
            # First row is headers
            headers = all_rows[0]
            rows_to_update = all_rows[1:]

            # Find unique symbols
            unique_symbols = set(row[1] for row in rows_to_update if len(row) >= 2)
            logger.info(f"Found {len(unique_symbols)} unique symbols in Alerts sheet.")

            # Parallel news fetching
            news_by_symbol = {}
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_sym = {
                    executor.submit(get_ticker_news, sym): sym for sym in unique_symbols
                }
                for future in future_to_sym:
                    sym = future_to_sym[future]
                    try:
                        news_by_symbol[sym] = future.result()
                    except Exception as e:
                        logger.warning(f"Failed to fetch news for {sym}: {e}")

            # Update rows in-memory
            updated_rows = []
            for idx, row in enumerate(rows_to_update):
                row_num = idx + 2
                symbol = row[1]
                action = row[2]

                # Check if news is already filled (Columns S, T, U: index 18, 19, 20)
                # Pad row to at least 18 items (Raw Message is index 17)
                while len(row) < 18:
                    row.append("")

                # News fields are at index 18, 19, 20
                has_news = len(row) >= 21 and (row[18] != "" or row[19] != "")

                action_upper = action.upper().strip()
                is_entry = action_upper.startswith("ENTER") or action_upper in ["BUY", "LONG"]

                if is_entry and not has_news:
                    news_data = news_by_symbol.get(symbol, {})
                    news_url = news_data.get("url", "")
                    news_source = news_data.get("source", "")
                    news_sentiment = news_data.get("sentiment", "NEUTRAL")
                    news_catalyst = news_data.get("catalyst", "General Market")

                    if news_url and news_source:
                        source_link_formula = f'=HYPERLINK("{news_url}", "{news_source}")'
                    else:
                        source_link_formula = news_source

                    # Remove any existing trailing news columns to replace cleanly
                    row = row[:18]
                    row.extend([source_link_formula, news_sentiment, news_catalyst])
                elif not is_entry:
                    # Pad non-entries with empty news cells to keep columns aligned
                    row = row[:18]
                    row.extend(["", "", ""])

                updated_rows.append(row)

            # Batch update worksheet starting from row 2
            logger.info(f"Batch writing {len(updated_rows)} rows to Alerts Sheet...")
            worksheet.update(
                range_name=f"A2:U{len(all_rows)}",
                values=updated_rows,
                value_input_option="USER_ENTERED",
            )
            # Update local cache
            sheets.all_rows_cache[date_str] = [headers] + updated_rows
            logger.info("Alerts Tracker news backfill complete.")

    except Exception as e:
        logger.error(f"Error backfilling Alerts Tracker: {e}", exc_info=True)

    # 2. Backfill Spreadsheet 2: TradingView Trades Tracker (Matched Journal Trades)
    try:
        logger.info(f"Checking Trades Tracker tab '{date_str}'...")
        worksheet_trades = sheets.get_trades_worksheet_for_date(date_str)
        all_trades_rows = sheets._get_all_rows(worksheet_trades)

        if len(all_trades_rows) > 1:
            headers_trades = all_trades_rows[0]
            rows_trades = all_trades_rows[1:]

            # Find unique symbols
            unique_symbols = set(row[1] for row in rows_trades if len(row) >= 2)
            logger.info(f"Found {len(unique_symbols)} unique symbols in Trades sheet.")

            # Parallel news fetching
            news_by_symbol = {}
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_sym = {
                    executor.submit(get_ticker_news, sym): sym for sym in unique_symbols
                }
                for future in future_to_sym:
                    sym = future_to_sym[future]
                    try:
                        news_by_symbol[sym] = future.result()
                    except Exception as e:
                        logger.warning(f"Failed to fetch news for {sym}: {e}")

            # Update rows in-memory
            updated_trades = []
            for idx, row in enumerate(rows_trades):
                symbol = row[1]
                status = row[3]

                # Check if news is already filled (Columns O, P, Q: index 14, 15, 16)
                while len(row) < 14:
                    row.append("")

                has_news = len(row) >= 17 and (row[14] != "" or row[15] != "")

                if not has_news:
                    news_data = news_by_symbol.get(symbol, {})
                    news_url = news_data.get("url", "")
                    news_source = news_data.get("source", "")
                    news_sentiment = news_data.get("sentiment", "NEUTRAL")
                    news_catalyst = news_data.get("catalyst", "General Market")

                    if news_url and news_source:
                        source_link_formula = f'=HYPERLINK("{news_url}", "{news_source}")'
                    else:
                        source_link_formula = news_source

                    row = row[:14]
                    row.extend([source_link_formula, news_sentiment, news_catalyst])

                updated_trades.append(row)

            # Batch update worksheet starting from row 2
            logger.info(f"Batch writing {len(updated_trades)} rows to Trades Sheet...")
            worksheet_trades.update(
                range_name=f"A2:Q{len(all_trades_rows)}",
                values=updated_trades,
                value_input_option="USER_ENTERED",
            )
            cache_key = f"{worksheet_trades.spreadsheet.title}:{date_str}"
            sheets.all_rows_cache[cache_key] = [headers_trades] + updated_trades
            logger.info("Trades Tracker news backfill complete.")

    except Exception as e:
        logger.error(f"Error backfilling Trades Tracker: {e}", exc_info=True)


if __name__ == "__main__":
    # If a date is passed as argument, use it; otherwise default to today
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "2026-07-08"
    backfill_news_for_date(date_arg)
