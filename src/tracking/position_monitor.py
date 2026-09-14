"""
Live position monitoring — the "what are we in, and is it still valid" loop.

Design:
- data/positions.json is the SINGLE SOURCE OF TRUTH for open trades.
- A TradingView ENTRY alert opens a position (upsert into state) and spins up a
  PositionMonitor thread for that ticker.
- A TradingView EXIT alert is routed through `review_tv_exit` (TV Exit Veto Gate):
  checks live broker quotes (Schwab / Tastytrade / Alpaca) to distinguish intra-bar
  wicks from confirmed structural breakdowns, preventing premature liquidations.
- Each monitor thread polls live quotes every N minutes and:
    1. autonomously trails stop to Break-Even when Target 1 is reached (PROFIT_LOCKED),
    2. autonomously closes on confirmed stop breach on live quote,
    3. calls the local LLM for dynamic tactical trade playbook and guidance,
    4. writes last_price + last_eval back into state (Sheets is a mirror).
"""

from __future__ import annotations

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


def review_tv_exit(symbol: str, alert: dict) -> dict:
    """Review an incoming TradingView EXIT alert against live broker telemetry and market context.

    Evaluates whether the exit is:
    1. A target hit / profit taking event -> CONFIRM_EXIT.
    2. A catastrophic stop-loss breach (>2.5% loss) -> CONFIRM_EXIT (non-negotiable safety guard).
    3. An intra-bar wick or noise tap where live price is still holding above stop (for LONG)
       or below stop (for SHORT) -> VETO_HOLD.
    4. A confirmed breakdown/breach where live price is confirmed at or beyond stop -> CONFIRM_EXIT.
    """
    symbol = symbol.strip().upper()
    open_positions = list_open()
    rec = open_positions.get(symbol)
    if not rec:
        return {"action": "CONFIRM_EXIT", "reason": f"No open position tracked for {symbol}."}

    side = str(rec.get("side", "LONG")).upper()
    entry = rec.get("entry_price")
    stop = rec.get("stop")
    target = rec.get("target")

    try:
        entry = float(entry) if entry is not None else None
    except (ValueError, TypeError):
        entry = None
    try:
        stop = float(stop) if stop is not None else None
    except (ValueError, TypeError):
        stop = None
    try:
        target = float(target) if target is not None else None
    except (ValueError, TypeError):
        target = None

    # Fetch live broker/market quote
    price = None
    try:
        price = get_current_price(symbol)
    except Exception as e:
        logger.debug(f"[review_tv_exit:{symbol}] price fetch failed: {e}")

    # Fallback to alert/market price if live client returned None
    if price is None or not isinstance(price, (int, float)):
        raw_p = alert.get("alert_price") or alert.get("market_price") or alert.get("exit_px")
        try:
            price = float(raw_p) if raw_p is not None else None
        except (ValueError, TypeError):
            price = None

    if price is None or not isinstance(price, (int, float)):
        return {"action": "CONFIRM_EXIT", "reason": "Unable to verify live broker price. Honoring TV exit."}

    action_text = str(alert.get("action") or alert.get("event") or alert.get("act_now") or "").upper()

    # 1. Target hit / profit taking
    is_tp = any(tok in action_text for tok in ("TAKE PROFIT", "TARGET", "TP", "PROFIT"))
    if is_tp or (target is not None and ((side == "LONG" and price >= target) or (side == "SHORT" and price <= target))):
        return {
            "action": "CONFIRM_EXIT",
            "reason": f"Target reached at ${price:.2f} (target=${target or price:.2f}). Locking profit.",
            "current_price": price,
            "stop": stop,
            "target": target,
        }

    # 2. Catastrophic risk safeguard (>2.5% drawdown from entry)
    if entry and entry > 0:
        drawdown = (entry - price) / entry if side == "LONG" else (price - entry) / entry
        if drawdown >= 0.025:
            return {
                "action": "CONFIRM_EXIT",
                "reason": f"Catastrophic stop safeguard breached ({drawdown*100:.1f}% drawdown from entry ${entry:.2f}).",
                "current_price": price,
                "stop": stop,
                "drawdown": drawdown,
            }

    # 3. Stop evaluation: Intra-bar wick vs confirmed breakdown
    if stop is not None:
        if side == "LONG":
            if price > stop:
                return {
                    "action": "VETO_HOLD",
                    "reason": f"Intra-bar wick noise. Live price ${price:.2f} is holding above stop ${stop:.2f}. Setup structure intact.",
                    "current_price": price,
                    "stop": stop,
                }
            else:
                return {
                    "action": "CONFIRM_EXIT",
                    "reason": f"Confirmed stop breach. Live price ${price:.2f} is at or below stop ${stop:.2f}.",
                    "current_price": price,
                    "stop": stop,
                }
        elif side == "SHORT":
            if price < stop:
                return {
                    "action": "VETO_HOLD",
                    "reason": f"Intra-bar wick noise. Live price ${price:.2f} is holding below stop ${stop:.2f}. Setup structure intact.",
                    "current_price": price,
                    "stop": stop,
                }
            else:
                return {
                    "action": "CONFIRM_EXIT",
                    "reason": f"Confirmed stop breach. Live price ${price:.2f} is at or above stop ${stop:.2f}.",
                    "current_price": price,
                    "stop": stop,
                }

    return {
        "action": "CONFIRM_EXIT",
        "reason": f"Exit confirmed at ${price:.2f}.",
        "current_price": price,
    }


def _is_exit_event(alert: dict) -> bool:
    """Heuristic: does this alert describe closing/exiting the position?

    TV alerts carry an `event`/`action` like 'EXIT', 'CLOSE', 'TAKE PROFIT',
    'STOP HIT', or a 'CLOSING' flag. We match ONLY the authoritative action/event
    fields (never the free-text body, which could mention "close"/"target" in
    unrelated prose), and require word boundaries so a ticker/strategy name like
    "CLOSED-END" or "TARGET" can't be misread as an exit."""
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
        trailed_stop = False

        if price is not None and isinstance(price, (int, float)):
            if side == "LONG":
                # Check target hit -> trail stop to break-even (entry price)
                if target is not None and price >= float(target):
                    hit_target = True
                    if entry is not None and stop is not None and float(stop) < float(entry):
                        stop = float(entry)
                        trailed_stop = True
                if stop is not None and price <= float(stop):
                    breached_stop = True
            elif side == "SHORT":
                # Check target hit -> trail stop to break-even (entry price)
                if target is not None and price <= float(target):
                    hit_target = True
                    if entry is not None and stop is not None and float(stop) > float(entry):
                        stop = float(entry)
                        trailed_stop = True
                if stop is not None and price >= float(stop):
                    breached_stop = True

        if trailed_stop:
            logger.info(
                f"[monitor:{self.ticker}] 🎯 TARGET HIT at {price} (target={target}) — "
                f"AUTONOMOUSLY TRAILING STOP TO BREAK-EVEN ({entry})."
            )
            update_position(
                self.ticker,
                last_price=price,
                stop=stop,
                breached_stop=False,
                last_eval=f"🎯 Target hit at {price}. Stop trailed to Break-Even (${entry}).",
                last_eval_at=_now_iso(),
            )
        else:
            update_position(self.ticker, last_price=price, breached_stop=breached_stop)

        if breached_stop:
            logger.warning(
                f"[monitor:{self.ticker}] 🛑 AUTONOMOUS STOP HIT at {price} (stop={stop}) — closing position."
            )
            close_position(self.ticker)
            self._stop.set()
            return
        elif hit_target:
            logger.info(f"[monitor:{self.ticker}] TARGET HIT at {price} (target={target}).")

        playbook = self._eval_playbook(rec, price)
        if playbook:
            if trailed_stop:
                playbook = f"🎯 Target hit at {price}. Stop trailed to Break-Even (${entry}).\n\n" + playbook
            update_position(self.ticker, last_eval=playbook, last_eval_at=_now_iso())

    def _eval_playbook(self, rec: dict, price) -> str:
        """Build a structured prompt and ask the local 9B for a status playbook.
        Inputs are explicit numbers (no free-form), so the model can't invent
        the quote. Evaluates active trade management and invalidation."""
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
                "You are actively monitoring an OPEN position. Given the live "
                "numbers below, provide tactical guidance: evaluate if the "
                "trade is still valid, whether to HOLD, TRAIL STOP, or EXIT, and "
                "the precise invalidation level. Output a clear GO/HOLD/EXIT "
                "recommendation and concise status playbook.\n\n" + json.dumps(ctx, indent=2)
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

    def handle_exit_alert(self, alert: dict) -> dict:
        """Evaluate TV exit alert through the veto gate and either confirm exit or veto and hold."""
        symbol = (alert.get("symbol") or alert.get("ticker") or "").strip().upper()
        if not symbol:
            return {"action": "CONFIRM_EXIT", "reason": "Missing symbol"}

        decision = review_tv_exit(symbol, alert)
        if decision["action"] == "CONFIRM_EXIT":
            closed = close_position(symbol)
            self._stop_monitor(symbol)
            if closed:
                logger.info(f"[manager] CONFIRMED exit for {symbol} — position closed. Reason: {decision['reason']}")
        else:
            logger.info(f"[manager] 🛡️ VETOED premature TV exit for {symbol} — holding position. Reason: {decision['reason']}")
            update_position(
                symbol,
                last_eval=f"🛡️ VETOED TV EXIT: {decision['reason']}",
                last_eval_at=_now_iso(),
            )
        return decision

    def rehydrate(self):
        """Respawn monitor threads for positions still open in state (e.g. after
        the tracker process was restarted). Only Intraday strategy positions opened today are monitored."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

        open_pos = list_open()
        count = 0
        for ticker, rec in open_pos.items():
            # Skip stale prior-day positions
            opened_at = rec.get("opened_at", "")
            if opened_at and opened_at[:10] != today_str:
                continue
            # Skip any position mistakenly saved from a Screener alert
            if str(rec.get("raw_alert", {}).get("subject", "")).lower() == "alert: screener":
                continue
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
            self.handle_exit_alert(alert)
            return

        # Determine directional side — NEVER open on NEUTRAL or non-directional signals
        raw_side = str(alert.get("side") or "").upper().strip()
        raw_action = str(alert.get("action") or "").upper().strip()

        if raw_side == "NEUTRAL" or raw_action in ("NEUTRAL", "ALERT", "NONE", "UNKNOWN", "") or bool(alert.get("setup")):
            logger.info(f"[manager] skipping non-directional alert for {symbol} (side={raw_side}, action={raw_action}, setup={alert.get('setup')}).")
            return

        if any(tok in raw_action for tok in ("PUT", "SHORT", "SELL", "BEAR")) or any(tok in raw_side for tok in ("PUT", "SHORT", "SELL", "BEAR")):
            side = "SHORT"
        elif any(tok in raw_action for tok in ("CALL", "LONG", "BUY", "BULL")) or any(tok in raw_side for tok in ("CALL", "LONG", "BUY", "BULL")):
            side = "LONG"
        else:
            logger.info(f"[manager] skipping alert for {symbol} with unhandled side/action (action={raw_action}, side={raw_side}).")
            return

        # Ensure stale alerts from prior calendar days never open live intraday positions
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        alert_ts = str(alert.get("timestamp") or "")
        if alert_ts and alert_ts[:10] != today_et:
            logger.info(f"[manager] skipping prior-day alert for {symbol} dated {alert_ts} (today is {today_et}).")
            return

        entry = alert.get("alert_price") or alert.get("market_price")
        try:
            entry = float(entry) if entry not in (None, "") else None
        except (TypeError, ValueError):
            entry = None

        # Extract stop and target levels from alert or plan string (e.g. "In 165.60 · Stop 165.21 · T1 166.19")
        stop = alert.get("stop")
        target = alert.get("target") or alert.get("t1")
        plan_str = str(alert.get("plan") or "")
        if not stop and plan_str:
            m_stop = re.search(r"\b(?:Stop|SL)\s+([0-9]+(?:\.[0-9]+)?)\b", plan_str, re.IGNORECASE)
            if m_stop:
                try:
                    stop = float(m_stop.group(1))
                except (ValueError, TypeError):
                    pass
        if not target and plan_str:
            m_target = re.search(r"\b(?:T1|Target|TP)\s+([0-9]+(?:\.[0-9]+)?)\b", plan_str, re.IGNORECASE)
            if m_target:
                try:
                    target = float(m_target.group(1))
                except (ValueError, TypeError):
                    pass
        try:
            stop = float(stop) if stop not in (None, "") else None
        except (ValueError, TypeError):
            stop = None
        try:
            target = float(target) if target not in (None, "") else None
        except (ValueError, TypeError):
            target = None

        open_position(
            symbol,
            side=side,
            strategy=strategy,
            entry_price=entry,
            stop=stop,
            target=target,
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
