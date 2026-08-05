import concurrent.futures
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from src import config
from src.logic.process_survivor import generate_thesis_task, prefilter_ticker

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (LocalResearch) %(message)s"
)


def _move_ticker_artifacts(src_dir: Path, target_dir: Path, ticker: str):
    """Move a ticker's complete artifact set from src_dir into target_dir so the
    triage folder is self-contained: chart png, data window json, news dossier,
    and both thesis files (md + json)."""
    safe = ticker.replace(":", "_")
    for fname in (
        f"{safe}_chart.png",
        f"{safe}_datawindow.json",
        f"{safe}_news_research.md",
        f"{safe}_thesis.json",
    ):
        src = src_dir / fname
        if src.exists():
            shutil.move(str(src), str(target_dir / fname))


def _consolidate_ledger(out_dir: Path):
    """Rebuild data/{date}/consolidate/consolidated_results.json from every
    per-ticker _thesis.json on disk. Each _thesis.json is written by a single
    worker (no race), so this is the authoritative, complete decision ledger."""
    ledger_dir = out_dir / "consolidate"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "consolidated_results.json"

    consolidated = {}
    # Source _thesis.json files live in out_dir, but deep-research-flagged ones
    # are MOVED into data/triage/<date>/_DEEP_RESEARCH by Phase 2E. Glob both
    # locations so a rerun does not drop those decisions from the persisted
    # ledger. Without this, an out_dir-only glob misses moved files and rebuilds
    # an incomplete ledger on every rerun.
    search_dirs = [out_dir]
    triage_dir = config.BASE_DIR / "data" / "triage" / out_dir.name
    for sub in ("_DEEP_RESEARCH", "force"):
        d = triage_dir / sub
        if d.exists():
            search_dirs.append(d)
    for base in search_dirs:
        for p in base.glob("*_thesis.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            ticker = rec.get("ticker")
            if ticker:
                # Later glob wins on collision; out_dir copy (freshest from this
                # run) is searched first, so it takes precedence.
                consolidated.setdefault(ticker, rec)

    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(consolidated, f, indent=2)
    logger.info(f"Consolidated {len(consolidated)} ticker decisions -> {ledger_path}")


def _load_survivors(out_dir: Path, target_ticker: str | None = None):
    manifest = out_dir / "survivors.json"
    if not manifest.exists():
        logger.error(
            f"No survivors.json at {manifest}. Run run_swing_research.py (scrape phase) first."
        )
        return []
    with open(manifest, "r", encoding="utf-8") as f:
        survivors = json.load(f)
    if target_ticker:
        survivors = [
            s
            for s in survivors
            if (s.get("ticker") or s.get("Ticker") or s.get("Symbol") or "").upper()
            == target_ticker.upper()
        ]
    return survivors


def run_local_research(
    target_date: str | None = None,
    target_ticker: str | None = None,
    force_tickers: set | None = None,
    regenerate: bool = False,
):
    logger.info("=" * 60)
    logger.info("STARTING LOCAL-LLM RESEARCH PHASE (News + Triage, FREE)")
    logger.info("=" * 60)

    force_tickers = {t.upper() for t in (force_tickers or set())}
    tgt = target_ticker.upper() if target_ticker else None
    today_str = target_date or datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "raw" / today_str
    if not out_dir.exists():
        logger.error(f"Raw dir missing: {out_dir}. Run the scrape phase first.")
        return

    survivors = _load_survivors(out_dir, target_ticker)
    if not survivors:
        logger.info("No survivors to research. Done.")
        return

    logger.info(f"Local-LLM research for {len(survivors)} survivor(s) on {today_str}")

    # Phase 2C (PASS 1 — deterministic prefilter, CHEAP, no local-LLM call):
    # rank every survivor by reproducible rank_score so we only spend the
    # expensive Qwen enrichment on the top-N (proposal A). This saves ~100
    # local-LLM calls/run — Qwen notes for non-top-N WATCH names were never read.
    num_workers = min(10, len(survivors))
    logger.info(f"\n--- PHASE 2C-1: DETERMINISTIC PREFILTER ({num_workers} workers, no LLM) ---")

    prefilter_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        for index, survivor in enumerate(survivors):
            worker_id = (index % num_workers) + 1
            prefilter_futures.append(
                executor.submit(
                    prefilter_ticker, survivor, out_dir, today_str, worker_id, regenerate
                )
            )

        prefilter_results = []
        for future in concurrent.futures.as_completed(prefilter_futures):
            try:
                res = future.result()
                if res:
                    prefilter_results.append(res)
            except Exception as e:
                logger.error(f"Prefilter worker thread failed: {e}")
    # Globally rank and select the top-N for enrichment via the SHARED
    # deep_research_sort_key — the exact same ordering the paid cap uses in
    # deep_research.py, so the two stages can never rank by different criteria.
    # Eligibility is QUALITY-driven (det-PASS OR high-conviction WATCH via the
    # quality_pass flag), so we select from that subset rather than only literal
    # in-zone PASS names — otherwise the paid pass starves on days when few
    # tickers are physically in their entry zone. Force-requested always enrich.
    from src.logic.data_window_filter import deep_research_sort_key

    enrich_n = config.ENRICH_TOP_N
    force_set = {t.upper() for t in force_tickers}
    send_results = [
        r for r in prefilter_results if not r.get("enriched") and r.get("send_for_deep_research")
    ]
    # Rank on the deterministic triage record (carries ev_r/rr/in_zone/regime/
    # dir_prob/conviction + news_negative). Contradiction isn't known pre-LLM, so
    # it's simply absent here; the paid cap re-ranks once it is.
    ranked = sorted(
        send_results,
        key=lambda r: deep_research_sort_key(r.get("triage") or {}),
        reverse=True,
    )
    selected = ranked[:enrich_n] if enrich_n > 0 else ranked
    enrich_targets = {r["ticker"].upper() for r in selected} | force_set
    logger.info(
        f"PHASE 2C-1 done: {len(prefilter_results)} prefiltered, "
        f"{len(send_results)} send-eligible, enriching {len(selected)} "
        f"+ {len(force_set)} forced "
        f"(ENRICH_TOP_N={enrich_n or 'uncapped'})."
    )

    # Phase 2C (PASS 2 — Qwen enrichment, top-N or uncapped):
    # only selected tickers hit local LLM + Finnhub news + yfinance earnings.
    # Quota-bound clients (Alpha Vantage, Adanos) run on the paid pass in deep_research.py.
    # The rest keep their deterministic-only _thesis.json from pass 1.
    # The enrichment pool is capped at LLM_LOCAL_CONCURRENCY (= the server's
    # --parallel) so we never oversubscribe the local GPU server.
    llm_workers = min(config.LLM_LOCAL_CONCURRENCY, len(enrich_targets)) or 1
    logger.info(
        f"\n--- PHASE 2C-2: QWEN ENRICHMENT ({len(enrich_targets)} tickers, {llm_workers} LLM workers) ---"
    )
    thesis_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=llm_workers) as executor:
        for index, survivor in enumerate(survivors):
            tk = (
                (survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", ""))
                .strip()
                .upper()
                .replace(":", "_")
            )
            if tk not in enrich_targets:
                continue
            worker_id = (index % llm_workers) + 1
            thesis_futures.append(
                executor.submit(
                    generate_thesis_task, survivor, out_dir, today_str, worker_id, regenerate, True
                )
            )

        batch_updates = []
        for future in concurrent.futures.as_completed(thesis_futures):
            try:
                res = future.result()
                if res:
                    batch_updates.append(res)
            except Exception as e:
                logger.error(f"Thesis worker thread failed: {e}")

    # Rebuild the staging ledger from the authoritative per-ticker _thesis.json
    # files. The parallel workers' per-ticker ledger writes can race (lost
    # updates), but each _thesis.json is written by exactly one worker, so a
    # final sweep from disk guarantees a complete, correct consolidated file.
    _consolidate_ledger(out_dir)

    # Consolidate updates from this run (batch_updates) with any decisions
    # already staged in research_ledger.json (supports reruns / partial runs).
    # The ledger is the authoritative pre-Sheets staging file. Keyed by
    # row_index, falling back to Trade ID when a row_index isn't available
    # (e.g. a single-ticker run that resolved the row only by Trade ID).
    # Only push to Sheets rows that have a sheet _row_index. generate_thesis_task
    # now returns a record for EVERY ticker (so deep-research segregation works
    # even without a row), but the Sheets batch must stay limited to tickers that
    # actually map to a sheet row. Tickers without _row_index are segregated for
    # deep research but never pushed to Sheets.
    def _ukey(u):
        return u.get("row_index") or f"tid:{u.get('trade_id')}"

    updates_by_row = {_ukey(u): u for u in batch_updates if u.get("row_index") and _ukey(u)}
    ledger_path = out_dir / "consolidate" / "consolidated_results.json"
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            for rec in ledger.values():
                key = _ukey(rec)
                if key and key not in updates_by_row:
                    updates_by_row[key] = rec
        except Exception as e:
            logger.error(f"Failed to read research ledger for Sheets update: {e}")
    all_updates = list(updates_by_row.values())

    # A targeted --ticker run must only (re)write that ticker's sheet row. Never
    # replay the entire consolidated ledger (which holds every ticker from prior
    # runs) back onto the sheet, or a single-ticker re-run would clobber all rows.
    if target_ticker:
        tgt = target_ticker.upper()
        all_updates = [u for u in all_updates if (u.get("ticker") or "").upper() == tgt]

    if all_updates:
        logger.info(f"\n--- PHASE 2D: BATCH UPDATING SHEETS ({len(all_updates)} rows) ---")
        try:
            from src.tracking.sheets_tracker import SheetsTracker

            tracker = SheetsTracker()
            tracker.batch_update_swing_research(today_str, all_updates)
        except Exception as e:
            logger.error(f"Batch Sheets update failed: {e}")

    # Phase 2E: Segregate tickers routed to DEEP RESEARCH.
    # The deep-research folder is the single triage output: tickers whose local
    # triage set send_for_deep_research == True are MOVED (chart + data window +
    # news dossier + thesis) into data/triage/<date>/_DEEP_RESEARCH so the paid
    # Minimax pass has a self-contained input. Tickers passed via --force are
    # MOVED into data/triage/<date>/force (manual override of the LLM gate).
    # PASS/WATCH/CUT tickers that are neither flagged nor forced are NOT copied
    # anywhere — they remain in data/raw alongside their artifacts.
    if target_ticker:
        logger.info(f"\n--- PHASE 2E: SEGREGATING 'FORCE' TICKER ({target_ticker}) ---")
        tgt = target_ticker.upper()
    else:
        logger.info("\n--- PHASE 2E: SEGREGATING 'DEEP_RESEARCH' TICKERS ---")

    triage_dir = config.BASE_DIR / "data" / "triage" / today_str
    deep_dir = triage_dir / "_DEEP_RESEARCH"
    force_dir = triage_dir / "force"

    # Read every ticker's decision from disk (the consolidated ledger), NOT just
    # the enriched batch — the deterministic-only pass-1 tickers also carry a
    # send_for_deep_research verdict and must be segregated too.
    all_thesis = {}
    ledger_path = out_dir / "consolidate" / "consolidated_results.json"
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                all_thesis = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read consolidated ledger for segregation: {e}")

    # Determine the full set of tickers that SHOULD be in each folder this run,
    # then drop any stale artifacts left over from a previous --regenerate run so
    # the folder never shows names the current run did not flag.
    # The deep-research folder is CAPPED at DEEP_RESEARCH_CAP (default 8): only
    # the top-N candidates by deep_research_sort_key are kept. This matches the
    # cap the PAID pass (deep_research.py) applies before calling Minimax, so the
    # _DEEP_RESEARCH folder holds exactly the tickers that will actually be
    # processed — not the full (often 50-75) broad gate set. The gate's
    # send_for_deep_research flag is the ELIGIBILITY filter; the cap is the
    # PRIORITISATION filter and must run here too, or the folder balloons.
    from src.logic.data_window_filter import rank_pass_tickers

    cap = int(os.getenv("DEEP_RESEARCH_CAP", "8"))
    deep_candidates, force_candidates = [], []
    for rec in all_thesis.values():
        ticker = rec.get("ticker")
        if not ticker:
            continue
        if tgt and ticker.upper() != tgt:
            continue
        llm_data = rec.get("llm_data", {})
        is_forced = ticker.upper() in force_tickers
        if is_forced:
            force_candidates.append(ticker.upper())
        elif llm_data.get("send_for_deep_research") is True and llm_data.get("enriched") is True:
            # Rank on the FLATTENED triage dict (carries ev_r/regime/in_zone at
            # top level), NOT the whole consolidated record — otherwise those
            # fields read as None, every candidate ties, and the stable sort
            # silently falls back to alphabetical order. This matches the record
            # shape _load_triage_record builds for the paid pass, so segregation
            # and the paid cap rank identically.
            tr = dict(rec.get("triage") or {})
            tr["ticker"] = ticker.upper()
            deep_candidates.append(tr)
    ranked_deep = rank_pass_tickers(deep_candidates)
    keep_deep = {str(r.get("ticker", "")).upper() for r in ranked_deep[:cap]}
    keep_force = set(force_candidates)
    if len(deep_candidates) > cap:
        logger.info(
            f"Deep-research cap {cap}: {len(deep_candidates)} flagged -> "
            f"{len(keep_deep)} kept ({sorted(keep_deep)}); "
            f"{len(deep_candidates) - cap} deferred."
        )

    for stale_dir, keep in ((deep_dir, keep_deep), (force_dir, keep_force)):
        if not stale_dir.exists():
            continue
        # If this run flagged nothing for a folder, DO NOT wipe it — preserve the
        # prior run's artifacts (charts, theses) instead of nuking them. Only
        # prune when we actually have a non-empty set to keep.
        if not keep:
            logger.info(f"No keepers for {stale_dir.name}; preserving existing artifacts.")
            continue
        for old in stale_dir.glob("*"):
            # A file belongs to a kept ticker if its name starts with that ticker
            # followed by '_' or '.' (e.g. AAPL_chart.png, AAPL_thesis.json,
            # AAPL.png). Anything else is a leftover from a previous run.
            name = old.name.upper()
            is_stale = True
            for tk in keep:
                if name.startswith(tk + "_") or name.startswith(tk + "."):
                    is_stale = False
                    break
            if is_stale:
                try:
                    if old.is_file() or old.is_symlink():
                        old.unlink()
                    elif old.is_dir():
                        import shutil

                        shutil.rmtree(old)
                except Exception as e:
                    logger.warning(f"Failed to remove stale artifact {old}: {e}")

    segregated_count = 0
    for rec in all_thesis.values():
        ticker = rec.get("ticker")
        if not ticker:
            continue
        if tgt and ticker.upper() != tgt:
            continue
        is_forced = ticker.upper() in force_tickers
        # Only move tickers that survived the cap (keep_deep / keep_force).
        # send_for_deep_research==True alone is ELIGIBILITY, not selection; the
        # capped keep-sets above are what actually get deep-researched.
        if is_forced:
            if ticker.upper() not in keep_force:
                continue
        else:
            if ticker.upper() not in keep_deep:
                continue

        dest_dir = force_dir if is_forced else deep_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Move the ticker's full artifact set into the destination folder so it
        # is self-contained. Everything else stays in data/raw.
        _move_ticker_artifacts(out_dir, dest_dir, ticker)

        segregated_count += 1

    if segregated_count:
        logger.info(
            f"Segregation complete. {segregated_count} tickers' artifacts moved to data/triage (_DEEP_RESEARCH / force)."
        )
    else:
        logger.info("No tickers flagged for deep research — nothing segregated.")

    logger.info("=" * 60)
    logger.info("LOCAL-LLM RESEARCH PHASE COMPLETE.")
    logger.info("Deep research (paid Minimax) runs only on tickers flagged send_for_deep_research.")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run local-LLM news + triage research (free)")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD). If a non-date token is given, "
        "it is treated as --ticker using today's date.",
    )
    parser.add_argument("--ticker", type=str, help="Run only on a specific ticker")
    parser.add_argument(
        "--force",
        type=str,
        default="",
        help="Comma-separated tickers to force into deep research "
        "regardless of the local-LLM send_for_deep_research gate.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Ignore the _thesis.json cache guard and regenerate every "
        "ticker's thesis (re-runs the local-LLM triage + applies the "
        "current send_for_deep_research gate + social sentiment). "
        "Use after changing gate logic without deleting files.",
    )

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker

    # Smart positional: a token that doesn't look like YYYY-MM-DD is a ticker.
    import re

    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    force_tickers = {t.strip().upper() for t in args.force.split(",") if t.strip()}
    run_local_research(target_date, target_ticker, force_tickers, regenerate=args.regenerate)
