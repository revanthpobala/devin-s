--
name: Trader desk restructure
overview: "Replace the 20-panel sprawl with three trader views built on data the pipeline already stores: Today (what the agent found and what is actionable now), Journal (every trade the agent took, from the suggestions ledger), Record (honest scorecard). Everything else moves to Ops. No Schwab."
todos:
 - id: record-bug
  content: Dedent get_main_record_stats body (always returns {} today)
  status: pending
 - id: geometry-followups
  content: "Fix 3699cd8 follow-ups (r_mult None, MISSED_RUNAWAY typo, KPI status list, reaudit per row, entry_type, fail-closed upsert)"
  status: pending
 - id: veto-test
  content: Fix pre-existing test_review_tv_exit_catastrophic_wide_stop_within_risk_budget failure
  status: pending
 - id: desk-api
  content: "src/ui/routes/desk.py: today, journal (+rejected, notes PATCH), record endpoints"
  status: pending
 - id: desk-nav
  content: "index.html nav: Today | Journal | Record | Ops; re-parent old desks under Ops"
  status: pending
 - id: desk-js
  content: "web/static/js/desk.js: Today blocks, Journal table+drawer, Record KPIs/lanes/equity curve"
  status: pending
 - id: desk-tests
  content: tests/test_desk_routes.py + record stats regression test
  status: pending
 - id: desk-verify
  content: pytest + live check on Windows
  status: pending
isProject: false
---

# Trader desk: Today / Journal / Record / Ops

## Data already exists
- `research_queue` ([src/tracking/alert_db.py](devin-s/src/tracking/alert_db.py) L126): what the scanner found today (symbol, source, setup, status, reason).
- `suggestions` ([src/tracking/suggestions_ledger.py](devin-s/src/tracking/suggestions_ledger.py) L185): every gated agent trade: lane, levels, rr_at_market_at_signal, lane priors, fill/exit date+price, exit_reason, bars_held, r_net, mae_r.
- `rejected_plans` (same file L44): what the agent found and refused, with reasons.
- `watch_targets`: live status (STALKING / IN_ZONE / IN_TRADE) + last_price.
- Gap: no GET endpoint lists `suggestions` or `rejected_plans`.

```mermaid
flowchart LR
 scan[Scanner] --> rq[(research_queue)]
 rq --> triage[Triage]
 triage --> deep[Deep research + gate]
 deep -->|PASS| sug[(suggestions)]
 deep -->|FAIL| rej[(rejected_plans)]
 sug --> wt[(watch_targets live)]
 sug --> scorer[21-bar scorer]
 rq --> today[Today]
 wt --> today
 sug --> journal[Journal]
 rej --> journal
 scorer --> record[Record]
0. Bug to fix first (blocks Record)
http://devin-s/src/tracking/suggestion_scorer.py get_main_record_stats L411-442: everything after if not rows: return {} is indented inside that if, so the function returns {} or None always. Dedent L413-442 one level.
0b. 3699cd8 follow-ups (Journal/Record read these rows)
http://devin-s/src/tracking/suggested_trades_auditor.py:

- dollar_pnl computed when r_mult is None crashes evaluate; guard it.
- "MISSING_RUNAWAY" typo, must be "MISSED_RUNAWAY".
- KPI valid_statuses drops STALKING / MISSED_RUNAWAY / INVALIDATED: stalking count always 0, filled INVALIDATED losses vanish. Count filled INVALIDATED in R; list the others separately.
- Evaluate must skip rows already marked INVALID_GEOMETRY (today it overwrites them).
http://devin-s/scripts/reaudit_geometry.py: check every suggested_trades_audit row by its own levels, not the current watch_targets row per ticker (legacy AMZN/AAPL 09-17/18/22, INTC 09-15 get missed).
http://devin-s/src/logic/level_validation.py: infer BREAKOUT from entry_type, not breakout_level > 0.
http://devin-s/src/tracking/watch_manager.py: upsert guard fails open on exception; make it reject. Sync path must also call check_geometry.
Tests: one per bullet in tests/test_audit_geometry.py.
0c. Pre-existing failure
tests/test_exit_veto_engine.py::test_review_tv_exit_catastrophic_wide_stop_within_risk_budget expects VETO_HOLD, gets CONFIRM_EXIT. Fails before 3699cd8 too. Decide if test or engine is wrong.


## 1. API (new [src/ui/routes/desk.py](devin-s/src/ui/routes/desk.py), register in app)
- `GET /api/desk/today`
 - `found`: today's `research_queue` rows + triage verdict + lane.
 - `actionable`: `suggestions` joined to `watch_targets` where status IN_ZONE / IN_TRADE or |dist| <= 1.5%, sorted by live RR at market desc. Row: ticker, lane, entry zone, stop, target, stop width in ATR, live RR@mkt, dist %, earnings days, lane_prior_win / lane_prior_ev, suggestion_id.
 - `stalking`: remaining open PASS suggestions (< 21 bars old).
- `GET /api/desk/journal?status=&lane=&ticker=&from=&to=&page=`
 - One row per suggestion: date, ticker, lane, verdict, planned entry/stop/T1, RR@mkt at signal, fill date/price, exit date/price, exit_reason, bars_held, r_net, mae_r, priors, link to dossier (`/api/report/{date}/{ticker}`).
 - Status derived: OPEN (no fill), FILLED (fill, no exit), CLOSED (exit), EXPIRED (no fill in 21 bars).
 - `include_rejected=1` adds `rejected_plans` rows (status REJECTED, reasons).
- `PATCH /api/desk/journal/{id}`: `notes` only. Levels and outcomes are immutable (append-only ledger).
- `GET /api/desk/record?from=2026-09-23`: `get_main_record_stats` + per-lane n, win %, mean/median r_net, sum R vs lane prior, equity curve (cumulative r_net by exit_date), avg bars held, avg mae_r.

## 2. UI
- Desk nav ([web/index.html](devin-s/web/index.html) L52-70): `Today | Journal | Record | Ops`. Default Today.
- New [web/static/js/desk.js](devin-s/web/static/js/desk.js):
 - **Today**: three blocks. "Act now" (IN_ZONE/IN_TRADE cards, big), "Stalking" (table), "Found today" (scanner + triage result, "Deep research" button calls existing `/api/research/run`).
 - **Journal**: filter bar (status, lane, date, ticker, show rejected), table, row click opens drawer: plan vs outcome, R, MAE, bars, exit reason, notes box, dossier link, chart image (`/api/charts/{date}/{ticker}/...`).
 - **Record**: KPI strip (n, win %, mean R, median R, sum R), per-lane table with prior vs actual, equity curve (inline SVG, no new lib). Show "n < 100: not significant" banner below 100 closed.
- Ops desk: move alerts, intraday/0DTE, portfolio, VRAM, logs, superforecasting, Tastytrade alerts, old audit modal. Nothing deleted; just re-parented.
- Keep existing dossier modal; open it with Suggested Position tab first.

## 3. Rules the UI must respect
- R is the unit everywhere; $ only as secondary (1 contract / 100 shares) and labelled est.
- No green on anything < 50% win lanes except realised winners.
- Record counts only `scorer_version=2`, gate PASS, kind NEW, date >= 2026-09-23.

## 4. Tests
- `tests/test_desk_routes.py`: today/journal/record against a temp DB seeded with 1 IN_ZONE, 1 CLOSED win, 1 CLOSED stop, 1 EXPIRED, 1 rejected; assert statuses, sort order, record math (mean/median/sum), notes PATCH cannot change levels.
- `test_get_main_record_stats_returns_rows` for the dedent bug.

## 5. Verify
pytest green; on Windows open Today / Journal / Record; journal row count equals `SELECT COUNT(*) FROM suggestions WHERE source='judge'`.