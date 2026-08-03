import email
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# Reconfigure stdout for utf-8 console printing
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("historical_import")

from src.clients.gmail_client import GmailClient
from src.clients.price_client import get_current_price
from src.tracking.sheets_tracker import SheetsTracker


def import_history():
    logger.info("Starting optimized historical import...")

    # 1. Connect to Google Sheets
    sheets = SheetsTracker()
    if not sheets.connect():
        logger.error("Could not connect to Google Sheets.")
        return

    # 2. Connect to Gmail
    gmail = GmailClient()
    if not gmail.connect():
        logger.error("Could not connect to Gmail.")
        return

    try:
        gmail.mail.select("inbox")

        # Search for ALL emails from TradingView
        search_query = f'(FROM "{gmail.sender}")'
        status, response_data = gmail.mail.search(None, search_query)

        if status != "OK":
            logger.error(f"Search failed with status: {status}")
            return

        email_ids = response_data[0].split()
        total_emails = len(email_ids)
        logger.info(f"Total historical alert emails found: {total_emails}")

        # Parse all emails first in memory
        parsed_alerts = []
        unique_symbols = set()

        logger.info("Fetching and parsing email details...")
        for index, e_id in enumerate(email_ids):
            status, data = gmail.mail.fetch(e_id, "BODY.PEEK[]")
            if status != "OK" or not data or not data[0]:
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Decode subject
            subject = ""
            if msg["Subject"]:
                from email.header import decode_header

                parts = decode_header(msg["Subject"])
                for part, encoding in parts:
                    if isinstance(part, bytes):
                        subject += part.decode(encoding or "utf-8", errors="ignore")
                    else:
                        subject += part
            subject = subject.strip()

            # Skip non-alert emails (like verification codes)
            if "verification code" in subject.lower():
                continue

            body = gmail._extract_body(msg)
            parsed = gmail.parse_alert(subject, body)

            symbol = parsed.get("symbol")
            strategy = parsed.get("strategy")
            if not symbol or strategy not in ["Intraday", "Daily"]:
                # Only include Intraday and Daily setups
                continue

            # Parse Date and convert to Eastern Time (New York)
            from datetime import timezone

            try:
                dt = parsedate_to_datetime(msg["Date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_eastern = dt.astimezone(ZoneInfo("America/New_York"))
                timestamp_str = dt_eastern.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                timestamp_str = datetime.now(ZoneInfo("America/New_York")).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            parsed_alerts.append(
                {
                    "timestamp": timestamp_str,
                    "symbol": symbol,
                    "action": parsed.get("action"),
                    "strategy": parsed.get("strategy"),
                    "alert_price": parsed.get("alert_price"),
                    "score": parsed.get("score", ""),
                    "dir_prob": parsed.get("dir_prob", ""),
                    "net_sigma": parsed.get("net_sigma", ""),
                    "grade": parsed.get("grade", ""),
                    "align": parsed.get("align", ""),
                    "premium": parsed.get("premium", ""),
                    "wrong_if": parsed.get("wrong_if", ""),
                    "context": parsed.get("context", ""),
                    "raw_message": body,
                }
            )
            unique_symbols.add(symbol)

        logger.info(
            f"Parsed {len(parsed_alerts)} valid alerts. Found {len(unique_symbols)} unique symbols."
        )

        # 3. Parallel price and news fetching
        logger.info("Fetching current market prices and news in parallel...")
        prices = {}
        news_by_symbol = {}
        from src.clients.news_client import get_ticker_news

        with ThreadPoolExecutor(max_workers=15) as executor:
            # Map symbol to thread pool futures for prices
            future_to_symbol = {
                executor.submit(get_current_price, sym): sym for sym in unique_symbols
            }
            # Map symbol to thread pool futures for news
            future_to_news = {executor.submit(get_ticker_news, sym): sym for sym in unique_symbols}

            for future in future_to_symbol:
                sym = future_to_symbol[future]
                try:
                    prices[sym] = future.result()
                except Exception as e:
                    logger.warning(f"Error fetching price for {sym}: {e}")
                    prices[sym] = None

            for future in future_to_news:
                sym = future_to_news[future]
                try:
                    news_by_symbol[sym] = future.result()
                except Exception as e:
                    logger.warning(f"Error fetching news for {sym}: {e}")
                    news_by_symbol[sym] = {
                        "url": "",
                        "source": "",
                        "sentiment": "NEUTRAL",
                        "catalyst": "General Market",
                    }

        # 4. Group parsed alerts by date (YYYY-MM-DD)
        alerts_by_date = {}
        for alert in parsed_alerts:
            date_str = alert["timestamp"][:10]
            if date_str not in alerts_by_date:
                alerts_by_date[date_str] = []
            alerts_by_date[date_str].append(alert)

        logger.info(f"Grouping complete: Alerts split into {len(alerts_by_date)} distinct dates.")

        # 5. Import date-by-date
        for date_str, group_alerts in sorted(alerts_by_date.items()):
            # Filter to only log Intraday alerts in the Daily Alerts Tracker
            intraday_alerts = [a for a in group_alerts if a["strategy"] == "Intraday"]
            if not intraday_alerts:
                logger.info(
                    f"[{date_str}] No Intraday alerts for this date. Skipping Daily Alerts Tracker sheet tab."
                )
                continue

            logger.info(
                f"Importing {len(intraday_alerts)} Intraday alerts for date '{date_str}'..."
            )

            # Get or create worksheet for this date
            worksheet = sheets.get_worksheet_for_date(date_str)

            # Perform clean slate clear & resize to 2 rows (keeps header, leaves 1 empty row, deletes other empty rows)
            logger.info(
                f"Clearing and resetting worksheet '{date_str}' to remove padding/duplicate rows..."
            )
            worksheet.clear()
            worksheet.resize(rows=2)
            sheets._initialize_sheet_headers(worksheet)

            headers = [
                "Timestamp",
                "Symbol",
                "Action",
                "Strategy",
                "Alert Price",
                "LLM Trade Decision",
                "LLM Playbook",
                "Market Price (Alert Time)",
                "Slippage",
                "Current Live Price",
                "Live Perf %",
                "Score",
                "Dir Prob",
                "Net Sigma",
                "Grade",
                "Alignment",
                "Premium Info",
                "Wrong If",
                "Context",
                "Raw Message",
                "News Source Link",
                "News Sentiment",
                "News Catalyst",
            ]
            sheets.all_rows_cache[date_str] = [headers]
            existing_rows = sheets.all_rows_cache[date_str]
            next_row_num = len(existing_rows) + 1

            rows_to_append = []
            skipped_duplicates = 0

            for alert in intraday_alerts:
                symbol = alert["symbol"]
                timestamp = alert["timestamp"]
                action = alert["action"]
                strategy = alert["strategy"]
                alert_price = alert["alert_price"]
                raw_message = alert["raw_message"]

                # Check duplicates locally
                is_duplicate = False
                for row in existing_rows:
                    if row[0] == timestamp and row[1] == symbol:
                        is_duplicate = True
                        break

                for row in rows_to_append:
                    if row[0] == timestamp and row[1] == symbol:
                        is_duplicate = True
                        break

                if is_duplicate:
                    skipped_duplicates += 1
                    continue

                # Prepare formulas with local row numbers for this worksheet
                row_num = next_row_num + len(rows_to_append)
                alert_val = alert_price if alert_price is not None else ""
                market_val = prices.get(symbol)
                if market_val is None:
                    market_val = ""

                # Slippage = Market Price (Col H) - Alert Price (Col E)
                slippage_formula = f'=IF(AND(ISNUMBER(E{row_num}), ISNUMBER(H{row_num})), H{row_num}-E{row_num}, "")'

                # Dynamic index formula mapping
                if symbol == "SPX":
                    live_price_formula = '=GOOGLEFINANCE("INDEXSP:.INX")'
                elif symbol == "VIX":
                    live_price_formula = '=GOOGLEFINANCE("INDEXCBOE:VIX")'
                else:
                    live_price_formula = f"=GOOGLEFINANCE(B{row_num})"

                # Performance = (Live Price (Col J) - Market Price (Col H)) / Market Price (Col H)
                live_perf_formula = f'=IF(AND(ISNUMBER(J{row_num}), ISNUMBER(H{row_num})), (J{row_num}-H{row_num})/H{row_num}, "")'

                # Get news details if it is an entry trade action
                action_upper = action.upper().strip()
                is_entry = action_upper.startswith("ENTER") or action_upper in ["BUY", "LONG"]

                # (Keep original news fetching blocks)
                news_url = ""
                news_source = ""
                news_sentiment = ""
                news_catalyst = ""

                if is_entry:
                    news_data = news_by_symbol.get(symbol, {})
                    news_url = news_data.get("url", "")
                    news_source = news_data.get("source", "")
                    news_sentiment = news_data.get("sentiment", "")
                    news_catalyst = news_data.get("catalyst", "")

                if news_url and news_source:
                    source_link_formula = f'=HYPERLINK("{news_url}", "{news_source}")'
                else:
                    source_link_formula = news_source

                row_data = [
                    timestamp,
                    symbol,
                    action,
                    strategy,
                    alert_val,
                    "",  # LLM Trade Decision (blank for historical imports)
                    "",  # LLM Playbook (blank for historical imports)
                    market_val,
                    slippage_formula,
                    live_price_formula,
                    live_perf_formula,
                    alert.get("score", ""),
                    alert.get("dir_prob", ""),
                    alert.get("net_sigma", ""),
                    alert.get("grade", ""),
                    alert.get("align", ""),
                    alert.get("premium", ""),
                    alert.get("wrong_if", ""),
                    alert.get("context", ""),
                    raw_message,
                    source_link_formula,
                    news_sentiment,
                    news_catalyst,
                ]
                rows_to_append.append(row_data)

            logger.info(
                f"[{date_str}] Skipped {skipped_duplicates} duplicates. Writing {len(rows_to_append)} rows..."
            )

            if rows_to_append:
                import time

                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
                        logger.info(f"Successfully logged batch for '{date_str}'.")
                        # Update cache
                        sheets.all_rows_cache[date_str].extend(rows_to_append)
                        break
                    except Exception as e:
                        import gspread

                        if (
                            isinstance(e, gspread.exceptions.APIError)
                            and hasattr(e, "response")
                            and e.response is not None
                            and e.response.status_code == 429
                        ):
                            wait_time = 5 + (attempt * 2)
                            logger.warning(
                                f"Sheets write limit hit during batch update for {date_str}. Retrying in {wait_time}s..."
                            )
                            time.sleep(wait_time)
                        else:
                            raise e
            else:
                logger.info(f"No new rows to append for '{date_str}'.")

        logger.info("Historical import complete across all daily sheets.")

        # 6. Rebuild Trades Ledger
        logger.info("Compiling matched trades history chronologically...")
        # Sort parsed_alerts by timestamp to guarantee chronological order
        parsed_alerts.sort(key=lambda x: x["timestamp"])

        trades_list = []
        for alert in parsed_alerts:
            if alert.get("strategy") != "Daily":
                # Only include Daily setups (individual stocks) in Trades Tracker
                continue

            symbol = alert["symbol"]
            timestamp = alert["timestamp"]
            action = alert.get("action", "ALERT")
            alert_price = alert["alert_price"]

            action_upper = action.upper().strip()
            is_entry = action_upper.startswith("ENTER") or action_upper in ["BUY", "LONG"]
            is_exit = action_upper == "EXIT" or action_upper in ["SELL"]

            if not is_entry and not is_exit:
                continue

            trade_type = "LONG"
            if "CALLS" in action_upper:
                trade_type = "CALLS"
            elif "PUTS" in action_upper:
                trade_type = "PUTS"
            elif action_upper in ["SHORT"]:
                trade_type = "SHORT"

            if is_entry:
                news_data = news_by_symbol.get(symbol, {})
                trade = {
                    "symbol": symbol,
                    "type": trade_type,
                    "status": "OPEN",
                    "entry_time": timestamp,
                    "exit_time": "",
                    "entry_price_alert": alert_price if alert_price is not None else "",
                    "entry_price_market": prices.get(symbol, ""),
                    "exit_price_alert": "",
                    "exit_price_market": "",
                    "pnl_alert": "",
                    "pnl_market": "",
                    "pnl_pct": "",
                    "duration": "",
                    "news_url": news_data.get("url", ""),
                    "news_source": news_data.get("source", ""),
                    "news_sentiment": news_data.get("sentiment", ""),
                    "news_catalyst": news_data.get("catalyst", ""),
                }
                trades_list.append(trade)
            elif is_exit:
                # Scan from bottom to top in trades_list to find the last open trade
                found_trade = None
                for t in reversed(trades_list):
                    if t["symbol"] == symbol and t["status"] == "OPEN":
                        found_trade = t
                        break

                if found_trade:
                    found_trade["status"] = "CLOSED"
                    found_trade["exit_time"] = timestamp
                    found_trade["exit_price_alert"] = alert_price if alert_price is not None else ""
                    found_trade["exit_price_market"] = prices.get(symbol, "")

        # Group compiled trades by entry date
        trades_by_date = {}
        for t in trades_list:
            date_str = t["entry_time"][:10]
            if date_str not in trades_by_date:
                trades_by_date[date_str] = []
            trades_by_date[date_str].append(t)

        logger.info(f"Grouping complete: Trades split into {len(trades_by_date)} distinct dates.")

        # Rebuild daily Trades sheets
        for date_str, trades in sorted(trades_by_date.items()):
            logger.info(
                f"Rebuilding daily Trades worksheet '{date_str}' with {len(trades)} trades..."
            )
            worksheet = sheets.get_trades_worksheet_for_date(date_str)

            worksheet.clear()
            worksheet.resize(rows=2)
            sheets._initialize_trades_headers(worksheet)

            # Reset cache
            headers = [
                "Trade ID",
                "Symbol",
                "Type",
                "Status",
                "Entry Time",
                "Exit Time",
                "Entry Price (Alert)",
                "Entry Price (Market)",
                "Exit Price (Alert)",
                "Exit Price (Market)",
                "PnL (Alert)",
                "PnL (Market)",
                "PnL % (Market)",
                "Duration (Mins)",
                "News Source Link",
                "News Sentiment",
                "News Catalyst",
            ]
            cache_key = f"{worksheet.spreadsheet.title}:{date_str}"
            sheets.all_rows_cache[cache_key] = [headers]

            trades_to_append = []
            for idx, t in enumerate(trades):
                row_num = idx + 2

                pnl_alert_formula = ""
                pnl_market_formula = ""
                pnl_pct_formula = ""
                duration_formula = ""

                if t["status"] == "CLOSED":
                    if t["type"] in ["PUTS", "SHORT"]:
                        pnl_alert_formula = f'=IF(AND(ISNUMBER(G{row_num}), ISNUMBER(I{row_num})), G{row_num}-I{row_num}, "")'
                        pnl_market_formula = f'=IF(AND(ISNUMBER(H{row_num}), ISNUMBER(J{row_num})), H{row_num}-J{row_num}, "")'
                    else:
                        pnl_alert_formula = f'=IF(AND(ISNUMBER(G{row_num}), ISNUMBER(I{row_num})), I{row_num}-G{row_num}, "")'
                        pnl_market_formula = f'=IF(AND(ISNUMBER(H{row_num}), ISNUMBER(J{row_num})), J{row_num}-H{row_num}, "")'

                    pnl_pct_formula = (
                        f'=IF(AND(ISNUMBER(H{row_num}), H{row_num}>0), L{row_num}/H{row_num}, "")'
                    )
                    duration_formula = f'=IF(AND(ISNUMBER(E{row_num}), ISNUMBER(F{row_num})), ROUND((F{row_num}-E{row_num})*1440, 1), "")'

                news_url = t.get("news_url", "")
                news_source = t.get("news_source", "")
                if news_url and news_source:
                    source_link_formula = f'=HYPERLINK("{news_url}", "{news_source}")'
                else:
                    source_link_formula = news_source

                row_data = [
                    "=ROW()-1",
                    t["symbol"],
                    t["type"],
                    t["status"],
                    t["entry_time"],
                    t["exit_time"],
                    t["entry_price_alert"],
                    t["entry_price_market"],
                    t["exit_price_alert"],
                    t["exit_price_market"],
                    pnl_alert_formula,
                    pnl_market_formula,
                    pnl_pct_formula,
                    duration_formula,
                    source_link_formula,
                    t.get("news_sentiment", ""),
                    t.get("news_catalyst", ""),
                ]
                trades_to_append.append(row_data)

            if trades_to_append:
                import time

                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        worksheet.append_rows(trades_to_append, value_input_option="USER_ENTERED")
                        logger.info(
                            f"Successfully bulk logged {len(trades_to_append)} trades in Trades Sheet '{date_str}'."
                        )
                        # Update cache
                        sheets.all_rows_cache[cache_key].extend(trades_to_append)
                        break
                    except Exception as e:
                        import gspread

                        if (
                            isinstance(e, gspread.exceptions.APIError)
                            and hasattr(e, "response")
                            and e.response is not None
                            and e.response.status_code == 429
                        ):
                            wait_time = 5 + (attempt * 2)
                            logger.warning(
                                f"Quota exceeded during trades batch update for {date_str}. Retrying in {wait_time}s..."
                            )
                            time.sleep(wait_time)
                        else:
                            raise e
            else:
                logger.info(f"No trades parsed to write to ledger for '{date_str}'.")

    except Exception as e:
        logger.error(f"Error importing history: {e}", exc_info=True)
    finally:
        gmail.disconnect()


if __name__ == "__main__":
    import_history()
