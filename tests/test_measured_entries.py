"""Measured entries: Data Window lane, ticket build, paper fill/score, pre-open."""
from datetime import date, timedelta

import pytest

from src.logic import order_ticket as ot
from src.tracking import ticket_paper as tp


def _dw(**kw):
    base = {"close": 100.0, "Long Entry": 99.0, "Long Stop Loss": 95.0, "Long Target": 115.0,
            "Zone RR Flags Pack": 5, "Signal Pack": 36, "Action Long Code": 10, "Ext Z Self Relative": 0.0,
            "atr14": 2.0}
    base.update(kw)
    return base


def _bars(rows, start="2026-10-20"):
    d0 = date.fromisoformat(start)
    return [{"date": str(d0 + timedelta(days=i)), "open": o, "high": h, "low": l, "close": c}
            for i, (o, h, l, c) in enumerate(rows)]


class TestLane:
    def test_zone_rr(self):
        assert ot.measured_lane(_dw())["lane"] == "ZONE_RR"

    def test_fade_blocks_zone_rr(self):
        assert ot.measured_lane(_dw(**{"Signal Pack": 32})) is None

    def test_code18_blocks_zone_rr(self):
        assert ot.measured_lane(_dw(**{"Action Long Code": 18})) is None

    def test_low_rr_not_zone(self):
        assert ot.measured_lane(_dw(**{"Long Target": 105.0})) is None

    def test_code20(self):
        m = ot.measured_lane(_dw(**{"Zone RR Flags Pack": 0, "Action Long Code": 20}))
        assert m["lane"] == "CODE20"

    def test_oversold_even_if_toxic(self):
        m = ot.measured_lane(_dw(**{"Zone RR Flags Pack": 0, "Action Long Code": 18, "Ext Z Self Relative": -2.3}))
        assert m["lane"] == "OVERSOLD"

    def test_bad_levels(self):
        assert ot.measured_lane(_dw(**{"Long Stop Loss": 101.0})) is None


class TestBuild:
    @pytest.fixture(autouse=True)
    def _iso(self, monkeypatch):
        monkeypatch.setattr(ot, "held_symbols", lambda: set())
        monkeypatch.setattr(ot, "_earnings_within", lambda *a: None)
        monkeypatch.setattr(tp, "has_open", lambda *a: False)

    def test_ticket_schema(self):
        t = ot.build_measured_ticket("msft", _dw(), "2026-10-20")
        assert t["ticker"] == "MSFT" and t["lane"] == "ZONE_RR" and t["entry_method"] == "NEXT_OPEN"
        assert (t["stop"], t["target_1"], t["signal_close"]) == (95.0, 115.0, 100.0)
        assert t["good_til"] == "2026-10-21" and t["exit_by"] == "2026-11-18"
        assert t["paper"] and t["levels_source"] == "pine_alert"
        assert "MSFT BUY" in ot.ticket_text(t)

    def test_held_blocks(self, monkeypatch):
        monkeypatch.setattr(ot, "held_symbols", lambda: {"MSFT"})
        assert ot.build_measured_ticket("MSFT", _dw(), "2026-10-20") is None

    def test_earnings_blocks(self, monkeypatch):
        monkeypatch.setattr(ot, "_earnings_within", lambda *a: "2026-10-23")
        assert ot.build_measured_ticket("MSFT", _dw(), "2026-10-20") is None

    def test_open_paper_blocks(self, monkeypatch):
        monkeypatch.setattr(tp, "has_open", lambda *a: True)
        assert ot.build_measured_ticket("MSFT", _dw(), "2026-10-20") is None

    def test_research_supersede_does_not_block(self, monkeypatch):
        monkeypatch.setattr(ot, "_latest_research_date", lambda *a: "2026-10-22")
        assert ot.build_measured_ticket("MSFT", _dw(), "2026-10-20") is not None


class TestEvaluate:
    T = {"entry_method": "NEXT_OPEN", "stop": 95.0, "target_1": 115.0}

    def test_go(self):
        assert ot.evaluate_ticket(self.T, 101.0)["verdict"] == "GO"

    def test_gap_below(self):
        assert ot.evaluate_ticket(self.T, 94.0)["verdict"] == "STAND DOWN"

    def test_gap_above(self):
        assert ot.evaluate_ticket(self.T, 116.0)["verdict"] == "STAND DOWN"

    def test_legacy_limit_unchanged(self):
        t = {"stop": 95.0, "target_1": 115.0, "limit_price": 99.0, "max_chase_price": 101.0}
        assert ot.evaluate_ticket(t, 100.0)["verdict"] == "RESTING"


class TestFill:
    def test_target_hit_with_fees(self):
        b = _bars([(100, 100, 100, 100), (101, 103, 100, 102), (102, 116, 101, 114)])
        r = tp.fill_ticket(100.0, 95.0, 115.0, b)
        assert r["filled_price"] == 101 and r["outcome"] == "TARGET_HIT" and r["mature"] == 1
        assert r["r_multiple"] == pytest.approx((115 - 101 - 0.0005 * 216) / 5)

    def test_stop_first_same_bar(self):
        b = _bars([(100, 100, 100, 100), (101, 116, 94, 100)])
        assert tp.fill_ticket(100.0, 95.0, 115.0, b)["outcome"] == "STOP_HIT"

    def test_gap_through_stop_exits_at_open(self):
        b = _bars([(100, 100, 100, 100), (101, 102, 100, 101), (90, 91, 89, 90)])
        r = tp.fill_ticket(100.0, 95.0, 115.0, b)
        assert r["exit_price"] == 90 and r["r_multiple"] < -2

    def test_gap_below_stop_no_fill(self):
        b = _bars([(100, 100, 100, 100), (94, 95, 93, 94)])
        assert tp.fill_ticket(100.0, 95.0, 115.0, b)["outcome"] == "GAP_BELOW_STOP"

    def test_gap_above_target_no_fill(self):
        b = _bars([(100, 100, 100, 100), (116, 117, 115, 116)])
        assert tp.fill_ticket(100.0, 95.0, 115.0, b)["outcome"] == "GAP_ABOVE_TARGET"

    def test_open_trade_tracks_spot(self):
        b = _bars([(100, 100, 100, 100), (101, 103, 100, 102), (102, 104, 101, 103)])
        r = tp.fill_ticket(100.0, 95.0, 115.0, b)
        assert r["outcome"] == "OPEN" and r["mature"] == 0 and r["last_price"] == 103 and r["unrealized_r"] > 0

    def test_expired_after_21_bars(self):
        b = _bars([(100, 100, 100, 100)] + [(101, 103, 99, 102)] * 21)
        r = tp.fill_ticket(100.0, 95.0, 115.0, b)
        assert r["outcome"] == "EXPIRED" and r["bars_held"] == 21 and r["mature"] == 1

    def test_pending_without_next_bar(self):
        assert tp.fill_ticket(100.0, 95.0, 115.0, _bars([(100, 100, 100, 100)]))["outcome"] == "PENDING"


class TestLedger:
    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        import sqlite3
        db = tmp_path / "w.db"
        monkeypatch.setattr(tp, "_get_connection", lambda: sqlite3.connect(str(db)))

    def test_record_score_summary(self):
        t = {"ticker": "MSFT", "date": "2026-10-20", "lane": "ZONE_RR", "levels_source": "pine_alert",
             "entry_method": "NEXT_OPEN", "signal_close": 100.0, "rr_at_market": 3.0, "stop": 95.0,
             "target_1": 115.0, "limit_price": 100.0, "exit_by": "2026-11-18", "qty": 100}
        assert tp.record_ticket(t) and not tp.record_ticket(t)
        assert tp.has_open("MSFT", "ZONE_RR")
        res = tp.score_pending({"MSFT": _bars([(100, 100, 100, 100), (101, 103, 100, 102), (102, 116, 101, 114)])})
        assert res == {"scored": 1, "matured": 1, "no_bars": 0}
        assert not tp.has_open("MSFT", "ZONE_RR")
        s = tp.lane_summary()["ZONE_RR"]
        assert s["signals"] == 1 and s["filled"] == 1 and s["n_scored"] == 1 and s["win_pct"] == 100.0
