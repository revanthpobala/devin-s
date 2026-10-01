import time
from pathlib import Path
from unittest.mock import patch

from src.data.artifact_cache import ArtifactCache


def test_sanitize_name(tmp_path: Path):
    cache = ArtifactCache(base_dir=tmp_path)
    # Test illegal Windows path characters
    raw_name = 'search_web_"Stock_Market:_Will_S&P_500_Op?'
    sanitized = cache._sanitize_name(raw_name)
    assert '"' not in sanitized
    assert ':' not in sanitized
    assert '?' not in sanitized
    assert '<' not in sanitized
    assert '>' not in sanitized
    assert '|' not in sanitized
    assert '*' not in sanitized
    assert '\\' not in sanitized
    assert '/' not in sanitized


def test_save_and_get_with_special_characters(tmp_path: Path):
    cache = ArtifactCache(base_dir=tmp_path)
    date_str = "2026-08-25"
    ticker = "GLOBAL"
    artifact_type = 'search_web_"Stock_Market:_Will_S&P_500_Op'
    payload = {"result": "ok", "items": [1, 2, 3]}

    # Should not raise OSError: [Errno 22] Invalid argument
    saved_path = cache.save(date_str, ticker, artifact_type, payload)
    assert saved_path.exists()

    cached = cache.get(date_str, ticker, artifact_type, ttl_seconds=300)
    assert cached == payload


def test_artifact_ttl_expiration(tmp_path: Path):
    cache = ArtifactCache(base_dir=tmp_path)
    date_str = "2026-08-25"
    ticker = "AAPL"
    artifact_type = "test_quote"

    cache.save(date_str, ticker, artifact_type, {"price": 150.0})

    # Fresh hit
    assert cache.get(date_str, ticker, artifact_type, ttl_seconds=10) == {"price": 150.0}

    # Expired hit. Driven by a frozen clock rather than time.sleep: a sleep of 50ms is a coin
    # flip on a loaded machine, and if the filesystem stamps a coarse mtime the whole elapsed
    # time can round to zero and the assertion below fails for no real reason.
    with patch("src.data.artifact_cache.time.time", return_value=time.time() + 3600):
        assert cache.get(date_str, ticker, artifact_type, ttl_seconds=10) is None
        # ttl=0 expires everything with any age at all
        assert cache.get(date_str, ticker, artifact_type, ttl_seconds=0) is None
