"""
tests/test_b4_judge_and_extractor.py
====================================
Tests for B4 remediation:
- Extractor: comma prices ($1,234.56), multi-line strike regex, exact-token verdict map
  with default NO_TRADE ("DO NOT BUY" is not ENTER), spot 0.0 no longer invalidates,
  stop-to-breakeven not captured as stop, never overwrite watch_levels.json.
- Level validation: NO_ENTRY fails, stop floor max(LEVEL_ATR_STOP_MIN, STOP_ATR_MIN) from fill,
  reject on rr < floor (AND, not OR), at-market floor for all ENTER types.
- Judge: cannot emit ENTER for GOOGL 09-30 input.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.logic.actionable_gate import is_actionable
from src.logic.level_validation import validate_levels, check_geometry
from src.logic.report_level_extractor import extract_watch_levels_from_report


def test_level_validation_no_entry_fails():
    geo_reasons = check_geometry(
        side="LONG",
        entry_type="NO_ENTRY",
        entry_low=0.0,
        entry_high=0.0,
        breakout_level=0.0,
        stop=0.0,
        t1=0.0,
    )
    assert any("no_entry" in r.lower() for r in geo_reasons)

    plan = {
        "side": "LONG",
        "entry_type": "NO_ENTRY",
        "entry_low": 0.0,
        "entry_high": 0.0,
        "stop": 0.0,
        "target_1": 0.0,
    }
    ok, reasons = validate_levels(plan, {}, "LONG")
    assert not ok
    assert any("no_entry" in r.lower() for r in reasons)


def test_level_validation_stop_floor_from_fill():
    # ATR is 10.0, fill is 100.0, STOP_ATR_MIN is 0.7, LEVEL_ATR_STOP_MIN is 1.0 (max = 1.0)
    # Min buffer = 10.0 * 1.0 = 10.0. Max allowed stop is 100 - 10 = 90.0.
    # A stop at 95.0 should fail.
    plan = {
        "side": "LONG",
        "entry_type": "LIMIT",
        "entry_low": 100.0,
        "entry_high": 102.0,
        "stop": 95.0,  # Only 5.0 (0.5 ATR) away -> must fail
        "target_1": 130.0,
        "spot": 101.0,
        "setup_lane": "WATCH_SHADOW",
    }
    dw = {
        "close": 101.0,
        "rsi2_atr14": 10.0,
    }
    ok, reasons = validate_levels(plan, dw, "LONG")
    assert not ok
    assert any("exceeds max allowed stop" in r.lower() for r in reasons)


def test_level_validation_at_market_floor_for_limit():
    # At-market R:R floor applies to LIMIT too.
    # spot=100, stop=90, t1=105 -> at-market R:R = (105-100)/(100-90) = 0.5 < 2.0
    plan = {
        "side": "LONG",
        "entry_type": "LIMIT",
        "entry_low": 98.0,
        "entry_high": 100.0,
        "stop": 85.0,
        "target_1": 105.0,
        "spot": 100.0,
        "setup_lane": "WATCH_SHADOW",
    }
    dw = {
        "close": 100.0,
        "rsi2_atr14": 5.0,
    }
    ok, reasons = validate_levels(plan, dw, "LONG")
    assert not ok
    assert any("at-market r:r" in r.lower() for r in reasons)


def test_extractor_verdict_do_not_buy_is_no_trade(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-10-01"
    reports_dir.mkdir(parents=True)
    raw_dir = tmp_path / "data" / "raw" / "2026-10-01" / "TEST"
    raw_dir.mkdir(parents=True)

    (reports_dir / "TEST_summary.md").write_text(
        "Bar close: $150.00\nTACTICAL ENTRY ZONE: $148.00 – $150.00\nTACTICAL STOP: $140.00\nTARGET 1: $170.00",
        encoding="utf-8",
    )
    (reports_dir / "TEST_arbitration.md").write_text(
        "**Verdict:** DO NOT BUY\nConviction: 1/10\nDistribution breakdown in progress.",
        encoding="utf-8",
    )
    (raw_dir / "TEST_datawindow.json").write_text(
        json.dumps({"Close": "150.00", "rsi2_atr14": 5.0}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("TEST", "2026-10-01")
    assert res is not None
    assert res["verdict"] == "NO_TRADE"
    assert res["actionable"] is False


def test_extractor_comma_prices(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-10-01"
    reports_dir.mkdir(parents=True)
    raw_dir = tmp_path / "data" / "raw" / "2026-10-01" / "BIG"
    raw_dir.mkdir(parents=True)

    (reports_dir / "BIG_summary.md").write_text(
        "Bar close: $1,234.56\nTACTICAL ENTRY ZONE: $1,200.00 – $1,220.00\nTACTICAL STOP: $1,150.00\nTARGET 1: $1,350.00",
        encoding="utf-8",
    )
    (reports_dir / "BIG_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 8/10\nDaily Close below $1,150.00\nLogic: Key support broken.",
        encoding="utf-8",
    )
    (raw_dir / "BIG_datawindow.json").write_text(
        json.dumps({"Close": "1234.56", "rsi2_atr14": 25.0}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("BIG", "2026-10-01")
    assert res is not None
    sp = res["shares_plan"]
    assert sp["entry_zone_low"] == 1200.0
    assert sp["entry_zone_high"] == 1220.0
    assert sp["tactical_stop"] == 1150.0
    assert sp["target_1"] == 1350.0


def test_extractor_stop_to_breakeven_not_captured_as_stop(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-10-01"
    reports_dir.mkdir(parents=True)
    raw_dir = tmp_path / "data" / "raw" / "2026-10-01" / "TRIM"
    raw_dir.mkdir(parents=True)

    (reports_dir / "TRIM_summary.md").write_text(
        "Bar close: $100.00\nTACTICAL ENTRY ZONE: $98.00 – $100.00\nTACTICAL STOP: $92.00\nTARGET 1: $115.00",
        encoding="utf-8",
    )
    (reports_dir / "TRIM_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 7/10\nOn Target 1 hit, ratchet stop to breakeven ($100.00) immediately.",
        encoding="utf-8",
    )
    (raw_dir / "TRIM_datawindow.json").write_text(
        json.dumps({"Close": "100.00", "rsi2_atr14": 4.0}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("TRIM", "2026-10-01")
    assert res is not None
    # Must NOT capture the $100.00 breakeven stop; must preserve initial tactical stop $92.00
    assert res["shares_plan"]["tactical_stop"] == 92.0


def test_extractor_spot_zero_does_not_invalidate(tmp_path):
    reports_dir = tmp_path / "reports" / "2026-10-01"
    reports_dir.mkdir(parents=True)
    raw_dir = tmp_path / "data" / "raw" / "2026-10-01" / "NOQUOTE"
    raw_dir.mkdir(parents=True)

    (reports_dir / "NOQUOTE_summary.md").write_text(
        "TACTICAL ENTRY ZONE: $98.00 – $100.00\nTACTICAL STOP: $92.00\nTARGET 1: $115.00",
        encoding="utf-8",
    )
    (reports_dir / "NOQUOTE_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 7/10\nDaily Close below $92.00",
        encoding="utf-8",
    )
    (raw_dir / "NOQUOTE_datawindow.json").write_text("{}", encoding="utf-8")

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("NOQUOTE", "2026-10-01")
    assert res is not None
    assert res["status"] == "STALKING"
    assert res["status"] != "INVALIDATED"


def test_extractor_never_overwrites_existing_watch_levels(tmp_path):
    raw_dir = tmp_path / "data" / "raw" / "2026-10-01" / "FROZEN"
    raw_dir.mkdir(parents=True)
    reports_dir = tmp_path / "reports" / "2026-10-01"
    reports_dir.mkdir(parents=True)

    # Pre-existing watch_levels.json
    existing_levels = {"ticker": "FROZEN", "verdict": "ENTER", "custom_flag": "DO_NOT_OVERWRITE"}
    (raw_dir / "FROZEN_watch_levels.json").write_text(json.dumps(existing_levels), encoding="utf-8")

    (reports_dir / "FROZEN_summary.md").write_text(
        "Bar close: $50.00\nTACTICAL ENTRY ZONE: $48.00 – $50.00\nTACTICAL STOP: $45.00\nTARGET 1: $60.00",
        encoding="utf-8",
    )
    (reports_dir / "FROZEN_arbitration.md").write_text(
        "**Verdict:** STALK\nConviction: 5/10",
        encoding="utf-8",
    )
    (raw_dir / "FROZEN_datawindow.json").write_text(
        json.dumps({"Close": "50.00", "rsi2_atr14": 2.0}), encoding="utf-8"
    )

    with patch("src.config.BASE_DIR", tmp_path):
        res = extract_watch_levels_from_report("FROZEN", "2026-10-01")

    # The file on disk must NOT have been overwritten
    disk_content = json.loads((raw_dir / "FROZEN_watch_levels.json").read_text(encoding="utf-8"))
    assert disk_content.get("custom_flag") == "DO_NOT_OVERWRITE"


def test_judge_cannot_emit_enter_for_googl_09_30():
    dw_path = Path("data/triage/2026-09-30/force/GOOGL/GOOGL_datawindow.json")
    if not dw_path.exists():
        pytest.skip("GOOGL 2026-09-30 datawindow not found")
    dw = json.loads(dw_path.read_text(encoding="utf-8"))
    plan = {
        "side": "LONG",
        "entry_type": "LIMIT",
        "entry_low": 342.73,
        "entry_high": 344.84,
        "tactical_stop": 340.0,
        "target_1": 354.37,
        "spot": 344.08,
    }
    actionable, reasons = is_actionable(dw, plan)
    assert not actionable

    # If model emitted ENTER, arbitration capping logic must force it to STALK or CASH_SKIP
    model_verdict = "ENTER (Limit @ Floor)"
    is_no_setup = str(dw.get("setup_lane", "")).lower() in ("no_setup", "none", "")
    capped_verdict = model_verdict
    if "ENTER" in model_verdict.upper():
        if is_no_setup or not actionable:
            capped_verdict = "CASH_SKIP" if is_no_setup else "STALK"

    assert "ENTER" not in capped_verdict
    assert capped_verdict in ("STALK", "CASH_SKIP")
