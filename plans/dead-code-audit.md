# Dead Code Audit & Removal Plan

**Scope:** Over-engineering / dead code only. Correctness, security, and performance are out of scope.
**Method:** AST + token cross-reference scan over all tracked project Python (excluded `.venv`, `scratch/`, `.kilo/worktrees/`, data/report artifacts). Each finding below was manually verified — the flagged symbol is genuinely unreferenced by any live code path.

> ⚠️ Per AGENTS.md: **no file is deleted without explicit confirmation.** This plan lists candidates; nothing is removed until you approve each batch.

---

## Summary of Findings (ranked biggest cut first)

| # | Tag | What to cut | Lines | Replacement |
|---|-----|-------------|------:|-------------|
| 1 | delete | Weekly-direction standalone tool (4 files + test) | ~2,300 | nothing — self-contained CLI not in AGENTS.md, no callers |
| 2 | delete | `src/streaming/schwab_streamer.py` | 263 | nothing — only reachable via a dead branch in `quote_router` |
| 3 | delete | Junk root files: `=`, `run_ui_monolith.py.bak`, `scratch_response.txt` | ~540 KB | gitignored / deleted |
| 4 | delete | `src/clients/adanos_client.py` | (whole file) | nothing — never imported anywhere |
| 5 | delete | `src/clients/qwen_client.py` | (whole file) | nothing — `query_qwen` has zero callers |
| 6 | yagni | `RANK_MODEL_ENABLED` flag + `watch_ranker.py` LightGBM path | ~120 | remove flag; keep deterministic ordering |
| 7 | native | `scratch/` + `.kilo/worktrees/` (untracked) | — | untrack / gitignore |

---

## Detailed Findings

### 1. Weekly-direction standalone tool — `delete: ~2,300 lines`
A complete self-contained CLI feature (indicator engine + backtester + HTML report generator) that is **not documented in AGENTS.md** and has zero integration into the main pipeline. Nothing imports it except its own test file.

- `run_weekly_direction.py` (~218L) — CLI entry (`--once`, `--backtest`, `--stream`)
- `src/indicators/weekly_direction_engine.py` (1,018L) — `WeeklyDirectionEngine`, `cluster_zone`, `EngineState`, `_calc_dmi`, primitives
- `src/indicators/weekly_direction_backtester.py` (612L) — `WeeklyDirectionBacktester`, `BacktestSummary`, `BacktestTrade`
- `src/indicators/__init__.py` — re-exports the engine
- `tests/test_weekly_direction.py` (~251L)

**Decision needed:** This is a *coherent feature*, not scattered dead code. If you still use it ad-hoc, move it to a separate branch/repo instead of deleting. If it's abandoned, delete all 5 files.

> Note: `src/streaming/tripwire_streamer.py` (263L) is imported by both `run_weekly_direction.py` **and** `tests/test_weekly_direction.py`. If the weekly tool goes, verify `tripwire_streamer` has no other live consumer before removing it.

---

### 2. `src/streaming/schwab_streamer.py` — `delete: 263 lines`
`SchwabStreamer` (a WebSocket quote daemon) is only ever referenced from **one lazy import** at `src/clients/quote_router.py:203`:

```python
from src.streaming.schwab_streamer import schwab_streamer
ws_tick = schwab_streamer.get_latest_quote(clean_sym)
```

That branch is a best-effort enrichment that's guarded and non-critical. The module is otherwise orphaned (only `src/streaming/__init__.py` re-exports it, and nothing imports the package for it). Removing the file + the lazy-import branch in `quote_router.py` + the `__init__` export kills ~263 lines with no behavior loss to the core pipeline.

---

### 3. Junk root files — `delete: ~540 KB of dead weight`
- `=` (0 bytes) — accidental redirect artifact from a mis-typed shell command
- `run_ui_monolith.py.bak` (278,484 bytes / **271 KB**) — backup of the old monolithic UI, superseded by `run_ui.py` + `src/ui/`
- `scratch_response.txt` (5,387 bytes) — leftover scratch output

All three are git-tracked but serve no purpose. Delete and add to `.gitignore` where appropriate.

---

### 4. `src/clients/adanos_client.py` — `delete: whole file`
Adanos social-sentiment client. **Zero importers.** Only referenced by:
- Its own module
- `src/config.py:112-114` (`ADANOS_API_KEY`, `ADANOS_BASE_URL`)

Remove the file + the two config vars. (AGENTS.md lists it, but that's a doc reference, not code usage.)

---

### 5. `src/clients/qwen_client.py` — `delete: whole file`
`query_qwen()` (DashScope cloud Qwen wrapper) has **zero callers**. The LLM path goes through `src/clients/llm_client.py` → Meta AI / OpenRouter / local llama-server, never through this. AGENTS.md documents it as a capability, but no code invokes it.

Remove the file. (Confirm you don't intend to wire DashScope in later.)

---

### 6. `RANK_MODEL_ENABLED` + `watch_ranker.py` — `yagni: ~120 lines`
`src/logic/watch_ranker.py` trains a LightGBM model to re-order WATCH candidates behind a feature flag that **defaults ON** (`ENABLED = ... not in ("0","false","no")`). Per AGENTS.md it "never affects PASS/WATCH/CUT" — i.e. it only re-orders a subset for paid-slot allocation.

This is speculative ML flexibility:
- Requires `data/models/watch_ranker.txt` + a training corpus (`full_v2`) that must be checked out to regenerate.
- Adds a model-load dependency to the triage hot path.

**If you don't actively tune the ranker:** remove the flag and the LightGBM ordering, fall back to the deterministic `deep_research_sort_key` order. Keep it only if you've measured a real win from learned ordering.

---

### 7. Untracked noise — `native: untrack / gitignore`
Not tracked by git, but inflates any "repo size" perception and pollutes scans:
- `scratch/` — ~30 one-off experiment scripts (`test_*.py`, `capture_watchlist.py`, etc.)
- `.kilo/worktrees/` — Agent Manager worktree copies (duplicates of `scripts/`, `src/`)

Add both to `.gitignore`. Don't delete without confirming you want to keep the scratch experiments around.

---

## Verification-confirmed NOT dead (do not touch)
These looked suspicious but are live:
- `run_watch_alerts.py` → uses `quote_router` (`run_watch_alerts.py:96`)
- `src/clients/price_client.py` → thin wrapper over `quote_router`, used throughout
- `src/clients/options_client.py` → lazy-imports `quote_router` (`options_client.py:575`)
- `CircuitBreaker` (quote_router) → instantiated 4× in `QuoteRouter.__init__`
- `EngineState` / `_calc_dmi` → used inside `weekly_direction_engine.py` (live only if you keep #1)
- All `src/ui/routes/*.py` → registered via `app.include_router(...)` in `src/ui/app.py:67-76`
- `get_raw_job_log` (`status.py:399`) → live FastAPI route `/api/logs/raw/{job_id}`
- `save_scan_funnel`, `_screener_payload_verdict` → called within their own modules' main flows

---

## Suggested execution order (each batch = one confirmable commit)

1. **Batch A — zero-risk junk** (no code path affected): delete `=`, `run_ui_monolith.py.bak`, `scratch_response.txt`; add `scratch/` + `.kilo/worktrees/` to `.gitignore`. Run `pytest` to confirm green.
2. **Batch B — orphaned clients**: delete `adanos_client.py` + its 2 config vars; delete `qwen_client.py`. Grep-confirm no new import errors; run `pytest`.
3. **Batch C — schwab_streamer**: remove file + `quote_router.py:203-205` lazy branch + `src/streaming/__init__.py` export. Run `pytest -k quote`.
4. **Batch D — weekly-direction tool** *(needs your call)*: delete the 5 files in finding #1 (and re-check `tripwire_streamer`). Or relocate to a branch.
5. **Batch E — ranker flag** *(needs your call)*: strip `RANK_MODEL_ENABLED` + LightGBM path if unused.

After each batch: `pytest` must stay green. No batch is destructive to live behavior; B/C/D/E are pure dead-code removal.

---

## Net impact (if all approved)
- **~2,700+ lines** of Python removed
- **3 orphaned client modules** + 1 streaming module gone
- **~540 KB** of junk files removed
- **1 speculative ML dependency** (LightGBM ranker) optionally dropped
- `pytest` green throughout; no live behavior change
