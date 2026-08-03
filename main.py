import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src import config
from src.clients.gmail_client import GmailClient
from src.clients.price_client import get_current_price
from src.tracking.position_monitor import PositionManager
from src.tracking.sheets_tracker import SheetsTracker

# Singleton: lives for the lifetime of the tracker process. Routes every alert
# (entry/exit) into the open-position state + per-ticker monitor threads.
_position_manager = PositionManager(
    poll_interval=int(getattr(config, "POSITION_POLL_INTERVAL", 60))
)

# Force UTF-8 on stdout so emoji in LLM output doesn't crash the logger on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Configure logging
os.makedirs(config.LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "tracker.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("tracker")


def query_local_llm_for_trade(
    alert_data: dict,
    symbol: str,
    strategy: str,
    news_data: dict | None = None,
    prior_position: dict | None = None,
) -> tuple[str, str]:
    """
    Run the revanth-0dte.md rules card against every incoming alert.
    Always uses revanth-0dte.md as the system prompt — no exceptions.
    `news_data` (from get_ticker_news) is injected as live news context so the
    intraday LLM decision actually sees the Alpaca/Finnhub headlines we fetch.
    `prior_position` is the open-position snapshot taken BEFORE this alert was
    routed — so the model sees what we held going INTO this alert, never the
    position this same alert just opened.
    """
    import json
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src import config

    # 1. Load the 0DTE gem (revanth-0dte.md) — always, for every alert
    rules_file = "revanth-0dte.md"
    rules_path = config.BASE_DIR / "gems" / rules_file
    if not os.path.exists(rules_path):
        logger.warning(f"revanth-0dte.md not found at {rules_path}. Skipping LLM decision.")
        return "", ""

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except Exception as e:
        logger.warning(f"Failed to read revanth-0dte.md: {e}")
        return "", ""

    # 1b. Open-position state as it was BEFORE this alert (snapshot passed in by
    # the caller). Using the pre-alert snapshot is critical: an ENTRY alert opens
    # a position asynchronously, so reading live state here would show the model
    # the very position it's being asked to judge — making it think we're already
    # in the trade. If we were flat going into this alert, it says so.
    our_position = prior_position
    if our_position is not None:
        position_lines = (
            f"- Our open position (held BEFORE this alert): {our_position.get('side', '?')} @ {our_position.get('entry_price', '?')}\n"
            f"  Stop: {our_position.get('stop', '?')}  |  Target: {our_position.get('target', '?')}\n"
            f"  Opened at: {our_position.get('opened_at', '?')}\n"
            f"  Last eval: {our_position.get('last_eval', '') or '(none)'}\n"
        )
    else:
        position_lines = "- Our open position: NONE (flat going into this alert — this is a fresh signal to evaluate, we are NOT already in it)\n"

    # 2. Get live market context
    try:
        price = get_current_price(symbol)
        vix = get_current_price("VIX")
    except Exception:
        price = "N/A"
        vix = "N/A"

    current_time_et = datetime.now(ZoneInfo("America/New_York")).strftime("%I:%M %p ET")

    # 4. Pre-fetch macro/tape context (Finnhub + FMP) for STEP 2 of the gem
    try:
        from src.clients.macro_client import build_macro_context

        macro_context = build_macro_context(ticker=symbol)
    except Exception as e:
        logger.warning(f"macro_client failed: {e}")
        macro_context = "Macro context: unavailable"

    # 3. Build clean alert payload — strip internal plumbing keys
    skip_keys = {"body", "subject", "email_id", "timestamp", "strategy", "symbol"}
    alert_payload = {k: v for k, v in alert_data.items() if k not in skip_keys}

    # Ensure ticker is always present
    alert_payload["ticker"] = symbol

    # Use actual event/action from the alert — do NOT override with fake "ENTRY"
    event_val = alert_payload.get("event", alert_payload.get("action", "UNKNOWN"))
    action_val = alert_payload.get("action", "UNKNOWN")

    # Derive bias only if missing
    if "bias" not in alert_payload:
        alert_payload["bias"] = (
            "BULL"
            if "CALLS" in str(action_val).upper()
            else ("BEAR" if "PUTS" in str(action_val).upper() else "NEUTRAL")
        )

    user_prompt = f"""Current Live Context:
- Current Time (ET): {current_time_et}
- Ticker Underlying Price: {price}
- VIX Index Level: {vix}
- Event Type: {event_val}
- Action: {action_val}

{macro_context}

Live News Context (last ~2 days, from Alpaca/Finnhub):
- News Sentiment: {news_data.get("sentiment", "NEUTRAL") if news_data else "NEUTRAL"}
- News Catalyst: {news_data.get("catalyst", "none") if news_data else "none"}
- News Source: {news_data.get("source", "") if news_data else ""}
- Recent Headlines:
{(news_data.get("raw_news") or "No recent news found.").strip() if news_data else "No recent news found."}

OUR POSITION STATE GOING INTO THIS ALERT (ground truth, captured BEFORE this alert was routed):
{position_lines}

Webhook JSON Payload (authoritative — use this, not the screenshot path):
{json.dumps(alert_payload, indent=2)}

Raw Alert Body:
{alert_data.get("body", "")}

Apply the revanth-0dte.md rules card to this alert and return your GO/NO-GO decision in the required OUTPUT format.
"""

    try:
        from src.clients.llm_client import query_local_llm

        response_text = query_local_llm(system_prompt, user_prompt)

        if response_text:
            cleaned_text = response_text.strip()

            # Strip markdown code fences if model wrapped output
            if cleaned_text.startswith("```"):
                lines = cleaned_text.splitlines()
                lines = lines[1:] if lines[0].strip().startswith("```") else lines
                lines = lines[:-1] if lines and lines[-1].strip().startswith("```") else lines
                cleaned_text = "\n".join(lines).strip()

            lines = cleaned_text.splitlines()

            # Extract the formatted header line: [TICKER] [TIME] — emoji ACTION
            header_line = ""
            for line in lines:
                l = line.strip()
                if l.startswith("[") and any(
                    x in l for x in ["🟢", "🔴", "⏸️", "⛔", "TAKE", "WAIT", "STAND"]
                ):
                    header_line = l
                    break

            if not header_line:
                for line in lines:
                    if line.strip():
                        header_line = line.strip()
                        break

            return header_line, cleaned_text

    except Exception as e:
        logger.warning(f"LLM inference failed for {symbol}: {e}", exc_info=True)

    return "", ""


def _append_screener_candidate(symbol: str, setup: str, date_str: str):
    """Append a screener-sourced ticker to today's survivors.json so it flows
    through the existing local-research pipeline unchanged. Dedupes by ticker;
    never overwrites or removes existing entries."""
    out_dir = config.BASE_DIR / "data" / "raw" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "survivors.json"
    survivors = []
    if manifest_path.exists():
        try:
            raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(raw_data, list):
                survivors = raw_data
        except Exception as e:
            logger.warning(f"Failed to read survivors.json, will not overwrite: {e}")
            return
    existing = {(s.get("Ticker") or s.get("ticker") or s.get("Symbol") or "").upper() for s in survivors}
    if symbol.upper() not in existing:
        survivors.append({"Ticker": symbol.upper(), "source": "screener", "screener_setup": setup})
        manifest_path.write_text(json.dumps(survivors, indent=2), encoding="utf-8")
        logger.info(f"[screener] Added {symbol} to today's research candidates (setup={setup}).")


def process_alert(alert, sheets):
    symbol = alert.get("symbol")
    strategy = alert.get("strategy")
    alert_price = alert.get("alert_price")
    email_id = alert.get("email_id")

    if not symbol or strategy not in ["Intraday", "Daily"]:
        logger.info(f"Skipping alert for {symbol} as strategy is {strategy}.")
        return email_id  # Return email_id to mark as read

    from src.tracking.position_state import get_position as _get_position

    prior_position = _get_position(symbol)

    # Route into the open-position state + monitor threads: entry alerts open a
    # position + spawn a monitor thread; exit alerts close it + stop the thread.
    # Sheets remains a mirror below.
    try:
        _position_manager.route_alert(alert)
    except Exception as e:
        logger.warning(f"PositionManager routing failed for {symbol}: {e}")

    logger.info(
        f"New alert received -> Symbol: {symbol}, Strategy: {strategy}, Alert Price: {alert_price}"
    )

    # 3. Get actual market price at alert processing time
    market_price = get_current_price(symbol)
    if market_price is None:
        logger.warning(f"Could not retrieve market price for {symbol}. Will log alert price only.")

    # Get the action type and log it
    action_val = alert.get("action", "ALERT")
    logger.info(f"Alert action: {action_val}")

    news_headline = ""
    news_url = ""
    news_source = ""
    news_sentiment = ""
    news_catalyst = ""
    news_data = None  # pre-initialized; query_local_llm_for_trade guards with `if news_data`    # Only fetch news for Intraday (Swing trades do not need it right now)
    if strategy == "Intraday":
        try:
            from src.clients.news_client import get_ticker_news

            news_data = get_ticker_news(symbol)
            news_url = news_data.get("url", "")
            news_source = news_data.get("source", "")
            news_sentiment = news_data.get("sentiment", "")
            news_catalyst = news_data.get("catalyst", "")
        except Exception as e:
            logger.warning(f"Failed to fetch news for {symbol}: {e}")

    # 4. Log alert immediately to Google Sheet (LLM columns blank first)
    timestamp_str = alert.get("timestamp")
    success = sheets.log_alert(
        timestamp=timestamp_str,
        symbol=symbol,
        action=action_val,
        strategy=strategy,
        alert_price=alert_price,
        market_price=market_price,
        raw_message=alert.get("body", ""),
        score=alert.get("score", ""),
        dir_prob=alert.get("dir_prob", ""),
        net_sigma=alert.get("net_sigma", ""),
        grade=alert.get("grade", ""),
        align=alert.get("align", ""),
        premium=alert.get("premium", ""),
        wrong_if=alert.get("wrong_if", ""),
        context=alert.get("context", ""),
        news_url=news_url,
        news_source=news_source,
        news_sentiment=news_sentiment,
        news_catalyst=news_catalyst,
        llm_decision="",  # Blank initially
        llm_playbook="",  # Blank initially
    )

    # 5. Mark email as read and update Trades Ledger only if logged successfully to Sheet
    if success:
        # Update Trades Ledger immediately
        sheets.log_trade_action(
            timestamp=timestamp_str,
            symbol=symbol,
            action=action_val,
            alert_price=alert_price,
            market_price=market_price,
            strategy=strategy,
            news_url=news_url,
            news_source=news_source,
            news_sentiment=news_sentiment,
            news_catalyst=news_catalyst,
            raw_alert=alert,
        )

        # 6. Run LLM analysis for Intraday alerts only (Swing uses future infrastructure)
        date_str = (
            timestamp_str[:10] if timestamp_str else datetime.now().strftime("%Y-%m-%d")
        )
        if strategy == "Daily" and alert.get("setup"):
            _append_screener_candidate(symbol, alert.get("setup"), date_str)

        if strategy == "Intraday":
            # Since threads might update last_logged_row, we use the value cached in sheet if possible,
            # but for now we rely on the tracker returning True. Note: last_logged_row might be slightly off
            # if multiple threads write to the same sheet exactly concurrently.
            row_num = sheets.last_logged_row
            logger.info(
                f"Alert logged. Running local AI trade analysis for {symbol} [{action_val}] (Row {row_num})..."
            )
            llm_decision, llm_playbook = query_local_llm_for_trade(
                alert, symbol, strategy, news_data=news_data, prior_position=prior_position
            )
            if llm_decision or llm_playbook:
                sheets.update_llm_decision(date_str, row_num, llm_decision, llm_playbook)
                logger.info(f"AI decision updated in Row {row_num}: {llm_decision}")
            else:
                logger.warning(f"LLM returned empty response for {symbol} [{action_val}].")
        else:
            logger.info(
                f"Alert logged to spreadsheet. Routing {symbol} ({alert.get('setup')}) to research candidate list."
            )

        return email_id
    else:
        logger.error(
            f"Failed to log alert for {symbol} to Google Sheets. Retaining email as unread for retry."
        )
        return None


def run_tracker():
    """Main execution cycle: check emails, log to sheets, run LLM."""
    # 1. Check if market is open (or use override for testing)
    force_run = getattr(config, "DEBUG_FORCE_MARKET_OPEN", False)

    if not is_market_hours() and not force_run:
        logger.info("Market is closed. Sleeping...")
        return

    logger.info("Market is open (or forced). Checking for TradingView alerts...")

    # Initialize Google Sheets client
    sheets = SheetsTracker()

    # Start the live position monitor (idempotent; rehydrates open positions
    # from data/positions.json so monitors survive a tracker restart).
    _position_manager.start()

    # 2. Connect to Gmail and fetch new alerts
    gmail = GmailClient()
    if not gmail.connect():
        logger.error("Could not establish Gmail connection. Skipping this cycle.")
        return

    try:
        alerts = gmail.fetch_new_alerts()
        if not alerts:
            logger.info("No new TradingView alerts found in Gmail.")
            return

        logger.info(f"Processing {len(alerts)} new alert(s) in parallel via local LLM server...")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        successful_email_ids = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_alert = {
                executor.submit(process_alert, alert, sheets): alert for alert in alerts
            }
            for future in as_completed(future_to_alert):
                try:
                    eid = future.result()
                    if eid:
                        successful_email_ids.append(eid)
                except Exception as exc:
                    logger.error(f"Alert processing generated an exception: {exc}")

        # Mark all successful emails as read sequentially to avoid IMAP thread-safety issues
        for eid in successful_email_ids:
            gmail.mark_as_read(eid)

    except Exception as e:
        logger.error(f"Error during tracker execution cycle: {e}", exc_info=True)
    finally:
        gmail.disconnect()


def is_market_hours() -> bool:
    """Check if current Mountain Time is within configured market hours (Monday-Friday)."""
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    if now_mt.weekday() >= 5:  # Saturday or Sunday
        return False
    start_time = now_mt.replace(
        hour=getattr(config, "MARKET_OPEN_HOUR", 7),
        minute=getattr(config, "MARKET_OPEN_MINUTE", 15),
        second=0,
        microsecond=0,
    )
    end_time = now_mt.replace(
        hour=getattr(config, "MARKET_CLOSE_HOUR", 20),
        minute=getattr(config, "MARKET_CLOSE_MINUTE", 0),
        second=0,
        microsecond=0,
    )
    return start_time <= now_mt <= end_time


def main():
    parser = argparse.ArgumentParser(description="TradingView Alert Tracker to Google Sheets")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit immediately (ignores LOOP_MODE in env)",
    )
    parser.add_argument(
        "--loop", action="store_true", help="Run continuously in a loop (ignores LOOP_MODE in env)"
    )
    args = parser.parse_args()

    # Validate config
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration Error: {e}")
        logger.error("Please configure your .env file with valid credentials.")
        sys.exit(1)

    # Determine run mode
    if args.once:
        run_once = True
    elif args.loop:
        run_once = False
    else:
        # Fall back to config file setting
        run_once = not config.LOOP_MODE

    if run_once:
        logger.info("Running in ONE-SHOT mode.")
        try:
            run_tracker()
        finally:
            _position_manager.stop()
        logger.info("One-shot run complete. Exiting.")
    else:
        logger.info(f"Running in LOOP mode. Polling interval: {config.POLLING_INTERVAL} seconds.")
        try:
            while True:
                if is_market_hours():
                    run_tracker()
                else:
                    # Log only periodically to avoid filling up log file
                    now_mt = datetime.now(ZoneInfo("America/Denver"))
                    if now_mt.minute % 15 == 0 and now_mt.second < 60:
                        logger.info(
                            "Outside market hours (7:15 AM - 3:00 PM MT Mon-Fri). Skipping check cycle."
                        )
                time.sleep(config.POLLING_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received. Stopping tracker loop.")
        finally:
            _position_manager.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
