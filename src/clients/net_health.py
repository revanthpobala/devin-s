"""Outbound network resilience for the rig: DNS cache with stale fallback, pooled sessions, link probe.

Rig logs show connect timeouts, `getaddrinfo failed` and WinError 10060/10048 hitting Yahoo, Alpaca, Tastytrade, Schwab
and Finnhub in the same minute, so the cause is the local link or resolver, not any one provider. This module (1) keeps
resolving known hosts when the resolver blips, (2) reuses connections instead of opening one per quote, and (3) writes
data/net_probe.jsonl so the failing layer (DNS, raw TCP, or provider) can be read off instead of guessed.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

from src import config

logger = logging.getLogger(__name__)

DNS_TTL_S = 300.0
DNS_STALE_MAX_S = 6 * 3600.0
PROBE_EVERY_S = 30.0
PROBE_KEEP_LINES = 5000
PROBE_HOSTS = ("query1.finance.yahoo.com", "data.alpaca.markets", "api.schwabapi.com", "api.tastyworks.com")
PROBE_IPS = (("1.1.1.1", 443), ("8.8.8.8", 443))

_real_getaddrinfo = socket.getaddrinfo
_dns_cache: Dict[Tuple, Tuple[float, List]] = {}
_dns_lock = threading.Lock()
_installed = False


def _is_literal(host: Any) -> bool:
    if not isinstance(host, str) or not host:
        return True
    if host in ("localhost",) or host.startswith("127."):
        return True
    return host.replace(".", "").isdigit() or ":" in host


def _cached_getaddrinfo(host, port, *args, **kwargs):
    if _is_literal(host):
        return _real_getaddrinfo(host, port, *args, **kwargs)
    key = (host, port, args, tuple(sorted(kwargs.items())))
    now = time.time()
    with _dns_lock:
        hit = _dns_cache.get(key)
    if hit and now - hit[0] < DNS_TTL_S:
        return hit[1]
    try:
        res = _real_getaddrinfo(host, port, *args, **kwargs)
    except OSError:
        if hit and now - hit[0] < DNS_STALE_MAX_S:
            logger.warning("DNS lookup for %s failed; serving cached address", host)
            return hit[1]
        raise
    with _dns_lock:
        _dns_cache[key] = (now, res)
    return res


def install_dns_cache() -> None:
    """Idempotent. Successful lookups are reused for 5 min; a failed lookup falls back to the last good answer."""
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _cached_getaddrinfo
    _installed = True


def new_session(pool_size: int = 16) -> requests.Session:
    """Keep-alive session: one TLS connection reused across quotes instead of DNS+TCP+TLS per symbol."""
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=pool_size, max_retries=0)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def probe_path() -> Path:
    return config.BASE_DIR / "data" / "net_probe.jsonl"


def _timed_connect(addr: Tuple[str, int], timeout: float = 4.0) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection(addr, timeout=timeout):
            return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except OSError as e:
        return {"ok": False, "ms": round((time.perf_counter() - t0) * 1000), "err": f"{type(e).__name__}:{getattr(e, 'winerror', None) or e.errno}"}


def probe_once() -> Dict[str, Any]:
    """DNS time (uncached), TCP connect to the resolved host, and TCP connect to raw IPs that need no DNS."""
    row: Dict[str, Any] = {"ts": round(time.time(), 1), "hosts": {}, "raw": {}}
    for host in PROBE_HOSTS:
        t0 = time.perf_counter()
        try:
            infos = _real_getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            dns = {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "v6": any(i[0] == socket.AF_INET6 for i in infos)}
            v4 = next((i[4] for i in infos if i[0] == socket.AF_INET), None)
        except OSError as e:
            dns, v4 = {"ok": False, "ms": round((time.perf_counter() - t0) * 1000), "err": f"{type(e).__name__}:{getattr(e, 'winerror', None) or e.errno}"}, None
        row["hosts"][host] = {"dns": dns, "tcp": _timed_connect(v4) if v4 else None}
    for ip, port in PROBE_IPS:
        row["raw"][ip] = _timed_connect((ip, port))
    return row


def _trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > PROBE_KEEP_LINES * 1.2:
            path.write_text("\n".join(lines[-PROBE_KEEP_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _probe_loop() -> None:
    path = probe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(probe_once()) + "\n")
            n += 1
            if n % 200 == 0:
                _trim(path)
        except Exception as e:
            logger.debug("net probe failed: %s", e)
        time.sleep(PROBE_EVERY_S)


def start_probe() -> Optional[threading.Thread]:
    t = threading.Thread(target=_probe_loop, name="net_probe", daemon=True)
    t.start()
    return t
