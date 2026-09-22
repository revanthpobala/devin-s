import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src import config
from src.clients.gmail_client import GmailClient
from src.clients.price_client import get_current_price
from src.tracking.position_monitor import PositionManager

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
    """Append a screener-sourced ticker to today's research queue (SQLite) and survivors.json.
    Dedupes by ticker; never overwrites or removes existing entries."""
    from src.tracking.alert_db import queue_for_research
    queue_for_research(
        symbol=symbol,
        date_str=date_str,
        setup=setup,
        source="screener",
        reason=f"Screener candidate setup: {setup}",
    )


_enrichment_queue: queue.Queue = queue.Queue()


def ingest_alert_fast(alert: dict, gmail: Optional[GmailClient] = None) -> bool:
    """Stage 1: FAST Ingestion (<15ms).
    
    1. Immediately records alert to SQLite (trading_alerts.db) for instant Cockpit UI visibility.
    2. Immediately routes Intraday directional alerts into PositionManager (positions.json updated,
       monitor threads started/stopped with zero delay).
    3. Immediately marks email as read in Gmail so no duplicates occur.
    4. Enqueues the alert for asynchronous downstream enrichment (Sheets + Local LLM).
    
    This function NEVER waits for Google Sheets API, News API, or Local LLM inference.
    """
    symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
    strategy = str(alert.get("strategy") or "Intraday").strip()
    alert_price = alert.get("alert_price")
    email_id = alert.get("email_id")

    if not symbol or strategy not in ["Intraday", "Daily", "RSI2"]:
        logger.info(f"Skipping alert for {symbol} as strategy is {strategy}.")
        if email_id and gmail:
            try:
                gmail.mark_as_read(email_id)
            except Exception:
                pass
        return False

    # 1. Immediate SQLite Persistence (Durable Audit Trail & Cockpit UI)
    from src.tracking.alert_db import (
        get_eastern_date_str,
        record_alert as _record_alert_db,
        update_routing_stage as _update_routing_stage_db,
    )
    try:
        inserted = _record_alert_db(alert)
        if not inserted:
            # Already in DB — deduplicated. Mark read and skip to avoid ghost routing.
            logger.debug(f"[DEDUP] {symbol} alert already in SQLite. Skipping.")
            if email_id and gmail:
                try:
                    gmail.mark_as_read(email_id)
                except Exception:
                    pass
            return False
    except Exception as e_rec:
        logger.error(f"Immediate SQLite record error for {symbol}: {e_rec}. Ingestion aborted to prevent unaudited routing.")
        return False

    # 2. Durable ENQUEUED stage: alert is recorded and about to be handed to the
    #    PositionManager. Crash between ENQUEUED and ROUTED is recoverable via
    #    replay_unrouted_alerts() (which picks up both RECORDED and ENQUEUED).
    if alert.get("message_id"):
        _update_routing_stage_db(alert["message_id"], "ENQUEUED")

    # 3. Immediate Position State & Monitor Routing
    from src.tracking.position_monitor import _is_exit_event

    raw_action = str(alert.get("action", "")).upper().strip()
    raw_side = str(alert.get("side", "")).upper().strip()
    is_exit = _is_exit_event(alert)
    is_non_trade = not is_exit and (
        raw_side == "NEUTRAL"
        or raw_action in ("NEUTRAL", "ALERT", "NONE", "UNKNOWN", "")
        or bool(alert.get("setup"))
    )
    if strategy == "Intraday" and not is_non_trade:
        try:
            if is_exit:
                exit_decision = _position_manager.handle_exit_alert(alert)
                if exit_decision.get("action") == "VETO_HOLD":
                    alert["llm_decision"] = "🛡️ VETOED PREMATURE TV EXIT — HOLDING"
                    alert["llm_playbook"] = f"VETO REASON: {exit_decision.get('reason')}"
                    from src.tracking.alert_db import update_alert_llm
                    if alert.get("message_id"):
                        update_alert_llm(
                            alert["message_id"],
                            alert["llm_decision"],
                            alert["llm_playbook"],
                            status="PROCESSED",
                        )
            else:
                _position_manager.route_alert(alert)
            if alert.get("message_id"):
                _update_routing_stage_db(alert["message_id"], "ROUTED")
        except Exception as e:
            logger.warning(f"PositionManager routing failed for {symbol}: {e}")
    else:
        logger.debug(
            f"Skipping PositionManager routing for {symbol} (strategy={strategy}, side={raw_side}, action={raw_action}, setup={alert.get('setup')})"
        )
        if alert.get("message_id"):
            _update_routing_stage_db(alert["message_id"], "ROUTED")

    logger.info(
        f"⚡ [INGESTED] Symbol: {symbol}, Strategy: {strategy}, Action: {raw_action}, Price: {alert_price}"
    )

    # 4. Mark email as read in Gmail immediately
    if email_id and gmail:
        try:
            gmail.mark_as_read(email_id)
        except Exception as e_mark:
            logger.debug(f"Failed to mark email {email_id} as read: {e_mark}")

    # 5. Enqueue for background asynchronous enrichment
    _enrichment_queue.put(alert)
    return True


def replay_unrouted_alerts():
    """Recover unrouted events at startup (e.g. after crash-after-record window)."""
    try:
        from src.tracking.alert_db import get_unrouted_alerts, update_routing_stage
        unrouted = get_unrouted_alerts()
        if unrouted:
            logger.info(f"[recovery] Found {len(unrouted)} unrouted alert(s) in SQLite. Replaying routing...")
            for a in unrouted:
                sym = a.get("symbol")
                strat = a.get("strategy", "Intraday")
                msg_id = a.get("message_id")
                if strat == "Intraday":
                    try:
                        _position_manager.route_alert(a)
                        if msg_id:
                            update_routing_stage(msg_id, "ROUTED")
                        logger.info(f"[recovery] Successfully recovered routing for {sym}")
                    except Exception as e:
                        logger.warning(f"[recovery] Failed recovering routing for {sym}: {e}")
                else:
                    if msg_id:
                        update_routing_stage(msg_id, "ROUTED")
                    logger.info(f"[recovery] Marked non-intraday alert {sym} ({strat}) as ROUTED")
    except Exception as e_recov:
        logger.debug(f"[recovery] Error during unrouted alerts check: {e_recov}")


def process_alert_enrichment(alert: dict, sheets=None):
    """Stage 2: Asynchronous Downstream Enrichment.

    Runs in background worker threads:
    - Fetches market price & live news
    - Runs scraping-free local LLM analysis (#ponytail triage)
    - Updates SQLite with LLM decision

    Completely decoupled from Gmail polling and Google Sheets.
    """
    symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
    strategy = str(alert.get("strategy") or "Intraday").strip()
    alert_price = alert.get("alert_price")
    action_val = alert.get("action", "ALERT")
    timestamp_str = alert.get("timestamp")

    from src.tracking.alert_db import (
        get_eastern_date_str,
        update_routing_stage as _update_routing_stage_db,
    )

    # 1. Market price at processing time
    if strategy == "Daily" and alert_price is not None:
        market_price = alert_price
    else:
        try:
            market_price = get_current_price(symbol)
        except Exception:
            market_price = None
        if market_price is None:
            market_price = alert_price

    # 2. News data
    news_data = None
    if strategy == "Intraday":
        try:
            from src.clients.news_client import get_ticker_news
            news_data = get_ticker_news(symbol)
        except Exception as e:
            logger.debug(f"Failed to fetch news for {symbol}: {e}")

    # 3. Screener queueing if applicable
    date_str = get_eastern_date_str(timestamp_str)
    if (strategy == "Daily" and alert.get("setup")) or alert.get("setup"):
        try:
            _append_screener_candidate(symbol, alert.get("setup"), date_str)
        except Exception as e_scr:
            logger.debug(f"Screener candidate append error for {symbol}: {e_scr}")

    # 4. Local LLM Analysis (#ponytail triage)
    try:
        from src.tracking.alert_evaluator import evaluate_alert_payload
        eval_res = evaluate_alert_payload(alert, use_tools=False)
        llm_decision = eval_res.get("llm_decision", "")
        if llm_decision:
            logger.info(f"AI decision written to SQLite for {symbol}: {llm_decision}")
        # Veto sync: if the AI vetoed this entry but routing already opened a position
        # (routing uses default grade/score before the real Pine payload was evaluated),
        # close the phantom position so positions.json and the UI stay consistent.
        is_exit = any(k in str(alert.get("action") or "").upper() for k in ("EXIT", "CLOSE", "STOP", "FLATTEN", "CUT"))
        if strategy == "Intraday" and not is_exit:
            v_up = llm_decision.upper()
            if "STAND ASIDE" in v_up or "DAY PAUSE" in v_up:
                try:
                    from src.tracking.position_state import close_position, list_open
                    open_pos = list_open()
                    pos_rec = open_pos.get(symbol)
                    # Only close positions opened today (avoid killing stale rehydrated state)
                    if pos_rec and str(pos_rec.get("opened_at", ""))[:10] == date_str:
                        try:
                            from src.clients.price_client import get_current_price
                            close_px = get_current_price(symbol, context="execution")
                        except Exception:
                            close_px = None
                        closed = close_position(
                            symbol,
                            exit_price=close_px,
                            exit_reason=f"AI triage veto after routing ({llm_decision[:80]})",
                        )
                        if closed:
                            logger.warning(f"[veto-sync] 🛡️ {symbol} position opened by routing but vetoed by AI triage — closed at ${closed.get('exit_price')}.")
                except Exception as e_vsync:
                    logger.debug(f"Veto sync check failed for {symbol}: {e_vsync}")
        if alert.get("message_id"):
            _update_routing_stage_db(alert["message_id"], "COMPLETED")
    except Exception as e_eval:
        logger.warning(f"Local alert evaluation failed for {symbol}: {e_eval}")


# Backward-compatible alias for single-threaded / legacy callers
process_alert = process_alert_enrichment


def start_enrichment_workers(
    num_workers: int = 2, stop_event: Optional[threading.Event] = None
) -> list[threading.Thread]:
    """Start background worker threads to drain and process the enrichment queue."""
    stop = stop_event or threading.Event()

    def _worker(worker_id: int):
        logger.info(f"[EnrichmentWorker-{worker_id}] started.")
        while not stop.is_set():
            try:
                alert = _enrichment_queue.get(timeout=1.5)
            except queue.Empty:
                # During idle polling, autonomously check if there are pending alerts in SQLite and triage them!
                if worker_id == 1:
                    try:
                        from src.tracking.alert_evaluator import evaluate_batch_pending
                        evaluate_batch_pending(limit=2)
                    except Exception:
                        pass
                continue

            try:
                process_alert_enrichment(alert)
            except Exception as e:
                logger.error(f"[EnrichmentWorker-{worker_id}] Error: {e}", exc_info=True)
            finally:
                _enrichment_queue.task_done()
        logger.info(f"[EnrichmentWorker-{worker_id}] stopped.")

    workers = []
    for i in range(num_workers):
        t = threading.Thread(target=_worker, args=(i + 1,), name=f"EnrichmentWorker-{i+1}", daemon=True)
        t.start()
        workers.append(t)
    return workers


class GmailIngestionThread(threading.Thread):
    """Dedicated background thread for Gmail ingestion.
    
    Continuously polls Gmail for unread TradingView alerts on its own independent loop.
    Never blocked by downstream LLM inference, news scraping, or Google Sheets latency.
    """

    def __init__(self, poll_interval: int = 15, stop_event: Optional[threading.Event] = None):
        super().__init__(name="GmailIngestorThread", daemon=True)
        self.poll_interval = poll_interval
        self._stop = stop_event or threading.Event()
        self.gmail = GmailClient()

    def run(self):
        logger.info(f"🚀 Gmail Ingestion Thread started (interval={self.poll_interval}s).")
        while not self._stop.is_set():
            if is_market_hours() or getattr(config, "DEBUG_FORCE_MARKET_OPEN", False):
                try:
                    self._poll_cycle()
                except Exception as e:
                    logger.error(f"Error during Gmail ingestion cycle: {e}", exc_info=True)
            else:
                now_mt = datetime.now(ZoneInfo("America/Denver"))
                if now_mt.minute % 15 == 0 and now_mt.second < 20:
                    logger.info("Outside market control window. Ingestion idle.")
            self._stop.wait(self.poll_interval)
        logger.info("Gmail Ingestion Thread stopped.")

    def _poll_cycle(self):
        if not self.gmail.connect():
            logger.warning("Could not establish Gmail connection. Retrying next cycle.")
            return

        try:
            alerts = self.gmail.fetch_new_alerts(limit=100)
            if alerts:
                logger.info(f"📥 Found {len(alerts)} new alert(s) in Gmail. Ingesting immediately...")
                for alert in alerts:
                    ingest_alert_fast(alert, self.gmail)
            else:
                logger.debug("No new TradingView alerts found in Gmail.")
        finally:
            try:
                self.gmail.disconnect()
            except Exception:
                pass


def run_tracker(force_run: bool = False):
    """One-shot execution cycle: check emails, ingest immediately, and process enrichment."""
    effective_force = force_run or getattr(config, "DEBUG_FORCE_MARKET_OPEN", False)
    if not is_market_hours() and not effective_force:
        logger.info("Market is closed. Sleeping...")
        return

    logger.info("Checking for TradingView alerts (one-shot)...")
    _position_manager.start()
    replay_unrouted_alerts()

    gmail = GmailClient()
    if not gmail.connect():
        logger.error("Could not establish Gmail connection. Skipping.")
        return

    try:
        alerts = gmail.fetch_new_alerts(limit=100)
        if not alerts:
            logger.info("No new TradingView alerts found in Gmail.")
            return

        logger.info(f"Ingesting {len(alerts)} alert(s)...")
        for alert in alerts:
            ingest_alert_fast(alert, gmail)

        # Drain enrichment queue for one-shot mode
        while not _enrichment_queue.empty():
            try:
                alert = _enrichment_queue.get_nowait()
                process_alert_enrichment(alert)
                _enrichment_queue.task_done()
            except queue.Empty:
                break
    finally:
        try:
            gmail.disconnect()
        except Exception:
            pass


def is_market_hours() -> bool:
    """Check if current Mountain Time is within configured market hours (Monday-Friday, 7:15 AM - 8:00 PM MT)."""
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

    if args.once:
        run_once = True
    elif args.loop:
        run_once = False
    else:
        run_once = not config.LOOP_MODE

    if run_once:
        logger.info("Running in ONE-SHOT mode.")
        try:
            run_tracker(force_run=True)
        finally:
            _position_manager.stop()
        logger.info("One-shot run complete. Exiting.")
    else:
        interval = int(getattr(config, "POLLING_INTERVAL", 15))
        logger.info(f"Running in MULTI-THREADED LOOP mode. Ingestion interval: {interval}s.")
        stop_event = threading.Event()

        # 1. Start live Position Manager & recover unrouted alerts
        _position_manager.start()
        replay_unrouted_alerts()

        # 2. Start background enrichment workers
        start_enrichment_workers(num_workers=2, stop_event=stop_event)

        # 3. Start dedicated Gmail Ingestion Thread
        ingestor_thread = GmailIngestionThread(poll_interval=interval, stop_event=stop_event)
        ingestor_thread.start()

        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received. Stopping threads...")
            stop_event.set()
            ingestor_thread.join(timeout=5.0)
        finally:
            stop_event.set()
            _position_manager.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
