import html
import json
import re
from email.message import Message
from typing import Any, Dict


def strip_html(html_content: str) -> str:
    """Simple HTML tags removal."""
    clean = re.sub(
        r"<(script|style).*?>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE
    )
    clean = re.sub(r"<[^>]*>", " ", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def extract_body(msg: Message) -> str:
    """Extract the text body from the email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    break
                except Exception:
                    pass
            elif content_type == "text/html" and not body:
                # If we only have HTML, save it as a backup
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except Exception:
            pass

    # If it's HTML, strip tags to make text parsing easier
    if "<body" in body or "<html" in body or "</div" in body:
        body = strip_html(body)

    # Unescape HTML entities
    body = html.unescape(body)
    return body.strip()


def parse_alert(subject: str, body: str) -> Dict[str, Any]:
    """
    Parse subject and body to extract stock symbol, strategy, and alert price.
    Supports JSON format or plain text matching.
    """
    result = {
        "symbol": "",
        "strategy": "Daily",  # Default strategy is Daily if not Intraday
        "action": "ALERT",
        "alert_price": None,
        "score": "",
        "dir_prob": "",
        "net_sigma": "",
        "grade": "",
        "align": "",
        "premium": "",
        "wrong_if": "",
        "context": "",
        "subject": subject,
        "body": body,
    }

    # Clean common exchange prefixes (e.g. NASDAQ:MSFT -> MSFT)
    body_clean = re.sub(
        r"\b(?:NASDAQ|NYSE|AMEX|BATS|CBOE|ARCX|TSX|LSE|ASX|FX|BINANCE|COINBASE|OANDA|FOREXCOM|INDEX|CME|CBOT|NYMEX|COMEX|ICE|EUREX|NSE|BSE)\s*:\s*([A-Za-z0-9\.\^_-]{1,8})\b",
        r"\1",
        body,
        flags=re.IGNORECASE,
    )

    # Normalize subject and body
    subject_lower = subject.lower()
    body_lower = body.lower()
    full_text = f"{subject} {body_clean}".lower()

    # Classify strategy (Intraday vs. Daily)
    if "intraday" in subject_lower or "intraday" in body_lower:
        result["strategy"] = "Intraday"
    elif "screener" in subject_lower:
        result["strategy"] = "Daily"

    # 1. Try to find and parse JSON block in body
    json_match = re.search(r"\{.*?\}", body, re.DOTALL)
    if json_match:
        try:
            json_data = json.loads(json_match.group(0))

            # Extract symbol
            for key in ["ticker", "symbol", "stock", "asset"]:
                if key in json_data:
                    result["symbol"] = str(json_data[key]).upper().strip()
                    break

            # Extract strategy (Swing/Daily)
            for key in ["strategy", "type", "interval", "timeframe"]:
                if key in json_data:
                    val = str(json_data[key]).capitalize().strip()
                    if val in ["Intraday", "Daily", "Swing"]:
                        result["strategy"] = "Daily" if val == "Swing" else val
                    break

            # If setup is present, it's a screener/swing trade
            if "setup" in json_data:
                result["strategy"] = "Daily"

            # Extract price
            for key in ["price", "close", "last", "value", "exit_px", "entry_px", "px"]:
                if key in json_data:
                    try:
                        result["alert_price"] = float(json_data[key])
                    except ValueError:
                        pass
                    break

            # Fallback: Check if price is 0 or None, and try to extract from 'plan' key (e.g. "In 745.32")
            if (
                result["alert_price"] is None or result["alert_price"] == 0.0
            ) and "plan" in json_data:
                plan_str = str(json_data["plan"])
                in_match = re.search(
                    r"\b(?:In|Held)\s+([0-9]+(?:\.[0-9]+)?)\b", plan_str, re.IGNORECASE
                )
                if in_match:
                    try:
                        result["alert_price"] = float(in_match.group(1))
                    except ValueError:
                        pass

            # Extract action
            for key in ["action", "event", "side", "direction", "signal"]:
                if key in json_data:
                    result["action"] = str(json_data[key]).upper().strip()
                    break

            # Store all raw keys from the webhook JSON in the parsed result
            for k, v in json_data.items():
                result[k] = v

            # Standard fallback lookups
            result["score"] = str(json_data.get("score", ""))
            result["dir_prob"] = str(json_data.get("dir_prob", ""))
            result["net_sigma"] = str(json_data.get("net_sigma", ""))
            result["grade"] = str(json_data.get("grade", ""))
            result["align"] = str(json_data.get("align", ""))
            result["premium"] = str(json_data.get("premium", ""))
            result["wrong_if"] = str(json_data.get("wrong_if", ""))
            result["context"] = str(json_data.get("context", ""))

        except Exception:
            # If JSON parsing fails, fall back to plain text regexes
            pass

    # 2. Plain Text Ticker Prefix Heuristic (e.g. "USB: 💎 A+ TREND LONG SETUP!")
    if not result["symbol"]:
        # Match a symbol at the start of a line or sentence followed by a colon
        ticker_prefix_match = re.search(r"(?:^|\n)\s*([A-Za-z0-9\.\^_-]{1,8})\s*:", body_clean)
        if ticker_prefix_match:
            ticker = ticker_prefix_match.group(1).upper().strip()
            # Verify it looks like a ticker and not a word
            if ticker not in ["ALERT", "SUBJECT", "BODY", "PRICE", "NOTE", "WARNING"]:
                result["symbol"] = ticker

    # 3. Regex parsing fallback (if symbol is not found yet)
    if not result["symbol"]:
        # Pattern matching ticker like "Symbol: AAPL" or "Ticker: TSLA"
        symbol_patterns = [
            r"(?:symbol|ticker)\s*[:=-]\s*([A-Za-z0-9\.\^_-]{1,8})\b",
            r"\b([A-Z]{1,5})\b\s*(?:alert|triggered)",
        ]
        for pattern in symbol_patterns:
            match = re.search(pattern, body_clean, re.IGNORECASE)
            if not match:
                match = re.search(pattern, subject, re.IGNORECASE)
            if match:
                result["symbol"] = match.group(1).upper()
                break

    # 4. Determine action/signal from full text if not found from JSON
    if result["action"] == "ALERT":
        action_match = re.search(r"\b(buy|sell|entry|exit|long|short|bullish|bearish)\b", full_text)
        if action_match:
            result["action"] = action_match.group(1).upper()

    # 5. Extract price fallback (if price is not found yet)
    if result["alert_price"] is None:
        price_patterns = [
            r"(?:price|close|last|at)\s*[:=-]?\s*\$?\s*([0-9]+(?:\.[0-9]+)?)\b",
            r"\b\$([0-9]+(?:\.[0-9]+)?)\b",
        ]
        for pattern in price_patterns:
            match = re.search(pattern, body_clean, re.IGNORECASE)
            if match:
                try:
                    result["alert_price"] = float(match.group(1))
                    break
                except ValueError:
                    pass

    # 6. Final heuristics if symbol is still missing
    if not result["symbol"]:
        # If the subject contains uppercase words, look for a likely ticker (1-5 letters)
        # E.g. "TradingView Alert: AAPL intraday trigger"
        uppercase_words = re.findall(r"\b([A-Z]{1,5})\b", subject)
        # Filter out common short terms like "TV", "US", "USD", "BTC" unless it's the only uppercase
        filtered_words = [
            w for w in uppercase_words if w not in ("TV", "OK", "AM", "PM", "UTC", "EST")
        ]
        if filtered_words:
            result["symbol"] = filtered_words[0]

    # Clean symbol (remove exchange prefix if present, e.g. NASDAQ:AAPL -> AAPL)
    if ":" in result["symbol"]:
        result["symbol"] = result["symbol"].split(":")[-1]

    return result
