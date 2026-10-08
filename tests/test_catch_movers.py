"""
tests/test_catch_movers.py

Comprehensive verification of Catch Movers Plan:
1. Stale-spot and bar-age detection (stale_data tag, prevents CUT)
2. Degenerate zone rejection (entry_low == entry_high == spot -> no_levels, levels None)
3. Coil detector (VST-type low IV / compression routes to COIL lane, not CUT)
4. Mover trigger fires on live move (VST fixture 140.44 -> 166.66)
5. Chased flag routes to MOMENTUM_BREAKOUT lane instead of silent CUT/drop
6. Missed-moves scorecard and weekly rollup
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.logic.data_window_filter import triage_ticker
from src.logic.level_validation import check_geometry
from src.logic.process_survivor import (
    _check_data_staleness_and_live_quote,
    _is_bar_older_than_last_daily_bar,
    _sanitize_degenerate_levels,
    _sanitize_llm_cut_verdict,
)
from src.tracking.forward_log import get_forward_summary, UNMEASURED_RULES
from src.tracking.missed_moves import get_missed_moves


def test_bar_older_than_last_daily_bar():
    # Wednesday 2026-10-07 vs Tuesday 2026-10-06 (not older than last completed)
    assert not _is_bar_older_than_last_daily_bar("2026-10-06", "2026-10-07")
    # Wednesday 2026-10-07 vs Wednesday 2026-10-07 (same day)
    assert not _is_bar_older_than_last_daily_bar("2026-10-07", "2026-10-07")
    # Wednesday 2026-10-07 vs Monday 2026-10-05 (strictly older than Tuesday)
    assert _is_bar_older_than_last_daily_bar("2026-10-05", "2026-10-07")
    # Monday 2026-10-05 vs Friday 2026-10-02 (Friday was last completed daily bar)
    assert not _is_bar_older_than_last_daily_bar("2026-10-02", "2026-10-05")
    # Monday 2026-10-05 vs Thursday 2026-10-01 (older than Friday)
    assert _is_bar_older_than_last_daily_bar("2026-10-01", "2026-10-05")


def test_stale_data_detection_and_no_cut():
    # If bar is older than last completed daily bar, stamp stale_data = True
    dw = {
        "dw_bar_date": "2026-08-05",
        "close": 140.44,
    }
    with patch("src.logic.process_survivor._find_prior_dossier_spot", return_value=None):
        with patch("src.clients.price_client.get_current_price", return_value=166.66):
            info = _check_data_staleness_and_live_quote("VST", dw, "2026-10-07")
            assert info["stale_data"] is True
            assert info["spot_age"] > 30
            assert info["live_spot"] == 166.66
            assert info["move_since_bar_pct"] == pytest.approx(18.67, 0.05)


def test_spot_equals_prior_dossier_spot_stale():
    # If spot equals prior dossier spot across sessions, mark stale
    dw = {
        "dw_bar_date": "2026-10-06",
        "close": 140.44,
    }
    with patch("src.logic.process_survivor._find_prior_dossier_spot", return_value=140.44):
        with patch("src.clients.price_client.get_current_price", return_value=166.66):
            info = _check_data_staleness_and_live_quote("VST", dw, "2026-10-07")
            assert info["stale_data"] is True
            assert any("equals_prior_spot" in r for r in info["stale_reasons"])


def test_degenerate_zone_rejection():
    # When entry_low == entry_high == spot (the VST bug: 140.44 to 140.44 at spot 140.44)
    plan = {
        "zone": [140.44, 140.44],
        "entry_low": 140.44,
        "entry_high": 140.44,
        "stop": 135.0,
        "target": 155.0,
    }
    sanitized, is_degen = _sanitize_degenerate_levels(plan, 140.44)
    assert is_degen is True
    assert sanitized["zone"] == [None, None]
    assert sanitized["entry_low"] is None
    assert sanitized["entry_high"] is None
    assert sanitized["stop"] is None
    assert sanitized["target"] is None
    assert sanitized["no_levels"] is True

    # Geometry check rejection
    geom_reasons = check_geometry(
        side="LONG",
        entry_type="LIMIT",
        entry_low=140.44,
        entry_high=140.44,
        breakout_level=0.0,
        stop=135.0,
        t1=155.0,
        spot=140.44,
    )
    assert any("degenerate zone" in r for r in geom_reasons)


def test_coil_detector_lane_and_forward_logging():
    # Coiled base with low IV rank and squeeze routes to COIL lane (WATCH status)
    dw = {
        "price": 140.44,
        "ma20": 138.0,
        "ma50": 135.0,
        "ma200": 130.0,
        "weinstein": 132.0,
        "buy": 60.0,
        "sell": 40.0,
        "stage": 1,
        "dir_prob": 55.0,
        "regime": 0,
        "ext_pct": 8.0,
        "exhaustion": 0.05,
        "rev_l": 0.0,
        "rev_s": 0.0,
        "long_zbot": 138.0,
        "long_ztop": 142.0,
        "long_stop_loss": 135.0,
        "long_target": 160.0,
        "energy_ivrank": 7.2,
        "_is_active_bb_kc_squeeze": True,
    }
    res = triage_ticker("VST", dw, fetch_news=False)
    assert res["setup_lane"] == "COIL"
    assert res["triage"] == "WATCH"
    assert res["lane_label"] == "UNMEASURED"
    assert "coil" in res["reason"]

    # Check forward logging registry has COIL
    summary = get_forward_summary()
    assert "COIL" in summary["rules"]
    assert summary["rules"]["COIL"]["status"] == "UNMEASURED"


def test_llm_cut_overruled_for_stagnation():
    # Local LLM outputs CUT for "dead chart / no catalyst" on non-toxic setup
    triage_mock = {
        "triage": "WATCH",
        "action": 0,
        "reason": "stagnation",
        "stage": 1,
    }
    verdict, reason = _sanitize_llm_cut_verdict(
        llm_verdict="CUT",
        triage_dict=triage_mock,
        flags=["stagnation", "dead_chart"],
        earnings_gate="PASS",
        iv_rank=10.0,
        squeeze_on=True,
    )
    assert verdict == "WATCH"
    assert reason == "coil_compression_not_cut"


def test_llm_cut_allowed_for_genuine_toxic_geometry():
    # Genuine toxic geometry (action code 18) allows CUT
    triage_mock = {
        "triage": "CUT",
        "action": 18,
        "reason": "toxic_risk_geometry",
        "stage": 1,
    }
    verdict, reason = _sanitize_llm_cut_verdict(
        llm_verdict="CUT",
        triage_dict=triage_mock,
        flags=["toxic_geometry"],
        earnings_gate="PASS",
    )
    assert verdict == "CUT"
    assert reason == "toxic_risk_geometry"


def test_chased_routes_to_momentum_breakout():
    # Stage 2 name above zone with chased flag routes to MOMENTUM_BREAKOUT, not CUT
    dw = {
        "price": 701.0,
        "ma20": 680.0,
        "ma50": 650.0,
        "ma200": 600.0,
        "weinstein": 610.0,
        "buy": 85.0,
        "sell": 15.0,
        "stage": 2,
        "dir_prob": 70.0,
        "regime": 0,
        "ext_pct": 16.8,
        "exhaustion": 0.1,
        "rev_l": 0.0,
        "rev_s": 0.0,
        "long_zbot": 666.0,
        "long_ztop": 672.0,
        "long_stop_loss": 650.0,
        "long_target": 750.0,
        "ext_z": 1.2,
        "action_long": 21.0,
    }
    res = triage_ticker("PWR", dw, fetch_news=False)
    assert res["setup_lane"] == "MOMENTUM_BREAKOUT"
    assert res["lane_label"] == "UNMEASURED"
    assert res["triage"] in ("WATCH", "PASS")


def test_mover_trigger_detection(tmp_path):
    from src.screener.continuous_screener_daemon import ContinuousScreenerDaemon

    daemon = ContinuousScreenerDaemon(poll_interval=600)

    # Mock dossiers directory structure
    dossier_data = {
        "triage": {
            "spot": 140.44,
            "setup_lane": "COIL",
            "triage": "WATCH",
            "long_plan": {
                "zone": [138.0, 142.0],
                "stop": 135.0,
                "target": 170.0,
            },
        },
        "llm_data": {
            "setup_lane": "COIL",
        },
    }

    t_dir = tmp_path / "data" / "raw" / "2026-10-02" / "VST"
    t_dir.mkdir(parents=True)
    th_file = t_dir / "VST_thesis.json"
    th_file.write_text(json.dumps(dossier_data), encoding="utf-8")

    with patch("src.config.BASE_DIR", tmp_path):
        with patch("src.clients.price_client.get_current_prices_batch", return_value={"VST": 166.66}):
            with patch("src.tracking.watch_manager.log_trigger_alert") as mock_alert:
                with patch("src.tracking.alert_db.queue_for_research") as mock_queue:
                    movers = daemon.rescore_recent_dossiers("2026-10-07")
                    assert len(movers) == 1
                    assert movers[0]["symbol"] == "VST"
                    assert movers[0]["move_pct"] > 18.0
                    assert mock_alert.called
                    alert_args = mock_alert.call_args[0]
                    assert alert_args[0] == "VST"
                    assert alert_args[1] == "MOVING"
                    assert "BUY 100 at market" in alert_args[2]
                    assert "lane COIL [UNMEASURED]" in alert_args[2]
                    assert mock_queue.called


def test_missed_moves_scorecard():
    res = get_missed_moves()
    assert "missed_moves" in res
    assert "weekly_rollup" in res
    assert len(res["missed_moves"]) >= 2

    # Verify VST backfill diagnosis
    vst = next((m for m in res["missed_moves"] if m["ticker"] == "VST"), None)
    assert vst is not None
    assert vst["root_cause_reason"] == "stale_data"
    assert vst["move_pct"] == 18.7
    assert vst["last_verdict"] == "CUT"

    # Verify NRG backfill diagnosis
    nrg = next((m for m in res["missed_moves"] if m["ticker"] == "NRG"), None)
    assert nrg is not None
    assert nrg["root_cause_reason"] == "stale_data"
    assert nrg["last_verdict"] == "WATCH"


def test_append_suggestion_live_spot_and_move_since_bar_pct(tmp_path):
    """Verify append_suggestion stores live_spot and move_since_bar_pct with correct bindings."""
    import sqlite3
    from src.tracking.suggestions_ledger import append_suggestion, ensure_suggestions_schema
    db_file = tmp_path / "test_sugg.db"
    with patch("src.tracking.watch_manager.DB_PATH", db_file):
        conn = sqlite3.connect(str(db_file))
        ensure_suggestions_schema(conn)
        conn.close()

        row_id = append_suggestion({
            "ticker": "VST",
            "date": "2026-10-07",
            "source": "triage",
            "side": "LONG",
            "entry_type": "LIMIT",
            "entry_low": 138.0,
            "entry_high": 142.0,
            "stop": 135.0,
            "target_1": 170.0,
            "live_spot": 166.66,
            "move_since_bar_pct": 18.7,
            "setup_lane": "COIL",
            "gate_status": "WATCH",
        })
        assert row_id > 0

        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute("SELECT * FROM suggestions WHERE ticker = 'VST'").fetchone()
        assert row is not None
        assert row["live_spot"] == 166.66
        assert row["move_since_bar_pct"] == 18.7
        assert row["setup_lane"] == "COIL"
        conn.close()
