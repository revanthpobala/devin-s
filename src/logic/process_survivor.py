import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import config
from src.clients.news_client import get_ticker_news
from src.data.tv_scraper import TVScraper
from src.logic.data_window_filter import normalize_number_str

logger = logging.getLogger(__name__)


def _parse_json_lenient(raw):
    """Parse an LLM JSON response tolerating code fences / surrounding prose. Returns dict or None."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            v = json.loads(s[start : end + 1])
            return v if isinstance(v, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def _find_artifact(out_dir: Path, filename: str) -> Path:
    """Finds an artifact in out_dir/<ticker>/, out_dir/, or the triage segregation folders if it was moved."""
    ticker = filename.split("_")[0]
    ticker_p = out_dir / ticker / filename
    if ticker_p.exists():
        return ticker_p
    p = out_dir / filename
    if p.exists():
        return p
    triage_dir = config.BASE_DIR / "data" / "triage" / out_dir.name
    for sub in ("_DEEP_RESEARCH", "force"):
        d_sub = triage_dir / sub / ticker / filename
        if d_sub.exists():
            return d_sub
        d = triage_dir / sub / filename
        if d.exists():
            return d
    return ticker_p


# Serializes ledger writes: multiple thesis workers call _update_research_ledger
# concurrently and would otherwise clobber consolidated_results.json (last-writer-wins).
_ledger_lock = threading.Lock()

# JSON schema for the local triage verdict (mirrors gems/revanth-gem-local.md OUTPUT).
# Passed as a strict json_schema response_format to the LOCAL llama-server (compiled to a
# GBNF grammar). This is safe now that the server is launched with --reasoning off (no
# <think> trace to conflict with the grammar's ROOT-first-char-'{' rule), and it makes the
# model structurally unable to emit invalid JSON. The deterministic filter owns the verdict,
# so even a (rare) parse failure degrades gracefully to the deterministic fallback.
# `conviction` is a number (the gem asks 1-10; the pipeline normalizes any 0-100 downstream).
# NOTE: `reasoning` / `catalyst` are capped with maxLength so the model cannot ramble past
# the token budget and truncate the schema mid-object (the leading cause of transient
# "Failed to parse LLM JSON" on attempt 1). Short fields => fast, in-budget, valid JSON.
_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "dominant_side": {"type": "string", "enum": ["long", "short"]},
        "entry_mode": {
            "type": "string",
            "enum": [
                "TREND_LONG",
                "TREND_SHORT",
                "BREAKOUT_LONG",
                "REVERSION_LONG",
                "REVERSION_SHORT",
                "INCOME_CSP",
                "INCOME_CC",
                "INCOME_STRUCTURE",
                "RSI2_LONG",
                "NONE",
            ],
        },
        "rev_zone": {"type": "string", "maxLength": 12},
        "confirm_contradict": {"type": "string", "enum": ["CONFIRMS", "CONTRADICTS", "NEUTRAL"]},
        "catalyst": {"type": "string", "maxLength": 120},
        "news_sentiment": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "key_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "reasoning": {"type": "string", "maxLength": 240},
        "triage": {"type": "string", "enum": ["PASS", "WATCH", "CUT"]},
        "conviction": {"type": "number"},
        "send_for_deep_research": {"type": "boolean"},
        "trajectory": {"type": "string", "maxLength": 500}
    },
    "required": ["dominant_side", "entry_mode", "confirm_contradict", "triage", "conviction"],
}


_EMPTY_TOKENS = {"", "∅", "⌀", "none", "n/a", "na", "-", "—", "null", "nan"}


def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        s = normalize_number_str(val).strip()
        if s.lower() in _EMPTY_TOKENS:
            return default
        for p in ["C", "O", "H", "L"]:
            s = s.replace(p, "")
        s = s.replace(",", "").replace("%", "").replace(" ", "").strip()
        if not s or s.lower() in _EMPTY_TOKENS:
            return default
        return float(s)
    except Exception:
        return default


def _next_earnings_days(ticker: str):
    from src.clients.earnings_client import get_next_earnings_days
    return get_next_earnings_days(ticker)


def _find_prior_dossier_spot(ticker: str, today_str: str) -> Optional[float]:
    """Find the most recent spot price from a prior dossier before today_str."""
    safe_ticker = ticker.replace(":", "_").upper()
    try:
        base_dir = config.BASE_DIR / "data"
        candidates = []
        for d in (base_dir / "triage", base_dir / "raw"):
            if not d.exists():
                continue
            for date_dir in d.iterdir():
                if date_dir.is_dir() and date_dir.name < today_str and len(date_dir.name) == 10:
                    th = date_dir / safe_ticker / f"{safe_ticker}_thesis.json"
                    if not th.exists():
                        th = date_dir / f"{safe_ticker}_thesis.json"
                    if not th.exists():
                        th = date_dir / f"{safe_ticker}_triage.json"
                    if th.exists():
                        candidates.append((date_dir.name, th))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, latest_path = candidates[0]
        with open(latest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        triage = data.get("triage", {}) if isinstance(data, dict) else {}
        spot = triage.get("spot") or triage.get("price")
        if spot is None and isinstance(data, dict) and "llm_data" in data:
            spot = data["llm_data"].get("price") or data["llm_data"].get("spot")
        if spot is None and isinstance(data, dict) and "spot_at_signal" in data:
            spot = data.get("spot_at_signal")
        return float(spot) if spot is not None else None
    except Exception as e:
        logger.debug(f"Error finding prior dossier spot for {ticker}: {e}")
        return None


def _is_bar_older_than_last_daily_bar(bar_date_str: str, today_str: str) -> bool:
    """Check if the bar date is older than the last completed daily bar."""
    try:
        from datetime import date, timedelta
        b_dt = datetime.strptime(bar_date_str[:10], "%Y-%m-%d").date()
        t_dt = datetime.strptime(today_str[:10], "%Y-%m-%d").date()
        if b_dt >= t_dt:
            return False
        cur = t_dt - timedelta(days=1)
        while cur.weekday() >= 5:  # skip weekends
            cur -= timedelta(days=1)
        return b_dt < cur
    except Exception:
        return False


def _check_data_staleness_and_live_quote(ticker: str, data_window: dict, today_str: str) -> dict:
    """Stamp triage with dw_bar_date and spot_age; detect staleness; fetch live quote."""
    dw_bar_date = None
    if data_window:
        dw_bar_date = (
            data_window.get("dw_bar_date")
            or data_window.get("bar_date")
            or data_window.get("date")
            or data_window.get("time")
            or data_window.get("Date")
        )
    if dw_bar_date and isinstance(dw_bar_date, str) and len(dw_bar_date) >= 10:
        dw_bar_date = dw_bar_date[:10]
    else:
        dw_bar_date = today_str

    spot_age = 0
    try:
        b_dt = datetime.strptime(dw_bar_date, "%Y-%m-%d").date()
        t_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
        spot_age = max(0, (t_dt - b_dt).days)
    except Exception:
        spot_age = 0

    bar_spot = None
    if data_window:
        for k in ("close", "Close", "price", "spot", "C"):
            if data_window.get(k) is not None:
                try:
                    bar_spot = float(data_window[k])
                    break
                except Exception:
                    pass

    prior_spot = _find_prior_dossier_spot(ticker, today_str)
    spot_equals_prior = (
        bar_spot is not None
        and prior_spot is not None
        and abs(bar_spot - prior_spot) < 1e-3
    )

    is_older = _is_bar_older_than_last_daily_bar(dw_bar_date, today_str)
    stale_reasons = []
    if is_older:
        stale_reasons.append(f"bar_{dw_bar_date}_older_than_last_daily")
    if spot_equals_prior:
        stale_reasons.append(f"spot_{bar_spot}_equals_prior_spot_{prior_spot}")

    stale_data = bool(stale_reasons)

    live_spot = None
    move_since_bar_pct = None
    try:
        from src.clients.price_client import get_current_price
        live_spot = get_current_price(ticker)
        if live_spot is not None and bar_spot is not None and bar_spot > 0:
            move_since_bar_pct = round(((live_spot - bar_spot) / bar_spot) * 100.0, 2)
    except Exception as e:
        logger.debug(f"Failed to fetch live quote for {ticker}: {e}")

    return {
        "dw_bar_date": dw_bar_date,
        "spot_age": spot_age,
        "bar_spot": bar_spot,
        "prior_spot": prior_spot,
        "stale_data": stale_data,
        "stale_reasons": stale_reasons,
        "live_spot": live_spot,
        "move_since_bar_pct": move_since_bar_pct,
    }


def _sanitize_degenerate_levels(plan: dict, spot: Optional[float]) -> tuple:
    """Reject degenerate zones: if entry_low == entry_high == spot, set levels to None and tag no_levels."""
    if not isinstance(plan, dict):
        return plan, False
    z = plan.get("zone")
    low = None
    high = None
    if isinstance(z, (list, tuple)) and len(z) >= 2:
        low, high = z[0], z[1]
    if low is None:
        low = plan.get("entry_low") or plan.get("entry_zone_low")
    if high is None:
        high = plan.get("entry_high") or plan.get("entry_zone_high")

    if low is not None and high is not None and spot is not None:
        try:
            f_low, f_high, f_spot = float(low), float(high), float(spot)
            if abs(f_low - f_high) < 1e-4 and abs(f_low - f_spot) < 1e-4:
                sanitized = dict(plan)
                sanitized["zone"] = [None, None]
                sanitized["entry_low"] = None
                sanitized["entry_high"] = None
                sanitized["entry_zone_low"] = None
                sanitized["entry_zone_high"] = None
                sanitized["stop"] = None
                sanitized["target"] = None
                sanitized["target_2"] = None
                sanitized["no_levels"] = True
                return sanitized, True
        except (ValueError, TypeError):
            pass
    return plan, False


def _sanitize_llm_cut_verdict(
    llm_verdict: str,
    triage_dict: dict,
    flags: list,
    earnings_gate: str,
    iv_rank: Optional[float] = None,
    squeeze_on: bool = False,
) -> tuple:
    """D2: CUT is reserved for toxic geometry, warmup, delisted/test, and earnings inside blackout.
    The local LLM may lower conviction but cannot delete/CUT a name on stagnation/no-catalyst.
    """
    if str(llm_verdict).upper() != "CUT":
        return llm_verdict, None

    flags_set = set(str(f).lower() for f in (flags or []))
    act_code = triage_dict.get("action") or triage_dict.get("act_code")
    reason = str(triage_dict.get("reason") or "").lower()
    stage = triage_dict.get("stage")

    is_toxic = (
        act_code == 18
        or "toxic_risk_geometry" in reason
        or "stop_above_entry_long" in reason
        or "stop_below_entry_short" in reason
        or "toxic_geometry" in flags_set
    )
    is_warmup = (
        stage == 0
        or "warmup" in reason
        or "warmup" in flags_set
    )
    is_delisted = "delisted" in flags_set or "test_symbol" in flags_set
    is_earnings = earnings_gate == "FAIL"

    if is_toxic:
        return "CUT", "toxic_risk_geometry"
    if is_warmup:
        return "CUT", "stage_0_warmup"
    if is_delisted:
        return "CUT", "delisted_or_test"
    if is_earnings:
        return "CUT", "earnings_inside_blackout"

    # Not a genuine CUT case -> local LLM tried to cut on stagnation / dead chart / no catalyst
    if (iv_rank is not None and iv_rank <= 25) or squeeze_on or "stagnation" in flags_set or "no_edge" in flags_set:
        return "WATCH", "coil_compression_not_cut"
    return "WATCH", "downgraded_from_cut_no_catalyst"


def scrape_survivor_task(survivor, out_dir, today_str, worker_id, lookback_days: int = 90,
                         chrome_profile: str = None, force: bool = False, headless: bool = True):
    ticker = survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", "")
    if not ticker:
        logger.warning(f"[Scraper-{worker_id}] Survivor dict has no Ticker key: {survivor}")
        return

    ticker = ticker.strip().upper()
    safe_ticker = ticker.replace(":", "_")

    ticker_dir = out_dir / safe_ticker
    json_path = (ticker_dir / f"{safe_ticker}_datawindow.json") if (ticker_dir / f"{safe_ticker}_datawindow.json").exists() else (out_dir / f"{safe_ticker}_datawindow.json")
    chart_path = (ticker_dir / f"{safe_ticker}_chart.png") if (ticker_dir / f"{safe_ticker}_chart.png").exists() else (out_dir / f"{safe_ticker}_chart.png")

    if not force and json_path.exists() and chart_path.exists():
        logger.info(
            f"[Scraper-{worker_id}] Data Window and Screenshot already exist for {ticker}. Skipping scrape!"
        )
        return

    logger.info(f"[Scraper-{worker_id}] Scraping TradingView for {ticker} (lookback_days={lookback_days}, headless={headless})...")
    try:
        if chrome_profile:
            scraper = TVScraper(chrome_profile=chrome_profile, target_date=today_str, headless=headless)
        else:
            scraper = TVScraper(worker_id=worker_id, target_date=today_str, headless=headless)
        scraper.capture_ticker(ticker, lookback_days=lookback_days)
    except Exception as e:
        logger.error(f"[Scraper-{worker_id}] Scraper failed for {ticker}: {e}")
        raise e


def _deep_research_gate(triage, earnings_gate, news_contradiction=False, news_negative=False):
    """SINGLE source of truth for the deep-research gate + informational rank score.

    Called by BOTH prefilter_ticker (pass 1, deterministic) and generate_thesis_task
    (pass 2, enriched) so the two stages can never drift. Returns:
        (quality_pass, send, rank_score, has_plan)

    Eligibility (``send``) requires ALL of:
      - quality_pass: deterministic PASS, OR a WATCH whose conviction clears
        WATCH_MIN_CONVICTION (near-zone, high-quality, not-yet-triggered setups),
      - pursue: the technical verdict is not CUT,
      - conviction >= MIN_CONVICTION_FOR_DEEP_RESEARCH (stable deterministic score,
        NOT the flaky LLM 1-10),
      - earnings gate != FAIL,
      - has_plan: a complete zone + stop + target (something for the paid pass to
        evaluate).
      - NOT a terminal-climax / blow-off: blocked when regime==2 OR the
        "exhaustion" flag is set (a name that has already run its move, e.g. a
        spike-then-fade, must never reach paid deep research).

    Authoritative ORDERING is done separately by
    ``data_window_filter.deep_research_sort_key`` (shared by the enrichment top-N
    pick and the paid cap). ``ev_score`` here is an informational scalar
    persisted for logs / Sheets, not the ordering key.
    """
    min_conviction = config.MIN_CONVICTON_FOR_DEEP_RESEARCH
    min_rev_zone = config.MIN_REV_ZONE_FOR_DEEP_RESEARCH
    min_ev_r = config.TIER_A_MIN_EV_R
    min_watch_conv = config.WATCH_MIN_CONVICTION

    det = triage.get("triage")
    det_pass = det == "PASS"
    # Single conviction normalization (fixes the prior prefilter-vs-enrichment
    # divergence where one folded a missing score to 0 and the other to None):
    # a genuinely MISSING conviction stays None and does NOT auto-fail the
    # >= min_conviction check — quality_pass / ev / plan gates still decide.
    det_raw = triage.get("conviction")
    det_conv = None
    if det_raw is not None:
        try:
            val = float(det_raw)
            det_conv = val / 10.0 if val > 10 else val
        except (ValueError, TypeError):
            det_conv = None
    det_ev_raw = triage.get("ev_r")
    det_ev_r = None
    if det_ev_raw is not None:
        try:
            det_ev_r = float(det_ev_raw)
        except (ValueError, TypeError):
            det_ev_r = None

    watch_pass = bool(det == "WATCH" and (det_conv is not None and det_conv >= min_watch_conv))
    quality_pass = det_pass or watch_pass
    plan = (
        triage.get("long_plan") if triage.get("chosen_side") == "long" else triage.get("short_plan")
    )
    is_rsi2_setup = (triage.get("mode") == "RSI2_LONG") or bool(triage.get("rsi2_setup_event"))
    has_plan = bool(
        plan
        and (
            (plan.get("zone") and all(v is not None for v in plan["zone"]))
            or is_rsi2_setup
        )
        and plan.get("stop") is not None
        and plan.get("target") is not None
    )
    # BLOCK terminal-climax / blow-off names from paid deep research. The two-tier
    # ranking sorts non-in-zone by rr, but rr alone does NOT catch a name that has
    # already exhausted its move (e.g. MRNA: regime 3, exhaustion flag, spike
    # 45->88->62). A regime-2 terminal-climax only becomes WATCH upstream, so a
    # high-conviction climax can slip through the same hole. Excluding here (the
    # single source of truth for BOTH passes) fixes enrichment-select and the
    # paid-cap together. triage already carries `flags` + `regime`.
    flags = triage.get("flags") or []
    regime_v = int(round(triage.get("regime") or 0))
    blocked = regime_v == 2 or "exhaustion" in flags  # terminal climax / blow-off
    mode = triage.get("mode") or "NONE"
    is_reversion = mode.startswith("REVERSION")
    rev_raw = triage.get("rev")
    rev_score = None
    if rev_raw is not None:
        try:
            rev_score = float(rev_raw)
        except (ValueError, TypeError):
            rev_score = None

    # Phase 6: Stop gating PASS names on Buy Score or ev_r built from Dir Prob
    if quality_pass:
        conviction_ok = True
    elif is_reversion:
        conviction_ok = rev_score is None or rev_score >= min_rev_zone
    else:
        conviction_ok = det_conv is None or det_conv >= min_conviction

    # INCOME / STRUCTURE-ONLY NAMES DO NOT GO TO PAID RESEARCH.
    # 'no_fresh_long' means "do not BUY here" (fade active, Ext Z >= 2.5, or parabolic). They
    # are still tradeable -- they are the best measured premium-SELLING context -- but the
    # support needs 'Energy IV Rank Pct', 'Exp Move Pct 21b', the level ladder and the earnings gate,
    # all of which are deterministic and already computed locally. Deep research buys DIRECTIONAL
    # conviction (catalyst, flow, policy, insider divergence), which cannot improve a strike sale,
    # so paying for it here spends the expensive resource on the one question it does not answer.
    # They still flow through the free, uncapped local enrichment, and the filter hands them back as
    # 'structure' + 'structure_strikes' ready to use.
    # This exclusion used to happen ONLY INCIDENTALLY: ev_r is built from long directional EV,
    # so an extended/faded name almost always failed TIER_A_MIN_EV_R. That is fragile -- it breaks
    # the moment ev_r changes -- so it is stated explicitly.
    income_only = bool(triage.get("no_fresh_long"))

    # CAUTION intermediate effect: earnings within 3-7 days raises the
    # conviction floor — CAUTION is neither PASS (no weight) nor FAIL
    # (hard block). A near-dividend setup needs stronger deterministic
    # conviction to justify paid deep research.
    caution_floor = min_conviction + 10.0 if earnings_gate == "CAUTION" else 0.0
    caution_pass = det_conv is None or det_conv >= caution_floor

    # Phase 6: PASS names are not blocked by ev_r or caution conviction floor
    ev_r_ok = quality_pass or (det_ev_r is None or det_ev_r >= min_ev_r)
    send = bool(
        quality_pass
        and triage.get("pursue", True) is True
        and earnings_gate != "FAIL"
        and conviction_ok
        and (quality_pass or caution_pass)
        and has_plan
        and not blocked
        and not income_only
        and ev_r_ok
    )
    # Informational scalar (mirrors deep_research_sort_key's news penalty on the
    # ev axis so the logged number tracks the real ordering intent).
    # Neutral placeholder (0.0) for missing EV — cohort median would be ideal
    # but is unavailable at this scope; 0.0 avoids maximally penalizing
    # otherwise-strong setups for a single missing field.
    ev_score = det_ev_r if det_ev_r is not None else 0.0
    if news_contradiction:
        ev_score -= 1.0
    if news_negative:
        ev_score -= 0.25
    if det_conv is not None:
        ev_score += det_conv / 100.0
    return quality_pass, send, round(ev_score, 4), has_plan


def prefilter_ticker(survivor, out_dir, today_str, worker_id, regenerate: bool = False):
    """Deterministic-only pass (CHEAP, no local-LLM call).

    Runs the Data-Window pre-filter + news sentiment (local 9B only, free and
    rate-limit-free) for ONE ticker, computes the reproducible ``rank_score``,
    writes a deterministic-only ``_thesis.json`` (no Qwen enrichment), and
    returns a small record so the orchestrator can globally rank all tickers
    and pick the top-N for the expensive Qwen enrichment pass.

    This is the first half of the selective-enrichment design (proposal A):
    we never spend a local-LLM call on a ticker that can't rank into the top-N.
    """
    ticker = survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", "")
    if not ticker:
        return None
    ticker = ticker.strip().upper()
    safe_ticker = ticker.replace(":", "_")
    trade_id = survivor.get("Trade ID") or survivor.get("trade_id") or ""
    thesis_json_path = _find_artifact(out_dir, f"{safe_ticker}_thesis.json")

    if thesis_json_path.exists() and not regenerate:
        try:
            with open(thesis_json_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # Already enriched in a prior run -> keep it, but still return the
            # deterministic rank so the orchestrator's top-N math is complete.
            triage = cached.get("triage", {})
            if isinstance(triage, dict) and triage.get("triage") == "PASS":
                return {
                    "ticker": safe_ticker,
                    "trade_id": trade_id,
                    "row_index": survivor.get("_row_index"),
                    "rank_score": cached.get("llm_data", {}).get("rank_score", -1e9),
                    "enriched": True,
                    "triage": triage,
                }
        except Exception:
            pass

    json_path = _find_artifact(out_dir, f"{safe_ticker}_datawindow.json")
    csv_path = _find_artifact(out_dir, f"{safe_ticker}_datawindow.csv")
    data_window = {}
    realvol_10d = ret_10d = None

    if csv_path.exists():
        try:
            from src.data.csv_adapter import csv_to_datawindow
            data_window, _, realvol_10d, ret_10d = csv_to_datawindow(
                str(csv_path), str(json_path), ticker=ticker
            )
        except Exception as e:
            logger.warning(f"[Prefilter-{worker_id}] CSV parse failed for {ticker}: {e}")

    if not data_window and json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data_window = json.load(f)
        except Exception as e:
            logger.error(f"[Prefilter-{worker_id}] Failed to read datawindow for {ticker}: {e}")

    if not data_window:
        logger.warning(f"[Prefilter-{worker_id}] Data window missing for {ticker}, metrics 0.")

    from src.logic.data_window_filter import triage_ticker

    triage = triage_ticker(ticker, data_window, realvol_10d=realvol_10d, ret_10d=ret_10d)
    staleness_info = _check_data_staleness_and_live_quote(ticker, data_window, today_str)
    dw_bar_date = staleness_info["dw_bar_date"]
    spot_age = staleness_info["spot_age"]
    stale_data = staleness_info["stale_data"]
    live_spot = staleness_info["live_spot"]
    move_since_bar_pct = staleness_info["move_since_bar_pct"]
    bar_spot = staleness_info["bar_spot"]

    triage["dw_bar_date"] = dw_bar_date
    triage["spot_age"] = spot_age
    triage["stale_data"] = stale_data
    triage["live_spot"] = live_spot
    triage["move_since_bar_pct"] = move_since_bar_pct

    # Degenerate zone check
    if triage.get("long_plan"):
        triage["long_plan"], degen = _sanitize_degenerate_levels(triage["long_plan"], bar_spot)
        if degen:
            triage["no_levels"] = True
            flags_list = list(triage.get("flags") or [])
            if "no_levels" not in flags_list:
                flags_list.append("no_levels")
            triage["flags"] = flags_list

    # A1: If Data Window bar is older than last completed daily bar or spot equals prior dossier spot,
    # mark stale_data and do NOT CUT or score on it.
    if stale_data:
        flags_list = list(triage.get("flags") or [])
        if "stale_data" not in flags_list:
            flags_list.append("stale_data")
        triage["flags"] = flags_list
        if triage.get("triage") == "CUT":
            triage["triage"] = "WATCH"
            triage["reason"] = "stale_data"
            triage["conviction"] = None

    sentiment = triage.get("sentiment", {})
    # News at prefilter time: we first check the gate using cheap headline sentiment.
    news_negative = bool(triage.get("news_negative"))
    contradicts = False
    min_ev_r = config.TIER_A_MIN_EV_R  # for the ev_floor_override message below

    det_pass = triage.get("triage") == "PASS"
    det_ev_r = triage.get("ev_r")
    earnings_days = _next_earnings_days(ticker)
    earnings_gate = (
        "UNKNOWN"
        if earnings_days is None
        else "FAIL"
        if earnings_days < 3
        else "CAUTION"
        if earnings_days < 7
        else "PASS"
    )

    # Single shared gate (identical logic to the enrichment pass) WITHOUT full news.
    quality_pass, send, ev_score, has_plan = _deep_research_gate(
        triage,
        earnings_gate,
        news_contradiction=contradicts,
        news_negative=news_negative,
    )

    # D1: Run the full confirm/contradict news loop during prefilter for flagged PASS tickers
    if send and triage.get("triage") == "PASS":
        logger.info(f"[{ticker}] Flagged for deep research. Running full news synthesis...")
        try:
            from src.clients.news_researcher import run_news_research

            out_path, contradicts, news_sent = run_news_research(ticker, today_str, out_dir, triage)
            news_negative = news_sent == "BEARISH"

            # Re-evaluate gate WITH full news
            quality_pass, send, ev_score, has_plan = _deep_research_gate(
                triage,
                earnings_gate,
                news_contradiction=contradicts,
                news_negative=news_negative,
            )
        except Exception as e:
            logger.error(f"[{ticker}] Failed full news synthesis during prefilter: {e}")

    screener_setup = survivor.get("screener_setup") or survivor.get("Setup")
    income_only = bool(triage.get("no_fresh_long"))

    compact = {
        "triage": triage.get("triage"),
        "chosen_side": triage.get("chosen_side"),
        "mode": triage.get("mode"),
        "reason": triage.get("reason"),
        "conviction": triage.get("conviction"),
        "rr": triage.get("rr"),
        "in_zone": triage.get("in_zone"),
        "flags": triage.get("flags"),
        "send_for_deep_research": send,
        "pursue": triage.get("pursue"),
        "pursue_reason": triage.get("pursue_reason"),
        "news_negative": news_negative,
        "news_contradiction": contradicts,
        "rank_score": round(ev_score, 4),
        "enriched": False,
        "sentiment": sentiment.get("label", "neutral"),
        "sentiment_summary": sentiment.get("summary", ""),
        "screener_setup": screener_setup,
        "income_only": income_only,
        "structure": triage.get("structure"),
        "structure_strikes": triage.get("structure_strikes"),
        "iv_rank": triage.get("iv_rank"),
        "triggers": triage.get("triggers"),
        "dw_bar_date": dw_bar_date,
        "spot_age": spot_age,
        "stale_data": stale_data,
        "live_spot": live_spot,
        "move_since_bar_pct": move_since_bar_pct,
        "cut_reason": triage.get("cut_reason"),
        "cut_spot": triage.get("cut_spot"),
    }
    if quality_pass and not send:
        compact["triage"] = "WATCH"
        if earnings_gate == "FAIL":
            compact["earnings_override"] = f"earnings in {earnings_days}d"
        elif det_ev_r is not None and det_ev_r < min_ev_r:
            compact["ev_floor_override"] = f"ev_r {det_ev_r} < {min_ev_r}"

    result_dict = {
        "ticker": safe_ticker,
        "trade_id": trade_id,
        "row_index": survivor.get("_row_index"),
        "researched_at": datetime.now().isoformat(timespec="seconds"),
        "llm_data": compact,
        "av_sentiment": {},
        "av_earnings": {},
        "triage": triage,
        "social_sentiment": {},
        "thesis_json": str(thesis_json_path),
        "dw_bar_date": dw_bar_date,
        "spot_age": spot_age,
        "stale_data": stale_data,
        "live_spot": live_spot,
        "move_since_bar_pct": move_since_bar_pct,
        "cut_reason": triage.get("cut_reason"),
        "cut_spot": triage.get("cut_spot"),
    }
    try:
        with open(thesis_json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4)
        triage_json_path = out_dir / f"{safe_ticker}_triage.json"
        with open(triage_json_path, "w", encoding="utf-8") as f:
            json.dump(triage, f, indent=4)
    except Exception as e:
        logger.error(f"[Prefilter-{worker_id}] Failed to cache thesis/triage JSON for {ticker}: {e}")
    _update_research_ledger(out_dir, result_dict)

    # Phase 4: Log rule baseline (source="rule") to suggestions ledger (LONG only — shorts measured no edge)
    if triage.get("triage") in ("PASS", "WATCH") and str(triage.get("chosen_side") or "LONG").upper() == "LONG":
        try:
            from src.tracking.suggestions_ledger import append_suggestion
            from src.logic.level_validation import validate_levels

            long_p = triage.get("long_plan") or {}
            z = long_p.get("zone") or [None, None]
            e_low = z[0] if isinstance(z, (list, tuple)) and len(z) > 0 else long_p.get("entry_zone_low")
            e_high = z[1] if isinstance(z, (list, tuple)) and len(z) > 1 else long_p.get("entry_zone_high")
            stop_lvl = float(long_p.get("stop")) if long_p.get("stop") else None
            t1_lvl = float(long_p.get("target")) if long_p.get("target") else None
            t2_lvl = float(long_p.get("target_2")) if long_p.get("target_2") else None

            # Guard against invalid stop geometry (stop >= entry_low for LONG)
            if stop_lvl is not None and e_low is not None and float(stop_lvl) >= float(e_low):
                stop_lvl = None

            s_lane = "WATCH_SHADOW" if triage.get("triage") == "WATCH" else (triage.get("setup_lane") or "RR_SETUP")
            dw_bar_date = (data_window.get("date") or data_window.get("time") or data_window.get("Date") or today_str) if data_window else today_str
            if isinstance(dw_bar_date, str) and len(dw_bar_date) >= 10:
                dw_bar_date = dw_bar_date[:10]

            rule_plan = {
                "ticker": ticker,
                "date": dw_bar_date,
                "entry_low": float(e_low) if e_low else None,
                "entry_high": float(e_high) if e_high else None,
                "stop": stop_lvl,
                "target_1": t1_lvl,
                "target_2": t2_lvl,
                "setup_lane": s_lane,
                "kind": "NEW",
            }
            gate_ok, gate_reasons = validate_levels(
                plan=rule_plan,
                dw=data_window or {},
                side="LONG",
                ticker=ticker,
                date_str=dw_bar_date,
            )
            rule_gate_status = "PASS" if gate_ok else "REJECTED_BY_GATE"

            atr_val = None
            if data_window:
                for k in ("RSI2 ATR14", "rsi2_atr14", "atr14", "ATR 14", "atr_14", "ATR"):
                    if data_window.get(k) is not None:
                        try:
                            atr_val = float(data_window[k])
                            break
                        except Exception:
                            pass

            spot_val = None
            if data_window:
                for k in ("close", "Close", "price", "spot"):
                    if data_window.get(k) is not None:
                        try:
                            spot_val = float(data_window[k])
                            break
                        except Exception:
                            pass

            append_suggestion({
                "ticker": ticker,
                "date": dw_bar_date,
                "source": "rule",
                "side": "LONG",
                "entry_type": "LIMIT",
                "entry_low": float(e_low) if e_low else None,
                "entry_high": float(e_high) if e_high else None,
                "stop": stop_lvl,
                "target_1": t1_lvl,
                "target_2": t2_lvl,
                "planned_rr": float(triage.get("rr")) if triage.get("rr") else None,
                "verdict": "ENTER" if triage.get("triage") == "PASS" else "STALK",
                "gate_status": rule_gate_status,
                "setup_lane": s_lane,
                "kind": "NEW",
                "atr_at_signal": atr_val,
                "spot_at_signal": spot_val,
                "live_spot": live_spot,
                "move_since_bar_pct": move_since_bar_pct,
                "rr_at_market_at_signal": float(triage["rr_at_market"]) if triage.get("rr_at_market") is not None else None,
                "lane_prior_win": triage.get("lane_prior_win"),
                "lane_prior_ev": triage.get("lane_prior_ev"),
                "pb_funnel": triage.get("pb_funnel"),
                "_datawindow": data_window,
                "notes": f"Rule triage: {triage.get('triage')} ({triage.get('reason')})",
            })
        except Exception as e_base:
            logger.debug(f"[Prefilter-{worker_id}] Failed to log rule baseline for {ticker}: {e_base}")

    if not survivor.get("_row_index"):
        logger.warning(
            f"[Prefilter-{worker_id}] No _row_index for {ticker}; saved locally, not pushed to Sheets."
        )

    # Income candidates are deliberately not sent to paid research (see _deep_research_gate), but log
    # what they DO support -- otherwise a name that is a perfectly good premium sale just drops
    # from the run with send=False and no explanation.
    if income_only:
        logger.info(
            f"[Prefilter-{worker_id}] {ticker}: INCOME-ONLY (no fresh long) "
            f"structure={triage.get('structure')} iv_rank={triage.get('iv_rank')} "
            f"exp_move={triage.get('exp_move_pct')} strikes={triage.get('structure_strikes')} "
            f"setup={screener_setup}"
        )
    else:
        logger.info(
            f"[Prefilter-{worker_id}] {ticker}: det={triage.get('triage')} "
            f"rank={round(ev_score, 3)} send={send} news_neg={news_negative} contradicts={contradicts} "
            f"setup={screener_setup}"
        )
    return {
        "ticker": safe_ticker,
        "trade_id": trade_id,
        "row_index": survivor.get("_row_index"),
        "rank_score": round(ev_score, 4),
        "quality_pass": bool(quality_pass),
        "send_for_deep_research": bool(send),
        "enriched": False,
        "triage": triage,
        "thesis_json_path": str(thesis_json_path),
        # Income lane: consumers read these instead of re-deriving a strike from the chart.
        "screener_setup": screener_setup,
        "income_only": income_only,
        "structure": triage.get("structure"),
        "structure_strikes": triage.get("structure_strikes"),
    }


def _screener_payload_verdict(ticker, safe_ticker, survivor, payload, thesis_json_path, out_dir, worker_id, today_str):
    """Consume a typed SCHWAB_SCAN feature payload WITHOUT a TradingView Data Window.

    The deterministic filter's measured PASS lanes (Code 20 / RSI2 / RR-at-market) are built
    on real indicator cohorts and must never fire on scan metrics — so this lane emits an
    explicit WATCH verdict from the candidate's OWN observed levels, then lets the free local
    LLM + news decide whether it is worth paid deep research. No Data Window is fabricated.
    """
    side = str(payload.get("side") or "LONG").upper()
    chosen = "long" if side == "LONG" else "short"

    def _lvl(key):
        v = payload.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    price = _lvl("price")
    stop = _lvl("stop_level")
    target = _lvl("target_level")
    zone_bot = _lvl("support_level") if chosen == "long" else price
    zone_top = _lvl("entry_level") if chosen == "long" else _lvl("ceiling_level")

    plan = {"zone": [zone_bot, zone_top], "stop": stop, "target": target}
    has_plan = bool(
        (plan["zone"] and all(v is not None for v in plan["zone"]))
        and plan["stop"] is not None
        and plan["target"] is not None
    )

    # Free local news synthesis — the same source of directional context a Data Window run gets.
    contradicts = False
    news_negative = False
    try:
        from src.clients.news_researcher import run_news_research

        sentinel_triage = {"triage": "WATCH"}
        _, contradicts, news_sent = run_news_research(ticker, today_str, out_dir, sentinel_triage)
        news_negative = news_sent == "BEARISH"
    except Exception as e:
        logger.debug(f"[ThesisWorker-{worker_id}] News synthesis for screener payload {ticker}: {e}")

    # Lane-aware earnings gate (lane-aware policy per implementation plan): swing basing uses a
    # 14d blackout; a directional options setup would use 48h. This lane is always swing-family.
    earnings_days = _next_earnings_days(ticker)
    if earnings_days is None:
        earnings_gate = "UNKNOWN"
    elif earnings_days < 3:
        earnings_gate = "FAIL"
    elif earnings_days < 14:
        earnings_gate = "CAUTION"
    else:
        earnings_gate = "PASS"

    # The free local LLM judges the OBSERVED thesis (levels + news), never a fabricated window.
    llm_conviction = None
    send_for_deep_research = False
    try:
        from src.clients.llm_client import query_local_llm

        obs = {
            "ticker": ticker,
            "side": side,
            "price": price,
            "weinstein_stage": payload.get("weinstein_stage"),
            "entry_zone": plan["zone"],
            "stop": stop,
            "target": target,
            "rr": payload.get("long_rr") if chosen == "long" else payload.get("short_rr"),
            "ext_200_pct": payload.get("ext_200_pct"),
            "is_extreme_reversal": payload.get("is_extreme_reversal"),
            "squeeze_on": payload.get("squeeze_on"),
            "priority_score": payload.get("priority_score"),
            "iv_rank": payload.get("iv_rank"),
        }
        system_prompt = (
            "You are a swing-trade triage gate. You receive ONLY observed Schwab screener metrics — "
            "this candidate has NO TradingView Data Window, so every Pine indicator field is unavailable. "
            "Do NOT assume an entry signal exists. Respond with JSON only: "
            '{"conviction": <0-100 or null>, "send_for_deep_research": <true|false>, "reasoning": "<= 25 words>'
        )
        user_prompt = (
            f"OBSERVED: {json.dumps(obs, default=str)}\n"
            f"News bearish: {news_negative} | News contradicts thesis: {contradicts} | Earnings gate: {earnings_gate}"
        )
        raw = query_local_llm(system_prompt, user_prompt=user_prompt, json_mode=True, use_openrouter=False)
        parsed = _parse_json_lenient(raw) if isinstance(raw, str) else None
        if isinstance(parsed, dict):
            c = parsed.get("conviction")
            try:
                llm_conviction = float(c) if c is not None else None
            except (TypeError, ValueError):
                llm_conviction = None
            send_for_deep_research = bool(parsed.get("send_for_deep_research"))
    except Exception as e:
        logger.debug(f"[ThesisWorker-{worker_id}] Local LLM triage for screener payload {ticker}: {e}")

    # Deterministic floor: a candidate without a verified Data Window is never auto-PASS.
    # It reaches paid deep research only if the free LLM explicitly endorses it AND the gates hold.
    quality_pass = True  # WATCH-quality; promotion is decided by send_for_deep_research below
    prio_score = float(payload.get("priority_score") or 0.0)
    prio_tier = str(payload.get("priority_tier") or "")
    is_high_conv_screener = (prio_tier == "HIGH_PRIORITY" or prio_score >= 65.0)

    send = bool(
        has_plan
        and earnings_gate != "FAIL"
        and not news_negative
        and not contradicts
        and (llm_conviction is None or float(llm_conviction) >= 50.0)
        and (send_for_deep_research or is_high_conv_screener)
    )

    triage = {
        "ticker": ticker,
        "bar_date": None,
        "chosen_side": chosen,
        "mode": "SCHWAB_SCAN_" + ("LONG" if chosen == "long" else "SHORT"),
        "triage": "WATCH",
        "reason": "screener_feature_payload",
        "conviction": llm_conviction,
        "conviction_str": None,
        "rev": None,
        "rr": payload.get("long_rr") if chosen == "long" else payload.get("short_rr"),
        "ev_r": None,
        "win_prob": None,
        "in_zone": None,
        "missed": None,
        "dir_prob": None,
        "regime": None,
        "flags": ["screener_feature_payload", "no_data_window"],
        "long_plan": plan if chosen == "long" else {"zone": [None, None], "stop": None, "target": None},
        "short_plan": plan if chosen == "short" else {"zone": [None, None], "stop": None, "target": None},
        "action_long": None,
        "action_short": None,
        "action": None,
        "action_actionable": False,
        "mtf_long": None,
        "mtf_short": None,
        "ext_pct": payload.get("ext_200_pct"),
        "rank_model_score": None,
        "bad_data": False,
        "no_fresh_long": False,
        "structure": None,
        "structure_strikes": None,
        "iv_rank": payload.get("iv_rank"),
        "exp_move_pct": None,
        "rr_at_market": None,
        "pursue": True,
        "pursue_reason": "screener_feature_payload",
        "news_negative": news_negative,
    }

    compact = {
        "triage": "WATCH",
        "chosen_side": chosen,
        "mode": triage["mode"],
        "reason": "screener_feature_payload",
        "conviction": llm_conviction,
        "rr": triage["rr"],
        "in_zone": None,
        "flags": ["screener_feature_payload", "no_data_window"],
        "send_for_deep_research": send,
        "pursue": True,
        "pursue_reason": "screener_feature_payload",
        "news_negative": news_negative,
        "sentiment": "bearish" if news_negative else "neutral",
        "sentiment_summary": "",
        "screener_setup": survivor.get("screener_setup") or payload.get("setup_family"),
        "income_only": False,
        "structure": None,
        "structure_strikes": None,
        "iv_rank": payload.get("iv_rank"),
        "triggers": None,
    }

    result_dict = {
        "ticker": safe_ticker,
        "trade_id": survivor.get("Trade ID") or survivor.get("trade_id") or "",
        "row_index": survivor.get("_row_index"),
        "researched_at": datetime.now().isoformat(timespec="seconds"),
        "llm_data": compact,
        "av_sentiment": {},
        "av_earnings": {},
        "triage": triage,
        "social_sentiment": {},
        "thesis_json": str(thesis_json_path),
    }
    try:
        with open(thesis_json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4)
        triage_json_path = out_dir / f"{safe_ticker}_triage.json"
        with open(triage_json_path, "w", encoding="utf-8") as f:
            json.dump(triage, f, indent=4)
    except Exception as e:
        logger.error(f"[ThesisWorker-{worker_id}] Failed to cache screener-payload thesis for {ticker}: {e}")
    _update_research_ledger(out_dir, result_dict)

    logger.info(
        f"[ThesisWorker-{worker_id}] {ticker}: SCHWAB_SCAN payload lane "
        f"side={side} watch=True send_for_deep_research={send} llm_conviction={llm_conviction} "
        f"news_neg={news_negative} earnings_gate={earnings_gate}"
    )
    return result_dict


def generate_thesis_task(
    survivor, out_dir, today_str, worker_id, regenerate: bool = False, enrich: bool = False
):
    ticker = survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", "")
    if not ticker:
        return

    ticker = ticker.strip().upper()
    safe_ticker = ticker.replace(":", "_")
    # Trade ID is the sheet's auto-incrementing column A value (=ROW()-1).
    # Captured so the consolidated ledger can match/update a specific trade row.
    trade_id = survivor.get("Trade ID") or survivor.get("trade_id") or ""
    thesis_json_path = _find_artifact(out_dir, f"{safe_ticker}_thesis.json")

    if thesis_json_path.exists() and not regenerate and not enrich:
        logger.info(
            f"[ThesisWorker-{worker_id}] Skipping {ticker} - thesis JSON already exists, loading from cache"
        )
        try:
            with open(thesis_json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(
                f"[ThesisWorker-{worker_id}] Failed to load cached thesis JSON for {ticker}: {e}"
            )
            # If we fail to load, we'll just regenerate it

    logger.info(f"\n--- [ThesisWorker-{worker_id}] PHASE 2C: GENERATING THESIS FOR {ticker} ---")

    # B. Load Data Window
    json_path = _find_artifact(out_dir, f"{safe_ticker}_datawindow.json")
    csv_path = _find_artifact(out_dir, f"{safe_ticker}_datawindow.csv")
    data_window = {}
    realvol_10d = ret_10d = None

    if csv_path.exists():
        try:
            from src.data.csv_adapter import csv_to_datawindow
            data_window, _, realvol_10d, ret_10d = csv_to_datawindow(
                str(csv_path), str(json_path), ticker=ticker
            )
        except Exception as e:
            logger.warning(f"[ThesisWorker-{worker_id}] CSV parse failed for {ticker}: {e}")

    if not data_window and json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data_window = json.load(f)
        except Exception as e:
            logger.error(
                f"[ThesisWorker-{worker_id}] Failed to read datawindow JSON for {ticker}: {e}"
            )

    # ------------------------------------------------------------------
    # TYPED SCREENER FEATURE PAYLOAD LANE (source="SCHWAB_SCAN")
    # A candidate dispatched by the Schwab screener without a fresh TradingView
    # scrape has NO real Data Window. We do NOT fabricate one: the deterministic
    # Code-20 / RSI2 / RR lanes are measured on real indicator cohorts and must
    # never fire on scan metrics. Instead we consume the typed payload directly —
    # its OBSERVED levels (support/ceiling, stop, target) + news + local LLM —
    # and emit an explicit WATCH verdict that can be promoted to deep research.
    # ------------------------------------------------------------------
    if not data_window:
        payload_path = _find_artifact(out_dir, f"{safe_ticker}_screener_payload.json")
        if payload_path.exists():
            try:
                with open(payload_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                logger.error(
                    f"[ThesisWorker-{worker_id}] Failed to read screener payload for {ticker}: {e}"
                )
                payload = None
            if isinstance(payload, dict) and payload.get("datawindow_source") == "SCHWAB_SCAN":
                return _screener_payload_verdict(ticker, safe_ticker, survivor, payload, thesis_json_path, out_dir, worker_id, today_str)

    if not data_window:
        logger.warning(
            f"[ThesisWorker-{worker_id}] Data window JSON not found for {ticker}, metrics will be 0."
        )

    # ---- Data-Window pre-filter + basic Alpaca news sentiment ----
    # Run for EVERY ticker (cheap: label math + Alpaca headlines + one small
    # sentiment call). The data-window verdict decides whether a setup exists.
    # News sentiment is ONE INPUT, NOT A VETO: it annotates (surfaced in the
    # compact record + downstream conviction), but a negative tone no longer
    # blocks pursuit. Only a non-PASS technical verdict skips the LLM triage.
    from src.logic.data_window_filter import triage_ticker

    triage = triage_ticker(ticker, data_window, realvol_10d=realvol_10d, ret_10d=ret_10d)
    staleness_info = _check_data_staleness_and_live_quote(ticker, data_window, today_str)
    dw_bar_date = staleness_info["dw_bar_date"]
    spot_age = staleness_info["spot_age"]
    stale_data = staleness_info["stale_data"]
    live_spot = staleness_info["live_spot"]
    move_since_bar_pct = staleness_info["move_since_bar_pct"]
    bar_spot = staleness_info["bar_spot"]

    triage["dw_bar_date"] = dw_bar_date
    triage["spot_age"] = spot_age
    triage["stale_data"] = stale_data
    triage["live_spot"] = live_spot
    triage["move_since_bar_pct"] = move_since_bar_pct

    # Degenerate zone check
    if triage.get("long_plan"):
        triage["long_plan"], degen = _sanitize_degenerate_levels(triage["long_plan"], bar_spot)
        if degen:
            triage["no_levels"] = True
            flags_list = list(triage.get("flags") or [])
            if "no_levels" not in flags_list:
                flags_list.append("no_levels")
            triage["flags"] = flags_list

    # A1: If Data Window bar is older than last completed daily bar or spot equals prior dossier spot,
    # mark stale_data and do NOT CUT or score on it.
    if stale_data:
        flags_list = list(triage.get("flags") or [])
        if "stale_data" not in flags_list:
            flags_list.append("stale_data")
        triage["flags"] = flags_list
        if triage.get("triage") == "CUT":
            triage["triage"] = "WATCH"
            triage["reason"] = "stale_data"
            triage["conviction"] = None

    sentiment = triage.get("sentiment", {})
    logger.info(
        f"[ThesisWorker-{worker_id}] Pre-filter {ticker}: triage={triage['triage']} "
        f"side={triage['chosen_side']} sentiment={sentiment.get('label')} "
        f"pursue={triage['pursue']}"
    )

    screener_setup = survivor.get("screener_setup") or survivor.get("Setup")
    income_only = bool(triage.get("no_fresh_long"))

    if not triage["pursue"] and not enrich:
        # Not worth pursuing — record the deterministic verdict + sentiment and
        # skip the local-LLM triage, Alpha Vantage, and Gemini deep research.
        # When called as the selective-enrichment pass (enrich=True), we ALWAYS
        # run the Qwen enrichment for the explicitly top-N-selected ticker,
        # regardless of the deterministic pursue flag.
        compact = {
            "triage": triage["triage"],
            "chosen_side": triage["chosen_side"],
            "mode": triage["mode"],
            "reason": triage["reason"],
            "conviction": triage["conviction"],
            "rr": triage["rr"],
            "in_zone": triage["in_zone"],
            "flags": triage["flags"],
            "send_for_deep_research": False,
            "pursue": False,
            "pursue_reason": triage["pursue_reason"],
            "news_negative": triage.get("news_negative", False),
            "sentiment": sentiment.get("label", "neutral"),
            "sentiment_summary": sentiment.get("summary", ""),
            "screener_setup": screener_setup,
            "income_only": income_only,
            "structure": triage.get("structure"),
            "structure_strikes": triage.get("structure_strikes"),
            "iv_rank": triage.get("iv_rank"),
            "triggers": triage.get("triggers"),
            "dw_bar_date": dw_bar_date,
            "spot_age": spot_age,
            "stale_data": stale_data,
            "live_spot": live_spot,
            "move_since_bar_pct": move_since_bar_pct,
            "cut_reason": triage.get("cut_reason"),
            "cut_spot": triage.get("cut_spot"),
        }
        # NOTE: no _thesis.md is written (markdown generation removed); the
        # canonical record is the _thesis.json below.

        result_dict = {
            "ticker": safe_ticker,
            "trade_id": trade_id,
            "row_index": survivor.get("_row_index"),
            "researched_at": datetime.now().isoformat(timespec="seconds"),
            "llm_data": compact,
            "av_sentiment": {},
            "av_earnings": {},
            "triage": triage,
            "thesis_json": str(thesis_json_path),
            "dw_bar_date": dw_bar_date,
            "spot_age": spot_age,
            "stale_data": stale_data,
            "live_spot": live_spot,
            "move_since_bar_pct": move_since_bar_pct,
            "cut_reason": triage.get("cut_reason"),
            "cut_spot": triage.get("cut_spot"),
        }
        try:
            with open(thesis_json_path, "w", encoding="utf-8") as f:
                json.dump(result_dict, f, indent=4)
        except Exception as e:
            logger.error(f"[Analyzer-{worker_id}] Failed to cache thesis JSON for {ticker}: {e}")
        _update_research_ledger(out_dir, result_dict)
        # Always return result_dict (so the ticker is eligible for deep-research
        # segregation regardless of _row_index); Sheets push is gated downstream.
        if not survivor.get("_row_index"):
            logger.warning(
                f"[Analyzer-{worker_id}] No _row_index for {ticker}; saved locally, not pushed to Sheets."
            )
        return result_dict

    # ---- pursue == True: continue to the local-LLM deep triage ----
    # Alpaca pre-filter already cleared (above). Only NOW do we fetch richer
    # Finnhub news for the few pursued tickers, so we never burn the 60/min
    # Finnhub quota on the 150+ tickers the Alpaca filter already rejected.
    # Falls back to the Alpaca summary if Finnhub is empty/unconfigured.
    finhub_news = ""
    try:
        finhub_news = get_ticker_news(ticker, days=3).get("raw_news", "")
    except Exception as e:
        logger.warning(f"[ThesisWorker-{worker_id}] Finnhub news fetch failed for {ticker}: {e}")
    raw_news_src = finhub_news or sentiment.get("summary", "")

    news_data = {
        "sentiment": (sentiment.get("label") or "neutral").upper(),
        "catalyst": "none",
        "raw_news": raw_news_src,
    }
    headlines = list(sentiment.get("headlines", []) or [])

    # Parse Data Window early with parse_data_window for reliable normalized field extraction
    try:
        from src.logic.data_window_filter import parse_data_window

        _pf = parse_data_window(data_window) or {}
    except Exception as e:
        logger.warning(
            f"[ThesisWorker-{worker_id}] parse_data_window failed for {ticker}: {e}"
        )
        _pf = {}

    def _pget(key, default=0.0):
        v = _pf.get(key)
        return v if isinstance(v, (int, float)) else default

    # E. Calculate RR inputs (needed before the direction inference below)
    current_price = _pf.get("price") if _pf.get("price") is not None else safe_float(data_window.get("Close") or data_window.get("C"))
    buy_score = _pf.get("buy") if _pf.get("buy") is not None else safe_float(data_window.get("Buy Score"))
    sell_score = _pf.get("sell") if _pf.get("sell") is not None else safe_float(data_window.get("Sell Score"))

    # # Determine Trade Direction (deterministic filter's chosen_side is authoritative)
    side = str(
        triage.get("chosen_side")
        or survivor.get("_raw", {}).get("side", survivor.get("Direction", "UNKNOWN"))
    ).upper()
    if side not in ("LONG", "SHORT"):
        # Fallback for single-ticker --ticker runs that bypass the sheet cascade
        # and therefore carry no alert-side. Use the indicator's own Direction
        # Probability (bible §5.13 Group B field 14): >50 = bull, <50 = bear.
        dir_prob = _pf.get("dir_prob") if _pf.get("dir_prob") is not None else safe_float(
            data_window.get("Evidence Bias Pct Above 50 Bull")
            or data_window.get("Dir Prob Pct Above 50 Bull")
            or data_window.get("Dir Prob % (>50 bull)")
        )
        if dir_prob > 0:
            side = "LONG" if dir_prob >= 50 else "SHORT"
        else:
            # Dir Prob missing/uncomputed: infer from score + short zone presence.
            short_zone_val = _pf.get("short_zbot")
            if buy_score >= sell_score or short_zone_val is None:
                side = "LONG"
            else:
                side = "SHORT"

    rr_from_current = 0.0
    try:
        if side == "LONG":
            target = _pf.get("long_target") if _pf.get("long_target") is not None else safe_float(data_window.get("Long Target"))
            stop = _pf.get("long_stop_loss") if _pf.get("long_stop_loss") is not None else safe_float(data_window.get("Long Stop Loss"))
            if target is not None and stop is not None and current_price is not None and current_price - stop > 0:
                rr_from_current = (target - current_price) / (current_price - stop)
        elif side == "SHORT":
            target = _pf.get("short_target") if _pf.get("short_target") is not None else safe_float(data_window.get("Short Target"))
            stop = _pf.get("short_stop_loss") if _pf.get("short_stop_loss") is not None else safe_float(data_window.get("Short Stop Loss"))
            if target is not None and stop is not None and current_price is not None and stop - current_price > 0:
                rr_from_current = (current_price - target) / (stop - current_price)
    except Exception as e:
        logger.warning(f"[ThesisWorker-{worker_id}] Failed to calculate RR for {ticker}: {e}")

    if triage.get("rr") is not None:
        rr_from_current = triage["rr"]

    zone_bot = _pf.get("long_zbot")
    zone_top = _pf.get("long_ztop")

    if side == "SHORT":
        czb = _pf.get("short_zbot")
        czt = _pf.get("short_ztop")
    else:
        czb, czt = zone_bot, zone_top
    zone_state = "in_zone"
    if czt is not None and current_price is not None and current_price > czt:
        zone_state = "above_zone"
    elif czb is not None and current_price is not None and current_price < czb:
        zone_state = "below_zone"

    raw_news = news_data.pop("raw_news", "")
    # Headlines are sourced from the basic Alpaca news in the triage gate above;
    # keep them unless empty, in which case fall back to the sentiment summary.
    if not headlines:
        headlines = [h for h in (raw_news.split("\n") if raw_news else []) if h.strip()]

    earnings_days = _next_earnings_days(ticker)
    if earnings_days is None:
        earnings_gate = "UNKNOWN"
    elif earnings_days < 3:
        earnings_gate = "FAIL"
    elif earnings_days < 7:
        earnings_gate = "CAUTION"
    else:
        earnings_gate = "PASS"

    llm_input = {
        "ticker": ticker,
        "price": current_price,
        "dir_prob": _pget("dir_prob"),
        "regime": int(round(_pget("regime"))),
        "ext_pct": _pget("ext_pct"),
        "exhaustion": _pget("exhaustion"),
        "exp_move_pct": _pget("exp_move_pct"),
        "ignition_long": _pget("ignition_long"),
        "rev_zone_l": _pf.get("rev_l") if _pf.get("rev_l") is not None else safe_float(data_window.get("Long Rev Zone")),
        "rev_zone_s": _pf.get("rev_s") if _pf.get("rev_s") is not None else safe_float(data_window.get("Short Rev Zone")),
        "long_zone": [zone_bot, zone_top],
        "long_target": _pf.get("long_target") if _pf.get("long_target") is not None else safe_float(data_window.get("Long Target")),
        "long_stop": _pf.get("long_stop_loss") if _pf.get("long_stop_loss") is not None else safe_float(data_window.get("Long Stop Loss")),
        "ma200": _pget("ma200"),
        "avwap_res": _pf.get("avwap_resistance") if _pf.get("avwap_resistance") is not None else safe_float(data_window.get("AVWAP Resistance")),
        "avwap_sup": _pf.get("avwap_support") if _pf.get("avwap_support") is not None else safe_float(data_window.get("AVWAP Support")),
        "golden_cross": _pf.get("golden_cross") if _pf.get("golden_cross") is not None else safe_float(data_window.get("Golden Cross")),
        "death_cross": _pf.get("death_cross") if _pf.get("death_cross") is not None else safe_float(data_window.get("Death Cross")),
        "dominant_side": side.lower(),
        "zone_state": zone_state,
        "rr_from_current": rr_from_current,
        "rr_to_target": _pget("rr_to_target"),
        # R-VRVP companion. The local gem documents these and builds five soft flags on
        # them (into_supply / below_value / above_value / volume_confirmed /
        # low_volume_breakout), but they were never sent — so those rules could not fire.
        # None (not 0.0) when the companion is off the chart, since the gem is told not to
        # infer a missing VP field and 0.0 would read as a real price.
        "poc": _pf.get("vp_poc") if _pf.get("vp_poc") is not None else safe_float(data_window.get("VP POC"), None),
        "vah": _pf.get("vp_vah") if _pf.get("vp_vah") is not None else safe_float(data_window.get("VP VAH"), None),
        "val": _pf.get("vp_val") if _pf.get("vp_val") is not None else safe_float(data_window.get("VP VAL"), None),
        "hvn_above": _pf.get("vp_hvn_above") if _pf.get("vp_hvn_above") is not None else safe_float(data_window.get("VP HVN Above"), None),
        "hvn_below": _pf.get("vp_hvn_below") if _pf.get("vp_hvn_below") is not None else safe_float(data_window.get("VP HVN Below"), None),
        "rvol": _pf.get("rvol") if _pf.get("rvol") is not None else safe_float(data_window.get("RVOL Vs Avg"), None),
        "ev_r": safe_float(triage.get("ev_r")),
        "win_prob": safe_float(triage.get("win_prob")),
        "computed_flags": list(triage.get("flags") or []),
        "recency": triage.get("recency"),
        "earnings_days": earnings_days if earnings_days is not None else -1,
        "earnings_gate": earnings_gate,
        "catalyst_move_summary": data_window.get("_catalyst_move_summary"),
        "earnings_history": data_window.get("_earnings_reaction_events"),
        "news_sentiment": news_data.get("sentiment", "NEUTRAL"),
        "news_catalyst": news_data.get("catalyst", "none"),
        "today": today_str,
        "headlines": headlines[:5],
        "screener_setup": screener_setup,
        "income_only": income_only,
        "structure": triage.get("structure"),
        "structure_strikes": triage.get("structure_strikes"),
        "iv_rank": triage.get("iv_rank"),
        "triggers": triage.get("triggers"),
        # --- Full Technical Analysis & Tape Context ---
        "weinstein_stage": data_window.get("_weinstein_stage"),
        "stage_age_bars": data_window.get("_stage_age_bars"),
        "weinstein_ma150": safe_float(data_window.get("Weinstein MA 150")),
        "darvas_box_top": safe_float(data_window.get("Darvas Box Top")),
        "darvas_base_status": data_window.get("_darvas_base_status"),
        "darvas_box_duration_bars": data_window.get("_darvas_box_duration_bars"),
        "premove_darvas_state": data_window.get("_premove_darvas_state"),
        "active_candlestick_patterns": data_window.get("active_candlestick_patterns") or [],
        "candlestick_summary": data_window.get("candlestick_summary"),
        "mtf_alignment": data_window.get("_mtf_alignment_string"),
        "institutional_flow_bias": data_window.get("_institutional_flow_bias"),
        "volume_accumulation_ratio_60d": data_window.get("_volume_accumulation_ratio_60d"),
        "cmf_20d": data_window.get("_chaikin_money_flow_20d"),
        "is_active_bb_kc_squeeze": data_window.get("_is_active_bb_kc_squeeze"),
        "active_squeeze_bars": data_window.get("_active_squeeze_bars"),
        "squeeze_expansion_profile": data_window.get("_squeeze_expansion_profile"),
        "ma20_fast": safe_float(data_window.get("MA 20 Fast") or data_window.get("sma20")),
        "ma50_mid": safe_float(data_window.get("MA 50 Mid") or data_window.get("sma50")),
        "ma200_slow": safe_float(data_window.get("MA 200 Slow") or data_window.get("sma200")),
        "dist_52w_high_pct": safe_float(data_window.get("dist_52w_high_pct")),
        "high_52w": safe_float(data_window.get("high_52w") or data_window.get("_52w_high")),
        "tastytrade_iv_rank": safe_float(data_window.get("tastytrade_iv_rank")),
        "tastytrade_volatility_regime": data_window.get("tastytrade_volatility_regime"),
        "monte_carlo_p_target_first": safe_float(
            (data_window.get("monte_carlo_horizons") or {}).get("21", {}).get("p_target_first_pct")
            or (data_window.get("monte_carlo_horizons") or {}).get("45", {}).get("p_target_first_pct")
        ),
    }


    # G. Query Local LLM (FREE — local Qwen 9B). This is the cheap, wide-net
    # first pass that decides whether a ticker is strong enough to justify the
    # paid Minimax deep-research pass downstream. No OpenRouter call here.
    logger.info(f"[ThesisWorker-{worker_id}] Querying Local Qwen (free) for {ticker} triage...")
    from src.clients.llm_client import query_local_llm

    prompt_path = config.BASE_DIR / "gems" / "revanth-gem-local.md"
    system_prompt = "You are a financial analyst."
    if prompt_path.exists():
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()

    # Add few-shot example for better JSON consistency
    few_shot_example = """
Example 1 (Directional):
Input:
{
  "ticker": "AAPL", "price": 178.5,
  "dir_prob": 61,
  "regime": 1, "ext_pct": 35, "exhaustion": 0.2,
  "ignition_long": 0, "rev_zone_l": 8, "rev_zone_s": 2,
  "dominant_side": "long", "zone_state": "above_zone", "rr_from_current": 2.1,
  "computed_flags": ["extended"],
  "earnings_days": -1, "earnings_gate": "UNKNOWN",
  "headlines": ["AAPL beats earnings expectations", "Analysts raise price targets"],
  "today": "2026-07-17"
}
Output:
{"ticker": "AAPL", "dominant_side": "long", "entry_mode": "TREND_LONG", "rev_zone": "L:Z2", "confirm_contradict": "CONFIRMS", "catalyst": "earnings beat", "news_sentiment": "bullish", "key_flags": ["extended"], "reasoning": "TREND_LONG fires with dir_prob=61 >= 50, upside edge confirmed by volume and moving averages. News CONFIRMS with earnings beat + upgrades. extended flag from ext_pct=35 + regime=1. PASS triage.", "triage": "PASS", "conviction": 7, "send_for_deep_research": true}

Example 2 (Income / Structure):
Input:
{
  "ticker": "MSFT", "price": 410.0,
  "dir_prob": 50,
  "regime": 0, "ext_pct": 8, "exhaustion": 0.1,
  "income_only": false, "structure": "cash_secured_put_or_put_credit",
  "structure_strikes": {"put_1_25x": 385.0, "put_1_50x": 380.0},
  "iv_rank": 65, "screener_setup": "Put-Sell Timing (Research)",
  "dominant_side": "long", "computed_flags": [],
  "earnings_days": 28, "earnings_gate": "PASS",
  "headlines": ["Tech sector consolidating in healthy range"],
  "today": "2026-07-17"
}
Output:
{"ticker": "MSFT", "dominant_side": "long", "entry_mode": "INCOME_CSP", "rev_zone": "-", "confirm_contradict": "NEUTRAL", "catalyst": "none", "news_sentiment": "neutral", "key_flags": [], "reasoning": "INCOME_CSP fires on Put-Sell Timing setup. Structure supports CSP/put credit at support with IV rank 65. Target 1.25x/1.50x put strikes at 385/380. PASS triage.", "triage": "PASS", "conviction": 6, "send_for_deep_research": false}

Example 3 (Base Compression / Reversal / Floor Defense):
Input:
{
  "ticker": "SYNTH", "price": 50.0,
  "weinstein_stage": 1, "darvas_base_status": "Mature Base (300 bars) - High Breakout Compression",
  "active_candlestick_patterns": ["🟢 Bullish Daily Closingmarubozu", "🟢 Bullish Daily Longline"],
  "mtf_alignment": "M ✓ W ✓ D ✓",
  "institutional_flow_bias": "Institutional Accumulation",
  "is_active_bb_kc_squeeze": true,
  "dist_52w_high_pct": -2.5,
  "monte_carlo_p_target_first": 77.0,
  "long_zone": [49.50, 50.20], "long_target": 55.0, "long_stop": 48.0,
  "dominant_side": "long", "zone_state": "in_zone", "rr_from_current": 2.5,
  "today": "2026-10-05"
}
Output:
{"ticker": "SYNTH", "dominant_side": "long", "entry_mode": "BREAKOUT_LONG", "rev_zone": "-", "confirm_contradict": "NEUTRAL", "catalyst": "300-bar Stage 1 base compression breakout", "news_sentiment": "neutral", "key_flags": ["base_compression", "mtf_aligned", "squeeze", "bullish_candlesticks"], "reasoning": "Stage 1 Mature Base (300 bars) in active BB-KC squeeze near 52w high (-2.5%). M✓W✓D✓ MTF long aligned with Bullish Daily Closingmarubozu and institutional accumulation. In zone at 50.0 with 2.5:1 R:R (Target: 55.0, Stop: 48.0). 77% Monte Carlo target touch probability. PASS triage.", "triage": "PASS", "conviction": 8, "send_for_deep_research": true}
"""

    # Build user prompt with few-shot example for better JSON consistency
    user_prompt = (
        few_shot_example.strip() + "\n\n---\n\nActual Data:\n" + json.dumps(llm_input, indent=2)
    )

    if enrich:
        user_prompt += (
            "\n\nTRAJECTORY INSTRUCTION:\n"
            "You must also generate a 'trajectory' string (<= 500 chars). Use the provided static levels "
            "(ma200, long_zone, avwap_res, avwap_sup, poc, vah, val, hvn_above, hvn_below) and evaluate the candidate prices "
            "(52wHigh/zone/MA50/MA200) against the explicitly graded LANE DEFINITIONS (reversal=+edge, pullback/breakout=flat/exclusion). "
            "Provide an explicit trajectory narration."
        )

    # LOCAL Qwen is FREE and the server is launched with --reasoning off, so the model
    # never emits a <think> token that would trip the local GBNF grammar (HTTP 400). The
    # local path now uses a strict json_schema (GBNF grammar) since thinking is OFF, and
    # _extract_json_response parses the JSON. There is NO remote fallback: the GLM/
    # OpenRouter free tier rate-limits under the 148-ticker sweep and was an unreliable
    # external dependency. The deterministic filter already owns the verdict, so a local
    # miss degrades gracefully to the deterministic fallback. Every attempt is local + free.
    from src.clients.llm_client import _extract_json_response

    llm_json = {}
    # Attempt ladder: (use_openrouter, max_tokens, disable_thinking).
    #   RELIABILITY-FIRST: ALL attempts are LOCAL (free Qwen, thinking OFF). The remote
    #   GLM/OpenRouter "rescue" was removed — it rate-limits under the 148-ticker sweep
    #   and added a flaky external dependency. The deterministic filter already owns the
    #   verdict, so a local miss degrades gracefully to the deterministic fallback. The
    #   ladder is now pure-local retries with escalating output budgets (cheap insurance
    #   against transient truncation), then the deterministic verdict.
    #   Attempt 1: LOCAL, think OFF, default budget — workhorse (schema forces valid JSON).
    #   Attempt 2: LOCAL, think OFF, 4x budget — retry if attempt 1 was truncated.
    #   Attempt 3: LOCAL, think OFF, 8x budget — second retry.
    #   Else:      deterministic fallback verdict (no external dependency, ever).

    _attempts = [
        (False, int(os.getenv("LLM_TRIAGE_MAX_TOKENS", "1536")), True),  # 1: local, think OFF
        (
            False,
            int(os.getenv("LLM_TRIAGE_MAX_TOKENS", "1536")) * 2,
            True,
        ),  # 2: local, think OFF, 2x
        (
            False,
            int(os.getenv("LLM_TRIAGE_MAX_TOKENS", "1536")) * 4,
            True,
        ),  # 3: local, think OFF, 4x
    ]
    det_pass = triage.get("triage") == "PASS"  # authoritative; defined at function scope
    for _attempt, (_remote, _mt, _dt) in enumerate(_attempts, start=1):
        _where = "remote" if _remote else "local"
        _think = "think-on" if not _dt else "think-off"
        llm_response = query_local_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=True,
            max_tokens=_mt,
            use_openrouter=_remote,  # all local now (no remote dependency)
            use_tools=False,  # triage reads provided numbers, no web search
            disable_thinking=_dt,  # all attempts think-off (server launched --reasoning off)
            json_schema=_TRIAGE_SCHEMA,  # GBNF grammar: always valid, complete JSON
            model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
        )
        if llm_response:
            try:
                llm_json = _extract_json_response(llm_response)
            except Exception as e:
                logger.error(
                    f"[Analyzer-{worker_id}] Failed to parse LLM JSON output: {e}\nRaw Output: {llm_response}"
                )
                llm_json = {}
        if llm_json:
            logger.info(
                f"[Analyzer-{worker_id}] LLM Triage Verdict: {llm_json.get('triage')} (Conviction {llm_json.get('conviction')}) [attempt {_attempt}, {_where}, {_think}, max_tokens={_mt}]"
            )
            break
        if _attempt < len(_attempts):
            _next = "retrying (think-off, larger budget)"
        else:
            _next = "giving up -> deterministic fallback"
        logger.warning(
            f"[Analyzer-{worker_id}] {ticker}: empty/unparseable LLM triage (attempt {_attempt}, {_where}, {_think}, max_tokens={_mt}); {_next}."
        )
    else:
        # LLM returned nothing parseable on ALL attempts (empty content / truncation /
        # server hiccup). An ABSENT opinion must NOT act as a veto — per the rule
        # above, the LLM can only VETO via a news CONTRADICTION, never by being
        # missing. Fall back to the deterministic verdict; only the hard earnings
        # gate (computable without the LLM) still vetoes.
        llm_json = {
            "triage": triage.get("triage"),
            "dominant_side": side.lower(),
            "key_flags": list(triage.get("flags") or []),
            "send_for_deep_research": bool(det_pass and earnings_gate != "FAIL"),
            "llm_failed": True,
            "bull_case": None,
            "bear_case": None,
            "bull_rebuttal": None,
            "bear_rebuttal": None,
            "moderator_agreement": None,
            "moderator_disagreement": None,
        }
        if det_pass and earnings_gate == "FAIL":
            llm_json["triage"] = "WATCH"
            llm_json["earnings_override"] = f"earnings in {earnings_days}d"
        logger.warning(
            f"[Analyzer-{worker_id}] {ticker}: LLM returned no parseable JSON — "
            f"falling back to deterministic verdict ({triage.get('triage')}), "
            f"send_for_deep_research={llm_json['send_for_deep_research']}."
        )

    # ── MULTI-ROUND BULL / BEAR DEBATE ───────────────────────────────────
    # We use a TradingAgents-style debate architecture on the free Local LLM.
    # Round 1 (Parallel): Bull and Bear read technicals + news and write openings.
    # Round 2 (Parallel): Bull and Bear read each other's openings and rebut them.
    # Round 3 (Moderator): Synthesizes exact points of agreement/disagreement.
    # The moderator summary is passed to the Brain (Muse Spark) to settle.
    import concurrent.futures as _cf

    _debate_data = json.dumps({
        "ticker": ticker, "price": current_price,
        "regime": int(round(_pget("regime"))),
        "ext_pct": _pget("ext_pct"), "exhaustion": _pget("exhaustion"),
        "dir_prob": _pget("dir_prob"),
        "rev_zone_l": safe_float(data_window.get("Long Rev Zone")),
        "rev_zone_s": safe_float(data_window.get("Short Rev Zone")),
        "zone_state": zone_state, "rr_from_current": round(rr_from_current, 2),
        "headlines": headlines[:5],
    }, indent=2)

    def _run_debate(sys_prompt: str, usr_prompt: str, max_toks: int = 256) -> str:
        try:
            resp = query_local_llm(
                system_prompt=sys_prompt, user_prompt=usr_prompt,
                json_mode=False, max_tokens=max_toks, use_openrouter=False,
                use_tools=False, disable_thinking=True, model=os.getenv("LOCAL_LLM_MODEL", "gpt-4"),
            )
            return (resp or "").strip()
        except Exception as e:
            logger.warning(f"[Debate-{worker_id}] {ticker} call failed: {e}")
            return ""

    # ROUND 1
    _BULL_R1_SYS = "You are a BULLISH equity researcher. Build the absolute strongest 2-sentence argument for why this stock is a great LONG trade right now. Use BOTH the technical data and news headlines provided. Output ONLY the 2 sentences."
    _BEAR_R1_SYS = "You are a BEARISH equity researcher. Build the absolute strongest 2-sentence argument for why this stock is a terrible LONG trade/trap. Use BOTH the technical data and news headlines provided. Output ONLY the 2 sentences."
    
    bull_case = ""
    bear_case = ""
    with _cf.ThreadPoolExecutor(max_workers=2) as dpool:
        bf1 = dpool.submit(_run_debate, _BULL_R1_SYS, _debate_data)
        br1 = dpool.submit(_run_debate, _BEAR_R1_SYS, _debate_data)
        bull_case = bf1.result(timeout=30)
        bear_case = br1.result(timeout=30)

    # ROUND 2
    _BULL_R2_SYS = "You are a BULLISH equity researcher. Read the Bear's argument and the original data. Rebut their specific claims in exactly 2 sentences and re-assert the long edge."
    _BEAR_R2_SYS = "You are a BEARISH equity researcher. Read the Bull's argument and the original data. Rebut their specific claims in exactly 2 sentences and assert why it's a trap."
    
    bull_rebuttal = ""
    bear_rebuttal = ""
    with _cf.ThreadPoolExecutor(max_workers=2) as dpool:
        bf2 = dpool.submit(_run_debate, _BULL_R2_SYS, f"DATA:\n{_debate_data}\n\nBEAR'S ARGUMENT:\n{bear_case}")
        br2 = dpool.submit(_run_debate, _BEAR_R2_SYS, f"DATA:\n{_debate_data}\n\nBULL'S ARGUMENT:\n{bull_case}")
        bull_rebuttal = bf2.result(timeout=30)
        bear_rebuttal = br2.result(timeout=30)

    # ROUND 3
    _MOD_SYS = "You are an impartial moderator synthesizing a stock debate between a Bull and a Bear. Based on their openings and rebuttals, output EXACTLY 3 points they agree on, and EXACTLY 3 points of fierce disagreement."
    _MOD_USR = f"BULL OPENING:\n{bull_case}\n\nBEAR OPENING:\n{bear_case}\n\nBULL REBUTTAL:\n{bull_rebuttal}\n\nBEAR REBUTTAL:\n{bear_rebuttal}"
    
    mod_out = _run_debate(_MOD_SYS, _MOD_USR, max_toks=512)
    
    # Split the moderator output if it clearly uses headers, otherwise just store it
    mod_agree = ""
    mod_disagree = ""
    if "isagree" in mod_out.lower():
        parts = mod_out.split("isagree")
        mod_agree = parts[0].strip()
        mod_disagree = "Disagree" + parts[1].strip()
    else:
        mod_agree = mod_out

    logger.info(f"[Debate-{worker_id}] {ticker}: 3 Rounds Complete. Mod output {len(mod_out)} chars.")

    llm_json["bull_case"] = bull_case or None
    llm_json["bear_case"] = bear_case or None
    llm_json["bull_rebuttal"] = bull_rebuttal or None
    llm_json["bear_rebuttal"] = bear_rebuttal or None
    llm_json["moderator_agreement"] = mod_agree or None
    llm_json["moderator_disagreement"] = mod_disagree or None

    # Authoritative deep-research flag — recomputed for EVERY ticker (whether the
    # LLM succeeded or fell back) from the DETERMINISTIC verdict, NOT from whatever
    # the model happened to emit. The model may return send_for_deep_research=null
    # or omit it, which would otherwise leave it unset and never trigger deep
    # research. This runs unconditionally after the LLM attempt loop.
    #
    # Eligibility requires ALL of:
    #   - deterministic PASS (technicals own the setup),
    #   - deterministic conviction >= MIN_CONVICTION_FOR_DEEP_RESEARCH (stable,
    #     reproducible — NOT the flaky LLM 1-10 score, which is only informational),
    #   - ev_r >= TIER_A_MIN_EV_R (a thin-EV PASS, e.g. 0.11, must not crowd out a
    #     3.04 name just because both are technical PASS),
    #   - earnings gate != FAIL (risk gate, independent of news tone).
    #
    # NEWS IS ONE INPUT, NOT A VETO. A news CONTRADICTION is recorded as a flag and
    # folded into the deterministic RANK SCORE as a soft penalty (so a CONTRADICTS
    # name sinks below a clean one at equal EV) — it can NEVER force WATCH or block
    # deep research. The deterministic technical verdict owns the setup.
    # The LLM's own PASS/WATCH is a SOFT hint only; a flaky LLM WATCH must never
    # veto a technically-valid candidate.
    min_ev_r = config.TIER_A_MIN_EV_R  # for the ev_floor_override message below
    det_ev_r = triage.get("ev_r")
    # News tone (negative headline sentiment OR an explicit LLM contradiction) is
    # ONE INPUT — folded into the rank as a SOFT penalty (see deep_research_sort_key),
    # never a veto. Persist the flags onto BOTH the llm_data record AND the
    # deterministic triage record: the paid cap ranks off the persisted `triage`
    # dict (deep_research._load_triage_record), so it must carry the same news
    # awareness as the enrichment-selection ranking — otherwise a contradiction-
    # penalised name could rank right back in at the cap.
    contradicts = str(llm_json.get("confirm_contradict", "")).upper() == "CONTRADICTS"
    news_negative = bool(triage.get("news_negative"))
    llm_json["news_contradiction"] = contradicts
    llm_json["news_negative"] = news_negative
    triage["news_contradiction"] = contradicts
    triage["news_negative"] = news_negative

    # Single shared gate (byte-identical logic to the prefilter pass).
    quality_pass, send, rank_score, has_plan = _deep_research_gate(
        triage,
        earnings_gate,
        news_contradiction=contradicts,
        news_negative=news_negative,
    )
    llm_json["rank_score"] = rank_score
    llm_json["enriched"] = True
    llm_json["send_for_deep_research"] = send
    llm_json["screener_setup"] = screener_setup
    llm_json["income_only"] = income_only
    llm_json["structure"] = triage.get("structure")
    llm_json["structure_strikes"] = triage.get("structure_strikes")
    llm_json["iv_rank"] = triage.get("iv_rank")

    # D2: Restrict LLM CUTs: LLM cannot delete/cut on stagnation or no-catalyst
    raw_llm_triage = llm_json.get("triage")
    if raw_llm_triage:
        iv_r = triage.get("iv_rank")
        sq_on = bool(data_window.get("_is_active_bb_kc_squeeze"))
        san_triage, cut_reas = _sanitize_llm_cut_verdict(
            raw_llm_triage,
            triage,
            llm_json.get("key_flags") or [],
            earnings_gate,
            iv_rank=iv_r,
            squeeze_on=sq_on,
        )
        llm_json["triage"] = san_triage
        if san_triage == "CUT":
            triage["cut_reason"] = cut_reas
            triage["cut_spot"] = current_price or bar_spot
            llm_json["cut_reason"] = cut_reas
            llm_json["cut_spot"] = current_price or bar_spot
        elif raw_llm_triage == "CUT" and san_triage == "WATCH":
            llm_json["cut_overruled"] = True
            llm_json["cut_overruled_reason"] = cut_reas
            if cut_reas == "coil_compression_not_cut":
                triage["setup_lane"] = "COIL"
                llm_json["setup_lane"] = "COIL"

    llm_json["dw_bar_date"] = dw_bar_date
    llm_json["spot_age"] = spot_age
    llm_json["stale_data"] = stale_data
    llm_json["live_spot"] = live_spot
    llm_json["move_since_bar_pct"] = move_since_bar_pct
    llm_json["cut_reason"] = triage.get("cut_reason")
    llm_json["cut_spot"] = triage.get("cut_spot")

    if quality_pass and not send:
        llm_json["triage"] = "WATCH"
        if earnings_gate == "FAIL":
            llm_json["earnings_override"] = f"earnings in {earnings_days}d"
        elif det_ev_r is not None and det_ev_r < min_ev_r:
            llm_json["ev_floor_override"] = f"ev_r {det_ev_r} < {min_ev_r}"

    # H. Alpha Vantage Fetch (Moved to the paid pass in deep_research.py)
    av_sentiment = {}
    av_earnings = {}

    # Markdown thesis generation removed — the canonical decision record is the
    # _thesis.json written below. (deep_research.py and run_local_research.py now
    # discover/move tickers via _thesis.json, not _thesis.md.)

    # Social sentiment (news + Reddit retail buzz) from Adanos free API
    # (Moved to the paid pass in deep_research.py)
    social_sentiment = {}

    # I. Persist the decision locally (staging BEFORE any Google Sheets write).
    # This per-ticker JSON is the canonical local record of what research was
    # done and the resulting decision. It is written for EVERY ticker, including
    # --ticker runs that have no _row_index and therefore won't be pushed to
    # Sheets in this pass.
    ticker_dir = out_dir / safe_ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    thesis_json_path = ticker_dir / f"{safe_ticker}_thesis.json"
    triage_json_path = ticker_dir / f"{safe_ticker}_triage.json"

    result_dict = {
        "ticker": safe_ticker,
        "trade_id": trade_id,
        "row_index": survivor.get("_row_index"),
        "researched_at": datetime.now().isoformat(timespec="seconds"),
        "llm_data": llm_json,
        "triage": triage,
        "av_sentiment": av_sentiment,
        "av_earnings": av_earnings,
        "social_sentiment": social_sentiment,
        "thesis_json": str(thesis_json_path),
        "user_position": None,
        "dw_bar_date": dw_bar_date,
        "spot_age": spot_age,
        "stale_data": stale_data,
        "live_spot": live_spot,
        "move_since_bar_pct": move_since_bar_pct,
        "cut_reason": triage.get("cut_reason"),
        "cut_spot": triage.get("cut_spot"),
    }

    try:
        with open(thesis_json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4)
        with open(triage_json_path, "w", encoding="utf-8") as f:
            json.dump(triage, f, indent=4)
    except Exception as e:
        logger.error(f"[Analyzer-{worker_id}] Failed to cache thesis/triage JSON for {ticker}: {e}")

    # Aggregate into a per-date ledger so there is one consolidated view of
    # every ticker's research + decision prior to the Sheets batch update.
    _update_research_ledger(out_dir, result_dict)

    # Always return result_dict so the caller can (a) segregate the ticker into
    # _DEEP_RESEARCH/force for the paid deep-research pass (gated on the LLM's
    # send_for_deep_research flag inside llm_data) and (b) push to Sheets when a
    # sheet row exists. Pushing to Sheets is gated downstream on _row_index — a
    # missing _row_index must NOT also suppress deep-research segregation (it used
    # to return None here, silently dropping deep-research-flagged tickers).
    if not survivor.get("_row_index"):
        logger.warning(
            f"[Analyzer-{worker_id}] No _row_index for {ticker}; decision saved locally + deep-research eligible, but not pushed to Sheets."
        )
    return result_dict


def _update_research_ledger(out_dir: Path, record: dict):
    """Merge a ticker's decision record into the per-date consolidated results.

    `data/{date}/consolidate/consolidated_results.json` is the staging file read
    before the Google Sheets write, giving one consolidated view of all
    local-research decisions for a date (idempotent across reruns)."""
    ledger_dir = out_dir / "consolidate"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "consolidated_results.json"
    ticker = record.get("ticker")
    if not ticker:
        return
    try:
        with _ledger_lock:
            ledger = {}
            if ledger_path.exists():
                try:
                    with open(ledger_path, "r", encoding="utf-8") as f:
                        ledger = json.load(f)
                except Exception:
                    ledger = {}
            ledger[ticker] = record
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to update research ledger: {e}")


if __name__ == "__main__":
    # Direct invocation runs the local-LLM research phase over today's scraped
    # survivors (the same work run_local_research.py performs). This file is a
    # module of worker functions, so it needs this entry point to be runnable
    # on its own. For the FULL pipeline (scrape + local + deep) use the run_*.py
    # scripts at the repo root, or `python -m run_local_research`.
    import argparse
    import re
    from datetime import datetime

    ap = argparse.ArgumentParser(description="Run local-LLM survivor research (free)")
    ap.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD); a non-date token is treated as --ticker.",
    )
    ap.add_argument("--ticker", type=str, help="Run only on a specific ticker")
    ap.add_argument(
        "--force", type=str, default="", help="Comma-separated tickers to force into deep research."
    )
    args = ap.parse_args()

    target_date = args.date
    target_ticker = args.ticker
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    force_tickers = {t.strip().upper() for t in args.force.split(",") if t.strip()}

    # Imported lazily to keep this module importable from the worker context.
    from run_local_research import run_local_research

    run_local_research(
        target_date or datetime.now().strftime("%Y-%m-%d"), target_ticker, force_tickers
    )
