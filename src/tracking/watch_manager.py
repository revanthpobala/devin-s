"""
Watchlist & Trigger Alert State Manager (SQLite-backed).
Manages active stalking targets, tactical levels, live price states, and alert logs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config

logger = logging.getLogger(__name__)

DB_PATH = config.research_watch_db_path()
_db_lock = threading.RLock()


def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    return conn


def init_watch_db():
    """Initialize database tables if they do not exist."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_targets (
                    ticker TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    conviction INTEGER DEFAULT 5,
                    actionable BOOLEAN DEFAULT 1,
                    side TEXT DEFAULT 'LONG',
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    breakout_level REAL,
                    breakout_stop REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_structure TEXT,
                    options_summary TEXT,
                    options_actionable BOOLEAN DEFAULT 0,
                    options_entry_trigger TEXT,
                    invalidation_price REAL,
                    invalidation_condition TEXT,
                    invalidation_rationale TEXT,
                    last_price REAL,
                    distance_to_entry_pct REAL,
                    status TEXT DEFAULT 'STALKING',
                    last_alert_type TEXT,
                    last_alert_at TEXT,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT,
                    signal_pack INTEGER,
                    fade REAL,
                    action_long INTEGER,
                    ext_z REAL,
                    atr_at_signal REAL,
                    zone_rr_flags INTEGER,
                    rr_at_market_at_signal REAL
                )
                """
            )
            # Automatic column migrations for existing databases
            existing_cols = {col[1] for col in cursor.execute("PRAGMA table_info(watch_targets)").fetchall()}
            if "side" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN side TEXT DEFAULT 'LONG'")
            if "breakout_level" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN breakout_level REAL")
            if "breakout_stop" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN breakout_stop REAL")
            if "options_actionable" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN options_actionable BOOLEAN DEFAULT 0")
            if "options_entry_trigger" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN options_entry_trigger TEXT")
            if "is_active" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN is_active INTEGER DEFAULT 1")
            if "user_taken" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN user_taken INTEGER DEFAULT 0")
            if "suggestion_id" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN suggestion_id INTEGER")
            if "fill_price" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN fill_price REAL")
            if "quantity" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN quantity REAL DEFAULT 100")
            if "evaluation_verdict" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN evaluation_verdict TEXT")
            if "evaluation_playbook" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN evaluation_playbook TEXT")
            if "evaluation_time" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN evaluation_time TEXT")
            if "is_actionable_now" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN is_actionable_now INTEGER DEFAULT 0")
            if "thesis_id" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN thesis_id TEXT")
            if "created_date" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN created_date TEXT")
            if "expires_on" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN expires_on TEXT")
            if "signal_pack" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN signal_pack INTEGER")
            if "fade" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN fade REAL")
            if "action_long" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN action_long INTEGER")
            if "ext_z" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN ext_z REAL")
            if "atr_at_signal" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN atr_at_signal REAL")
            if "zone_rr_flags" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN zone_rr_flags INTEGER")
            if "rr_at_market_at_signal" not in existing_cols:
                cursor.execute("ALTER TABLE watch_targets ADD COLUMN rr_at_market_at_signal REAL")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS last_researched (
                    ticker TEXT PRIMARY KEY,
                    last_researched_date TEXT NOT NULL,
                    action_code INTEGER,
                    in_zone INTEGER,
                    rr_at_market REAL,
                    stop REAL,
                    target REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_targets_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    conviction INTEGER DEFAULT 5,
                    actionable BOOLEAN DEFAULT 1,
                    side TEXT DEFAULT 'LONG',
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    breakout_level REAL,
                    breakout_stop REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_structure TEXT,
                    options_summary TEXT,
                    options_actionable BOOLEAN DEFAULT 0,
                    options_entry_trigger TEXT,
                    invalidation_price REAL,
                    invalidation_condition TEXT,
                    invalidation_rationale TEXT,
                    status TEXT DEFAULT 'STALKING',
                    updated_at TEXT NOT NULL,
                    raw_json TEXT,
                    signal_pack INTEGER,
                    fade REAL,
                    action_long INTEGER,
                    ext_z REAL,
                    atr_at_signal REAL,
                    zone_rr_flags INTEGER,
                    rr_at_market_at_signal REAL
                )
                """
            )
            existing_hist_cols = {col[1] for col in cursor.execute("PRAGMA table_info(watch_targets_history)").fetchall()}
            for col_name, col_type in [
                ("signal_pack", "INTEGER"),
                ("fade", "REAL"),
                ("action_long", "INTEGER"),
                ("ext_z", "REAL"),
                ("atr_at_signal", "REAL"),
                ("zone_rr_flags", "INTEGER"),
                ("rr_at_market_at_signal", "REAL"),
            ]:
                if col_name not in existing_hist_cols:
                    cursor.execute(f"ALTER TABLE watch_targets_history ADD COLUMN {col_name} {col_type}")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    spot_price REAL NOT NULL,
                    triggered_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS superforecasting_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    model_type TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    target_date TEXT NOT NULL,
                    event_description TEXT NOT NULL,
                    predicted_probability REAL NOT NULL,
                    actual_outcome INTEGER,
                    brier_score REAL,
                    evaluation_price REAL,
                    evaluated_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, report_date, model_type, horizon_days, event_description)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS suggested_trades_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'LONG',
                    trade_type TEXT NOT NULL,
                    trade_structure TEXT NOT NULL,
                    trade_label TEXT NOT NULL,
                    entry_type TEXT DEFAULT 'LIMIT',
                    entry_price REAL,
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    tactical_stop REAL,
                    target_1 REAL,
                    target_2 REAL,
                    options_expiration TEXT,
                    long_strike REAL,
                    short_strike REAL,
                    target_debit REAL,
                    max_profit REAL,
                    max_loss REAL,
                    last_price REAL,
                    distance_to_entry_pct REAL,
                    status TEXT NOT NULL DEFAULT 'STALKING',
                    dollar_pnl REAL DEFAULT 0.0,
                    roc_pct REAL DEFAULT 0.0,
                    r_multiple REAL,
                    is_primary INTEGER DEFAULT 1,
                    outcome_notes TEXT,
                    evaluated_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, date, trade_type, trade_structure)
                )
                """
            )
            existing_audit_cols = {col[1] for col in cursor.execute("PRAGMA table_info(suggested_trades_audit)").fetchall()}
            if "is_primary" not in existing_audit_cols:
                cursor.execute("ALTER TABLE suggested_trades_audit ADD COLUMN is_primary INTEGER DEFAULT 1")
            if "r_multiple" not in existing_audit_cols:
                cursor.execute("ALTER TABLE suggested_trades_audit ADD COLUMN r_multiple REAL")
            conn.commit()


# Auto-initialize on import
init_watch_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def upsert_watch_target(data: Dict[str, Any]) -> None:
    """Insert or update a watch target from an extracted watch payload."""
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return

    shares_plan = data.get("shares_plan", {}) or {}
    options_plan = data.get("options_plan", {}) or {}
    invalidation = data.get("invalidation", {}) or {}

    side = str(data.get("side") or shares_plan.get("side") or "LONG").upper()
    breakout_level = shares_plan.get("breakout_level")
    breakout_stop = shares_plan.get("breakout_stop")

    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    geometry_reasons: List[str] = []
    try:
        from src.logic.level_validation import check_geometry

        entry_type = str(shares_plan.get("entry_type") or "LIMIT").upper()
        entry_low = float(shares_plan.get("entry_zone_low") or 0.0)
        entry_high = float(shares_plan.get("entry_zone_high") or 0.0)
        stop = float(shares_plan.get("tactical_stop") or 0.0)
        target_1 = float(shares_plan.get("target_1") or 0.0)
        target_2 = float(shares_plan.get("target_2") or 0.0)
        geometry_reasons = check_geometry(
            side=side,
            entry_type=entry_type,
            entry_low=entry_low,
            entry_high=entry_high,
            breakout_level=float(breakout_level or 0.0),
            stop=stop,
            t1=target_1,
            t2=target_2,
        )
    except Exception as e:
        logger.debug(f"[WATCH_GATE] {ticker} geometry pre-check raised: {e}")
        geometry_reasons.append(f"geometry pre-check exception: {e}")

    if geometry_reasons:
        failure_msg = "; ".join(geometry_reasons)
        logger.warning(
            f"[WATCH_GATE] {ticker} FAILED underlying geometry: {failure_msg} — NOT persisting to watch_targets."
        )
        try:
            from src.tracking.suggestions_ledger import log_rejected_plan

            log_rejected_plan(ticker, date_str, data, geometry_reasons)
        except Exception as e_log:
            logger.debug(f"[WATCH_GATE] Failed to log rejected plan for {ticker}: {e_log}")
        return

    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    created_date = str(data.get("created_date") or date_str)
    
    # 5 trading days expiry calculation
    try:
        cur_dt = datetime.strptime(created_date[:10], "%Y-%m-%d").date()
    except Exception:
        cur_dt = datetime.now().date()
    added_td = 0
    exp_cur = cur_dt
    while added_td < 5:
        exp_cur += timedelta(days=1)
        if exp_cur.weekday() < 5:
            added_td += 1
    expires_on = str(data.get("expires_on") or exp_cur.strftime("%Y-%m-%d"))
    thesis_id = str(data.get("thesis_id") or f"{ticker}_{created_date}")

    now = _now_iso()

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()

            # Check if active unexpired thesis already exists: do not overwrite for the same open thesis
            existing = cursor.execute(
                "SELECT date, thesis_id, expires_on, status, tactical_stop, entry_zone_low, entry_zone_high FROM watch_targets WHERE ticker = ?",
                (ticker,)
            ).fetchone()
            if existing:
                ex_dict = dict(existing)
                ex_thesis_id = ex_dict.get("thesis_id") or f"{ticker}_{ex_dict.get('date')}"
                ex_expires = ex_dict.get("expires_on") or ""
                ex_status = str(ex_dict.get("status") or "").upper()
                if ex_status in ("IN_TRADE", "IN_ZONE", "STALKING", "WATCH"):
                    # If same thesis_id or active and within expiry with matching key stop level, preserve thesis
                    stop_val = float(shares_plan.get("tactical_stop") or 0.0)
                    ex_stop_val = float(ex_dict.get("tactical_stop") or 0.0)
                    if ex_thesis_id == thesis_id or (ex_expires and date_str <= ex_expires and abs(stop_val - ex_stop_val) < 0.05):
                        logger.info(f"[watch_manager] Open thesis {ex_thesis_id} for {ticker} still active (expires {ex_expires}). Skipping overwrite.")
                        return

            from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow
            dw_candidate = data.get("datawindow") or data.get("dw") or data.get("_datawindow") or {}
            gw = gate_inputs_from_datawindow(dw_candidate) if dw_candidate else {}

            def _get_val(k, gw_k=None, conv=float):
                v = data.get(k)
                if v is None and gw_k:
                    v = gw.get(gw_k)
                if v is None:
                    return None
                try:
                    return conv(v)
                except (ValueError, TypeError):
                    return None

            sig_pack = _get_val("signal_pack", "signal_pack", int)
            fade_val = _get_val("fade", "fade_long", float)
            if fade_val is None and data.get("fade_long") is not None:
                try:
                    fade_val = float(data["fade_long"])
                except Exception:
                    pass
            act_long = _get_val("action_long", "action_long", int)
            ext_z_val = _get_val("ext_z", "ext_z_self", float)
            atr_sig = _get_val("atr_at_signal", "atr14", float)
            z_flags = _get_val("zone_rr_flags", None, int)
            if z_flags is None and dw_candidate:
                try:
                    z_raw = dw_candidate.get("Zone RR Flags Pack") or dw_candidate.get("zone_rr_flags")
                    z_flags = int(round(float(z_raw))) if z_raw is not None else None
                except Exception:
                    pass
            rr_mkt_sig = _get_val("rr_at_market_at_signal", "long_rr_at_market", float)

            # Re-verify gate actionability deterministically
            e_high_val = shares_plan.get("entry_zone_high") or shares_plan.get("entry_zone_low")
            gate_in = {
                "atr14": atr_sig,
                "fade_long": fade_val,
                "long_in_zone": 1.0 if (data.get("status") == "IN_ZONE" or data.get("in_zone")) else 0.0,
                "signal_pack": sig_pack,
                "action_long": act_long,
                "ext_z_self": ext_z_val,
                "price": data.get("spot") or data.get("last_price") or e_high_val,
                "long_rr_at_market": rr_mkt_sig,
                "long_stop_loss": shares_plan.get("tactical_stop"),
            }
            gate_ok, gate_fails = is_actionable(gate_in, {"side": side.lower(), "entry": e_high_val})
            is_no_entry = str(shares_plan.get("entry_type") or "").upper() == "NO_ENTRY"
            is_rejected = bool(data.get("level_gate_rejected"))
            db_actionable = 1 if (gate_ok and not is_no_entry and not is_rejected) else 0

            # Store compact datawindow subset in raw_json
            raw_payload = dict(data)
            raw_payload["signal_pack"] = sig_pack
            raw_payload["fade"] = fade_val
            raw_payload["action_long"] = act_long
            raw_payload["ext_z"] = ext_z_val
            raw_payload["atr_at_signal"] = atr_sig
            raw_payload["zone_rr_flags"] = z_flags
            raw_payload["rr_at_market_at_signal"] = rr_mkt_sig
            compact_dw = {
                "signal_pack": sig_pack,
                "fade_long": fade_val,
                "action_long": act_long,
                "ext_z_self": ext_z_val,
                "atr14": atr_sig,
                "zone_rr_flags": z_flags,
                "long_rr_at_market": rr_mkt_sig,
                "long_in_zone": gate_in["long_in_zone"],
            }
            if "datawindow" not in raw_payload:
                raw_payload["datawindow"] = compact_dw
            raw_json_str = json.dumps(raw_payload, default=str)

            cursor.execute(
                """
                INSERT INTO watch_targets (
                    ticker, date, verdict, conviction, actionable, side,
                    entry_type, entry_zone_low, entry_zone_high, breakout_level, breakout_stop,
                    tactical_stop, target_1, target_2, options_structure, options_summary,
                    options_actionable, options_entry_trigger,
                    invalidation_price, invalidation_condition, invalidation_rationale,
                    status, updated_at, raw_json, is_active, user_taken, suggestion_id,
                    thesis_id, created_date, expires_on,
                    signal_pack, fade, action_long, ext_z, atr_at_signal, zone_rr_flags, rr_at_market_at_signal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    date=excluded.date,
                    verdict=excluded.verdict,
                    conviction=excluded.conviction,
                    actionable=excluded.actionable,
                    side=excluded.side,
                    entry_type=excluded.entry_type,
                    entry_zone_low=excluded.entry_zone_low,
                    entry_zone_high=excluded.entry_zone_high,
                    breakout_level=excluded.breakout_level,
                    breakout_stop=excluded.breakout_stop,
                    tactical_stop=excluded.tactical_stop,
                    target_1=excluded.target_1,
                    target_2=excluded.target_2,
                    options_structure=excluded.options_structure,
                    options_summary=excluded.options_summary,
                    options_actionable=excluded.options_actionable,
                    options_entry_trigger=excluded.options_entry_trigger,
                    invalidation_price=excluded.invalidation_price,
                    invalidation_condition=excluded.invalidation_condition,
                    invalidation_rationale=excluded.invalidation_rationale,
                    status=CASE 
                        WHEN watch_targets.status = 'IN_TRADE' THEN watch_targets.status 
                        ELSE excluded.status 
                    END,
                    is_active=CASE
                        WHEN watch_targets.date != excluded.date OR (excluded.suggestion_id IS NOT NULL AND excluded.suggestion_id IS NOT watch_targets.suggestion_id) THEN 1
                        ELSE watch_targets.is_active
                    END,
                    user_taken=CASE
                        WHEN watch_targets.date != excluded.date OR (excluded.suggestion_id IS NOT NULL AND excluded.suggestion_id IS NOT watch_targets.suggestion_id) THEN 0
                        ELSE watch_targets.user_taken
                    END,
                    suggestion_id=COALESCE(excluded.suggestion_id, watch_targets.suggestion_id),
                    thesis_id=excluded.thesis_id,
                    created_date=excluded.created_date,
                    expires_on=excluded.expires_on,
                    updated_at=excluded.updated_at,
                    raw_json=excluded.raw_json,
                    signal_pack=excluded.signal_pack,
                    fade=excluded.fade,
                    action_long=excluded.action_long,
                    ext_z=excluded.ext_z,
                    atr_at_signal=excluded.atr_at_signal,
                    zone_rr_flags=excluded.zone_rr_flags,
                    rr_at_market_at_signal=excluded.rr_at_market_at_signal
                """,
                (
                    ticker,
                    date_str,
                    data.get("verdict", "STALK"),
                    data.get("conviction", 5),
                    db_actionable,
                    side,
                    shares_plan.get("entry_type", "LIMIT"),
                    shares_plan.get("entry_zone_low"),
                    shares_plan.get("entry_zone_high"),
                    breakout_level,
                    breakout_stop,
                    shares_plan.get("tactical_stop"),
                    shares_plan.get("target_1"),
                    shares_plan.get("target_2"),
                    options_plan.get("structure", "NONE"),
                    options_plan.get("summary", ""),
                    1 if (options_plan.get("actionable") or (options_plan.get("structure") not in ("NONE", "", None) and (options_plan.get("target_debit", 0) > 0 or options_plan.get("max_profit", 0) > 0))) else 0,
                    str(options_plan.get("entry_trigger") or "AT_FLOOR_LIMIT"),
                    invalidation.get("price_level"),
                    invalidation.get("condition", "DAILY_CLOSE_BELOW"),
                    invalidation.get("rationale", ""),
                    data.get("status", "WATCH"),
                    now,
                    raw_json_str,
                    data.get("suggestion_id"),
                    thesis_id,
                    created_date,
                    expires_on,
                    sig_pack,
                    fade_val,
                    act_long,
                    ext_z_val,
                    atr_sig,
                    z_flags,
                    rr_mkt_sig,
                ),
            )
            # Append to history table for immutable audit tracking
            cursor.execute(
                """
                INSERT INTO watch_targets_history (
                    ticker, date, verdict, conviction, actionable, side,
                    entry_type, entry_zone_low, entry_zone_high, breakout_level,
                    breakout_stop, tactical_stop, target_1, target_2,
                    options_structure, options_summary, options_actionable,
                    options_entry_trigger, invalidation_price, invalidation_condition,
                    invalidation_rationale, status, updated_at, raw_json,
                    signal_pack, fade, action_long, ext_z, atr_at_signal, zone_rr_flags, rr_at_market_at_signal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    data.get("date", datetime.now().strftime("%Y-%m-%d")),
                    data.get("verdict", "STALK"),
                    data.get("conviction", 5),
                    db_actionable,
                    side,
                    shares_plan.get("entry_type", "LIMIT"),
                    shares_plan.get("entry_zone_low"),
                    shares_plan.get("entry_zone_high"),
                    breakout_level,
                    breakout_stop,
                    shares_plan.get("tactical_stop"),
                    shares_plan.get("target_1"),
                    shares_plan.get("target_2"),
                    options_plan.get("structure", "NONE"),
                    options_plan.get("summary", ""),
                    1 if (options_plan.get("actionable") or (options_plan.get("structure") not in ("NONE", "", None) and (options_plan.get("target_debit", 0) > 0 or options_plan.get("max_profit", 0) > 0))) else 0,
                    str(options_plan.get("entry_trigger") or "AT_FLOOR_LIMIT"),
                    invalidation.get("price_level"),
                    invalidation.get("condition", "DAILY_CLOSE_BELOW"),
                    invalidation.get("rationale", ""),
                    data.get("status", "STALKING"),
                    now,
                    raw_json_str,
                    sig_pack,
                    fade_val,
                    act_long,
                    ext_z_val,
                    atr_sig,
                    z_flags,
                    rr_mkt_sig,
                ),
            )
            conn.commit()
            try:
                from src.ui.routes.watchlist import _WATCH_TARGETS_CACHE
                _WATCH_TARGETS_CACHE.clear()
            except Exception:
                pass
            logger.info(f"[{ticker}] Registered in Watchlist DB (Side: {side}, Verdict: {data.get('verdict')}, Status: {data.get('status')})")


def backfill_watch_targets_gate_inputs() -> int:
    """Re-derive gate inputs for active/unexpired rows in watch_targets from on-disk Data Windows.
    Rows with no Data Window stay unmeasured (None, never defaulted).
    Ensures actionable flag is derived from the deterministic gate, not LLM verdict.
    """
    import glob
    from src.logic.actionable_gate import is_actionable, gate_inputs_from_datawindow

    updated = 0
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT ticker, date, side, entry_type, entry_zone_low, entry_zone_high, tactical_stop,
                       target_1, signal_pack, fade, action_long, ext_z, atr_at_signal, zone_rr_flags,
                       rr_at_market_at_signal, actionable, status, last_alert_type, raw_json
                FROM watch_targets
                """
            ).fetchall()

            for r in rows:
                ticker = r["ticker"]
                date_str = r["date"]
                raw = {}
                if r["raw_json"]:
                    try:
                        raw = json.loads(r["raw_json"])
                    except Exception:
                        raw = {}

                # Look for datawindow in raw_json or on disk using config.BASE_DIR
                dw = raw.get("datawindow") or raw.get("_datawindow")
                if not dw:
                    # Check disk
                    candidates = list(
                        (config.BASE_DIR / "data" / "triage" / str(date_str)).glob(f"**/{ticker}*datawindow*.json")
                    ) + list(
                        (config.BASE_DIR / "data" / "raw" / str(date_str) / ticker).glob(f"*datawindow*.json")
                    ) + list(
                        (config.BASE_DIR / "data" / "triage").glob(f"**/{ticker}*datawindow*.json")
                    ) + list(
                        (config.BASE_DIR / "data" / "raw").glob(f"**/{ticker}/*datawindow*.json")
                    )
                    if candidates:
                        try:
                            with open(candidates[0], "r", encoding="utf-8") as f:
                                dw = json.load(f)
                        except Exception:
                            dw = None

                gw = gate_inputs_from_datawindow(dw) if dw else {}

                # Re-derive or keep existing (never default a missing value)
                sig_pack = gw.get("signal_pack") if gw.get("signal_pack") is not None else r["signal_pack"]
                fade_val = gw.get("fade_long") if gw.get("fade_long") is not None else r["fade"]
                act_long = gw.get("action_long") if gw.get("action_long") is not None else r["action_long"]
                ext_z_val = gw.get("ext_z_self") if gw.get("ext_z_self") is not None else r["ext_z"]
                atr_sig = gw.get("atr14") if gw.get("atr14") is not None else r["atr_at_signal"]
                z_flags = r["zone_rr_flags"]
                if dw:
                    try:
                        z_raw = dw.get("Zone RR Flags Pack") or dw.get("zone_rr_flags")
                        z_flags = int(round(float(z_raw))) if z_raw is not None else z_flags
                    except Exception:
                        pass
                rr_mkt = gw.get("long_rr_at_market") if gw.get("long_rr_at_market") is not None else r["rr_at_market_at_signal"]

                # Single shared gate evaluation
                side = str(r["side"] or "LONG").upper()
                e_px = r["entry_zone_high"] or r["entry_zone_low"]
                gate_in = {
                    "atr14": atr_sig,
                    "fade_long": fade_val,
                    "long_in_zone": 1.0 if (r["status"] == "IN_ZONE") else 0.0,
                    "signal_pack": sig_pack,
                    "action_long": act_long,
                    "ext_z_self": ext_z_val,
                    "price": e_px,
                    "long_rr_at_market": rr_mkt,
                    "long_stop_loss": r["tactical_stop"],
                }
                gate_ok, _fails = is_actionable(gate_in, {"side": side.lower(), "entry": e_px})
                is_no_entry = str(r["entry_type"] or "").upper() == "NO_ENTRY"
                is_rejected = bool(raw.get("level_gate_rejected"))
                target_act = 1 if (gate_ok and not is_no_entry and not is_rejected) else 0

                # Fix invalid last_alert_type if fired without gate clearance
                last_alert = r["last_alert_type"]
                if not gate_ok and last_alert == "ENTRY_ACTIONABLE_BUY":
                    last_alert = "ENTRY_TRIGGERED"

                curr_status = r["status"]
                if not dw and curr_status in ("STALKING", "WATCH", "UNMEASURED"):
                    curr_status = "AWAITING_MEASUREMENT"

                # Update raw_json
                raw["signal_pack"] = sig_pack
                raw["fade"] = fade_val
                raw["action_long"] = act_long
                raw["ext_z"] = ext_z_val
                raw["atr_at_signal"] = atr_sig
                raw["zone_rr_flags"] = z_flags
                raw["rr_at_market_at_signal"] = rr_mkt
                if dw:
                    raw["datawindow"] = {
                        "signal_pack": sig_pack,
                        "fade_long": fade_val,
                        "action_long": act_long,
                        "ext_z_self": ext_z_val,
                        "atr14": atr_sig,
                        "zone_rr_flags": z_flags,
                        "long_rr_at_market": rr_mkt,
                        "long_in_zone": gate_in["long_in_zone"],
                    }

                cursor.execute(
                    """
                    UPDATE watch_targets SET
                        signal_pack = ?,
                        fade = ?,
                        action_long = ?,
                        ext_z = ?,
                        atr_at_signal = ?,
                        zone_rr_flags = ?,
                        rr_at_market_at_signal = ?,
                        actionable = ?,
                        status = ?,
                        last_alert_type = ?,
                        raw_json = ?
                    WHERE ticker = ?
                    """,
                    (
                        sig_pack, fade_val, act_long, ext_z_val, atr_sig, z_flags,
                        rr_mkt, target_act, curr_status, last_alert, json.dumps(raw, default=str), ticker
                    )
                )

                # Also sync gate inputs into suggestions table if row exists
                cursor.execute(
                    """
                    UPDATE suggestions SET
                        signal_pack = ?,
                        fade = ?,
                        action_long = ?,
                        ext_z = ?,
                        atr_at_signal = ?,
                        rr_at_market_at_signal = ?
                    WHERE ticker = ? AND (date = ? OR date IS NULL)
                    """,
                    (sig_pack, fade_val, act_long, ext_z_val, atr_sig, rr_mkt, ticker, str(date_str)[:10])
                )
                updated += 1
            conn.commit()
    logger.info(f"[watch_manager] Backfilled gate inputs and re-evaluated actionability for {updated} watch targets.")
    return updated


def sweep_expired_watch_targets(days: int = 5, as_of_date: Optional[str] = None) -> int:
    """Expire STALKING and IN_ZONE targets older than `days` trading days or past `expires_on`."""
    now = _now_iso()
    today_dt = datetime.strptime(as_of_date[:10], "%Y-%m-%d").date() if as_of_date else datetime.now().date()
    today_str = today_dt.strftime("%Y-%m-%d")

    expired_count = 0
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ticker, date, created_date, expires_on, status 
                FROM watch_targets 
                WHERE status IN ('STALKING', 'IN_ZONE', 'WATCH')
                """
            )
            rows = cursor.fetchall()
            for r in rows:
                ticker = r["ticker"]
                exp_on = r["expires_on"]
                created_d_str = r["created_date"] or r["date"]
                is_expired = False

                if exp_on and today_str > str(exp_on)[:10]:
                    is_expired = True
                elif created_d_str:
                    try:
                        c_dt = datetime.strptime(str(created_d_str)[:10], "%Y-%m-%d").date()
                        t_days = 0
                        cur = c_dt
                        while cur < today_dt:
                            cur += timedelta(days=1)
                            if cur.weekday() < 5:
                                t_days += 1
                        if t_days >= days:
                            is_expired = True
                    except Exception:
                        pass

                if is_expired:
                    cursor.execute(
                        """
                        UPDATE watch_targets 
                        SET status = 'EXPIRED', is_active = 0, is_actionable_now = 0, updated_at = ?
                        WHERE ticker = ?
                        """,
                        (now, ticker),
                    )
                    cursor.execute(
                        """
                        INSERT INTO watch_alerts (ticker, date, trigger_type, message, spot_price, triggered_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ticker.upper(),
                            today_str,
                            "THESIS_EXPIRED",
                            f"[{ticker}] Stalking/In-Zone thesis expired after {days} trading days.",
                            0.0,
                            now,
                        ),
                    )
                    expired_count += 1
            if expired_count > 0:
                conn.commit()
                try:
                    from src.ui.routes.watchlist import _WATCH_TARGETS_CACHE

                    _WATCH_TARGETS_CACHE.clear()
                except Exception:
                    pass
                logger.info(f"[watch_manager] Swept and marked {expired_count} watch targets as EXPIRED.")
    return expired_count


def get_active_watch_targets() -> List[Dict[str, Any]]:
    """Return all active watch targets that are not terminated."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM watch_targets 
                WHERE status IN ('STALKING', 'IN_ZONE', 'IN_TRADE', 'TESTING_SUPPORT')
                  AND (verdict IS NULL OR verdict != 'REJECTED_BY_GATE')
                ORDER BY date DESC, conviction DESC
                """
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def get_all_watch_targets() -> List[Dict[str, Any]]:
    """Return all watch targets."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM watch_targets ORDER BY date DESC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def update_target_live_state(
    ticker: str,
    live_price: float,
    status: str,
    distance_to_entry_pct: Optional[float] = None,
    alert_type: Optional[str] = None,
    is_actionable_now: Optional[int] = None,
    evaluation_verdict: Optional[str] = None,
) -> None:
    """Update live price, distance, status, alert state, and actionability for a ticker."""
    now = _now_iso()
    status_upper = (status or "").upper()
    terminal_statuses = (
        "TARGET_HIT", "COMPLETED", "INVALIDATED", "STOP_BREACHED",
        "STOPPED", "MISSED_RUNAWAY", "EXPIRED", "REJECTED_BY_GATE"
    )
    if status_upper in terminal_statuses:
        if is_actionable_now is None:
            is_actionable_now = 0
        if evaluation_verdict is None:
            evaluation_verdict = "TARGET_HIT" if "TARGET" in status_upper else "STAND_ASIDE"

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            query_parts = ["last_price = ?", "status = ?", "distance_to_entry_pct = ?", "updated_at = ?"]
            params: list = [live_price, status, distance_to_entry_pct, now]
            if alert_type:
                query_parts.extend(["last_alert_type = ?", "last_alert_at = ?"])
                params.extend([alert_type, now])
            if is_actionable_now is not None:
                query_parts.append("is_actionable_now = ?")
                params.append(is_actionable_now)
            if evaluation_verdict is not None:
                query_parts.append("evaluation_verdict = ?")
                params.append(evaluation_verdict)
            params.append(ticker.upper())
            cursor.execute(
                f"UPDATE watch_targets SET {', '.join(query_parts)} WHERE ticker = ?",
                params,
            )
            conn.commit()


def log_trigger_alert(ticker: str, trigger_type: str, message: str, spot_price: float) -> None:
    """Log an alert event to the watch_alerts audit log."""
    now = _now_iso()
    date_str = datetime.now().strftime("%Y-%m-%d")
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO watch_alerts (ticker, date, trigger_type, message, spot_price, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ticker.upper(), date_str, trigger_type, message, spot_price, now),
            )
            conn.commit()
    logger.info(f"🚨 [WATCH ALERT] [{ticker}] {trigger_type}: {message} (Price: ${spot_price:.2f})")


def get_recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent alert events."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM watch_alerts ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


def upsert_superforecasting_prediction(p: Dict[str, Any]) -> None:
    """Insert or update a superforecasting prediction."""
    now = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO superforecasting_audits (
                    ticker, report_date, model_type, horizon_days, target_date,
                    event_description, predicted_probability, actual_outcome,
                    brier_score, evaluation_price, evaluated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, report_date, model_type, horizon_days, event_description) DO UPDATE SET
                    predicted_probability = excluded.predicted_probability,
                    actual_outcome = COALESCE(superforecasting_audits.actual_outcome, excluded.actual_outcome),
                    brier_score = COALESCE(superforecasting_audits.brier_score, excluded.brier_score),
                    evaluation_price = COALESCE(superforecasting_audits.evaluation_price, excluded.evaluation_price),
                    evaluated_at = COALESCE(superforecasting_audits.evaluated_at, excluded.evaluated_at)
                """,
                (
                    p["ticker"].upper(),
                    p["report_date"],
                    p["model_type"],
                    int(p["horizon_days"]),
                    p["target_date"],
                    p["event_description"],
                    float(p["predicted_probability"]),
                    p.get("actual_outcome"),
                    p.get("brier_score"),
                    p.get("evaluation_price"),
                    p.get("evaluated_at"),
                    now,
                ),
            )
            conn.commit()


def get_superforecasting_stats() -> Dict[str, Any]:
    """Calculate aggregate calibration stats and Brier scores by model type."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()

            # Model A (Proprietary Pine) stats
            cursor.execute(
                """
                SELECT 
                    COUNT(*) as total_predictions,
                    COUNT(CASE WHEN actual_outcome IS NOT NULL THEN 1 END) as resolved_predictions,
                    AVG(brier_score) as mean_brier_score,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN (predicted_probability >= 0.5 AND actual_outcome = 1) OR (predicted_probability < 0.5 AND actual_outcome = 0) THEN 1.0 ELSE 0.0 END) ELSE NULL END) as directional_accuracy,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN actual_outcome = 1 THEN 1.0 ELSE 0.0 END) ELSE NULL END) as actual_hit_rate
                FROM superforecasting_audits
                WHERE model_type = 'MODEL_A_PINE'
                """
            )
            row_a = cursor.fetchone()
            model_a_stats = {
                "total": row_a["total_predictions"] if row_a else 0,
                "resolved": row_a["resolved_predictions"] if row_a else 0,
                "brier_score": round(row_a["mean_brier_score"], 4) if row_a and row_a["mean_brier_score"] is not None else 0.0,
                "accuracy_pct": round((row_a["actual_hit_rate"] if row_a and row_a["actual_hit_rate"] is not None else (row_a["directional_accuracy"] or 0.0)) * 100, 1) if row_a else 0.0,
                "directional_pct": round((row_a["directional_accuracy"] or 0.0) * 100, 1) if row_a else 0.0,
            }

            # Model B (Independent Quant) stats
            cursor.execute(
                """
                SELECT 
                    COUNT(*) as total_predictions,
                    COUNT(CASE WHEN actual_outcome IS NOT NULL THEN 1 END) as resolved_predictions,
                    AVG(brier_score) as mean_brier_score,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN (predicted_probability >= 0.5 AND actual_outcome = 1) OR (predicted_probability < 0.5 AND actual_outcome = 0) THEN 1.0 ELSE 0.0 END) ELSE NULL END) as directional_accuracy,
                    AVG(CASE WHEN actual_outcome IS NOT NULL THEN (CASE WHEN actual_outcome = 1 THEN 1.0 ELSE 0.0 END) ELSE NULL END) as actual_hit_rate
                FROM superforecasting_audits
                WHERE model_type = 'MODEL_B_INDEPENDENT'
                """
            )
            row_b = cursor.fetchone()
            model_b_stats = {
                "total": row_b["total_predictions"] if row_b else 0,
                "resolved": row_b["resolved_predictions"] if row_b else 0,
                "brier_score": round(row_b["mean_brier_score"], 4) if row_b and row_b["mean_brier_score"] is not None else 0.0,
                "accuracy_pct": round((row_b["actual_hit_rate"] if row_b and row_b["actual_hit_rate"] is not None else (row_b["directional_accuracy"] or 0.0)) * 100, 1) if row_b else 0.0,
                "directional_pct": round((row_b["directional_accuracy"] or 0.0) * 100, 1) if row_b else 0.0,
            }

            # Recent predictions list
            cursor.execute(
                """
                SELECT id, ticker, report_date, model_type, horizon_days, target_date,
                       event_description, predicted_probability, actual_outcome, brier_score, evaluated_at
                FROM superforecasting_audits
                ORDER BY id DESC LIMIT 20
                """
            )
            recent_rows = [dict(r) for r in cursor.fetchall()]

            return {
                "model_a": model_a_stats,
                "model_b": model_b_stats,
                "recent_audits": recent_rows,
            }


def get_last_researched(ticker: str) -> Optional[Dict[str, Any]]:
    """Get last researched metrics for a ticker from last_researched table."""
    sym = ticker.strip().upper()
    if not sym:
        return None
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM last_researched WHERE ticker = ?", (sym,)
            ).fetchone()
            return dict(row) if row else None


def record_last_researched(
    ticker: str,
    date_str: str,
    action_code: int = 0,
    in_zone: int = 0,
    rr_at_market: float = 0.0,
    stop: float = 0.0,
    target: float = 0.0,
) -> None:
    """Record or update research state in last_researched table."""
    sym = ticker.strip().upper()
    if not sym:
        return
    now = _now_iso()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO last_researched (
                    ticker, last_researched_date, action_code, in_zone, rr_at_market, stop, target, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    last_researched_date = excluded.last_researched_date,
                    action_code = excluded.action_code,
                    in_zone = excluded.in_zone,
                    rr_at_market = excluded.rr_at_market,
                    stop = excluded.stop,
                    target = excluded.target,
                    updated_at = excluded.updated_at
                """,
                (sym, date_str, int(action_code or 0), int(in_zone or 0), float(rr_at_market or 0.0), float(stop or 0.0), float(target or 0.0), now),
            )
            conn.commit()


def ensure_universe_prepopulated() -> int:
    """Pre-populate watch_targets with the full Schwab 1000 constituent universe (~983 tickers).

    1. Loads all constituents from All_tickrs/Schwab_1000_Index®_Index_Constituents.csv.
    2. Syncs any existing deep research reports from reports/ so researched tickers have their
       latest research timestamp, levels, verdict, conviction, and status.
    3. Inserts unresearched constituents with status='UNRESEARCHED' and updated_at='1970-01-01T00:00:00'.
    Returns total count of targets in watch_targets.
    """
    csv_path = config.BASE_DIR / "All_tickrs" / "Schwab_1000_Index®_Index_Constituents.csv"
    if not csv_path.exists():
        logger.warning(f"Schwab 1000 constituents CSV not found at {csv_path}")
        return 0

    import csv
    constituents = {}
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sym = (row.get("Ticker") or "").strip().upper()
                if sym and sym not in constituents:
                    constituents[sym] = {
                        "name": (row.get("Name") or "").strip(),
                        "weight": float(row.get("IndexWeighting") or 0.0),
                    }
    except Exception as e:
        logger.error(f"Error loading constituents CSV: {e}")
        return 0

    # Extract all latest deep research reports from disk
    rep_root = config.BASE_DIR / "reports"
    reports_on_disk = {}
    if rep_root.exists():
        for d in sorted(rep_root.iterdir()):
            if not d.is_dir():
                continue
            d_str = d.name
            for f in d.glob("*_summary.md"):
                sym = f.name.replace("_summary.md", "").upper()
                mtime = f.stat().st_mtime
                if sym not in reports_on_disk or mtime > reports_on_disk[sym]["mtime"]:
                    reports_on_disk[sym] = {"date": d_str, "mtime": mtime}

    extracted_cards = {}
    if reports_on_disk:
        try:
            from src.ui.routes.research import extract_report_card
            for sym, meta in reports_on_disk.items():
                card = extract_report_card(meta["date"], sym)
                if card:
                    extracted_cards[sym] = card
        except Exception as e:
            logger.warning(f"Error extracting report cards for universe prepopulation: {e}")

    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            existing_rows = {r["ticker"]: dict(r) for r in cursor.execute("SELECT * FROM watch_targets").fetchall()}

            # Upsert all researched cards
            for sym, card in extracted_cards.items():
                date_str = card.get("date") or ""
                verdict = (card.get("verdict") or "STALK").upper()
                conviction = card.get("conviction") or 5
                ez = card.get("entry_zone") or [None, None]
                ez_low = ez[0] if len(ez) > 0 else None
                ez_high = ez[1] if len(ez) > 1 else None
                stop = card.get("tactical_stop")
                t1 = card.get("target_1")
                t2 = card.get("target_2")
                opt = card.get("options_summary") or ""
                res_time = card.get("researched_at") or f"{date_str}T12:00:00"
                status = "IN_ZONE" if verdict == "ENTER" else "STALKING"

                if sym in existing_rows:
                    old_st = existing_rows[sym].get("status")
                    if old_st in ("IN_TRADE", "TARGET_HIT", "MISSED_RUNAWAY", "STOP_BREACHED"):
                        status = old_st

                cursor.execute(
                    """
                    INSERT INTO watch_targets (
                        ticker, date, verdict, conviction, actionable, side,
                        entry_type, entry_zone_low, entry_zone_high, tactical_stop,
                        target_1, target_2, options_summary, status, updated_at, is_active
                    ) VALUES (?, ?, ?, ?, 1, 'LONG', 'LIMIT', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(ticker) DO UPDATE SET
                        date=excluded.date,
                        verdict=excluded.verdict,
                        conviction=excluded.conviction,
                        entry_zone_low=COALESCE(excluded.entry_zone_low, watch_targets.entry_zone_low),
                        entry_zone_high=COALESCE(excluded.entry_zone_high, watch_targets.entry_zone_high),
                        tactical_stop=COALESCE(excluded.tactical_stop, watch_targets.tactical_stop),
                        target_1=COALESCE(excluded.target_1, watch_targets.target_1),
                        target_2=COALESCE(excluded.target_2, watch_targets.target_2),
                        options_summary=COALESCE(excluded.options_summary, watch_targets.options_summary),
                        updated_at=excluded.updated_at,
                        is_active=1
                    """,
                    (sym, date_str, verdict, conviction, ez_low, ez_high, stop, t1, t2, opt, status, res_time),
                )

            # Insert unresearched constituents
            for sym, meta in constituents.items():
                if sym not in extracted_cards and sym not in existing_rows:
                    name = meta.get("name") or sym
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO watch_targets (
                            ticker, date, verdict, conviction, actionable, side,
                            entry_type, status, options_summary, updated_at, is_active
                        ) VALUES (?, '', 'UNRESEARCHED', 0, 0, 'LONG', 'LIMIT', 'UNRESEARCHED', ?, '1970-01-01T00:00:00', 1)
                        """,
                        (sym, f"Schwab 1000 ({name})"),
                    )

            conn.commit()
            total = cursor.execute("SELECT count(1) FROM watch_targets").fetchone()[0]
            logger.info(f"[WATCHLIST] Universe pre-populated. Total constituents tracked: {total}")
            return total


