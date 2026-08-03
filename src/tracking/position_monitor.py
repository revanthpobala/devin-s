"""
Live position monitoring — the "what are we in, and is it still valid" loop.

Design (agreed with the user):
- data/positions.json is the SINGLE SOURCE OF TRUTH for open trades.
- A TradingView ENTRY alert opens a position (upsert into state) and spins up a
  PositionMonitor thread for that ticker.
- A TradingView EXIT alert closes the position (remove from state) and stops
  the thread. The LLM NEVER authorizes an exit — TV's alert is authoritative,
  same as the entry.
- Each monitor thread polls live quotes every N minutes and:
    1. hard-checks stop/target deterministically (NO LLM, no hallucination),
    2. calls the LOCAL 9B ONLY to generate a playbook/commentary string,
    3. writes last_price + last_eval back into state (Sheets is a mirror).

The local 9B is a narrator, not a risk manager. Exits are external (TV alerts).
"""

import json
import logging
import queue
import re
import threading
from datetime import datetime, timezone

from src import config
from src.clients.price_client import get_current_price
from src.tracking.position_state import close_position, list_open, open_position, update_position

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _is_exit_event(alert: dict) -> bool:
    """Heuristic: does this alert describe closing/exiting the position?

    TV alerts carry an `event`/`action` like 'EXIT', 'CLOSE', 'TAKE PROFIT',
    'STOP HIT', or a 'CLOSING' flag. We match ONLY the authoritative action/event
    fields (never the free-text body, which could mention "close"/"target" in
    unrelated prose), and require word boundaries so a ticker/strategy name like
    "CLOSED-END" or "TARGET" can't be misread as an exit. An exit alert always
    routes to close_position; everything else opens/refreshes."""
    action = str(alert.get("action") or alert.get("event") or "").upper().strip()
    if not action:
        return False
    # Exact-token match on the action verb (the field is a single signal word).
    exit_tokens = {
        "EXIT",
        "CLOSE",
        "CLOSING",
        "CLOSED",
        "TAKE PROFIT",
        "STOP HIT",
        "STOPPED",
        "EXIT LONG",
        "EXIT SHORT",
        "FLATTEN",
    }
    tokens = set(re.findall(r"[A-Z][A-Z0-9 ]*", action))
    return bool(tokens & exit_tokens)


class PositionMonitor(threading.Thread):
    """One thread per open ticker. Polls quotes, hard-checks stop/target, and
    asks the local 9B for a playbook (commentary only)."""

    def __init__(self, ticker: str, poll_interval: int = 60, stop_event: threading.Event | None = None):
        super().__init__(name=f"Monitor-{ticker}", daemon=True)
        self.ticker = ticker.upper()
        self.poll_interval = poll_interval
        self._stop = stop_event or threading.Event()

    def run(self):
        logger.info(f"[monitor:{self.ticker}] started (interval={self.poll_interval}s).")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.warning(f"[monitor:{self.ticker}] tick error: {e}")
            self._stop.wait(self.poll_interval)
        logger.info(f"[monitor:{self.ticker}] stopped.")

    def _tick(self):
        rec = list_open().get(self.ticker)
        if rec is None:
            self._stop.set()
            return

        side = str(rec.get("side", "")).upper()
        stop = rec.get("stop")
        target = rec.get("target")
        entry = rec.get("entry_price")

        try:
            price = get_current_price(self.ticker)
        except Exception as e:
            logger.warning(f"[monitor:{self.ticker}] price fetch failed: {e}")
            price = None

        breached_stop = False
        hit_target = False
        if price is not None and isinstance(price, (int, float)):
            if side == "LONG":
                if stop is not None and price <= float(stop):
                    breached_stop = True
                if target is not None and price >= float(target):
                    hit_target = True
            elif side == "SHORT":
                if stop is not None and price >= float(stop):
                    breached_stop = True
                if target is not None and price <= float(target):
                    hit_target = True

        update_position(self.ticker, last_price=price, breached_stop=breached_stop)
        if breached_stop:
            logger.warning(
                f"[monitor:{self.ticker}] STOP BREACH at {price} (stop={stop}) — awaiting TV exit alert."
            )
        elif hit_target:
            logger.info(f"[monitor:{self.ticker}] TARGET HIT at {price} (target={target}).")

        playbook = self._eval_playbook(rec, price)
        if playbook:
            update_position(self.ticker, last_eval=playbook, last_eval_at=_now_iso())

    def _eval_playbook(self, rec: dict, price) -> str:
        """Build a structured prompt and ask the local 9B for a status playbook.
        Inputs are explicit numbers (no free-form), so the model can't invent
        the quote. Output is treated as commentary, never as an order."""
        if rec.get("strategy") != "Intraday":
            return ""
        try:
            import os

            from src.clients.llm_client import query_local_llm

            rules_path = config.BASE_DIR / "gems" / "revanth-0dte.md"
            system_prompt = (
                rules_path.read_text(encoding="utf-8")
                if rules_path.exists()
                else "You are a trading analyst."
            )
            vix = get_current_price("VIX")
            ctx = {
                "ticker": self.ticker,
                "side": rec.get("side"),
                "strategy": rec.get("strategy"),
                "entry_price": rec.get("entry_price"),
                "stop": rec.get("stop"),
                "target": rec.get("target"),
                "current_price": price,
                "vix": vix,
                "opened_at": rec.get("opened_at"),
            }
            user_prompt = (
                "You are monitoring an OPEN position. Do NOT authorize any exit — "
                "exits are handled externally by a TradingView alert. Given the live "
                "numbers below, output a short GO/NO-GO-style status playbook: is the "
                "trade still valid, is it approaching stop/target, and what would "
                "invalidate it. Be concise.\n\n" + json.dumps(ctx, indent=2)
            )
            resp = query_local_llm(
                system_prompt,
                user_prompt,
                json_mode=False,
                use_openrouter=False,
                use_tools=False,
                disable_thinking=True,
                model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
            )
            return (resp or "").strip()
        except Exception as e:
            logger.warning(f"[monitor:{self.ticker}] playbook LLM failed: {e}")
            return ""


class PositionManager:
    """Owns the queue + the per-ticker monitor threads. The tracker feeds alerts
    in via `route_alert`; the manager opens/closes state and starts/stops
    threads accordingly. On startup, `rehydrate` respawns monitors for anything
    still open (state survives process restarts)."""

    def __init__(self, poll_interval: int = 60):
        self.poll_interval = poll_interval
        self._queue: "queue.Queue" = queue.Queue()
        self._monitors: dict = {}
        self._monitors_lock = threading.Lock()
        self._router_thread = threading.Thread(
            target=self._route_loop, name="PositionRouter", daemon=True
        )
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self.rehydrate()
        self._router_thread.start()
        logger.info("[manager] PositionManager started.")

    def stop(self):
        self._running = False
        with self._monitors_lock:
            for mon in self._monitors.values():
                mon._stop.set()
            self._monitors.clear()
        logger.info("[manager] PositionManager stopped.")

    def route_alert(self, alert: dict):
        """Producer side: push an alert onto the queue."""
        self._queue.put(alert)

    def rehydrate(self):
        """Respawn monitor threads for positions still open in state (e.g. after
        the tracker process was restarted). Only Intraday strategy positions are monitored."""
        open_pos = list_open()
        count = 0
        for ticker, rec in open_pos.items():
            if rec.get("strategy") == "Intraday":
                self._ensure_monitor(ticker)
                count += 1
        logger.info(f"[manager] rehydrated {count} open Intraday position(s).")

    def _route_loop(self):
        while self._running:
            try:
                alert = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._handle_alert(alert)
            except Exception as e:
                logger.error(f"[manager] alert routing failed: {e}")
            finally:
                self._queue.task_done()

    def _handle_alert(self, alert: dict):
        symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
        if not symbol:
            return
        strategy = alert.get("strategy", "Intraday")
        if strategy != "Intraday":
            logger.debug(f"[manager] ignoring non-Intraday alert for {symbol} (strategy={strategy}).")
            return

        if _is_exit_event(alert):
            closed = close_position(symbol)
            self._stop_monitor(symbol)
            if closed:
                logger.info(f"[manager] exit alert for {symbol} — position closed.")
            return

        side = "SHORT" if "PUT" in str(alert.get("action", "")).upper() else "LONG"
        entry = alert.get("alert_price") or alert.get("market_price")
        try:
            entry = float(entry) if entry not in (None, "") else None
        except (TypeError, ValueError):
            entry = None
        open_position(
            symbol,
            side=side,
            strategy=strategy,
            entry_price=entry,
            alert_price=entry,
            raw_alert=alert,
        )
        self._ensure_monitor(symbol)

    def _ensure_monitor(self, ticker: str):
        ticker = ticker.upper()
        with self._monitors_lock:
            if ticker in self._monitors and self._monitors[ticker].is_alive():
                return
            mon = PositionMonitor(ticker, poll_interval=self.poll_interval)
            self._monitors[ticker] = mon
            mon.start()

    def _stop_monitor(self, ticker: str):
        ticker = ticker.upper()
        with self._monitors_lock:
            mon = self._monitors.pop(ticker, None)
        if mon is not None:
            mon._stop.set()
