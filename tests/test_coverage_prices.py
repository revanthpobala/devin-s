import sqlite3
from unittest.mock import patch

from src.tracking import alert_db
from src.tracking import auto_triage_daemon as atd

DAY = "2026-10-29"


def _rows():
    with sqlite3.connect(str(alert_db.DB_PATH)) as c:
        return {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT symbol, alert_price, market_price FROM alerts WHERE date = ? AND message_id LIKE 'synth-%'", (DAY,))}


def _queue(*syms):
    with sqlite3.connect(str(alert_db.DB_PATH)) as c:
        for s in syms:
            c.execute("INSERT OR IGNORE INTO research_queue (date, symbol, source, created_at) VALUES (?, ?, 'test', ?)",
                      (DAY, s, DAY))


def test_failed_quote_row_is_backfilled_next_cycle():
    _queue("CVS", "OUT")
    with patch.object(atd, "_coverage_prices", return_value={"CVS": 71.5}):
        atd._ensure_queue_coverage(DAY)
    assert _rows() == {"CVS": (71.5, 71.5), "OUT": (None, None)}

    with patch.object(atd, "_coverage_prices", return_value={"OUT": 9.25}) as m:
        atd._ensure_queue_coverage(DAY)
    assert m.call_args[0][0] == ["OUT"]            # only the unpriced row is re-quoted
    assert _rows() == {"CVS": (71.5, 71.5), "OUT": (9.25, 9.25)}


def test_stored_price_is_never_overwritten():
    _queue("CVS")
    with patch.object(atd, "_coverage_prices", return_value={"CVS": 71.5}):
        atd._ensure_queue_coverage(DAY)
    with patch.object(atd, "_coverage_prices", return_value={"CVS": 99.0}):
        atd._ensure_queue_coverage(DAY)
    assert _rows()["CVS"] == (71.5, 71.5)


def test_coverage_prices_falls_back_to_execution_quotes():
    calls = []

    def fake(syms, context="surveillance"):
        calls.append((tuple(syms), context))
        return {"CVS": 71.5} if context == "surveillance" else {"OUT": 9.25}

    with patch("src.clients.price_client.get_current_prices_batch", side_effect=fake):
        px = atd._coverage_prices(["CVS", "OUT"])
    assert px == {"CVS": 71.5, "OUT": 9.25}
    assert calls == [(("CVS", "OUT"), "surveillance"), (("OUT",), "execution")]
