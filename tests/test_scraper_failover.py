from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.logic.process_survivor import scrape_survivor_task

def test_scrape_survivor_task_retries_alternate_profile(tmp_path, monkeypatch):
    survivor = {"Ticker": "TEST"}
    out_dir = tmp_path / "raw"
    out_dir.mkdir()
    
    # Mock signed_in_profiles to return 2 profiles
    monkeypatch.setattr("src.data.tv_scraper.signed_in_profiles", lambda: ["tv_chrome_profile_1", "tv_chrome_profile_2"])
    monkeypatch.setattr("src.data.tv_scraper.is_profile_locked", lambda p: False)

    call_profiles = []

    class MockScraper:
        def __init__(self, chrome_profile=None, target_date=None, headless=True):
            self.profile = chrome_profile
            call_profiles.append(chrome_profile)

        def capture_ticker(self, ticker, lookback_days=90):
            if self.profile == "tv_chrome_profile_1":
                raise RuntimeError("Simulated failure on profile 1")
            # Profile 2 succeeds

    monkeypatch.setattr("src.logic.process_survivor.TVScraper", MockScraper)

    # Execute scrape_survivor_task
    scrape_survivor_task(
        survivor=survivor,
        out_dir=out_dir,
        today_str="2026-10-24",
        worker_id=1,
        chrome_profile="tv_chrome_profile_1",
        force=True,
    )

    # Verify that it tried profile 1 first, failed, and cleanly retried on profile 2
    assert call_profiles == ["tv_chrome_profile_1", "tv_chrome_profile_2"]
