---
name: Windows Python fixes
overview: Python/UI changes for you to implement on Windows so devin-s suggests only measured lanes (in-zone R:R, R:R tier 3, code 20, RSI2, new OVERSOLD), with post-COVID base rates instead of Dir Prob or per-stock RSI2 stats, and scores every suggestion honestly from closed bars. Pine is done and verified by scrape (AMZN/HOOD/MSFT/WDAY, 2026-09-23).
todos:
  - id: p0
    content: "P0: loader derives ATR14, RSI2, RR@mkt, action codes, protocol, prev extZ; drop Matured aliases; psutil"
    status: pending
  - id: p1
    content: "P1: RR_MKT_STRONG=3, LANE_PRIORS replace Dir Prob/RSI2 stats, add OVERSOLD lane, setup_lane"
    status: pending
  - id: p2
    content: "P2: validator + scorer honest fills/exits, RSI2 own replay rules, scorer_version"
    status: pending
  - id: p3
    content: "P3: ledger columns, dedupe, score only ENTER/STALK NEW"
    status: pending
  - id: p4
    content: "P4: selection from triage only, WATCH_SHADOW, MANAGE, 3-day dedupe"
    status: pending
  - id: p5
    content: "P5: judge ground truth, JSON-first, gate blocks persistence, Report B sanitize"
    status: pending
  - id: p6
    content: "P6: level gate ATR stop, RR>=2 except RSI2/CODE20/OVERSOLD, OVERSOLD uses Pine levels"
    status: pending
  - id: p7
    content: "P7: options from live chain, daily mark, option R"
    status: pending
  - id: p8
    content: "P8: UI scoreboard by lane, table columns, retire $ models, bug fixes"
    status: pending
  - id: p9
    content: "P9: re-score, record starts 2026-09-23"
    status: pending
  - id: gems
    content: "Mine: sync gems/bible for tier 3, OVERSOLD, removed Matured fields, lane priors"
    status: pending
isProject: false
---

# devin-s Python/UI fixes (Windows)

Pine state (done, verified 2026-09-23 scrape, 83 columns):
- Exported: `Long Entry`, `Long Stop Loss`, `Long Target`, `Ext Z Self Relative`, `Zone RR Flags Pack`, `Context Action Pack`, `RSI2 Events Pack`, RSI2 levels.
- NOT exported (Python must derive): `Long RR At Market`, `Action Long Code`, `RSI2 RSI2`, `RSI2 ATR14`, `RSI2 Protocol Version`.
- REMOVED: `RSI2 Matured Trades / Win Pct / Mean R / Mean R 10bps` (per-stock record measured as pure noise, also post-COVID).
- New chart labels: `R:R x@mkt · stop yATR` (teal/large at RR@mkt >= 3), `OVERSOLD` (extZ <= -2).

Measured lanes, post-COVID (Mar-2020 to Jul-2026, 529 tickers, R vs same-day universe, 4/4 periods positive):
- in-zone & RR@mkt >= 3 & no fade: +0.13R, win ~25%
- in-zone & RR@mkt >= 2 & no fade: +0.08R, win ~30%
- code 20 REVERSAL & RR@mkt >= 2: +0.08R, win ~45%
- OVERSOLD extZ <= -2: +0.06R, win ~51%, median positive
- RSI2 (own levels): +0.06R, win ~61%
- Negative: fade set, extZ >= 2, RR@mkt < 1, near 52w high, RS leader. Shorts: no edge.

```mermaid
flowchart LR
    csv[datawindow csv] --> loader[load_data_window derive fields]
    loader --> triage[triage lanes]
    triage -->|PASS lane| deep[deep research]
    triage -->|WATCH| shadow[WATCH_SHADOW]
    held[held names] --> manage[MANAGE dossier only]
    deep --> judge[judge JSON]
    shadow --> judge
    judge --> gate[validate_levels]
    gate -->|pass| ledger[suggestions ledger]
    gate -->|fail| rejected[rejected_plans]
    ledger --> scorer[suggestion_scorer closed bars]
    scorer --> ui[UI R by lane]
P0. Data contract (blocking, do first)
In devin-s/src/utils/artifact_loader.py load_data_window (inject into dw_dict so all consumers keep working):

- From the 1-year *_datawindow.csv OHLC compute and inject: RSI2 ATR14 = Wilder ATR(14) (RMA of true range, alpha=1/14). RSI2 RSI2 = Wilder RSI(2). Long RR At Market = (Long Target - close) / (close - Long Stop Loss) only if close > stop and target > close, else leave absent. Action Long Code = Context Action Pack % 32; Action Short Code = (pack // 32) % 32; Entry At Market = pack // 1024. RSI2 Protocol Version = 2 when absent. Prev Ext Z = previous row's Ext Z Self Relative (for OVERSOLD first-bar flag).
- RSI2 ATR14 = Wilder ATR(14) (RMA of true range, alpha=1/14).
- RSI2 RSI2 = Wilder RSI(2).
- Long RR At Market = (Long Target - close) / (close - Long Stop Loss) only if close > stop and target > close, else leave absent.
- Action Long Code = Context Action Pack % 32; Action Short Code = (pack // 32) % 32; Entry At Market = pack // 1024.
- RSI2 Protocol Version = 2 when absent.
- Prev Ext Z = previous row's Ext Z Self Relative (for OVERSOLD first-bar flag).
- Same derivations in devin-s/src/data/csv_adapter.py (~line 356 protocol read) so both loaders agree.
- devin-s/src/logic/data_window_filter.py: Delete aliases rsi2_matured, rsi2_win_pct, rsi2_mean_r, rsi2_mean_stress_r (lines 169-172). Keep long_rr_at_market alias (line 196); it now resolves to the injected value.
- Delete aliases rsi2_matured, rsi2_win_pct, rsi2_mean_r, rsi2_mean_stress_r (lines 169-172).
- Keep long_rr_at_market alias (line 196); it now resolves to the injected value.
- devin-s/src/logic/deep_research/arbitration.py line 157: reads Long RR At Market from dw_dict; works after injection (was always N/A).
- requirements.txt: add psutil.
- Check: re-triage WDAY/AMZN from today's scrape; expect non-null action, stop, rr_at_market (AMZN today 0.77, so WATCH)
 
## P1. Triage lanes and win/EV (data_window_filter.py)

- Line 79: `RR_MKT_STRONG = 3.0` (was 5.0; 5 never re-measured post-COVID, 3 holds in every period).
- Lines 596-612: stop using `Dir Prob` and per-stock RSI2 stats for `win_prob` / `ev_r` (both measured no edge). Replace with a lane constant table:

```python
LANE_PRIORS = { # post-COVID, path-accurate 21 bars
    "rr_at_market_lane_strong": (25.0, 0.13),
    "rr_at_market_lane": (30.0, 0.08),
    "reversal_buy_lane": (45.0, 0.08),
    "oversold_lane": (51.0, 0.06),
    "rsi2_setup_lane": (61.0, 0.06),
}
Set win_prob, ev_r = LANE_PRIORS[reason] after triage decides; None for WATCH/CUT. Any EV gate (TIER_A_MIN_EV_R) must use these, not RR x Dir Prob.

- Lines 817-837 lane order: code 20 (RR@mkt >= 2), RR lane, RSI2, then new OVERSOLD branch:
elif W["side"] == "long" and ext_z_self <= -2.0 and act_code != 18 \
        and W["stop"] is not None and W["target"] is not None and W["stop"] < price < W["target"]:
    triage, reason = "PASS", "oversold_lane"
Add oversold_first_bar = prev_ext_z > -2.0 as a flag only (all-bar version also measured +0.066R).

- Line 881 structure read stays (extZ <= -1.5 put side) — orthogonal question.
- Record setup_lane from reason: RR_SETUP, RR_SETUP_STRONG, CODE20, OVERSOLD, RSI2.
P2. Scorer and validator (honest fills/exits)
devin-s/src/tracking/execution_validator.py:

- Line 230: delete was_filled = (curr_st == "IN_TRADE").
- Lines 199-221: no fake live bar; scorer passes live_price=0.
- Line 114: auto_adjust=False; drop bars after _get_last_closed_session_date().
- Line 312: open below stop = GAP_STOP at open. Lines 394-408: target on fill bar only if filled at open.
- max_fill_bars=5, then NOT_FILLED. INVALIDATED before fill carries no P&L (332-349). Filled stays IN_TRADE (513-521). T1 closes the trade.
devin-s/src/tracking/suggestion_scorer.py:

- Line 124: risk = planned entry_high - stop; stop >= fill means INVALID_GEOMETRY.
- Line 86: no last-close live bar. Line 115: ignore your_fill. Line 117: breakout uses validator gap fill.
- Cost 0.0005*(fill+exit)/risk. Store the real validator status. Add scorer_version; re-score on change. _compute_stats (262) filters gate_status='PASS'.
- RSI2 lane scored with its own rules (next-open entry, EMA5 recovery exit, 10-bar max) — reuse honest_entry_replay contract, not the 21-bar zone walk.
P3. Ledger (suggestions_ledger.py)

- Columns: verdict, setup_lane, kind (NEW/MANAGE), atr_at_signal, rr_at_market_at_signal, spot_at_signal, lane_prior_win, lane_prior_ev.
- Dedupe (ticker, date, source), last write wins. Score only verdict IN ('ENTER','STALK'), complete levels, kind=NEW.
- Stop writing single-price model A/B rows (output_writer.py 163-175, 234-246). backfill_legacy.py 52/57: gate_status=LEGACY_UNGATED, no options rows.
- Remove gate re-run inside append_suggestion (84-86).
P4. Selection

- process_survivor.py:214: quality_pass = det_pass. WATCH goes to WATCH_SHADOW, ranked by rr_at_market, never Buy Score.
- research.py 281-301: queue only from triage PASS / force; delete hard-coded list 327-357.
- pipeline.py 149: explicit ticker reads triage lane, or kind=MANAGE when held; delete raw-chart fallback 220-226.
- continuous_screener_daemon.py:594: scrape_only then triage then deep research if PASS.
- schwab_pre_move_scan.py 1321 (no stale DW), 1376 (read send_for_deep_research).
- last_researched table: skip if within 3 trading days and (action code, in-zone, RR@mkt, stop, target) unchanged.
- UI launch force=false (swing.js:31, status.js:358). schwab_plugin.py:131: remove forced add-on zone.
P5. Judge (arbitration.py, context_builder.py)

- Ground truth (139-158): ATR14, Exp Move Pct 21b, decoded Zone RR Flags, Action Long Code, triage lane + reason + lane_prior_win/ev, DW bar date and close, held yes/no. IV keys energy_iv30/energy_ivrank. Drop 52w fields. Remove Dir Prob / Buy Score as evidence.
- JSON first (or separate JSON call), max_tokens above 3072; missing JSON gives verdict=NO_LEVELS. Drop status (104), add setup_lane.
- 234-261: failed gate means no upsert_watch_target, no append_suggestion; write rejected_plans.
- context_builder.py 583-591: sanitized CSV for Report B, blacklist Buy/Sell Score and Dir Prob. Line 515: remove "override Pine zone when floor R:R >= 2".
- debate.py:65 cache on DW hash. live_fetcher.py 67-71 quote TTL. report_level_extractor.py 339-362 fallback = source=fallback.



## P6. Level gate (`level_validation.py`)

- ATR (now always injected) missing means fail.
- Risk from `entry_high`. Stop <= min(`entry_low - 1.0*ATR`, Pine `Long Stop Loss`).
- At-market R:R `(T1 - spot)/(spot - stop)` recorded; require >= 2 except lanes `RSI2`, `CODE20` (already RR-gated in triage), `OVERSOLD`.
- OVERSOLD: stop and T1 must equal Pine `Long Stop Loss` / `Long Target` (the measured geometry); judge may not tighten.
- T1 <= `close * (1 + 1.5 * ExpMove21b)`. Earnings inside 21 bars from `date_str` = fail for NEW. Reject SHORT.

## P7. Options

- Entry = live chain mid (Schwab, then Tasty, then Alpaca) at suggestion time, stored as `opt_entry_*`; unpriced rows not scored.
- `strike_validator.py`: credit short strike >= 1.25x ExpMove, legs must exist, live spot.
- Daily chain mark; exit on underlying stop/target or expiry intrinsic; option R = P&L / max loss; per-structure payoff (fix CC/long-call phantom P&L, bear put spread as debit).

## P8. UI

- Scoreboard panel from `/api/scoreboard`, `/api/trades/sources`: lane x source n, fill rate, mean/median R, win %, stop %, and lane prior next to realised (flag when n >= 30).
- Suggested table: gate, lane, kind, stop in xATR, RR zone and RR@mkt; status `NOT_FILLED/IN_TRADE/T1/STOP/TIME/RUNAWAY`; no dollar P&L.
- `trade_ideas_generator.py` 139-148: no invented stop/T1, no fake 2.0 R:R, INVALID geometry shown.
- `watchlist.py` 388-403 Filled/Reset becomes `user_taken` by row id; 351 soft delete; retire $ model 181-343. Retire `suggested_trades_auditor.py` $ model 280-371 (also fixes lock deadlock 402/423).
- Bugs: `trades.js:471` match by id, `713-722` hard-coded CALL/BUY 100, `171` Target Hit filter includes IN_TRADE, `index.html` duplicate `modal-watchlist-audit` (1827, 2260).

## P9. Reset

- Bump `scorer_version`, re-score all rows.
- Main record = `source=judge`, `gate=PASS`, `kind=NEW`, lanes `RR_SETUP`, `RR_SETUP_STRONG`, `CODE20`, `OVERSOLD`, `RSI2`, dated >= 2026-09-23. Everything else reported separately.

## Mine (after P1/P5 land)

- Gems (`devin-s/gems/revanth-original-gem.md`, `revanth-bible.md`, `revanth-gem-local.md`): new R:R tier 3, OVERSOLD label, removed RSI2 Matured fields, post-COVID lane priors, per-stock RSI2 record is noise.
- Ponytail/original/independent stop wording, HOLD rule, judge prompt text for `arbitration.py` 31 and 175.

## Verify (Windows)

- Triage today's scrape: AMZN/HOOD/MSFT/WDAY all WATCH (RR@mkt < 1). Historical replay of AMZN 2026-08-18 row gives PASS `rr_at_market_lane` with RR 2.7, stop 0.7 ATR.
- Re-score ledger; compare to hand check of 41: 6 T1 / 16 stops, no T1 on HOOD/AMD/NVDA, no fills on WDAY/AAPL/CDW/MSFT.
- One PASS, one WATCH, one held name land in lane / `WATCH_SHADOW` / `MANAGE`.
