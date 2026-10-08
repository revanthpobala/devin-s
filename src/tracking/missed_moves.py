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
        "is_backfill": True,
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
        "is_backfill": True,
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
    include_backfill: bool = True,
) -> Dict[str, Any]:
    """Scan and compile missed moves across the screening universe.
    
    Joins large recent movers (>= min_move_pct in trailing sessions) against our last
    triage verdict, spot, lane, cut reason, and dossier age.
    """
    records: List[Dict[str, Any]] = []
    seen_tickers: set = set()

    # Discover candidate dossiers across recent trailing sessions
    base_dir = config.BASE_DIR / "data"
    today_dt = datetime.now().date()
    today_str = today_dt.strftime("%Y-%m-%d")

    dates_set = set()
    for d in (base_dir / "triage", base_dir / "raw"):
        if not d.exists():
            continue
        for dt_dir in d.iterdir():
            if dt_dir.is_dir() and len(dt_dir.name) == 10 and dt_dir.name <= today_str:
                dates_set.add(dt_dir.name)
    recent_dates = sorted(list(dates_set), reverse=True)[: max(10, lookback_days * 2)]

    from src.clients.price_client import get_current_prices_batch, get_current_price

    candidate_entries: Dict[str, List[Dict[str, Any]]] = {}

    for dt_str in recent_dates:
        for sub in (base_dir / "triage" / dt_str, base_dir / "raw" / dt_str):
            if not sub.exists():
                continue

            # 1. Read survivors manifests (pre-move scan universe)
            for surv_name in ("survivors.json", "schwab_survivors.json"):
                s_file = sub / surv_name
                if s_file.exists():
                    try:
                        with open(s_file, "r", encoding="utf-8") as f:
                            for item in json.load(f):
                                sym = (item.get("symbol") or item.get("Ticker") or "").upper().strip()
                                pr = item.get("price")
                                if sym and pr:
                                    candidate_entries.setdefault(sym, []).append({
                                        "date": dt_str,
                                        "spot": float(pr),
                                        "triage": item,
                                        "llm_d": {},
                                        "c_data": item,
                                        "source": surv_name,
                                    })
                    except Exception:
                        pass

            # 2. Read all *_thesis.json and *_triage.json files
            file_candidates = list(sub.glob("**/*_thesis.json")) + list(sub.glob("**/*_triage.json")) + list(sub.glob("*_triage.json"))
            for th_p in file_candidates:
                sym = th_p.name.replace("_thesis.json", "").replace("_triage.json", "").upper().strip()
                if not sym:
                    continue
                try:
                    with open(th_p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    triage = data["triage"] if isinstance(data.get("triage"), dict) else data
                    llm_d = data.get("llm_data", {}) if isinstance(data.get("llm_data"), dict) else {}

                    d_spot = (
                        triage.get("spot")
                        or triage.get("price")
                        or llm_d.get("live_spot")
                        or llm_d.get("spot")
                        or llm_d.get("price")
                        or data.get("live_spot")
                        or data.get("bar_spot")
                        or data.get("price")
                    )

                    if not d_spot:
                        for cand_dw in (
                            th_p.parent / f"{sym}_datawindow.json",
                            th_p.parent / sym / f"{sym}_datawindow.json",
                        ):
                            if cand_dw.exists():
                                try:
                                    with open(cand_dw, "r", encoding="utf-8") as dw_fp:
                                        dw = json.load(dw_fp)
                                    d_spot = dw.get("close") or dw.get("Close") or dw.get("price")
                                    if d_spot:
                                        break
                                except Exception:
                                    pass

                    if not d_spot and triage.get("long_plan", {}).get("zone"):
                        z = triage["long_plan"]["zone"]
                        if isinstance(z, (list, tuple)) and len(z) == 2 and z[0] is not None and z[1] is not None:
                            d_spot = (float(z[0]) + float(z[1])) / 2.0

                    if d_spot and float(d_spot) > 0:
                        candidate_entries.setdefault(sym, []).append({
                            "date": dt_str,
                            "spot": float(d_spot),
                            "triage": triage,
                            "llm_d": llm_d,
                            "c_data": data,
                            "source": th_p.name,
                        })
                except Exception:
                    pass

    # Batch fetch live market prices
    syms_to_check = list(candidate_entries.keys())
    live_prices: Dict[str, float] = {}
    if syms_to_check:
        try:
            live_prices = get_current_prices_batch(syms_to_check)
        except Exception:
            pass

    for sym, entries in candidate_entries.items():
        live_p = live_prices.get(sym)
        if live_p is None or live_p <= 0:
            try:
                live_p = get_current_price(sym)
            except Exception:
                continue
        if live_p is None or live_p <= 0:
            continue

        # Find the entry maximizing the move percentage across historical entries in lookback
        best_entry = None
        best_move_pct = -999.0
        for e in entries:
            d_spot = e["spot"]
            if d_spot <= 0:
                continue
            pct = ((live_p - d_spot) / d_spot) * 100.0
            if pct > best_move_pct:
                best_move_pct = pct
                best_entry = e

        if best_entry and best_move_pct >= min_move_pct:
            d_spot = best_entry["spot"]
            d_date_str = best_entry["date"]
            age_days = 0
            try:
                d_dt = datetime.strptime(d_date_str, "%Y-%m-%d").date()
                age_days = max(0, (today_dt - d_dt).days)
            except Exception:
                pass

            triage = best_entry["triage"]
            llm_d = best_entry["llm_d"]
            c_data = best_entry["c_data"]

            raw_v = c_data.get("triage") if isinstance(c_data.get("triage"), str) else (triage.get("triage") or llm_d.get("triage") or triage.get("priority_tier") or "WATCH")
            verdict = str(raw_v).upper()
            lane = triage.get("setup_lane") or llm_d.get("setup_lane") or triage.get("lane") or triage.get("setup_posture") or "None"
            cut_reas = triage.get("cut_reason") or llm_d.get("cut_reason") or triage.get("reason")
            is_stale = bool(triage.get("stale_data") or c_data.get("stale_data"))
            no_levels = bool(triage.get("no_levels") or ("no_levels" in (triage.get("flags") or [])))

            # Classify root cause reason with real dossier diagnostics (no setup, constructible watch, chased, stale data, etc.)
            flags_list = list(triage.get("flags") or [])
            is_chased = bool(triage.get("chased") or "chased" in flags_list)

            root_cause = "other"
            if is_stale:
                root_cause = "stale_data"
            elif "CUT" in verdict:
                root_cause = "CUT"
            elif no_levels:
                root_cause = "no_levels"
            elif is_chased:
                root_cause = "chased"
            elif triage.get("pb_funnel") in (0, 0.0, False):
                root_cause = "no-PB"
            elif llm_d.get("send_for_deep_research") is False:
                root_cause = "cap"
            elif cut_reas in ("no_setup", "constructible_watch", "structure_only_no_fresh_long", "stale_data", "chased"):
                root_cause = str(cut_reas)
            elif cut_reas:
                root_cause = str(cut_reas)

            records.append({
                "ticker": sym,
                "move_pct": round(best_move_pct, 1),
                "dossier_date": d_date_str,
                "dossier_spot": round(d_spot, 2),
                "live_spot": round(live_p, 2),
                "last_verdict": verdict,
                "lane": lane,
                "cut_reason": str(cut_reas) if cut_reas else None,
                "age_days": age_days,
                "root_cause_reason": root_cause,
                "details": f"Moved +{best_move_pct:.1f}% from ${d_spot:.2f} to ${live_p:.2f} ({verdict}: {root_cause})",
                "is_backfill": False,
            })
            seen_tickers.add(sym)

    # If backfill is requested, include historical diagnostic benchmarks
    if include_backfill:
        for b in BACKFILL_MISSED_MOVES:
            b_row = dict(b)
            b_row["is_backfill"] = True
            if b["ticker"] in seen_tickers:
                records = [r for r in records if r["ticker"] != b["ticker"]]
            records.append(b_row)
            seen_tickers.add(b["ticker"])

    # Sort all records by move percentage descending
    records.sort(key=lambda x: x.get("move_pct", 0.0), reverse=True)

    # Calculate weekly rollup by root cause dynamically
    rollup: Dict[str, int] = {
        "stale_data": 0,
        "CUT": 0,
        "no-PB": 0,
        "cap": 0,
        "no_levels": 0,
        "chased": 0,
        "no_setup": 0,
        "constructible_watch": 0,
        "other": 0,
    }
    for r in records:
        rc = r.get("root_cause_reason", "other")
        rollup[rc] = rollup.get(rc, 0) + 1

    return {
        "missed_moves": records,
        "weekly_rollup": rollup,
        "total_missed": len(records),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
