"""
src/streaming/schwab_streamer.py

Real-Time Intrabar Schwab WebSocket Streamer.
Connects directly to Schwab's official streaming servers via schwab.streaming.StreamClient.
Provides zero-latency live Level 1 equity/option ticks for open intraday positions.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from schwab.streaming import StreamClient
from src.clients.quote_router import QuoteData

logger = logging.getLogger(__name__)


class SchwabStreamer:
    """
    Background daemon that manages Schwab WebSocket streaming for active intraday positions.
    Maintains a thread-safe live tick cache and notifies registered listeners on every tick.
    """

    def __init__(self):
        self._stream_client: Optional[StreamClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._lock = threading.Lock()

        # Active subscriptions
        self._subscribed_symbols: Set[str] = set()

        # Latest live tick store: symbol -> QuoteData
        self._latest_ticks: Dict[str, QuoteData] = {}
        self._tick_lock = threading.Lock()

        # Listeners: list of callables receiving QuoteData
        self._listeners: List[Callable[[QuoteData], None]] = []

    def start(self) -> bool:
        """Start the Schwab streaming background daemon thread."""
        with self._lock:
            if self._is_running:
                return True

            self._is_running = True
            self._thread = threading.Thread(
                target=self._run_loop, name="SchwabStreamerDaemon", daemon=True
            )
            self._thread.start()
            logger.info("SchwabStreamer daemon thread started.")
            return True

    def stop(self) -> None:
        """Stop the streaming daemon."""
        with self._lock:
            self._is_running = False
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            logger.info("SchwabStreamer stop requested.")

    def add_tick_listener(self, listener: Callable[[QuoteData], None]) -> None:
        """Register a callback for every incoming tick."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_tick_listener(self, listener: Callable[[QuoteData], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def get_latest_quote(self, symbol: str) -> Optional[QuoteData]:
        """Fetch the most recent streaming tick from in-memory cache (0.00ms latency)."""
        sym = symbol.strip().upper().replace(".", "/")
        with self._tick_lock:
            return self._latest_ticks.get(sym)

    MAX_INTRADAY_SYMBOLS = 25

    def subscribe(self, symbols: List[str]) -> None:
        """
        Subscribe to Level 1 equity ticks for the given symbols.
        STRICTLY RESERVED for active intraday positions and trade desk tickers (max 25).
        Broad scanning or universe streaming is strictly blocked from Schwab.
        """
        clean_syms = [s.strip().upper().replace(".", "/") for s in symbols if s]
        if not clean_syms:
            return

        if len(clean_syms) > self.MAX_INTRADAY_SYMBOLS:
            logger.error(
                f"[SchwabStreamer] REJECTED broad subscription request of {len(clean_syms)} symbols! "
                f"Schwab WebSocket is strictly restricted to active intraday tickers (cap: {self.MAX_INTRADAY_SYMBOLS}). "
                f"Use Alpaca or Tastytrade for broad scanning."
            )
            clean_syms = clean_syms[:self.MAX_INTRADAY_SYMBOLS]

        with self._lock:
            new_syms = [s for s in clean_syms if s not in self._subscribed_symbols]
            if not new_syms:
                return
            for s in new_syms:
                if len(self._subscribed_symbols) >= self.MAX_INTRADAY_SYMBOLS:
                    logger.warning(f"[SchwabStreamer] Max subscription cap ({self.MAX_INTRADAY_SYMBOLS}) reached. Skipping {s}")
                    break
                self._subscribed_symbols.add(s)

        # Dispatch subscription to the event loop
        if self._loop and self._loop.is_running() and self._stream_client:
            asyncio.run_coroutine_threadsafe(
                self._apply_subscriptions(list(self._subscribed_symbols)), self._loop
            )

    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from Level 1 equity ticks."""
        clean_syms = [s.strip().upper().replace(".", "/") for s in symbols if s]
        if not clean_syms:
            return

        with self._lock:
            for s in clean_syms:
                self._subscribed_symbols.discard(s)
            remaining = list(self._subscribed_symbols)

        if self._loop and self._loop.is_running() and self._stream_client:
            asyncio.run_coroutine_threadsafe(
                self._apply_subscriptions(remaining), self._loop
            )

    async def _apply_subscriptions(self, symbols: List[str]) -> None:
        """Async helper to update StreamClient subscriptions."""
        if not self._stream_client:
            return
        try:
            if symbols:
                logger.info(f"SchwabStreamer subscribing to {len(symbols)} symbol(s): {symbols}")
                await self._stream_client.level_one_equity_subs(symbols)
            else:
                logger.info("SchwabStreamer unsubscribing all symbols.")
                await self._stream_client.level_one_equity_unsubs()
        except Exception as e:
            logger.warning(f"Error updating SchwabStreamer subscriptions: {e}")

    def _on_level_one_equity(self, message: Dict[str, Any]) -> None:
        """Handle incoming Level 1 equity ticks from Schwab."""
        try:
            content = message.get("content") or []
            now = time.time()
            for item in content:
                key = item.get("key")
                if not key:
                    continue
                sym = str(key).upper().strip().replace(".", "/")

                last_price = (
                    item.get("LAST_PRICE")
                    or item.get("REGULAR_MARKET_LAST_PRICE")
                    or item.get("CLOSE_PRICE")
                )
                if last_price is None:
                    continue

                qd = QuoteData(
                    symbol=sym,
                    last_price=float(last_price),
                    bid=float(item.get("BID_PRICE") or 0.0),
                    ask=float(item.get("ASK_PRICE") or 0.0),
                    open=float(item.get("OPEN_PRICE") or 0.0),
                    high=float(item.get("HIGH_PRICE") or 0.0),
                    low=float(item.get("LOW_PRICE") or 0.0),
                    close=float(item.get("CLOSE_PRICE") or 0.0),
                    volume=int(item.get("TOTAL_VOLUME") or 0),
                    net_change=float(item.get("NET_CHANGE") or 0.0),
                    net_percent_change=float(item.get("NET_PERCENT_CHANGE") or 0.0),
                    source="SCHWAB_WS",
                    timestamp=now,
                )

                with self._tick_lock:
                    self._latest_ticks[sym] = qd

                # Notify listeners
                for listener in list(self._listeners):
                    try:
                        listener(qd)
                    except Exception as ex_l:
                        logger.debug(f"SchwabStreamer listener error: {ex_l}")
        except Exception as e:
            logger.debug(f"Error parsing SchwabStreamer equity tick: {e}")

    def _run_loop(self) -> None:
        """Background thread target that runs the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        while self._is_running:
            try:
                from src.clients.schwab_client import get_schwab_client

                client = get_schwab_client()
                if not client:
                    logger.warning("Schwab client unavailable for streaming. Retrying in 10s...")
                    time.sleep(10)
                    continue

                self._stream_client = StreamClient(client)
                self._stream_client.add_level_one_equity_handler(self._on_level_one_equity)

                async def _stream_worker():
                    await self._stream_client.login()
                    logger.info("SchwabStreamer connected and authenticated.")

                    with self._lock:
                        syms = list(self._subscribed_symbols)
                    if syms:
                        await self._stream_client.level_one_equity_subs(syms)

                    while self._is_running:
                        await self._stream_client.handle_message()

                self._loop.run_until_complete(_stream_worker())
            except Exception as e:
                logger.warning(f"SchwabStreamer connection dropped: {e}. Reconnecting in 5s...")
                time.sleep(5)

        logger.info("SchwabStreamer event loop terminated.")


# Global singleton instance
schwab_streamer = SchwabStreamer()
