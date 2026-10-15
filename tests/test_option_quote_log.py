from datetime import datetime
from zoneinfo import ZoneInfo

from src.tracking import option_quote_log as oq

ET = ZoneInfo("America/New_York")


def _chain(cp_key, strikes):
    return {"underlyingPrice": 255.0, "status": "SUCCESS",
            cp_key: {"2026-11-20:38": {f"{k:.1f}": [{"bid": b, "ask": a, "volatility": 30.0, "delta": d,
                                                    "openInterest": 100, "totalVolume": 10}]
                                      for k, (b, a, d) in strikes.items()}}}


def test_legs():
    assert oq.legs_for("COVERED_CALL", 0, 270) == [("SHORT", "C", 270)]
    assert oq.legs_for("BULL_PUT_SPREAD", 240, 250) == [("SHORT", "P", 250), ("LONG", "P", 240)]
    assert oq.legs_for("LONG_CALL", 230, 0) == [("LONG", "C", 230)]


def test_capture_logs_net_mid_once_then_marks_after_close(tmp_path, monkeypatch):
    monkeypatch.setattr(oq, "db_path", lambda: tmp_path / "q.db")
    chain = _chain("callExpDateMap", {270: (3.0, 3.4, 0.3), 280: (1.0, 1.2, 0.15)})
    audit = {"id": 7, "ticker": "AMZN", "trade_structure": "BEAR_CALL_SPREAD", "options_expiration": "2026-11-20",
             "long_strike": 280.0, "short_strike": 270.0, "target_debit": 2.0}
    rows = oq.build_rows(audit, chain, "2026-10-14", "SUGGESTED", True)
    assert [r["leg"] for r in rows] == ["SHORT", "LONG"] and rows[0]["net_mid"] == round(3.2 - 1.1, 4)
    calls = []
    fetch = lambda t, e: calls.append((t, e)) or chain
    mid_day = datetime(2026, 10, 14, 11, 0, tzinfo=ET)
    assert oq.capture(mid_day, [audit], fetch) == 2
    assert oq.capture(mid_day, [audit], fetch) == 0 and len(calls) == 1
    assert oq.capture(datetime(2026, 10, 14, 16, 10, tzinfo=ET), [audit], fetch) == 2
    assert oq.capture(datetime(2026, 10, 14, 16, 20, tzinfo=ET), [audit], fetch) == 0
