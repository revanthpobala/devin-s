from datetime import datetime
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

# pyrefly: ignore [missing-import]
import gspread

# pyrefly: ignore [missing-import]
from google.oauth2.service_account import Credentials

from src import config

logger = logging.getLogger(__name__)


def _format_triage_label(llm_data: Dict[str, Any]) -> str:
    """Build the LLM Triage cell text (e.g. 'WATCH (6/10)' or 'CUT').

    Real local-LLM verdicts carry a 1-10 `conviction`. Mechanical pre-filter
    verdicts (pursue=False) either have no conviction (None) or a 0-100 score
    field, so they are shown WITHOUT a fake '/10' suffix instead of 'CUT (None/10)'.
    """
    triage = llm_data.get("triage", "UNKNOWN")
    conviction = llm_data.get("conviction")
    is_llm_verdict = ("entry_mode" in llm_data) or ("reasoning" in llm_data)
    if is_llm_verdict and isinstance(conviction, (int, float)):
        return f"{triage} ({int(conviction)}/10)"
    return triage


def _format_llm_decision(llm_data: Dict[str, Any]) -> str:
    """Build a concise local-LLM decision string for the Trades sheet."""
    decision = _format_triage_label(llm_data)
    if llm_data.get("send_for_deep_research"):
        decision += " → Deep Research"
    return decision


class SheetsTracker:
    def __init__(self):
        self.service_account_file = config.SERVICE_ACCOUNT_FILE
        self.sheet_name = config.GOOGLE_SHEET_NAME
        self.sheet_id = config.GOOGLE_SHEET_ID
        self.trades_sheet_name = config.GOOGLE_TRADES_SHEET_NAME
        self.spx_sheet_name = getattr(config, "GOOGLE_SWING_SPX_SHEET_NAME", "SWING-SPX")
        self.client = None
        self.sheet: Optional[gspread.Spreadsheet] = None
        self.trades_sheet: Optional[gspread.Spreadsheet] = None
        self.spx_sheet: Optional[gspread.Spreadsheet] = None
        self.last_logged_row = None

        # Caches to avoid duplicate API calls
        self.alerts_worksheets_cache = {}  # title -> Worksheet object
        self.trades_worksheets_cache = {}  # title -> Worksheet object
        self.spx_worksheets_cache = {}     # title -> Worksheet object
        self.all_rows_cache = {}  # spreadsheet:title -> List[List[str]]
        self.lock = threading.Lock()

    def _get_all_rows(self, worksheet) -> List[List[str]]:
        """Fetch all rows from a specific worksheet, using cache if available and retrying on rate limits."""
        sheet_title = worksheet.spreadsheet.title
        title = worksheet.title
        cache_key = f"{sheet_title}:{title}"
        if cache_key not in self.all_rows_cache:
            import time

            max_retries = 5
            for attempt in range(max_retries):
                try:
                    logger.info(
                        f"Fetching existing rows to update cache for worksheet '{title}' inside '{sheet_title}'..."
                    )
                    self.all_rows_cache[cache_key] = worksheet.get_all_values()
                    break
                except gspread.exceptions.APIError as e:
                    if self._is_transient_sheets_error(e) and attempt < max_retries - 1:
                        wait_time = 5 + (attempt * 2)
                        logger.warning(
                            f"Google Sheets transient error. Retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(wait_time)
                    else:
                        raise e
            if cache_key not in self.all_rows_cache:
                self.all_rows_cache[cache_key] = []
        return self.all_rows_cache[cache_key]

    @staticmethod
    def _is_transient_sheets_error(e: Exception) -> bool:
        """Return True for transient Google Sheets API errors worth retrying.

        Covers HTTP 429 (quota/rate limit) and 5xx (503 service unavailable,
        backend errors, etc.). Auth/permission/404 errors are NOT transient.
        """
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 429:
            return True
        if status is not None and 500 <= status < 600:
            return True
        # gspread sometimes wraps errors without a response attribute; fall back
        # to a message check for the common "service is currently unavailable".
        if "503" in str(e) or "service is currently unavailable" in str(e).lower():
            return True
        return False

    def connect(self) -> bool:
        """Authenticate with Google Sheets API and connect to Alerts and Trades sheets.

        Retries on transient errors (429 quota, 5xx outages like 503) with
        exponential backoff so a brief Google-side hiccup doesn't abort the run.
        """
        if not os.path.exists(self.service_account_file):
            logger.error(
                f"Service account file not found at {self.service_account_file}. "
                f"Please place your Google Cloud credentials JSON there."
            )
            return False

        max_retries = 5
        for attempt in range(max_retries):
            try:
                logger.info("Authenticating with Google API using Service Account...")
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                credentials = Credentials.from_service_account_file(
                    self.service_account_file, scopes=scopes
                )
                self.client = gspread.authorize(credentials)

                # 1. Connect to Alerts Sheet
                if self.sheet_id:
                    logger.info(f"Opening Google Sheet by ID: {self.sheet_id}")
                    self.sheet = self.client.open_by_key(self.sheet_id)
                else:
                    logger.info(f"Opening Google Sheet by Name: {self.sheet_name}")
                    self.sheet = self.client.open(self.sheet_name)

                # 2. Connect or Create/Share Trades Sheet
                try:
                    logger.info(f"Opening Trades Google Sheet: {self.trades_sheet_name}")
                    self.trades_sheet = self.client.open(self.trades_sheet_name)
                except gspread.exceptions.SpreadsheetNotFound:
                    logger.info(
                        f"Trades Google Sheet '{self.trades_sheet_name}' not found. Creating it..."
                    )
                    self.trades_sheet = self.client.create(self.trades_sheet_name)

                # 3. Connect or Create/Share SWING-SPX Sheet
                try:
                    logger.info(f"Opening SWING-SPX Google Sheet: {self.spx_sheet_name}")
                    self.spx_sheet = self.client.open(self.spx_sheet_name)
                except gspread.exceptions.SpreadsheetNotFound:
                    logger.info(
                        f"SWING-SPX Google Sheet '{self.spx_sheet_name}' not found. Creating it..."
                    )
                    self.spx_sheet = self.client.create(self.spx_sheet_name)

                # Share with the user's Gmail
                user_email = config.GMAIL_EMAIL
                if user_email:
                    logger.info(
                        f"Automatically sharing Google Sheets with {user_email} as Editor..."
                    )
                    for sh_name, sh_obj in (("Trades", self.trades_sheet), ("SWING-SPX", self.spx_sheet)):
                        if sh_obj:
                            try:
                                sh_obj.share(user_email, perm_type="user", role="writer")
                                logger.info(f"Successfully shared {sh_name} sheet with user.")
                            except Exception as se:
                                logger.warning(f"Failed to share {sh_name} sheet with {user_email}: {se}")

                logger.info("Successfully connected to Google Sheets.")
                return True

            except gspread.exceptions.SpreadsheetNotFound:
                logger.error(
                    f"Google Sheet '{self.sheet_name}' was not found. Please create it and share it with your Service Account."
                )
                return False
            except Exception as e:
                if self._is_transient_sheets_error(e) and attempt < max_retries - 1:
                    wait_time = 5 + (attempt * 5)
                    logger.warning(
                        f"Transient Google Sheets error (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    continue
                logger.error(f"Failed to authenticate/open Google Sheet: {e}")
                return False

        return False

    def get_worksheet_for_date(self, date_str: str) -> gspread.Worksheet:
        """Open or create a worksheet tab named with the YYYY-MM-DD date."""
        if date_str in self.alerts_worksheets_cache:
            return self.alerts_worksheets_cache[date_str]

        if not self.sheet:
            if not self.connect() or not self.sheet:
                raise RuntimeError("Not connected to Google Sheets Alerts Spreadsheet")

        sheet = self.sheet
        try:
            worksheet = sheet.worksheet(date_str)
            logger.info(f"Opened existing worksheet tab: '{date_str}'")
            # Restore headers if this tab was left headerless (e.g. rows deleted in UI).
            self._ensure_headers(worksheet, self._initialize_sheet_headers, "Timestamp")
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"Creating new worksheet tab for date: '{date_str}'")
            # Create sheet with 23 columns (A to W) for all metrics including LLM decisions
            worksheet = sheet.add_worksheet(title=date_str, rows=1000, cols=23)

            # Initialize headers
            self._initialize_sheet_headers(worksheet)

        self.alerts_worksheets_cache[date_str] = worksheet
        return worksheet

    def get_trades_worksheet_for_date(self, date_str: str) -> gspread.Worksheet:
        """Open or create a worksheet tab named with the YYYY-MM-DD date inside Trades Tracker sheet."""
        if date_str in self.trades_worksheets_cache:
            return self.trades_worksheets_cache[date_str]

        if not self.trades_sheet:
            if not self.connect() or not self.trades_sheet:
                raise RuntimeError("Not connected to Google Sheets Trades Spreadsheet")

        trades_sheet = self.trades_sheet
        try:
            worksheet = trades_sheet.worksheet(date_str)
            logger.info(f"Opened existing Trades worksheet tab: '{date_str}'")
            # Restore headers if this tab was left headerless (e.g. rows deleted in UI).
            self._ensure_headers(worksheet, self._initialize_trades_headers, "Trade ID")
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"Creating new Trades worksheet tab for date: '{date_str}'")
            # Create sheet with 24 columns (A to X) for matched trades & research
            worksheet = trades_sheet.add_worksheet(title=date_str, rows=1000, cols=24)

            # Initialize headers
            self._initialize_trades_headers(worksheet)

            # Try to delete default Sheet1 if present in Trades sheet
            try:
                default_ws = trades_sheet.worksheet("Sheet1")
                trades_sheet.del_worksheet(default_ws)
            except Exception:
                pass

        self.trades_worksheets_cache[date_str] = worksheet
        return worksheet

    def get_spx_worksheet_for_date(self, date_str: str) -> gspread.Worksheet:
        """Open or create a worksheet tab named with the YYYY-MM-DD date inside SWING-SPX sheet."""
        if date_str in self.spx_worksheets_cache:
            return self.spx_worksheets_cache[date_str]

        if not self.spx_sheet:
            if not self.connect() or not self.spx_sheet:
                raise RuntimeError("Not connected to Google Sheets SWING-SPX Spreadsheet")

        spx_sheet = self.spx_sheet
        try:
            worksheet = spx_sheet.worksheet(date_str)
            logger.info(f"Opened existing SWING-SPX worksheet tab: '{date_str}'")
            self._ensure_headers(worksheet, self._initialize_trades_headers, "Trade ID")
        except gspread.exceptions.WorksheetNotFound:
            logger.info(f"Creating new SWING-SPX worksheet tab for date: '{date_str}'")
            worksheet = spx_sheet.add_worksheet(title=date_str, rows=1000, cols=24)
            self._initialize_trades_headers(worksheet)
            try:
                default_ws = spx_sheet.worksheet("Sheet1")
                spx_sheet.del_worksheet(default_ws)
            except Exception:
                pass

        self.spx_worksheets_cache[date_str] = worksheet
        return worksheet

    def batch_upload_spx_survivors(self, date_str: str, survivors: list) -> bool:
        """Batch upload all SPX constituent survivors to the SWING-SPX spreadsheet under the date tab."""
        import json
        if not survivors:
            return True

        if not self.spx_sheet:
            if not self.connect():
                return False

        try:
            with self.lock:
                worksheet = self.get_spx_worksheet_for_date(date_str)
                all_existing = self._get_all_rows(worksheet)

                existing_map = {}
                if len(all_existing) > 1:
                    for idx, r in enumerate(all_existing[1:]):
                        if len(r) >= 3 and r[2]:
                            sym = r[2].strip().upper()
                            existing_map[sym] = r

                rows_to_write = []
                try:
                    from zoneinfo import ZoneInfo
                    est_now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    est_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                import random
                used_trade_ids = set()

                def _random_trade_id():
                    # Random numeric Trade ID, unique within this batch upload.
                    while True:
                        tid = random.randint(100000, 999999)
                        if tid not in used_trade_ids:
                            used_trade_ids.add(tid)
                            return str(tid)

                for idx, survivor in enumerate(survivors):
                    symbol = (survivor.get("Symbol") or survivor.get("Ticker") or survivor.get("ticker") or "").strip().upper()
                    if not symbol:
                        continue

                    trade_id = survivor.get("Trade ID") or _random_trade_id()
                    survivor["Trade ID"] = trade_id
                    survivor["_sheet_type"] = "spx"
                    current_row = idx + 2
                    survivor["_row_index"] = current_row

                    prev_row = existing_map.get(symbol, [])
                    entry_time = prev_row[1] if (len(prev_row) > 1 and prev_row[1].strip()) else est_now
                    raw_req = json.dumps(survivor)

                    if symbol == "SPX":
                        live_price_formula = '=GOOGLEFINANCE("INDEXSP:.INX")'
                    elif symbol == "VIX":
                        live_price_formula = '=GOOGLEFINANCE("INDEXCBOE:VIX")'
                    else:
                        live_price_formula = f"=GOOGLEFINANCE(C{current_row})"

                    row_vals = [
                        trade_id,           # A: Trade ID
                        entry_time,         # B: Entry Time (EST)
                        symbol,             # C: Symbol
                        "",                 # D: Type (empty)
                        "",                 # E: Setup (empty)
                        "",                 # F: Stage (empty)
                        "",                 # G: Entry Price (empty)
                        "",                 # H: Targets (empty)
                        "",                 # I: Dir Prob (empty)
                        "",                 # J: Context (empty)
                        "",                 # K: Research (empty)
                        "",                 # L: Verdict (empty)
                        "",                 # M: Conviction (empty)
                        "",                 # N: Action Plan (empty)
                        live_price_formula, # O: Live Price
                        "",                 # P: Live PnL % (empty)
                        "",                 # Q: 1D PnL % (empty)
                        "",                 # R: 5D PnL % (empty)
                        "",                 # S: 20D PnL % (empty)
                        "",                 # T: Raw Request (empty)
                        "",                 # U: LLM Triage (empty)
                        "",                 # V: LLM Reasoning (empty)
                        "",                 # W: AV Data (empty)
                        "",                 # X: LLM Trade Decision (empty)
                    ]
                    rows_to_write.append(row_vals)

                if rows_to_write:
                    logger.info(f"Batch writing {len(rows_to_write)} SPX constituent rows to SWING-SPX tab '{date_str}'...")
                    needed_rows = len(rows_to_write) + 10
                    if worksheet.row_count < needed_rows:
                        worksheet.add_rows(needed_rows - worksheet.row_count)

                    worksheet.update(range_name=f"A2:X{len(rows_to_write) + 1}", values=rows_to_write, value_input_option="USER_ENTERED")
                    cache_key = f"{worksheet.spreadsheet.title}:{date_str}"
                    if cache_key in self.all_rows_cache:
                        del self.all_rows_cache[cache_key]

                logger.info(f"Successfully uploaded {len(rows_to_write)} SPX constituents with Trade IDs to SWING-SPX sheet.")
                return True

        except Exception as e:
            logger.error(f"Failed to batch upload SPX constituents to SWING-SPX sheet: {e}")
            return False

    def _ensure_headers(self, worksheet, init_func, expected_first):
        """Restore headers on an existing tab if its header row is missing or corrupt.

        If row 1 is not our expected header, shift any existing data down by one row
        and (re)write the header row, so we never append into a headerless tab.
        """
        try:
            row1 = worksheet.row_values(1)
            has_header = bool(row1) and row1[0].strip() == expected_first
            if has_header:
                return
            # Header missing/corrupt: preserve existing data by shifting it down one row.
            if row1 and any(str(c).strip() for c in row1):
                worksheet.insert_row([""] * worksheet.col_count, 1)
            init_func(worksheet)
            logger.info(f"Restored headers on existing tab '{worksheet.title}'.")
        except Exception as e:
            logger.warning(
                f"Failed to ensure headers on tab '{getattr(worksheet, 'title', '?')}': {e}"
            )

    def _initialize_sheet_headers(self, worksheet):
        """Initializes the headers and styling on a specific worksheet."""
        try:
            new_headers = [
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

            logger.info(f"Initializing headers for worksheet tab '{worksheet.title}'...")

            # Write headers using named arguments for gspread v5/v6 compatibility
            worksheet.update(range_name="A1:W1", values=[new_headers])

            # Basic Header Formatting (Sleek Dark Theme)
            header_format = {
                "textFormat": {
                    "bold": True,
                    "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                },
                "backgroundColor": {"red": 0.12, "green": 0.12, "blue": 0.14},
                "horizontalAlignment": "CENTER",
            }
            worksheet.format("A1:W1", header_format)

            # Set formatting for columns
            worksheet.format(
                "A2:A1000",
                {"numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}},
            )
            worksheet.format(
                "E2:E1000", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
            )
            worksheet.format(
                "H2:J1000", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
            )
            worksheet.format("K2:K1000", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})

            # Freeze the first row
            worksheet.freeze(rows=1)
            logger.info(f"Worksheet tab '{worksheet.title}' initialized successfully.")
        except Exception as e:
            logger.warning(f"Failed to initialize headers for worksheet '{worksheet.title}': {e}")

    def log_alert(
        self,
        timestamp: str,
        symbol: str,
        action: str,
        strategy: str,
        alert_price: Optional[float],
        market_price: Optional[float],
        raw_message: str,
        score: str = "",
        dir_prob: str = "",
        net_sigma: str = "",
        grade: str = "",
        align: str = "",
        premium: str = "",
        wrong_if: str = "",
        context: str = "",
        news_url: str = "",
        news_source: str = "",
        news_sentiment: str = "",
        news_catalyst: str = "",
        llm_decision: str = "",
        llm_playbook: str = "",
    ) -> bool:
        """
        Logs a parsed alert to the Google Sheet tab for that specific date (YYYY-MM-DD).
        Automatically inserts formulas for Slippage, Google Finance Live Price, and Performance.
        Includes automatic retry logic if Google Sheets API rate limits are hit.
        """
        if strategy != "Intraday":
            # Skip daily screener setups in the Alerts Tracker
            return True

        # Clean timestamp (e.g. 2026-07-09T14:27:04Z -> 2026-07-09 14:27:04)
        clean_timestamp = timestamp.replace("T", " ").replace("Z", "")
        # Determine the date string (first 10 characters: YYYY-MM-DD)
        date_str = clean_timestamp[:10]

        if not self.sheet:
            if not self.connect():
                return False

        try:
            with self.lock:
                # Get worksheet dynamically for this date
                worksheet = self.get_worksheet_for_date(date_str)

                # Let's count rows using the cache for this worksheet
                all_rows = self._get_all_rows(worksheet)
                next_row_num = len(all_rows) + 1

                # Check for duplicates (same Timestamp and Symbol in Columns A and B)
                for row in all_rows:
                    if len(row) >= 2 and row[0] == timestamp and row[1] == symbol:
                        logger.info(
                            f"Duplicate alert detected for {symbol} at {timestamp} in sheet '{date_str}'. Skipping log."
                        )
                        return True

                # Prepare row values
                alert_val = alert_price if alert_price is not None else ""
                market_val = market_price if market_price is not None else ""

                # Slippage = Market Price (Col H) - Alert Price (Col E)
                slippage_formula = f'=IF(AND(ISNUMBER(E{next_row_num}), ISNUMBER(H{next_row_num})), H{next_row_num}-E{next_row_num}, "")'

                # Format live price with index codes mapping to prevent #N/A in Google Sheets
                if symbol == "SPX":
                    live_price_formula = '=GOOGLEFINANCE("INDEXSP:.INX")'
                elif symbol == "VIX":
                    live_price_formula = '=GOOGLEFINANCE("INDEXCBOE:VIX")'
                else:
                    live_price_formula = f"=GOOGLEFINANCE(B{next_row_num})"

                # Performance = (Live Price (Col J) - Market Price (Col H)) / Market Price (Col H)
                live_perf_formula = f'=IF(AND(ISNUMBER(J{next_row_num}), ISNUMBER(H{next_row_num})), (J{next_row_num}-H{next_row_num})/H{next_row_num}, "")'

                if news_url and news_source:
                    source_link_formula = f'=HYPERLINK("{news_url}", "{news_source}")'
                else:
                    source_link_formula = news_source

                row_data = [
                    clean_timestamp,
                    symbol,
                    action,
                    strategy,
                    alert_val,
                    llm_decision,
                    llm_playbook,
                    market_val,
                    slippage_formula,
                    live_price_formula,
                    live_perf_formula,
                    score,
                    dir_prob,
                    net_sigma,
                    grade,
                    align,
                    premium,
                    wrong_if,
                    context,
                    raw_message,
                    source_link_formula,
                    news_sentiment,
                    news_catalyst,
                ]

                # Write row with retries for rate limits
                import time

                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        worksheet.append_row(row_data, value_input_option="USER_ENTERED")  # type: ignore[arg-type]
                        self.last_logged_row = next_row_num
                        # Update local cache
                        cache_key = f"{worksheet.spreadsheet.title}:{date_str}"
                        if cache_key in self.all_rows_cache:
                            self.all_rows_cache[cache_key].append(row_data)
                        return True
                    except gspread.exceptions.APIError as e:
                        # Retry on transient errors: 429 (quota) and 5xx (503 outages, etc.)
                        if self._is_transient_sheets_error(e) and attempt < max_retries - 1:
                            wait_time = 5 + (attempt * 2)
                            logger.warning(
                                f"Google Sheets write quota/transient error. Retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})..."
                            )
                            time.sleep(wait_time)
                        else:
                            raise e

                logger.error(
                    f"Failed to log alert for {symbol} after {max_retries} retries due to quota limits."
                )
                return False

        except Exception as e:
            logger.error(f"Failed to log alert to Google Sheets: {e}")
            return False

    def update_llm_decision(
        self, date_str: str, row_num: int, llm_decision: str, llm_playbook: str
    ) -> bool:
        """Update the LLM Trade Decision and LLM Playbook columns for an existing row."""
        try:
            worksheet = self.get_worksheet_for_date(date_str)
            # Column F is LLM Trade Decision (6th column)
            # Column G is LLM Playbook (7th column)
            range_name = f"F{row_num}:G{row_num}"

            # gspread API update call
            worksheet.update(range_name=range_name, values=[[llm_decision, llm_playbook]])

            # Update local cache
            cache_key = f"{worksheet.spreadsheet.title}:{date_str}"
            if cache_key in self.all_rows_cache:
                cache_idx = row_num - 1
                if cache_idx < len(self.all_rows_cache[cache_key]):
                    row_data = self.all_rows_cache[cache_key][cache_idx]
                    while len(row_data) < 7:
                        row_data.append("")
                    row_data[5] = llm_decision  # Col F is 6th element (index 5)
                    row_data[6] = llm_playbook  # Col G is 7th element (index 6)
            return True
        except Exception as e:
            logger.warning(f"Failed to update LLM decision for row {row_num}: {e}")
            return False

    def _initialize_trades_headers(self, worksheet):
        """Initialize headers and formatting for the Matched Trades sheet."""
        try:
            # Ensure the sheet has enough columns (24 cols: A to X) before updating range A1:X1
            if worksheet.col_count < 24:
                worksheet.add_cols(24 - worksheet.col_count)

            headers = [
                "Trade ID",
                "Entry Time",
                "Symbol",
                "Type",
                "Setup",
                "Stage",
                "Entry Price (Alert)",
                "Targets (Buy/Sell)",
                "Dir Prob",
                "Context",
                "Research",
                "Verdict",
                "Conviction",
                "Action Plan",
                "Live Price",
                "Live PnL %",
                "1D PnL %",
                "5D PnL %",
                "20D PnL %",
                "Raw Request",
                "LLM Triage",
                "LLM Reasoning",
                "AV Data",
                "LLM Trade Decision",
            ]
            worksheet.update(range_name="A1:X1", values=[headers])

            # Formatting (Charcoal Dark Theme for all 24 headers)
            header_format = {
                "textFormat": {
                    "bold": True,
                    "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                },
                "backgroundColor": {"red": 0.12, "green": 0.12, "blue": 0.14},
                "horizontalAlignment": "CENTER",
            }
            worksheet.format("A1:X1", header_format)

            # Formatting for columns
            worksheet.format(
                "B2:B1000",
                {"numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}},
            )
            worksheet.format(
                "G2:G1000", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
            )
            worksheet.format(
                "O2:O1000", {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
            )
            worksheet.format("P2:S1000", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})

            worksheet.freeze(rows=1)
            logger.info("Matched Trades headers initialized and formatted.")
        except Exception as e:
            logger.warning(f"Failed to initialize Trades headers: {e}")

    def log_trade_action(
        self,
        timestamp: str,
        symbol: str,
        action: str,
        alert_price: Optional[float],
        market_price: Optional[float],
        strategy: str = "Daily",
        news_url: str = "",
        news_source: str = "",
        news_sentiment: str = "",
        news_catalyst: str = "",
        raw_alert: dict | None = None,
    ) -> bool:
        """
        Logs simplified trade actions to the TradingView Trades Tracker sheet.
        """
        if strategy != "Daily":
            # Only track Daily setups (individual stocks) in the Trades Tracker
            return True

        if not self.trades_sheet:
            if not self.connect():
                return False

        trade_type = action.upper().strip()
        alert_val = alert_price if alert_price is not None else ""

        # Clean timestamp (e.g. 2026-07-09T14:27:04Z -> 2026-07-09 14:27:04) so Google Sheets detects it as a DateTime
        clean_timestamp = timestamp.replace("T", " ").replace("Z", "")
        date_str = clean_timestamp[:10]

        try:
            with self.lock:
                # Open or create daily Trades worksheet
                worksheet = self.get_trades_worksheet_for_date(date_str)
                all_rows = self._get_all_rows(worksheet)
                next_row = len(all_rows) + 1

                # Extract Swing Trade specific fields if present
                alert_dict = raw_alert or {}
                setup_val = alert_dict.get("setup", "")
                buy_target = alert_dict.get("buy", "")
                sell_target = alert_dict.get("sell", "")
                targets_val = (
                    f"Buy: {buy_target} | Sell: {sell_target}" if buy_target or sell_target else ""
                )
                stage_val = alert_dict.get("stage", "")
                dir_prob_val = alert_dict.get("dir_prob", "")
                context_val = alert_dict.get("context", "")

                import json

                # Generate Google Finance Formulas
                live_price = f'=IF(ISBLANK($C{next_row}), "", GOOGLEFINANCE($C{next_row}, "price"))'

                live_pnl = f'=IF(OR(ISBLANK($G{next_row}), ISBLANK($O{next_row})), "", IF($D{next_row}="LONG", ($O{next_row}-$G{next_row})/$G{next_row}, ($G{next_row}-$O{next_row})/$G{next_row}))'

                # Historical Prices (1, 5, 20 business days after entry date)
                # Note: IF the date hasn't happened yet, it will return an empty string.
                p1d = f'IFERROR(INDEX(GOOGLEFINANCE($C{next_row}, "price", WORKDAY(INT($B{next_row}), 1)), 2, 2), "")'
                pnl_1d = f'=IF(OR(ISBLANK($G{next_row}), {p1d}=""), "", IF($D{next_row}="LONG", ({p1d}-$G{next_row})/$G{next_row}, ($G{next_row}-{p1d})/$G{next_row}))'

                p5d = f'IFERROR(INDEX(GOOGLEFINANCE($C{next_row}, "price", WORKDAY(INT($B{next_row}), 5)), 2, 2), "")'
                pnl_5d = f'=IF(OR(ISBLANK($G{next_row}), {p5d}=""), "", IF($D{next_row}="LONG", ({p5d}-$G{next_row})/$G{next_row}, ($G{next_row}-{p5d})/$G{next_row}))'

                p20d = f'IFERROR(INDEX(GOOGLEFINANCE($C{next_row}, "price", WORKDAY(INT($B{next_row}), 20)), 2, 2), "")'
                pnl_20d = f'=IF(OR(ISBLANK($G{next_row}), {p20d}=""), "", IF($D{next_row}="LONG", ({p20d}-$G{next_row})/$G{next_row}, ($G{next_row}-{p20d})/$G{next_row}))'

                # Append a new open trade
                row_data = [
                    "=ROW()-1",  # Auto-incrementing Trade ID formula
                    clean_timestamp,
                    symbol,
                    trade_type,
                    setup_val,
                    stage_val,
                    alert_val,
                    targets_val,
                    dir_prob_val,
                    context_val,
                    "",  # Research (Placeholder)
                    "",  # Verdict
                    "",  # Conviction
                    "",  # Action Plan
                    live_price,
                    live_pnl,
                    pnl_1d,
                    pnl_5d,
                    pnl_20d,
                    json.dumps(alert_dict, default=str),  # Raw Request payload
                ]

                import time

                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        worksheet.append_row(row_data, value_input_option="USER_ENTERED")  # type: ignore[arg-type]
                        logger.info(
                            f"Successfully logged trade entry for {symbol} (Type: {trade_type}) in Trades Sheet '{date_str}'."
                        )
                        # Update cache
                        cache_key = f"{worksheet.spreadsheet.title}:{date_str}"
                        if cache_key in self.all_rows_cache:
                            self.all_rows_cache[cache_key].append(row_data)
                        return True
                    except gspread.exceptions.APIError as e:
                        if self._is_transient_sheets_error(e) and attempt < max_retries - 1:
                            wait_time = 5 + (attempt * 2)
                            logger.warning(
                                f"Google Sheets write quota/transient error. Retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})..."
                            )
                            time.sleep(wait_time)
                        else:
                            raise e
                return False

        except Exception as e:
            logger.error(f"Failed to log trade action for {symbol}: {e}")
            return False

    def update_swing_research(
        self,
        date_str: str,
        row_index: int,
        llm_data: Dict[str, Any],
        av_sentiment: Dict[str, Any],
        av_earnings: Dict[str, Any],
    ) -> bool:
        """
        Updates the specific row in the Trades Tracker with the LLM triage results and AlphaVantage data.
        Assumes the row_index is 1-indexed as returned by Google Sheets (e.g., row 2).
        """
        if not self.trades_sheet:
            if not self.connect():
                return False

        max_retries = 5
        import time

        for attempt in range(max_retries):
            try:
                with self.lock:
                    # Throttle writes to safely stay under Google's 60 req/min quota across 10 concurrent threads
                    time.sleep(1.5)

                    worksheet = self.get_trades_worksheet_for_date(date_str)

                    # Format Data
                    triage = _format_triage_label(llm_data)
                    reasoning = (
                        llm_data.get("reasoning")
                        or llm_data.get("reason")
                        or llm_data.get("pursue_reason")
                        or ""
                    )

                    av_text = ""
                    if av_sentiment:
                        score = av_sentiment.get("overall_sentiment_score", "")
                        label = av_sentiment.get("overall_sentiment_label", "")

                        ticker_sentiments = av_sentiment.get("ticker_sentiment", [])
                        for ts in ticker_sentiments:
                            if float(ts.get("relevance_score", "0")) > 0.5:
                                score = ts.get("ticker_sentiment_score", score)
                                label = ts.get("ticker_sentiment_label", label)
                                break

                        av_text += f"Sentiment: {score} ({label})\n"
                    if av_earnings:
                        edate = av_earnings.get("reportedDate", "")
                        surp = av_earnings.get("surprisePercentage", "")
                        av_text += f"Earnings: {edate} (Surprise: {surp}%)"

                    # Ensure the sheet has enough columns (we need at least 24 now)
                    if worksheet.col_count < 24:
                        worksheet.add_cols(24 - worksheet.col_count)

                    import gspread

                    # Using cell updates to target the appended LLM research columns
                    # (21=LLM Triage, 22=LLM Reasoning, 23=AV Data, 24=LLM Trade Decision)
                    # so the existing formula columns (Live/1D/5D/20D PnL, Raw Request) are preserved.
                    cells_to_update = [
                        gspread.Cell(row=row_index, col=21, value=triage),
                        gspread.Cell(row=row_index, col=22, value=reasoning),
                        gspread.Cell(row=row_index, col=23, value=av_text),
                        gspread.Cell(row=row_index, col=24, value=_format_llm_decision(llm_data)),
                    ]

                    worksheet.update_cells(cells_to_update)
                    logger.info(
                        f"Successfully updated row {row_index} in {date_str} Trades sheet with Swing Research data."
                    )

                    # We should also ensure the header row has these columns named properly
                    headers = worksheet.row_values(1)
                    header_updates = []
                    if len(headers) < 21 or headers[20] != "LLM Triage":
                        header_updates.append(gspread.Cell(row=1, col=21, value="LLM Triage"))
                    if len(headers) < 22 or headers[21] != "LLM Reasoning":
                        header_updates.append(gspread.Cell(row=1, col=22, value="LLM Reasoning"))
                    if len(headers) < 23 or headers[22] != "AV Data":
                        header_updates.append(gspread.Cell(row=1, col=23, value="AV Data"))
                    if len(headers) < 24 or headers[23] != "LLM Trade Decision":
                        header_updates.append(
                            gspread.Cell(row=1, col=24, value="LLM Trade Decision")
                        )

                    if header_updates:
                        worksheet.update_cells(header_updates)

                    return True
            except Exception as e:
                logger.error(
                    f"Failed to update swing research in sheets (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 3)  # Exponential backoff
                else:
                    return False
        return False

    def batch_update_swing_research(self, date_str: str, updates_list: list, is_spx: bool = False) -> bool:
        """
        Batch updates multiple rows in the Trades or SWING-SPX Tracker with LLM triage results and AlphaVantage data.
        This completely avoids rate limits by submitting a single API request for all updates.
        """
        if not updates_list:
            return True

        if not self.trades_sheet or not self.spx_sheet:
            if not self.connect():
                return False

        try:
            with self.lock:
                use_spx = is_spx or any(
                    u.get("_sheet_type") == "spx" or u.get("survivor", {}).get("_sheet_type") == "spx"
                    for u in updates_list
                )
                if use_spx:
                    worksheet = self.get_spx_worksheet_for_date(date_str)
                else:
                    worksheet = self.get_trades_worksheet_for_date(date_str)
                import gspread

                # Ensure the sheet has enough columns (we need at least 24 now)
                if worksheet.col_count < 24:
                    worksheet.add_cols(24 - worksheet.col_count)

                cells_to_update = []

                for update in updates_list:
                    row_index = update.get("row_index")
                    if not row_index:
                        # Fallback: resolve the row via the sheet Trade ID so a
                        # record with trade_id but no row_index can still update.
                        tid = update.get("trade_id")
                        if tid:
                            row_index = self._find_row_by_trade_id(worksheet, tid)
                    if not row_index:
                        continue

                    llm_data = update.get("llm_data", {})
                    av_sentiment = update.get("av_sentiment", {})
                    av_earnings = update.get("av_earnings", {})

                    # Format Data
                    triage = _format_triage_label(llm_data)
                    reasoning = (
                        llm_data.get("reasoning")
                        or llm_data.get("reason")
                        or llm_data.get("pursue_reason")
                        or ""
                    )

                    av_text = ""
                    if av_sentiment:
                        score = av_sentiment.get("overall_sentiment_score", "")
                        label = av_sentiment.get("overall_sentiment_label", "")

                        ticker_sentiments = av_sentiment.get("ticker_sentiment", [])
                        for ts in ticker_sentiments:
                            if float(ts.get("relevance_score", "0")) > 0.5:
                                score = ts.get("ticker_sentiment_score", score)
                                label = ts.get("ticker_sentiment_label", label)
                                break

                        av_text += f"Sentiment: {score} ({label})\n"
                    if av_earnings:
                        edate = av_earnings.get("reportedDate", "")
                        surp = av_earnings.get("surprisePercentage", "")
                        av_text += f"Earnings: {edate} (Surprise: {surp}%)"

                    cells_to_update.extend(
                        [
                            gspread.Cell(row=row_index, col=21, value=triage),
                            gspread.Cell(row=row_index, col=22, value=reasoning),
                            gspread.Cell(row=row_index, col=23, value=av_text),
                            gspread.Cell(
                                row=row_index, col=24, value=_format_llm_decision(llm_data)
                            ),
                        ]
                    )

                if cells_to_update:
                    worksheet.update_cells(cells_to_update)
                    logger.info(
                        f"Successfully batch updated {len(updates_list)} rows in {date_str} Trades sheet."
                    )

                    # Update headers if missing
                    headers = worksheet.row_values(1)
                    header_updates = []
                    if len(headers) < 21 or headers[20] != "LLM Triage":
                        header_updates.append(gspread.Cell(row=1, col=21, value="LLM Triage"))
                    if len(headers) < 22 or headers[21] != "LLM Reasoning":
                        header_updates.append(gspread.Cell(row=1, col=22, value="LLM Reasoning"))
                    if len(headers) < 23 or headers[22] != "AV Data":
                        header_updates.append(gspread.Cell(row=1, col=23, value="AV Data"))
                    if len(headers) < 24 or headers[23] != "LLM Trade Decision":
                        header_updates.append(
                            gspread.Cell(row=1, col=24, value="LLM Trade Decision")
                        )

                    if header_updates:
                        worksheet.update_cells(header_updates)

                return True
        except Exception as e:
            logger.error(f"Failed to batch update swing research in sheets: {e}")
            return False

    def _find_row_by_trade_id(self, worksheet, trade_id) -> int:
        """Resolve the 1-indexed sheet row for a given Trade ID (column A).

        Used as a fallback when a research record carries a Trade ID but no
        resolved row_index, so updates stay correct even if rows shift."""
        try:
            col_values = worksheet.col_values(1)  # Column A = Trade ID
            target = str(trade_id).strip()
            for idx, val in enumerate(col_values):
                if str(val).strip() == target:
                    return idx + 1  # 1-indexed sheet row
        except Exception as e:
            logger.warning(f"Failed to resolve sheet row by Trade ID {trade_id}: {e}")
        return -1

    def update_deep_research(self, date_str: str, ticker: str, payload_dict: dict) -> bool:
        """
        Updates the specific row in the Trades Tracker with the Minimax JSON results.
        """
        if not self.trades_sheet:
            if not self.connect():
                return False

        try:
            with self.lock:
                worksheet = self.get_trades_worksheet_for_date(date_str)
                all_rows = self._get_all_rows(worksheet)

                # Find the row index for this ticker
                # Ticker is in Column C (index 2)
                row_index = -1
                for idx, row in enumerate(all_rows):
                    if len(row) >= 3 and row[2] == ticker:
                        row_index = idx + 1
                        break

                if row_index == -1:
                    logger.warning(
                        f"Ticker {ticker} not found in sheet {date_str}, cannot update deep research."
                    )
                    return False

                # Columns we want to update:
                # L: Verdict (col 12)
                # M: Conviction (col 13)
                # N: Action Plan (col 14)

                verdict = payload_dict.get("verdict", "")
                conviction = str(payload_dict.get("conviction", ""))

                # Format the action plan nicely
                plan_dict = payload_dict.get("action_plan", {})
                action_plan_text = ""
                if plan_dict:
                    action_plan_text += f"Entry: {plan_dict.get('entry', '')}\n"
                    action_plan_text += f"Stop: {plan_dict.get('stop', '')}\n"
                    action_plan_text += f"Target: {plan_dict.get('target', '')}\n"
                    if plan_dict.get("rationale"):
                        action_plan_text += f"\n{plan_dict.get('rationale')}"

                cells_to_update = [
                    gspread.Cell(row=row_index, col=12, value=verdict),
                    gspread.Cell(row=row_index, col=13, value=conviction),
                    gspread.Cell(row=row_index, col=14, value=action_plan_text),
                ]

                worksheet.update_cells(cells_to_update)
                logger.info(f"Successfully updated Deep Research for {ticker} in row {row_index}.")
                return True
        except Exception as e:
            logger.error(f"Failed to update deep research in sheets for {ticker}: {e}")
            return False
