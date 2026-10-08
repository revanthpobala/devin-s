import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from src.logic.data_window_filter import rank_pass_tickers, deep_research_sort_key


def test_rank_pass_tickers_priority_order():
    """Verify that rank_pass_tickers sorts by closest_to_gate, mover_score, pb_rank, etc."""
    rec_a = {
        "ticker": "AAPL",
        "triage": "PASS",
        "closest_to_gate": 1,
        "mover_score": 10.0,
        "pullback_rank": 1.0,
        "rr": 2.5,
        "ev_r": 1.5,
    }
    rec_b = {
        "ticker": "MSFT",
        "triage": "PASS",
        "closest_to_gate": 0,
        "mover_score": 5.0,
        "pullback_rank": 0.5,
        "rr": 2.0,
        "ev_r": 0.8,
    }
    rec_c = {
        "ticker": "NVDA",
        "triage": "PASS",
        "closest_to_gate": 1,
        "mover_score": 25.0,  # Higher mover score than AAPL
        "pullback_rank": 1.0,
        "rr": 3.0,
        "ev_r": 2.0,
    }

    # NVDA should rank #1, AAPL #2, MSFT #3
    ranked = rank_pass_tickers([rec_b, rec_a, rec_c])
    tickers = [r["ticker"] for r in ranked]
    assert tickers == ["NVDA", "AAPL", "MSFT"]


def test_batch_discovery_preserves_rank_order(tmp_path):
    """Verify that batch discovery orders chart files strictly by rank, not glob order."""
    triage_dir = tmp_path / "triage"
    deep_dir = triage_dir / "_DEEP_RESEARCH"
    deep_dir.mkdir(parents=True)

    tickers = ["BBB", "AAA", "CCC", "DDD", "EEE"]
    # Setup mock records where CCC is highest rank, then AAA, then BBB, then DDD, then EEE
    records = {
        "BBB": {"ticker": "BBB", "in_zone": False, "mover_score": 2.0, "triage": "PASS"},
        "AAA": {"ticker": "AAA", "in_zone": True, "mover_score": 15.0, "triage": "PASS"},
        "CCC": {"ticker": "CCC", "in_zone": True, "mover_score": 30.0, "triage": "PASS"},
        "DDD": {"ticker": "DDD", "in_zone": False, "mover_score": 1.0, "triage": "PASS"},
        "EEE": {"ticker": "EEE", "in_zone": False, "mover_score": 0.5, "triage": "PASS"},
    }

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)

    import time
    for sym, rec in records.items():
        sym_dir = deep_dir / sym
        sym_dir.mkdir(parents=True)
        (sym_dir / f"{sym}_chart.png").write_bytes(b"mock_png")
        (sym_dir / f"{sym}_thesis.json").write_text(json.dumps({"triage": rec}), encoding="utf-8")

    # Simulate pipeline candidate discovery block
    flagged_map = {}
    flagged = []
    for sub in ("_DEEP_RESEARCH", "force"):
        sd = triage_dir / sub
        if not sd.exists():
            continue
        import glob
        for thesis_file in glob.glob(str(sd / "**" / "*_thesis.json"), recursive=True):
            t = Path(thesis_file).name.replace("_thesis.json", "").upper()
            if t not in flagged_map:
                from src.logic.deep_research.artifact_loader import load_triage_record
                rec = load_triage_record(raw_dir, deep_dir, t, tdir=Path(thesis_file).parent)
                if isinstance(rec, dict):
                    if not rec.get("ticker"):
                        rec["ticker"] = t
                else:
                    rec = {"ticker": t}
                flagged_map[t] = rec
                flagged.append((t, rec))

    ranked_recs = rank_pass_tickers([r for _, r in flagged if r])
    ranked_tickers = []
    for r in ranked_recs:
        sym = str(r.get("ticker", "")).upper()
        if sym and sym not in ranked_tickers:
            ranked_tickers.append(sym)
    for t, _ in flagged:
        if t.upper() not in ranked_tickers:
            ranked_tickers.append(t.upper())

    cap = 0
    kept_tickers = ranked_tickers if cap <= 0 else ranked_tickers[:cap]
    assert kept_tickers == ["CCC", "AAA", "BBB", "DDD", "EEE"]

    chart_files = []
    for t in kept_tickers:
        tdir = deep_dir / t
        matches = glob.glob(str(tdir / f"{t}_*.png"))
        if matches:
            chart_files.append(matches[0])

    chart_tickers = [Path(cf).name.split("_")[0].upper() for cf in chart_files]
    assert chart_tickers == ["CCC", "AAA", "BBB", "DDD", "EEE"]

    # Test full vs mid tier distribution
    full_runs_cap = 3
    full_runs_count = 0
    assigned_tiers = {}
    for chart_path in chart_files:
        ticker = Path(chart_path).name.split("_")[0].upper()
        if full_runs_count < full_runs_cap:
            assigned_tiers[ticker] = "FULL"
            full_runs_count += 1
        else:
            assigned_tiers[ticker] = "MID"

    assert assigned_tiers["CCC"] == "FULL"
    assert assigned_tiers["AAA"] == "FULL"
    assert assigned_tiers["BBB"] == "FULL"
    assert assigned_tiers["DDD"] == "MID"
    assert assigned_tiers["EEE"] == "MID"

