import email
import logging
import sys

# Reconfigure stdout to use utf-8 to avoid CP1252/charmap errors on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Setup console logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inspect_emails")

from src import config
from src.clients.gmail_client import GmailClient


def inspect_all_emails():
    config.validate_config()

    gmail = GmailClient()
    if not gmail.connect():
        logger.error("Could not connect to Gmail.")
        return

    try:
        gmail.mail.select("inbox")

        # Search for ALL emails from the sender
        search_query = f'(FROM "{gmail.sender}")'
        status, response_data = gmail.mail.search(None, search_query)

        if status != "OK":
            logger.error(f"Search failed with status: {status}")
            return

        email_ids = response_data[0].split()
        logger.info(f"Total emails from {gmail.sender} found: {len(email_ids)}")

        # Group by unique subjects
        subject_groups = {}

        for e_id in email_ids:
            status, data = gmail.mail.fetch(e_id, "(RFC822)")
            if status != "OK":
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
            if subject not in subject_groups:
                subject_groups[subject] = []
            subject_groups[subject].append(e_id)

        print("\n" + "=" * 80)
        print("UNIQUE ALERTS GROUPED BY SUBJECT")
        print("=" * 80 + "\n")

        for subject, ids in subject_groups.items():
            print(f"Subject:  {subject}")
            print(f"Count:    {len(ids)} emails")

            # Get the body of the most recent email in this group
            latest_id = ids[-1]
            status, data = gmail.mail.fetch(latest_id, "(RFC822)")
            if status == "OK":
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)
                body = gmail._extract_body(msg)
                parsed = gmail.parse_alert(subject, body)

                print(
                    f"Sample Parsed: Symbol={parsed['symbol']}, Action={parsed['action']}, Strategy={parsed['strategy']}, Price={parsed['alert_price']}"
                )
                print("-" * 40)
                print("Sample Body:")
                print(body)
            print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Error inspecting emails: {e}", exc_info=True)
    finally:
        gmail.disconnect()


if __name__ == "__main__":
    inspect_all_emails()
