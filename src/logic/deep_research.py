from __future__ import annotations

import os
import sys
import glob
import json
import logging
import re
from pathlib import Path
from typing import Optional
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


def run_ponytail_pm_review(
    ticker: str,
    date_str: str,
    draft_thesis: str,
    dw_dict: dict,
) -> str:
    """
    Pass 2B: Senior Quantitative PM Ponytail Review & Due Diligence.
    Audits the generated thesis and trades against Ponytail Finance rules:
    - R:R & Chase Audit (Flag R:R < 1.5:1).
    - Regime-Matched Structure & Downside Risk Audit (4-leg Iron Condor vs 2-leg spread vs Shares).
    - Single Point of Failure (The ONE Thing).
    - Actionable adjustments for the trader.
    """
    ponytail_file = config.BASE_DIR / "gems" / "ponytail_finance.md"
    ponytail_rules = ponytail_file.read_text(encoding="utf-8") if ponytail_file.exists() else ""

    pm_sys_prompt = (
        f"You are a battle-tested Senior Quantitative Portfolio Manager executing the Ponytail Finance Review.\n\n"
        f"{ponytail_rules}\n\n"
        f"Your task is to ruthlessly critique the draft thesis for {ticker} and provide due-diligence feedback.\n"
        f"Output in concise markdown format with these exact 4 sections:\n"
        f"### 1. R:R & Entry Audit\n"
        f"- Is at-market R:R < 1.5:1? Is price chased above the long zone? (If so, mandate limit/stalk/skip).\n"
        f"### 2. Structure & Downside Risk Audit\n"
        f"- Does the structure properly cap downside risk? (Audit if 4-leg Iron Condor / Box is superior for Darvas box coiling vs 2-leg spread for trend).\n"
        f"### 3. The ONE Thing Invalidation\n"
        f"- State the single binary condition that kills the trade.\n"
        f"### 4. Senior PM Final Recommendation & Adjustments\n"
        f"- Final verdict (ENTER / STALK / SKIP) with dense, bulleted strike/level adjustments."
    )

    close_price = dw_dict.get("close") or dw_dict.get("Time")
    poc = dw_dict.get("VP POC") or dw_dict.get("Volume Profile POC")
    darvas_top = dw_dict.get("Darvas Box Top")
    darvas_bot = dw_dict.get("Darvas Box Bottom")

    pm_user_prompt = (
        f"TICKER: {ticker} | DATE: {date_str}\n"
        f"SPOT CLOSE: {close_price}\n"
        f"KEY DATA: VP POC={poc}, Darvas Top={darvas_top}, Darvas Bottom={darvas_bot}, "
        f"Buy Score={dw_dict.get('Buy Score')}, Sell Score={dw_dict.get('Sell Score')}, "
        f"IV Rank={dw_dict.get('IV Rank Pct')}\n\n"
        f"--- DRAFT THESIS TO AUDIT ---\n{draft_thesis}"
    )

    try:
        logger.info(f"[{ticker}] Running Pass 2B — Senior PM Ponytail Due-Diligence Audit...")
        review_output = query_local_llm(
            system_prompt=pm_sys_prompt,
            user_prompt=pm_user_prompt,
            json_mode=False,
            use_openrouter=False,
            use_tools=False,
            disable_thinking=True,
            max_tokens=2048,
        )
        return review_output.strip()
    except Exception as e:
        logger.warning(f"[{ticker}] Ponytail PM Review failed: {e}")
        return ""


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
        """Return the subfolder holding this ticker's artifacts, or None.
        Checks ticker directory inside _DEEP_RESEARCH, force, or raw."""
        safe = tkr.replace(":", "_")
        for d in (deep_dir / safe, deep_dir, triage_dir / "force" / safe, triage_dir / "force"):
            if (d / f"{safe}_chart.png").exists() or (d / f"{safe}_thesis.json").exists():
                return d
        if (raw_dir / safe).exists() and any((raw_dir / safe).iterdir()):
            return raw_dir / safe
        return None

    chart_files = []

    if target_ticker:
        target_tickers = [t.strip() for t in target_ticker.split(",") if t.strip()]
        for single_ticker in target_tickers:
            safe_target = single_ticker.replace(":", "_")
            tdir = _triage_subdir_for(single_ticker)
            target_file = (tdir or (raw_dir / safe_target) or raw_dir) / f"{safe_target}_chart.png"
            if not target_file.exists():
                target_file = raw_dir / f"{safe_target}_chart.png"
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
                    logger.info(f"[{single_ticker}] Found existing screenshot at {all_matches[0]}")
                else:
                    logger.info(
                        f"[{single_ticker}] Screenshot not found. Running scrape + local research..."
                    )
                    try:
                        base_cmd = [config.get_python_exe()]
                        subprocess.run(
                            base_cmd
                            + [
                                str(config.BASE_DIR / "run_swing_research.py"),
                                "--ticker",
                                single_ticker,
                            ],
                            check=True,
                        )
                        subprocess.run(
                            base_cmd
                            + [
                                str(config.BASE_DIR / "run_local_research.py"),
                                "--ticker",
                                single_ticker,
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
                                / f"{safe_target}_chart.png"
                            ),
                            recursive=True,
                        )
                        if new_matches:
                            new_matches.sort(key=os.path.getmtime, reverse=True)
                            chart_files.append(new_matches[0])
                        else:
                            logger.error(
                                f"[{single_ticker}] Scraper finished but failed to generate screenshot."
                            )
                    except Exception as e:
                        logger.error(f"[{single_ticker}] Error running scraper: {e}")
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
            thesis_files = glob.glob(str(sd / "**" / "*_thesis.json"), recursive=True)
            for thesis_file in thesis_files:
                t = Path(thesis_file).name.replace("_thesis.json", "")
                t_parent = Path(thesis_file).parent
                flagged.append((t, _load_triage_record(raw_dir, deep_dir, t, tdir=t_parent)))

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
            tdir = _triage_subdir_for(t) or (raw_dir / t) or raw_dir
            matches = glob.glob(str(tdir / f"{t}_*.png"))
            if not matches:
                matches = glob.glob(str(raw_dir / f"{t}_*.png"))
            if matches:
                chart_files.append(matches[0])

        # Fallback: if triage has no flagged tickers, discover all chart files directly in raw_dir
        if not chart_files and raw_dir.exists():
            for sub_p in sorted(raw_dir.glob("*/")):
                if sub_p.is_dir() and not sub_p.name.startswith((".", "NASDAQ_", "NYSE_", "BATS_", "AMEX_")):
                    t_matches = glob.glob(str(sub_p / "*_chart.png"))
                    if t_matches:
                        chart_files.append(t_matches[0])

        chart_files = list(dict.fromkeys(chart_files))

    if not chart_files:
        logger.warning("No charts found for deep research.")
        return
    original_gem_path = config.BASE_DIR / "gems" / "revanth-original-gem.md"
    response_path = config.BASE_DIR / "gems" / "response.md"
    bible_path = config.BASE_DIR / "gems" / "revanth-bible.md"
    ponytail_path = config.BASE_DIR / "gems" / "ponytail_finance.md"
    independent_gem_path = config.BASE_DIR / "gems" / "independent_gem.md"

    if not original_gem_path.exists() or not response_path.exists() or not bible_path.exists():
        logger.error("Cannot find Deep Research Gem files.")
        return

    with open(original_gem_path, "r", encoding="utf-8") as f:
        gem_text = f.read()
    with open(response_path, "r", encoding="utf-8") as f:
        response_text = f.read()
    with open(bible_path, "r", encoding="utf-8") as f:
        bible_text = f.read()
    ponytail_text = ponytail_path.read_text(encoding="utf-8") if ponytail_path.exists() else ""
    independent_gem_text = independent_gem_path.read_text(encoding="utf-8") if independent_gem_path.exists() else ""

    few_shot_example = ""
    example_path = config.BASE_DIR / "gems" / "few_shot_template.md"
    if example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            few_shot_example = f.read()

    # Move invariant prompt blocks into system_prompt for prefix caching across all tickers in the run
    system_prompt = f"--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---\n{ponytail_text}\n\n{gem_text}\n\n--- REVANTH BIBLE (Rules & Framework) ---\n{bible_text}\n\n--- STRICT EXAMPLE OF THE EXACT FORMAT, ASCII ART, AND DEPTH YOU MUST OUTPUT ---\n{few_shot_example}\n\n{response_text}"
    system_prompt_independent = f"--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---\n{ponytail_text}\n\n{independent_gem_text}\n\n{response_text}"

    logger.info(f"Starting Agentic Deep Research Phase for {len(chart_files)} tickers (Dual Report Mode: Proprietary + Independent)...")

    _drift_checker = ThesisDriftChecker()
    macro_news = _pull_macro_news(date_str)

    seen_tickers = set()
    for chart_path in chart_files:
        raw_name = Path(chart_path).name.replace("_chart.png", "")
        ticker = raw_name.split("_")[-1].upper()
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)

        # Artifacts live in the ticker subfolder (or deep-research folder after segregation)
        ticker_raw_dir = raw_dir / ticker
        ticker_raw_dir.mkdir(parents=True, exist_ok=True)
        tdir = _triage_subdir_for(ticker) or ticker_raw_dir
        out_dir = tdir
        out_path = out_dir / f"{ticker}_gemini_thesis.md"

        logger.info(f"[{ticker}] Initiating Deep Research (2-pass flow)...")

        dw_path = tdir / f"{ticker}_datawindow.json"
        if not dw_path.exists():
            if (raw_dir / ticker / f"{ticker}_datawindow.json").exists():
                dw_path = raw_dir / ticker / f"{ticker}_datawindow.json"
            elif (raw_dir / f"{ticker}_datawindow.json").exists():
                dw_path = raw_dir / f"{ticker}_datawindow.json"

        csv_path = tdir / f"{ticker}_datawindow.csv"
        if not csv_path.exists():
            if (raw_dir / ticker / f"{ticker}_datawindow.csv").exists():
                csv_path = raw_dir / ticker / f"{ticker}_datawindow.csv"
            elif (raw_dir / f"{ticker}_datawindow.csv").exists():
                csv_path = raw_dir / f"{ticker}_datawindow.csv"

        dossier_path = tdir / f"{ticker}_news_research.md"
        if not dossier_path.exists():
            if (raw_dir / ticker / f"{ticker}_news_research.md").exists():
                dossier_path = raw_dir / ticker / f"{ticker}_news_research.md"
            elif (raw_dir / f"{ticker}_news_research.md").exists():
                dossier_path = raw_dir / f"{ticker}_news_research.md"

        # Load data window JSON (math state) early for fallback filtering
        data_window_str = "{}"
        dw_dict = {}
        if dw_path.exists():
            try:
                with open(dw_path, "r", encoding="utf-8") as f:
                    dw_dict = json.load(f)
                    data_window_str = json.dumps(dw_dict, indent=2)
            except Exception as e:
                logger.warning(f"[{ticker}] Error loading data window: {e}")
        else:
            logger.warning(f"[{ticker}] Data window JSON not found at {dw_path}")

        triage_record = _load_triage_record(raw_dir, deep_dir, ticker, tdir=tdir) or {}

        # Snapshot the deterministic verdict BEFORE the flags fallback below.
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

            flags = (
                triage_record.get("flags")
                if isinstance(triage_record, dict)
                else None
            ) or []

        # If no triage record exists on disk, compute it deterministically from DW
        if not verdict_record and dw_dict:
            try:
                from src.logic.data_window_filter import run_data_window_filter
                verdict_record = run_data_window_filter(ticker, dw_dict)
                triage_record = verdict_record
                flags = verdict_record.get("flags") or []
            except Exception as e:
                logger.warning(f"[{ticker}] Fallback data_window_filter failed: {e}")

        # Persist _triage.json so validate_report and downstream consumers have the exact ground truth
        if verdict_record:
            try:
                triage_out = tdir / f"{ticker}_triage.json"
                if not triage_out.exists():
                    triage_out.write_text(json.dumps(verdict_record, indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug(f"[{ticker}] Failed to write {ticker}_triage.json: {e}")

        # Multi-Agent Debate will be run live.
        # We ignore the stale mod_agree/mod_disagree from local triage.

        flags_block = _format_flags_block(flags)
        engine_math_block = _format_engine_math_block(verdict_record)
        triggers_block = _format_triggers_block(verdict_record)

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

        def _get_image_path(name: str) -> str | None:
            for p in (tdir / name, raw_dir / ticker / name, raw_dir / name):
                if p.exists():
                    return str(p)
            return None

        plain_p = _get_image_path(f"{ticker}_chart_plain.png")
        zoom_p = _get_image_path(f"{ticker}_chart_zoom.png")
        wide_p = _get_image_path(f"{ticker}_chart.png")

        # Multimodal image pair: Plain (clean naked price action) + Zoom (indicator & structural overlay)
        if plain_p and zoom_p:
            image_paths = [plain_p, zoom_p]
        elif zoom_p:
            image_paths = [zoom_p]
        else:
            image_paths = [p for p in (wide_p, zoom_p) if p and os.path.exists(p)]

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
                    dw_spot = float(dw_dict.get("close") or 0.0) if isinstance(dw_dict, dict) else 0.0
                    dw_exp_move = float(dw_dict.get("Exp Move Pct 21b") or dw_dict.get("exp_move_pct") or 0.0) if isinstance(dw_dict, dict) else None

                    valid_strats = []
                    rejected_count = 0
                    for s in strat_data:
                        formula = s.get("formula", "")
                        stype = (s.get("strategy_type", "") or "").upper().replace(" ", "_")
                        strikes = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[CPcp]\b", formula)]

                        extra_short_st = None
                        if "JADE_LIZARD" in stype or "JADE" in stype:
                            puts = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[Pp]\b", formula)]
                            calls = [float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*[Cc]\b", formula)]
                            if len(puts) >= 1 and len(calls) >= 2:
                                extra_short_st = puts[0]
                                short_st = min(calls)
                                long_st = max(calls)
                            else:
                                long_st, short_st = None, None
                        elif len(strikes) < 2:
                            if any(tag in stype for tag in ("SHORT", "COVERED", "SELL", "CASH_SECURED", "CSP", "WRITE")):
                                short_st = strikes[0] if strikes else None
                                long_st = None
                            else:
                                long_st = strikes[0] if strikes else None
                                short_st = None
                        else:
                            lo, hi = min(strikes), max(strikes)
                            is_put = bool(re.search(r"\d+\s*[Pp]\b", formula)) or "PUT" in stype
                            is_credit = any(tag in stype for tag in ("BEAR_CALL", "CALL_CREDIT", "BULL_PUT", "PUT_CREDIT")) or "CREDIT" in stype
                            if is_credit:
                                short_st, long_st = (hi, lo) if is_put else (lo, hi)
                            else:
                                long_st, short_st = (hi, lo) if is_put else (lo, hi)

                        is_valid, defects = validate_strike_geometry(
                            strategy_type=s.get("strategy_type", ""),
                            spot_price=dw_spot,
                            exp_move_pct_21b=dw_exp_move,
                            long_strike=long_st,
                            short_strike=short_st,
                            extra_short_strike=extra_short_st,
                            max_profit=float(s.get("max_profit", 0) or 0) if s.get("max_profit") is not None else None,
                            max_loss=float(s.get("max_loss", 0) or 0) if s.get("max_loss") is not None else None,
                        )
                        if is_valid:
                            valid_strats.append(s)
                        else:
                            rejected_count += 1
                            logger.info(f"[{ticker}] Strategy Filter rejected '{formula}': {', '.join(defects)}")

                    if valid_strats:
                        lines = [
                            f"--- TRADINGVIEW STRATEGY FINDER (GEOMETRY-VALIDATED STRUCTURES FOR {ticker}) ---",
                            "(Note: Geometry-validated implies strikes are OTM / within ExpMove bounds. Naked short puts & Jade Lizards carry undefined downside assignment risk on 100 shares.)",
                            "| Expiry | Days | Strategy | Formula/Strikes | Max Profit | Max Loss / Risk Profile | R:R | Breakeven |",
                            "|---|---|---|---|---|---|---|---|",
                        ]
                        for s in valid_strats[:12]:  # provide top 12 valid diverse spreads across bullish/bearish/income
                            raw_loss = s.get("max_loss")
                            try:
                                loss_val = float(raw_loss or 0)
                            except (ValueError, TypeError):
                                loss_val = 0.0

                            stype_name = s.get("strategy_type", "")
                            is_undefined_risk = (
                                abs(loss_val) > (dw_spot * 100 * 0.5)
                                or stype_name in ("Short Put", "Jade Lizard", "Cash Secured Put")
                                or loss_val < -50000
                            )
                            if is_undefined_risk:
                                loss_str = "Undefined (Assignment Risk on 100sh)"
                            else:
                                loss_str = str(raw_loss)

                            lines.append(
                                f"| {s.get('expiration')} | {s.get('days')} | {stype_name} | "
                                f"{s.get('formula')} | {s.get('max_profit')} | {loss_str} | "
                                f"{s.get('reward_risk')} | {s.get('breakeven')} |"
                            )
                        tv_strat_block = "\n".join(lines)
                    elif rejected_count > 0:
                        tv_strat_block = (
                            f"--- TRADINGVIEW STRATEGY FINDER ({ticker}) ---\n"
                            f"The Strategy Finder returned {len(strat_data)} pre-computed spreads, "
                            f"but ALL {rejected_count} were rejected by the deterministic strike geometry validator "
                            f"(ITM short legs, naked-long disguises, or accounting mismatches). "
                            f"Do NOT invent a spread. Use the live `fetch_options_chain` and `scrape_tradingview_options_finder` tools "
                            f"to find a valid structure, or recommend SKIP if no clean geometry exists."
                        )
            except Exception as e:
                logger.warning(f"[{ticker}] Error loading tv_strategies: {e}")

        options_block = f"{gex_block}\n\n{tv_strat_block}".strip()

        # Persist fetched blocks for validation and auditability
        deep_context = {
            "ticker": ticker,
            "date": date_str,
            "fresh_news": fresh_news,
            "macro_news": macro_news,
            "market_sentiment": market_sentiment_block,
            "av_block": av_block,
            "social_block": social_block,
            "earnings_fact_block": earnings_fact_block,
            "institutional_block": institutional_block,
            "grounded_block": grounded_block,
            "macro_grounded_block": macro_grounded_block,
            "live_quote_block": live_quote_block,
            "gex_block": gex_block,
            "tv_strat_block": tv_strat_block,
        }
        try:
            (tdir / f"{ticker}_deep_context.json").write_text(
                json.dumps(deep_context, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[{ticker}] Failed to write deep_context.json: {e}")

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
            "You are a rigorous, evidence-based Bullish Technical & Fundamental Analyst operating under Ponytail Finance rules (Occam's Razor, minimal bloat, hard math). "
            "Your job is to identify valid positive drivers, catalyst timelines, and support levels for this ticker. "
            "CRITICAL RULES: Every claim must cite an exact Data Window field name or verified news item. "
            "Never fabricate moving average levels or invent technical indicators. Never contradict the pre-decoded Section 2d-1 engine math. "
            "Output your bull case in a concise, punchy markdown format."
        )
        bear_sys = (
            "You are a skeptical, disciplined Bearish Technical & Fundamental Analyst operating under Ponytail Finance rules (Occam's Razor, minimal bloat, hard math). "
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

        {debate_block}

        ## 2B. MULTIMODAL CHART STATE (2 High-Resolution Vision Images Provided)
        - Image 1 (Plain Chart: `{ticker}_chart_plain.png`): Clean naked Japanese candlesticks & raw volume sub-pane. Use this to visually identify swing pivot highs/lows, rejection tails (pin bars), and gap boundaries without indicator clutter.
        - Image 2 (Indicator Overlay: `{ticker}_chart_zoom.png`): 90-day technical view displaying Darvas compression boxes, Volume Profile POC/VAH/VAL, and Anchored VWAPs.

        --- 2g. QUANTITATIVE SANDBOX & 1-YEAR HISTORICAL DATA DICTIONARY (`df` & `dw`) ---
        The quantitative sandbox (`execute_python_code`) automatically pre-loads:
        - `df`: 300 daily bars x 85 columns (CSV File: `{csv_path}`)
        - `dw`: Latest closed bar dictionary (JSON File: `{dw_path}`)
        - `np`, `pd`, `scipy`, `stats`, `math`, `json`, `datetime`
        
        📊 COLUMN HEADERS & DEFINITIONS IN `df`:
        1. OHLCV & Volume:
           - `time` (bar date), `open`, `high`, `low`, `close`, `Volume`
           - `RVOL Vs Avg` (Relative Volume vs 20-day baseline; >1.5 = institutional surge)
           - `Z Volume` (Normalized volume Z-score)
        2. Moving Averages & Trend Anchors:
           - `Sprint Line EMA` (Fast 8 EMA), `Hull Baseline HMA` (Hull moving average baseline)
           - `MA 20 Fast`, `MA 50 Mid`, `MA 200 Slow`
           - `Weinstein MA 150` (150-day SMA, Stan Weinstein Stage Analysis baseline)
           - `Golden Cross`, `Death Cross` (50/200 MA cross flags: 1/0)
        3. Multi-Factor Scores & Market Stages:
           - `Buy Score` (0-100 composite score), `Sell Score` (0-100)
           - `Buy Sigma Evidence`, `Sell Sigma Evidence` (Statistical sigma evidence for directional bias)
           - `Stage 1 Base 2 Up 3 Top 4 Down` (1=Base/Accumulation, 2=Advancing Uptrend, 3=Top/Distribution, 4=Declining Downtrend, 5=Recovery)
           - `Stage Age Bars` (Number of bars elapsed in current stage)
           - `Action Long Code` (8=Watch, 10=Wait, 16=Blow-off Exhaustion, 20=Reversal, etc.)
           - `Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz` (0=Healthy, 1=Extended, 2=Climax, 3=Distribution, 4=Down, 5=Ignition, 6=Squeeze)
        4. Trade Geometry & Structural Zones:
           - `Long Entry Zone Bot`, `Long Entry Zone Top` (Bounding box of low-risk buyer defense)
           - `Long Stop Loss`, `Long Target`, `Long Target T1 Waypoint`
           - `Short Entry Zone Bot`, `Short Entry Zone Top`, `Short Stop Loss`, `Short Target`
           - `Long RR At Market` (Current at-market Reward-to-Risk ratio), `RR To Target` (Zone R:R)
           - `Entry At Market 0No 1L 2S 3Both` (0=No market entry, 1=Long at market OK)
           - `Long Ignition Fresh Breakout` (Flag: 1 if fresh breakout ignition bar)
        5. Extension, Momentum & Z-Scores:
           - `Ext Pct vs MA200` ((Close - MA200)/MA200 * 100; >25-60% = negative expectancy exclusion)
           - `Exhaustion Gradient` (Slope of overextension), `Ext Z Self Relative` (Z-score vs distribution)
           - `Z Velocity` (>2.0 indicates blow-off velocity), `Z RSI`, `Z Elasticity`
           - `Trend Bars Up` (Consecutive bars closing higher)
        6. Volume Profile & Anchored VWAPs:
           - `Darvas Box Top` (Upper boundary of recent consolidation base)
           - `VP POC` (Point of Control), `VP VAH` (Value Area High), `VP VAL` (Value Area Low)
           - `VP HVN Above`, `VP HVN Below` (High Volume Nodes)
           - `AVWAP Support`, `AVWAP Resistance` (Anchored VWAP from key swing pivots)
        7. Volatility, Implied Energy & Options State:
           - `HV20 Ann Pct` (20-day annualized Historical Realized Volatility)
           - `Energy IV30 Ann Pct` (30-day annualized Implied Volatility)
           - `Energy IV Rank Pct` (IV percentile rank 0-100%)
           - `Energy IV HV Spread` (IV minus HV; positive = IV rich / premium selling, negative = IV cheap / debit buying)
           - `Energy State 3 Exp 2 Warm 1 Sqz 0 Dorm` (3=Expansion, 2=Warming, 1=Squeeze, 0=Dormant)
           - `Exp Move Pct 21b` (21-bar Expected Move percentage)
           - `ADX 14`, `DMI DI Plus`, `DMI DI Minus`
        8. Bitmasks & Signal Packs:
           - `Signal Pack` (Bit 2 = Fade Gate active/inactive)
           - `Reversal Pattern Mask`, `Bear Warning Mask`, `Weak Level Mask`
           - `MTF Long Short Pack` (Multi-Timeframe alignment)

        You have access to LIVE TOOLS for fundamental discovery, pattern recognition, and quantitative execution:
        - `detect_candlestick_patterns` to scan the 1-year OHLCV dataset for high-conviction Pin Bars (rejection wicks), Gap Retests, Inside Day compressions, and Engulfing patterns.
        - `fetch_finnhub_news` and `fetch_alpaca_news` for the latest ticker-specific news.
        - `search_web` for broader macro or catalyst context.
        - `fetch_options_chain` for real-time Greeks, multi-horizon strikes (both short-dated and LEAPS with min_dte=120, max_dte=365+), and exact contract quotes.
        - `scrape_tradingview_options_finder` to dynamically search TradingView's proprietary Strategy Finder for pre-computed spreads matching your desired prediction period ('Next month', 'Next 3 months', 'Next 6 months') and expected move direction.
        - `run_quantitative_plugin` to run specialized analytics ('candlestick_patterns', 'order_flow', 'earnings_history', 'squeeze_expansion', 'htf_confluence', or 'all').
        - `fetch_prior_research` to retrieve our most recent prior research report from reports/<date>/<ticker>_summary.md within the last 14 days. Use this to audit active stalk states, track thesis evolution, and check whether prior limit orders or triggers have played out.
        - `execute_python_code`: Act as Lead Quantitative Trader. You MUST write and execute a Python script to run a Monte Carlo simulation (10,000 paths) using the provided IV30 and HV20 to calculate the exact mathematical probability of hitting your Profit Target vs your Stop Loss before finalizing your Options Plan. CRITICAL: Your python code MUST be concise. Use arrays and for-loops to test multiple horizons or targets. DO NOT unroll scenarios into 50+ lines of repeated code. Also formulate any other open-ended mathematical hypothesis tailored to this specific ticker and market regime (e.g., historical setup backtesting on `df`, volume absorption flow, volatility risk premium $IV - HV$, or options $EV$ / spread payoff math).

        --- PRIOR RESEARCH & PATTERN SYNTHESIS WORKFLOW ---
        When `fetch_prior_research` is called alongside `detect_candlestick_patterns`:
        1. **Stalk vs Trigger Audit:** Audit whether price tested or rejected the prior session's stalk limit, entry zone, or breakout trigger level.
        2. **Fresh Rejection Wicks & Gap Retests:** Check if a fresh Pin Bar (rejection wick) or Gap Retest formed today at the key floor that confirms buyer defense and resolves the stalk into an actionable entry.
        3. **Tactical Stop Refinement:** If buyer defense is confirmed by a lower rejection wick, anchor the updated tactical stop directly below the rejection wick low.

        Form your OWN independent verdict from the Data Window, chart, news, and the LIVE data you pull - do not 
        assume any prior read is correct. Act as Senior Quantitative Portfolio Manager and EMIT a single-pass, high-conviction trade thesis:
        - ACCURATE STRUCTURAL R:R: Calculate mathematical R:R as `(Target 1 - Entry) / (Entry - Tactical Stop)`. If price is testing a defended structural floor (e.g. Doji low, Gap floor, MA20) with tactical R:R >= 2.0, evaluate it as an actionable floor-defense limit entry rather than blindly disqualifying it on the distant macro zone.
        - EXPECTED STOCK PRICE RANGE: support floor, resistance ceiling, and your projected 14-120 day trading range.
        - OPTIONS PLAN (DUAL HORIZON): Evaluate both Tactical Swing (21-45 DTE credit/debit) AND Multi-Quarter / LEAPS (90-365+ DTE Deep ITM Calls). If IV Rank > 70% and IV/HV spread is positive, explicitly favor defined-risk credit spreads (e.g. Bull Put Spread) over buying expensive extrinsic premium.
        - ENTRY, STOP LOSS, and PROFIT TARGET (exact prices) with strict binary invalidation ("The ONE Thing").
        - CONVICTION and risk/reward rationale.
        
        CRITICAL: Emit your final Portfolio Manager Thesis EXACTLY as instructed in the system prompt format.
        CRITICAL: At the very end of your response, you MUST append a strict JSON array of falsifiable predictions with exact probabilities (the "SUPERFORECASTING PREDICTIONS" block) exactly as shown in the example.
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

                # Clean leading tool-call chatter / pre-synthesis thoughts if present
                clean_response = response.strip()
                match_header = re.search(r"(?m)^#\s+[A-Z0-9]+(?:\s*\||\s*$)", clean_response)
                if match_header and match_header.start() > 0:
                    clean_response = clean_response[match_header.start():].strip()

                # Emit final clean report directly in 1-pass Senior PM quality
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(clean_response)

                try:
                    _drift_checker.check_and_write(ticker, date_str, clean_response, out_dir)
                except Exception as e:
                    logger.warning(f"[{ticker}] Thesis drift check failed: {e}")

                response_for_parse = clean_response

                # Parse metrics via regex for Google Sheets tracker
                # Parse metrics via regex for Google Sheets tracker
                verdict_match = re.search(
                    r"\*\*Verdict:\*\*\s*(.*?)(?=\s*·|\s*\*\*Conviction|$)", response_for_parse, re.IGNORECASE
                )
                if not verdict_match:
                    verdict_match = re.search(
                        r"\|\s*[*]*Equity[^\*|]*[*]*\s*\|\s*[*]*([A-Z\s\(\)]+?)[*]*\s*\|", response_for_parse, re.IGNORECASE
                    )
                if not verdict_match:
                    verdict_match = re.search(
                        r"\|\s*[*]*Options[^\*|]*[*]*\s*\|\s*[*]*([A-Z\s\(\)]+?)[*]*\s*\|", response_for_parse, re.IGNORECASE
                    )

                conviction_match = re.search(
                    r"\*\*Conviction[^\*]*:\*\*\s*([\d\.]+)(?:\s*/\s*10)?", response_for_parse, re.IGNORECASE
                )
                if not conviction_match:
                    conviction_match = re.search(
                        r"\|\s*([\d\.]+)\s*/\s*10\s*\|", response_for_parse, re.IGNORECASE
                    )

                thesis_match = re.search(
                    r"\*\*The Thesis in 2 Sentences:\*\*\s*(.+)", response_for_parse, re.IGNORECASE
                )

                entry_match = re.search(
                    r"(?:\|\s*[*]*(?:Pullback\s+)?(?:Limit\s+)?Entry[*]*\s*\|\s*[$]?\s*(\d+(?:\.\d+)?)|-\s*[*]*(?:Pullback\s+)?(?:Limit\s+)?Entry[*]*:\s*[$]?\s*(\d+(?:\.\d+)?))",
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
                sheet_updated = tracker.update_deep_research(date_str, ticker, payload_dict)

                # Save identical copy to reports folder
                reports_dir = config.BASE_DIR / "reports" / date_str
                reports_dir.mkdir(parents=True, exist_ok=True)
                digest_path = reports_dir / f"{ticker}_summary.md"
                with open(digest_path, "w", encoding="utf-8") as f:
                    f.write(clean_response)

                if sheet_updated:
                    logger.info(
                        f"[{ticker}] Deep Research completed, Google Sheet updated, and Summary generated successfully!"
                    )
                else:
                    logger.info(
                        f"[{ticker}] Deep Research completed and Summary generated successfully (Sheet update skipped: ticker not found in today's sheet)."
                    )

                # ========================================================
                # PASS 2-IND: INDEPENDENT MACRO & TECHNICAL THESIS
                # ========================================================
                logger.info(f"[{ticker}] Generating Independent Macro & Technical Summary Report...")
                independent_user_prompt = f"""
                RESEARCH DATE: {date_str}   (SYSTEM/TODAY: {datetime.now().strftime("%Y-%m-%d")})
                VERIFY every macro, CPI, Fed, and earnings reference against this date.

                I am requesting an INDEPENDENT Macro & Technical Analysis for the ticker: {ticker}.

                --- 1. DATA WINDOW (Exact Math State from TradingView — the last CLOSED bar) ---
                {data_window_str}

                --- 1a. LIVE QUOTE (pre-fetched at run time) ---
                {live_quote_block}

                --- 2. NEWS RESEARCH DOSSIER ---
                {news_dossier}

                --- 2a. FRESH NEWS (LIVE) ---
                {fresh_news}

                --- 2b. MACRO NEWS (LIVE) ---
                {macro_news}

                {market_sentiment_block}

                --- 2c. FUNDAMENTAL & PER-TICKER SOCIAL ---
                {av_block}
                {social_block}
                {institutional_block}
                {grounded_block}

                --- 2c-ii. MACRO GROUNDING ---
                {macro_grounded_block}

                --- 2e. EARNINGS DATE ---
                {earnings_fact_block}

                ## MULTIMODAL CHART STATE (Vision Images Provided)
                - Image 1: Naked Japanese candlesticks & raw volume.
                - Image 2: 90-day technical view displaying Darvas compression boxes, Volume Profile POC/VAH/VAL, and Anchored VWAPs.

                --- QUANTITATIVE SANDBOX & 1-YEAR HISTORICAL DATAFRAME (`df`) ---
                The quantitative sandbox (`execute_python_code`) pre-loads:
                - `df`: 300 daily bars x 85 columns (CSV File: `{csv_path}`)
                - `dw`: Latest closed bar dictionary (JSON File: `{dw_path}`)
                - `np`, `pd`, `scipy`, `stats`, `math`, `json`, `datetime`
                - Live tools: `fetch_options_chain`, `detect_candlestick_patterns`, `run_quantitative_plugin`.

                MANDATORY QUANTITATIVE WORKFLOW:
                1. Act as Lead Quantitative Trader & Macro Strategist.
                2. You MUST use your python sandbox (`execute_python_code`) for MATHEMATICAL VERIFICATION:
                   - Verify exact historical levels in `df` (52W high/low, MA 50/200 baselines, gap boundaries, and volume profile nodes). Do not invent prices.
                   - Run Monte Carlo simulations using `HV20` and `IV30` to model P(Target First) vs P(Stop First) across 21d, 30d, 45d, and 90d horizons.
                   - Test live options contracts from `fetch_options_chain` to calculate exact mathematical Greeks, Net Credit/Debit, and risk-to-reward.
                3. Synthesize the macro backdrop (Treasury yields, DXY, SPY/QQQ regime) vs micro company news, evaluate the auction liquidity (VP POC, VAH/VAL, RVOL), compute your independent Technical Rating, and emit your comprehensive thesis and trade plan into the exact markdown structure defined in your rules card.
                """

                ind_response = None
                if use_remote:
                    try:
                        ind_response = query_local_llm(
                            system_prompt=system_prompt_independent,
                            user_prompt=independent_user_prompt,
                            json_mode=False,
                            use_openrouter=True,
                            image_paths=image_paths,
                            use_tools=True,
                            max_tokens=8192,
                            summarize_tool_context=f"The simulated date is {date_str}. Treat {date_str} as the present day."
                        )
                    except Exception as e_ind_remote:
                        logger.warning(f"[{ticker}] Remote Independent API inference failed ({e_ind_remote}) — falling back to Local GPU LLM!")
                        ind_response = None

                if not ind_response:
                    logger.info(f"[{ticker}] Executing Independent Pass with Local GPU LLM Server...")
                    ind_response = query_local_llm(
                        system_prompt=system_prompt_independent,
                        user_prompt=independent_user_prompt,
                        json_mode=False,
                        use_openrouter=False,
                        image_paths=image_paths,
                        use_tools=True,
                        disable_thinking=True,
                        max_tokens=8192,
                        summarize_tool_context=f"The simulated date is {date_str}. Treat {date_str} as the present day."
                    )

                if ind_response:
                    clean_ind_response = ind_response.strip()
                    match_ind_header = re.search(r"(?m)^#\s+[A-Z0-9]+(?:\s*\||\s*$)", clean_ind_response)
                    if match_ind_header and match_ind_header.start() > 0:
                        clean_ind_response = clean_ind_response[match_ind_header.start():].strip()

                    ind_report_path = reports_dir / f"{ticker}_independent.md"
                    with open(ind_report_path, "w", encoding="utf-8") as f:
                        f.write(clean_ind_response)

                    ind_triage_path = out_dir / f"{ticker}_independent_thesis.md"
                    with open(ind_triage_path, "w", encoding="utf-8") as f:
                        f.write(clean_ind_response)

                    logger.info(f"[{ticker}] Independent Macro & Technical Summary generated at {ind_report_path}!")

                    # ========================================================
                    # PASS 2-JUDGE: PONYTAIL PM ARBITRATION & CROSS-EXAMINATION
                    # ========================================================
                    logger.info(f"[{ticker}] Running Senior PM Ponytail Judge (Cross-Examining Report A vs Report B)...")
                    judge_sys_prompt = (
                        "You are the Chief Investment Officer & Senior Portfolio Manager operating under Ponytail Finance rules "
                        "(Occam's Razor, minimal bloat, hard math, ruthless risk management).\n\n"
                        "You have received two independent reports for this ticker:\n"
                        "- REPORT A: Proprietary Quantitative Engine (Bible rules, Code 8 / Zone R:R, Titanium levels)\n"
                        "- REPORT B: Independent Macro & Volume Profile Study (Macro attribution, 12 MAs, VRVP POC, IV/HV Forensics)\n\n"
                        "Your job is to cross-examine both reports with a strict FOR vs AGAINST trial, settle their disagreements, and issue the FINAL binding trading directive.\n\n"
                        "Output in this exact markdown format:\n\n"
                        "# [TICKER] | ⚖️ SENIOR PM ARBITRATION & FINAL DIRECTIVE\n\n"
                        "## 🟢 THE CASE FOR (Bull Cross-Examination)\n"
                        "[The strongest, evidence-backed arguments synthesized across both reports for why this trade should be taken]\n\n"
                        "## 🔴 THE CASE AGAINST (Bear Cross-Examination & Traps)\n"
                        "[The strongest risk arguments, hidden traps, and friction points synthesized across both reports for why this trade should be avoided or hedged]\n\n"
                        "## ⚖️ THE JUDGE'S FINAL RULING\n"
                        "* **Concurrence:** [Where Model A and Model B 100% agree]\n"
                        "* **Conflict Resolution:** [Where they disagreed, which model is correct, and why]\n"
                        "* **Final Verdict:** **[ENTER (Limit @ Floor) / ENTER (Breakout) / ENTER (Options Credit) / CASH / SKIP]** (Conviction: X/10)\n\n"
                        "## 🎯 FINAL ACTIONABLE DIRECTIVES\n"
                        "* **Equity (Shares):** [Exact Limit Price, Tactical Stop, Target 1, Target 2, R:R]\n"
                        "* **Options (Derivatives):** [Exact Structure, Expiry, Strikes, Net Credit/Debit, Max Loss, Break-Even]\n"
                        "* **The ONE Thing Invalidation:** [The single binary price condition that kills the trade immediately]\n"
                    )

                    judge_user_prompt = f"""
                    TICKER: {ticker} | DATE: {date_str}
                    
                    --- REPORT A: PROPRIETARY QUANTITATIVE ENGINE ---
                    {clean_response}
                    
                    --- REPORT B: INDEPENDENT MACRO & VOLUME PROFILE STUDY ---
                    {clean_ind_response}
                    """

                    judge_response = query_local_llm(
                        system_prompt=judge_sys_prompt,
                        user_prompt=judge_user_prompt,
                        json_mode=False,
                        use_openrouter=False,
                        use_tools=False,
                        disable_thinking=True,
                        max_tokens=3072,
                    )

                    if judge_response:
                        clean_judge = judge_response.strip()
                        match_judge = re.search(r"(?m)^#\s+[A-Z0-9]+(?:\s*\||\s*$)", clean_judge)
                        if match_judge and match_judge.start() > 0:
                            clean_judge = clean_judge[match_judge.start():].strip()

                        arbitration_path = reports_dir / f"{ticker}_arbitration.md"
                        with open(arbitration_path, "w", encoding="utf-8") as f:
                            f.write(clean_judge)

                        # Append arbitration ruling to the bottom of both reports for complete self-contained context
                        with open(digest_path, "a", encoding="utf-8") as f:
                            f.write(f"\n\n---\n\n{clean_judge}\n")

                        with open(ind_report_path, "a", encoding="utf-8") as f:
                            f.write(f"\n\n---\n\n{clean_judge}\n")

                        logger.info(f"[{ticker}] Senior PM Ponytail Arbitration & Final Directive generated at {arbitration_path}!")
                else:
                    logger.error(f"[{ticker}] Independent LLM returned empty response.")
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
    parser.add_argument("--local", action="store_true", help="Force 100 percent local LLM inference (no OpenRouter/remote API calls)")

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker
    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_deep_research(target_date or datetime.now().strftime("%Y-%m-%d"), target_ticker, force_local=args.local)
