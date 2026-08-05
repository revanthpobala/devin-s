import glob
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from src import config
from src.clients import adanos_client, alphavantage_client, earnings_client, google_grounding_client
from src.clients.adanos_client import format_market_sentiment_block
from src.clients.llm_client import query_local_llm
from src.logic.thesis_drift import ThesisDriftChecker
from src.tracking.sheets_tracker import SheetsTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import subprocess
from datetime import datetime


def _load_triage_record(raw_dir: Path, deep_dir: Path, ticker: str) -> dict:
    """Load the deterministic triage dict (chosen_side/in_zone/regime/dir_prob/rr)
    persisted in the thesis JSON, for rank_pass_tickers. Returns empty dict if unavailable."""
    for cand in (raw_dir / f"{ticker}_thesis.json", deep_dir / f"{ticker}_thesis.json"):
        if cand.exists():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                rec = data.get("triage")
                if isinstance(rec, dict):
                    rec.setdefault("ticker", ticker.upper())
                    return rec
            except Exception:
                pass
    return {}


def _pull_macro_news(date_str: str) -> str:
    """Run-level macro context (CPI/Fed) — fetched ONCE per run, shared by all tickers."""
    from src.clients.search_client import search_web

    queries = [
        "US CPI inflation report latest Fed interest rate decision",
        f"Federal Reserve rate decision {date_str[:7]} hawkish dovish rates",
    ]
    blocks = []
    for q in queries:
        try:
            res = search_web(q, max_results=3)  # Brave (auto)
        except Exception:
            res = []
        if res:
            blocks.append(
                "Q: "
                + q
                + "\n"
                + "\n".join(f"- [{r.get('title', '')}] {r.get('body', '')}\n" for r in res)
            )
    return "\n".join(blocks) if blocks else "No fresh macro news retrieved."


def _pull_fresh_news(ticker: str, date_str: str) -> str:
    """Pull LIVE, dated news at run time so deep research never relies on a stale
    cached dossier. Covers ticker-specific catalysts."""
    from src.clients.search_client import search_web

    pull_date = datetime.now().strftime("%Y-%m-%d")
    queries = [
        f"{ticker} latest news earnings catalyst today",
        f"{ticker} analyst rating upgrade downgrade price target",
    ]
    blocks = []
    for q in queries:
        try:
            res = search_web(q, max_results=3)
        except Exception as e:
            logger.warning(f"[{ticker}] live search failed for '{q}': {e}")
            res = []
        if res:
            block = f"Q: {q}\n"
            for r in res:
                block += f"- [{r.get('title', '')}] {r.get('body', '')}\n"
            blocks.append(block)
    if not blocks:
        return "No fresh news could be retrieved via live search."
    return (
        f"(Pulled via live web search on system date {pull_date}; research date {date_str})\n"
        + "\n".join(blocks)
    )


def _dossier_stale(dossier_path, date_str: str) -> bool:
    """True if a cached news dossier exists but was written on a different date
    than the research run date — i.e. it may contain outdated macro/earnings info."""
    if not dossier_path.exists():
        return True
    try:
        mtime = datetime.fromtimestamp(dossier_path.stat().st_mtime).date().isoformat()
        return mtime != date_str
    except Exception:
        return True


FLAG_LEGEND = {
    "stage_lag": (
        "Chosen side's MA-stack (price vs MA50/MA200/Weinstein 30wk MA) already confirms the "
        "direction, but the slower Weinstein Stage classifier hasn't caught up yet (it also requires the "
        "average's own slope/volume/RS to confirm). This can be a genuine EARLY reclaim (aggressive/valid entry) "
        "or a premature whipsaw — weigh it against price action and news, don't treat as "
        "either a green light or a red flag by default."
    ),
    "chased": "Price has moved past the technical entry zone in the trade direction.",
    "pullback": "Price missed the zone from above/below but hasn't broken the MA20 timing filter.",
    "exhaustion": "Extended >20% above MA200 with high exhaustion gradient — late-stage risk.",
    "oversold": "Extended >20% below MA200 (short side) with high exhaustion gradient.",
    "churn": "Opposing side's score is also elevated (>=60) — two-sided/indecisive tape.",
    "extended": "Regime 1 — statistically stretched.",
    "squeeze": "Regime 6 — volatility compression, energy building.",
    "counter_trend_high_risk": "This is a REVERSION mode (counter-trend) setup — inherently higher risk.",
    "reversal_against_stage": (
        "This is a reversal bet made WHILE the stock is still in the stage the trend "
        "typically continues in (long reversal in Stage 4 decline, or short reversal in "
        "Stage 2 advance). The screener's own alert logic explicitly excludes this exact "
        "case as higher-risk ('death spiral'/'no counter-trend short in an uptrend') - "
        "weigh this against price action and catalyst, don't treat as a block."
    ),
}


def _format_flags_block(flags: list) -> str:
    if not isinstance(flags, (list, tuple)) or not flags:
        return "(none)"
    lines = ["(informational — weigh, don't treat as a hard veto or green light)"]
    for fl in flags:
        lines.append(f"- {fl}: {FLAG_LEGEND.get(fl, '(no legend — infer from name)')}")
    return "\n".join(lines)

def _format_engine_math_block(rec: dict) -> str:
    """Deterministic values the filter already computed, so the model reads them
    instead of doing the arithmetic. EV/Win-Prob are NOT Data Window exports —
    they are derived in data_window_filter (rrHaircut 0.5) and would otherwise
    have to be recomputed by the LLM, which is where it silently gets them wrong.
    """
    if not isinstance(rec, dict) or not rec:
        return "(unavailable — triage record not found; derive EV yourself if needed)"

    def f(key: str, fmt: str = "{:.2f}") -> str:
        v = rec.get(key)
        return fmt.format(v) if isinstance(v, (int, float)) else "n/a"

    lines = [
        f"- Triage verdict: {rec.get('triage') or 'n/a'} ({rec.get('reason') or 'n/a'})",
        f"- Chosen side / mode: {rec.get('chosen_side') or 'n/a'} / {rec.get('mode') or 'n/a'}",
        f"- Win Prob: {f('win_prob')}   (Dir-Prob mapped through the EV gate, haircut 0.5)",
        f"- Expected Value: {f('ev_r')} R   (= Win Prob x R:R - (1 - Win Prob))",
        f"- R:R used: {f('rr')}   |  Rev score: {f('rev', '{:.1f}')}",
        f"- In zone: {rec.get('in_zone')}   |  Missed (above/below zone): {rec.get('missed')}",
        "(deterministic — these are computed, not model output. Do NOT recompute them.)",
    ]

    return "\n".join(lines)

def _format_unmasked_recency_block(dw_dict: dict) -> str:
    """Format raw recency integer bitmasks (rev_mask, bear_mask, weak_mask) into
    explicit, human-readable pattern lists with clear directional polarities.
    """
    if not isinstance(dw_dict, dict) or not dw_dict:
        return "--- 1b. UNMASKED PATTERN & RECENCY SIGNALS ---\n(no data window dict)"

    def _get(keys):
        for k in keys:
            if k in dw_dict and dw_dict[k] is not None:
                return dw_dict[k]
        return None

    rev_mask = _get(["rev_mask", "reversal pattern mask", "Reversal Pattern Mask"])
    bear_mask = _get(["bear_mask", "bear warning mask", "Bear Warning Mask"])
    weak_mask = _get(["weak_mask", "weak level mask", "Weak Level Mask"])

    rev_age = _get(["rev_age", "reversal pattern age", "Reversal Pattern Age"])
    bear_age = _get(["bear_age", "bear warning age", "Bear Warning Age"])
    weak_age = _get(["weak_age", "weak level age", "Weak Level Age"])

    lines = ["--- 1b. UNMASKED PATTERN & RECENCY SIGNALS (Pre-Decoded by Engine) ---"]

    if rev_mask is not None:
        try:
            val = int(round(float(rev_mask)))
            if val > 0:
                polarity_map = [
                    (1, "KEY_REV_BULL", "🟢 BULLISH"),
                    (2, "KEY_REV_BEAR", "🔴 BEARISH"),
                    (4, "SWEEP_BULL", "🟢 BULLISH"),
                    (8, "SWEEP_BEAR", "🔴 BEARISH"),
                    (16, "FAILSWEEP_BULL", "🔴 BEARISH (Trapped Bulls)"),
                    (32, "FAILSWEEP_BEAR", "🟢 BULLISH (Trapped Bears)"),
                    (64, "TRAP_BULL", "🔴 BEARISH (Bull Trap)"),
                    (128, "TRAP_BEAR", "🟢 BULLISH (Bear Trap)"),
                    (256, "HIKKAKE_BULL", "🟢 BULLISH"),
                    (512, "HIKKAKE_BEAR", "🔴 BEARISH"),
                    (1024, "OOPS_BULL", "🟢 BULLISH"),
                    (2048, "OOPS_BEAR", "🔴 BEARISH"),
                ]
                decoded = []
                bull_cnt, bear_cnt = 0, 0
                for bit, name, pol in polarity_map:
                    if val & bit:
                        decoded.append(f"{name} ({pol})")
                        if "BULLISH" in pol:
                            bull_cnt += 1
                        elif "BEARISH" in pol:
                            bear_cnt += 1
                lines.append(f"Reversal Pattern Mask: {val} (Age: {rev_age if rev_age is not None else 'N/A'} bars ago)")
                lines.append(f"  -> Active Patterns: {', '.join(decoded)}")
                lines.append(f"  -> Net Signals: {bull_cnt} Bullish vs {bear_cnt} Bearish")
            else:
                lines.append("Reversal Pattern Mask: 0 (No active reversal patterns)")
        except Exception as e:
            lines.append(f"Reversal Pattern Mask: {rev_mask} (Decode error: {e})")

    if bear_mask is not None:
        try:
            val = int(round(float(bear_mask)))
            if val > 0:
                from src.logic.data_window_filter import _BEAR_MASK_BITS
                decoded_bear = []
                for bit, name in _BEAR_MASK_BITS.items():
                    if val & bit:
                        pol = "🟢 BULLISH (Hidden Accumulation)" if bit == 16 else "🔴 BEARISH"
                        decoded_bear.append(f"{name} ({pol})")
                lines.append(f"Bear Warning Mask: {val} (Age: {bear_age if bear_age is not None else 'N/A'} bars ago)")
                lines.append(f"  -> Active Warnings: {', '.join(decoded_bear)}")
            else:
                lines.append("Bear Warning Mask: 0 (Clean - no warnings)")
        except Exception as e:
            lines.append(f"Bear Warning Mask: {bear_mask} (Decode error: {e})")

    if weak_mask is not None:
        try:
            val = int(round(float(weak_mask)))
            if val > 0:
                from src.logic.data_window_filter import _WEAK_MASK_BITS
                decoded_weak = []
                for bit, name in _WEAK_MASK_BITS.items():
                    if val & bit:
                        pol = "🟢 BULLISH" if bit == 1 else "🔴 BEARISH"
                        decoded_weak.append(f"{name} ({pol})")
                lines.append(f"Weak Level Mask: {val} (Age: {weak_age if weak_age is not None else 'N/A'} bars ago)")
                lines.append(f"  -> Active Weak Levels: {', '.join(decoded_weak)}")
            else:
                lines.append("Weak Level Mask: 0 (No weakened levels)")
        except Exception as e:
            lines.append(f"Weak Level Mask: {weak_mask} (Decode error: {e})")

    return "\n".join(lines)



def run_deep_research(date_str, target_ticker=None):
    """
    Automates the Deep Research validation phase using OpenAI-compatible tool calling.

    COST GATE: the paid Minimax passes (Pass 1 + Pass 2) run ONLY for tickers the
    free local Qwen triage flagged via send_for_deep_research == true. Batch mode enforces
    this; a manually-specified target_ticker is an explicit request and is always run.
    """
    triage_dir = config.BASE_DIR / "data" / "triage" / date_str
    deep_dir = triage_dir / "_DEEP_RESEARCH"
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str

    def _triage_subdir_for(tkr: str) -> Path | None:
        """Return the triage subfolder holding this ticker's artifacts, or None.
        Segregated tickers live in _DEEP_RESEARCH (auto-flagged by local triage)
        or force (manually forced in via --force); everything else stays in raw."""
        safe = tkr.replace(":", "_")
        for d in (deep_dir, triage_dir / "force"):
            if (d / f"{safe}_chart.png").exists() or (d / f"{safe}_thesis.json").exists():
                return d
        return None

    chart_files = []

    if target_ticker:
        safe_target = target_ticker.replace(":", "_")
        tdir = _triage_subdir_for(target_ticker)
        target_file = (tdir or raw_dir) / f"{safe_target}_chart.png"
        if target_file.exists():
            chart_files.append(str(target_file))
        else:
            search_pattern = str(
                config.BASE_DIR / "data" / "raw" / "**" / f"{safe_target}_chart.png"
            )
            all_matches = glob.glob(search_pattern, recursive=True)
            if all_matches:
                all_matches.sort(key=os.path.getmtime, reverse=True)
                chart_files.append(all_matches[0])
                logger.info(f"[{target_ticker}] Found existing screenshot at {all_matches[0]}")
            else:
                logger.info(
                    f"[{target_ticker}] Screenshot not found. Running scrape + local research..."
                )
                try:
                    base_cmd = [config.get_python_exe()]
                    subprocess.run(
                        base_cmd
                        + [
                            str(config.BASE_DIR / "run_swing_research.py"),
                            "--ticker",
                            target_ticker,
                        ],
                        check=True,
                    )
                    # Scrape phase no longer runs the local-LLM research, so run it
                    # separately so deep research has the news dossier + triage.
                    subprocess.run(
                        base_cmd
                        + [
                            str(config.BASE_DIR / "run_local_research.py"),
                            "--ticker",
                            target_ticker,
                        ],
                        check=True,
                    )
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    new_matches = glob.glob(
                        str(
                            config.BASE_DIR
                            / "data"
                            / "raw"
                            / today_str
                            / "**"
                            / f"{target_ticker}_chart.png"
                        ),
                        recursive=True,
                    )
                    if new_matches:
                        new_matches.sort(key=os.path.getmtime, reverse=True)
                        chart_files.append(new_matches[0])
                    else:
                        logger.error(
                            f"[{target_ticker}] Scraper finished but failed to generate screenshot."
                        )
                        return
                except Exception as e:
                    logger.error(f"[{target_ticker}] Error running scraper: {e}")
                    return
    else:
        # Batch mode: the local-research pipeline already MOVED the
        # deep-research-flagged tickers (send_for_deep_research == True) into
        # data/triage/<date>/_DEEP_RESEARCH, and any --force tickers into
        # data/triage/<date>/force. Those are exactly the tickers we
        # run paid Minimax on, so discover them there directly.
        flagged = []
        for sub in ("_DEEP_RESEARCH", "force"):
            sd = triage_dir / sub
            if not sd.exists():
                continue
            # Discovery is via _thesis.json (markdown generation was removed).
            thesis_files = glob.glob(str(sd / "*_thesis.json"))
            for thesis_file in thesis_files:
                t = Path(thesis_file).name.replace("_thesis.json", "")
                flagged.append((t, _load_triage_record(raw_dir, deep_dir, t)))

        # Deterministic RANK + hard cap: only the top-N setups reach paid research.
        from src.logic.data_window_filter import rank_pass_tickers

        cap = int(os.getenv("DEEP_RESEARCH_CAP", "8"))
        ranked = rank_pass_tickers([r for _, r in flagged if r])
        keep = {str(r.get("ticker", "")).upper() for r in ranked[:cap]}
        logger.info(
            f"Deep-research cap {cap}: {len(flagged)} flagged -> {len(keep)} kept ({sorted(keep)})."
        )
        for t, rec in flagged:
            if rec and t.upper() not in keep:
                logger.info(f"[{t}] Below deep-research cap ({cap}) - deferred.")
                continue
            tdir = _triage_subdir_for(t) or raw_dir
            matches = glob.glob(str(tdir / f"{t}_*.png"))
            if matches:
                chart_files.append(matches[0])

        chart_files = list(dict.fromkeys(chart_files))

    if not chart_files:
        logger.warning("No charts found for deep research.")
        return

    # Load the split prompt files
    original_gem_path = config.BASE_DIR / "gems" / "revanth-original-gem.md"
    response_path = config.BASE_DIR / "gems" / "response.md"
    bible_path = config.BASE_DIR / "gems" / "revanth-bible.md"

    if not original_gem_path.exists() or not response_path.exists() or not bible_path.exists():
        logger.error("Cannot find Deep Research Gem files.")
        return

    with open(original_gem_path, "r", encoding="utf-8") as f:
        gem_text = f.read()
    with open(response_path, "r", encoding="utf-8") as f:
        response_text = f.read()
    with open(bible_path, "r", encoding="utf-8") as f:
        bible_text = f.read()

    # Load the generalized few-shot template for Minimax
    example_path = config.BASE_DIR / "gems" / "few_shot_template.md"
    few_shot_example = ""
    if example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            few_shot_example = f.read()

    system_prompt = f"{gem_text}\n\n{response_text}\n\n--- STRICT EXAMPLE OF THE EXACT FORMAT, ASCII ART, AND DEPTH YOU MUST OUTPUT ---\n{few_shot_example}"

    logger.info(f"Starting Agentic Deep Research Phase for {len(chart_files)} tickers...")

    _drift_checker = ThesisDriftChecker()
    macro_news = _pull_macro_news(date_str)

    for chart_path in chart_files:
        ticker = Path(chart_path).name.replace("_chart.png", "")
        # Artifacts live in the deep-research folder after local-research
        # segregation; fall back to raw for ad-hoc / non-segregated tickers.
        tdir = _triage_subdir_for(ticker) or raw_dir
        # The Gemini thesis is written next to the ticker's artifacts (deep_dir for
        # batch runs, raw for ad-hoc --ticker runs not yet segregated). Re-running
        # OVERWRITES the existing thesis (regenerate, not append).
        out_dir = tdir
        out_path = out_dir / f"{ticker}_gemini_thesis.md"

        logger.info(f"[{ticker}] Initiating Deep Research (2-pass flow)...")

        dw_path = tdir / f"{ticker}_datawindow.json"
        dossier_path = tdir / f"{ticker}_news_research.md"

        triage_record = _load_triage_record(raw_dir, deep_dir, ticker) or {}

        # Snapshot the deterministic verdict BEFORE the flags fallback below. An empty
        # `flags` list is normal on a clean bar, so that fallback can replace a perfectly
        # good filter record — potentially with `llm_data`, which is model output. EV /
        # Win Prob must never come from that, so the math block only ever reads a record
        # that carries the filter's own `triage` verdict.
        verdict_record = triage_record if triage_record.get("triage") else {}

        # Default to an empty flag list; the thesis-file path below may override it.
        flags = (
            triage_record.get("flags")
            if isinstance(triage_record, dict)
            else None
        ) or []

        thesis_file = tdir / f"{ticker}_thesis.json"
        if not triage_record.get("flags") and thesis_file.exists():
            try:
                tdata = json.loads(thesis_file.read_text(encoding="utf-8"))
                triage_record = tdata.get("triage") or tdata.get("llm_data") or {}

                if not verdict_record and isinstance(tdata.get("triage"), dict):
                    verdict_record = tdata["triage"]
            except (json.JSONDecodeError, OSError):
                pass

            # Re-read flags from the (possibly replaced) triage_record.
            flags = (
                triage_record.get("flags")
                if isinstance(triage_record, dict)
                else None
            ) or []

        flags_block = _format_flags_block(flags)
        engine_math_block = _format_engine_math_block(verdict_record)

        # Load data window JSON (math state)
        data_window_str = "{}"
        dw_dict = {}
        if dw_path.exists():
            with open(dw_path, "r") as f:
                dw_dict = json.load(f)
                data_window_str = json.dumps(dw_dict, indent=2)
        else:
            logger.warning(f"[{ticker}] Data window JSON not found at {dw_path}")

        unmasked_recency_block = _format_unmasked_recency_block(dw_dict)

        # Load pre-compiled news research dossier (Steps 3-7)
        news_dossier = ""
        if dossier_path.exists():
            with open(dossier_path, "r", encoding="utf-8") as f:
                news_dossier = f.read()
            logger.info(f"[{ticker}] Loaded news research dossier ({len(news_dossier):,} chars)")
        else:
            # The heavy DDGS dossier (RTX_news_research.md) is intentionally no
            # longer produced; fall back to the cached Alpaca sentiment that the
            # local pre-filter stored in the ticker's _thesis.json so deep
            # research still has some news context.
            thesis_path = tdir / f"{ticker}_thesis.json"
            cached_sentiment = None
            if thesis_path.exists():
                try:
                    tdata = json.loads(thesis_path.read_text(encoding="utf-8"))
                    cached_sentiment = tdata.get("llm_data", {}).get("news_sentiment") or tdata.get(
                        "triage", {}
                    ).get("sentiment", {}).get("label")
                except Exception:
                    cached_sentiment = None
            if cached_sentiment:
                news_dossier = (
                    f"No pre-compiled news dossier available. "
                    f"Cached Alpaca news sentiment from local pre-filter: {cached_sentiment}."
                )
                logger.info(
                    f"[{ticker}] Using cached Alpaca sentiment '{cached_sentiment}' (no DDGS dossier)."
                )
            else:
                logger.warning(f"[{ticker}] News research dossier not found at {dossier_path}.")
                news_dossier = "No pre-compiled news research dossier available."

        image_paths = glob.glob(str(tdir / f"{ticker}_*.png"))

        # ── FRESH, DATED NEWS (live pull so the paid pass never sees stale macro) ──
        fresh_news = _pull_fresh_news(ticker, date_str)

        # ── MACRO SOCIAL SENTIMENT (overall market mood from Adanos, free tier) ──
        # Injected as context so the paid pass sees broad retail/news sentiment,
        # not just the single-ticker read. Costs 2 quota calls/run (cached per run).
        market_sentiment_block = format_market_sentiment_block() or (
            "--- MACRO SOCIAL SENTIMENT ---\n(no Adanos data available)"
        )

        av_block = alphavantage_client.format_av_block(ticker)
        social_block = adanos_client.format_social_block(ticker)
        earnings_fact_block = earnings_client.format_earnings_fact_block(ticker)

        grounded_question = (
            f"{ticker} stock latest news, analyst rating changes, and earnings outlook this week"
        )
        grounded_block = google_grounding_client.format_grounded_block(ticker, grounded_question)

        stale = _dossier_stale(dossier_path, date_str)
        if stale:
            logger.warning(
                f"[{ticker}] Cached news dossier is STALE (run date {date_str}) — "
                f"relying on freshly pulled live news."
            )

        # Let the live tools fall back to this ticker if the model omits the arg.
        from src.clients import options_client

        options_client.set_active_ticker(ticker)
        # Pre-fetch the live quote rather than relying on the model to call the tool.
        # The Data Window is the last CLOSED bar, so without this the thesis can be
        # built on a stale anchor whenever the model skips get_realtime_quote — and
        # whether it did is not auditable after the fact. The tool stays enabled for
        # refresh; this just guarantees a deterministic price is always in the payload.
        try:
            live_quote_block = options_client.get_realtime_quote(ticker) or ""
        except Exception as e:
            logger.warning(f"[{ticker}] Live quote pre-fetch failed: {e}")
            live_quote_block = ""
        if not live_quote_block:
            live_quote_block = (
                "(unavailable — say so explicitly and anchor on the Data Window bar close; "
                "do NOT invent a live price)"
            )

        options_block = ""
        user_prompt = f"""
        RESEARCH DATE: {date_str}   (SYSTEM/TODAY: {datetime.now().strftime("%Y-%m-%d")})
        VERIFY every macro, CPI, Fed, and earnings reference against this date. Do NOT assume
        prior-session news is current — the FRESH LIVE NEWS block below is the authoritative,
        dated source for today's economy/earnings context.

        I am requesting a Deep Research Validation for the ticker: {ticker}.

        --- 0. REVANTH BIBLE (Rules & Framework) ---
        {bible_text}

        --- 1. DATA WINDOW (Exact Math State from TradingView — the last CLOSED bar) ---
        {data_window_str}

        --- 1a. LIVE QUOTE (pre-fetched at run time — where the market is NOW) ---
        {live_quote_block}

        {unmasked_recency_block}

        ⚠️ CRITICAL BITMASK RULE FOR REVERSAL PATTERN MASK:
        When filling the Reversal Pattern Mask table row in your output report, you MUST copy the exact pattern names and polarities from Section 1b (UNMASKED PATTERN & RECENCY SIGNALS). Do NOT infer or change suffixes.
        - Bit 256 IS HIKKAKE_BULL (Bullish) — NOT Hikkake Bear
        - Bit 1024 IS OOPS_BULL (Bullish) — NOT Oops Bear
        - Bit 512 IS HIKKAKE_BEAR (Bearish)
        - Bit 2048 IS OOPS_BEAR (Bearish)
        For Mask 1473 (= 1024 + 256 + 128 + 64 + 1), the exact decoded sequence is: OOPS_BULL (1024) + HIKKAKE_BULL (256) + TRAP_BEAR (128, Bullish) + TRAP_BULL (64, Bearish) + KEY_REV_BULL (1, Bullish) = 4 Bullish vs 1 Bearish signals.


        --- 2. NEWS RESEARCH DOSSIER (Pre-compiled by Local Pipeline) ---
        {news_dossier}
        {"(NOTE: this cached dossier is STALE — written on a different date. Prefer the FRESH LIVE NEWS block below.)" if stale else ""}

        --- 2b. FRESH LIVE NEWS (pulled at run time — AUTHORITATIVE for today) ---
        {macro_news}

        {fresh_news}

        {market_sentiment_block}

        --- 2c. FUNDAMENTAL & PER-TICKER SOCIAL (fetched live for this ticker) ---
        {av_block}
        {social_block}

        --- 2d. ENGINE FLAGS (deterministic) ---
        {flags_block}

        --- 2d-i. ENGINE MATH (deterministic — already computed, do not recompute) ---
        {engine_math_block}

        --- 2e. GOOGLE-GROUNDED RESEARCH (Gemini + Google Search, cited) ---
        {grounded_block}

        --- 2f. EARNINGS DATE (deterministic where available) ---
        {earnings_fact_block}

        {options_block}
        You have access to LIVE TOOLS. BEFORE you finalize the thesis you MUST call them for the
        option chain and for anything time-sensitive you still need. What is already pre-fetched
        above and must NOT be re-derived: the live price (section 1a), the engine math (2d-i), the
        earnings date (2f), and macro/CPI/Fed/earnings context (2b). The Data Window state itself
        (scores, action codes, stage, zones, geometry) is NOT stale and is NOT superseded by any
        tool - it is the bar you are analysing. Only PRICE moves after the close.

        - get_realtime_quote("{ticker}")  -> refresh the live quote if section 1a is unavailable or you need bid/ask depth.
        - fetch_options_chain("{ticker}", direction="CALL"|"PUT", strike_low=..., strike_high=...,
                min_dte=..., max_dte=...)    -> live option strikes, bids/asks, mid, volume, and greeks
                (delta/gamma/theta/vega) from Alpaca. NOTE: IV and open interest are NOT returned.
                Derive direction/strike band from the chart + live quote; widen if needed.
        - search_web(...)                  -> latest earnings date, catalyst, analyst/macro context.

        Form your OWN independent verdict from the Data Window, chart, news, and the LIVE data you pull - do not 
        assume any prior read is correct. Then synthesize the FINAL thesis and
        EMIT a concrete trade plan with ALL of:
        - EXPECTED STOCK PRICE RANGE: support floor, resistance ceiling, and your projected
            14-120 day trading range, justified from the chart + live data.
        - OPTIONS PLAN: direction, specific strike(s), expiry/DTE window, and why that
            structure fits the range + catalyst.
        - ENTRY, STOP LOSS, and PROFIT TARGET (exact prices) derived from the live chain
            and the expected range.
        - CONVICTION and the risk/reward rationale.
        Emit your final Portfolio Manager Thesis exactly as instructed in the response format.
        """

        provider_model = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3")
        logger.info(
            f"[{ticker}] Pass 2 — Transmitting full payload + {len(image_paths)} images to {provider_model} (tools enabled)..."
        )
        try:
            response = query_local_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=False,
                use_openrouter=True,
                image_paths=image_paths,
                use_tools=True,  # let M3 pull live quotes / chains / news itself
                max_tokens=8192,
            )

            if response:
                out_dir.mkdir(parents=True, exist_ok=True)

                # OVERWRITE: re-running deep research regenerates the ticker's
                # thesis. The file is replaced wholesale — no append, no delete
                # of other research. For ad-hoc --ticker runs the Gemini thesis is
                # written next to the ticker's artifacts (data/raw or
                # _DEEP_RESEARCH), so it never collides with other tickers.
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(response)

                try:
                    _drift_checker.check_and_write(ticker, date_str, response, out_dir)
                except Exception as e:
                    logger.warning(f"[{ticker}] Thesis drift check failed: {e}")

                response_for_parse = response

                # Parse metrics via regex for Google Sheets tracker
                verdict_match = re.search(
                    r"\*\*Verdict:\*\*\s*(.+)", response_for_parse, re.IGNORECASE
                )
                conviction_match = re.search(
                    r"\*\*Conviction:\*\*\s*([\d\.]+)(?:/10)?", response_for_parse, re.IGNORECASE
                )
                thesis_match = re.search(
                    r"\*\*The Thesis in 2 Sentences:\*\*\s*(.+)", response_for_parse, re.IGNORECASE
                )

                entry_match = re.search(
                    r"\|\s*Entry\s*\|\s*\$([\d\.]+)", response_for_parse, re.IGNORECASE
                )
                stop_match = re.search(
                    r"\|\s*Stop\s*\|\s*\$([\d\.]+)", response_for_parse, re.IGNORECASE
                )
                target_match = re.search(
                    r"\|\s*Target\s*\|\s*\$([\d\.]+)", response_for_parse, re.IGNORECASE
                )

                payload_dict = {
                    "verdict": verdict_match.group(1).strip() if verdict_match else "N/A",
                    "conviction": conviction_match.group(1).strip() if conviction_match else "N/A",
                    "thesis": thesis_match.group(1).strip() if thesis_match else "",
                    "action_plan": {
                        "entry": entry_match.group(1).strip() if entry_match else "",
                        "stop": stop_match.group(1).strip() if stop_match else "",
                        "target": target_match.group(1).strip() if target_match else "",
                        "rationale": "See markdown for full rationale.",
                    },
                }

                # Update Google Sheets with the Deep Research Verdict
                tracker = SheetsTracker()
                tracker.update_deep_research(date_str, ticker, payload_dict)

                # Save identical copy to reports folder
                reports_dir = config.BASE_DIR / "reports" / date_str
                reports_dir.mkdir(parents=True, exist_ok=True)
                digest_path = reports_dir / f"{ticker}_summary.md"
                with open(digest_path, "w", encoding="utf-8") as f:
                    f.write(response)

                logger.info(
                    f"[{ticker}] Deep Research completed, Sheet updated, and Summary generated successfully!"
                )
            else:
                logger.error(f"[{ticker}] LLM returned empty response.")
        except Exception as e:
            logger.error(f"[{ticker}] Deep Research failed: {e}", exc_info=True)


if __name__ == "__main__":
    load_dotenv()
    import argparse
    import re
    from datetime import datetime

    parser = argparse.ArgumentParser(description="Run deep research phase")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD). If a non-date token is given, "
        "it is treated as --ticker using today's date.",
    )
    parser.add_argument("--ticker", type=str, help="Run only on a specific ticker")

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker
    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_deep_research(target_date or datetime.now().strftime("%Y-%m-%d"), target_ticker)
