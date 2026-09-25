---
name: Desk UI refine
overview: Fix the desk bugs the live service shows today (Journal 500, Record all zeros, duplicate and bogus Act Now rows, wrong JS call signatures), then restyle Today/Journal/Record so the actionable information is on top and empty or zero values read as a dash instead of 0. The user implements all of it on Windows.
todos:
  - id: loop-close
    content: "Close research loop: every local PASS auto-dispatches deep research; research_queue status updated through lifecycle; coverage check"
    
---

# Desk UI refine (you implement; I verify after pull)

## 0. Actionable-first + local research on every alert (top priority)

Verified live (commit 28f251d is latest, nothing new on origin):
- Local research does run on every alert: `/api/alerts/evaluate-status` gives 2401/2401 evaluated. Today: 110 alerts / 85 symbols, all with `llm_decision` (69 WATCH, 16 CUT, 12 PASS).
- **The loop breaks after the local research.** All 12 local PASS today (ADI, BIIB, COHR, CRWD, DIS, HOOD, HPE, KEYS, NOW, NVDA, TEL, UHS) have **no deep research and no suggestion**; the only rows in today's journal are CRL and PANW.
- `research_queue`: 82/82 rows stuck at `QUEUED`. `update_research_status` ([alert_db.py](devin-s/src/tracking/alert_db.py) L583) is never called anywhere.
- The desk's "Found today" shows those stale QUEUED rows and ignores the local decision, so it's noise.

```mermaid
flowchart LR
  alert[Alert] --> local["Local research (alert_evaluator)"]
  local -->|CUT| cut[Hidden by default]
  local -->|WATCH| watch[Watch list]
  local -->|PASS| deep[Auto deep research]
  deep -->|gate PASS| act[Act now]
  deep -->|gate FAIL| rej[Rejected with reason]
Fix:

- Every local PASS dispatches deep research. In http://devin-s/src/tracking/auto_triage_daemon.py, right after evaluate_alert_payload: if the decision contains PASS, enqueue into active_research_jobs (mode="full"), skipping when the ticker already has a report or a RUNNING/QUEUED job today (reuse the _is_job_active_in_db logic from http://devin-s/src/screener/continuous_screener_daemon.py L605). The existing dispatch_next_queued_job drains it.
- Lifecycle status via update_research_status: QUEUED -> LOCAL_PASS|LOCAL_WATCH|LOCAL_CUT (after local) -> DEEP_RUNNING -> DEEP_DONE_PASS|DEEP_DONE_REJECT (after the gate in the research worker).
- Coverage guarantee: any research_queue symbol with no local decision for today gets evaluate_alert_payload run on a synthetic payload. Add GET /api/desk/coverage: {alerts, local_done, local_pass, deep_done, deep_missing:[...]}.
0a. Today = inbox, not a list

- Replace found with inbox: one row per symbol (latest alert) with setup, local decision + score (parsed from llm_decision, e.g. PASS 4/10), a one-line llm_playbook, deep status (none / queued / running / done), and gate result plus a suggestion link.
- Order of the page: 1. Act now: deep done + gate PASS + in zone / in trade / within 1.5%. 2. Needs you: local PASS with deep missing or failed (button: Run deep). 3. Watch: local WATCH, compact. 4. CUT: collapsed, count only.
- Coverage bar at the top: "Local 85/85 · Deep 0/12". Red when deep < local PASS.
- Nothing without a local decision appears anywhere except the coverage "missing" list.
Live findings (https://172.20.11.73:8050, commit 28f251d)

- /api/desk/journal?include_rejected=1 returns 500: SQLite rejects the parenthesised (SELECT..) UNION ALL (SELECT..) at http://devin-s/src/ui/routes/desk.py L132.
- Record is all 0: its filter is scorer_version=2 AND setup_lane IN (...) AND date>=2026-09-23, but all 26 filled/closed trades are legacy (lane NULL). Correct as a gated scorecard, but it reads as broken.
- Today "Act now" has 23 rows with duplicates (AMZN x5, WDAY x4): the LEFT JOIN watch_targets ON ticker runs once per suggestion.
- Live R:R is nonsense near the stop: TRV last 362.49, stop 361.9 gives 31.03.
- Journal shows -0.00 for open trades (the scorer writes placeholder r_net=-0.001 with exit_reason=IN_TRADE). Rejected rows print 0 in every price field.
- http://devin-s/web/static/js/desk.js call bugs: AppSwing.openReportModal(ticker, date) has its arguments reversed; the signature is (date, ticker) (http://devin-s/web/static/js/swing.js L1337). AppSwing.launchResearch(ticker) is wrong: it takes dateOverride. Use AppApi.triggerResearch(ticker, 'full') (api.js L271). The notes PATCH sends no Content-Type: application/json header, so FastAPI returns 422.
- AppSwing.openReportModal(ticker, date) has its arguments reversed; the signature is (date, ticker) (http://devin-s/web/static/js/swing.js L1337).
- AppSwing.launchResearch(ticker) is wrong: it takes dateOverride. Use AppApi.triggerResearch(ticker, 'full') (api.js L271).
- The notes PATCH sends no Content-Type: application/json header, so FastAPI returns 422.
- The Journal status filter runs after LIMIT/OFFSET, so pages come back short.


## 1. Backend ([desk.py](devin-s/src/ui/routes/desk.py))
- `journal`:
  - Remove the parentheses around the UNION parts.
  - Rejected branch: return `NULL` instead of `0` for prices and R.
  - Push the status filter into SQL:
    - CLOSED = `exit_date IS NOT NULL`
    - FILLED = `fill_date IS NOT NULL AND exit_date IS NULL`
    - OPEN / EXPIRED = no fill, split on `date >= date('now','-30 days')`
  - Select `entry_type`, `breakout_level`, `source`.
  - For non-CLOSED rows return `r_net` as NULL, or return live `unrealized_r` as a separate field.
  - Add `summary`: closed n, wins, win %, sum R, mean R, median R, counts per status (computed over all rows matching the filters, not just the page).
- `today`:
  - One row per ticker: join on the latest suggestion (`s.id = (SELECT MAX(id) FROM suggestions WHERE ticker=s.ticker AND gate_status='PASS')`).
  - Add `room_to_stop = (last-stop)/(entry_high-stop)`.
  - Set `live_rr = NULL` when `last<=stop` or `room_to_stop<0.25`, with a `flag` of `BELOW_STOP` or `AT_STOP`.
  - Sort: IN_TRADE first, then IN_ZONE, then near; within each group by `live_rr` desc.
  - `found`: group server-side by `setup` as `{setup, tickers:[...]}`; drop `reason` when it equals `"Screener candidate setup: " + setup`.
- `record`:
  - Add `scope=gated|all` (default `gated`).
  - `all` = every closed suggestion, labelled legacy. Returns the same stats, `by_lane` (lane NULL shown as `UNLANED`) and equity curve.
  - Always return `total_scored`, `sum_r`, `mean_r`, `median_r` keys, even when empty.
  - Add `first_score_eta` = min(date)+21 trading days of open gated suggestions, so the empty state can say when numbers will appear.

## 2. UI ([desk.js](devin-s/web/static/js/desk.js), [index.html](devin-s/web/index.html))
- Shared helpers at the top of desk.js:
  - `fmt(v, d)` returns an em dash for null/undefined/0-placeholder.
  - `pill(text, tone)`, `kpi(label, value, sub, tone)`.
  - Tones: neutral / info / warn / bad / good. Green only for realised positive R.
- **Today** (order matters):
  1. KPI strip: In trade, In zone, Near (<=1.5%), Stalking, Found today, and all-time closed R from `/api/desk/record?scope=all`.
  2. Act now as a card grid (3 per row):
     - ticker, status pill, last, zone, stop, T1, dist %, live R:R (or an AT STOP / BELOW STOP warn pill), stop width in ATR if available.
     - Buttons: Dossier, Research.
  3. Stalking: compact table (ticker, lane, dist %, zone, stop, T1).
  4. Found today: collapsed `<details>`; header "Found today (82)"; one row per setup with ticker chips (chip opens dossier). No Reason column.
- **Journal**:
  - Summary strip from `summary`.
  - Filter bar: status, lane incl. UNLANED, ticker search, rejected toggle, Prev/Next.
  - Columns: Date, Ticker, Lane, Status, Entry, Stop, T1, Fill, Exit, R, Bars, Exit reason.
  - Nulls show an em dash; R for open rows shows a dash (or muted unrealised R).
  - Rejected rows muted, with the reason in the Exit reason column (truncated, full text in the tooltip).
  - Replace `prompt()` with an inline textarea plus Save in the drawer.
- **Record**:
  - Toggle Gated (default) / All history.
  - Gated with 0 scored: a single empty-state card ("No gated trades scored yet. First results around {first_score_eta}."), plus a "Show all history" button. No wall of zeros.
  - Zero values neutral colour (not red).
  - Equity curve as an SVG polyline with a 0 baseline instead of bars.
  - Lanes table shows prior win/EV vs actual.
  - The n<100 banner stays.
- Bump the `desk.js?v=` cache-buster in index.html.
- Check whether the LIVE OPERATIONS terminal stream below the panes should be hidden on Today/Journal/Record (it currently shows under Record).

## 3. Tests ([tests/test_desk_routes.py](devin-s/tests/test_desk_routes.py))
- `include_rejected=1` returns 200 and mixes rows.
- Status filter + paging returns a full page.
- Today dedupes a ticker with 3 suggestions.
- `live_rr` is NULL when last is within 25% of the stop.
- `record?scope=all` counts legacy closed rows; gated with none scored returns the keys with 0 plus `first_score_eta`.
- Use the real `research_queue` schema (`symbol`) in `trading_alerts.db`, not a fake table.
- Also still open: the `test_review_tv_exit_catastrophic_wide_stop_within_risk_budget` failure (add the `read_tape` patch to that test).

## 4. Verify (me, after you commit from Windows and I pull)
- pytest green.
- curl all 4 endpoints: 200; no duplicate tickers in `actionable`; `journal.summary.closed` matches the closed count I get from the API today (20 closed + 6 filled).
- Headless screenshots of Today / Journal / Record to check layout.