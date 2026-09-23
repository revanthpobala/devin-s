---
name: Suggestion ledger and pipeline fix plan
overview: \"Make the research-to-suggestion pipeline produce an honest, per-source, immutable track record: validate LLM levels before they become suggestions, fix watch-status and auditor math, add a rule-only baseline, and fix forecast scoring. Based on live UI/DB data (39 watch targets, 58 audit rows, 403 forecasts, 40 jobs) and a full code trace.\"
todos:
  - id: p0-actionable
    content: \"Phase 0: actionable intraday alerts - shadow-score every veto, drop weekly-stage veto for 0DTE, grade gate by grade, mute NEUTRAL Daily emails, one push per non-vetoed ENTRY with full plan\"
    status: pending
  - id: p1-ledger
    content: \"Phase 1: append-only suggestions table + honest 21-bar R scorer with fees; backfill legacy audit rows\"
    status: pending
  - id: p2-gate
    content: \"Phase 2: validate_levels gate on all write paths (ordering, R:R floor, stop >= 1 ATR, Pine drift, strike_validator); feed Pine levels to judge; remove hardcoded rr/expiry\"
    status: pending
  - id: p3-watch
    content: \"Phase 3: fix watch engine (poll TESTING_SUPPORT, close-based checks, strict IN_ZONE, 5-bar expiry, history key, single writer)\"
    status: pending
  - id: p4-baseline
    content: \"Phase 4: log rule-only triage + each report source to ledger; taken/fill fields; per-source UI table\"
    status: pending
  - id: p5-forecast
    content: \"Phase 5: structured forecast events, high/low touch resolution, trading-day horizon, fix CSV fallback bug\"
    status: pending
  - id: p6-gates
    content: \"Phase 6: triage gate + screener scoring fixes (EV/buy-score gate, code-20 R:R, RSI2 order, R:R-based screener rank)\"
    status: pending
  - id: p7-reliability
    content: \"Phase 7: detach research jobs from UI server; subprocess timeouts\"
    status: pending
isProject: false
---

# Suggestion Ledger and Pipeline Fix Plan

## What the data shows

- **`watch_targets`: 39 rows, all LONG.** Status counts: 16 STOP_BREACHED, 10 TARGET_HIT, 5 IN_TRADE, 3 MISSED_RUNAWAY, 2 STALKING, 2 IN_ZONE, 1 INVALIDATED.
  - Planned R:R measured from the zone midpoint has a median of **2.88**. Only 1 row has its target at or below the entry.
  - The **0.29 R:R** I quoted before came from the auditor, which uses `breakout_level` as the entry. It was a bug in the audit, not in the research.
- **Stops are tight.** The median stop sits **2.45%** below the zone midpoint, and a quarter sit under 1.24%. Stopped-out trades had a 2.2% median stop versus 3.06% for target hits. Three stops sit inside the entry zone itself: AMZN 09-22 has a zone of 246–250.5 with the stop at 248.5.
- **The audit headline is not trustworthy.** The UI shows +$7,224 and profit factor 2.21, but 32 of the 34 resolved rows are options with modeled P/L. Recomputed in R on the stock price, the mean is −0.05R.
- **Forecasts are coin-flip level.** A coin flip scores Brier 0.25. Model A (Pine) scores 0.248 over 25 resolved forecasts; Model B (Independent) scores 0.283 over 42.
- **Jobs mostly fail.** Of 40 jobs, 16 failed (mostly \"Server restarted while job was running\") and 15 were killed; only 9 completed.

## Current flow and where it breaks

```mermaid
flowchart LR
  DW[PineDataWindow] --> Triage[data_window_filter]
  Triage -->|\"verdict + Pine levels, files only\"| Files[thesis_json_and_jsonl]
  DW --> ModelA[Report_A_summary]
  DW -->|\"Pine fields stripped\"| ModelB[Report_B_independent]
  ModelA --> Judge[arbitration]
  ModelB --> Judge
  Judge -->|\"raw LLM json, no validation\"| WT[watch_targets_PK_ticker]
  Extractor[run_watch_alerts_sync] -->|\"regex fallback, overwrite\"| WT
  WT --> Audit[suggested_trades_audit]
  WT --> Poll[live_quote_poll_60s]
  ModelA --> SF[superforecasting_keyword_parse]
  ModelB --> SF
```

Main defects, found in the code trace:

- **Levels are written by the LLM and never checked.** `arbitration.py:200-220` upserts the judge's raw JSON straight into the table. The judge's ground-truth block (`arbitration.py:139-153`) contains no Pine zone, stop or target.
- **Each report overwrites the last.** `watch_targets` uses `ticker` as its primary key (`watch_manager.py:41`), so there is no history. Two write paths also race: the arbitration upsert and the `run_watch_alerts.py:70` sync.
- **The auditor mis-prices trades** (`suggested_trades_auditor.py`):
  - Entry uses `breakout_level` first (`:66-74`).
  - `entry_type` and breakout are not passed to the validator (`:263-275`).
  - The walk includes the setup-date bar.
  - No horizon, no fees, no R. Options P/L comes from the LLM's `max_profit`/`max_loss`, and anything with \"PUT\" in its name is treated as a credit spread (`:306`).
- **Watch-status transitions are wrong** (`run_watch_alerts.py`):
  - TESTING_SUPPORT is never polled again, because `watch_manager.py:264` doesn't include it.
  - Invalidations meant to trigger on a daily close fire intraday, and the tolerance band is asymmetric (`:162`, `:168`).
  - A breakout triggers without a daily close.
  - IN_ZONE covers everything from the stop up to 1% above the zone top.
  - There is no expiry, even though the gem specifies 5 bars.
- **There is no baseline.** Rule-only verdicts live only in `*_thesis.json` and `data/logs/data_window_scrapes.jsonl`, and nothing records whether you took a trade.
- **Forecast scoring is keyword-based** (`superforecasting_auditor.py:57-80`). The word \"test\" matches inside \"latest\". Outcomes use daily closes only, horizons are in calendar days, the \"before stop\" condition is ignored, and an empty date mask returns the whole CSV (`:101`). Model B is asked about Pine zone events, but its Pine fields are stripped.
- **LLM-invented fallback values.** The regex fallback hardcodes `rr_ratio=2.0` (`report_level_extractor.py:607`) and literal option expirations (`:430`, `:439`).

## Target design

```mermaid
flowchart LR
  Triage[data_window_filter] -->|\"source=rule\"| Gate
  ModelA -->|\"source=model_a\"| Gate
  ModelB -->|\"source=model_b\"| Gate
  Judge -->|\"source=judge\"| Gate[validate_levels]
  Gate -->|\"append-only, frozen levels\"| Ledger[suggestions_ledger]
  Ledger --> Scorer[nightly_bar_walk_21d_R_fees]
  Ledger --> Watch[watch_targets_live_view]
  Human[you] -->|\"taken, fill\"| Ledger
  Scorer --> UI[per_source_report]
```

## Phases

### Phase 0: actionable alerts from `revanth-weekly-direction.pine` (do first)

Source: the full server history (`/api/alerts/history?limit=20000`, 2,127 alerts, 09-04 to 09-22). It contains 413 Intraday ENTRY and 408 EXIT alerts, which pair into 331 same-day ticker trades. Every trade was replayed independently on yfinance 1-minute bars: entry at the next 1-minute open after the alert, the script's own stop and T1, stop checked first, flat at 15:55 ET, results in R on the underlying. Cached at `/tmp/ui/bars/*.parquet`, `/tmp/ui/replay.csv` and `/tmp/ui/pairs.json`.

- **The ENTRY signal has no edge yet.**
  - The independent replay gives −0.09R mean, −1.00 median, 34% wins. The script's own `exit_r` gives −0.147R. The two correlate at 0.63.
  - Positive on only 5 of 11 days.
  - Option spread and theta are not included, so real 0DTE results are worse.
- **Grade is the only separator.**
  - A averaged +0.06R (n=172) and B −0.25R (n=159); B is negative under every stop variant tested.
  - `alert_evaluator.py:199` gates on `score < 65`, not grade, so B entries still get through.
- **Stops are noise, but widening them does not help.**
  - The median stop is 0.24% of price, about 1.1× the typical 5-minute range. 66% of trades stop out, 55 of them within 5 minutes.
  - Stop and target at 2× gave −0.136R; 3× gave −0.105R; 2× and 3× stops with an EOD exit gave −0.184R and −0.121R.
  - So the problem is the trigger, not stop placement.
- **The veto layer is not adding value.**
  - LLM TAKE on grade A: +0.15R (n=62), the only positive cell.
  - Vetoed grade A: −0.03R (n=74).
  - Every veto reason has n < 30, too few to judge.
  - The earlier 2-day read (+0.46R for A, stage veto \"blocking winners\") does not hold over 11 days. Drop it.
- **Time of day:** only the 9:xx ET hour is positive (+0.09R, n=66). The 10–12 ET hours run −0.13 to −0.30R.
- **Noise:** 1,306 of 2,127 alerts are Daily \"Stagnation (Research)\" NEUTRAL, about 145 a day. In the last 500, the LLM verdict was WATCH or CUT on 99.7% of them.

Fixes (actionable = few, grade A, measured):
1. **Hard veto grade B/C/D.** In `evaluate_risk_vetoes` (`alert_evaluator.py:196-208`), veto on `grade != \"A\"`, not `score < 65`. This cuts entries in half, and they are the negative half.
2. **Mute Daily NEUTRAL.** In `main.py`/`alert_parser`, skip LLM enrichment and push for `strategy=Daily and action=NEUTRAL`. Store them only, plus one end-of-day digest line.
3. **One push per grade-A ENTRY that passes the deterministic gates.** The body is one block: ticker, side, held/stop/T1, stop in % and in 5-minute-range units, EM/premium, size, `wrong_if`, the LLM note, and time. EXIT is one line with `exit_r` and `session_r`. Label every push `UNPROVEN` until item 5 clears.
4. **Shadow ledger.** Add an `intraday_signals` table: `trade_id` (currently null in the `alerts` rows, so set it in the Pine payload or derive it from ticker + entry ts), grade, score, align, veto_reason, `pine_exit_r`, `replay_r` from 1-minute bars, `taken`, `your_fill`. Run a nightly replay that uses exactly the method above. In the UI, show R by grade, veto reason and hour.
5. **Go/no-go.** At n ≥ 200 grade-A pairs (about 4 more weeks at about 8 a day), keep pushing only if replay R ≥ +0.10 on the underlying (the cushion for option costs) and it is positive on the majority of days. Otherwise treat the weekly script as context only. Don't tune the stage, time or LLM vetoes before then.
6. **Pine side (you, in `revanth-weekly-direction.pine`).** Add `trade_id` and `held_px` to the ENTRY/EXIT JSON, and make EXIT carry the ENTRY's plan rather than the current bar's. Right now EXIT `plan` shows new levels, and `exit_px` can equal the plan's `In` price.

The watch-path alert defects are in Phase 3: the 1% proximity buffer firing \"Order active\" above the zone, re-entry after invalidation, and 107 of 219 Tastytrade alerts being duplicates (94 of them LYB at 69.13; fix the dedupe key in `tastytrade_client.sync_watch_alerts`).

### Phase 1: an immutable ledger with honest scoring (do first)

- **Add a table.** Create `suggestions` in `data/research_watch.db`, with a unique key on (ticker, date, source, report_hash). Columns:
  - **Levels, frozen at insert:** `source`, `side`, `entry_type`, `entry_low`, `entry_high`, `breakout_level`, `stop`, `target_1`, `target_2`, `planned_rr`, `atr_at_signal`.
  - **Your input:** `taken`, `your_fill`, `notes`.
  - **Scorer output:** `fill_date`, `fill_price`, `exit_date`, `exit_price`, `exit_reason`, `r_net`, `mae_r`, `scored_at`.
  - Insert with `ON CONFLICT DO NOTHING`. Only the scorer and `taken`/`your_fill` update a row after that.
- **Scorer.** Rewrite `evaluate_all_suggested_trades` (or add a new `src/tracking/suggestion_scorer.py`) on top of `evaluate_setup_lifecycle_bars`:
  - Pass `entry_type` and `breakout_level`.
  - Start walking at the bar after the signal.
  - Exit after 21 trading days (`max_holding_bars=21`).
  - Use the validator's gap-through-stop `exit_price`.
  - Charge 10bps round trip.
  - Report `R = (exit − fill) / (fill − stop)`.
  - Reuse the conventions in `data-windows/scripts/honest_entry_replay.py`: next open, stop checked first, skip gaps through the stop.
- **Options.** Keep modeled options P/L in its own column with `is_modeled=1` and leave it out of every headline number until real option marks are stored.
- **Backfill.** Load the existing 58 `suggested_trades_audit` rows as `source=legacy`, keeping their levels unchanged.

### Phase 2: one level-validation gate for every write path

- **Build the gate.** Add `validate_levels(plan, dw, side) -> (ok, reasons)` in `src/logic/`. Call it from `arbitration.py:220`, from `run_watch_alerts.py:70`, and before any ledger insert. Its checks:
  - **Level ordering:** LONG requires `stop < entry_low <= entry_high < target_1 <= target_2`.
  - **R:R floor:** planned R:R from the zone midpoint must be at least 1.5. Make the floor configurable; measured evidence favours 2.0.
  - **Stop distance:** at least 1.0 × ATR14 from the zone midpoint. Pull ATR from the Data Window or bars. This is the direct fix for the 2.45%-median tight stops.
  - **Pine drift:** flag when LLM levels differ from the Pine `Long Entry Zone Bot/Top`, `Long Stop Loss` or `Long Target` by more than X ATR, and store the drift, so you can later measure whether the LLM's changes help.
  - **Options:** run `strike_validator.validate_strike_geometry` on the LLM's `options_plan`, and apply the expected-move demotion on the arbitration path too.
- **What happens to failures.** Log rows that fail the gate as `source=judge, verdict=REJECTED_BY_GATE` with the reasons, rather than dropping them. That keeps the gate itself measurable.
- **Give the judge the Pine levels.** Add the Pine zone, stop, target and at-market R:R to the ground-truth block at `arbitration.py:139-153`.
- **Remove invented values.** Delete the hardcoded `rr_ratio=2.0` and the literal expirations in `report_level_extractor.py`, and compute or leave null instead.

### Phase 3: correct the watch engine (`run_watch_alerts.py`, `watch_manager.py`)

- **Keep TESTING_SUPPORT polled.** Add it to `get_active_watch_targets` (`watch_manager.py:264`) so the reclaim logic can run.
- **Close conditions.** When the condition says close, check the daily close after 16:00 ET only, with a symmetric band.
- **Breakouts.** Require a daily close above `breakout_level`.
- **IN_ZONE.** Make it strictly `entry_low <= price <= entry_high`; no 1% buffer and no stop-to-zone gap.
- **Expiry.** Expire a STALKING row after 5 trading days, as the gem specifies.
- **Keep history.** Change the primary key to (ticker, date), or treat `watch_targets` as a view of the latest ledger row per ticker. Remove the double write: let only the extractor path write, after the gate.

### Phase 4: baseline, attribution and the UI

- **Log the rule baseline.** In `process_survivor.py` (around `:455-472`, where `*_thesis.json` is written), insert every PASS and WATCH triage verdict with Pine `long_plan` levels as `source=rule`. Backfill from `data/logs/data_window_scrapes.jsonl`.
- **Log every report source.** Insert Model A, Model B and the judge as separate `source` rows from `output_writer.py` and `arbitration.py`.
- **UI.** In `src/ui/routes/trades.py`, add a per-source table: n, mean R, median R, win %, stop-out %, and taken versus not taken. Compare sources only on the same (ticker, date) set. Add a `taken` toggle and a fill-price input per row.

### Phase 5: structured forecasts (`superforecasting_auditor.py`)

- **Structured fields.** Change the report schema (`gems/few_shot_template.md:74-95`, `gems/independent_gem.md:126-147`) to structured fields: `{type: touch|close_above|close_below, level, horizon_trading_days, before_level?}`. Drop keyword parsing.
- **Resolution.** Resolve on daily high/low for touch events and on close for close events, count horizons in trading days, and honour `before_level`.
- **Bugs.** Fix the whole-CSV fallback at `:101`.
- **Model B.** Stop asking it about Pine zones, since it can't see them. Give it its own level-based events.

### Phase 6: gate and screener logic (from earlier, still open)

- **`process_survivor.py:259,291`.** Stop gating PASS names on Buy Score or on `ev_r` built from `Dir Prob`.
- **`data_window_filter.py:820`.** The code-20 PASS should require at-market R:R ≥ 2.
- **`data_window_filter.py:817`.** Rank the RSI2 branch below the at-market R:R lane, add `not no_fresh_long`, and use pooled win-rate and expected-value constants.
- **`pine_screener_engine.py:594-679`.** Rank on R:R and use stage and squeeze for display only.

### Phase 7: reliability

- **Jobs.** Jobs die when the UI server restarts (\"Server restarted while job was running\", 16 of 40). Run research jobs in a supervisor detached from the UI process, or make `research_queue.py` re-attach to jobs that are still running instead of marking them FAILED.
- **Timeouts (#13).** Add `timeout=` to the four `subprocess.run` calls in `schwab_pre_move_scan.py` (~1319, 1359, 1372, 1375).

## Acceptance checks

- Re-running research on the same ticker and date never changes an existing ledger row's levels.
- Every ledger row has `source`, and every scored row has `r_net` over a horizon of 21 trading days or fewer.
- The UI headline equals the mean R on the stock price; modeled options P/L shows only as a separate labelled figure.
- No row with a LONG `target_1 <= entry` or a stop under 1 ATR reaches `watch_targets` without a `REJECTED_BY_GATE` record.
- After about 100 scored rows per source, the per-source table answers whether the judge beats the rule.
",
