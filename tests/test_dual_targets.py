from unittest.mock import patch

import run_watch_alerts as rwa
from src.logic import report_consistency as rc
from src.tracking import plan_repair, watch_manager

DUAL = {"entry_zone_low": 96.0, "entry_zone_high": 98.0, "tactical_stop": 93.0, "target_1": 104.0, "target_2": 108.0,
        "breakout_level": 110.0, "breakout_stop": 106.0, "breakout_target_1": 118.0, "breakout_target_2": 126.0}


def test_leg_targets_follow_the_leg_price_is_on():
    pv = rwa._plan_view_from_row({"shares_plan": DUAL})
    assert pv["breakout_target_1"] == 118.0
    assert rwa._leg_targets(pv, 99.0) == (104.0, 108.0)
    assert rwa._leg_targets(pv, 111.0) == (118.0, 126.0)
    assert rwa._leg_targets(pv, 105.0, breakout_cross=True) == (118.0, 126.0)
    pure = rwa._plan_view_from_row({"shares_plan": {k: v for k, v in DUAL.items() if not k.startswith("breakout_target")}})
    assert rwa._leg_targets(pure, 111.0) == (104.0, 108.0)


def test_watch_row_and_sync_carry_breakout_targets(tmp_path):
    with patch.object(watch_manager, "DB_PATH", tmp_path / "t.db"):
        watch_manager.init_watch_db()
        watch_manager.upsert_watch_target({"ticker": "DUA", "date": "2026-10-23", "verdict": "STALK", "shares_plan": dict(DUAL),
                                           "options_plan": {}, "invalidation": {"price_level": 93.0}, "status": "STALKING"})
        row = {t["ticker"]: t for t in watch_manager.get_all_watch_targets()}["DUA"]
        assert (row["target_1"], row["breakout_target_1"], row["breakout_target_2"]) == (104.0, 118.0, 126.0)
        with watch_manager._get_connection() as conn:
            conn.execute(
                "CREATE TABLE suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, date TEXT, side TEXT, entry_low REAL,"
                " entry_high REAL, stop REAL, target_1 REAL, target_2 REAL, signal_pack REAL, fade REAL, action_long REAL, ext_z REAL,"
                " atr_at_signal REAL, rr_at_market_at_signal REAL, verdict TEXT, setup_lane TEXT, gate_status TEXT, source TEXT,"
                " breakout_target_1 REAL, breakout_target_2 REAL)")
            conn.execute(
                "INSERT INTO suggestions (ticker, date, side, entry_low, entry_high, stop, target_1, target_2, verdict, gate_status,"
                " breakout_target_1, breakout_target_2) VALUES ('SYN', date('now'), 'LONG', 10, 11, 9, 14, 16, 'PASS', 'PASS', 18, 20)")
            conn.commit()
        assert watch_manager.sync_active_suggestions_to_watch_targets() == 1
        row = {t["ticker"]: t for t in watch_manager.get_all_watch_targets()}["SYN"]
        assert (row["target_1"], row["breakout_target_1"], row["breakout_target_2"]) == (14.0, 18.0, 20.0)


def test_repair_writes_breakout_targets_for_dual_plans_only():
    dual = {k: v for k, v in DUAL.items() if not k.startswith("breakout_target")}
    dual["target_1"], dual["target_2"] = 104.0, 109.0
    ch = plan_repair.repair_shares_plan(dual, ticker="TST", atr=2.0)
    assert ch and (dual["target_1"], dual["target_2"]) == (104.0, 109.0) and dual["breakout_target_1"] > 110.0
    assert plan_repair.repair_shares_plan(dual, ticker="TST", atr=2.0) is None
    pure = {"entry_type": "BREAKOUT", "tactical_stop": 93.0, "target_1": 104.0, "target_2": 109.0, "breakout_level": 110.0, "breakout_stop": 106.0}
    plan_repair.repair_shares_plan(pure, ticker="TST", atr=2.0)
    assert pure["target_1"] > 110.0 and "breakout_target_1" not in pure


def test_report_names_both_legs_without_flagging():
    wd = {"shares_plan": {**DUAL, "targets_rebuilt": True}, "atr14": 2.0}
    text = "Zone entry stop $93. Zone T1 $104.00. Breakout T1 $118.00 above $110."
    assert rc.check(text, wd, {}, 100.0) == []
    block = rc.binding_block(wd, [])
    assert "zone T1 104" in block and "breakout T1 118" in block
