"""artifact_loader.py — resolve and load per-ticker disk artifacts for deep research."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Triage record loading (was _load_triage_record at module level)
# ---------------------------------------------------------------------------

def load_triage_record(
    raw_dir: Path,
    deep_dir: Path,
    ticker: str,
    tdir: Optional[Path] = None,
) -> dict:
    """Load the deterministic pre-filter triage record for *ticker*.

    Search order: caller-supplied *tdir*, then *deep_dir*, then *raw_dir*
    sub-folder.  Returns an empty dict when nothing is found.
    """
    safe = ticker.replace(":", "_")
    search_dirs = []
    if tdir:
        search_dirs.append(tdir)
    search_dirs += [deep_dir, raw_dir / safe, raw_dir]

    for d in search_dirs:
        for fname in (f"{safe}_thesis.json", f"{safe}_triage.json"):
            p = d / fname
            if p.exists():
                # Check for stale cache against refreshed datawindow or chart artifacts
                try:
                    p_mtime = p.stat().st_mtime
                    is_stale = False
                    for check_dir in search_dirs:
                        for art_name in (f"{safe}_datawindow.json", f"{safe}_datawindow.csv", f"{safe}_chart.png"):
                            art_file = check_dir / art_name
                            if art_file.exists() and art_file.stat().st_mtime > p_mtime:
                                logger.info(
                                    f"[{ticker}] Stale cached triage at {p.name} (older than {art_file.name}). Recomputing triage."
                                )
                                is_stale = True
                                break
                        if is_stale:
                            break
                    if is_stale:
                        continue

                    data = json.loads(p.read_text(encoding="utf-8"))
                    # _thesis.json wraps the record; _triage.json is flat.
                    rec = data.get("triage") or data.get("llm_data") or data
                    if isinstance(rec, dict) and rec:
                        return rec
                except Exception as e:
                    logger.debug(f"[{ticker}] Could not parse {p}: {e}")
    return {}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_artifact_paths(ticker: str, tdir: Path, raw_dir: Path) -> dict[str, Path]:
    """Return a mapping of artifact name → resolved Path for *ticker*.

    Each path is the first existing candidate; callers must still check
    ``.exists()`` for optional artifacts.
    """
    safe = ticker.replace(":", "_")

    def _first(*candidates: Path) -> Path:
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]  # Return primary (non-existing) as default

    return {
        "dw_json": _first(
            tdir / f"{safe}_datawindow.json",
            raw_dir / safe / f"{safe}_datawindow.json",
            raw_dir / f"{safe}_datawindow.json",
        ),
        "dw_csv": _first(
            tdir / f"{safe}_datawindow.csv",
            raw_dir / safe / f"{safe}_datawindow.csv",
            raw_dir / f"{safe}_datawindow.csv",
        ),
        "dossier": _first(
            tdir / f"{safe}_news_research.md",
            raw_dir / safe / f"{safe}_news_research.md",
            raw_dir / f"{safe}_news_research.md",
        ),
        "chart_plain": _first(
            tdir / f"{safe}_chart_plain.png",
            raw_dir / safe / f"{safe}_chart_plain.png",
            raw_dir / f"{safe}_chart_plain.png",
        ),
        "chart_zoom": _first(
            tdir / f"{safe}_chart_zoom.png",
            raw_dir / safe / f"{safe}_chart_zoom.png",
            raw_dir / f"{safe}_chart_zoom.png",
        ),
        "chart_wide": _first(
            tdir / f"{safe}_chart.png",
            raw_dir / safe / f"{safe}_chart.png",
            raw_dir / f"{safe}_chart.png",
        ),
        "thesis": tdir / f"{safe}_thesis.json",
        "quote": _first(
            tdir / f"{safe}_quote.json",
            raw_dir / f"{safe}_quote.json",
        ),
        "gex": _first(
            tdir / f"{safe}_gex.json",
            raw_dir / f"{safe}_gex.json",
        ),
        "tv_strategies": _first(
            tdir / f"{safe}_tv_strategies.json",
            raw_dir / f"{safe}_tv_strategies.json",
        ),
    }


# ---------------------------------------------------------------------------
# Data window loading + triage computation
# ---------------------------------------------------------------------------

def load_data_window(ticker: str, dw_path: Path) -> dict:
    """Load and return the data window JSON dict, or ``{}`` on failure.
    Injects derived missing Pine fields (RSI2 ATR14, RSI2 RSI2, Long RR At Market,
    Action Long/Short Code, Protocol Version, Prev Ext Z).
    """
    dw_path = Path(dw_path)
    if not dw_path.exists():
        logger.warning(f"[{ticker}] Data window JSON not found at {dw_path}")
        return {}
    try:
        dw_dict = json.loads(dw_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[{ticker}] Error loading data window: {e}")
        return {}

    # Look for sibling or related datawindow CSV for OHLC derivations
    df = None
    safe = ticker.replace(":", "_")
    csv_candidates = [
        dw_path.with_suffix(".csv"),
        dw_path.parent / f"{safe}_datawindow.csv",
        dw_path.parent / f"{ticker}_datawindow.csv",
    ]
    for cand in csv_candidates:
        if cand.exists():
            try:
                import pandas as pd
                df = pd.read_csv(cand)
                break
            except Exception:
                pass

    try:
        from src.data.csv_adapter import derive_datawindow_fields
        dw_dict = derive_datawindow_fields(dw_dict, df)
    except Exception as e:
        logger.debug(f"[{ticker}] derive_datawindow_fields notice: {e}")

    return dw_dict


def resolve_triage(
    ticker: str,
    dw_dict: dict,
    raw_dir: Path,
    deep_dir: Path,
    tdir: Path,
) -> dict:
    """Return the triage/verdict record, computing it from DW when absent.

    Side-effect: writes ``{ticker}_triage.json`` to *tdir* if not present.
    """
    triage_record = load_triage_record(raw_dir, deep_dir, ticker, tdir=tdir)

    verdict_record = triage_record if triage_record.get("triage") else {}

    # Pull flags from thesis.json if the triage record doesn't have them
    safe = ticker.replace(":", "_")
    thesis_file = tdir / f"{safe}_thesis.json"
    if not triage_record.get("flags") and thesis_file.exists():
        try:
            tdata = json.loads(thesis_file.read_text(encoding="utf-8"))
            triage_record = tdata.get("triage") or {}
            if not triage_record.get("flags") and tdata.get("llm_data", {}).get("flags"):
                triage_record["flags"] = tdata["llm_data"]["flags"]
            if not verdict_record and isinstance(tdata.get("triage"), dict):
                verdict_record = tdata["triage"]
        except (json.JSONDecodeError, OSError):
            pass

    # Last-resort: compute deterministically from the data window
    if not verdict_record and dw_dict:
        try:
            from src.logic.data_window_filter import run_data_window_filter
            verdict_record = run_data_window_filter(ticker, dw_dict)
            triage_record = verdict_record
        except Exception as e:
            logger.warning(f"[{ticker}] Fallback data_window_filter failed: {e}")

    # Persist _triage.json so downstream consumers have exact ground truth
    if verdict_record:
        try:
            triage_out = tdir / f"{safe}_triage.json"
            if not triage_out.exists():
                triage_out.write_text(json.dumps(verdict_record, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[{ticker}] Failed to write {safe}_triage.json: {e}")

    return triage_record, verdict_record


# ---------------------------------------------------------------------------
# News dossier loading
# ---------------------------------------------------------------------------

def load_news_dossier(ticker: str, dossier_path: Path, thesis_path: Path) -> str:
    """Return the pre-compiled news research dossier string.

    Falls back to cached Alpaca sentiment from *thesis_path* when the dossier
    file is missing or empty.
    """
    if dossier_path.exists():
        try:
            content = dossier_path.read_text(encoding="utf-8")
            logger.info(f"[{ticker}] Loaded news research dossier ({len(content):,} chars)")
            return content
        except Exception as e:
            logger.warning(f"[{ticker}] Could not read dossier: {e}")

    # Fallback: cached sentiment from thesis
    cached_sentiment = None
    if thesis_path.exists():
        try:
            tdata = json.loads(thesis_path.read_text(encoding="utf-8"))
            cached_sentiment = (
                tdata.get("llm_data", {}).get("news_sentiment")
                or tdata.get("triage", {}).get("sentiment", {}).get("label")
            )
        except Exception:
            pass

    if cached_sentiment:
        logger.info(f"[{ticker}] Using cached Alpaca sentiment '{cached_sentiment}' (no DDGS dossier).")
        return (
            f"No pre-compiled news dossier available. "
            f"Cached Alpaca news sentiment from local pre-filter: {cached_sentiment}."
        )

    logger.warning(f"[{ticker}] News research dossier not found at {dossier_path}.")
    return "No pre-compiled news research dossier available."


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

def _dossier_stale_check(dossier_path: Path, date_str: str) -> bool:
    """Return True if the cached dossier was written on a date other than *date_str*.

    A stale dossier still provides context but the caller should warn the LLM
    to prefer live news tools.
    """
    if not dossier_path.exists():
        return True
    try:
        mtime = datetime.fromtimestamp(dossier_path.stat().st_mtime).date().isoformat()
        return mtime != date_str
    except Exception:
        return True
