import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from src import config
from src.ui.routes import intraday


def test_exited_signal_marked_at_exit_and_deduped(tmp_path, monkeypatch):
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    db = tmp_path / "a.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE intraday_signals (id INTEGER PRIMARY KEY, ticker TEXT, side TEXT, entry_price REAL, date TEXT, score REAL)")
    c.execute("CREATE TABLE alerts (symbol TEXT, alert_price REAL, timestamp TEXT, llm_decision TEXT, action TEXT)")
    c.executemany("INSERT INTO intraday_signals (ticker, side, entry_price, date, score) VALUES (?,?,?,?,?)",
                  [("AAPL", "LONG", 100.0, today, 100), ("AAPL", "LONG", 100.0, today, 88)])
    c.execute("INSERT INTO alerts VALUES ('AAPL', 101.0, ?, 'EXIT', 'EXIT')", (today + " 09:45:00",))
    c.commit()
    c.close()
    monkeypatch.setattr(config, "alerts_db_path", lambda: db)
    monkeypatch.setattr(intraday, "POSITIONS_FILE", tmp_path / "none.json")
    monkeypatch.setattr("src.clients.price_client.get_current_price", lambda *a, **k: 95.0)
    out = intraday.get_intraday_signals_endpoint()
    assert out["count"] == 1
    assert out["signals"][0]["status"] == "EXITED" and out["signals"][0]["pnl_pct"] == 1.0
