import email
import imaplib
import logging
from email.header import decode_header
from typing import Any, Dict, List, Optional

from src import config
from src.logic.alert_parser import extract_body, parse_alert

logger = logging.getLogger(__name__)


class GmailClient:
    def __init__(self):
        self.email = config.GMAIL_EMAIL
        self.password = config.GMAIL_APP_PASSWORD
        self.sender = config.SENDER_EMAIL
        self.mail: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> bool:
        """Connect and login to Gmail IMAP."""
        try:
            logger.info("Connecting to Gmail IMAP...")
            self.mail = imaplib.IMAP4_SSL("imap.gmail.com")
            self.mail.login(self.email, self.password)
            self.mail.select("inbox")
            logger.info("Successfully connected to Gmail.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Gmail: {e}")
            self.mail = None
            return False

    def disconnect(self):
        """Logout and close connection."""
        if self.mail:
            try:
                self.mail.close()
            except Exception:
                pass
            try:
                self.mail.logout()
            except Exception:
                pass
            self.mail = None
            logger.info("Disconnected from Gmail.")

    def fetch_new_alerts(self) -> List[Dict[str, Any]]:
        """Fetch all unread TradingView alert emails, parse them, and return a list of alerts."""
        if not self.mail:
            if not self.connect() or not self.mail:
                return []

        mail = self.mail
        alerts = []
        try:
            # Select inbox
            mail.select("inbox")

            # Search for UNSEEN emails from the specified sender
            search_query = f'(UNSEEN FROM "{self.sender}")'
            status, response_data = mail.search(None, search_query)

            if status != "OK":
                logger.warning(f"Search command returned status {status}")
                return []

            email_ids = response_data[0].split()
            logger.info(f"Found {len(email_ids)} unread alert emails.")

            for e_id in email_ids:
                alert_data = self.process_email(e_id)
                if alert_data:
                    alerts.append(alert_data)

        except Exception as e:
            logger.error(f"Error fetching alerts: {e}")

        return alerts

    def process_email(self, email_id: bytes | str) -> Optional[Dict[str, Any]]:
        """Fetch, decode, and parse a single email by ID."""
        if not self.mail:
            if not self.connect() or not self.mail:
                return None

        email_str = (
            email_id.decode("utf-8", errors="ignore") if isinstance(email_id, bytes) else email_id
        )

        try:
            import time

            max_retries = 3
            status, data = None, None

            for attempt in range(max_retries):
                try:
                    if self.mail:
                        status, data = self.mail.fetch(email_str, "(RFC822)")
                    if status == "OK":
                        break
                except Exception as fetch_err:
                    logger.warning(
                        f"IMAP fetch error on attempt {attempt + 1} for {email_id}: {fetch_err}. Reconnecting..."
                    )
                    time.sleep(1)
                    self.connect()

            if status != "OK" or not data or not data[0]:
                logger.error(f"Failed to fetch email {email_id} after retries")
                return None

            raw_email = data[0][1]
            if not isinstance(raw_email, (bytes, bytearray)):
                logger.error(f"Invalid email raw payload structure for {email_id}")
                return None
            msg = email.message_from_bytes(raw_email)

            # Decode subject
            subject_header = msg["Subject"]
            subject = ""
            if subject_header:
                decoded_parts = decode_header(subject_header)
                for part, encoding in decoded_parts:
                    if isinstance(part, bytes):
                        subject += part.decode(encoding or "utf-8", errors="ignore")
                    else:
                        subject += part

            # Get body
            body = extract_body(msg)

            # Clean and parse the alert
            parsed_data = parse_alert(subject, body)
            parsed_data["email_id"] = email_str

            # Parse email Date header and convert to Eastern Time (America/New_York)
            from datetime import timezone
            from email.utils import parsedate_to_datetime
            from zoneinfo import ZoneInfo

            try:
                dt = parsedate_to_datetime(msg["Date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_eastern = dt.astimezone(ZoneInfo("America/New_York"))
                email_date = dt_eastern.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                # Fallback to current Eastern Time
                from datetime import datetime

                email_date = datetime.now(ZoneInfo("America/New_York")).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            parsed_data["timestamp"] = email_date

            return parsed_data

        except Exception as e:
            logger.error(f"Error processing email {email_id}: {e}")
            return None

    def mark_as_read(self, email_id: bytes | str) -> bool:
        """Mark the email as read (Seen)."""
        if not self.mail:
            return False
        import time

        email_str = (
            email_id.decode("utf-8", errors="ignore") if isinstance(email_id, bytes) else email_id
        )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.mail.store(email_str, "+FLAGS", "\\Seen")
                logger.info(f"Marked email {email_id} as read.")
                return True
            except Exception as e:
                logger.warning(
                    f"Failed to mark email {email_id} as read on attempt {attempt + 1}: {e}. Reconnecting..."
                )
                time.sleep(1)
                self.connect()
        logger.error(f"Failed to mark email {email_id} as read after retries.")
        return False
