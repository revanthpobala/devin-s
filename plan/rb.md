Rules that every fix must respect:
Measured lanes: RR_SETUP, RR_SETUP_STRONG (RR@mkt >= 3), CODE20, OVERSOLD use Pine Long Stop Loss / Long Target exactly. RSI2 uses its own levels (close - 2 ATR / close + 4 ATR, EMA5 recovery exit next open, 10-bar max).
The 1-ATR stop floor and planned R:R >= 2 apply only to judge-invented levels (FLOOR_DEFENSE, BREAKOUT, WATCH_SHADOW).
Lane priors (win %, ev R): strong 25/0.13, RR 30/0.08, CODE20 45/0.08, OVERSOLD 51/0.06, RSI2 61/0.06.
Long RR At Market is NOT exported; derive (Long Target - close)/(close - Long Stop Loss) only when close > stop and target > close. In-zone = Zone RR Flags Pack & 1.
G1. Ledger (src/tracking/suggestions_ledger.py) — do first
Migration (lines 120-146): remove the DEFAULT 'PASS', 'STALK', 'NEW' on gate_status, verdict, kind. After adding columns, one-time: UPDATE suggestions SET gate_status='LEGACY_UNGATED', verdict=NULL, kind=NULL WHERE scorer_version IS NULL OR scorer_version < 2.
Old databases still carry UNIQUE(ticker, date, source, report_hash). Rebuild once: create new table without it, copy rows keeping the highest id per (ticker, date, source), drop old, rename, then CREATE UNIQUE INDEX IF NOT EXISTS ux_sugg ON suggestions(ticker, date, source).
append_suggestion (206-226): use INSERT ... ON CONFLICT(ticker, date, source) DO UPDATE for plan fields only. Never overwrite taken, your_fill, notes. Clear outcome fields (fill_*, exit_*, bars_held, gross_r, r_net, mae_r, scored_at, scorer_version) when levels change.
Lines 165-184: missing levels stored as NULL, not 0.0. No default verdict or gate; caller must pass them.
Remove unused validate_levels import (line 18).
backfill_legacy.py:74: works once the unique index exists (item 2). Lines 61-63, 92: stop writing the your_fill midpoint.
G2. Scorer and validator
src/tracking/execution_validator.py:
Lines 496-505: delete the T2 branch. T1 closes the trade; exit at T1, or at the open if the open gapped above T1.
Lines 282-322: enforce the fill window inside the walk. If 5 bars after the signal pass unfilled, stop and return NOT_FILLED. Remove the post-walk len(bar_records) >= 5 check (539).
Lines 381-388: open below stop before fill = status GAP_STOP, no fill. Open below stop after fill = exit at open, status STOP_BREACHED, R counted.
Lines 252-261: warm EMA5 with at least 20 bars before the signal (fetch setup_date - 40 calendar days, walk from the signal).
Lines 94-103: CSV path also drops bars after _get_last_closed_session_date(). Lines 54-55: use America/New_York date.
src/tracking/suggestion_scorer.py: 6. Before scoring any row, clear all outcome fields; never return a stale v1 row unchanged (84-86, 133-135, 177-186). 7. Store the validator's real status (INVALIDATED, MISSED_RUNAWAY, GAP_STOP, NOT_FILLED), not blanket NOT_FILLED (116-135). Count bars after the signal bar only. 8. Line 148: GAP_STOP has no R (never filled). A filled trade that gaps through the stop gets real R at the open, not INVALID_GEOMETRY. 9. Line 90: RSI2 detection by setup_lane == 'RSI2' only; pass opening_ceiling = min(stop + 2*ATR, stop/(1-0.05), target). 10. _compute_stats (332): gate_status='PASS' AND scorer_version=2 AND kind='NEW' AND verdict IN ('ENTER','STALK') AND r_net IS NOT NULL.
src/tracking/suggested_trades_auditor.py: 11. Line 314: no R when risk <= 0. Lines 316-318: unfilled INVALIDATED / STOP_BREACHED = no R. Stop writing R into dollar_pnl / roc_pct (342); add an r_multiple column.
G3. Level gate and triage
src/logic/level_validation.py:
Lines 229-233: skip the planned R:R floor for RR_SETUP*, CODE20, OVERSOLD, RSI2.
Measured lanes: require abs(stop - Long Stop Loss) <= 0.05 and abs(T1 - Long Target) <= 0.05; skip the ATR floor for them.
Line 294 _pine_drift: skip for RSI2.
Lines 277-291: earnings counted from date_str, fail if inside 21 trading bars.
Line 101: cast plan values with float(); on bad values return a gate failure, never raise.
src/data/csv_adapter.py: 6. Lines 209-221: RR@mkt from Long Stop Loss and Long Target only; no RSI2 / T1 waypoint / generic fallbacks.
src/logic/data_window_filter.py: 7. Lines 421-440: only swap in RSI2 Fixed Stop/Target when the lane is RSI2; keep Pine levels in long_stop_loss / long_target otherwise. 8. Line ~565: do not recompute rr when the injected RR@mkt was deliberately absent. 9. _self_test: update fixtures (RR 3-5 is now _strong; EV guard expects lane prior semantics).
src/logic/process_survivor.py: 10. Line 450, 1360: TIER_A_MIN_EV_R 0.5 is above every lane prior; set 0.05 or remove. 11. Lines 475-496: rule-source WATCH rows get setup_lane='WATCH_SHADOW', gate_status from the real gate, not PASS.

G4. Arbitration and pipeline
src/logic/deep_research/arbitration.py:
Line 334: persist suggestion_id only if sugg_id > 0.
Line 372: JSON parse error appends the NO_LEVELS marker and writes rejected_plans. Regex also accepts

```json watch_levels 

```` (space).
3. `append_suggestion` call (271-289): pass `verdict`, `gate_status`, `setup_lane`, `kind`, `atr_at_signal`, `spot_at_signal` (DW close), `rr_at_market_at_signal`, `lane_prior_win`, `lane_prior_ev`. Do not overwrite the model's `setup_lane` with None (279).
4. Ground truth: add triage reason and lane priors (195). Lines 177-178: if `Action Long Code` is missing decode `pack % 32`, never print the raw pack.
5. Lines 347-357 (`last_researched` write): record in-zone from `Zone RR Flags Pack & 1`, and Pine `Long Stop Loss` / `Long Target`, RR@mkt, action code — the same fields `pipeline.py` compares.

`src/logic/deep_research/pipeline.py`:
6. Line 148: handle `position_state.list_open()` returning a dict (see G4.8).
7. Lines 187-197: compare the same fields as G4.5; add a `force` parameter that skips dedupe.
8. `src/tracking/position_state.py`: delete the duplicate `list_open` (line 75 or 430) so one definition remains; update callers to its return type.
9. Explicit `--ticker`: read the triage record; non-held CUT stops, WATCH runs as `WATCH_SHADOW`, held runs as `MANAGE`.

`run_watch_alerts.py`:
10. Line 228: `suggestion_id` lookup adds `AND source='judge'`.
11. Before extraction, if `_arbitration.md` contains `NO_LEVELS` or `LEVEL GATE REJECTED`, skip; never fall through to `_summary.md` (`report_level_extractor.py:139`).
12. Lines 181-185: no Data Window found = skip upsert (gate cannot run fully).

## G5. Watchlist and watch_targets

1. `watch_manager.py` upsert: reset `is_active=1, user_taken=0` only when `date` or `suggestion_id` changes; otherwise keep them.
2. `watchlist.py:243-244`: target hit with no risk = None, excluded from `won_r`. Lines 246-252: unfilled STOP_BREACHED / STOPPED = no R. Lines 256-258: options IN_TRADE unscored.
3. `trades.py:98-99`: join on `suggestion_id` only; legacy rows without it show unjoined, not duplicated. Line 176: missing gate shows `UNKNOWN`, not PASS. Line 188: R:R @ Mkt from `rr_at_market_at_signal` only, never `rr_ratio`.

## G6. UI

1. `watchlist.js` 438/446/454, 806/822, 909/913: show R sums, not $. Lines 409-410: unfilled INVALIDATED not counted as Stopped.
2. Stop-in-ATR column reads `atr_at_signal` (filled by G4.3).
3. `trade_ideas_generator.py:22`: no `spot or 100.0` fallback; missing entry = row hidden.

## G7. Remaining

1. Report B leak: `pipeline.py:520` exception path must not fall back to the full Pine CSV; `llm_client.py:780-803` sandbox loads the sanitized CSV for Model B.
2. Options (P7, not started): live chain mid at suggestion time (Schwab, Tasty, Alpaca) into `opt_entry_*`; unpriced not scored; daily mark; option R = P&L / max loss. `strike_validator.py`: missing ExpMove fails instead of passing silently.

## G8. Tests and re-score

1. Add tests: ledger migration on an old-schema DB (duplicates + old UNIQUE) and fresh DB; walk cases (gap below stop pre-fill, T1+T2 same bar, fill on bar 6 = NOT_FILLED, stop-first both-touch, 21-bar time exit, RSI2 recovery with warm EMA5); level gate per lane (AMZN 2026-08-18 RR_SETUP pass with Pine levels, AMZN 2026-02-06 OVERSOLD pass, RSI2 2 ATR / 4 ATR pass).
2. Re-scrape one ticker with the current Pine, triage, deep research end to end; check the ledger row has lane, gate, ATR, spot, RR@mkt, priors.
3. Bump `scorer_version` to 2, re-score all rows; main record = `get_main_record_stats` (`source=judge`, gate PASS, kind NEW, the five lanes, date >= 2026-09-23).
4. Verify against the hand check of 41: 6 T1 / 16 stops; no T1 on HOOD, AMD, NVDA; no fills on WDAY, AAPL, CDW, MSFT.
