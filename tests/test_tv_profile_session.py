"""
TradingView profile session detection.

The saved chart layout is private to the TradingView account. A profile without
a login session is served "Can't open this chart layout" (no canvas) and every
scrape on it fails — measured 2026-10-13, when only tv_chrome_profile_1 and _2
carried a session and the rest produced the intermittent "Scrape failed for
<TICKER>" errors. The probe is read-only and hermetic: it only inspects the
EXISTING profiles on disk and never creates one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.data.tv_scraper import (
    profile_has_tv_session,
    profile_tv_session_state,
    rank_profiles_signed_in_first,
    signed_in_profiles,
    usable_profiles,
)


def _write_cookies(profile: Path, rows):
    """Minimal Chrome-shaped Cookies DB (values are DPAPI blobs in reality;
    only host_key/name are plaintext, which is all the probe reads)."""
    cookies_dir = profile / "Default" / "Network"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cookies_dir / "Cookies"))
    try:
        conn.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB)"
        )
        conn.executemany("INSERT INTO cookies VALUES (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


TV_COOKIE = (".tradingview.com", "sessionid", b"\x01\x02\x03dpapi")
ANALYTICS = (".tradingview.com", "_ga", b"\x01\x02\x03dpapi")


def test_a_session_cookie_means_signed_in(tmp_path):
    profile = tmp_path / "tv_chrome_profile_1"
    _write_cookies(profile, [ANALYTICS, TV_COOKIE])
    assert profile_has_tv_session(profile) is True


def test_analytics_cookies_alone_are_not_a_session(tmp_path):
    """A signed-out profile still collects _ga / device_t / g_state, so the probe
    must key on the login cookie or it would call every profile signed in."""
    profile = tmp_path / "tv_chrome_profile_3"
    _write_cookies(
        profile,
        [ANALYTICS, ("www.tradingview.com", "g_state", b"x"), (".tradingview.com", "device_t", b"x")],
    )
    assert profile_has_tv_session(profile) is False


def test_a_profile_with_no_cookies_db_is_not_signed_in(tmp_path):
    assert profile_has_tv_session(tmp_path / "tv_chrome_profile_9") is False


def test_an_unreadable_cookies_db_is_unknown_never_signed_out(tmp_path):
    """A browser still running on the profile locks its Cookies DB (every killed
    scrape leaves one behind). Reading that as "signed out" would push the job
    onto a broken profile, so it must stay "unknown"."""
    locked = tmp_path / "tv_chrome_profile_2"
    cookies_dir = locked / "Default" / "Network"
    cookies_dir.mkdir(parents=True)
    (cookies_dir / "Cookies").write_bytes(b"not a sqlite database at all")

    assert profile_has_tv_session(locked) is False
    assert profile_tv_session_state(locked) == "unknown"
    assert usable_profiles([locked.name]) == [locked.name]


def test_only_existing_profiles_are_listed_and_signed_in_ones_rank_first(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.BASE_DIR", tmp_path)
    for name, rows in (
        ("tv_chrome_profile", [ANALYTICS]),
        ("tv_chrome_profile_1", [TV_COOKIE]),
        ("tv_chrome_profile_2", [TV_COOKIE]),
        ("tv_chrome_profile_3", [ANALYTICS]),
    ):
        _write_cookies(tmp_path / name, rows)
    (tmp_path / "tv_chrome_profile_4").mkdir()  # exists, never launched, no cookies

    assert signed_in_profiles() == ["tv_chrome_profile_1", "tv_chrome_profile_2"]

    names = [
        "tv_chrome_profile", "tv_chrome_profile_1", "tv_chrome_profile_2",
        "tv_chrome_profile_3", "tv_chrome_profile_4",
    ]
    # signed-in first, then unprovable (never launched), then provably signed out.
    assert rank_profiles_signed_in_first(names) == [
        "tv_chrome_profile_1", "tv_chrome_profile_2", "tv_chrome_profile_4",
        "tv_chrome_profile", "tv_chrome_profile_3",
    ]
    # A worker is never handed a profile that is provably signed out.
    assert usable_profiles(names) == [
        "tv_chrome_profile_1", "tv_chrome_profile_2", "tv_chrome_profile_4",
    ]


def test_no_profiles_at_all_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.BASE_DIR", tmp_path)
    assert signed_in_profiles() == []
    assert usable_profiles() == []
    assert rank_profiles_signed_in_first([]) == []
