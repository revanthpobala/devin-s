"""
Watchlist Trigger & Price Alert Engine
Polls live real-time quotes against extracted research report levels,
evaluates state transitions (STALKING -> IN_ZONE -> TARGET_HIT / INVALIDATED),
emits desktop/console alerts, and syncs live status to Google Sheets.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from src import config
from src.clients.price_client import get_current_price
from src.clients.tastytrade_client import TastytradeClient
from src.logic.report_level_extractor import extract_watch_levels_from_report
from src.tracking.level_engine import (
    bars_high_20d_and_adv20,
    get_engine_state,
    rearm_for_new_day,
    volume_pace,
)
from src.tracking.sheets_tracker import SheetsTracker
from src.tracking.alert_db import get_eastern_now
from src.tracking.watch_manager import (
    get_active_watch_targets,
    get_all_watch_targets,
    has_level_alert_today,
    log_trigger_alert,
    sweep_expired_watch_targets,
    sync_active_suggestions_to_watch_targets,
    update_target_live_state,
    upsert_watch_target,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (WatchAlerts) %(message)s"
)
yf_log = logging.getLogger("yfinance")
yf_log.setLevel(logging.CRITICAL)
yf_log.propagate = False
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class SyncResult(int):
    """An int subclass representing the count of indexed targets, enriched with sync metadata."""
    def __new__(
        cls,
        count: int,
        indexed: Optional[List[str]] = None,
        rejected: Optional[List[Dict[str, Any]]] = None,
        tt_alerts_count: int = 0,
        tt_synced: Optional[List[str]] = None,
    ):
        obj = super().__new__(cls, count)
        obj.indexed = indexed or []
        obj.rejected = rejected or []
        obj.tt_alerts_count = tt_alerts_count
        obj.tt_synced = tt_synced or []
        return obj


def sync_reports_to_watchlist(
    target_date: Optional[str] = None,
    target_ticker: Optional[str] = None,
    sync_tastytrade: Optional[bool] = None,
) -> int:
    """Discover all generated research reports and index their structured levels into SQLite & Tastytrade.
    
    NOTE: Tastytrade cloud quote alert generation is strictly gated. By default, alerts are NOT generated
    after deep research unless explicitly enabled via sync_tastytrade=True or ENABLE_TASTYTRADE_ALERTS=1.
    """
    if sync_tastytrade is None:
        sync_tastytrade = os.getenv("ENABLE_TASTYTRADE_ALERTS", "0").lower() in ("1", "true", "yes")

    if target_ticker and not target_date:
        safe_sym = target_ticker.strip().upper().replace(":", "_")
        reports_base = config.BASE_DIR / "reports"
        if reports_base.exists():
            for d in sorted([p for p in reports_base.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True):
                if (d / f"{safe_sym}_summary.md").exists() or (d / f"{safe_sym}_arbitration.md").exists():
                    target_date = d.name
                    break
        if not target_date:
            raw_base = config.BASE_DIR / "data" / "raw"
            if raw_base.exists():
                for d in sorted([p for p in raw_base.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)], reverse=True):
                    if (d / safe_sym).exists():
                        target_date = d.name
                        break

    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    reports_dir = config.BASE_DIR / "reports" / date_str
    count = 0
    indexed_list: List[str] = []
    rejected_list: List[Dict[str, Any]] = []
    tt_synced_list: List[str] = []
    tt_alerts_total = 0

    if target_ticker:
        tickers = [target_ticker.strip().upper()]
    elif reports_dir.exists():
        # Find all summary or arbitration files
        tickers = set()
        for p in reports_dir.glob("*_summary.md"):
            t = p.name.replace("_summary.md", "").upper()
            tickers.add(t)
        for p in reports_dir.glob("*_arbitration.md"):
            t = p.name.replace("_arbitration.md", "").upper()
            tickers.add(t)
    else:
        tickers = set()

    tasty_client = TastytradeClient() if sync_tastytrade else None

    logger.info(f"Indexing research levels for {len(tickers)} ticker(s) on date '{date_str}'...")
    for t in sorted(tickers):
        data = extract_watch_levels_from_report(t, date_str)
        if data:
            # Load ticker's same-date data window: data/raw/<report date>/<T>/<T>_datawindow.json
            dw_dict = {}
            safe_sym = t.replace(":", "_")
            dw_paths = [config.BASE_DIR / "data" / "raw" / date_str / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "triage" / date_str / "_DEEP_RESEARCH" / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "triage" / date_str / "force" / safe_sym / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "raw" / date_str / f"{safe_sym}_datawindow.json", config.BASE_DIR / "data" / "raw" / date_str / f"{safe_sym}_triage.json"]
            dw_file = next((p for p in dw_paths if p.exists()), dw_paths[0])
            if dw_file.exists():
                try:
                    dw_dict = json.loads(dw_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[{t}] Error reading DW JSON {dw_file}: {e}")

            if not dw_dict:
                logger.warning(f"[{t}] No same-date Data Window found at data/raw/{date_str}/{safe_sym}/{safe_sym}_datawindow.json. Gate cannot run fully — SKIPPING upsert.")
                # Fallback to minimal dict with spot price so validate_levels can still run
                dw_dict = {"Close": data.get("current_price") or data.get("spot") or 0.0}

            from src.logic.level_validation import validate_levels
            plan = {
                **data.get("shares_plan", {}),
                "options_plan": data.get("options_plan", {}),
                "ticker": t,
                "date": date_str,
                "side": data.get("side", "LONG"),
                "setup_lane": data.get("setup_lane") or data.get("lane") or dw_dict.get("setup_lane"),
                "spot": data.get("current_price") or data.get("spot") or dw_dict.get("Close"),
                "source": data.get("source", "judge"),
                "is_judge": str(data.get("source", "judge")).lower() in ("judge", "arbitration"),
            }
            try:
                _ok, _reasons = validate_levels(plan, dw_dict, data.get("side", "LONG"), ticker=t, date_str=date_str)
            except Exception as e_gate:
                logger.warning(f"[{t}] Gate validation crashed: {e_gate} — SKIPPING upsert.")
                rejected_list.append({"ticker": t, "reasons": [f"Gate validation error: {e_gate}"]})
                continue

            # C1's keep-and-rebuild must reach the stored plan: `plan` is a copy of
            # shares_plan, and upsert_watch_target persists data["shares_plan"]. The
            # trigger is never zeroed — it is kept with its targets rebuilt above it.
            if plan.get("breakout_demoted_reason") and isinstance(data.get("shares_plan"), dict):
                _sp = data["shares_plan"]
                _sp["breakout_level"] = plan.get("breakout_level") or _sp.get("breakout_level") or 0.0
                _sp["breakout_stop"] = plan.get("breakout_stop") or _sp.get("breakout_stop") or 0.0
                _sp["target_1"] = plan.get("target_1", _sp.get("target_1"))
                _sp["target_2"] = plan.get("target_2", _sp.get("target_2"))
                _sp["targets_rebuilt"] = bool(plan.get("targets_rebuilt"))
                _sp["targets_rebuilt_basis"] = plan.get("targets_rebuilt_basis")
                _sp["breakout_demoted_reason"] = plan["breakout_demoted_reason"]

            if not _ok:
                data["verdict"] = "REJECTED_BY_GATE"
            verdict_str = str(data.get("verdict") or "").upper()
            source_str = str(data.get("source") or "").lower()

            if (
                not _ok
                or source_str == "fallback"
                or verdict_str in ("NO_LEVELS", "REJECTED", "REJECTED_BY_GATE")
            ):
                fail_reasons = list(_reasons)
                if source_str == "fallback":
                    fail_reasons.append("source is fallback regex")
                if verdict_str in ("NO_LEVELS", "REJECTED", "REJECTED_BY_GATE"):
                    fail_reasons.append(f"verdict is {verdict_str}")
                logger.warning(
                    f"[{t}] Plan rejected: {'; '.join(fail_reasons)} — SKIPPING upsert to watch_targets."
                )
                from src.tracking.suggestions_ledger import log_rejected_plan
                log_rejected_plan(t, date_str, plan, fail_reasons)
                rejected_list.append({"ticker": t, "reasons": fail_reasons})
                continue

            if not data.get("suggestion_id"):
                try:
                    from src.tracking.watch_manager import _get_connection, _db_lock
                    with _db_lock:
                        with _get_connection() as sconn:
                            scur = sconn.cursor()
                            srow = scur.execute(
                                "SELECT id FROM suggestions WHERE ticker = ? AND date = ? AND source = 'judge' ORDER BY id DESC LIMIT 1",
                                (t, date_str)
                            ).fetchone()
                            if srow:
                                data["suggestion_id"] = srow[0]
                            else:
                                from src.tracking.suggestions_ledger import append_suggestion
                                from src.logic.actionable_gate import gate_inputs_from_datawindow
                                sp = data.get("shares_plan", {})
                                dw_data = data.get("datawindow") or data.get("_datawindow") or {}
                                gate_in = gate_inputs_from_datawindow(dw_data)
                                atr_at_sig = data.get("atr_at_signal") or gate_in.get("atr14")
                                rr_at_sig = data.get("rr_at_market_at_signal") or gate_in.get("long_rr_at_market")
                                sig_pack = data.get("signal_pack") or gate_in.get("signal_pack")
                                fade_val = data.get("fade") or gate_in.get("fade_long")
                                ext_z_val = data.get("ext_z") or gate_in.get("ext_z_self")
                                stop_val = sp.get("tactical_stop") or gate_in.get("long_stop_loss")
                                entry_val = sp.get("entry_zone_high") or gate_in.get("price")
                                stop_width_atr = None
                                if atr_at_sig and atr_at_sig > 0 and stop_val and entry_val:
                                    stop_width_atr = round(abs(entry_val - stop_val) / atr_at_sig, 3)
                                raw_sig = data.get("pb_funnel")
                                if raw_sig is None and sig_pack is not None:
                                    try:
                                        raw_sig = int(bool(int(round(float(sig_pack))) & 32))
                                    except Exception:
                                        raw_sig = None
                                sid = append_suggestion({
                                    "ticker": t,
                                    "date": date_str,
                                    "source": "judge",
                                    "side": data.get("side", "LONG"),
                                    "entry_type": sp.get("entry_type", "LIMIT"),
                                    "entry_low": sp.get("entry_zone_low"),
                                    "entry_high": sp.get("entry_zone_high"),
                                    "breakout_level": sp.get("breakout_level"),
                                    "stop": stop_val,
                                    "target_1": sp.get("target_1"),
                                    "target_2": sp.get("target_2"),
                                    "planned_rr": sp.get("rr_ratio"),
                                    "verdict": data.get("verdict"),
                                    "gate_status": data.get("gate_status", "PASS"),
                                    "setup_lane": data.get("setup_lane") or data.get("lane"),
                                    "kind": data.get("kind", "NEW"),
                                    "atr_at_signal": atr_at_sig,
                                    "rr_at_market_at_signal": rr_at_sig,
                                    "pb_funnel": raw_sig,
                                    "signal_pack": sig_pack,
                                    "fade": fade_val,
                                    "ext_z": ext_z_val,
                                    "stop_width_atr": stop_width_atr,
                                    "notes": f"Judge directive: {data.get('verdict')}",
                                })
                                if sid and sid > 0:
                                    data["suggestion_id"] = sid

                                data["signal_pack"] = sig_pack
                                data["fade"] = fade_val
                                data["ext_z"] = ext_z_val
                                data["action_long"] = gate_in.get("action_long")
                                data["atr_at_signal"] = atr_at_sig
                                data["rr_at_market_at_signal"] = rr_at_sig
                                data["zone_rr_flags"] = dw_data.get("Zone RR Flags Pack") or dw_data.get("zone_rr_flags")
                                data["datawindow"] = gate_in
                except Exception:
                    pass

            upsert_watch_target(data)
            count += 1
            indexed_list.append(t)

            # Sync to Tastytrade cloud alerts: ONLY after deep research levels pass the validation gate
            if tasty_client and _ok and data.get("verdict") != "REJECTED_BY_GATE":
                try:
                    tt_alerts = tasty_client.sync_watch_levels(data)
                    if tt_alerts:
                        logger.info(f"[{t}] Registered {len(tt_alerts)} cloud alert(s) in Tastytrade.")
                        tt_alerts_total += len(tt_alerts)
                        tt_synced_list.append(t)
                except Exception as e_tt:
                    logger.warning(f"[{t}] Tastytrade alert sync failed: {e_tt}")
            elif tasty_client and not _ok:
                logger.info(f"[{t}] Skipped Tastytrade cloud alert registration because level validation failed.")

    return SyncResult(
        count,
        indexed=indexed_list,
        rejected=rejected_list,
        tt_alerts_count=tt_alerts_total,
        tt_synced=tt_synced_list,
    )


# ---------------------------------------------------------------------------
# A. Universal level engine + B. At-market re-score (plan/no-lost-movers)
# ---------------------------------------------------------------------------

def _f(val) -> float:
    try:
        f = float(val)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _live_datawindow(t: Dict[str, Any]) -> Dict[str, Any]:
    """The most recent compact data window for a row, from raw_json. {} when absent."""
    raw = t.get("raw_json")
    if not raw:
        return {}
    try:
        rj = json.loads(raw) if isinstance(raw, str) else raw
        return rj.get("datawindow") or rj.get("_datawindow") or {}
    except Exception:
        return {}


def _plan_view_from_row(t: Dict[str, Any]) -> Dict[str, Any]:
    """The row's plan in the vocabulary build_armed_levels() reads."""
    sp = t.get("shares_plan") or {}
    return {
        "entry_type": sp.get("entry_type") or t.get("entry_type") or "LIMIT",
        "entry_zone_low": t.get("entry_zone_low") or sp.get("entry_zone_low"),
        "entry_zone_high": t.get("entry_zone_high") or sp.get("entry_zone_high"),
        "breakout_level": t.get("breakout_level") or sp.get("breakout_level"),
        "breakout_stop": t.get("breakout_stop") or sp.get("breakout_stop"),
        "tactical_stop": t.get("tactical_stop") or sp.get("tactical_stop"),
        "target_1": t.get("target_1") or sp.get("target_1"),
        "target_2": t.get("target_2") or sp.get("target_2"),
        "invalidation_price": t.get("invalidation_price") or sp.get("invalidation_price"),
    }


_BARS_STATS_CACHE: Dict[str, Tuple[Optional[float], Optional[float]]] = {}


def _bars_stats_for_ticker(
    ticker: str, dw: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[float], Optional[float]]:
    """(prior 20-day high, ADV20) for a ticker, one fetch per cycle.

    The live Data Window carries the 20-day high when present; ADV20 only exists on
    daily bars. Both bar-derived numbers use the LAST COMPLETED sessions — the plan
    measured today's print against them, so the window must not stop at the setup
    date (that made RVOL a stale denominator when a thesis was days old). Absent
    means absent — a missing 20d high simply arms no HIGH_20D level, and a missing
    ADV20 leaves crosses tagged unmeasured, never dropped.
    """
    key = (ticker.upper(), dw_high20_key(dw))
    if key in _BARS_STATS_CACHE:
        return _BARS_STATS_CACHE[key]
    dw_high = 0.0
    if isinstance(dw, dict):
        for k in ("high_20d", "High 20D", "20d high", "20d_high", "high_20d_high", "Highest 20D"):
            v = _f(dw.get(k))
            if v > 0:
                dw_high = v
                break
    bars_high, adv20 = bars_high_20d_and_adv20(ticker)
    _BARS_STATS_CACHE[key] = (dw_high if dw_high > 0 else bars_high, adv20)
    return _BARS_STATS_CACHE[key]


def dw_high20_key(dw: Optional[Dict[str, Any]]) -> float:
    """The Data Window's 20-day high, or 0.0 when absent — the cache key's second part,
    so a later call carrying a different DW value is not shadowed by an earlier one."""
    if not isinstance(dw, dict):
        return 0.0
    for k in ("high_20d", "High 20D", "20d high", "20d_high", "high_20d_high", "Highest 20D"):
        v = _f(dw.get(k))
        if v > 0:
            return v
    return 0.0


def _cooldown_elapsed(state, kind: str, now_et) -> bool:
    """A4 cooldown: a same-level alert may not re-fire inside LEVEL_ALERT_COOLDOWN_MIN."""
    lv = state.by_kind(kind)
    if not lv or not lv.last_fired_at:
        return True
    try:
        last = datetime.fromisoformat(lv.last_fired_at)
        return (now_et - last) >= timedelta(minutes=config.LEVEL_ALERT_COOLDOWN_MIN)
    except Exception:
        return True


def _emit_level_alert(ticker: str, trigger_type: str, message: str, price: float) -> bool:
    """Log a level/re-score alert unless the same (ticker, level, day) already fired.

    The dedupe key is derived from the same clock that files the row (log_trigger_alert
    writes date = datetime.now()), so the COUNT and the INSERT can never disagree about
    what "today" is — an ET-vs-local skew would otherwise break the dedupe (or suppress
    the wrong day) in the late-night window.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        if has_level_alert_today(ticker, trigger_type, date_str):
            return False
    except Exception as e:
        # A dedupe read failure must not suppress the alert itself.
        logger.debug(f"[{ticker}] level dedupe check failed ({e}); emitting {trigger_type}.")
    try:
        log_trigger_alert(ticker, trigger_type, message, price)
        return True
    except Exception as e:
        logger.debug(f"[{ticker}] level alert {trigger_type} failed: {e}")
        return False


def _level_cross_message(ticker: str, cross, t: Dict[str, Any], rvol: Optional[float]) -> str:
    """The cross alert: what crossed, at what price, on what volume, and the judgment.

    A trigger cross states the R:R at market to the plan's (possibly rebuilt) T1/T2 and
    whether the lane is MEASURED or UNMEASURED — never a bare price print.
    """
    side = str(t.get("side") or "LONG").upper()
    vol_txt = f"RVOL {rvol:.2f}" if rvol is not None else "RVOL unmeasured"
    verb = "crossed up through" if (side == "LONG") == (cross.kind not in ("STOP", "INVALIDATION")) else "crossed down through"
    head = (
        f"[{ticker}] {cross.kind} {verb} ${cross.price:.2f} "
        f"(${cross.prev_price:.2f} -> ${cross.curr_price:.2f}) | {vol_txt}"
    )

    pv = _plan_view_from_row(t)
    stop = _f(pv.get("tactical_stop")) or _f(pv.get("invalidation_price"))
    t1 = _f(pv.get("target_1"))
    t2 = _f(pv.get("target_2"))
    px = _f(cross.curr_price)
    rr_bits = []
    if stop > 0 and px > stop:
        if t1 > px:
            rr_bits.append(f"R:R to T1 ${t1:.2f} = {(t1 - px) / (px - stop):.2f}")
        elif t1 > 0:
            rr_bits.append(f"T1 ${t1:.2f} already above the print")
        if t2 > px:
            rr_bits.append(f"R:R to T2 ${t2:.2f} = {(t2 - px) / (px - stop):.2f}")
        elif t2 > 0:
            rr_bits.append(f"T2 ${t2:.2f} already above the print")
    rr_txt = (" | " + "; ".join(rr_bits)) if rr_bits else ""

    lane_txt = ""
    if cross.kind in ("ZONE_TOP", "BREAKOUT", "HIGH_20D") or rr_bits:
        try:
            lane_label = _runaway_rule_label(runaway_rule_for([cross.kind]))
            lane_txt = f" | lane {lane_label['label']}"
        except Exception:
            lane_txt = " | lane UNMEASURED"

    judge_txt = ""
    if cross.kind in ("ZONE_TOP", "BREAKOUT", "HIGH_20D"):
        try:
            from src.tracking.rr_config import min_rr

            res = _rescore_at_market(ticker, px, t, side, get_eastern_now(), [cross.kind])
            judge_txt = (
                f" | at market: {res['outcome']} "
                f"(best R:R {res['live_rr']:.2f}@mkt vs {min_rr():.2f} floor)"
            )
        except Exception:
            judge_txt = ""

    return f"{head}{rr_txt}{lane_txt}{judge_txt} | level armed for a re-test"


_RULE_LABELS_PATH = config.BASE_DIR / "data" / "research" / "rule_labels.json"
_RULE_LABELS_TTL_S = 900
_rule_labels_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# Which runaway rule a crossing maps to (E1's three rules decide the wording).
_CROSS_TO_RULE = {
    "HIGH_20D": "high_20d_break",
    "BREAKOUT": "zone_top_market",
    "ZONE_TOP": "zone_top_market",
}


def runaway_rule_for(cross_kinds: List[str], reached_target: bool = False) -> str:
    """The E1 rule a crossing belongs to; the corpus decides that rule's wording."""
    if reached_target:
        return "post_t1_reentry"
    for kind in ("HIGH_20D", "BREAKOUT", "ZONE_TOP"):
        if kind in (cross_kinds or []):
            return _CROSS_TO_RULE[kind]
    return "zone_top_market"


def _runaway_rule_label(rule: str = "zone_top_market") -> Dict[str, Any]:
    """E1/E2: the replay corpus decides a rule's label and whether it may say "actionable".

    ``rule_labels.json`` is written by ``scripts/research/replay_runaway_rules.py --write-labels``.
    A rule whose honest replay passes (n, win rate, expectancy, fire-rate) gets to say
    actionable; a rule that fails — or has no corpus, or an unreadable one — is watch-only
    and its alerts carry the UNMEASURED/measured-negative reason. Fail-closed in every
    direction, so no alert can claim more than the corpus measured.
    """
    import time as _time

    now = _time.time()
    if now - _rule_labels_cache["ts"] < _RULE_LABELS_TTL_S and _rule_labels_cache["data"] is not None:
        data = _rule_labels_cache["data"]
    else:
        data = {}
        try:
            if _RULE_LABELS_PATH.exists():
                raw = json.loads(_RULE_LABELS_PATH.read_text(encoding="utf-8"))
                data = raw.get("rules") or {}
        except Exception as e:
            logger.debug(f"[E2] rule_labels unreadable ({e}); every lane stays watch-only.")
        _rule_labels_cache["ts"] = now
        _rule_labels_cache["data"] = data

    rule_meta = data.get(rule) or {}
    allowed = bool(rule_meta.get("actionable_wording_allowed")) and rule_meta.get("verdict") == "MEASURED"
    return {
        "rule": rule,
        "label": "MEASURED" if allowed else "UNMEASURED",
        "actionable_allowed": allowed,
        "source": "replay_corpus" if rule_meta else "absent",
        "note": rule_meta.get("note"),
    }


def _rescore_at_market(
    ticker: str, live_price: float, t: Dict[str, Any], side: str, now_et, cross_kinds: List[str]
) -> Dict[str, Any]:
    """B1/B2: re-score a running name at the live price.

    Math reuses zone_arrival_evaluator's live R:R ((t1 - price) / (price - stop))
    and the single gate is_actionable / gate_inputs_from_datawindow. The running
    lane sits above the zone by definition, so the in-zone bit is the one gate
    input that can never hold for a runner: it is the MEASURED lane's requirement,
    and every other gate reason must clear. A runaway or breakout buy is therefore
    UNMEASURED (B4) — it never borrows the in-zone PB lane's measured edge.
    """
    from src.logic.actionable_gate import gate_inputs_from_datawindow, is_actionable
    from src.tracking.rr_config import min_rr

    pv = _plan_view_from_row(t)
    stop = _f(pv.get("tactical_stop")) or _f(pv.get("invalidation_price"))
    t1 = _f(pv.get("target_1"))
    t2 = _f(pv.get("target_2"))
    entry_high = _f(pv.get("entry_zone_high"))
    entry_low = _f(pv.get("entry_zone_low"))

    live_rr = 0.0
    if 0 < stop < live_price and t1 > live_price:
        live_rr = round((t1 - live_price) / (live_price - stop), 2)

    dw = _live_datawindow(t)
    gate_in = gate_inputs_from_datawindow(dw) if dw else {
        "atr14": t.get("atr_at_signal"),
        "fade_long": t.get("fade"),
        "long_in_zone": None,
        "signal_pack": t.get("signal_pack"),
        "action_long": t.get("action_long"),
        "ext_z_self": t.get("ext_z"),
        "price": live_price,
        "long_rr_at_market": None,
        "long_stop_loss": stop,
    }
    gate_in["price"] = live_price
    gate_in["long_rr_at_market"] = live_rr
    gate_in["long_stop_loss"] = stop
    in_zone = bool(entry_low > 0 and entry_low <= live_price <= entry_high)
    gate_in["long_in_zone"] = 1.0 if in_zone else 0.0

    _ok, gate_reasons = is_actionable(gate_in, {"side": "long", "entry": live_price})
    running_reasons = [r for r in gate_reasons if "zone" not in r.lower()]

    floor = min_rr()
    gates_clear = not running_reasons
    stop_is_real = 0 < stop < live_price
    actionable = gates_clear and stop_is_real and live_rr >= floor

    lane_label = _runaway_rule_label(runaway_rule_for(cross_kinds))
    lane = lane_label["label"]
    gates_ok = gates_clear and stop_is_real and live_rr >= floor
    # E2: the corpus decides who may say "actionable". A gate pass on an unmeasured (or
    # measured-negative) rule is a watch-only alert with the reason stated, never a buy.
    actionable = bool(gates_ok and lane_label["actionable_allowed"])
    outcome = "RUNNING_ACTIONABLE" if actionable else "RUNNING_NO_ROOM"
    crosses_txt = ",".join(cross_kinds) if cross_kinds else "above-zone"

    if actionable:
        message = (
            f"BUY 100 {ticker} at market-${live_price:.2f} | stop ${stop:.2f} | "
            f"T1 ${t1:.2f} | R:R {live_rr:.2f}@mkt | lane {lane} | trigger {crosses_txt}"
        )
    elif gates_ok:
        zone_txt = f"${entry_low:.2f}-${entry_high:.2f}" if entry_low > 0 else "no stored zone"
        reason = lane_label.get("note") or "no replay corpus yet"
        message = (
            f"[{ticker}] ran: gate clears at market (R:R {live_rr:.2f}@mkt, stop ${stop:.2f} below price) "
            f"but lane {lane} ({lane_label['rule']}: {reason}) — watch-only, wait for pullback to "
            f"{zone_txt} or a new level. Level stays armed; no buy instruction."
        )
    else:
        zone_txt = f"${entry_low:.2f}-${entry_high:.2f}" if entry_low > 0 else "no stored zone"
        message = (
            f"[{ticker}] ran, no room: price ${live_price:.2f} above the plan "
            f"(R:R {live_rr:.2f}@mkt vs {floor:.2f} floor; gates: {'; '.join(running_reasons) or 'clear'}) "
            f"— wait for pullback to {zone_txt} or a new level. Level stays armed."
        )
    return {
        "outcome": outcome,
        "lane": lane,
        "rule": lane_label["rule"],
        "gates_ok": gates_ok,
        "label_allows_actionable": lane_label["actionable_allowed"],
        "live_rr": live_rr,
        "stop": stop,
        "target_1": t1,
        "target_2": t2,
        "gate_reasons": running_reasons,
        "message": message,
    }


def _requeue_for_local_triage(ticker: str, t: Dict[str, Any], live_price: float, today_str: str) -> bool:
    """B3: a name that completed T1 without a fill is re-queued for local triage on
    the live bar so levels refresh, and dispatched into a free research slot."""
    try:
        from src.tracking.alert_db import queue_for_research

        date_str = str(t.get("date") or today_str)[:10]
        queue_for_research(
            symbol=ticker,
            date_str=date_str,
            setup=None,
            source="watch_runaway",
            reason=f"T1 reached without a fill at ${live_price:.2f} — levels need refresh",
            force=True,
        )
    except Exception as e:
        logger.debug(f"[{ticker}] runaway re-queue failed: {e}")
        return False
    try:
        from src.screener.continuous_screener_daemon import _daemon_instance

        if _daemon_instance and _daemon_instance.is_slot_available():
            _daemon_instance.dispatch_candidate_research(
                ticker, str(t.get("date") or today_str)[:10], candidate_dict={"symbol": ticker}
            )
    except Exception as e:
        logger.debug(f"[{ticker}] runaway dispatch skipped: {e}")
    return True


def _collect_armed_coverage(existing_syms: set) -> List[Dict[str, Any]]:
    """A5: arm every name, not 16.

    The armed universe is active watch_targets UNION the dossiers written in the
    last 21 sessions UNION the screener candidates. Screener candidates reach the
    engine through their dossiers (a candidate with no levels has nothing to arm);
    dossier names carry their long_plan levels straight into the engine with no
    watch_targets row required. Placeholder rows (universe storage, not a thesis)
    are excluded, and a newer session's plan wins over an older one.
    """
    try:
        base = config.BASE_DIR / "data"
        if not base.exists():
            return []
        session_dirs = []
        for sub in ("raw", "triage"):
            sub_root = base / sub
            if not sub_root.exists():
                continue
            session_dirs.extend(d for d in sub_root.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name))
        recent = sorted({d.name for d in session_dirs if d.name <= datetime.now().strftime("%Y-%m-%d")}, reverse=True)[:21]
        scan_dirs = [
            d for d in session_dirs if d.name in recent
        ]

        placeholder_tickers = set()
        try:
            from src.tracking.watch_manager import get_all_watch_targets, is_placeholder_row
            for row in get_all_watch_targets():
                if is_placeholder_row(row):
                    placeholder_tickers.add(str(row.get("ticker") or "").upper())
        except Exception:
            pass

        armed: Dict[str, Dict[str, Any]] = {}
        for d in sorted(scan_dirs, key=lambda p: p.name, reverse=True):
            for thesis_path in sorted(d.glob("**/*_thesis.json"), reverse=True):
                try:
                    data = json.loads(thesis_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                sym = str(data.get("ticker") or data.get("symbol") or "").upper().strip()
                if not sym or sym in existing_syms or sym in placeholder_tickers or sym in armed:
                    continue
                triage = data.get("triage") or {}
                long_p = triage.get("long_plan") or data.get("long_plan") or {}
                if not long_p or not (_f(long_p.get("stop")) > 0 and (_f(long_p.get("target")) > 0 or _f(long_p.get("target_1")) > 0)):
                    continue
                # Gate inputs that actually exist in the thesis triage are mapped so the
                # at-market re-score's gate reasons are precise instead of claiming every
                # input is missing. signal_pack / ext_z / atr14 are NOT synthesised from
                # derived facts — absent means absent, the gate fails closed on them.
                action_long = triage.get("action_long")
                fade_long = triage.get("fade_long")
                armed[sym] = {
                    "ticker": sym,
                    "date": d.name,
                    "side": str(triage.get("side") or "LONG").upper(),
                    "entry_zone_low": None,
                    "entry_zone_high": None,
                    "breakout_level": None,
                    "breakout_stop": None,
                    "tactical_stop": _f(long_p.get("stop")),
                    "target_1": _f(long_p.get("target") or long_p.get("target_1")),
                    "target_2": _f(long_p.get("target_2")),
                    "invalidation_price": None,
                    "action_long": action_long if action_long is not None else None,
                    "fade": fade_long if fade_long is not None else None,
                    "status": "STALKING",
                    "verdict": triage.get("triage") or "STALK",
                    "raw_json": json.dumps({"datawindow": triage.get("datawindow_signals") or triage.get("dw") or {}}),
                }
        if armed:
            logger.info(f"[A5 coverage] Arming {len(armed)} unstored dossier name(s) with live levels.")
        return list(armed.values())
    except Exception as e:
        logger.debug(f"A5 coverage collection failed: {e}")
        return []


def _process_coverage_row(
    c: Dict[str, Any], live_price: float, now_et, volumes: Dict[str, float]
) -> None:
    """A5: evaluate one armed-but-unstored name (a dossier from the last 21 sessions).

    Same engine, same re-score, same dedupe as a stored row; the only difference is
    that there is no watch_targets row to update, so the outcome lives in the alert
    log and the forward ledger only.
    """
    ticker = c["ticker"]
    side = str(c.get("side") or "LONG").upper()
    today_str = now_et.strftime("%Y-%m-%d")
    date_str = str(c.get("date") or "")[:10] or None

    plan_identity = str(c.get("thesis_id") or f"{ticker}_{date_str or ''}")
    dw_compact = _live_datawindow(c)
    _high, adv20 = _bars_stats_for_ticker(ticker, dw_compact)
    state = get_engine_state(
        ticker, plan=_plan_view_from_row(c), plan_identity=plan_identity,
        side=side, high_20d=_high, last_price=None,
    )
    rearm_for_new_day(state.levels, today_str)
    rvol = volume_pace(volumes.get(ticker), adv20)
    crosses = state.detect_crosses(live_price, rvol=rvol)
    cross_kinds: List[str] = []
    for _c in crosses:
        state.last_price = live_price
        if not _c.volume_confirmed:
            continue
        if not _cooldown_elapsed(state, _c.kind, now_et):
            continue
        _emit_level_alert(
            ticker, _c.trigger_type, _level_cross_message(ticker, _c, c, rvol), live_price
        )
        state.mark_fired(_c.kind, when=now_et)
        cross_kinds.append(_c.kind)
    state.last_price = live_price

    pv = _plan_view_from_row(c)
    target_1 = _f(pv.get("target_1"))
    target_2 = _f(pv.get("target_2"))
    entry_low = _f(pv.get("entry_zone_low"))
    entry_high = _f(pv.get("entry_zone_high"))
    tactical_stop = _f(pv.get("tactical_stop"))
    hit_t1 = target_1 > 0 and live_price >= target_1
    hit_t2 = target_2 > 0 and live_price >= target_2
    above_zone = side == "LONG" and (
        (entry_high > 0 and live_price > entry_high)
        or (entry_high <= 0 and entry_low <= 0 and tactical_stop > 0 and live_price > tactical_stop * 1.10)
    )
    if not above_zone and not (hit_t1 or hit_t2):
        return

    _apply_running_rescore(
        ticker, live_price, c, side, now_et, cross_kinds, today_str,
        reached_target=bool(hit_t1 or hit_t2),
    )
    logger.info(f"[{ticker}] (coverage) Spot: ${live_price:.2f} | crossings: {cross_kinds or 'none'}")


def _apply_running_rescore(
    ticker: str,
    live_price: float,
    t: Dict[str, Any],
    side: str,
    now_et,
    cross_kinds: List[str],
    today_str: str,
    reached_target: bool,
) -> Dict[str, Any]:
    """B: apply the at-market re-score and report the outcome.

    RUNNING_ACTIONABLE when the gate clears, the stop is a real level below price
    and R:R at market clears the floor (the name goes IN_TRADE). Otherwise the name
    "ran, no room": it stays armed and waits for a pullback or a new level. When a
    target was reached WITHOUT a fill, the outcome additionally carries the
    informational TARGET_HIT_NO_FILL note and re-queues local triage (B3) so the
    levels refresh — never a terminal status either way.

    Returns dict: new_status, alert_fired, is_actionable_now, eval_verdict, outcome.
    """
    res = _rescore_at_market(ticker, live_price, t, side, now_et, cross_kinds)
    out = {
        "new_status": "STALKING",
        "alert_fired": None,
        "is_actionable_now": 0,
        "eval_verdict": "STAND_ASIDE",
        "outcome": res["outcome"],
    }

    # E2/B4: the rule fired (gate + R:R at market). Forward-log the observation either way —
    # that is how an UNMEASURED lane graduates — but only a corpus-measured rule may say
    # "actionable"; everything else is watch-only wording.
    if res["gates_ok"]:
        _forward_log_runaway(ticker, res, cross_kinds)

    if res["outcome"] == "RUNNING_ACTIONABLE":
        out["new_status"] = "IN_TRADE"
        out["alert_fired"] = "RUNNING_ACTIONABLE"
        out["is_actionable_now"] = 1
        out["eval_verdict"] = "TAKE"
        _emit_level_alert(ticker, "RUNNING_ACTIONABLE", res["message"], live_price)
        return out

    # No room at market.
    if reached_target:
        pv = _plan_view_from_row(t)
        tgt = _f(pv.get("target_2")) if _f(pv.get("target_2")) and live_price >= _f(pv.get("target_2")) else _f(pv.get("target_1"))
        if _emit_level_alert(
            ticker, "TARGET_HIT_NO_FILL",
            f"[{ticker}] Target ${tgt:.2f} reached without a fill (price ${live_price:.2f}) — "
            f"ran, no room at market; levels re-queued for local triage, thesis stays alive.",
            live_price,
        ):
            out["alert_fired"] = "TARGET_HIT_NO_FILL"
        _requeue_for_local_triage(ticker, t, live_price, today_str)
    else:
        if _emit_level_alert(ticker, "RUNNING_NO_ROOM", res["message"], live_price):
            out["alert_fired"] = "RUNNING_NO_ROOM"
    return out


def _forward_log_runaway(ticker: str, res: Dict[str, Any], cross_kinds: List[str]) -> None:
    """B4: a runaway/breakout buy is UNMEASURED. Forward-log it as its own rule so the
    lane can only claim an edge once the honest replay corpus has measured it."""
    try:
        from src.tracking.forward_log import record_forward_observation

        record_forward_observation(
            "RUNAWAY_AT_MARKET", ticker,
            {
                "rr_at_market": res["live_rr"],
                "stop": res["stop"],
                "target_1": res["target_1"],
                "target_2": res.get("target_2"),
                "crosses": cross_kinds,
                "lane": res["lane"],
            },
        )
    except Exception:
        pass


def evaluate_watch_cycle(sync_sheets: bool = True) -> List[Dict[str, Any]]:
    """Run one single price polling and trigger evaluation cycle across all active watch targets."""
    # The 20-day stats cache is per cycle: one fetch per ticker per poll, so a slow
    # day's ADV20 and 20-day high can never go stale across the daemon's lifetime.
    _BARS_STATS_CACHE.clear()
    # 1. Automatically sync all active PASS suggestions (last 21 days) into watch_targets
    try:
        sync_active_suggestions_to_watch_targets(lookback_days=21)
    except Exception as e_sync:
        logger.debug(f"Sync active suggestions to watch targets exception: {e_sync}")

    # 2. Sweep expired targets (21 days lookback aligned with suggestions)
    try:
        now_et = get_eastern_now()
        sweep_expired_watch_targets(days=21, as_of_date=now_et.strftime("%Y-%m-%d"))
    except Exception as e_swp:
        logger.debug(f"Sweep expired watch targets exception: {e_swp}")
    targets = get_active_watch_targets()
    if not targets:
        logger.info("No active watch targets in SQLite DB.")
        return []

    logger.info(f"Evaluating {len(targets)} armed name(s) through the level engine...")
    updated_targets = []

    # A5: the armed universe is every name with a recent dossier, not just stored rows.
    coverage = _collect_armed_coverage({str(t["ticker"]).upper() for t in targets if t.get("ticker")})

    target_syms = [t["ticker"] for t in targets if t.get("ticker")]
    coverage_syms = [c["ticker"] for c in coverage]
    from src.clients.quote_router import quote_router

    # Offload watchlist stalking to Surveillance Channel (Yahoo Direct REST / Alpaca) - zero Schwab calls
    q_batch = quote_router.get_watchlist_quotes_batch(target_syms + coverage_syms)
    prices: Dict[str, float] = {
        sym: qd.last_price for sym, qd in q_batch.items() if qd and qd.last_price > 0
    }
    volumes: Dict[str, float] = {
        sym: _f(getattr(qd, "volume", 0) or 0) for sym, qd in q_batch.items() if qd
    }

    for t in targets:
        ticker = t["ticker"]
        side = str(t.get("side") or "LONG").upper()
        entry_low = t.get("entry_zone_low")
        entry_high = t.get("entry_zone_high")
        breakout_level = t.get("breakout_level")
        tactical_stop = t.get("tactical_stop")
        target_1 = t.get("target_1")
        target_2 = t.get("target_2")
        inv_price = t.get("invalidation_price") or tactical_stop
        old_status = t.get("status", "STALKING")

        # 1. Fetch live quote (from parallel pre-fetch or cached spot)
        live_price = prices.get(ticker) or get_current_price(ticker)
        if not live_price or live_price <= 0:
            logger.warning(f"[{ticker}] Unable to fetch live price; using cached spot.")
            live_price = t.get("last_price") or t.get("spot_price") or 0.0

        if not live_price or live_price <= 0:
            continue

        # 2. Compute Distance to Entry Zone
        distance_pct = None
        if entry_low and entry_high:
            if side == "LONG":
                if live_price > entry_high:
                    distance_pct = round(((live_price - entry_high) / entry_high) * 100, 2)
                elif live_price < entry_low:
                    if inv_price and live_price > inv_price:
                        distance_pct = 0.0
                    else:
                        distance_pct = round(((live_price - entry_low) / entry_low) * 100, 2)
                else:
                    distance_pct = 0.0
            else:  # SHORT
                if live_price < entry_low:
                    distance_pct = round(((entry_low - live_price) / entry_low) * 100, 2)
                elif live_price > entry_high:
                    if inv_price and live_price < inv_price:
                        distance_pct = 0.0
                    else:
                        distance_pct = round(((entry_high - live_price) / entry_high) * 100, 2)
                else:
                    distance_pct = 0.0

        # ── Phase 1 (A1/A2): record armed-level cross events ────────────────
        # Every armed level on this name is checked, one alert per (ticker, level, day),
        # with an N-minute cooldown. These events feed the decision matrix below; they
        # are not suppressed by it.
        now_et = get_eastern_now()
        today_str = now_et.strftime("%Y-%m-%d")
        # The daily-close invalidation rule (an intraday wick is a floor probe, not a
        # thesis kill) still needs the clock, and so does the after-close re-arm. The
        # breakout path itself no longer has any after-close gate (A3).
        is_after_close = (now_et.hour > 16) or (now_et.hour == 16 and now_et.minute >= 0)
        plan_identity = str(t.get("thesis_id") or f"{ticker}_{str(t.get('date') or '')[:10]}")
        dw_compact = _live_datawindow(t)
        high_20d, adv20 = _bars_stats_for_ticker(ticker, dw_compact)
        state = get_engine_state(
            ticker,
            plan=_plan_view_from_row(t),
            plan_identity=plan_identity,
            side=side,
            high_20d=high_20d,
            last_price=t.get("last_price"),
        )
        rearm_for_new_day(state.levels, today_str)
        rvol = volume_pace(volumes.get(ticker), adv20)
        crosses = state.detect_crosses(live_price, rvol=rvol)
        cross_kinds: List[str] = []
        for _c in crosses:
            state.last_price = live_price
            if not _c.volume_confirmed:
                logger.debug(f"[{ticker}] {_c.kind} cross held: RVOL {_c.rvol} below 1.0 floor")
                continue
            if not _cooldown_elapsed(state, _c.kind, now_et):
                continue
            _emit_level_alert(
                ticker, _c.trigger_type, _level_cross_message(ticker, _c, t, rvol), live_price
            )
            state.mark_fired(_c.kind, when=now_et)
            cross_kinds.append(_c.kind)
        state.last_price = live_price

        # A4: the poll price is the close proxy — a level whose price closed back through
        # it re-arms immediately (intraday), so tomorrow's session can fire it again and a
        # failed breakout is recognised the moment price gives the level back.
        state.rearm_on_close(live_price)

        # 3. Evaluate State Transitions & Alerts
        new_status = "STALKING"
        alert_fired = None

        # Check conditions
        inv_cond = str(t.get("invalidation_condition") or "DAILY_CLOSE_BELOW").upper()
        is_stop_breached = False
        is_testing_floor = False

        if inv_price and inv_price > 0:
            if side == "LONG":
                if live_price <= inv_price:
                    # Invalidation requires market close: intraday wicks are testing floor
                    if not is_after_close:
                        is_testing_floor = True
                    elif "CLOSE" in inv_cond and live_price > (inv_price * 0.99):
                        is_testing_floor = True
                    else:
                        is_stop_breached = True
            elif side == "SHORT":
                if live_price >= inv_price:
                    if not is_after_close:
                        is_testing_floor = True
                    elif "CLOSE" in inv_cond and live_price < (inv_price * 1.01):
                        is_testing_floor = True
                    else:
                        is_stop_breached = True

        hit_t2 = False
        if target_2 and target_2 > 0:
            if side == "LONG" and live_price >= target_2:
                hit_t2 = True
            elif side == "SHORT" and live_price <= target_2:
                hit_t2 = True

        hit_t1 = False
        if target_1 and target_1 > 0:
            if side == "LONG" and live_price >= target_1:
                hit_t1 = True
            elif side == "SHORT" and live_price <= target_1:
                hit_t1 = True

        hit_breakout = False
        if breakout_level and breakout_level > 0:
            # A3: no after-close precondition. The level engine fires the cross the
            # moment price trades through the breakout and the B re-score decides
            # the outcome; a midday breakout is no longer invisible.
            if side == "LONG" and live_price >= breakout_level:
                hit_breakout = True
            elif side == "SHORT" and live_price <= breakout_level:
                hit_breakout = True

        # Check Entry Zone: strictly entry_low <= live_price <= entry_high (no 1% buffer, no stop-to-zone gap)
        hit_in_zone = False
        in_proximity_zone = False
        if entry_low and entry_high and entry_low > 0 and entry_high > 0:
            if entry_low <= live_price <= entry_high:
                hit_in_zone = True

        # A. Invalidation / Hard Stop Breach Check
        if is_stop_breached:
            new_status = "INVALIDATED"
            if old_status != "INVALIDATED":
                alert_fired = "STOP_BREACHED"
                msg = f"[{ticker}] Price ${live_price:.2f} breached invalidation level (${inv_price:.2f}). Thesis dead."
                log_trigger_alert(ticker, alert_fired, msg, live_price)

        # A2. Floor Probe / Testing Support Check (intraday wicks on DAILY_CLOSE rules)
        elif is_testing_floor:
            new_status = "TESTING_SUPPORT"
            if old_status != "TESTING_SUPPORT":
                alert_fired = "TESTING_SUPPORT"
                msg = f"[{ticker}] Structural floor probe: Price ${live_price:.2f} testing support (${inv_price:.2f}). Invalidation requires Daily Close Below."
                log_trigger_alert(ticker, alert_fired, msg, live_price)

        # B. Breakout Entry Trigger Check (the B re-score decides the outcome)
        elif hit_breakout and old_status in ("STALKING", "IN_ZONE"):
            _rr = _apply_running_rescore(
                ticker, live_price, t, side, now_et, cross_kinds, today_str,
                reached_target=bool(hit_t1 or hit_t2),
            )
            new_status = _rr["new_status"]
            alert_fired = _rr["alert_fired"]

        # C. Target Reached Check (Differentiate IN_TRADE vs STALKING with bar verification; IN_ZONE is NOT filled)
        elif hit_t2:
            if old_status in ("IN_TRADE",):
                new_status = "TARGET_HIT"
                if old_status != "TARGET_HIT":
                    alert_fired = "TARGET_2_REACHED"
                    msg = f"[{ticker}] Price ${live_price:.2f} reached Target 2 (${target_2:.2f})! Full profit target met."
                    log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                # Check bar extremes before declaring runaway
                from src.tracking.execution_validator import evaluate_setup_lifecycle
                eval_res = evaluate_setup_lifecycle(
                    ticker=ticker,
                    setup_date=str(t.get("date") or ""),
                    side=side,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop_loss=inv_price,
                    target_1=target_1,
                    target_2=target_2,
                    live_price=live_price,
                    current_status=old_status,
                )
                if eval_res["was_filled"]:
                    new_status = "TARGET_HIT"
                    if old_status != "TARGET_HIT":
                        alert_fired = "TARGET_2_REACHED"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 2 (${target_2:.2f}) after filling entry (${eval_res['fill_price']:.2f})! Full profit target met."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)
                else:
                    # B2: a target reached WITHOUT a fill is never terminal. Re-score at
                    # market (the MISSED_RUNAWAY replacement) and, when the name ran,
                    # re-queue local triage on the live bar so levels refresh.
                    _rr = _apply_running_rescore(
                        ticker, live_price, t, side, now_et, cross_kinds, today_str, reached_target=True
                    )
                    new_status = _rr["new_status"]
                    alert_fired = _rr["alert_fired"]

        elif hit_t1:
            if old_status in ("IN_TRADE",):
                new_status = "TARGET_HIT"
                if old_status != "TARGET_HIT":
                    alert_fired = "TARGET_1_REACHED"
                    msg = f"[{ticker}] Price ${live_price:.2f} reached Target 1 (${target_1:.2f}). Consider trimming."
                    log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                from src.tracking.execution_validator import evaluate_setup_lifecycle
                eval_res = evaluate_setup_lifecycle(
                    ticker=ticker,
                    setup_date=str(t.get("date") or ""),
                    side=side,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop_loss=inv_price,
                    target_1=target_1,
                    target_2=target_2,
                    live_price=live_price,
                    current_status=old_status,
                )
                if eval_res["was_filled"]:
                    new_status = "TARGET_HIT"
                    if old_status != "TARGET_HIT":
                        alert_fired = "TARGET_1_REACHED"
                        msg = f"[{ticker}] Price ${live_price:.2f} reached Target 1 (${target_1:.2f}) after filling entry (${eval_res['fill_price']:.2f}). Consider trimming."
                        log_trigger_alert(ticker, alert_fired, msg, live_price)
                else:
                    # B2: a target reached WITHOUT a fill is never terminal. Re-score at
                    # market (the MISSED_RUNAWAY replacement) and, when the name ran,
                    # re-queue local triage on the live bar so levels refresh.
                    _rr = _apply_running_rescore(
                        ticker, live_price, t, side, now_et, cross_kinds, today_str, reached_target=True
                    )
                    new_status = _rr["new_status"]
                    alert_fired = _rr["alert_fired"]

        # D. Entry Zone Stalking Trigger Check (With Real-Time On-Arrival Local Evaluation)
        elif hit_in_zone:
            new_status = "IN_ZONE"
            # Trigger On-Arrival Real-Time Local Evaluation & Tactical Triage only on status transition
            if old_status != "IN_ZONE":
                try:
                    from src.logic.zone_arrival_evaluator import evaluate_target_on_zone_arrival
                    eval_res = evaluate_target_on_zone_arrival(ticker, live_price, t)
                    if eval_res.get("is_actionable_now"):
                        # Dedupe: one push per (ticker, setup onset)
                        onset_d = str(t.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
                        already_pushed = False
                        try:
                            with _db_lock:
                                with _get_connection() as conn:
                                    cur = conn.cursor()
                                    r_cnt = cur.execute(
                                        "SELECT COUNT(*) FROM watch_alerts WHERE ticker = ? AND date = ? AND trigger_type IN ('ENTRY_ACTIONABLE_BUY', 'ENTRY_ACTIONABLE_SHORT')",
                                        (ticker, onset_d)
                                    ).fetchone()
                                    if r_cnt and r_cnt[0] > 0:
                                        already_pushed = True
                        except Exception:
                            pass

                        if not already_pushed:
                            alert_fired = "ENTRY_ACTIONABLE_BUY" if side == "LONG" else "ENTRY_ACTIONABLE_SHORT"
                            
                            # Options line from plan + live quote
                            opt_p = t.get("options_plan") or {}
                            if not opt_p and t.get("raw_json"):
                                try:
                                    raw_j = json.loads(t["raw_json"])
                                    opt_p = raw_j.get("options_plan") or {}
                                except Exception:
                                    pass

                            opt_line = ""
                            if opt_p and opt_p.get("structure") and opt_p.get("structure") != "NONE":
                                struct = opt_p.get("structure")
                                ls = opt_p.get("long_strike")
                                ss = opt_p.get("short_strike")
                                exp = opt_p.get("expiration") or ""
                                strikes_str = f"${float(ls):.2f}" if (ls and not ss) else (f"${float(ls):.2f}/${float(ss):.2f}" if (ls and ss) else "")
                                prem_str = None
                                try:
                                    from src.clients.options_client import get_live_option_quote
                                    prem = get_live_option_quote(ticker, struct, float(ls) if ls else 0.0, float(ss) if ss else None, exp)
                                    if prem is not None:
                                        prem_str = f"premium ~${prem:.2f} at market"
                                except Exception:
                                    pass
                                if not prem_str:
                                    prem_str = "premium at market (live quote unavailable)"
                                opt_line = f"OPTION: {struct} {strikes_str} {exp} {prem_str}".strip()

                            rr_val = eval_res.get("live_rr") or 0.0
                            lane_val = t.get("setup_lane") or t.get("lane") or "RR_SETUP"
                            lane_lbl = "MEASURED" if lane_val in ("CODE20", "RR_SETUP", "RSI2") else "UNMEASURED"
                            msg = f"BUY 100 {ticker} at market ~${live_price:.2f} | stop ${eval_res['tactical_stop']:.2f} | T1 ${eval_res['target_1']:.2f} | R:R {rr_val:.2f}@mkt | lane {lane_val} [{lane_lbl}]"
                            if opt_line:
                                msg += f"\n{opt_line}"

                            log_trigger_alert(ticker, alert_fired, msg, live_price)
                    else:
                        # Non-passing arrivals write a log row, no push, no ENTRY_TRIGGERED to the user
                        logger.info(
                            f"[{ticker}] Price ${live_price:.2f} in zone [${entry_low:.2f}–${entry_high:.2f}] "
                            f"failed actionable gate ({'; '.join(eval_res.get('reasons', []))}). No push emitted."
                        )
                except Exception as e_eval:
                    logger.warning(f"[{ticker}] On-arrival zone evaluation error: {e_eval}")
        else:
            # B1: running above the plan — above the zone top and below T1. Re-score
            # at market; this is where a strong stock that ran used to fall through
            # to a bare STALKING with no alert at all. A name already IN_TRADE is
            # excluded: the trade owns it, and "ran, no room" would demote a live
            # position to STALKING.
            _above_zone = (
                (entry_high and entry_high > 0 and live_price > float(entry_high))
                or ((not entry_high or entry_high <= 0) and entry_low and entry_low > 0 and live_price > float(entry_low))
            )
            _below_t1 = (
                ((side == "LONG") and ((not target_1) or live_price < float(target_1)))
                or ((side == "SHORT") and ((not target_1) or live_price > float(target_1)))
            )
            if _above_zone and _below_t1 and old_status != "IN_TRADE":
                _rr = _apply_running_rescore(
                    ticker, live_price, t, side, now_et, cross_kinds, today_str,
                    reached_target=bool(hit_t1 or hit_t2),
                )
                new_status = _rr["new_status"]
                alert_fired = _rr["alert_fired"]
            elif old_status in ("IN_TRADE", "IN_ZONE"):
                # Maintain active trade holding above stop loss
                new_status = "IN_TRADE"
            elif old_status == "INVALIDATED" and is_stop_breached:
                new_status = "INVALIDATED"
            elif old_status in ("INVALIDATED", "STOP_BREACHED") and not is_stop_breached:
                new_status = "STALKING"
                alert_fired = "SUPPORT_RECLAIMED"
                msg = f"[{ticker}] Bullish Reclaim! Price ${live_price:.2f} dipped and recovered above support level (${inv_price:.2f}). Thesis restored."
                log_trigger_alert(ticker, alert_fired, msg, live_price)
            else:
                setup_date_str = str(t.get("date") or "")
                is_expired = False
                if setup_date_str:
                    try:
                        from datetime import datetime, timedelta
                        s_dt = datetime.strptime(setup_date_str[:10], "%Y-%m-%d").date()
                        t_dt = get_eastern_now().date()
                        days_diff = (t_dt - s_dt).days
                        if days_diff > 0:
                            bus_days = sum(1 for d in range(days_diff) if (s_dt + timedelta(days=d)).weekday() < 5)
                            if bus_days >= 21:
                                is_expired = True
                    except Exception:
                        pass
                new_status = "EXPIRED" if is_expired else "STALKING"

        # Determine if target should remain actionable or be reset
        target_actionable = None
        eval_verdict = None
        if new_status in ("TARGET_HIT", "COMPLETED"):
            target_actionable = 0
            eval_verdict = "TARGET_HIT"
        elif new_status in ("INVALIDATED", "STOP_BREACHED", "STOPPED", "EXPIRED", "REJECTED_BY_GATE"):
            target_actionable = 0
            eval_verdict = "STAND_ASIDE"
        elif new_status != "IN_ZONE":
            target_actionable = 0
            eval_verdict = "STALKING"
        elif alert_fired in ("ENTRY_ACTIONABLE_BUY", "ENTRY_ACTIONABLE_SHORT"):
            target_actionable = 1

        if alert_fired in ("RUNNING_ACTIONABLE",):
            # B2: a market re-entry that passed the gate. UNMEASURED lane (B4), so the
            # verdict is recorded but the lane label in the alert says UNMEASURED.
            target_actionable = 1
            eval_verdict = "TAKE"

        # Update SQLite DB
        update_target_live_state(
            ticker,
            live_price=live_price,
            status=new_status,
            distance_to_entry_pct=distance_pct,
            alert_type=alert_fired,
            is_actionable_now=target_actionable,
            evaluation_verdict=eval_verdict,
        )

        t_copy = dict(t)
        t_copy["last_price"] = live_price
        t_copy["status"] = new_status
        t_copy["distance_to_entry_pct"] = distance_pct
        if alert_fired:
            t_copy["last_alert_type"] = alert_fired
        t_copy["rvol"] = rvol
        t_copy["crosses"] = cross_kinds
        updated_targets.append(t_copy)

        status_icon = (
            "🎯" if new_status == "IN_ZONE"
            else "📈" if new_status == "IN_TRADE"
            else "⏳" if new_status == "STALKING"
            else "🏁" if new_status == "TARGET_HIT"
            else "🛑"
        )
        dist_str = f"{distance_pct:+.2f}%" if distance_pct is not None else "N/A"
        logger.info(
            f"[{ticker}] ({side}) Spot: ${live_price:.2f} | Dist: {dist_str} | Status: {status_icon} {new_status}"
        )

    # A5: armed-but-unstored names (dossiers from the last 21 sessions). They alert
    # through the level engine like any stored row; their status is not persisted
    # because no watch_targets row exists for them yet.
    for c in coverage:
        ticker = c["ticker"]
        live_price = prices.get(ticker)
        if not live_price:
            live_price = get_current_price(ticker)
        if not live_price or live_price <= 0:
            continue
        try:
            _process_coverage_row(c, live_price, get_eastern_now(), volumes)
        except Exception as e_cov:
            logger.debug(f"[{ticker}] coverage evaluation failed: {e_cov}")

    # 4. Mirror to Google Sheets WATCH-TRIGGERS tab
    if sync_sheets and updated_targets:
        all_targets = get_all_watch_targets()
        try:
            tracker = SheetsTracker()
            tracker.sync_watch_targets_to_sheet(all_targets)
        except Exception as e:
            logger.warning(f"Google Sheets sync failed: {e}")

    return updated_targets


def run_watch_loop(poll_interval: int = 60, sync_sheets: bool = True):
    """Continuous background polling loop."""
    logger.info(f"Starting Watchlist Alert Daemon (Polling interval: {poll_interval}s)...")
    try:
        while True:
            try:
                evaluate_watch_cycle(sync_sheets=sync_sheets)
            except Exception as e:
                logger.error(f"Error in watch cycle: {e}", exc_info=True)
            # Carry the thesis paper positions forward on live quotes and resolve any
            # thesis that has now played out (target hit / stop hit / expiry).
            try:
                from src.tracking.thesis_evaluator import refresh_thesis_paper_trades
                res = refresh_thesis_paper_trades()
                if res.get("resolved"):
                    logger.info(
                        f"[PAPER] {res['resolved']} thesis paper position(s) resolved "
                        f"({res.get('refreshed', 0)} refreshed)."
                    )
            except Exception as e_paper:
                logger.debug(f"[PAPER] refresh failed: {e_paper}")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Watchlist Alert Daemon stopped by user.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watchlist Trigger & Price Alert Engine")
    parser.add_argument("--sync", action="store_true", help="Sync/index research reports into Watchlist DB")
    parser.add_argument(
        "--sync-tastytrade",
        action="store_true",
        default=False,
        help="Explicitly enable Tastytrade cloud quote alert generation (default: False)",
    )
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Target date (YYYY-MM-DD)")
    parser.add_argument("--ticker", type=str, default=None, help="Target specific ticker")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring loop")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    parser.add_argument("--no-sheets", action="store_true", help="Disable Google Sheets sync")

    args = parser.parse_args()

    # Index reports
    if args.sync or not get_all_watch_targets():
        should_sync_tt = args.sync_tastytrade or (
            os.getenv("ENABLE_TASTYTRADE_ALERTS", "0").lower() in ("1", "true", "yes")
        )
        indexed = sync_reports_to_watchlist(args.date, args.ticker, sync_tastytrade=should_sync_tt)
        logger.info(f"Indexed {indexed} report(s) into Watchlist DB (Tastytrade alerts: {'ENABLED' if should_sync_tt else 'DISABLED'}).")

    if args.loop:
        run_watch_loop(poll_interval=args.interval, sync_sheets=not args.no_sheets)
    else:
        results = evaluate_watch_cycle(sync_sheets=not args.no_sheets)
        print("\n" + "=" * 60)
        print(f"WATCHLIST EVALUATION COMPLETE ({len(results)} targets evaluated)")
        print("=" * 60)
        for r in results:
            dist = f"{r.get('distance_to_entry_pct'):+.2f}%" if r.get('distance_to_entry_pct') is not None else "-"
            print(
                f"[{r.get('ticker')}] Spot: ${r.get('last_price', 0):.2f} | "
                f"Status: {r.get('status')} | "
                f"Dist to Entry: {dist} | "
                f"Zone: ${r.get('entry_zone_low', 0):.2f}-${r.get('entry_zone_high', 0):.2f} | "
                f"Stop: ${r.get('tactical_stop', 0):.2f}"
            )
