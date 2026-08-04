import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from src import config
from src.clients.news_client import get_ticker_news
from src.data.tv_scraper import TVScraper
from src.logic.data_window_filter import normalize_number_str

logger = logging.getLogger(__name__)


def _find_artifact(out_dir: Path, filename: str) -> Path:
    """Finds an artifact in out_dir, or the triage segregation folders if it was moved."""
    p = out_dir / filename
    if p.exists():
        return p
    triage_dir = config.BASE_DIR / "data" / "triage" / out_dir.name
    for sub in ("_DEEP_RESEARCH", "force"):
        d = triage_dir / sub / filename
        if d.exists():
            return d
    return p


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
    },
    "required": ["dominant_side", "entry_mode", "confirm_contradict", "triage", "conviction"],
}


def safe_float(val, default=0.0):
    try:
        s = normalize_number_str(val)
        for p in ["C", "O", "H", "L"]:
            s = s.replace(p, "")
        s = s.replace(",", "").replace("%", "").replace(" ", "").strip()
        if not s:
            return default
        return float(s)
    except:
        return default


def _next_earnings_days(ticker: str):
    from src.clients.earnings_client import get_next_earnings_days
    return get_next_earnings_days(ticker)


def scrape_survivor_task(survivor, out_dir, today_str, worker_id):
    ticker = survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", "")
    if not ticker:
        logger.warning(f"[Scraper-{worker_id}] Survivor dict has no Ticker key: {survivor}")
        return

    ticker = ticker.strip().upper()
    safe_ticker = ticker.replace(":", "_")

    json_path = out_dir / f"{safe_ticker}_datawindow.json"
    chart_path = out_dir / f"{safe_ticker}_chart.png"

    if json_path.exists() and chart_path.exists():
        logger.info(
            f"[Scraper-{worker_id}] Data Window and Screenshot already exist for {ticker}. Skipping scrape!"
        )
        return

    logger.info(f"[Scraper-{worker_id}] Scraping TradingView for {ticker}...")
    try:
        scraper = TVScraper(worker_id=worker_id)
        scraper.capture_ticker(ticker)
    except Exception as e:
        logger.error(f"[Scraper-{worker_id}] Scraper failed for {ticker}: {e}")


def _deep_research_gate(triage, earnings_gate, news_contradiction=False, news_negative=False):
    """SINGLE source of truth for the deep-research gate + informational rank score.

    Called by BOTH prefilter_ticker (pass 1, deterministic) and generate_thesis_task
    (pass 2, enriched) so the two stages can never drift. Returns:
        (quality_pass, send, rank_score, has_plan)

    Eligibility (``send``) requires ALL of:
      - quality_pass: deterministic PASS, OR a WATCH whose conviction clears
        WATCH_MIN_CONVICTION (near-zone, high-quality, not-yet-triggered setups),
      - conviction >= MIN_CONVICTION_FOR_DEEP_RESEARCH (stable deterministic score,
        NOT the flaky LLM 1-10),
      - ev_r >= TIER_A_MIN_EV_R (a thin-EV name must not crowd out a high-EV one),
      - earnings gate != FAIL,
      - has_plan: a complete zone + stop + target (something for the paid pass to
        evaluate).
      - setup mode is NOT "NONE": early-trend names without a setup are excluded.
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

    quality_pass = det_pass or (
        det == "WATCH" and det_conv is not None and det_conv >= min_watch_conv
    )
    plan = (
        triage.get("long_plan") if triage.get("chosen_side") == "long" else triage.get("short_plan")
    )
    has_plan = bool(
        plan
        and plan.get("zone")
        and all(v is not None for v in plan["zone"])
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

    if is_reversion:
        # A genuine Z1/Z0 reversal will almost always have a LOW trend-conviction
        # (Buy/Sell) score by construction — that low score is what makes it a
        # reversal, not a trend. Gate on the purpose-built Rev Zone metric instead.
        conviction_ok = rev_score is not None and rev_score >= min_rev_zone
    else:
        conviction_ok = det_conv is None or det_conv >= min_conviction

    send = bool(
        quality_pass
        and triage.get("pursue") is True
        and earnings_gate != "FAIL"
        and conviction_ok
        and (det_ev_r is None or det_ev_r >= min_ev_r)
        and has_plan
        and mode != "NONE"
        and not blocked
    )
    # Informational scalar (mirrors deep_research_sort_key's news penalty on the
    # ev axis so the logged number tracks the real ordering intent).
    ev_score = det_ev_r if det_ev_r is not None else -1e9
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
                str(csv_path), str(json_path)
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
    }
    try:
        with open(thesis_json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4)
    except Exception as e:
        logger.error(f"[Prefilter-{worker_id}] Failed to cache thesis JSON for {ticker}: {e}")
    _update_research_ledger(out_dir, result_dict)
    if not survivor.get("_row_index"):
        logger.warning(
            f"[Prefilter-{worker_id}] No _row_index for {ticker}; saved locally, not pushed to Sheets."
        )

    logger.info(
        f"[Prefilter-{worker_id}] {ticker}: det={triage.get('triage')} "
        f"rank={round(ev_score, 3)} send={send} news_neg={news_negative} contradicts={contradicts}"
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
    }


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
                str(csv_path), str(json_path)
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
    sentiment = triage.get("sentiment", {})
    logger.info(
        f"[ThesisWorker-{worker_id}] Pre-filter {ticker}: triage={triage['triage']} "
        f"side={triage['chosen_side']} sentiment={sentiment.get('label')} "
        f"pursue={triage['pursue']}"
    )

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

    # E. Calculate RR inputs (needed before the direction inference below)
    current_price = safe_float(data_window.get("Close") or data_window.get("C"))
    buy_score = safe_float(data_window.get("Buy Score"))
    sell_score = safe_float(data_window.get("Sell Score"))

    # # Determine Trade Direction (deterministic filter's chosen_side is authoritative)
    side = str(
        triage.get("chosen_side")
        or survivor.get("_raw", {}).get("side", survivor.get("Direction", "UNKNOWN"))
    ).upper()
    if side not in ("LONG", "SHORT"):
        # Fallback for single-ticker --ticker runs that bypass the sheet cascade
        # and therefore carry no alert-side. Use the indicator's own Direction
        # Probability (bible §5.13 Group B field 14): >50 = bull, <50 = bear.
        # This is the Grinold-Kahn standardized/damped score, i.e. the
        # canonical directional read, NOT a raw buy>vs>sell comparison.
        dir_prob = safe_float(data_window.get("Dir Prob % (>50 bull)"))
        if dir_prob > 0:
            side = "LONG" if dir_prob >= 50 else "SHORT"
        else:
            # Dir Prob missing/uncomputed: infer from score + short zone presence.
            short_zone = str(data_window.get("Short Entry Zone Bot", "")).strip()
            if buy_score >= sell_score or short_zone in ("", "∅", "N/A", "None"):
                side = "LONG"
            else:
                side = "SHORT"

    rr_from_current = 0.0
    try:
        if side == "LONG":
            target = safe_float(data_window.get("Long Target"))
            stop = safe_float(data_window.get("Long Stop Loss"))
            if current_price - stop > 0:
                rr_from_current = (target - current_price) / (current_price - stop)
        elif side == "SHORT":
            target = safe_float(data_window.get("Short Target"))
            stop = safe_float(data_window.get("Short Stop Loss"))
            if stop - current_price > 0:
                rr_from_current = (current_price - target) / (stop - current_price)
    except Exception as e:
        logger.warning(f"[ThesisWorker-{worker_id}] Failed to calculate RR for {ticker}: {e}")

    if triage.get("rr") is not None:
        rr_from_current = triage["rr"]

    zone_bot = safe_float(data_window.get("Long Entry Zone Bot"))
    zone_top = safe_float(data_window.get("Long Entry Zone Top"))

    if side == "SHORT":
        czb = safe_float(data_window.get("Short Entry Zone Bot"))
        czt = safe_float(data_window.get("Short Entry Zone Top"))
    else:
        czb, czt = zone_bot, zone_top
    zone_state = "in_zone"
    if czt and current_price > czt:
        zone_state = "above_zone"
    elif czb and current_price < czb:
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
    # Resolve the math fields through the FILTER'S OWN label matcher, never by exact key.
    # The nine fields below were read with hardcoded legacy titles ("Ext% (vs MA200)",
    # "Stage (1=Base,2=Up,3=Top,4=Dn)", "Dir Prob % (>50 bull)", ...). The Pine export
    # titles changed, so every one of them missed and safe_float(None) handed the model
    # 0.0 — dir_prob 0 made TREND_LONG unreachable and TREND_SHORT always true, stage 0
    # disabled both REVERSION modes, and regime 0 read as "Healthy" on every ticker.
    # parse_data_window carries both old and new variants for each label, so it survives
    # the next rename too.
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


    llm_input = {
        "ticker": ticker,
        "price": current_price,
        "buy": buy_score,
        "sell": sell_score,
        "dir_prob": _pget("dir_prob"),
        "stage": int(round(_pget("stage"))),
        "regime": int(round(_pget("regime"))),
        "ext_pct": _pget("ext_pct"),
        "exhaustion": _pget("exhaustion"),
        "exp_move_pct": _pget("exp_move_pct"),
        "ignition_long": _pget("ignition_long"),
        "rev_zone_l": safe_float(data_window.get("Long Rev Zone")),
        "rev_zone_s": safe_float(data_window.get("Short Rev Zone")),
        "long_zone": [zone_bot, zone_top],
        "long_target": safe_float(data_window.get("Long Target")),
        "long_stop": safe_float(data_window.get("Long Stop Loss")),
        "ma200": _pget("ma200"),
        "avwap_res": safe_float(data_window.get("AVWAP Resistance")),
        "avwap_sup": safe_float(data_window.get("AVWAP Support")),
        "golden_cross": safe_float(data_window.get("Golden Cross")),
        "death_cross": safe_float(data_window.get("Death Cross")),
        "dominant_side": side.lower(),
        "opposite_score": sell_score if side == "LONG" else buy_score,
        "zone_state": zone_state,
        "rr_from_current": rr_from_current,
        "rr_to_target": _pget("rr_to_target"),
        # R-VRVP companion. The local gem documents these and builds five soft flags on
        # them (into_supply / below_value / above_value / volume_confirmed /
        # low_volume_breakout), but they were never sent — so those rules could not fire.
        # None (not 0.0) when the companion is off the chart, since the gem is told not to
        # infer a missing VP field and 0.0 would read as a real price.
        "poc": safe_float(data_window.get("VP POC"), None),
        "vah": safe_float(data_window.get("VP VAH"), None),
        "val": safe_float(data_window.get("VP VAL"), None),
        "hvn_above": safe_float(data_window.get("VP HVN Above"), None),
        "hvn_below": safe_float(data_window.get("VP HVN Below"), None),
        "rvol": safe_float(data_window.get("RVOL Vs Avg"), None),
        "ev_r": safe_float(triage.get("ev_r")),
        "win_prob": safe_float(triage.get("win_prob")),
        "computed_flags": list(triage.get("flags") or []),
        "recency": triage.get("recency"),
        "earnings_days": earnings_days if earnings_days is not None else -1,
        "earnings_gate": earnings_gate,
        "news_sentiment": news_data.get("sentiment", "NEUTRAL"),
        "news_catalyst": news_data.get("catalyst", "none"),
        "today": today_str,
        "headlines": headlines[:5],
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
Example Input:
{
  "ticker": "AAPL", "price": 178.5,
  "buy": 72, "sell": 45, "dir_prob": 61,
  "stage": 1, "regime": 1, "ext_pct": 35, "exhaustion": 0.2,
  "ignition_long": 0, "rev_zone_l": 8, "rev_zone_s": 2,
  "dominant_side": "long", "zone_state": "above_zone", "rr_from_current": 2.1,
  "computed_flags": ["extended"],
  "earnings_days": -1, "earnings_gate": "UNKNOWN",
  "headlines": ["AAPL beats earnings expectations", "Analysts raise price targets"],
  "today": "2026-07-17"
}

Example Output:
{"ticker": "AAPL", "dominant_side": "long", "entry_mode": "TREND_LONG", "rev_zone": "L:Z2", "confirm_contradict": "CONFIRMS", "catalyst": "earnings beat", "news_sentiment": "bullish", "key_flags": ["extended"], "reasoning": "TREND_LONG fires with buy=72 > 65, dir_prob=61 >= 50, stage=1 healthy. News CONFIRMS with earnings beat + upgrades. extended flag from ext_pct=35 + regime=1. PASS triage.", "triage": "PASS", "conviction": 7, "send_for_deep_research": true}
"""

    # Build user prompt with few-shot example for better JSON consistency
    user_prompt = (
        few_shot_example.strip() + "\n\n---\n\nActual Data:\n" + json.dumps(llm_input, indent=2)
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
        }
        if det_pass and earnings_gate == "FAIL":
            llm_json["triage"] = "WATCH"
            llm_json["earnings_override"] = f"earnings in {earnings_days}d"
        logger.warning(
            f"[Analyzer-{worker_id}] {ticker}: LLM returned no parseable JSON — "
            f"falling back to deterministic verdict ({triage.get('triage')}), "
            f"send_for_deep_research={llm_json['send_for_deep_research']}."
        )

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
    }

    try:
        with open(thesis_json_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=4)
    except Exception as e:
        logger.error(f"[Analyzer-{worker_id}] Failed to cache thesis JSON for {ticker}: {e}")

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
    ledger = {}
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except Exception:
            ledger = {}
    ticker = record.get("ticker")
    if not ticker:
        return
    ledger[ticker] = record
    try:
        with _ledger_lock:
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
