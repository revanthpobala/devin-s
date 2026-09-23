"""pipeline.py — thin orchestrator for the deep research pipeline.

This is the new home of ``run_deep_research()`` after the monolith was
decomposed into focused sub-modules.  The function itself is now ~120 lines;
all heavy logic lives in the sibling modules.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src import config
from src.logic.thesis_drift import ThesisDriftChecker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standalone Ponytail PM review (was at module level in the old file)
# ---------------------------------------------------------------------------

def run_ponytail_pm_review(
    ticker: str,
    date_str: str,
    triage_record: dict,
    dw_dict: dict,
    news_dossier: str,
    fresh_news: str,
    macro_news: str,
    force_local: bool = False,
) -> str:
    """Run a quick local Ponytail PM review pass (no multimodal, no tools).

    Returns the LLM text response.
    """
    from src.clients.llm_client import query_local_llm
    from src.logic.deep_research.context_builder import (
        format_engine_math_block,
        format_flags_block,
    )

    flags = triage_record.get("flags") or [] if isinstance(triage_record, dict) else []
    flags_block = format_flags_block(flags)
    engine_math_block = format_engine_math_block(triage_record)
    data_window_str = __import__("json").dumps(dw_dict, indent=2) if dw_dict else "{}"

    ponytail_path = config.BASE_DIR / "gems" / "ponytail_finance.md"
    ponytail_text = ponytail_path.read_text(encoding="utf-8") if ponytail_path.exists() else ""

    system_prompt = (
        f"--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---\n{ponytail_text}\n\n"
        "You are the Ponytail Senior Portfolio Manager. Perform a quick no-frills review."
    )
    user_prompt = (
        f"TICKER: {ticker} | DATE: {date_str}\n\n"
        f"--- DATA WINDOW ---\n{data_window_str}\n\n"
        f"{flags_block}\n\n{engine_math_block}\n\n"
        f"--- NEWS DOSSIER ---\n{news_dossier}\n\n"
        f"--- FRESH NEWS ---\n{fresh_news}\n\n"
        f"--- MACRO NEWS ---\n{macro_news}\n\n"
        "Issue your concise PM verdict."
    )

    return query_local_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        use_openrouter=not force_local,
        use_tools=False,
        disable_thinking=True,
        max_tokens=2048,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_deep_research(date_str: str, target_ticker: Optional[str] = None, force_local: bool = False):
    """
    Automates the Deep Research validation phase using OpenAI-compatible tool calling.

    COST GATE: the paid Minimax passes (Pass 1 + Pass 2) run ONLY for tickers the
    free local Qwen triage flagged via send_for_deep_research == true. Batch mode enforces
    this; a manually-specified target_ticker is an explicit request and is always run.
    """
    from src.logic.deep_research.artifact_loader import (
        load_data_window,
        load_news_dossier,
        resolve_artifact_paths,
        resolve_triage,
    )
    from src.logic.deep_research.arbitration import run_arbitration
    from src.logic.deep_research.context_builder import (
        build_daily_alerts_block,
        build_debate_payload,
        build_independent_user_prompt,
        build_pine_benchmark_block,
        build_preloaded_block,
        build_user_prompt,
        format_engine_math_block,
        format_flags_block,
        format_scenario_block,
        format_state_response_block,
        format_triggers_block,
        format_unmasked_recency_block,
        sanitize_dw_for_independent,
    )
    from src.logic.deep_research.debate import format_debate_block, run_debate
    from src.logic.deep_research.live_fetcher import (
        fetch_live_context,
        pull_macro_news,
        save_deep_context,
    )
    from src.logic.deep_research.output_writer import save_model_a_report, save_model_b_report
    from src.logic.deep_research.pass2 import run_pass2
    from src.logic.deep_research.tv_strategies import load_tv_strategies

    triage_dir = config.BASE_DIR / "data" / "triage" / date_str
    deep_dir = triage_dir / "_DEEP_RESEARCH"
    raw_dir = config.BASE_DIR / "data" / "raw" / date_str

    # ------------------------------------------------------------------
    # Helper: find artifact sub-directory for a ticker
    # ------------------------------------------------------------------
    def _triage_subdir_for(tkr: str) -> Path | None:
        safe = tkr.replace(":", "_")
        for d in (deep_dir / safe, deep_dir, triage_dir / "force" / safe, triage_dir / "force"):
            if (d / f"{safe}_chart.png").exists() or (d / f"{safe}_thesis.json").exists():
                return d
        if (raw_dir / safe).exists() and any((raw_dir / safe).iterdir()):
            return raw_dir / safe
        return None

    # ------------------------------------------------------------------
    # Resolve ticker list
    # ------------------------------------------------------------------
    chart_files: list[str] = []

    if target_ticker:
        target_tickers = [t.strip() for t in target_ticker.split(",") if t.strip()]
        for single_ticker in target_tickers:
            safe_target = single_ticker.replace(":", "_")
            tdir = _triage_subdir_for(single_ticker)
            target_file = (tdir or (raw_dir / safe_target) or raw_dir) / f"{safe_target}_chart.png"
            dw_check = (tdir or (raw_dir / safe_target) or raw_dir) / f"{safe_target}_datawindow.json"

            if not target_file.exists() or not dw_check.exists():
                logger.info(
                    f"[{single_ticker}] Fresh chart/DataWindow missing for {date_str}. "
                    f"Running targeted scrape + local research..."
                )
                try:
                    base_cmd = [config.get_python_exe()]
                    subprocess.run(
                        base_cmd + [str(config.BASE_DIR / "run_swing_research.py"), date_str, "--ticker", single_ticker],
                        check=True,
                    )
                    subprocess.run(
                        base_cmd + [str(config.BASE_DIR / "run_local_research.py"), date_str, "--ticker", single_ticker],
                        check=True,
                    )
                except Exception as e:
                    logger.error(f"[{single_ticker}] Error running scraper: {e}")

            # Re-check after potential scrape
            tdir = _triage_subdir_for(single_ticker)
            target_file = (tdir or (raw_dir / safe_target) or raw_dir) / f"{safe_target}_chart.png"
            if not target_file.exists():
                target_file = raw_dir / f"{safe_target}_chart.png"

            if target_file.exists():
                chart_files.append(str(target_file))
            else:
                logger.error(
                    f"[{single_ticker}] Target artifacts not found for date {date_str}. "
                    f"Aborting to avoid using stale cross-date data."
                )
    else:
        # Batch mode: discover from _DEEP_RESEARCH and force folders
        flagged = []
        for sub in ("_DEEP_RESEARCH", "force"):
            sd = triage_dir / sub
            if not sd.exists():
                continue
            for thesis_file in glob.glob(str(sd / "**" / "*_thesis.json"), recursive=True):
                t = Path(thesis_file).name.replace("_thesis.json", "")
                t_parent = Path(thesis_file).parent
                from src.logic.deep_research.artifact_loader import load_triage_record
                flagged.append((t, load_triage_record(raw_dir, deep_dir, t, tdir=t_parent)))

        from src.logic.data_window_filter import rank_pass_tickers
        cap = int(os.getenv("DEEP_RESEARCH_CAP", "0"))
        ranked = rank_pass_tickers([r for _, r in flagged if r])
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
            matches = glob.glob(str(tdir / f"{t}_*.png")) or glob.glob(str(raw_dir / f"{t}_*.png"))
            if matches:
                chart_files.append(matches[0])

        # Fallback: discover all chart files in raw_dir
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

    # ------------------------------------------------------------------
    # Load gem / system prompt files (once per run — prefix-cache friendly)
    # ------------------------------------------------------------------
    gem_dir = config.BASE_DIR / "gems"
    original_gem_path = gem_dir / "revanth-original-gem.md"
    response_path = gem_dir / "response.md"
    bible_path = gem_dir / "revanth-bible.md"
    ponytail_path = gem_dir / "ponytail_finance.md"
    independent_gem_path = gem_dir / "independent_gem.md"
    independent_response_path = gem_dir / "independent_response.md"

    if not (original_gem_path.exists() and response_path.exists() and bible_path.exists()):
        logger.error("Cannot find Deep Research Gem files.")
        return

    gem_text = original_gem_path.read_text(encoding="utf-8")
    response_text = response_path.read_text(encoding="utf-8")
    bible_text = bible_path.read_text(encoding="utf-8")
    ponytail_text = ponytail_path.read_text(encoding="utf-8") if ponytail_path.exists() else ""
    independent_gem_text = independent_gem_path.read_text(encoding="utf-8") if independent_gem_path.exists() else ""
    independent_response_text = independent_response_path.read_text(encoding="utf-8") if independent_response_path.exists() else ""

    few_shot_example = ""
    example_path = gem_dir / "few_shot_template.md"
    if example_path.exists():
        few_shot_example = example_path.read_text(encoding="utf-8")

    system_prompt = (
        f"--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---\n{ponytail_text}\n\n"
        f"{gem_text}\n\n"
        f"--- REVANTH BIBLE (Rules & Framework) ---\n{bible_text}\n\n"
        f"--- STRICT EXAMPLE OF THE EXACT FORMAT, ASCII ART, AND DEPTH YOU MUST OUTPUT ---\n{few_shot_example}\n\n"
        f"{response_text}"
    )
    system_prompt_independent = (
        f"--- PONYTAIL FINANCE (Occam's Razor & PM Discipline) ---\n{ponytail_text}\n\n"
        f"{independent_gem_text}\n\n"
        f"{independent_response_text}"
    )

    logger.info(
        f"Starting Agentic Deep Research Phase for {len(chart_files)} tickers "
        f"(Dual Report Mode: Proprietary + Independent)..."
    )

    _drift_checker = ThesisDriftChecker()
    macro_news = pull_macro_news(date_str)

    # ------------------------------------------------------------------
    # Per-ticker loop
    # ------------------------------------------------------------------
    seen_tickers: set[str] = set()
    for chart_path in chart_files:
        raw_name = Path(chart_path).name.replace("_chart.png", "")
        ticker = raw_name.split("_")[-1].upper()
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)

        logger.info(f"[{ticker}] Initiating Deep Research (2-pass flow)...")

        ticker_raw_dir = raw_dir / ticker
        ticker_raw_dir.mkdir(parents=True, exist_ok=True)
        tdir = _triage_subdir_for(ticker) or ticker_raw_dir

        paths = resolve_artifact_paths(ticker, tdir, raw_dir)
        out_path = tdir / f"{ticker}_gemini_thesis.md"

        # 1. Load data window
        dw_dict = load_data_window(ticker, paths["dw_json"])
        if not dw_dict:
            logger.error(
                f"[{ticker}] No valid Data Window found on disk for {date_str} at {paths['dw_json']}. "
                f"Aborting to prevent hallucinated thesis."
            )
            continue

        # 2. Resolve triage record
        triage_record, verdict_record = resolve_triage(ticker, dw_dict, raw_dir, deep_dir, tdir)
        flags = (triage_record.get("flags") if isinstance(triage_record, dict) else None) or []

        # 3. Format deterministic blocks
        flags_block = format_flags_block(flags)
        engine_math_block = format_engine_math_block(verdict_record)
        data_window_str = json.dumps(dw_dict, indent=2)

        from src.logic.data_window_filter import parse_data_window
        f_parsed = parse_data_window(dw_dict) if dw_dict else {}

        triggers_src = verdict_record
        if not (isinstance(verdict_record, dict) and verdict_record.get("triggers")) and f_parsed:
            from src.logic.trigger_gaps import compute_triggers
            triggers_src = {"triggers": compute_triggers(f_parsed)}
        triggers_block = format_triggers_block(triggers_src)

        # Build scenarios
        scenarios = []
        try:
            from src.logic.scenario_model import build_scenario
            prices = []
            for key in ("long_target", "ma50", "ma200"):
                v = f_parsed.get(key)
                if v is not None:
                    try:
                        prices.append(float(v))
                    except (ValueError, TypeError):
                        pass
            for raw_key in ("52 Week High", "52 week high"):
                v = dw_dict.get(raw_key)
                if v is not None:
                    try:
                        prices.append(float(v))
                    except (ValueError, TypeError):
                        pass
            ma200 = f_parsed.get("ma200")
            if ma200 is not None:
                try:
                    prices.append(float(ma200) * 0.95)
                except (ValueError, TypeError):
                    pass
            candidate_prices = sorted({round(p, 2) for p in prices})
            scenarios = build_scenario(f_parsed, None, candidate_prices)
        except Exception as e:
            logger.warning(f"[{ticker}] scenario build failed: {e}")

        scenario_block = format_scenario_block(scenarios)
        state_response_block = format_state_response_block(f_parsed, scenarios)
        unmasked_recency_block = format_unmasked_recency_block(dw_dict)

        # 4. Load news dossier
        news_dossier = load_news_dossier(ticker, paths["dossier"], paths["thesis"])

        from src.logic.deep_research.artifact_loader import _dossier_stale_check
        stale = _dossier_stale_check(paths["dossier"], date_str)

        # 5. Fetch live context in parallel
        ctx = fetch_live_context(ticker, tdir, raw_dir, date_str, paths["dossier"], paths, dw_dict)

        # 6. TV strategies
        tv_strat_block = load_tv_strategies(ticker, tdir, raw_dir, dw_dict)
        options_block = f"{ctx.gex_block}\n\n{tv_strat_block}".strip()

        save_deep_context(tdir, ticker, date_str, ctx, tv_strat_block)

        # 7. Prefetch quant context
        from src.logic.deep_research.prefetch import prefetch_deep_research_context
        from src.plugins.schwab_plugin import format_active_position_block
        pre_ctx = prefetch_deep_research_context(ticker, date_str)
        preloaded_block = build_preloaded_block(pre_ctx)

        # Active broker position (Schwab / tracked portfolio)
        active_pos_block = format_active_position_block(ticker)

        # 8. Multi-agent debate
        debate_payload = build_debate_payload(
            ticker=ticker, date_str=date_str,
            data_window_str=data_window_str, live_quote_block=ctx.live_quote_block,
            unmasked_recency_block=unmasked_recency_block, news_dossier=news_dossier,
            fresh_news=ctx.fresh_news, macro_news=macro_news, av_block=ctx.av_block,
            grounded_block=ctx.grounded_block, macro_grounded_block=ctx.macro_grounded_block,
            gex_block=ctx.gex_block, flags_block=flags_block, engine_math_block=engine_math_block,
            earnings_fact_block=ctx.earnings_fact_block, preloaded_block=preloaded_block,
            active_pos_block=active_pos_block,
        )
        debate_result = run_debate(ticker, date_str, debate_payload, tdir)
        debate_block = format_debate_block(debate_result)

        # 9. Daily alerts + Pine benchmark
        daily_alerts_block, alerts_list = build_daily_alerts_block(ticker, raw_dir)
        pine_benchmark_block = build_pine_benchmark_block(ticker, dw_dict)

        # 10. Assemble prompts
        user_prompt = build_user_prompt(
            ticker=ticker, date_str=date_str, data_window_str=data_window_str,
            live_quote_block=ctx.live_quote_block, unmasked_recency_block=unmasked_recency_block,
            news_dossier=news_dossier, fresh_news=ctx.fresh_news, macro_news=macro_news,
            av_block=ctx.av_block, institutional_block=ctx.institutional_block,
            grounded_block=ctx.grounded_block, macro_grounded_block=ctx.macro_grounded_block,
            flags_block=flags_block, engine_math_block=engine_math_block,
            triggers_block=triggers_block, scenario_block=scenario_block,
            state_response_block=state_response_block,
            earnings_fact_block=ctx.earnings_fact_block, preloaded_block=preloaded_block,
            debate_block=debate_block, daily_alerts_block=daily_alerts_block,
            pine_setup_benchmark_block=pine_benchmark_block,
            csv_path=paths["dw_csv"], dw_path=paths["dw_json"],
            options_block=options_block, active_pos_block=active_pos_block, stale=stale,
        )

        independent_dw_str = sanitize_dw_for_independent(dw_dict)
        independent_user_prompt = build_independent_user_prompt(
            ticker=ticker, date_str=date_str, independent_dw_str=independent_dw_str,
            live_quote_block=ctx.live_quote_block, news_dossier=news_dossier,
            fresh_news=ctx.fresh_news, macro_news=macro_news, av_block=ctx.av_block,
            institutional_block=ctx.institutional_block, grounded_block=ctx.grounded_block,
            macro_grounded_block=ctx.macro_grounded_block,
            earnings_fact_block=ctx.earnings_fact_block,
            csv_path=paths["dw_csv"], dw_path=paths["dw_json"],
        )

        # 11. Image paths
        image_paths_model_a = [
            p for p in (str(paths["chart_plain"]), str(paths["chart_zoom"]))
            if Path(p).exists()
        ] or ([str(paths["chart_wide"])] if paths["chart_wide"].exists() else [])
        image_paths_model_b = (
            [str(paths["chart_plain"])] if paths["chart_plain"].exists()
            else ([str(paths["chart_wide"])] if paths["chart_wide"].exists() else [])
        )

        # 12. Pass 2 inference
        try:
            p2 = run_pass2(
                ticker=ticker, date_str=date_str,
                system_prompt=system_prompt, user_prompt=user_prompt,
                system_prompt_independent=system_prompt_independent,
                independent_user_prompt=independent_user_prompt,
                image_paths_model_a=image_paths_model_a,
                image_paths_model_b=image_paths_model_b,
                force_local=force_local,
            )

            reports_dir = config.BASE_DIR / "reports" / date_str
            reports_dir.mkdir(parents=True, exist_ok=True)

            clean_response, _ = save_model_a_report(
                ticker, date_str, p2.response, tdir, out_path, reports_dir, _drift_checker, dw_dict=dw_dict
            )
            clean_ind_response = save_model_b_report(
                ticker, date_str, p2.ind_response, tdir, reports_dir, dw_dict=dw_dict
            )

            run_arbitration(
                ticker=ticker, date_str=date_str,
                clean_response=clean_response, clean_ind_response=clean_ind_response,
                f_parsed=f_parsed, dw_dict=dw_dict,
                live_quote_block=ctx.live_quote_block,
                earnings_fact_block=ctx.earnings_fact_block,
                alerts_list=alerts_list, macro_news=macro_news,
                macro_grounded_block=ctx.macro_grounded_block,
                active_pos_block=active_pos_block,
                tdir=tdir, raw_dir=raw_dir, reports_dir=reports_dir,
            )

        except Exception as e:
            logger.error(f"[{ticker}] Deep Research failed: {e}", exc_info=True)
