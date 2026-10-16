"""
Tastytrade API Client for Cloud Quote Alerts.
Handles OAuth 2.0 automatic token refresh and quote alert CRUD operations.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Credentials from environment (Strictly never logged/printed)
CLIENT_ID = os.getenv("TASTY_TRADE_CLIENT_ID") or os.getenv("TASTYTRADE_CLIENT_ID")
CLIENT_SECRET = os.getenv("TASTY_TRADE_CLIENT_SECRET") or os.getenv("TASTYTRADE_CLIENT_SECRET")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tastytrade_token.json")
BASE_URL = "https://api.tastyworks.com"
USER_AGENT = "StockResearchAlerts/1.0"


class TastytradeClient:
    def __init__(self):
        self.token_path = os.path.abspath(TOKEN_PATH)
        self.client_id = CLIENT_ID
        self.client_secret = CLIENT_SECRET
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self._lock = threading.RLock()
        self._quote_token: Optional[Dict[str, Any]] = None
        self._quote_token_at: float = 0.0

    def _load_tokens(self) -> bool:
        if not os.path.exists(self.token_path):
            logger.warning(f"Tastytrade token file not found at {self.token_path}. Run setup_tastytrade.py first.")
            return False

        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token") or os.getenv("TASTY_TRADE_REFRESH_TOKEN") or os.getenv("TASTYTRADE_REFRESH_TOKEN")
            created_at = data.get("created_at", os.path.getmtime(self.token_path))
            expires_in = data.get("expires_in", 900)
            self.token_expiry = created_at + expires_in
            return bool(self.access_token or self.refresh_token)
        except Exception as e:
            logger.error(f"Failed to load Tastytrade tokens: {e}")
            return False

    def _save_tokens(self, token_data: Dict[str, Any]) -> None:
        token_data["created_at"] = time.time()
        # Preserve permanent refresh token if not in response payload
        if not token_data.get("refresh_token") and self.refresh_token:
            token_data["refresh_token"] = self.refresh_token
        os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token") or self.refresh_token
        self.token_expiry = token_data["created_at"] + token_data.get("expires_in", 900)

    def refresh_access_token(self) -> bool:
        """Exchange refresh token for a fresh 15-minute access token."""
        if not self.refresh_token:
            if not self._load_tokens() or not self.refresh_token:
                logger.error("No refresh token available to renew session.")
                return False

        token_url = f"{BASE_URL}/oauth/token"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }

        for attempt in range(1, 3):
            try:
                resp = requests.post(token_url, json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    self._save_tokens(data)
                    logger.info("Successfully refreshed Tastytrade OAuth access token.")
                    return True
                else:
                    logger.error(f"Failed to refresh Tastytrade token (HTTP {resp.status_code}): {resp.text}")
                    return False
            except Exception as e:
                if attempt == 1:
                    logger.warning(f"Tastytrade token refresh attempt 1 failed ({e}); retrying in 1s...")
                    time.sleep(1.0)
                else:
                    logger.error(f"Exception while refreshing Tastytrade token: {e}")
                    return False
        return False

    def get_auth_headers(self) -> Optional[Dict[str, str]]:
        """Return valid Authorization headers, auto-refreshing expired tokens (one refresh at a time)."""
        with self._lock:
            if not self.access_token:
                if not self._load_tokens():
                    return None

            # Refresh token if within 60 seconds of expiring
            if time.time() >= (self.token_expiry - 60):
                logger.info("Tastytrade access token near expiry or expired; refreshing...")
                if not self.refresh_access_token():
                    return None

            return {
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    def get_quote_alerts(self) -> List[Dict[str, Any]]:
        """Fetch all active quote alerts from Tastytrade cloud."""
        headers = self.get_auth_headers()
        if not headers:
            return []

        url = f"{BASE_URL}/quote-alerts"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("items", [])
            else:
                logger.warning(f"Tastytrade get_quote_alerts returned HTTP {resp.status_code}: {resp.text}")
                return []
        except Exception as e:
            logger.debug(f"Failed to retrieve Tastytrade quote alerts: {e}")
            return []

    def create_quote_alert(
        self,
        symbol: str,
        threshold: float,
        operator: str = "LessThanOrEqualTo",
        field: str = "Last",
        expires_at: Optional[str] = None,
        expires_days: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a cloud price alert on Tastytrade with optional custom expiration.

        Args:
            symbol: Ticker symbol (e.g. 'AMZN')
            threshold: Price level (e.g. 265.0)
            operator: 'LessThanOrEqualTo' | 'GreaterThanOrEqualTo' | '<=' | '>='
            field: 'Last' | 'Bid' | 'Ask'
            expires_at: Custom ISO expiration timestamp or 'YYYY-MM-DD' date string.
            expires_days: Days from now until alert expires.
        """
        headers = self.get_auth_headers()
        if not headers:
            return None

        # Normalize operator
        op_norm = "<"
        if operator in (">", ">=", "GreaterThanOrEqualTo", "above", "GT", "GE", "greater_than"):
            op_norm = ">"
        elif operator in ("<", "<=", "LessThanOrEqualTo", "below", "LT", "LE", "less_than"):
            op_norm = "<"

        payload: Dict[str, Any] = {
            "symbol": symbol.strip().upper(),
            "field": field,
            "operator": op_norm,
            "threshold": round(float(threshold), 2),
        }

        # Calculate custom expiration if provided and valid
        from datetime import datetime, timedelta, timezone
        if expires_at:
            clean_exp = str(expires_at).strip()
            if clean_exp.upper() not in ("N/A", "NONE", "NULL", "", "0", "FALSE"):
                if len(clean_exp) == 10 and clean_exp.count("-") == 2:
                    # 'YYYY-MM-DD' format -> set to 20:00 UTC on that date
                    payload["expires-at"] = f"{clean_exp}T20:00:00.000+00:00"
                elif "T" in clean_exp:
                    payload["expires-at"] = clean_exp
        elif expires_days is not None and expires_days > 0:
            exp_dt = datetime.now(timezone.utc) + timedelta(days=expires_days)
            payload["expires-at"] = exp_dt.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")

        url = f"{BASE_URL}/quote-alerts"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code in (200, 201):
                data = resp.json()
                alert_data = data.get("data", {})
                exp_display = alert_data.get("expires-at", "Default 90d")[:10]
                logger.info(
                    f"🔔 [Tastytrade Alert Created] {symbol} {op_norm} ${threshold:.2f} (Expires: {exp_display}, ID: {alert_data.get('alert-external-id', 'N/A')})"
                )
                return alert_data
            else:
                logger.error(f"Failed to create Tastytrade alert for {symbol} (HTTP {resp.status_code}): {resp.text}")
                return None
        except Exception as e:
            logger.error(f"Exception creating Tastytrade alert for {symbol}: {e}")
            return None

    def delete_quote_alert(self, alert_id: str) -> bool:
        """Delete an alert by external ID."""
        headers = self.get_auth_headers()
        if not headers:
            return False

        url = f"{BASE_URL}/quote-alerts/{alert_id}"
        try:
            resp = requests.delete(url, headers=headers, timeout=10)
            if resp.status_code in (200, 204):
                logger.info(f"Deleted Tastytrade quote alert {alert_id}")
                return True
            else:
                logger.warning(f"Failed to delete alert {alert_id}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error deleting alert {alert_id}: {e}")
            return False

    def delete_all_quote_alerts(self) -> int:
        """Delete ALL active quote alerts across all symbols in parallel. Returns count of deleted alerts."""
        alerts = self.get_quote_alerts()
        if not alerts:
            return 0

        import concurrent.futures
        aids = [a.get("alert-external-id") for a in alerts if a.get("alert-external-id")]

        def _del(aid):
            return self.delete_quote_alert(aid)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(_del, aids))
            deleted = sum(1 for r in results if r)

        logger.info(f"Deleted {deleted}/{len(alerts)} quote alerts from Tastytrade.")
        return deleted

    def modify_quote_alert(
        self,
        alert_id: str,
        symbol: str,
        threshold: float,
        operator: str = "<",
        field: str = "Last",
        expires_at: Optional[str] = None,
        expires_days: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Modify an existing quote alert by deleting the old alert and creating the new alert."""
        self.delete_quote_alert(alert_id)
        return self.create_quote_alert(
            symbol=symbol,
            threshold=threshold,
            operator=operator,
            field=field,
            expires_at=expires_at,
            expires_days=expires_days,
        )

    def sync_watch_levels(
        self,
        watch_data: Dict[str, Any],
        custom_expires_at: Optional[str] = None,
        custom_expires_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Sync research watch levels into Tastytrade cloud alerts.
        Modifies/updates existing alerts for this ticker so levels are always accurate without duplicates."""
        ticker = watch_data.get("ticker", "").strip().upper()
        if not ticker:
            return []

        shares_plan = watch_data.get("shares_plan", {})
        options_plan = watch_data.get("options_plan", {})
        invalidation = watch_data.get("invalidation", {})

        side = str(watch_data.get("side") or shares_plan.get("side") or "LONG").upper()
        entry_high = shares_plan.get("entry_zone_high") or shares_plan.get("entry_zone_low")
        entry_low = shares_plan.get("entry_zone_low") or entry_high
        breakout_level = shares_plan.get("breakout_level")
        tactical_stop = shares_plan.get("tactical_stop") or invalidation.get("price_level")
        target_1 = shares_plan.get("target_1")
        target_2 = shares_plan.get("target_2")
        entry_type = str(shares_plan.get("entry_type", "LIMIT")).upper()

        # Determine target expiration date (e.g. from recommended options expiry)
        exp_target = custom_expires_at or options_plan.get("expiration")

        # Operators:
        # For Long: Stop Loss is < threshold, Targets are > threshold
        # For Short: Stop Loss is > threshold, Targets are < threshold
        stop_op = ">" if side == "SHORT" else "<"
        target_op = "<" if side == "SHORT" else ">"

        desired_specs = []
        # 1. Pullback limit entry alert
        if (entry_high and float(entry_high) > 0) or (side == "SHORT" and entry_low and float(entry_low) > 0):
            limit_op = ">" if side == "SHORT" else "<"
            limit_thresh = float(entry_low if side == "SHORT" else entry_high)
            desired_specs.append((limit_op, round(limit_thresh, 2), f"Limit Entry ({side})"))

        # 2. Breakout entry alert (if configured)
        if breakout_level and float(breakout_level) > 0:
            bo_op = "<" if side == "SHORT" else ">"
            desired_specs.append((bo_op, round(float(breakout_level), 2), f"Breakout Entry ({side})"))

        # 3. Stop loss alert
        if tactical_stop and float(tactical_stop) > 0:
            desired_specs.append((stop_op, round(float(tactical_stop), 2), "Stop Loss"))

        # 4. Target alerts
        if target_1 and float(target_1) > 0:
            desired_specs.append((target_op, round(float(target_1), 2), "Target 1"))
        if target_2 and float(target_2) > 0:
            desired_specs.append((target_op, round(float(target_2), 2), "Target 2"))

        # Fetch all existing alerts from Tastytrade for this specific ticker
        existing_all = self.get_quote_alerts()
        ticker_alerts = [
            a for a in existing_all
            if a.get("symbol", "").strip().upper() == ticker
        ]

        active_synced_alerts = []
        matched_existing_ids = set()
        unmatched_specs = []

        # Pass 1: Claim all EXACT matches first across all specs (normalizing operator strings)
        def _norm_op(raw_op: str) -> str:
            if str(raw_op) in (">", ">=", "GreaterThanOrEqualTo", "above", "GT", "GE", "greater_than", "GreaterThan"):
                return ">"
            return "<"

        for spec in desired_specs:
            op, target_thresh, label = spec
            exact_match = next(
                (a for a in ticker_alerts if _norm_op(a.get("operator", "")) == op and round(float(a.get("threshold", 0)), 2) == target_thresh and a.get("alert-external-id") not in matched_existing_ids),
                None
            )
            if exact_match:
                matched_existing_ids.add(exact_match.get("alert-external-id"))
                active_synced_alerts.append(exact_match)
            else:
                unmatched_specs.append(spec)

        # Pass 2: Modify remaining stale alerts or create new ones
        for op, target_thresh, label in unmatched_specs:
            stale_match = next(
                (a for a in ticker_alerts if _norm_op(a.get("operator", "")) == op and a.get("alert-external-id") not in matched_existing_ids),
                None
            )

            if stale_match:
                alert_id = stale_match.get("alert-external-id")
                old_thresh = stale_match.get("threshold")
                logger.info(f"[{ticker}] Modifying existing Tastytrade alert {alert_id} ({label}: ${old_thresh} -> ${target_thresh:.2f})")
                new_alert = self.modify_quote_alert(
                    alert_id=alert_id,
                    symbol=ticker,
                    threshold=target_thresh,
                    operator=op,
                    expires_at=exp_target,
                    expires_days=custom_expires_days,
                )
                matched_existing_ids.add(alert_id)
                if new_alert:
                    active_synced_alerts.append(new_alert)
            else:
                # Create brand new alert
                logger.info(f"[{ticker}] Creating new Tastytrade alert ({label}: {op} ${target_thresh:.2f})")
                new_alert = self.create_quote_alert(
                    symbol=ticker,
                    threshold=target_thresh,
                    operator=op,
                    expires_at=exp_target,
                    expires_days=custom_expires_days,
                )
                if new_alert:
                    active_synced_alerts.append(new_alert)

        # Pass 3: Clean up any remaining obsolete alerts for this ticker
        for a in ticker_alerts:
            aid = a.get("alert-external-id")
            if aid and aid not in matched_existing_ids:
                logger.info(f"[{ticker}] Cleaning up obsolete Tastytrade alert {aid} ({a.get('operator')} ${a.get('threshold')})")
                self.delete_quote_alert(aid)

        return active_synced_alerts

    def get_market_metrics(self, symbols: List[str] | str) -> List[Dict[str, Any]]:
        """Fetch IV rank, IV percentile, 30d/60d/90d historical volatility, and beta."""
        headers = self.get_auth_headers()
        if not headers:
            return []

        if isinstance(symbols, list):
            sym_str = ",".join(symbols)
        else:
            sym_str = str(symbols)

        url = f"{BASE_URL}/market-metrics?symbols={sym_str}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("items", [])
            else:
                logger.warning(f"Tastytrade market-metrics returned HTTP {resp.status_code}: {resp.text}")
                return []
        except Exception as e:
            logger.debug(f"Error fetching Tastytrade market metrics for {sym_str}: {e}")
            return []

    def _get_quote_token(self) -> Optional[Dict[str, Any]]:
        """DXLink url + token; valid ~24h, cached 20h."""
        with self._lock:
            if self._quote_token and time.time() - self._quote_token_at < 20 * 3600:
                return self._quote_token
        headers = self.get_auth_headers()
        if not headers:
            return None
        try:
            r = requests.get(f"{BASE_URL}/api-quote-tokens", headers=headers, timeout=12)
            if r.status_code != 200:
                return None
            data = r.json().get("data", {})
            if not data.get("dxlink-url") or not data.get("token"):
                return None
            with self._lock:
                self._quote_token, self._quote_token_at = data, time.time()
            return data
        except Exception as e:
            logger.debug(f"Failed to fetch DXLink quote token: {e}")
            return None

    def get_realtime_quotes_batch(self, symbols: List[str], timeout: float = 6.0) -> Dict[str, Dict[str, Any]]:
        """Many symbols over ONE DXLink websocket. Returns {SYMBOL: {bid, ask, price, last_price, prev_close, ...}}."""
        import asyncio
        import math

        import websockets

        syms = list(dict.fromkeys(str(x).upper().strip() for x in symbols if x))
        tok = self._get_quote_token() if syms else None
        if not tok:
            return {}

        fields = {
            "Quote": ["eventSymbol", "bidPrice", "askPrice"],
            "Trade": ["eventSymbol", "price", "dayVolume"],
            "Summary": ["eventSymbol", "prevDayClosePrice", "dayOpenPrice", "dayHighPrice", "dayLowPrice"],
        }

        def _f(v):
            try:
                x = float(v)
                return None if (math.isnan(x) or math.isinf(x)) else x
            except (ValueError, TypeError):
                return None

        raw: Dict[str, Dict[str, Any]] = {s_: {} for s_ in syms}

        def _ingest(data):
            i = 0
            while i < len(data) - 1:
                etype, vals = data[i], data[i + 1]
                i += 2
                names = fields.get(etype)
                if not names or not isinstance(vals, list):
                    continue
                for k in range(0, len(vals) - len(names) + 1, len(names)):
                    row = dict(zip(names, vals[k:k + len(names)]))
                    sym = str(row.get("eventSymbol") or "").upper()
                    if sym in raw:
                        raw[sym].setdefault(etype, {}).update({n: row[n] for n in names[1:]})

        def _done():
            return all(raw[s_].get("Quote") and raw[s_].get("Summary") for s_ in syms)

        async def _run():
            async with websockets.connect(tok["dxlink-url"], open_timeout=8) as ws:
                async def _send(msg):
                    await ws.send(json.dumps(msg))

                await _send({"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0", "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60})
                deadline = asyncio.get_event_loop().time() + timeout
                state = {"auth_sent": False, "channel": False}

                async def _handshake(msg):
                    mt = msg.get("type")
                    if mt == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED" and not state["auth_sent"]:
                        state["auth_sent"] = True
                        await _send({"type": "AUTH", "channel": 0, "token": tok["token"]})
                    elif mt == "AUTH_STATE" and msg.get("state") == "AUTHORIZED" and not state["channel"]:
                        state["channel"] = True
                        await _send({"type": "CHANNEL_REQUEST", "channel": 1, "service": "FEED", "parameters": {"contract": "AUTO"}})
                    elif mt == "CHANNEL_OPENED":
                        await _send({"type": "FEED_SETUP", "channel": 1, "acceptAggregationPeriod": 0.1,
                                     "acceptDataFormat": "COMPACT", "acceptEventFields": fields})
                        await _send({"type": "FEED_SUBSCRIPTION", "channel": 1,
                                     "add": [{"type": t, "symbol": s_} for s_ in syms for t in fields]})
                    elif mt == "KEEPALIVE":
                        await _send({"type": "KEEPALIVE", "channel": 0})
                    elif mt == "ERROR":
                        raise RuntimeError(f"DXLink error: {msg.get('message')}")
                while not _done():
                    left = deadline - asyncio.get_event_loop().time()
                    if left <= 0:
                        break
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=left))
                    except asyncio.TimeoutError:
                        break
                    if msg.get("type") == "FEED_DATA":
                        _ingest(msg.get("data") or [])
                    else:
                        await _handshake(msg)

        try:
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _run()).result(timeout=timeout + 6)
            except RuntimeError:
                asyncio.run(_run())
        except Exception as e:
            logger.debug(f"DXLink batch quote error for {syms[:5]}: {e}")
            with self._lock:
                self._quote_token = None

        out: Dict[str, Dict[str, Any]] = {}
        for sym, ev in raw.items():
            q, t, sm = ev.get("Quote", {}), ev.get("Trade", {}), ev.get("Summary", {})
            bid, ask = _f(q.get("bidPrice")), _f(q.get("askPrice"))
            if not bid and not ask:
                continue
            mid = round((bid + ask) / 2.0, 4) if (bid and ask) else (bid or ask)
            last = _f(t.get("price"))
            price = last if (last and bid and ask and bid * 0.99 <= last <= ask * 1.01) else mid
            prev = _f(sm.get("prevDayClosePrice"))
            out[sym] = {
                "symbol": sym, "bid": bid, "ask": ask, "mid": mid, "price": price, "last_price": price,
                "prev_close": prev, "open": _f(sm.get("dayOpenPrice")), "high": _f(sm.get("dayHighPrice")),
                "low": _f(sm.get("dayLowPrice")), "volume": int(_f(t.get("dayVolume")) or 0),
            }
        return out

    def get_realtime_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Single-symbol wrapper over the batch call."""
        return self.get_realtime_quotes_batch([symbol]).get(str(symbol).upper())


_CLIENT: Optional[TastytradeClient] = None
_CLIENT_LOCK = threading.Lock()


def get_tastytrade_client() -> TastytradeClient:
    """Process-wide client so token refresh and the quote token are shared, not redone per call."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = TastytradeClient()
        return _CLIENT
