"""
src/clients/quote_router.py

Workload-Segregated Multi-Provider Quote Router.

Architecture:
  1. INTRADAY EXECUTION CHANNEL:
     - Powered EXCLUSIVELY by Schwab API & Schwab WebSocket.
     - Authoritative broker quotes and live NBBO for open positions, 0DTE triage,
       intrabar tripwires, trailing stops, and execution fills.
  2. SURVEILLANCE & WATCHLIST CHANNEL:
     - Split among Yahoo Direct Chart REST, Alpaca, and Tastytrade.
     - High-speed concurrent fetching (<400ms for 50 tickers) completely offloaded
       from Schwab to protect broker rate limits.
  3. BROAD MARKET INDEX CHANNEL:
     - Direct to Yahoo Chart REST (^VIX, ^SPX, ^NDX, ^RUT) with 15s shared caching.
     - Completely avoids Schwab 404s and Alpaca rejections.
  4. RESEARCH & DOSSIER CHANNEL:
     - Synthesizes spot quotes, 52w range, and day stats for LLM tools and research
       from secondary feeds without consuming Schwab execution quotas.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# pyrefly: ignore [missing-import]
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Broad market index tickers that must route to Yahoo REST (^ prefix)
INDEX_SYMBOLS: Set[str] = {"VIX", "SPX", "NDX", "RUT", "DJI", "COMP", "TNX"}


@dataclass
class QuoteData:
    symbol: str
    last_price: float
    bid: float = 0.0
    ask: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    net_change: float = 0.0
    net_percent_change: float = 0.0
    source: str = "UNKNOWN"
    timestamp: float = field(default_factory=time.time)
    is_extended_hours: bool = False
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    hv30: Optional[float] = None
    expected_earnings: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "last_price": self.last_price,
            "bid": self.bid,
            "ask": self.ask,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "net_change": self.net_change,
            "net_percent_change": self.net_percent_change,
            "source": self.source,
            "timestamp": self.timestamp,
            "is_extended_hours": self.is_extended_hours,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "hv30": self.hv30,
            "expected_earnings": self.expected_earnings,
        }


class CircuitBreaker:
    """Tracks provider health and enforces cool-downs on failure."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 120.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: int = 0
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        with self._lock:
            if time.time() < self._cooldown_until:
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._cooldown_until = 0.0

    def record_failure(self, reason: str = "") -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._cooldown_until = time.time() + self.cooldown_seconds
                logger.warning(
                    f"CircuitBreaker tripped for {self.cooldown_seconds:.0f}s: {reason} "
                    f"(failures: {self._failures})"
                )

    def force_cooldown(self, seconds: float, reason: str = "") -> None:
        with self._lock:
            self._failures = self.failure_threshold
            self._cooldown_until = time.time() + seconds
            logger.warning(f"CircuitBreaker forced cooldown for {seconds:.0f}s: {reason}")


class QuoteRouter:
    """Centralized, workload-segregated quote routing engine."""

    def __init__(self):
        # In-memory TTL caches: key -> (timestamp, QuoteData)
        self._cache_execution: Dict[str, Tuple[float, QuoteData]] = {}
        self._cache_surveillance: Dict[str, Tuple[float, QuoteData]] = {}
        self._cache_index: Dict[str, Tuple[float, QuoteData]] = {}
        self._cache_lock = threading.Lock()

        # TTL configurations (seconds)
        self.ttl_execution = 3.0       # Intraday execution (tight 3s)
        self.ttl_surveillance = 10.0    # Watchlist stalking (10s)
        self.ttl_index = 15.0          # Broad indices like VIX/SPX (15s)
        self.ttl_offhours = 300.0      # When market closed (5m)

        # Circuit breakers
        self.schwab_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        self.yahoo_breaker = CircuitBreaker(failure_threshold=4, cooldown_seconds=60.0)
        self.alpaca_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
        self.tastytrade_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)

        # Single-flight locks to coalesce concurrent requests for the exact same ticker
        self._inflight_locks: Dict[str, threading.Lock] = {}
        self._inflight_meta_lock = threading.Lock()

        # Thread pool for non-Schwab parallel surveillance
        self._surveillance_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="surveillance_quotes"
        )

    # -------------------------------------------------------------------------
    # Helper Utilities
    # -------------------------------------------------------------------------
    def _clean_symbol(self, symbol: str) -> str:
        s = str(symbol or "").strip().upper()
        if s.startswith("^"):
            s = s[1:]
        return s.replace(".", "/")

    def _is_index(self, symbol: str) -> bool:
        s = self._clean_symbol(symbol)
        return s in INDEX_SYMBOLS

    def _get_ticker_lock(self, symbol: str) -> threading.Lock:
        with self._inflight_meta_lock:
            if symbol not in self._inflight_locks:
                self._inflight_locks[symbol] = threading.Lock()
            return self._inflight_locks[symbol]

    # =========================================================================
    # 1. INTRADAY EXECUTION CHANNEL (SCHWAB API & WS EXCLUSIVELY)
    # =========================================================================
    def get_live_execution_quote(self, symbol: str) -> Optional[QuoteData]:
        """
        Authoritative broker quote for live intraday trading and active positions.
        Powered EXCLUSIVELY by Schwab API / WebSocket.
        """
        clean_sym = self._clean_symbol(symbol)
        if not clean_sym:
            return None

        # Route indices to IndexChannel automatically
        if self._is_index(clean_sym):
            return self.get_index_quote(clean_sym)

        now = time.time()
        # 1. Fast-path in-memory cache check
        with self._cache_lock:
            cached = self._cache_execution.get(clean_sym)
            if cached and (now - cached[0] < self.ttl_execution):
                return cached[1]

        # 1b. Fast-path Schwab WebSocket streamer tick check (0.00ms latency)
        try:
            from src.streaming.schwab_streamer import schwab_streamer

            ws_tick = schwab_streamer.get_latest_quote(clean_sym)
            if ws_tick and (now - ws_tick.timestamp < self.ttl_execution):
                return ws_tick
        except Exception:
            pass

        # 2. Coalesce concurrent requests for the same symbol
        ticker_lock = self._get_ticker_lock(f"exec_{clean_sym}")
        with ticker_lock:
            # Re-check cache inside lock
            with self._cache_lock:
                cached = self._cache_execution.get(clean_sym)
                if cached and (now - cached[0] < self.ttl_execution):
                    return cached[1]

            # 3. Call Schwab API
            if self.schwab_breaker.is_available():
                try:
                    from src.clients.schwab_client import get_realtime_quote as schwab_get_rt

                    sq = schwab_get_rt(clean_sym)
                    if sq and sq.get("last_price") and float(sq["last_price"]) > 0:
                        qd = QuoteData(
                            symbol=clean_sym,
                            last_price=float(sq["last_price"]),
                            bid=float(sq.get("bid") or 0.0),
                            ask=float(sq.get("ask") or 0.0),
                            open=float(sq.get("open") or 0.0),
                            high=float(sq.get("high") or 0.0),
                            low=float(sq.get("low") or 0.0),
                            close=float(sq.get("close") or 0.0),
                            volume=int(sq.get("volume") or 0),
                            net_change=float(sq.get("net_change") or 0.0),
                            net_percent_change=float(sq.get("net_percent_change") or 0.0),
                            source="SCHWAB",
                            timestamp=now,
                        )
                        with self._cache_lock:
                            self._cache_execution[clean_sym] = (now, qd)
                        self.schwab_breaker.record_success()
                        return qd
                except Exception as e:
                    logger.warning(f"[ExecutionChannel:{clean_sym}] Schwab live quote failed: {e}")
                    if "401" in str(e) or "invalid_grant" in str(e):
                        self.schwab_breaker.force_cooldown(180.0, "Schwab 401 Unauthorized")
                    else:
                        self.schwab_breaker.record_failure(str(e))

            # 4. Fallback: If Schwab is hard down during live trading, protect capital via secondary
            logger.warning(f"[ExecutionChannel:{clean_sym}] Schwab unavailable! Engaging safety backup.")
            backup_quote = self._fetch_yahoo_direct(clean_sym)
            if backup_quote:
                backup_quote.source = f"{backup_quote.source}_EMERGENCY_FALLBACK"
                with self._cache_lock:
                    self._cache_execution[clean_sym] = (now, backup_quote)
                return backup_quote

        return None

    def get_live_execution_quotes_batch(self, symbols: List[str]) -> Dict[str, QuoteData]:
        """Fetch live execution quotes for multiple open positions from Schwab."""
        results: Dict[str, QuoteData] = {}
        if not symbols:
            return results

        clean_syms = [self._clean_symbol(s) for s in symbols if s]
        equities = [s for s in clean_syms if not self._is_index(s)]
        indices = [s for s in clean_syms if self._is_index(s)]

        # Fetch indices via IndexChannel
        for idx in indices:
            iq = self.get_index_quote(idx)
            if iq:
                results[idx] = iq

        if not equities:
            return results

        now = time.time()
        to_fetch = []
        with self._cache_lock:
            for s in equities:
                cached = self._cache_execution.get(s)
                if cached and (now - cached[0] < self.ttl_execution):
                    results[s] = cached[1]
                else:
                    to_fetch.append(s)

        if not to_fetch:
            return results

        # Schwab Batch Fetch
        if self.schwab_breaker.is_available():
            try:
                from src.clients.schwab_client import get_realtime_quotes_batch as schwab_batch

                sq_batch = schwab_batch(to_fetch)
                with self._cache_lock:
                    for s, sq in sq_batch.items():
                        if sq and sq.get("last_price") and float(sq["last_price"]) > 0:
                            qd = QuoteData(
                                symbol=s,
                                last_price=float(sq["last_price"]),
                                bid=float(sq.get("bid") or 0.0),
                                ask=float(sq.get("ask") or 0.0),
                                open=float(sq.get("open") or 0.0),
                                high=float(sq.get("high") or 0.0),
                                low=float(sq.get("low") or 0.0),
                                close=float(sq.get("close") or 0.0),
                                volume=int(sq.get("volume") or 0),
                                net_change=float(sq.get("net_change") or 0.0),
                                net_percent_change=float(sq.get("net_percent_change") or 0.0),
                                source="SCHWAB",
                                timestamp=now,
                            )
                            self._cache_execution[s] = (now, qd)
                            results[s] = qd
                self.schwab_breaker.record_success()
            except Exception as e:
                logger.warning(f"[ExecutionChannel] Schwab batch failed: {e}")
                self.schwab_breaker.record_failure(str(e))

        # Check missing equities
        missing = [s for s in to_fetch if s not in results]
        if missing:
            logger.warning(f"[ExecutionChannel] {len(missing)} missing from Schwab; pulling backup.")
            backup_quotes = self._fetch_yahoo_direct_batch(missing)
            with self._cache_lock:
                for s, qd in backup_quotes.items():
                    qd.source = f"{qd.source}_EMERGENCY_FALLBACK"
                    self._cache_execution[s] = (now, qd)
                    results[s] = qd

        return results

    # =========================================================================
    # 2. BROAD MARKET INDEX CHANNEL (YAHOO DIRECT REST EXCLUSIVELY)
    # =========================================================================
    def get_index_quote(self, symbol: str) -> Optional[QuoteData]:
        """
        Fetch broad market indices (VIX, SPX, NDX, RUT, etc.) via direct Yahoo REST.
        Bypasses Schwab 404s and Alpaca rejections entirely.
        Shared 15s cache across all threads.
        """
        clean_sym = self._clean_symbol(symbol)
        now = time.time()

        with self._cache_lock:
            cached = self._cache_index.get(clean_sym)
            if cached and (now - cached[0] < self.ttl_index):
                return cached[1]

        ticker_lock = self._get_ticker_lock(f"idx_{clean_sym}")
        with ticker_lock:
            with self._cache_lock:
                cached = self._cache_index.get(clean_sym)
                if cached and (now - cached[0] < self.ttl_index):
                    return cached[1]

            qd = self._fetch_yahoo_direct(clean_sym, is_index=True)
            if qd:
                with self._cache_lock:
                    self._cache_index[clean_sym] = (now, qd)
                return qd

            # Fallback to yfinance fast_info
            qd_yf = self._fetch_yfinance_fast(f"^{clean_sym}")
            if qd_yf:
                with self._cache_lock:
                    self._cache_index[clean_sym] = (now, qd_yf)
                return qd_yf

        return None

    # =========================================================================
    # 3. SURVEILLANCE & WATCHLIST CHANNEL (SPLIT ACROSS NON-SCHWAB)
    # =========================================================================
    def get_watchlist_quote(self, symbol: str) -> Optional[QuoteData]:
        """Fetch quote for watchlist stalking without touching Schwab."""
        clean_sym = self._clean_symbol(symbol)
        if not clean_sym:
            return None

        if self._is_index(clean_sym):
            return self.get_index_quote(clean_sym)

        now = time.time()
        with self._cache_lock:
            cached = self._cache_surveillance.get(clean_sym)
            if cached and (now - cached[0] < self.ttl_surveillance):
                return cached[1]

        # 1. Try Alpaca Snapshot if credentials configured
        qd_alpaca = self._fetch_alpaca_snapshot_single(clean_sym)
        if qd_alpaca:
            with self._cache_lock:
                self._cache_surveillance[clean_sym] = (now, qd_alpaca)
            return qd_alpaca

        # 2. Try Yahoo Direct REST (~200ms)
        qd_yahoo = self._fetch_yahoo_direct(clean_sym)
        if qd_yahoo:
            with self._cache_lock:
                self._cache_surveillance[clean_sym] = (now, qd_yahoo)
            return qd_yahoo

        # 3. Try Tastytrade institutional quote
        qd_tt = self._fetch_tastytrade_quote(clean_sym)
        if qd_tt:
            with self._cache_lock:
                self._cache_surveillance[clean_sym] = (now, qd_tt)
            return qd_tt

        # 4. Fallback to yfinance
        qd_yf = self._fetch_yfinance_fast(clean_sym)
        if qd_yf:
            with self._cache_lock:
                self._cache_surveillance[clean_sym] = (now, qd_yf)
            return qd_yf

        return None

    def get_watchlist_quotes_batch(self, symbols: List[str]) -> Dict[str, QuoteData]:
        """
        High-throughput batch quotes for watchlist stalking (10–100 tickers).
        Runs via concurrent Yahoo Direct REST workers and Alpaca snapshots.
        ZERO impact on Schwab connection.
        """
        results: Dict[str, QuoteData] = {}
        if not symbols:
            return results

        clean_syms = list(dict.fromkeys(self._clean_symbol(s) for s in symbols if s))
        now = time.time()
        to_fetch: List[str] = []

        with self._cache_lock:
            for s in clean_syms:
                if self._is_index(s):
                    cached_idx = self._cache_index.get(s)
                    if cached_idx and (now - cached_idx[0] < self.ttl_index):
                        results[s] = cached_idx[1]
                    else:
                        to_fetch.append(s)
                else:
                    cached = self._cache_surveillance.get(s)
                    if cached and (now - cached[0] < self.ttl_surveillance):
                        results[s] = cached[1]
                    else:
                        to_fetch.append(s)

        if not to_fetch:
            return results

        # 1. Fetch via Alpaca snapshots if credentials available
        alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        if alpaca_key and alpaca_secret and self.alpaca_breaker.is_available():
            alpaca_syms = [s for s in to_fetch if not self._is_index(s)]
            alp_quotes = self._fetch_alpaca_snapshots_batch(alpaca_syms)
            with self._cache_lock:
                for s, qd in alp_quotes.items():
                    self._cache_surveillance[s] = (now, qd)
                    results[s] = qd
            to_fetch = [s for s in to_fetch if s not in results]

        if not to_fetch:
            return results

        # 2. Fetch remaining concurrently via Yahoo Direct REST (pool of 8 workers)
        yahoo_quotes = self._fetch_yahoo_direct_batch(to_fetch)
        with self._cache_lock:
            for s, qd in yahoo_quotes.items():
                if self._is_index(s):
                    self._cache_index[s] = (now, qd)
                else:
                    self._cache_surveillance[s] = (now, qd)
                results[s] = qd

        # 3. Last-resort fallback for any still missing: yfinance
        still_missing = [s for s in to_fetch if s not in results]
        if still_missing:
            for s in still_missing:
                try:
                    q_yf = self._fetch_yfinance_fast(f"^{s}" if self._is_index(s) else s)
                    if q_yf:
                        results[s] = q_yf
                except Exception:
                    pass

        return results

    # =========================================================================
    # 4. UNIFIED CONVENIENCE API (BACKWARD COMPATIBLE)
    # =========================================================================
    def get_price(self, symbol: str, context: str = "surveillance") -> Optional[float]:
        """
        Fast float price lookup.
        context="intraday" -> Schwab Execution Channel
        context="surveillance" (default) -> Non-Schwab Split Channel
        """
        if not symbol:
            return None

        clean_sym = self._clean_symbol(symbol)
        if self._is_index(clean_sym):
            q = self.get_index_quote(clean_sym)
            return q.last_price if q else None

        if context.lower() in ("intraday", "execution", "live", "trade"):
            q = self.get_live_execution_quote(clean_sym)
        else:
            q = self.get_watchlist_quote(clean_sym)

        return q.last_price if q else None

    def get_prices_batch(self, symbols: List[str], context: str = "surveillance") -> Dict[str, float]:
        """Batch float prices lookup."""
        if not symbols:
            return {}

        if context.lower() in ("intraday", "execution", "live", "trade"):
            quotes = self.get_live_execution_quotes_batch(symbols)
        else:
            quotes = self.get_watchlist_quotes_batch(symbols)

        return {s: q.last_price for s, q in quotes.items() if q and q.last_price > 0}

    def format_quote_text(self, symbol: str) -> Optional[str]:
        """Format a comprehensive quote block for LLM prompts and deep research context."""
        clean_sym = self._clean_symbol(symbol)
        q = self.get_watchlist_quote(clean_sym)
        if not q or q.last_price <= 0:
            q = self.get_live_execution_quote(clean_sym)

        if not q or q.last_price <= 0:
            return None

        day_range = f"{q.low:.2f} - {q.high:.2f}" if (q.low > 0 and q.high > 0) else "n/a"
        bid_ask = f"{q.bid:.2f} / {q.ask:.2f}" if (q.bid > 0 and q.ask > 0) else "n/a"
        vol_str = f"{q.volume:,}" if q.volume > 0 else "n/a"

        lines = [
            f"REAL-TIME QUOTE for {clean_sym} (source: {q.source}):",
            f"- Last: {q.last_price:.2f}",
            f"- Bid/Ask: {bid_ask}",
            f"- Net Change: {q.net_change:+.2f} ({q.net_percent_change:+.2f}%)",
            f"- Day Range: {day_range}",
            f"- Open: {q.open:.2f}" if q.open > 0 else "- Open: n/a",
            f"- Previous Close: {q.close:.2f}" if q.close > 0 else "- Previous Close: n/a",
            f"- Volume: {vol_str}",
        ]

        # Enrich with Tastytrade IV Rank & 30d HV if available
        if q.iv_rank is None and not self._is_index(clean_sym):
            tt_metrics = self.enrich_with_tastytrade_metrics([clean_sym])
            if clean_sym in tt_metrics:
                m = tt_metrics[clean_sym]
                q.iv_rank = m.get("iv_rank")
                q.iv_percentile = m.get("iv_percentile")
                q.hv30 = m.get("hv30")

        if q.iv_rank is not None or q.hv30 is not None:
            vol_parts = []
            if q.iv_rank is not None:
                vol_parts.append(f"IV Rank: {q.iv_rank:.1f}%")
            if q.iv_percentile is not None:
                vol_parts.append(f"IV Pct: {q.iv_percentile:.1f}%")
            if q.hv30 is not None:
                vol_parts.append(f"30d HV: {q.hv30:.1f}%")
            if vol_parts:
                lines.append(f"- Volatility Metrics (Tastytrade): {', '.join(vol_parts)}")

        return "\n".join(lines)

    # =========================================================================
    # LOW-LEVEL PROVIDER ADAPTERS (NON-SCHWAB)
    # =========================================================================
    def _fetch_yahoo_direct(self, symbol: str, is_index: bool = False) -> Optional[QuoteData]:
        """Direct query to Yahoo Finance v8 chart REST endpoint (~180–220ms, no API keys)."""
        clean = self._clean_symbol(symbol)
        yahoo_sym = f"^{clean}" if (is_index or self._is_index(clean)) else clean

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1m&range=1d"

        try:
            r = requests.get(url, headers=headers, timeout=3.5)
            if r.status_code == 200:
                data = r.json()
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta.get("regularMarketPrice")
                if price is not None and float(price) > 0:
                    px = float(price)
                    prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or px)
                    net_chg = round(px - prev_close, 2)
                    net_pct = round((net_chg / prev_close) * 100, 2) if prev_close > 0 else 0.0

                    return QuoteData(
                        symbol=clean,
                        last_price=px,
                        open=float(meta.get("regularMarketDayOpen") or 0.0),
                        high=float(meta.get("regularMarketDayHigh") or 0.0),
                        low=float(meta.get("regularMarketDayLow") or 0.0),
                        close=prev_close,
                        volume=int(meta.get("regularMarketVolume") or 0),
                        net_change=net_chg,
                        net_percent_change=net_pct,
                        source="YAHOO_DIRECT",
                        timestamp=time.time(),
                    )
        except Exception as e:
            logger.debug(f"Yahoo Direct query failed for {symbol}: {e}")

        return None

    def _fetch_yahoo_direct_batch(self, symbols: List[str]) -> Dict[str, QuoteData]:
        """Fetch multiple symbols concurrently via Yahoo Direct REST."""
        results: Dict[str, QuoteData] = {}
        if not symbols:
            return results

        futures = {
            self._surveillance_executor.submit(self._fetch_yahoo_direct, sym): sym
            for sym in symbols
        }
        for fut in concurrent.futures.as_completed(futures):
            sym = futures[fut]
            try:
                qd = fut.result()
                if qd and qd.last_price > 0:
                    results[sym] = qd
            except Exception as e:
                logger.debug(f"Yahoo concurrent worker error for {sym}: {e}")

        return results

    def _fetch_alpaca_snapshot_single(self, symbol: str) -> Optional[QuoteData]:
        """Fetch single symbol quote from Alpaca latest trade endpoint."""
        alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        if not alpaca_key or not alpaca_secret or self._is_index(symbol):
            return None

        clean = self._clean_symbol(symbol)
        base_url = (
            os.getenv("ALPACA_DATA_URL")
            or os.getenv("ALPACA_API_URL")
            or "https://data.alpaca.markets"
        ).strip().rstrip("/")
        if "paper-api" in base_url or "api.alpaca.markets" in base_url:
            base_url = "https://data.alpaca.markets"
        if "/v2" not in base_url:
            base_url = f"{base_url}/v2"

        url = f"{base_url}/stocks/{clean}/trades/latest"
        headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}

        try:
            r = requests.get(url, headers=headers, timeout=3.0)
            if r.status_code == 200:
                trade = r.json().get("trade") or {}
                px = trade.get("p")
                if px and float(px) > 0:
                    return QuoteData(
                        symbol=clean,
                        last_price=float(px),
                        volume=int(trade.get("s") or 0),
                        source="ALPACA",
                        timestamp=time.time(),
                    )
        except Exception as e:
            logger.debug(f"Alpaca single trade lookup failed for {symbol}: {e}")

        return None

    def _fetch_alpaca_snapshots_batch(self, symbols: List[str]) -> Dict[str, QuoteData]:
        """Fetch multiple symbols in a single request from Alpaca snapshots."""
        results: Dict[str, QuoteData] = {}
        alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
        if not alpaca_key or not alpaca_secret or not symbols:
            return results

        clean_syms = [s for s in symbols if not self._is_index(s)]
        if not clean_syms:
            return results

        base_url = "https://data.alpaca.markets/v2"
        headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}

        # Chunk into slices of 50
        for i in range(0, len(clean_syms), 50):
            chunk = clean_syms[i : i + 50]
            syms_param = ",".join(chunk)
            url = f"{base_url}/stocks/snapshots?symbols={syms_param}"
            try:
                r = requests.get(url, headers=headers, timeout=4.0)
                if r.status_code == 200:
                    data = r.json()
                    for sym, snap in data.items():
                        trade = snap.get("latestTrade") or {}
                        quote = snap.get("latestQuote") or {}
                        daily = snap.get("dailyBar") or {}
                        prev = snap.get("prevDailyBar") or {}

                        last_p = trade.get("p") or quote.get("ap") or quote.get("bp")
                        if last_p and float(last_p) > 0:
                            px = float(last_p)
                            close_p = float(prev.get("c") or px)
                            net_c = round(px - close_p, 2)
                            net_p = round((net_c / close_p) * 100, 2) if close_p > 0 else 0.0

                            results[sym] = QuoteData(
                                symbol=sym,
                                last_price=px,
                                bid=float(quote.get("bp") or 0.0),
                                ask=float(quote.get("ap") or 0.0),
                                open=float(daily.get("o") or 0.0),
                                high=float(daily.get("h") or 0.0),
                                low=float(daily.get("l") or 0.0),
                                close=close_p,
                                volume=int(daily.get("v") or 0),
                                net_change=net_c,
                                net_percent_change=net_p,
                                source="ALPACA",
                                timestamp=time.time(),
                            )
            except Exception as e:
                logger.debug(f"Alpaca snapshot chunk failed: {e}")

        return results

    def _fetch_yfinance_fast(self, symbol: str) -> Optional[QuoteData]:
        """Last-resort fallback via yfinance fast_info."""
        try:
            # pyrefly: ignore [missing-import]
            import yfinance as yf

            t = yf.Ticker(symbol)
            fi = t.fast_info
            px = fi.get("last_price")
            if px and float(px) > 0:
                clean = self._clean_symbol(symbol)
                prev_c = float(fi.get("previous_close") or px)
                return QuoteData(
                    symbol=clean,
                    last_price=float(px),
                    bid=float(fi.get("bid") or 0.0),
                    ask=float(fi.get("ask") or 0.0),
                    open=float(fi.get("open") or 0.0),
                    high=float(fi.get("day_high") or 0.0),
                    low=float(fi.get("day_low") or 0.0),
                    close=prev_c,
                    volume=int(fi.get("last_volume") or 0),
                    net_change=round(float(px) - prev_c, 2),
                    net_percent_change=round(((float(px) - prev_c) / prev_c) * 100, 2) if prev_c > 0 else 0.0,
                    source="YFINANCE",
                    timestamp=time.time(),
                )
        except Exception as e:
            logger.debug(f"yfinance fallback failed for {symbol}: {e}")

        return None

    def _fetch_tastytrade_quote(self, symbol: str) -> Optional[QuoteData]:
        """Fetch quote via Tastytrade DXLink / market-metrics."""
        if not self.tastytrade_breaker.is_available() or self._is_index(symbol):
            return None
        try:
            from src.clients.tastytrade_client import TastytradeClient
            client = TastytradeClient()
            q = client.get_realtime_quote(symbol)
            if q and q.get("last_price") and float(q["last_price"]) > 0:
                px = float(q["last_price"])
                self.tastytrade_breaker.record_success()
                return QuoteData(
                    symbol=symbol,
                    last_price=px,
                    bid=float(q.get("bid") or 0.0),
                    ask=float(q.get("ask") or 0.0),
                    source="TASTYTRADE",
                    timestamp=time.time(),
                )
        except Exception as e:
            logger.debug(f"Tastytrade quote fetch failed for {symbol}: {e}")
            self.tastytrade_breaker.record_failure(str(e))
        return None

    def enrich_with_tastytrade_metrics(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch Tastytrade institutional volatility metrics (IV Rank, IV Pct, HV30)."""
        if not symbols or not self.tastytrade_breaker.is_available():
            return {}
        try:
            from src.clients.tastytrade_client import TastytradeClient
            client = TastytradeClient()
            items = client.get_market_metrics(symbols)
            results = {}
            for it in items:
                sym = it.get("symbol")
                if not sym:
                    continue
                ivr = it.get("implied-volatility-index-rank")
                ivp = it.get("implied-volatility-percentile")
                hv = it.get("historical-volatility-30-day")

                def _to_flt(val):
                    try:
                        return float(val) if val is not None else None
                    except (ValueError, TypeError):
                        return None

                ivr_f = _to_flt(ivr)
                ivp_f = _to_flt(ivp)
                hv_f = _to_flt(hv)
                results[sym] = {
                    "iv_rank": round(ivr_f * 100, 1) if ivr_f is not None and ivr_f <= 1.0 else (round(ivr_f, 1) if ivr_f else None),
                    "iv_percentile": round(ivp_f * 100, 1) if ivp_f is not None and ivp_f <= 1.0 else (round(ivp_f, 1) if ivp_f else None),
                    "hv30": round(hv_f * 100, 1) if hv_f is not None and hv_f <= 1.0 else (round(hv_f, 1) if hv_f else None),
                }
            self.tastytrade_breaker.record_success()
            return results
        except Exception as e:
            logger.debug(f"Tastytrade market metrics fetch failed: {e}")
            self.tastytrade_breaker.record_failure(str(e))
            return {}


# Global singleton instance
quote_router = QuoteRouter()
