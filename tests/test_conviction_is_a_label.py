"""
tests/test_conviction_is_a_label.py

D1 from plan/no-lost-movers.md: cards, alerts and the copilot show
gate result -> R:R at market -> lane -> MEASURED/UNMEASURED, with conviction only a
trailing label. Nothing ranks or alerts on conviction.
"""

from __future__ import annotations

from unittest.mock import patch


def _seed_two_rows(watch_manager):
    """A high-conviction CUT beside a low-conviction gate-passed STALK."""
    base = {
        "verdict": "STALK",
        "actionable": True,
        "side": "LONG",
        "shares_plan": {
            "entry_type": "LIMIT",
            "side": "LONG",
            "entry_zone_low": 100.0,
            "entry_zone_high": 105.0,
            "tactical_stop": 95.0,
            "target_1": 120.0,
            "target_2": 130.0,
        },
        "options_plan": {"structure": "NONE", "summary": "None"},
        "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 95.0, "rationale": "Floor"},
        "status": "STALKING",
    }
    hi = dict(base, ticker="HICONV", date="2026-10-05", conviction=9)
    lo = dict(base, ticker="LOCONV", date="2026-10-09", conviction=3)
    watch_manager.upsert_watch_target(hi)
    watch_manager.upsert_watch_target(lo)
    return watch_manager.get_active_watch_targets()


def test_active_watch_targets_do_not_rank_on_conviction(tmp_path, monkeypatch):
    import src.tracking.watch_manager as wm

    monkeypatch.setattr(wm, "DB_PATH", tmp_path / "w.db")
    wm.init_watch_db()
    rows = _seed_two_rows(wm)

    # The newer, lower-conviction row leads: ordering is date/status/distance, and
    # conviction is not a ranking key anywhere.
    order = [r["ticker"] for r in rows]
    assert order.index("LOCONV") < order.index("HICONV")

    src = open(wm.__file__, encoding="utf-8").read()
    start = src.index("def get_active_watch_targets")
    end = src.index("def get_all_watch_targets")
    block = src[start:end]
    assert "conviction" not in block, "conviction is back in the watch ordering"


def test_copilot_card_puts_gate_and_lane_before_conviction(tmp_path, monkeypatch):
    import json as _json
    from src.ui.services import copilot_context as cc

    wl = {
        "verdict": "STALK",
        "setup_lane": "RR_SETUP",
        "conviction": 6,
        "shares_plan": {"entry_zone_low": 100.0, "entry_zone_high": 105.0,
                        "tactical_stop": 95.0, "target_1": 120.0, "target_2": 130.0},
        "invalidation_condition": "DAILY_CLOSE_BELOW",
    }
    src_text = (
        "## ⚖️ THE JUDGE'S FINAL RULING\nTake the zone.\n\n"
        "```json:watch_levels\n" + _json.dumps(wl) + "\n```"
    )

    reports = tmp_path / "reports" / "2026-10-09"
    reports.mkdir(parents=True)
    # The dossier card is rendered from the arbitration report's embedded watch levels.
    (reports / "GATE1_arbitration.md").write_text(src_text, encoding="utf-8")

    monkeypatch.setattr(cc.config, "BASE_DIR", tmp_path)
    block, _syms = cc._build_daily_overview_context("2026-10-09", "")

    card = [ln for ln in block.splitlines() if "GATE1" in ln and "Verdict" in ln][0]
    # Gate/lane/MEASURED lead the header; conviction is not in the header.
    assert "Lane: RR_SETUP [MEASURED]" in card
    assert "Conviction" not in card
    # Conviction trails the whole card as a label.
    conv_lines = [ln for ln in block.splitlines() if "Conviction:" in ln]
    assert len(conv_lines) == 1
    assert "6/10" in conv_lines[0]
    assert "never a ranking key" in conv_lines[0]


def test_copilot_prompt_orders_gate_before_conviction():
    # The desk directive string must rank by gate/in-zone/R:R/distance and never
    # present conviction as a ranking key.
    src = open("src/ui/services/copilot_context.py", encoding="utf-8").read()
    assert "rank or summarise the exact rows listed in the telemetry — by GATE RESULT first" in src
    assert "Conviction is a trailing label, never a ranking key" in src
    assert "by conviction / in-zone status" not in src
