import os
import sys
import glob
import json
import logging
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src import config
from src.clients import adanos_client, alphavantage_client, earnings_client, google_grounding_client, finnhub_client
from src.clients.adanos_client import format_market_sentiment_block
from src.clients.llm_client import query_local_llm
from src.logic.thesis_drift import ThesisDriftChecker
from src.tracking.sheets_tracker import SheetsTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import subprocess
from datetime import datetime


def _load_triage_record(raw_dir: Path, deep_dir: Path, ticker: str, tdir: Optional[Path] = None) -> dict:
    """Load the deterministic triage dict (chosen_side/in_zone/regime/dir_prob/rr)
    persisted in the thesis JSON or triage JSON. If not found on disk but datawindow.json exists,
    computes it directly via run_data_window_filter. Returns empty dict if unavailable."""
    search_dirs = [d for d in (tdir, deep_dir, raw_dir) if d is not None]
    for d in search_dirs:
        for fname in (f"{ticker}_thesis.json", f"{ticker}_triage.json"):
            cand = d / fname
            if cand.exists():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    rec = data.get("triage") if isinstance(data, dict) and "triage" in data else data
                    if isinstance(rec, dict) and rec.get("triage"):
                        rec.setdefault("ticker", ticker.upper())
                        return rec
                except Exception:
                    pass

    # Fallback: compute directly from datawindow.json if available
    for d in search_dirs:
        dw_cand = d / f"{ticker}_datawindow.json"
        if dw_cand.exists():
            try:
                from src.logic.data_window_filter import run_data_window_filter
                dw_data = json.loads(dw_cand.read_text(encoding="utf-8"))
                rec = run_data_window_filter(ticker, dw_data)
                if isinstance(rec, dict):
                    rec.setdefault("ticker", ticker.upper())
                    return rec
            except Exception:
                pass
    return {}


def _pull_macro_news(date_str: str) -> str:
    """Run-level macro context (CPI/Fed/NFP) — purely deterministic."""
    from src.clients.macro_client import get_upcoming_macro_events

    timeline = get_upcoming_macro_events(date_str)
    return timeline


def _pull_fresh_news(ticker: str, date_str: str) -> str:
    """Pull LIVE, dated news at run time so deep research never relies on a stale
    cached dossier. Covers ticker-specific catalysts."""
    from src.clients.search_client import search_web

    pull_date = datetime.now().strftime("%Y-%m-%d")
    queries = [
        f"{ticker} latest news earnings catalyst {date_str}",
        f"{ticker} analyst rating upgrade downgrade price target {date_str}",
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

    triage_v = rec.get("triage") or "WATCH"
    reason_v = rec.get("reason") or "no_setup"
    fade_long = rec.get("fade_long")
    if fade_long is None:
        fade_str = "ABSENT / UNCOMPUTED"
    elif fade_long == 1.0:
        fade_str = "ACTIVE / DO NOT CHASE (bit 2 is 0 -> fade active)"
    else:
        fade_str = "OFF (bit 2 is 1 -> fade gate not active)"

    in_zone = rec.get("in_zone")
    missed = rec.get("missed")
    long_bot = rec.get("long_bot")
    long_top = rec.get("long_top")
    if long_bot is None or long_top is None:
        zone_pos = "ZONELESS (no surviving entry zone; bounds are blank)"
    elif in_zone:
        zone_pos = "IN THE ZONE"
    elif missed:
        zone_pos = "ABOVE THE LONG ZONE (chased)"
    else:
        zone_pos = "BELOW/OUTSIDE ZONE (pullback / forming)"

    # Posture lock definition
    if triage_v == "PASS":
        permitted_primary = "DIRECTIONAL LONG OK (meets PASS criteria)"
    elif triage_v == "WATCH":
        permitted_primary = "STALK / CONDITIONAL TRIGGER / NON-DIRECTIONAL STRUCTURE ONLY (no 'enter now' primary)"
    else:
        permitted_primary = "SKIP (structure note only if structure is populated)"

    lines = [
        f"- Triage verdict: {triage_v} ({reason_v})",
        f"- Permitted Primary Posture: {permitted_primary}",
        f"- Chosen side / mode: {rec.get('chosen_side') or 'n/a'} / {rec.get('mode') or 'n/a'}",
        f"- Fade Gate Status: {fade_str}",
        f"- Price vs Zone: {zone_pos}  (in_zone={in_zone}, missed={missed})",
        f"- Long R:R at Market: {f('rr_at_market')}  (2.0+ is the verified PASS lane threshold)",
        f"- Expected Value: {f('ev_r')} R   |  Win Prob: {f('win_prob')}  |  R:R used: {f('rr')}",
        f"- Structure read: {rec.get('structure') or 'none'}  |  Strikes: {rec.get('structure_strikes') or 'none'}",
        f"- IV Rank: {f('iv_rank', '{:.1f}%')}  |  Expected Move 21b: {f('exp_move_pct', '{:.2f}%')}",
        "(deterministic — these are computed by Python. Read them verbatim. Do NOT recompute.)",
    ]

    return "\n".join(lines)

def _format_triggers_block(triage_record: dict) -> str:
    """Render the Buy-Trigger Gap Engine output as a human-readable block.

    The `triggers` dict lives inside the triage record (added by data_window_filter).
    For each actionable state (Code 20 Reversal, Stage-2 PRIME) it shows which gates
    are passed, which are open, and at what distance — so the deep-research agent can
    predict the specific price-event or catalyst that would close each gap.
    """
    triggers = triage_record.get("triggers") if isinstance(triage_record, dict) else None
    if not triggers:
        return "(Buy-Trigger Gap Engine not available — TRIGGERS_ENABLED=0 or bad_data)"

    lines = [
        "Each actionable state below lists its required gates, which ones are already",
        "satisfied (PASSED) and which are still open (OPEN — with the gap distance).",
        "For each OPEN gate, predict the specific price-event (the real level to hit)",
        "or catalyst-event (the news/fundamental that lifts the gate) and how plausible",
        "it is within 21 days. NEVER fabricate hypothetical Buy Score / Stage / Dir Prob values.",
        "",
    ]

    hard = triggers.get("hard_exclusions") or []
    if hard:
        lines.append("⛔ HARD EXCLUSIONS (active — no entry possible):")
        for h in hard:
            lines.append(f"  - {h}")
        lines.append("")

    for state_key in ("code20_reversal", "stage2_prime"):
        state = triggers.get(state_key)
        if not state:
            continue
        status = "✅ ALL GATES PASSED" if state.get("all_passed") else f"❌ {state.get('open_count')}/{state.get('total_count')} gates OPEN"
        lines.append(f"▸ {state.get('state', state_key).upper()} — {status}")

        passed = state.get("passed_gates") or []
        if passed:
            lines.append("  PASSED: " + ", ".join(passed))

        open_gates = state.get("open_gates") or []
        for g in open_gates:
            ref = g.get("ref_level")
            cur = g.get("current_value")
            gap = g.get("gap")
            ref_str = f"{ref:.2f}" if isinstance(ref, (int, float)) else str(ref)
            cur_str = f"{cur:.2f}" if isinstance(cur, (int, float)) else str(cur)
            gap_str = f"{gap:.2f}" if isinstance(gap, (int, float)) else "?"
            lines.append(
                f"  OPEN: {g['name']} — need {g['field']} {g['comparator']} {ref_str} "
                f"(currently {cur_str}, gap {gap_str}) [{g['change_type']}]"
            )
        lines.append("")

    nearest = triggers.get("nearest_actionable_state", "?")
    open_ct = triggers.get("open_gates_count", "?")
    ceiling = triggers.get("conviction_ceiling", "?")
    lines.append(f"Nearest actionable state: {nearest} ({open_ct} gates open)")
    lines.append(f"Conviction ceiling (indicator-only, no external pillar): {ceiling}")
    lines.append("")
    lines.append(
        "⚠️ RULE: Never fabricate hypothetical Buy Score / Stage / Dir Prob values. "
        "The deterministic layer names REAL thresholds and REAL levels only."
    )

    return "\n".join(lines)

def _format_scenario_block(scenarios: list) -> str:
    """Render the Deterministic Price Scenario output as a human-readable block."""
    if not scenarios:
        return "(Deterministic Price Scenario not available)"

    lines = [
        "The deterministic layer has projected one-step technical scenarios for key candidate prices.",
        "Use these EXACT numbers in your trajectory narration. Do not fabricate returns.",
        "",
    ]
    
    for s in scenarios:
        lines.append(f"Candidate Price: {s.get('candidate_price')}")
        lines.append(f"  - Projected MA50: {s.get('ma50_proj')}")
        lines.append(f"  - Projected MA200: {s.get('ma200_proj')}")
        lines.append(f"  - Extension %: {s.get('ext_pct')}%")
        lines.append(f"  - Lane Classification: {s.get('lane')} ({s.get('lane_edge')})")
        if s.get("zone_top"):
            lines.append(f"  - Target/Zone Top: {s.get('zone_top')}")
        if s.get("zone_bot"):
            lines.append(f"  - Stop/Zone Bot: {s.get('zone_bot')}")
        lines.append("")

    return "\n".join(lines)

def _format_state_response_block(f_parsed: dict, scenarios: list) -> str:
    """Render the Historical State Response output as a human-readable block."""
    if not isinstance(f_parsed, dict) or not f_parsed:
        return "(Data Window missing, cannot compute state response)"
        
    try:
        from src.logic.response_model import state_response
    except ImportError:
        return "(State Response model unavailable)"
    
    current_resp = state_response(f_parsed)
    if not current_resp:
        return "(State Response model disabled or missing)"
        
    lines = [
        "The response model projects historical 21-day outcomes for given structural states.",
        "Use this as the baseline expectation for your explicitly narrated trajectories.",
        "",
        f"CURRENT BAR: {current_resp.get('bucket')} (Edge vs Baseline: {current_resp.get('edge_vs_baseline')} | {current_resp.get('reliability')} | Median {current_resp.get('ex21_med')} | p10 {current_resp.get('p10')} / p90 {current_resp.get('p90')} | {'SIG' if current_resp.get('sig') else 'flat'})",
        ""
    ]
    
    if scenarios:
        lines.append("PROJECTED CANDIDATE STATES:")
        for s in scenarios:
            price = s.get('candidate_price')
            # build a simulated f for state_response
            f_sim = dict(f_parsed)
            f_sim["price"] = price
            f_sim["ma50"] = s.get("ma50_proj")
            f_sim["ma200"] = s.get("ma200_proj")
            f_sim["ext_pct"] = s.get("ext_pct")
            resp = state_response(f_sim)
            if resp:
                lines.append(f"  - If price -> {price}: {resp.get('bucket')} (Edge vs Baseline: {resp.get('edge_vs_baseline')})")
            else:
                lines.append(f"  - If price -> {price}: (unavailable)")
                
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



def run_deep_research(date_str, target_ticker=None, force_local=False):
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
                flagged.append((t, _load_triage_record(raw_dir, deep_dir, t, tdir=sd)))

        # Deterministic RANK + hard cap: only the top-N setups reach paid research.
        from src.logic.data_window_filter import rank_pass_tickers

        cap = int(os.getenv("DEEP_RESEARCH_CAP", "0"))
        ranked = rank_pass_tickers([r for _, r in flagged if r])
        # cap <= 0 means research everything eligible; the rank still sets the ORDER,
        # so a run that dies partway has already done the strongest names.
        kept_recs = ranked if cap <= 0 else ranked[:cap]
        keep = {str(r.get("ticker", "")).upper() for r in kept_recs}
        logger.info(
            f"Deep-research cap {cap or 'uncapped'}: {len(flagged)} flagged -> "
            f"{len(keep)} kept ({sorted(keep)})."
        )
        for t, rec in flagged:
            if rec and cap > 0 and t.upper() not in keep:
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

    few_shot_example = ""
    example_path = config.BASE_DIR / "gems" / "few_shot_template.md"
    if example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            few_shot_example = f.read()

    # Move invariant prompt blocks into system_prompt for prefix caching across all tickers in the run
    system_prompt = f"{gem_text}\n\n--- REVANTH BIBLE (Rules & Framework) ---\n{bible_text}\n\n--- STRICT EXAMPLE OF THE EXACT FORMAT, ASCII ART, AND DEPTH YOU MUST OUTPUT ---\n{few_shot_example}\n\n{response_text}"

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
        if not dw_path.exists() and (raw_dir / f"{ticker}_datawindow.json").exists():
            dw_path = raw_dir / f"{ticker}_datawindow.json"

        dossier_path = tdir / f"{ticker}_news_research.md"
        if not dossier_path.exists() and (raw_dir / f"{ticker}_news_research.md").exists():
            dossier_path = raw_dir / f"{ticker}_news_research.md"

        triage_record = _load_triage_record(raw_dir, deep_dir, ticker, tdir=tdir) or {}

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

        # Multi-Agent Debate will be run live.
        # We ignore the stale mod_agree/mod_disagree from local triage.

        flags_block = _format_flags_block(flags)
        engine_math_block = _format_engine_math_block(verdict_record)
        triggers_block = _format_triggers_block(verdict_record)

        # Load data window JSON (math state)
        data_window_str = "{}"
        dw_dict = {}
        if dw_path.exists():
            with open(dw_path, "r") as f:
                dw_dict = json.load(f)
                data_window_str = json.dumps(dw_dict, indent=2)
        else:
            logger.warning(f"[{ticker}] Data window JSON not found at {dw_path}")

        from src.logic.data_window_filter import parse_data_window

        f_parsed = parse_data_window(dw_dict) if dw_dict else {}

        triggers_src = verdict_record
        if not (isinstance(verdict_record, dict) and verdict_record.get("triggers")) and f_parsed:
            from src.logic.trigger_gaps import compute_triggers

            triggers_src = {"triggers": compute_triggers(f_parsed)}
        triggers_block = _format_triggers_block(triggers_src)

        # Build scenarios (parsed keys: price, ma50, ma200, long_target, ...)
        scenarios = []
        try:
            from src.logic.scenario_model import build_scenario
            prices = []
            
            def _try_add_price(val):
                if val is None:
                    return
                try:
                    prices.append(float(val))
                except (ValueError, TypeError):
                    pass

            for key in ("long_target", "ma50", "ma200"):
                _try_add_price(f_parsed.get(key))
            for raw_key in ("52 Week High", "52 week high"):
                _try_add_price(dw_dict.get(raw_key) if dw_dict else None)
                
            ma200 = f_parsed.get("ma200")
            if ma200 is not None:
                _try_add_price(float(ma200) * 0.95)
                    
            candidate_prices = sorted(list(set([round(p, 2) for p in prices])))
            scenarios = build_scenario(f_parsed, None, candidate_prices)
        except Exception as e:
            logger.warning(f"[{ticker}] scenario build failed: {e}")

        scenario_block = _format_scenario_block(scenarios)
        state_response_block = _format_state_response_block(f_parsed, scenarios)

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

        if force_local:
            zoom_p = tdir / f"{ticker}_chart_zoom.png"
            image_paths = [str(zoom_p)] if zoom_p.exists() else [str(tdir / f"{ticker}_chart.png")]
            image_paths = [p for p in image_paths if os.path.exists(p)]
        else:
            image_paths = [
                str(p) for p in [tdir / f"{ticker}_chart.png", tdir / f"{ticker}_chart_zoom.png"]
                if p.exists()
            ]

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
        institutional_block = finnhub_client.format_finnhub_institutional_block(ticker)

        grounded_question = (
            f"{ticker} stock latest news, analyst rating changes, and earnings outlook this week"
        )
        grounded_block = google_grounding_client.format_grounded_block(ticker, grounded_question)
        
        macro_grounded_question = f"US Macroeconomic news today {date_str}, including any CPI, NFP, or FOMC data released"
        macro_grounded_block = google_grounding_client.format_grounded_block("MACRO", macro_grounded_question)


        stale = _dossier_stale(dossier_path, date_str)
        if stale:
            logger.warning(
                f"[{ticker}] Cached news dossier is STALE (run date {date_str}) — "
                f"relying on freshly pulled live news."
            )

        # Let the live tools fall back to this ticker if the model omits the arg.
        from src.clients import options_client

        options_client.set_active_ticker(ticker)
        # Pre-fetch the live quote with disk snapshot caching
        quote_path = tdir / f"{ticker}_quote.json"
        if not quote_path.exists() and (raw_dir / f"{ticker}_quote.json").exists():
            quote_path = raw_dir / f"{ticker}_quote.json"

        if quote_path.exists():
            try:
                live_quote_block = json.loads(quote_path.read_text(encoding="utf-8")).get("quote", "")
            except Exception:
                live_quote_block = ""
        else:
            live_quote_block = ""

        if not live_quote_block:
            try:
                live_quote_block = options_client.get_realtime_quote(ticker) or ""
                if live_quote_block:
                    quote_path = tdir / f"{ticker}_quote.json"
                    quote_path.write_text(json.dumps({"ticker": ticker, "quote": live_quote_block, "date": date_str}, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[{ticker}] Live quote pre-fetch failed: {e}")
                live_quote_block = ""

        if not live_quote_block:
            live_quote_block = (
                "(unavailable — say so explicitly and anchor on the Data Window bar close; "
                "do NOT invent a live price)"
            )

        # Pre-fetch GEX and options positioning with disk snapshot caching
        gex_path = tdir / f"{ticker}_gex.json"
        if not gex_path.exists() and (raw_dir / f"{ticker}_gex.json").exists():
            gex_path = raw_dir / f"{ticker}_gex.json"

        if gex_path.exists():
            try:
                gex_block = json.loads(gex_path.read_text(encoding="utf-8")).get("gex", "")
            except Exception:
                gex_block = ""
        else:
            gex_block = ""

        if not gex_block:
            try:
                gex_block = options_client.format_gex_block(ticker) or ""
                if gex_block:
                    gex_path = tdir / f"{ticker}_gex.json"
                    gex_path.write_text(json.dumps({"ticker": ticker, "gex": gex_block, "date": date_str}, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[{ticker}] GEX block failed: {e}")
                gex_block = ""

        # Pre-load TradingView scraped strategies with deterministic strike validation
        tv_strat_path = tdir / f"{ticker}_tv_strategies.json"
        if not tv_strat_path.exists() and (raw_dir / f"{ticker}_tv_strategies.json").exists():
            tv_strat_path = raw_dir / f"{ticker}_tv_strategies.json"
        tv_strat_block = ""
        if tv_strat_path.exists():
            try:
                strat_data = json.loads(tv_strat_path.read_text(encoding="utf-8"))
                if strat_data:
                    from src.logic.strike_validator import validate_strike_geometry
                    dw_spot = float(dw_data.get("close") or 0.0) if isinstance(dw_data, dict) else 0.0
                    dw_exp_move = float(dw_data.get("Exp Move Pct 21b") or dw_data.get("exp_move_pct") or 0.0) if isinstance(dw_data, dict) else None

                    valid_strats = []
                    for s in strat_data:
                        formula = s.get("formula", "")
                        strikes = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[CPcp]\b", formula)]
                        long_st = strikes[0] if len(strikes) > 0 else None
                        short_st = strikes[1] if len(strikes) > 1 else None

                        is_valid, defects = validate_strike_geometry(
                            strategy_type=s.get("strategy_type", ""),
                            spot_price=dw_spot,
                            exp_move_pct_21b=dw_exp_move,
                            long_strike=long_st,
                            short_strike=short_st,
                            max_profit=float(s.get("max_profit", 0) or 0) if s.get("max_profit") is not None else None,
                            max_loss=float(s.get("max_loss", 0) or 0) if s.get("max_loss") is not None else None,
                        )
                        if is_valid:
                            valid_strats.append(s)
                        else:
                            logger.info(f"[{ticker}] Strategy Filter rejected '{formula}': {', '.join(defects)}")

                    if valid_strats:
                        lines = [
                            f"--- TRADINGVIEW STRATEGY FINDER (VALIDATED SPREADS FOR {ticker}) ---",
                            "| Expiry | Days | Strategy | Formula/Strikes | Max Profit | Max Loss | R:R | Breakeven |",
                            "|---|---|---|---|---|---|---|---|",
                        ]
                        for s in valid_strats[:6]:  # top 6 valid spreads
                            lines.append(
                                f"| {s.get('expiration')} | {s.get('days')} | {s.get('strategy_type')} | "
                                f"{s.get('formula')} | {s.get('max_profit')} | {s.get('max_loss')} | "
                                f"{s.get('reward_risk')} | {s.get('breakeven')} |"
                            )
                        tv_strat_block = "\n".join(lines)
            except Exception as e:
                logger.warning(f"[{ticker}] Error loading tv_strategies: {e}")

        options_block = f"{gex_block}\n\n{tv_strat_block}".strip()

        # ========================================================
        # MULTI-AGENT BULL VS BEAR DEBATE (Local LLM)
        # ========================================================
        debate_payload = f"""
        RESEARCH DATE: {date_str}

        --- 1. DATA WINDOW (Exact Math State from TradingView) ---
        {data_window_str}

        --- 1a. LIVE QUOTE ---
        {live_quote_block}

        {unmasked_recency_block}

        --- 2. NEWS RESEARCH DOSSIER ---
        {news_dossier}

        --- 2a. FRESH NEWS (LIVE) ---
        {fresh_news}

        --- 2b. MACRO NEWS (LIVE) ---
        {macro_news}

        {market_sentiment_block}

        --- 2c. FUNDAMENTAL & SOCIAL ---
        {av_block}
        {social_block}
        {grounded_block}
        {macro_grounded_block}

        {gex_block}

        --- 2d. ENGINE FLAGS ---
        {flags_block}
        {engine_math_block}

        --- 2e. EARNINGS DATE ---
        {earnings_fact_block}
        """

        bull_sys = (
            "You are a rigorous, evidence-based Bullish Technical & Fundamental Analyst. "
            "Your job is to identify valid positive drivers, catalyst timelines, and support levels for this ticker. "
            "CRITICAL RULES: Every claim must cite an exact Data Window field name or verified news item. "
            "Never fabricate moving average levels or invent technical indicators. Never contradict the pre-decoded Section 2d-1 engine math. "
            "Output your bull case in a concise, punchy markdown format."
        )
        bear_sys = (
            "You are a skeptical, disciplined Bearish Technical & Fundamental Analyst. "
            "Your job is to identify risks, overhead supply resistance, catalyst timing risks, and valuation/extension headwinds. "
            "CRITICAL RULES: Every claim must cite an exact Data Window field name or verified news item. "
            "Never fabricate moving average levels or invent technical indicators. Never contradict the pre-decoded Section 2d-1 engine math. "
            "Output your bear case in a concise, punchy markdown format."
        )

        import concurrent.futures

        def _run_debate_agent(sys_prompt, u_prompt, tokens=1024):
            return query_local_llm(
                system_prompt=sys_prompt,
                user_prompt=u_prompt,
                use_openrouter=False,  # Unconditionally local to avoid burning remote budget on debate
                use_tools=False,
                disable_thinking=True,
                max_tokens=tokens
            )

        debate_cache_file = tdir / f"{ticker}_debate_v2.json"
        if debate_cache_file.exists():
            logger.info(f"[{ticker}] Found cached Multi-Agent Debate transcript at {debate_cache_file}. Resuming directly to Pass 2...")
            try:
                d_cached = json.loads(debate_cache_file.read_text(encoding="utf-8"))
                bull_case = d_cached.get("bull_case", "")
                bear_case = d_cached.get("bear_case", "")
                bull_rebuttal = d_cached.get("bull_rebuttal", "")
                bear_rebuttal = d_cached.get("bear_rebuttal", "")
            except Exception as e:
                logger.warning(f"Failed to read cached debate ({e}), recomputing...")
                debate_cache_file = None

        if not debate_cache_file or not debate_cache_file.exists():
            debate_workers = 1 if force_local else 2
            logger.info(f"[{ticker}] Running Bull and Bear Agents (workers={debate_workers})...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=debate_workers) as executor:
                f_bull = executor.submit(_run_debate_agent, bull_sys, f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}", 4096)
                f_bear = executor.submit(_run_debate_agent, bear_sys, f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}", 4096)
                bull_case = f_bull.result()
                bear_case = f_bear.result()

            logger.info(f"[{ticker}] Running Rebuttal Agents (workers={debate_workers})...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=debate_workers) as executor:
                f_bull_reb = executor.submit(
                    _run_debate_agent,
                    bull_sys + " You are now in the rebuttal phase. Read the Bear Case below and systematically destroy their arguments.",
                    f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}\n\n--- THE BEAR CASE ---\n{bear_case}",
                    4096
                )
                f_bear_reb = executor.submit(
                    _run_debate_agent,
                    bear_sys + " You are now in the rebuttal phase. Read the Bull Case below and systematically destroy their arguments.",
                    f"TICKER: {ticker}\n\nDATA PAYLOAD:\n{debate_payload}\n\n--- THE BULL CASE ---\n{bull_case}",
                    4096
                )
                bull_rebuttal = f_bull_reb.result()
                bear_rebuttal = f_bear_reb.result()

            try:
                (tdir / f"{ticker}_debate_v2.json").write_text(json.dumps({
                    "bull_case": bull_case,
                    "bear_case": bear_case,
                    "bull_rebuttal": bull_rebuttal,
                    "bear_rebuttal": bear_rebuttal
                }, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to cache debate: {e}")

        debate_block = (
            "--- 2f. LOCAL RESEARCHER DEBATE (MULTI-AGENT) ---\n"
            "Local analysts conducted a debate on this ticker citing Data Window ground truth:\n\n"
            "🟢 BULL CASE:\n"
            f"{bull_case}\n\n"
            "🔴 BEAR CASE:\n"
            f"{bear_case}\n\n"
            "🟢 BULL REBUTTAL:\n"
            f"{bull_rebuttal}\n\n"
            "🔴 BEAR REBUTTAL:\n"
            f"{bear_rebuttal}\n\n"
            "You are the Portfolio Manager (Judge). You MUST settle these disagreements in your final thesis and formulate the trade plan."
        )

        use_remote = not force_local
        user_prompt = f"""
        RESEARCH DATE: {date_str}   (SYSTEM/TODAY: {datetime.now().strftime("%Y-%m-%d")})
        VERIFY every macro, CPI, Fed, and earnings reference against this date. Do NOT assume
        prior-session news is current.

        I am requesting a Deep Research Validation for the ticker: {ticker}.

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

        --- 2. NEWS RESEARCH DOSSIER (Pre-compiled by Local Pipeline) ---
        {news_dossier}
        {"(NOTE: this cached dossier is STALE — written on a different date. Prefer live tools.)" if stale else ""}

        --- 2a. FRESH NEWS (LIVE) ---
        {fresh_news}

        --- 2b. MACRO NEWS (LIVE) ---
        {macro_news}

        {market_sentiment_block}

        --- 2c. FUNDAMENTAL & PER-TICKER SOCIAL (fetched live for this ticker) ---
        {av_block}
        {social_block}
        {institutional_block}
        {grounded_block}

        --- 2c-ii. MACRO GROUNDING (Google Search) ---
        {macro_grounded_block}

        --- 2d. ENGINE FLAGS (deterministic) ---
        {flags_block}

        --- 2d-i. ENGINE MATH (deterministic — already computed, do not recompute) ---
        {engine_math_block}

        --- 2d-ii. BUY-TRIGGER GAP ENGINE (deterministic gate distances) ---
        {triggers_block}

        --- 2d-iii. PRICE SCENARIO TRAJECTORY ---
        {scenario_block}

        --- 2d-iv. STATE RESPONSE (historical, honest) ---
        {state_response_block}

        --- 2e. EARNINGS DATE (deterministic where available) ---
        {earnings_fact_block}
        {institutional_block}

        {debate_block}

        ## 2B. LATEST CHART STATE (from Pass 1)

        {options_block}
        
        You have access to LIVE TOOLS for fundamental discovery and derivatives strategy execution. BEFORE you finalize the thesis you MUST call them:
        - `fetch_finnhub_news` and `fetch_alpaca_news` for the latest ticker-specific news.
        - `search_web` for broader macro or catalyst context.
        - `fetch_options_chain` for real-time Greeks, multi-horizon strikes (both short-dated and LEAPS with min_dte=120, max_dte=365+), and exact contract quotes.
        - `scrape_tradingview_options_finder` to dynamically search TradingView's proprietary Strategy Finder for pre-computed spreads matching your desired prediction period ('Next month', 'Next 3 months', 'Next 6 months') and expected move direction.

        Form your OWN independent verdict from the Data Window, chart, news, and the LIVE data you pull - do not 
        assume any prior read is correct. Then synthesize the FINAL thesis and
        EMIT a concrete trade plan with ALL of:
        - EXPECTED STOCK PRICE RANGE: support floor, resistance ceiling, and your projected
            14-120 day trading range, justified from the chart + live data.
        - OPTIONS PLAN (DUAL HORIZON): Evaluate both Tactical Swing (21-45 DTE) AND Multi-Quarter / LEAPS (90-365+ DTE, e.g. Deep ITM Calls with Delta 0.70-0.85) when long-term fundamental catalysts justify multi-quarter compounding without rapid theta decay.
        - ENTRY, STOP LOSS, and PROFIT TARGET (exact prices) derived from the live chain
            and the expected range.
        - CONVICTION and the risk/reward rationale.
        
        CRITICAL: Emit your final Portfolio Manager Thesis EXACTLY as instructed in the system prompt format. You MUST draw the ASCII art explicitly.
        """

        use_remote = not force_local
        if use_remote:
            meta_key = os.getenv("META_AI_API_KEY")
            if meta_key:
                provider_model = os.getenv("META_LLM", "muse-spark-1.2-contributor")
            else:
                provider_model = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3")
        else:
            provider_model = "local-gpu (llama-cpp-server)"
            
        logger.info(
            f"[{ticker}] Pass 2 — Transmitting full payload + {len(image_paths)} images to {provider_model}..."
        )
        try:
            response = None
            if use_remote:
                try:
                    response = query_local_llm(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        json_mode=False,
                        use_openrouter=True,
                        image_paths=image_paths,
                        use_tools=True,  # let remote LLM pull live quotes / chains / news
                        max_tokens=8192,
                        summarize_tool_context=f"The simulated date is {date_str}. CRITICAL: Filter out any news from 2024 or other years that contradicts this date. Treat {date_str} as the present day."
                    )
                except Exception as e_remote:
                    logger.warning(f"[{ticker}] Remote API inference failed ({e_remote}) — falling back to Local GPU LLM!")
                    response = None

            if not response:
                logger.info(f"[{ticker}] Executing Pass 2 with Local GPU LLM Server (Qwen3.8-27B Vision & Reasoning)...")
                response = query_local_llm(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    json_mode=False,
                    use_openrouter=False,
                    image_paths=image_paths,  # Local multimodal vision enabled with mmproj
                    use_tools=True,
                    disable_thinking=True,
                    max_tokens=8192,
                    summarize_tool_context=f"The simulated date is {date_str}. CRITICAL: Filter out any news from 2024 or other years that contradicts this date. Treat {date_str} as the present day."
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
                    r"\*\*Verdict:\*\*\s*(.*?)(?=\s*·|\s*\*\*Conviction|$)", response_for_parse, re.IGNORECASE
                )
                conviction_match = re.search(
                    r"\*\*Conviction[^\*]*:\*\*\s*([\d\.]+)(?:/10)?", response_for_parse, re.IGNORECASE
                )
                thesis_match = re.search(
                    r"\*\*The Thesis in 2 Sentences:\*\*\s*(.+)", response_for_parse, re.IGNORECASE
                )

                entry_match = re.search(
                    r"(?:\|\s*[*]*Entry[*]*\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)|-\s*[*]*Entry[*]*:\s*[$]?\s*(\d+(?:\.\d+)?))",
                    response_for_parse, re.IGNORECASE
                )
                stop_match = re.search(
                    r"(?:\|\s*[*]*(?:Tactical\s+)?Stop(?:\s*Loss)?[*]*\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)|-\s*[*]*(?:Tactical\s+)?Stop(?:\s*Loss)?[*]*:\s*[$]?\s*(\d+(?:\.\d+)?))",
                    response_for_parse, re.IGNORECASE
                )
                target_match = re.search(
                    r"(?:\|\s*[*]*Target(?:\s*1(?:\s*\([^)]*\))?)?[*]*\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)|-\s*[*]*Target(?:\s*1(?:\s*\([^)]*\))?)?[*]*:\s*[$]?\s*(\d+(?:\.\d+)?))",
                    response_for_parse, re.IGNORECASE
                )

                if not verdict_match:
                    logger.error(f"[{ticker}] FORMAT DRIFT: Failed to parse '**Verdict:**' from report!")
                if not conviction_match:
                    logger.error(f"[{ticker}] FORMAT DRIFT: Failed to parse '**Conviction:**' from report!")
                if not entry_match:
                    logger.warning(f"[{ticker}] FORMAT DRIFT: Failed to parse Entry price from action plan.")

                entry_val = (entry_match.group(1) or entry_match.group(2)) if entry_match else ""
                stop_val = (stop_match.group(1) or stop_match.group(2)) if stop_match else ""
                target_val = (target_match.group(1) or target_match.group(2)) if target_match else ""

                payload_dict = {
                    "verdict": verdict_match.group(1).strip() if verdict_match else "N/A",
                    "conviction": conviction_match.group(1).strip() if conviction_match else "N/A",
                    "thesis": thesis_match.group(1).strip() if thesis_match else "",
                    "action_plan": {
                        "entry": entry_val,
                        "stop": stop_val,
                        "target": target_val,
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
    parser.add_argument("--local", action="store_true", help="Force 100% local LLM inference (no OpenRouter/remote API calls)")

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker
    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_deep_research(target_date or datetime.now().strftime("%Y-%m-%d"), target_ticker, force_local=args.local)
