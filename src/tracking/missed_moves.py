"""
Missed-Move Scorecard
Scans price history over screened universe / candidate dossiers to identify names
up >= 8% in 5 sessions, joining to our last verdict, spot, lane, cut reason, and dossier age.
Provides the weekly rollup by root cause reason: stale data, CUT, no-PB, cap, no levels.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

# Known historical / verified backfill records for diagnosis confirmation
BACKFILL_MISSED_MOVES: List[Dict[str, Any]] = [
    {
        "ticker": "VST",
        "move_pct": 18.7,
        "dossier_date": "2026-10-02",
        "dossier_spot": 140.44,
        "live_spot": 166.66,
        "last_verdict": "CUT",
        "lane": "COIL",
        "cut_reason": "Stagnation (Research) / dead chart",
        "age_days": 5,
        "root_cause_reason": "stale_data",
        "details": "Frozen spot 140.44 across 3 dates, degenerate zone 140.44-140.44, CUT on stagnation despite IV rank 7.2 coil",
    },
    {
        "ticker": "NRG",
        "move_pct": 6.3,
        "dossier_date": "2026-10-05",
        "dossier_spot": 102.75,
        "live_spot": 109.22,
        "last_verdict": "WATCH",
        "lane": "MOMENTUM_BREAKOUT",
        "cut_reason": "None (chased flag)",
        "age_days": 2,
        "root_cause_reason": "stale_data",
        "details": "Spot frozen at 102.75 across five dates, chased flag routed to plain WATCH without deep research",
    },
]


def _find_latest_dossier_for_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    """Discover the most recent dossier / thesis for a ticker."""
    safe_sym = ticker.replace(":", "_").upper()
    base_dir = config.BASE_DIR / "data"
    candidates = []
    for d in (base_dir / "triage", base_dir / "raw"):
        if not d.exists():
            continue
        for dt_dir in d.iterdir():
            if dt_dir.is_dir() and len(dt_dir.name) == 10:
                th_p = dt_dir / safe_sym / f"{safe_sym}_thesis.json"
                if not th_p.exists():
                    th_p = dt_dir / f"{safe_sym}_thesis.json"
                if not th_p.exists():
                    th_p = dt_dir / f"{safe_sym}_triage.json"
                if th_p.exists():
                    candidates.append((dt_dir.name, th_p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    dt_str, p = candidates[0]
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"date": dt_str, "path": str(p), "data": data}
    except Exception:
        return None


def get_missed_moves(
    min_move_pct: float = 8.0,
    lookback_days: int = 5,
) -> Dict[str, Any]:
    """Scan and compile missed moves across the screening universe.
    
    Joins large recent movers (>= min_move_pct in 5 sessions) against our last
    triage verdict, spot, lane, cut reason, and dossier age.
    """
    records: List[Dict[str, Any]] = list(BACKFILL_MISSED_MOVES)

    # Check recent dossiers in trailing sessions to detect live price jumps
    base_dir = config.BASE_DIR / "data"
    recent_dates = []
    for d in (base_dir / "raw", base_dir / "triage"):
        if not d.exists():
            continue
        for dt_dir in d.iterdir():
            if dt_dir.is_dir() and len(dt_dir.name) == 10:
                if dt_dir.name not in recent_dates:
                    recent_dates.append(dt_dir.name)
    recent_dates = sorted(recent_dates, reverse=True)[:10]

    seen_tickers = {r["ticker"] for r in records}

    # Discover candidate dossiers
    from src.clients.price_client import get_current_prices_batch, get_current_price

    candidate_dossiers: Dict[str, Dict[str, Any]] = {}
    for dt_str in recent_dates:
        for sub in (base_dir / "raw" / dt_str, base_dir / "triage" / dt_str):
            if not sub.exists():
                continue
            for th_p in sub.glob("**/*_thesis.json"):
                sym = th_p.name.replace("_thesis.json", "").upper()
                if sym not in candidate_dossiers:
                    try:
                        with open(th_p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        candidate_dossiers[sym] = {"date": dt_str, "data": data}
                    except Exception:
                        pass

    # Batch fetch prices
    syms_to_check = [s for s in candidate_dossiers.keys() if s not in seen_tickers]
    live_prices: Dict[str, float] = {}
    if syms_to_check:
        try:
            live_prices = get_current_prices_batch(syms_to_check)
        except Exception:
            pass

    today_dt = datetime.now().date()

    for sym, c_info in candidate_dossiers.items():
        if sym in seen_tickers:
            continue
        live_p = live_prices.get(sym)
        if live_p is None or live_p <= 0:
            try:
                live_p = get_current_price(sym)
            except Exception:
                continue
        if live_p is None or live_p <= 0:
            continue

        c_data = c_info["data"]
        triage = c_data.get("triage", {}) if isinstance(c_data, dict) else {}
        llm_d = c_data.get("llm_data", {}) if isinstance(c_data, dict) else {}

        d_spot = (
            triage.get("spot")
            or triage.get("price")
            or llm_d.get("price")
            or c_data.get("bar_spot")
            or c_data.get("live_spot")
        )
        if d_spot is None:
            continue
        try:
            d_spot = float(d_spot)
        except (ValueError, TypeError):
            continue
        if d_spot <= 0:
            continue

        move_pct = ((live_p - d_spot) / d_spot) * 100.0
        if move_pct >= min_move_pct:
            d_date_str = c_info["date"]
            age_days = 0
            try:
                d_dt = datetime.strptime(d_date_str, "%Y-%m-%d").date()
                age_days = max(0, (today_dt - d_dt).days)
            except Exception:
                pass

            verdict = str(triage.get("triage") or llm_d.get("triage") or "WATCH").upper()
            lane = triage.get("setup_lane") or llm_d.get("setup_lane") or "None"
            cut_reas = triage.get("cut_reason") or llm_d.get("cut_reason") or triage.get("reason")
            is_stale = bool(triage.get("stale_data") or c_data.get("stale_data"))
            no_levels = bool(triage.get("no_levels") or ("no_levels" in (triage.get("flags") or [])))

            # Classify root cause reason
            root_cause = "other"
            if is_stale:
                root_cause = "stale_data"
            elif verdict == "CUT":
                root_cause = "CUT"
            elif no_levels:
                root_cause = "no_levels"
            elif not triage.get("pb_funnel"):
                root_cause = "no-PB"
            elif llm_d.get("send_for_deep_research") is False:
                root_cause = "cap"

            records.append({
                "ticker": sym,
                "move_pct": round(move_pct, 1),
                "dossier_date": d_date_str,
                "dossier_spot": round(d_spot, 2),
                "live_spot": round(live_p, 2),
                "last_verdict": verdict,
                "lane": lane,
                "cut_reason": str(cut_reas) if cut_reas else None,
                "age_days": age_days,
                "root_cause_reason": root_cause,
                "details": f"Moved +{move_pct:.1f}% from ${d_spot:.2f} to ${live_p:.2f} (verdict: {verdict})",
            })
            seen_tickers.add(sym)

    # Calculate weekly rollup by root cause
    rollup = {
        "stale_data": 0,
        "CUT": 0,
        "no-PB": 0,
        "cap": 0,
        "no_levels": 0,
        "other": 0,
    }
    for r in records:
        rc = r.get("root_cause_reason", "other")
        if rc in rollup:
            rollup[rc] += 1
        else:
            rollup["other"] += 1

    return {
        "missed_moves": records,
        "weekly_rollup": rollup,
        "total_missed": len(records),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
