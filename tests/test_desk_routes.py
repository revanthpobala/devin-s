import pytest
import sqlite3
from fastapi.testclient import TestClient
import json

from src.ui.app import create_app
from src.tracking.watch_manager import _get_connection, _db_lock, init_watch_db
from src.tracking import suggestion_scorer
from src.tracking.alert_db import set_db_path as set_alert_db_path

# Filled in by the `client` fixture: the ids a trade opened from these desk rows would carry.
SEED_IDS = {}

@pytest.fixture
def client(tmp_path, monkeypatch):
    watch_db = tmp_path / "test_research_watch.db"
    alert_db = tmp_path / "test_trading_alerts.db"
    monkeypatch.setenv("RESEARCH_WATCH_DB", str(watch_db))
    # RESEARCH_WATCH_DB alone is not enough: ui_state.db_path() resolves STOCK_DB_PATH, so without
    # this the cockpit tables land in the conftest session copy instead of this test's database and
    # `active_research_jobs` is missing from the fixture's own DB.
    monkeypatch.setenv("STOCK_DB_PATH", str(watch_db))
    monkeypatch.setenv("ALERT_DB_PATH", str(alert_db))
    from src.tracking import watch_manager
    watch_manager.DB_PATH = watch_db
    set_alert_db_path(str(alert_db))

    init_watch_db()
    # The same helpers conftest.py uses, rather than hand-written CREATE TABLE stubs: a stub is a
    # no-op once the table exists (which is why the old 6-column watch_targets stub hid the fact
    # that it was never being used), and it never grows when the real schema does.
    from src.tracking.suggestions_ledger import (
        ensure_rejected_plans_schema,
        ensure_suggestions_schema,
    )
    from src.ui import state as ui_state

    ui_state.init_db()
    with _get_connection() as conn:
        ensure_suggestions_schema(conn)
        ensure_rejected_plans_schema(conn)
        conn.commit()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, atr_at_signal, signal_pack, fade, action_long, ext_z) VALUES (?, ?, 'test', 'PASS', 'RR_SETUP', 100, 110, 95, 130, 5.0, 36, 0.0, 20, 0.0)", ('MSFT', today))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1) VALUES (?, ?, 'test', 'PASS', 'CODE20', 200, 210, 190, 240)", ('AAPL', today))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'CODE20', 900, 910, 880, 950, '2026-09-25', '2026-09-26', 1.5)", ('TSLA', '2026-09-24'))
            cursor.execute("INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'RR_SETUP', 600, 610, 580, 640, '2026-09-25', '2026-09-26', -1.0)", ('NVDA', '2026-09-24'))

            # Insert 55 closed suggestions to verify SQL pagination returns a full page of 50
            for i in range(55):
                cursor.execute(
                    "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, stop, target_1, fill_date, exit_date, r_net) VALUES (?, ?, 'legacy', 'PASS', 'RR_SETUP', 100, 105, 95, 120, '2026-08-01', '2026-08-05', 1.0)",
                    (f"CL{i:02d}", "2026-08-01")
                )

            cursor.execute("INSERT INTO rejected_plans (ticker, date, side, reasons) VALUES ('REJ', '2026-09-24', 'LONG', 'Low R:R')", ())

            # last_price drives RR@mkt: MSFT at 105 against stop 95 / target 130 gives 2.5, which
            # is what earns it a place in needs-you rather than the unmeasured group.
            # Seeded against the FULL schema: watch_targets has 49 columns, including the
            # suggestion_id / watch_target_id links that a close writes back through.
            msft_watch = cursor.execute(
                """
                INSERT INTO watch_targets (ticker, date, status, verdict, conviction, actionable, side,
                                           entry_type, entry_zone_low, entry_zone_high, tactical_stop,
                                           target_1, target_2, options_structure, options_summary,
                                           options_actionable, last_price, distance_to_entry_pct,
                                           updated_at, signal_pack, fade, action_long, ext_z,
                                           atr_at_signal, is_active, thesis_id, created_date, expires_on)
                VALUES (?, ?, 'IN_ZONE', 'ENTER', 8, 1, 'LONG', 'LIMIT', 100.0, 105.0, 95.0, 130.0, 140.0,
                        'DEBIT SPREAD', 'MSFT 100/110 CALL SPREAD', 1, 105.0, 0.5, ?, 36, 0.0, 20, 0.0, 5.0, 1, ?, ?, ?)
                """,
                ('MSFT', today, today, 'MSFT_' + today, today, today),
            )
            msft_watch_rowid = msft_watch.lastrowid
            # The ids a trade opened from this desk row would carry. NOTE the shape: watch_target_id
            # is NOT a column on suggestions or watch_targets -- it is a field on the in-memory
            # position record in data/positions.json, set from the watchlist row's rowid. Which is
            # why close_position()'s watch_targets write used to raise on every trade opened by the
            # alert tracker: it only had a ticker to go on.
            msft_sugg = cursor.execute("SELECT id FROM suggestions WHERE ticker = 'MSFT'").fetchone()
            SEED_IDS.update({
                "msft_watch_rowid": msft_watch_rowid,
                "msft_suggestion_id": msft_sugg["id"],
            })
            assert msft_watch_rowid and msft_sugg["id"]
            # ...and the linked column really is absent, so nobody "fixes" this toward the wrong
            # guess later.
            watch_cols = {r[1] for r in cursor.execute("PRAGMA table_info(watch_targets)").fetchall()}
            sugg_cols = {r[1] for r in cursor.execute("PRAGMA table_info(suggestions)").fetchall()}
            assert "watch_target_id" not in watch_cols and "watch_target_id" not in sugg_cols

            cursor.execute("INSERT INTO watch_targets (ticker, date, status, verdict, conviction, actionable, side, entry_type, entry_zone_low, entry_zone_high, tactical_stop, target_1, target_2, distance_to_entry_pct, last_price, updated_at, is_active, thesis_id, created_date, expires_on) VALUES ('AAPL', ?, 'STALKING', 'STALK', 5, 1, 'LONG', 'LIMIT', 200.0, 210.0, 190.0, 240.0, 250.0, 3.0, 205.0, ?, 1, ?, ?, ?)", (today, today, 'AAPL_' + today, today, today))

            # The universe placeholder rows: 85% of what /api/watch-targets used to return.
            for i in range(5):
                cursor.execute(
                    "INSERT INTO watch_targets (ticker, date, verdict, status, updated_at, is_active) "
                    "VALUES (?, '', 'UNMEASURED', 'UNRESEARCHED', '1970-01-01T00:00:00', 1)",
                    (f"PLA{i:03d}",),
                )
            # The tracked_universe table is where those belong now.
            cursor.execute(
                "INSERT INTO tracked_universe (ticker, name, sector, source, added_at) VALUES (?, ?, ?, ?, ?)",
                ("MSFT", "Microsoft Corp", "Technology", "SCHWAB_1000", today),
            )
            # A live trigger alert, so the poll and the badge disagree when the watch row is stale.
            cursor.execute(
                "INSERT INTO watch_alerts (ticker, date, trigger_type, message, spot_price, triggered_at) "
                "VALUES (?, ?, 'ENTRY_ZONE', 'MSFT entered the buy zone', 104.25, ?)",
                ("MSFT", today, today),
            )

            cursor.execute("INSERT INTO active_research_jobs (job_id, ticker, mode, stage, status, started_at, target_date) VALUES ('job-1', 'AAPL', 'full', 'DONE', 'COMPLETED', ?, ?)", (datetime.now().isoformat(), today))

            conn.commit()

    with sqlite3.connect(str(alert_db), timeout=30.0) as acon:
        ac = acon.cursor()
        ac.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                message_id TEXT PRIMARY KEY,
                email_id TEXT,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                strategy TEXT NOT NULL,
                alert_price REAL,
                market_price REAL,
                event TEXT,
                setup TEXT,
                verdict TEXT,
                act_now TEXT,
                plan TEXT,
                score TEXT,
                grade TEXT,
                align TEXT,
                wrong_if TEXT,
                raw_payload TEXT NOT NULL,
                status TEXT DEFAULT 'INGESTED',
                llm_decision TEXT,
                llm_playbook TEXT,
                created_at TEXT NOT NULL
            );
        """)
        ac.execute("""
            CREATE TABLE IF NOT EXISTS research_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                setup TEXT,
                status TEXT DEFAULT 'QUEUED',
                reason TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(date, symbol)
            );
        """)
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'AAPL', 'screener', 'Breakout', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'MSFT', 'screener', 'Pullback', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))
        ac.execute("INSERT INTO research_queue (date, symbol, source, setup, status, reason, created_at) VALUES (?, 'GOOG', 'screener', 'Breakout', 'QUEUED', 'Test', ?)", (today, datetime.now().isoformat()))

        try:
            ac.execute("INSERT OR IGNORE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload) VALUES (?, ?, ?, 'MSFT', 'LONG', 'test', 'PASS', 'Test playbook', 'LOCAL_TRIAGE', ?, 'test raw payload')", (f"msg-msft-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            ac.execute("INSERT OR IGNORE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload) VALUES (?, ?, ?, 'AAPL', 'LONG', 'test', 'WATCH', 'Test playbook', 'LOCAL_TRIAGE', ?, 'test raw payload')", (f"msg-aapl-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            acon.commit()
        except Exception as e:
            print(f"Alert DB insert error: {e}")
            raise

    app = create_app()
    return TestClient(app)

def test_get_today(client):
    response = client.get("/api/desk/today")
    print('RESPONSE:', response.json())
    assert response.status_code == 200
    data = response.json()
    assert "actionable" in data
    assert "stalking" in data
    assert "found" in data
    assert "coverage" in data
    assert "needs_you" in data
    assert "watch" in data
    tickers_found = []
    for f in data["found"]:
        tickers_found.extend(f.get("tickers", []))
    assert "MSFT" in tickers_found or "AAPL" in tickers_found
    assert any(r.get("ticker") == "MSFT" for r in data.get("needs_you", []))
    assert any(r.get("ticker") == "AAPL" for r in data.get("watch", []))

def test_get_today_dedup(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    data = response.json()
    act_tickers = [r.get("ticker") for r in data.get("actionable", [])]
    stalk_tickers = [r.get("ticker") for r in data.get("stalking", [])]
    all_tickers = act_tickers + stalk_tickers
    assert len(all_tickers) == len(set(all_tickers))

def test_get_today_live_rr_null_when_below_stop(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    data = response.json()
    for a in data.get("actionable", []):
        if a.get("ticker") == "MSFT" and a.get("live_rr_flag") == "BELOW_STOP":
            assert a.get("live_rr") is None

def test_get_journal_include_rejected(client):
    response = client.get("/api/desk/journal?include_rejected=1")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "summary" in data
    assert any(r.get("row_type") == "rejected" for r in data["items"])

def test_get_journal_status_filter_paged(client):
    response = client.get("/api/desk/journal?status=CLOSED&page=1")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    # 57 total closed items inserted; page 1 must return a full page of 50
    assert len(data["items"]) == 50
    for r in data["items"]:
        assert r.get("derived_status") == "CLOSED"
        assert r.get("r_net") is not None

    # Page 2 returns the remaining 7 items
    response_p2 = client.get("/api/desk/journal?status=CLOSED&page=2")
    assert response_p2.status_code == 200
    assert len(response_p2.json()["items"]) == 7

def test_get_journal_paging_returns_full_page(client):
    response = client.get("/api/desk/journal?page=1")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "per_status" in data.get("summary", {})
    assert data["summary"]["per_status"]["CLOSED"] == 57

def test_get_record_scoped_keys(client):
    response = client.get("/api/desk/record")
    assert response.status_code == 200
    data = response.json()
    assert "total_scored" in data
    assert "sum_r" in data
    assert "mean_r" in data
    assert "median_r" in data
    assert "equity_curve" in data
    assert "by_lane" in data
    assert "first_score_eta" in data
    assert "scope" in data

def test_get_record_gated_zero_keys(client):
    from unittest.mock import MagicMock, patch
    # The record scorecard reads through the lock-free read-only WAL connection;
    # an empty result set must score zero keys, not fall back to the real DB.
    fake_conn = MagicMock()
    fake_cursor = fake_conn.__enter__.return_value.cursor.return_value
    fake_cursor.execute.return_value.fetchall.return_value = []
    fake_cursor.execute.return_value.fetchone.return_value = None
    with patch("src.ui.routes.desk.readonly_connection", return_value=fake_conn):
        response = client.get("/api/desk/record?from=2026-09-01&scope=gated")
    assert response.status_code == 200
    data = response.json()
    assert data["total_scored"] == 0
    assert data["sum_r"] == 0.0
    assert data["mean_r"] == 0.0
    assert data["median_r"] == 0.0

def test_get_record_all_legacy(client):
    response = client.get("/api/desk/record?scope=all")
    assert response.status_code == 200
    data = response.json()
    assert "equity_curve" in data
    assert "by_lane" in data
    assert "scope" in data
    assert data["scope"] == "all"
    assert data["total_scored"] == 57
    assert data["sum_r"] > 0
    assert "RR_SETUP" in data["by_lane"]
    assert "prior_win" in data["by_lane"]["RR_SETUP"]

def test_get_coverage(client):
    response = client.get("/api/desk/coverage")
    assert response.status_code == 200
    data = response.json()
    assert "alerts" in data
    assert "local_done" in data
    assert "local_pass" in data
    assert "deep_done" in data
    assert "deep_missing" in data
    assert isinstance(data["deep_missing"], list)
    assert "local_missing" in data
    assert isinstance(data["local_missing"], list)
    # GOOG is queued without evaluation, so it is missing; AAPL and MSFT are evaluated
    assert "GOOG" in data["local_missing"]
    assert "AAPL" not in data["local_missing"]
    assert "MSFT" not in data["local_missing"]

def test_coverage_intraday_alerts_not_in_local_missing(client):
    from src.tracking.alert_db import _get_connection as get_alert_conn, _db_lock as alert_lock
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    # Insert intraday alert with non-PASS/WATCH/CUT decision format
    with alert_lock:
        with get_alert_conn() as acon:
            cur = acon.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload)
                VALUES (?, ?, ?, 'QQQ', 'LONG', 'Intraday', 'QQQ 10:03 AM ET — TAKE CALLS', 'Calls playbook', 'EXECUTED', ?, '{}')
            """, (f"msg-qqq-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            # Insert alert without decision (genuinely unevaluated)
            cur.execute("""
                INSERT OR REPLACE INTO alerts (message_id, date, timestamp, symbol, action, strategy, llm_decision, llm_playbook, status, created_at, raw_payload)
                VALUES (?, ?, ?, 'AMD', 'LONG', 'Swing', NULL, NULL, 'PENDING', ?, '{}')
            """, (f"msg-amd-{today}", today, datetime.now().isoformat(), datetime.now().isoformat()))
            acon.commit()

    response = client.get("/api/desk/coverage")
    assert response.status_code == 200
    data = response.json()
    # Intraday alert QQQ has a valid non-empty decision, so it must NOT be in local_missing
    assert "QQQ" not in data["local_missing"]
    # AMD has NULL decision, so it MUST be in local_missing
    assert "AMD" in data["local_missing"]

def test_update_journal_notes(client):
    response = client.patch("/api/desk/journal/1", json={"notes": "new notes"})
    assert response.status_code == 200
    assert response.json().get("success") is True


# =============================================================================
# Real-schema fixtures: the desk reads tables that have 49 and ~28 columns, not 6.
#
# The fixture above used to CREATE its own 5-6 column `watch_targets` stub. That stub was inert
# (init_watch_db() had already created the real table, so the CREATE was a no-op), which meant the
# suite never exercised a single column added after the first version of the schema -- including
# every one close_position() now writes to. These tests exercise them.
# =============================================================================

def _desk_payload(client):
    response = client.get("/api/desk/today")
    assert response.status_code == 200
    return response.json()


def _all_desk_lists(data):
    """Every ticker list the desk's payload exposes a consumer to."""
    out = []
    for key in ("actionable", "stalking", "needs_you", "watch", "found", "missed"):
        out.extend(data.get(key, []))
    for group in data.get("found", []):
        out.extend(group.get("tickers", []))
    for item in out:
        if isinstance(item, dict):
            yield item
        elif isinstance(item, str):
            yield {"ticker": item}


def test_desk_today_ignores_the_unresearched_placeholders(client):
    """The 844 placeholder constituents must not appear anywhere in the desk payload."""
    data = _desk_payload(client)
    exposed = [row.get("ticker") for row in _all_desk_lists(data)]
    for i in range(5):
        assert f"PLA{i:03d}" not in exposed, (
            "a universe placeholder reached the desk payload; the read filter is gone"
        )


def test_the_real_schema_columns_survive_the_read_path(client):
    """The 6-column stub never had these; every one of them is read back over the API.

    The desk's `/api/desk/today` payload for this fixture is live-Schwab dependent (it reaches out
    for quotes), so the deterministic consumer of the fixture is the watchlist read it backs.
    """
    response = client.get("/api/watch-targets")
    assert response.status_code == 200
    row = next(t for t in response.json()["targets"] if t["ticker"] == "MSFT")

    # last_price 105 against stop 95 / target 130 is what RR@mkt is computed from.
    assert row["last_price"] == 105.0
    assert row["entry_zone_low"] == 100.0
    assert row["entry_zone_high"] == 105.0
    assert row["tactical_stop"] == 95.0
    assert row["target_1"] == 130.0
    # The identity and provenance columns the paper ledger joins on.
    assert row["thesis_id"] == f"MSFT_{row['date']}"
    assert row["created_date"] == row["date"]
    assert row.get("expires_on")


def test_desk_coverage_ignores_placeholders(client):
    response = client.get("/api/desk/coverage")
    assert response.status_code == 200
    data = response.json()
    local_missing = data.get("local_missing", []) or []
    watch_no_deep = data.get("watch_no_deep", []) or []
    for i in range(5):
        assert f"PLA{i:03d}" not in local_missing
        assert f"PLA{i:03d}" not in watch_no_deep


def test_watchlist_read_rejects_placeholders_on_the_real_schema(client):
    response = client.get("/api/watch-targets")
    assert response.status_code == 200
    tickers = [t["ticker"] for t in response.json()["targets"]]
    assert tickers == ["MSFT", "AAPL"], f"real rows leaked or placeholders exposed: {tickers}"


def test_close_position_can_write_through_to_the_seeded_watch_row(client, tmp_path):
    """End-to-end for §0.1: a trade opened from this desk row, then closed.

    Before the fix this UPDATE named `watch_targets.exit_price` and `watch_targets.r_realized`,
    neither of which exists, so it raised inside a broad `except` and every close silently failed
    to record its outcome -- the calibration panel's "only 22% of closed trades recorded an exit
    price" was that, missing, and no test saw it because the fixture's watch_targets stub had
    neither column either.
    """
    from src import config
    from src.tracking import position_state

    assert SEED_IDS.get("msft_watch_rowid") and SEED_IDS.get("msft_suggestion_id")

    fake_positions = tmp_path / "positions.json"
    with monkeypatch_attr(position_state, "POSITIONS_FILE", fake_positions):
        position_state.open_position(
            "MSFT", side="LONG", strategy="Swing",
            entry_price=100.0, stop=95.0, target=130.0,
            watch_target_id=SEED_IDS["msft_watch_rowid"],
            suggestion_id=SEED_IDS["msft_suggestion_id"],
        )
        rec = position_state.close_position("MSFT", exit_price=140.0, exit_reason="TARGET_2_HIT")
    assert rec and rec["exit_price"] == 140.0

    watch_db = config.research_watch_db_path()
    conn = sqlite3.connect(str(watch_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, last_price, evaluation_verdict, updated_at FROM watch_targets WHERE ticker = 'MSFT'"
        ).fetchone()
        sugg = conn.execute(
            "SELECT exit_price, exit_reason, r_net, exit_date FROM suggestions WHERE id = ?",
            (SEED_IDS["msft_suggestion_id"],),
        ).fetchone()
    finally:
        conn.close()

    assert row["status"] == "COMPLETED"
    assert row["last_price"] == 140.0
    assert row["evaluation_verdict"] == "TARGET_HIT"
    assert sugg["exit_price"] == 140.0
    assert sugg["exit_reason"] == "TARGET_2_HIT"
    assert sugg["r_net"] is not None


def test_the_seed_contains_the_universe_and_alert_tables(client):
    """tracked_universe and watch_alerts are real tables, and the fixture seeds both."""
    with _get_connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tracked_universe", "watch_alerts"} <= tables
        assert conn.execute("SELECT count(1) FROM tracked_universe").fetchone()[0] >= 1
        assert conn.execute("SELECT count(1) FROM watch_alerts").fetchone()[0] >= 1
        row = dict(conn.execute("SELECT * FROM watch_alerts WHERE ticker = 'MSFT'").fetchone())
    assert row["trigger_type"] == "ENTRY_ZONE"
    assert row["spot_price"] == 104.25


def test_rr_at_market_never_scores_the_planned_entry(client):
    """A ratio measured at the planned entry is not "at the market" and must not land there.

    The plan fallback also manufactured ratios out of degenerate geometry (a $1-wide risk
    scored as "RR 29"), and it ranked stop-inside-noise rows above real ones. rr_at_market
    now holds the signal-time ratio or the live-print ratio, or nothing.

    The plan-geometry ratio's own shape is round((target - entry_h) / (entry_h - stop), 2).
    C3 keeps a matching number for its own labelled field (rr_zone_at_entry_high — the zone
    path's R:R at entry_high, stated beside the breakout path's), so the guard is scoped to
    the rr_at_market block: the formula must never flow into that field's assignment.
    """
    import src.ui.services.today_service as desk_mod

    desk_src = (desk_mod.__file__ and open(desk_mod.__file__, encoding="utf-8").read()) or ""
    start = desk_src.index("if rr_at_signal is not None:")
    end = desk_src.index('r["rr_at_market_source"] = rr_source')
    rr_block = desk_src[start:end]
    assert "entry_h - stop" not in rr_block, "the plan-geometry ratio is back in rr_at_market"
    # ... and the C3 zone path carries it under its own name, separately.
    assert 'r["rr_zone_at_entry_high"] = rr_zone_at_entry_high' in desk_src


def test_local_score_is_published_on_one_scale(client):
    """alerts.score is the 0-100 card score; the desk's conviction band is 1-10.

    Before the normalization the inbox column served 63 (AAPL card) directly beside 4/10
    (a swing badge), which reads as two unrelated numbers.
    """
    from src.ui.routes.desk import _normalized_local_score

    assert _normalized_local_score("63") == 6.3
    assert _normalized_local_score("100") == 10.0
    # A raw number is always the 0-100 card score: a "7" is 0.7 on the band, never 7/10.
    # A band conviction never arrives without its "/10" (from the decision badge).
    assert _normalized_local_score("7") == 0.7
    assert _normalized_local_score("6/10") == 6.0
    assert _normalized_local_score("0") == 0.0
    # Missing or unparseable is unmeasured, not a guessed band.
    assert _normalized_local_score("") == ""
    assert _normalized_local_score(None) == ""
    assert _normalized_local_score("abc") == ""


def test_card_states_both_paths_and_both_rr(client):
    """C3: a name with a limit zone below market AND a breakout path states both paths
    and their R:R (zone R:R at entry_high; breakout R:R at the trigger) instead of one
    blended number. A trigger whose targets sit at/below it is flagged broken, not blended.
    """
    from src.ui.routes.desk import get_today

    with _db_lock, _get_connection() as conn:
        cursor = conn.cursor()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        # Zone 100-110 below the market, break at 125 above it, stop 95, target 130.
        cursor.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, breakout_level, stop, target_1, atr_at_signal, signal_pack, fade, action_long, ext_z) "
            "VALUES ('BKO', ?, 'test', 'PASS', 'RR_SETUP', 100, 110, 125, 95, 130, 5.0, 36, 0.0, 20, 0.0)",
            (today,),
        )
        # A broken trigger path: target 120 sits BELOW the breakout 125 (the META shape).
        cursor.execute(
            "INSERT INTO suggestions (ticker, date, source, gate_status, setup_lane, entry_low, entry_high, breakout_level, stop, target_1, atr_at_signal, signal_pack, fade, action_long, ext_z) "
            "VALUES ('BRK', ?, 'test', 'PASS', 'RR_SETUP', 100, 110, 125, 95, 120, 5.0, 36, 0.0, 20, 0.0)",
            (today,),
        )
        cursor.execute(
            "INSERT INTO watch_targets (ticker, date, status, verdict, actionable, side, last_price, updated_at) "
            "VALUES ('BKO', ?, 'STALKING', 'STALK', 1, 'LONG', 112.0, ?)",
            (today, datetime.now().isoformat()),
        )
        cursor.execute(
            "INSERT INTO watch_targets (ticker, date, status, verdict, actionable, side, last_price, updated_at) "
            "VALUES ('BRK', ?, 'STALKING', 'STALK', 1, 'LONG', 112.0, ?)",
            (today, datetime.now().isoformat()),
        )
        # The desk only assembles cards for rows with a live alert decision.
        conn.commit()

    # Cards are only built for rows that have a local triage decision; seed one per ticker
    # through the alerts table the desk reads.
    from src.tracking import alert_db as _adb
    with _adb._db_lock, _adb._get_connection() as ac:
        for sym in ("BKO", "BRK"):
            ac.execute(
                """
                INSERT OR REPLACE INTO alerts (message_id, email_id, date, timestamp, symbol, action, strategy,
                                               alert_price, market_price, setup, score, raw_payload, status,
                                               llm_decision, llm_playbook, created_at)
                VALUES (?, ?, ?, ?, ?, 'LONG', 'Swing', 112.0, 112.0, 'RR_SETUP', '70', '{}', 'PROCESSED',
                        'WATCH (card 70)', 'card playbook', ?)
                """,
                (f"msg-{sym}", f"mail-{sym}", today, datetime.now().isoformat(), sym, datetime.now().isoformat()),
            )
        ac.commit()

    payload = get_today()
    rows = {}
    for group in ("needs_you", "actionable_alerts", "stalking", "watch", "cut", "unmeasured", "closest_to_gate", "top_opportunities"):
        for item in payload.get(group) if payload.get(group) else []:
            tick = (item.get("ticker") or "").upper()
            if tick:
                rows[tick] = item

    bko = rows.get("BKO")
    assert bko is not None
    # Zone R:R at entry_high = (130-110)/(110-95) = 1.33
    assert bko["rr_zone_at_entry_high"] == 1.33
    # Breakout R:R at the trigger = (130-125)/(125-95) = 0.17
    assert bko["rr_breakout_at_trigger"] == 0.17
    assert bko["breakout_trigger_broken"] is False
    assert "zone R:R 1.33 @ $110.00" in bko["rr_paths_text"]
    assert "breakout R:R 0.17 @ $125.00" in bko["rr_paths_text"]

    brk = rows.get("BRK")
    assert brk is not None
    # Target 120 sits below the 125 trigger: the path is flagged broken, never blended.
    assert brk["breakout_trigger_broken"] is True
    assert brk["rr_breakout_at_trigger"] is None
    assert "broken" in brk["rr_paths_text"]


def monkeypatch_attr(obj, name, value):
    """Tiny stand-in for monkeypatch where a fixture parameter is not in scope."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        old = getattr(obj, name)
        setattr(obj, name, value)
        try:
            yield
        finally:
            setattr(obj, name, old)
    return _ctx()
