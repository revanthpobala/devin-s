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
from zoneinfo import ZoneInfo

from src import config
from src.clients.price_client import get_current_price
from src.tracking.position_state import close_position, list_open, open_position, update_position

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")


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
        price = get_current_price(symbol, context="execution")
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
    exit_why_raw = alert.get("exit_why") or alert.get("act_now") or alert.get("reason") or alert.get("why") or ""
    if not exit_why_raw and isinstance(alert.get("raw_payload"), str):
        try:
            p_obj = json.loads(alert["raw_payload"])
            exit_why_raw = p_obj.get("exit_why") or p_obj.get("act_now") or ""
        except Exception:
            pass
    exit_why = str(exit_why_raw).lower()

    # 1. Structural / Strategy Exits (bias flipped, chop stall, EOD flat, time stop, runner target, profit lock)
    # These are deliberate market structure or time-based exits from Pine script, NEVER wick noise.
    structural_triggers = (
        "bias flipped",
        "chop stall",
        "eod flat",
        "flat (0dte)",
        "time stop",
        "runner target",
        "profit lock",
        "catastrophe",
        "flatten",
    )
    if any(st in exit_why for st in structural_triggers):
        return {
            "action": "CONFIRM_EXIT",
            "reason": f"Confirmed strategic exit ({exit_why_raw or 'structural exit'}).",
            "current_price": price,
            "stop": stop,
            "target": target,
        }

    # 2. Target hit / profit taking
    is_tp = any(tok in action_text for tok in ("TAKE PROFIT", "TARGET", "TP", "PROFIT")) or "target" in exit_why
    if is_tp or (target is not None and ((side == "LONG" and price >= target) or (side == "SHORT" and price <= target))):
        return {
            "action": "CONFIRM_EXIT",
            "reason": f"Target reached at ${price:.2f} (target=${target or price:.2f}). Locking profit.",
            "current_price": price,
            "stop": stop,
            "target": target,
        }

    # 3. Catastrophic risk safeguard (>2.5% drawdown from entry)
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

    # 4. Stop evaluation: Intra-bar wick vs confirmed breakdown
    # Only genuine stop events ("stopped", "runner stop", or unclassified stop exits) undergo intra-bar wick veto.
    if stop is not None:
        if side == "LONG":
            if price > stop:
                try:
                    from src.plugins.order_flow_plugin import read_tape
                    tape_verdict = str(read_tape(symbol).get("verdict", "")).lower()
                    if "aggressive selling" in tape_verdict or ("volume accelerating" in tape_verdict and "selling" in tape_verdict):
                        return {
                            "action": "CONFIRM_EXIT",
                            "reason": f"Tape override: aggressive selling with accelerating volume despite price holding above stop. Real distribution, not a wick tap.",
                            "current_price": price,
                            "stop": stop,
                        }
                except Exception as e:
                    logger.debug(f"[review_tv_exit:{symbol}] tape read failed, holding veto: {e}")
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
                try:
                    from src.plugins.order_flow_plugin import read_tape
                    tape_verdict = str(read_tape(symbol).get("verdict", "")).lower()
                    if "aggressive buying" in tape_verdict or ("volume accelerating" in tape_verdict and "buying" in tape_verdict):
                        return {
                            "action": "CONFIRM_EXIT",
                            "reason": f"Tape override: aggressive buying with accelerating volume despite price holding below stop. Real accumulation, not a wick tap.",
                            "current_price": price,
                            "stop": stop,
                        }
                except Exception as e:
                    logger.debug(f"[review_tv_exit:{symbol}] tape read failed, holding veto: {e}")
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
            price = get_current_price(self.ticker, context="execution")
        except Exception as e:
            logger.warning(f"[monitor:{self.ticker}] price fetch failed: {e}")
            price = None

        breached_stop = False
        hit_target = False
        trailed_stop = False

        if price is not None and isinstance(price, (int, float)):
            # If stop or target were not set, dynamically calculate via Schwab intraday ATR
            if (stop is None or target is None) and entry:
                try:
                    from src.clients.schwab_client import calculate_intraday_atr
                    atr = calculate_intraday_atr(self.ticker)
                except Exception:
                    atr = None
                if not atr or atr <= 0:
                    atr = round(entry * 0.01, 2)
                if stop is None:
                    stop = round(entry - 1.25 * atr, 2) if side == "LONG" else round(entry + 1.25 * atr, 2)
                if target is None:
                    target = round(entry + 1.5 * atr, 2) if side == "LONG" else round(entry - 1.5 * atr, 2)
                update_position(self.ticker, stop=stop, target=target)

            # 0. EOD Force-Flat Rule (0DTE 3:45 PM ET mandatory liquidation)
            from src.tracking.alert_db import get_eastern_now
            now_et = get_eastern_now()
            strat = str(rec.get("strategy") or "").lower()
            opened_at_str = str(rec.get("opened_at") or "")
            opened_before_eod = True
            if opened_at_str:
                try:
                    op_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                    if op_dt.tzinfo is None:
                        op_dt = op_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                    else:
                        op_dt = op_dt.astimezone(ZoneInfo("America/New_York"))
                    if op_dt.hour == 15 and op_dt.minute >= 45:
                        opened_before_eod = False
                except Exception:
                    pass

            if strat == "intraday" and opened_before_eod and now_et.weekday() < 5:
                if now_et.hour > 15 or (now_et.hour == 15 and now_et.minute >= 45):
                    logger.info(
                        f"[monitor:{self.ticker}] ⏰ EOD FORCE-FLAT TRIGGERED (3:45 PM ET Rule) — closing position at ${price or entry:.2f}."
                    )
                    close_position(
                        self.ticker,
                        exit_price=price or entry,
                        exit_reason="EOD Force-Flat (0DTE 3:45 PM ET Rule)",
                    )
                    self._stop.set()
                    return

            # 1. Catastrophic circuit breaker (>2.5% drawdown or 1.25x ATR loss)
            if entry and entry > 0:
                drawdown = (entry - price) / entry if side == "LONG" else (price - entry) / entry
                if drawdown >= 0.025:
                    logger.warning(
                        f"[monitor:{self.ticker}] 🛑 CATASTROPHIC RISK SAFEGUARD TRIGGERED ({drawdown*100:.1f}% drawdown from entry ${entry:.2f}) — closing position immediately."
                    )
                    close_position(self.ticker, exit_price=price, exit_reason=f"Catastrophic drawdown safeguard breached ({drawdown*100:.1f}%)")
                    self._stop.set()
                    return

            # 2. Dynamic Profit Protection & Golden Lock
            scaled_at_t1 = rec.get("scaled_at_t1", False)
            be_locked = rec.get("be_locked", False)
            peak_price = float(rec.get("peak_price") or entry or price)
            eval_reason = ""

            # 2a. Track High-Water Mark (Peak Price)
            if side == "LONG":
                if price > peak_price:
                    peak_price = price
                    update_position(self.ticker, peak_price=peak_price)
            elif side == "SHORT":
                if price < peak_price:
                    peak_price = price
                    update_position(self.ticker, peak_price=peak_price)

            # 2b. Early Breakeven Protection (+0.5R or >= $100 Unrealized Gain)
            # Never let a trade that achieved meaningful traction turn into a red loss.
            if entry and entry > 0:
                unrealized_gain = (price - entry) if side == "LONG" else (entry - price)
                unrealized_pnl = unrealized_gain * 100.0
                halfway_to_target = False
                if target and entry:
                    target_dist = abs(float(target) - float(entry))
                    halfway_to_target = unrealized_gain >= (0.5 * target_dist)

                if not be_locked and (unrealized_pnl >= 100.0 or halfway_to_target):
                    be_stop = round(float(entry) + 0.05, 2) if side == "LONG" else round(float(entry) - 0.05, 2)
                    should_move = (side == "LONG" and (stop is None or float(stop) < be_stop)) or \
                                  (side == "SHORT" and (stop is None or float(stop) > be_stop))
                    if should_move:
                        stop = be_stop
                        trailed_stop = True
                        be_locked = True
                        eval_reason = f"🛡️ Gain traction reached (+${unrealized_pnl:.2f} / 0.5R). Stop ratcheted to BE+ (${stop})."
                        logger.info(
                            f"[monitor:{self.ticker}] 🛡️ GAIN TRACTION TRIGGERED (+${unrealized_pnl:.2f} / 0.5R reached) — "
                            f"AUTONOMOUSLY RATCHETED STOP TO BREAK-EVEN+ (${stop})."
                        )

            # 2c. Target 1 Reached: Golden Lock (50% scale + trail runner to BE+ 0.05)
            if side == "LONG":
                if target is not None and price >= float(target):
                    hit_target = True
                    if not scaled_at_t1:
                        from src.tracking.position_state import scale_position
                        scale_position(self.ticker, scale_pct=0.5, fill_price=price, reason=f"Target 1 hit at ${price:.2f} — Golden Lock 50% scale")
                        stop = round(float(entry) + 0.05, 2) if entry else stop
                        trailed_stop = True
                        eval_reason = f"🎯 Target hit at {price}. Scaled 50%, stop trailed to BE+ (${stop})."
                    elif entry is not None and stop is not None and float(stop) < float(entry):
                        stop = round(float(entry) + 0.05, 2)
                        trailed_stop = True
                        eval_reason = f"🎯 Target hit at {price}. Stop trailed to BE+ (${stop})."

                # 2d. Post-T1 Runner Protection (Trail at least 65% of peak gains once peak >= $200)
                if scaled_at_t1 and entry:
                    peak_gain = (peak_price - entry)
                    if peak_gain * 100.0 >= 200.0:
                        locked_gain = peak_gain * 0.65
                        lock_stop = round(float(entry) + locked_gain, 2)
                        if stop is None or float(stop) < lock_stop:
                            stop = lock_stop
                            trailed_stop = True
                            eval_reason = f"🔒 Runner profit locked (+${peak_gain*100:.2f} peak). Stop trailed to ${stop:.2f}."
                            logger.info(
                                f"[monitor:{self.ticker}] 🔒 RUNNER PROFIT PROTECTED: Trailed stop to ${stop:.2f} "
                                f"(locking 65% of peak +${peak_gain*100:.2f} gain)."
                            )

                if stop is not None and price <= float(stop):
                    breached_stop = True

            elif side == "SHORT":
                if target is not None and price <= float(target):
                    hit_target = True
                    if not scaled_at_t1:
                        from src.tracking.position_state import scale_position
                        scale_position(self.ticker, scale_pct=0.5, fill_price=price, reason=f"Target 1 hit at ${price:.2f} — Golden Lock 50% scale")
                        stop = round(float(entry) - 0.05, 2) if entry else stop
                        trailed_stop = True
                        eval_reason = f"🎯 Target hit at {price}. Scaled 50%, stop trailed to BE+ (${stop})."
                    elif entry is not None and stop is not None and float(stop) > float(entry):
                        stop = round(float(entry) - 0.05, 2)
                        trailed_stop = True
                        eval_reason = f"🎯 Target hit at {price}. Stop trailed to BE+ (${stop})."

                # 2d. Post-T1 Runner Protection (Trail at least 65% of peak gains once peak >= $200)
                if scaled_at_t1 and entry:
                    peak_gain = (entry - peak_price)
                    if peak_gain * 100.0 >= 200.0:
                        locked_gain = peak_gain * 0.65
                        lock_stop = round(float(entry) - locked_gain, 2)
                        if stop is None or float(stop) > lock_stop:
                            stop = lock_stop
                            trailed_stop = True
                            eval_reason = f"🔒 Runner profit locked (+${peak_gain*100:.2f} peak). Stop trailed to ${stop:.2f}."
                            logger.info(
                                f"[monitor:{self.ticker}] 🔒 RUNNER PROFIT PROTECTED: Trailed stop to ${stop:.2f} "
                                f"(locking 65% of peak +${peak_gain*100:.2f} gain)."
                            )

                if stop is not None and price >= float(stop):
                    breached_stop = True

        if trailed_stop:
            msg = eval_reason or (f"🎯 Target hit at {price}. Scaled 50%, stop trailed to BE+ (${stop})." if hit_target else f"Stop trailed to ${stop}.")
            logger.info(
                f"[monitor:{self.ticker}] {msg}"
            )
            update_position(
                self.ticker,
                last_price=price,
                stop=stop,
                breached_stop=False,
                be_locked=be_locked,
                peak_price=peak_price,
                last_eval=msg,
                last_eval_at=_now_iso(),
            )
        else:
            update_position(
                self.ticker,
                last_price=price,
                breached_stop=breached_stop,
                be_locked=be_locked,
                peak_price=peak_price,
            )

        if breached_stop:
            logger.warning(
                f"[monitor:{self.ticker}] 🛑 AUTONOMOUS STOP HIT at {price} (stop={stop}) — closing position."
            )
            close_position(self.ticker, exit_price=price, exit_reason=f"Autonomous stop breached at ${price:.2f} (stop=${stop:.2f})")
            self._stop.set()
            return
        elif hit_target:
            logger.info(f"[monitor:{self.ticker}] TARGET HIT at {price} (target={target}).")

        playbook = self._eval_playbook(rec, price)
        if playbook:
            # Sovereign LLM Exit Directive
            if any(term in playbook.upper() for term in ("ACTION: EXIT", "🔴 EXIT CONFIRMED", "ACTION: CLOSE", "INVALIDATED — EXIT")):
                logger.warning(f"[monitor:{self.ticker}] 🔴 LLM SOVEREIGN EXIT DIRECTIVE TRIGGERED: closing position at ${price:.2f}.")
                close_position(self.ticker, exit_price=price, exit_reason=f"LLM Sovereign Exit Directive: {playbook[:100]}")
                self._stop.set()
                return

            if trailed_stop and msg:
                playbook = f"{msg}\n\n" + playbook
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
            closed = close_position(symbol, exit_price=decision.get("current_price"), exit_reason=decision.get("reason"))
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

        if raw_side == "NEUTRAL" or raw_action in ("NEUTRAL", "ALERT", "NONE", "UNKNOWN", ""):
            logger.info(f"[manager] skipping non-directional alert for {symbol} (side={raw_side}, action={raw_action}).")
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

        # Parse raw payload if JSON to extract plan/levels
        payload = {}
        raw_p = alert.get("raw_payload") or alert.get("body") or ""
        if raw_p:
            try:
                payload = json.loads(raw_p) if isinstance(raw_p, str) else (raw_p if isinstance(raw_p, dict) else {})
            except Exception:
                payload = {}

        entry = alert.get("alert_price") or alert.get("market_price") or payload.get("price")
        try:
            entry = float(entry) if entry not in (None, "") else None
        except (TypeError, ValueError):
            entry = None

        # Check institutional risk vetoes (Grade-A gate, Weinstein stage, time windows, max exposure)
        from src.tracking.alert_evaluator import evaluate_risk_vetoes, get_eastern_now
        eastern_now = get_eastern_now()
        current_time_et = eastern_now.strftime("%I:%M %p ET")
        try:
            score_val = int(float(payload.get("score") or alert.get("score") or 85))
        except (ValueError, TypeError):
            score_val = 85
        grade_val = str(payload.get("grade") or alert.get("grade") or "A").upper()
        align_val = str(payload.get("align") or alert.get("align") or "")

        risk_veto = evaluate_risk_vetoes(
            symbol=symbol,
            action=raw_action,
            score=score_val,
            current_time_et=current_time_et,
            eastern_dt=eastern_now,
            grade=grade_val,
            align=align_val,
        )
        if risk_veto:
            hdr, pb = risk_veto
            logger.warning(f"[manager] ⛔ RISK VETO for {symbol}: {hdr} — position NOT opened.")
            msg_id = alert.get("message_id")
            if msg_id:
                try:
                    from src.tracking.alert_db import update_alert_llm
                    update_alert_llm(msg_id, hdr, pb, status="PROCESSED")
                except Exception:
                    pass
            return

        # AI Verdict Gate: never open a position the AI already vetoed in its persisted triage.
        # (The enrichment worker may have written a STAND ASIDE verdict from the real Pine
        # grade/score before this routing pass ran.)
        existing_verdict = str(alert.get("llm_decision") or "")
        if not existing_verdict and payload.get("verdict"):
            existing_verdict = str(payload.get("verdict"))
        if "STAND ASIDE" in existing_verdict.upper() or "DAY PAUSE" in existing_verdict.upper():
            logger.info(f"[manager] 🛡️ AI VETO GATE for {symbol}: persisted verdict '{existing_verdict[:60]}' — position NOT opened.")
            return

        # If a position is already open for this symbol, the AI's verdict decides:
        # a fresh TAKE verdict replaces it (latest alert wins); a STAND ASIDE closes it.
        if list_open().get(symbol):
            v_up = existing_verdict.upper()
            if "STAND ASIDE" in v_up or "DAY PAUSE" in v_up:
                try:
                    close_price = get_current_price(symbol, context="execution")
                except Exception:
                    close_price = None
                closed = close_position(
                    symbol,
                    exit_price=close_price,
                    exit_reason=f"AI triage vetoed position ({existing_verdict[:80]})",
                )
                self._stop_monitor(symbol)
                if closed:
                    logger.warning(f"[manager] 🛡️ AI VETO for {symbol}: open position closed at ${closed.get('exit_price')} ({existing_verdict[:60]})")
                return
            elif not ("TAKE" in v_up or "GO" in v_up):
                # No explicit TAKE and no veto — keep the existing position, don't replace it
                logger.info(f"[manager] ⏸️ {symbol} already open; verdict '{existing_verdict[:40]}' has no TAKE signal — keeping existing position.")
                return

        # Extract stop and target levels from alert or plan string (e.g. "In 165.60 · Stop 165.21 · T1 166.19")
        stop = alert.get("stop") or payload.get("stop")
        target = alert.get("target") or alert.get("t1") or payload.get("t1") or payload.get("target")
        plan_str = str(alert.get("plan") or payload.get("plan") or "")
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

        # Infallible guarantee: If stop or target are missing, derive via intraday ATR
        if (stop is None or target is None) and entry:
            try:
                from src.clients.schwab_client import calculate_intraday_atr
                atr = calculate_intraday_atr(symbol)
            except Exception:
                atr = None
            if not atr or atr <= 0:
                atr = round(entry * 0.01, 2)
            if stop is None:
                stop = round(entry - 1.25 * atr, 2) if side == "LONG" else round(entry + 1.25 * atr, 2)
            if target is None:
                target = round(entry + 1.5 * atr, 2) if side == "LONG" else round(entry - 1.5 * atr, 2)

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
