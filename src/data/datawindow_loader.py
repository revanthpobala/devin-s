"""
src/data/datawindow_loader.py

Robust, unified Data Window discovery and loading across all directories:
- data/triage/<date>/_DEEP_RESEARCH/<ticker>/
- data/triage/<date>/force/<ticker>/
- data/triage/<date>/<ticker>/
- data/raw/<date>/<ticker>/
- data/artifacts/<date>/<ticker>/

Supports both JSON (*_datawindow.json) and CSV (*_datawindow.csv / *_data_window.csv) formats.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import config

logger = logging.getLogger(__name__)


def find_datawindow_paths(ticker: str, target_date: Optional[str] = None) -> List[Path]:
    """Find all candidate Data Window paths for a ticker, prioritized from newest/most specific to oldest."""
    ticker_u = ticker.upper()
    candidates: List[Path] = []
    seen: set = set()

    def _add(p: Path):
        p_resolved = p.resolve() if p.exists() else p
        p_str = str(p_resolved)
        if p_str not in seen:
            seen.add(p_str)
            candidates.append(p)

    # 1. If target_date is given, search that specific date first
    if target_date and re.match(r"^\d{4}-\d{2}-\d{2}$", target_date.strip()):
        d_str = target_date.strip()
        specific_dirs = [
            config.BASE_DIR / "data" / "triage" / d_str / "_DEEP_RESEARCH" / ticker_u,
            config.BASE_DIR / "data" / "triage" / d_str / "force" / ticker_u,
            config.BASE_DIR / "data" / "triage" / d_str / ticker_u,
            config.BASE_DIR / "data" / "raw" / d_str / ticker_u,
            config.BASE_DIR / "data" / "artifacts" / d_str / ticker_u,
            config.BASE_DIR / "data" / "raw" / d_str,
        ]
        for s_dir in specific_dirs:
            _add(s_dir / f"{ticker_u}_datawindow.json")
            _add(s_dir / f"{ticker_u}_datawindow.csv")
            _add(s_dir / f"{ticker_u}_data_window.csv")

        # Glob in date subdirectories
        triage_date_dir = config.BASE_DIR / "data" / "triage" / d_str
        if triage_date_dir.exists():
            for p in triage_date_dir.glob(f"**/{ticker_u}*data*window*.json"):
                _add(p)
            for p in triage_date_dir.glob(f"**/{ticker_u}*data*window*.csv"):
                _add(p)

        raw_date_dir = config.BASE_DIR / "data" / "raw" / d_str
        if raw_date_dir.exists():
            for p in raw_date_dir.glob(f"**/{ticker_u}*data*window*.json"):
                _add(p)
            for p in raw_date_dir.glob(f"**/{ticker_u}*data*window*.csv"):
                _add(p)

    # 2. Search all recent dates (newest first)
    for root_name in ["triage", "raw", "artifacts"]:
        root_dir = config.BASE_DIR / "data" / root_name
        if not root_dir.exists():
            continue
        try:
            date_dirs = [d for d in root_dir.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)]
            date_dirs.sort(key=lambda d: d.name, reverse=True)
            for d in date_dirs:
                if target_date and d.name == target_date:
                    continue  # already checked above
                # Deep research & force subdirs
                _add(d / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_datawindow.json")
                _add(d / "_DEEP_RESEARCH" / ticker_u / f"{ticker_u}_datawindow.csv")
                _add(d / "force" / ticker_u / f"{ticker_u}_datawindow.json")
                _add(d / "force" / ticker_u / f"{ticker_u}_datawindow.csv")
                _add(d / ticker_u / f"{ticker_u}_datawindow.json")
                _add(d / ticker_u / f"{ticker_u}_datawindow.csv")
                _add(d / f"{ticker_u}_datawindow.json")
                _add(d / f"{ticker_u}_datawindow.csv")
        except Exception as e:
            logger.debug(f"Error scanning {root_dir} for {ticker_u}: {e}")

    return candidates


def load_datawindow(ticker: str, target_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Find and load the authoritative Data Window dictionary for a ticker.
    Searches JSON and CSV across triage (_DEEP_RESEARCH, force), raw, and artifacts.
    Enriches with computed live/market R:R if stop and target are present.
    """
    candidates = find_datawindow_paths(ticker, target_date)
    dw_dict = None

    for p in candidates:
        if not p.exists():
            continue
        suffix = p.suffix.lower()
        if suffix == ".json":
            try:
                raw_json = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw_json, dict) and len(raw_json) > 0:
                    dw_dict = raw_json
                    break
            except Exception as e:
                logger.debug(f"Failed parsing Data Window JSON {p}: {e}")
        elif suffix == ".csv":
            try:
                from src.data.csv_adapter import csv_to_datawindow
                snapshot, _, _, _ = csv_to_datawindow(str(p), ticker=ticker)
                if isinstance(snapshot, dict) and len(snapshot) > 0:
                    dw_dict = snapshot
                    break
            except Exception as e:
                logger.debug(f"Failed parsing Data Window CSV {p}: {e}")

    if not dw_dict:
        return None

    # Normalization & field derivation
    dw_dict["ticker"] = ticker.upper()

    # Mathematical R:R derivation if missing
    if dw_dict.get("long_rr_at_market") is None and dw_dict.get("Long RR At Market") is None:
        try:
            px_val = None
            for k in ["close", "Close", "price", "last", "bar_close"]:
                if k in dw_dict and dw_dict[k] is not None:
                    px_val = float(dw_dict[k])
                    break
            stop_val = None
            for k in ["Long Stop Loss", "long_stop_loss", "tactical_stop", "stop"]:
                if k in dw_dict and dw_dict[k] is not None:
                    stop_val = float(dw_dict[k])
                    break
            target_val = None
            for k in ["Long Target", "long_target", "target_1", "target"]:
                if k in dw_dict and dw_dict[k] is not None:
                    target_val = float(dw_dict[k])
                    break

            if px_val is not None and stop_val is not None and target_val is not None:
                if px_val > stop_val and target_val > px_val:
                    calc_rr = round((target_val - px_val) / (px_val - stop_val), 2)
                    dw_dict["long_rr_at_market"] = calc_rr
                    dw_dict["Long RR At Market"] = calc_rr
        except Exception:
            pass

    return dw_dict
