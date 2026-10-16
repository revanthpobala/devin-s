import socket
from unittest.mock import patch

from src.clients import net_health
from src.clients.quote_router import CircuitBreaker


def test_dns_cache_serves_stale_answer_when_resolver_fails():
    net_health._dns_cache.clear()
    good = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]
    with patch.object(net_health, "_real_getaddrinfo", return_value=good):
        assert net_health._cached_getaddrinfo("example.test", 443) == good
    with patch.object(net_health, "_real_getaddrinfo", side_effect=socket.gaierror("down")), patch.object(net_health, "DNS_TTL_S", 0.0):
        assert net_health._cached_getaddrinfo("example.test", 443) == good


def test_dns_failure_without_cache_still_raises():
    net_health._dns_cache.clear()
    with patch.object(net_health, "_real_getaddrinfo", side_effect=socket.gaierror("down")):
        try:
            net_health._cached_getaddrinfo("never-seen.test", 443)
        except OSError:
            return
    raise AssertionError("expected OSError")


def test_open_breaker_admits_a_single_probe_after_cooldown():
    b = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
    b.record_failure("x")
    b.record_failure("x")
    assert b.is_available() is False
    b._cooldown_until = 0.0
    assert [b.is_available() for _ in range(4)] == [True, False, False, False]
    b.record_success()
    assert b.is_available() is True


def test_probe_row_shape():
    with patch.object(net_health, "_real_getaddrinfo", side_effect=socket.gaierror("down")), \
         patch.object(net_health, "_timed_connect", return_value={"ok": True, "ms": 5}):
        row = net_health.probe_once()
    assert set(row["raw"]) == {"1.1.1.1", "8.8.8.8"}
    assert all(v["dns"]["ok"] is False and v["tcp"] is None for v in row["hosts"].values())
