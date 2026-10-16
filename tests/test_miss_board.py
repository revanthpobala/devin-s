from src.tracking.miss_board import classify


def _bars(*rows):
    return [{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows]


def _row(**kw):
    base = {"ticker": "AAOI", "date": "2026-10-15", "verdict": "STALK", "gate_status": "PASS", "spot_at_signal": 80.7,
            "atr_at_signal": 6.0, "entry_low": 76.0, "entry_high": 78.5, "stop": 74.5, "target_1": 85.75}
    base.update(kw)
    return base


def test_ran_up_without_touching_zone_is_missed():
    out = classify(_row(), _bars(("2026-10-16", 81, 84, 80, 83), ("2026-10-17", 84, 87, 83, 85)))
    assert out["outcome"] == "MISSED_UP" and "ATR above" in out["why"] and out["move_pct"] > 0


def test_touch_then_target_and_stop_first_on_shared_bar():
    win = classify(_row(), _bars(("2026-10-16", 80, 80, 77, 79), ("2026-10-17", 79, 86, 78, 85)))
    assert win["outcome"] == "FILLED_TARGET"
    shared = classify(_row(), _bars(("2026-10-16", 80, 90, 70, 85)))
    assert shared["outcome"] == "FILLED_STOPPED"


def test_gate_rejected_plan_without_levels_is_no_plan():
    out = classify(_row(entry_low=None, entry_high=None, stop=34.5, target_1=None, gate_status="REJECTED_BY_GATE",
                        gate_reasons='["earnings inside 21 bars"]'), _bars(("2026-10-16", 81, 84, 80, 83)))
    assert out["outcome"] == "NO_PLAN" and "earnings" in out["why"]


def test_no_bars_after_signal_is_pending():
    assert classify(_row(), _bars(("2026-10-15", 80, 81, 76, 80.7)))["outcome"] == "PENDING"


def test_tracks_spot_move_direction_and_market_context():
    spy = _bars(("2026-10-15", 700, 701, 699, 700), ("2026-10-16", 700, 707, 700, 707))
    row = _row(ext_z=-1.5, fade=0)
    up = classify(row, _bars(("2026-10-16", 81, 84, 80, 83)), spy)
    assert up["with_thesis"] and up["ret_1d"] == 2.85 and up["mfe_pct"] == 4.09 and up["spy_move_pct"] == 1.0
    assert up["conditions"]["ext_z"] == -1.5
    down = classify(row, _bars(("2026-10-16", 80, 80, 76, 77)), spy)
    assert not down["with_thesis"] and down["mae_pct"] < 0
