"""
Unified Artifact Cache Manager with TTL for Stock Market Agents.
Stores raw API outputs, tool responses, and debate transcripts under data/artifacts/<date>/<ticker>/
with automated TTL validation.
"""

import os
import json
import time
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

from src import config

logger = logging.getLogger(__name__)

# Standard default TTLs in seconds
DEFAULT_TTLS: Dict[str, Optional[int]] = {
    "quote": 60,            # 1 minute (real-time price quotes)
    "news": 300,            # 5 minutes (Finnhub / Alpaca news)
    "grounding": 300,       # 5 minutes (Google Grounding / Web search)
    "search_web": 300,      # 5 minutes (DuckDuckGo / Brave search)
    "options_chain": 900,   # 15 minutes (Alpaca options chains)
    "tv_options": 900,      # 15 minutes (TradingView Strategy Finder)
    "debate": None,         # Permanent for that date (Multi-agent debate)
    "dossier": 300,         # 5 minutes (Synthesized news dossier)
}


class ArtifactCache:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (config.BASE_DIR / "data" / "artifacts")

    def _sanitize_name(self, name: str) -> str:
        # Replace characters not allowed in file paths (especially Windows: \ / : * ? " < > |)
        sanitized = re.sub(r'[\<\>\:\"\/\\\|\?\*\x00-\x1f]', '_', str(name))
        # Collapse multiple underscores and strip trailing dots/spaces/underscores
        sanitized = re.sub(r'_+', '_', sanitized).strip('. _')
        return sanitized or "unnamed"

    def _get_ticker_dir(self, date_str: str, ticker: str) -> Path:
        clean_date = self._sanitize_name(date_str)
        clean_ticker = self._sanitize_name(ticker).upper()
        tdir = self.base_dir / clean_date / clean_ticker
        tdir.mkdir(parents=True, exist_ok=True)
        return tdir

    def _get_file_path(self, date_str: str, ticker: str, artifact_type: str) -> Path:
        tdir = self._get_ticker_dir(date_str, ticker)
        clean_artifact = self._sanitize_name(artifact_type)
        return tdir / f"{clean_artifact}.json"

    def get(
        self,
        date_str: str,
        ticker: str,
        artifact_type: str,
        ttl_seconds: Optional[int] = -1,
    ) -> Optional[Any]:
        """
        Retrieves cached artifact data if it exists and has not expired past its TTL.
        Pass ttl_seconds=None to bypass TTL check (indefinite persistence).
        Pass ttl_seconds=-1 to use default TTL from DEFAULT_TTLS.
        """
        file_path = self._get_file_path(date_str, ticker, artifact_type)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            cached_time = payload.get("_cached_at", 0)
            data = payload.get("data")

            # Determine TTL
            effective_ttl = (
                DEFAULT_TTLS.get(artifact_type, 300)
                if ttl_seconds == -1
                else ttl_seconds
            )

            if effective_ttl is not None:
                age_seconds = time.time() - cached_time
                if age_seconds > effective_ttl:
                    logger.debug(
                        f"[{ticker}] Artifact '{artifact_type}' EXPIRED (age: {age_seconds:.1f}s > TTL: {effective_ttl}s)"
                    )
                    return None

            logger.info(
                f"[{ticker}] Reusing fresh cached artifact '{artifact_type}' (age: {time.time() - cached_time:.1f}s)"
            )
            return data
        except Exception as e:
            logger.warning(f"[{ticker}] Failed to read cached artifact '{artifact_type}': {e}")
            return None

    def save(
        self,
        date_str: str,
        ticker: str,
        artifact_type: str,
        data: Any,
    ) -> Path:
        """
        Saves artifact data to JSON with metadata timestamp.
        """
        file_path = self._get_file_path(date_str, ticker, artifact_type)
        payload = {
            "_ticker": ticker.upper(),
            "_date": date_str,
            "_artifact_type": artifact_type,
            "_cached_at": time.time(),
            "_cached_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": data,
        }

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            logger.debug(f"[{ticker}] Cached artifact '{artifact_type}' -> {file_path}")

            # Also mirror directly into data/raw/<date>/<ticker>/ for instant visibility
            clean_date = self._sanitize_name(date_str)
            clean_ticker = self._sanitize_name(ticker).upper()
            clean_artifact = self._sanitize_name(artifact_type)
            raw_dir = config.BASE_DIR / "data" / "raw" / clean_date / clean_ticker
            if raw_dir.exists():
                raw_file = raw_dir / f"{clean_ticker}_{clean_artifact}.json"
                with open(raw_file, "w", encoding="utf-8") as rf:
                    json.dump(payload, rf, indent=2, default=str)
            return file_path
        except Exception as e:
            logger.error(f"[{ticker}] Failed to write artifact '{artifact_type}': {e}")
            return file_path


# Global Singleton Instance
artifact_cache = ArtifactCache()
