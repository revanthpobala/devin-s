"""
Tastytrade API Client for Cloud Quote Alerts.
Handles OAuth 2.0 automatic token refresh and quote alert CRUD operations.
"""

from __future__ import annotations

import json
import logging
import os
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

        try:
            resp = requests.post(token_url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self._save_tokens(data)
                logger.info("Successfully refreshed Tastytrade OAuth access token.")
                return True
            else:
                logger.error(f"Failed to refresh Tastytrade token (HTTP {resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Exception while refreshing Tastytrade token: {e}")
            return False

    def get_auth_headers(self) -> Optional[Dict[str, str]]:
        """Return valid Authorization headers, auto-refreshing expired tokens."""
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
        # 1. Pullback limit entry alert (includes 1.0% institutional front-running buffer)
        if entry_high and float(entry_high) > 0:
            limit_op = ">" if side == "SHORT" else "<"
            limit_thresh = float(entry_low * 0.99 if side == "SHORT" else entry_high * 1.01)
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

    def get_realtime_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch instantaneous institutional real-time Bid/Ask quote via Tastytrade DXLink."""
        import asyncio
        import math
        import websockets

        headers = self.get_auth_headers()
        if not headers:
            return None

        # 1. Get Quote Token
        try:
            r = requests.get(f"{BASE_URL}/api-quote-tokens", headers=headers, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json().get("data", {})
            dxlink_url = data.get("dxlink-url")
            token = data.get("token")
            if not dxlink_url or not token:
                return None
        except Exception as e:
            logger.debug(f"Failed to fetch DXLink quote token: {e}")
            return None

        def _safe_float(v):
            if v is None or v in ("NaN", "nan", "None", ""):
                return None
            try:
                flt = float(v)
                return None if (math.isnan(flt) or math.isinf(flt)) else flt
            except (ValueError, TypeError):
                return None

        async def _fetch():
            async with websockets.connect(dxlink_url) as ws:
                # 1. SETUP
                await ws.send(
                    json.dumps(
                        {
                            "type": "SETUP",
                            "channel": 0,
                            "version": "0.1-DXF-JS/0.3.0",
                            "keepaliveTimeout": 60,
                            "acceptKeepalive": True,
                        }
                    )
                )
                await ws.recv()

                # 2. AUTH
                await ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                await ws.recv()

                # 3. CHANNEL_REQUEST
                await ws.send(
                    json.dumps(
                        {
                            "type": "CHANNEL_REQUEST",
                            "channel": 1,
                            "service": "FEED",
                            "parameters": {"contract": "AUTO"},
                        }
                    )
                )
                await ws.recv()

                # 4. FEED_SETUP
                await ws.send(
                    json.dumps(
                        {
                            "type": "FEED_SETUP",
                            "channel": 1,
                            "acceptAggregationPeriod": 0.1,
                            "acceptDataFormat": "COMPACT",
                            "acceptEventFields": {
                                "Quote": ["eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize"],
                            },
                        }
                    )
                )
                await ws.recv()

                # 5. FEED_SUBSCRIPTION
                await ws.send(
                    json.dumps(
                        {
                            "type": "FEED_SUBSCRIPTION",
                            "channel": 1,
                            "add": [{"type": "Quote", "symbol": symbol.upper()}],
                        }
                    )
                )

                # Wait for feed data
                for _ in range(8):
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    msg_data = json.loads(msg)
                    if msg_data.get("type") == "FEED_DATA":
                        feed_rows = msg_data.get("data", [])
                        if len(feed_rows) >= 2 and feed_rows[0] == "Quote":
                            quote_vals = feed_rows[1]
                            if isinstance(quote_vals, list) and len(quote_vals) >= 3:
                                bid = _safe_float(quote_vals[1])
                                ask = _safe_float(quote_vals[2])
                                mid = round((bid + ask) / 2.0, 2) if (bid is not None and ask is not None) else (bid or ask)
                                price = mid or ask or bid
                                if price is not None:
                                    return {
                                        "symbol": symbol.upper(),
                                        "bid": bid,
                                        "ask": ask,
                                        "mid": mid,
                                        "price": price,
                                    }
                return None

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, _fetch()).result(timeout=10)
            else:
                return asyncio.run(_fetch())
        except Exception as e:
            logger.debug(f"DXLink quote fetch timeout/error for {symbol}: {e}")
            return None

