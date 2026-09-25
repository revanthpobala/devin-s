Opportunity-source pipeline audit (TV Daily/Swing, Schwab pre-move, TV Intraday)
The system finds opportunities 3 ways: TV alerts arrive through one Gmail inbox (src/clients/gmail_client.py), classified by src/logic/alert_parser.py into strategy="Daily" (EOD swing) vs "Intraday"/"RSI2", and routed in main.py; separately the Schwab pre-move screener (continuous_screener_daemon.py) scans every 10 minutes. Audited each path plus cross-pipeline consistency on 2026-09-25 against main HEAD 451bae8.
8. research_queue / "Found today" coverage is blind to the Schwab pre-move screener — highest-severity new finding
Where: research_queue table (alert_db.py:126-139) has exactly one writer: main.py:201-210's _append_screener_candidate(), called only from the Gmail/TV alert ingestion loop (main.py:409). continuous_screener_daemon.py — the Schwab pre-move scanner — never calls queue_for_research/_append_screener_candidate anywhere; it only writes to a separate table, active_research_jobs.
Problem: auto_triage_daemon.py's _ensure_queue_coverage() backfill and the desk UI's "Found today" list both read only from research_queue. Neither has ever seen a single Schwab-sourced candidate — they only ever see TV-sourced ones.
Why this matters for money: Deep research still fires correctly for Schwab candidates today, via the separate active_research_jobs path — this is not currently losing trades. But it means: (a) the coverage-guarantee safety net that exists for TV alerts has no equivalent for Schwab candidates, so a silent dispatch failure on that path has nothing catching it; (b) the desk UI literally cannot show you what the continuously-scanning Schwab screener is finding — the one source built specifically to run unattended every 10 minutes is invisible in the one screen meant to show "what did we find today."
Fix: Have continuous_screener_daemon.py also call queue_for_research() (or _append_screener_candidate-equivalent) when a candidate clears its conviction gate, with a real source="schwab_screener" tag (see #10 below) — so both the coverage guarantee and "Found today" see the full picture.
Effort: small — one call added where the daemon already decides to dispatch.
9. TV Daily/Swing alerts skip the conviction-score gate entirely — asymmetric vs. Schwab
Where: Schwab candidates must clear score >= min_conviction_score (or half that for HIGH_PRIORITY) before queueing (continuous_screener_daemon.py:710-715). TV Daily alerts queue into the same downstream funnel on a bare truthy check — main.py: if (strategy == "Daily" and alert.get("setup")) or alert.get("setup"): _append_screener_candidate(...) — no score parameter exists in that call at all.
Why this matters for money: Not currently a bad-trade bug — every research_queue row (TV or Schwab) still has to clear local triage in auto_triage_daemon.py before deep research fires, and that gate treats all sources identically. But it means the queue is noisier from TV alerts specifically, and (combined with item #1 above, the slot-count fix) unscored candidates compete for the scarce dispatch slot against Schwab candidates that were already pre-filtered by a real number.
Fix: Either score Daily alerts on ingestion using the same conviction formula, or accept the noise since local triage already backstops it — lower priority than #8/#1.

10. research_queue.source is hardcoded to "screener" for every TV alert — misleading provenance

Where: main.py:207 hardcodes source="screener" on every call to _append_screener_candidate, overriding queue_for_research's own default of "screener_alert" (alert_db.py:492). Every row in the table says "screener" regardless of whether it came from a TV Daily alert or (once #8 is fixed) the actual Schwab screener.

Why this matters for money: Nothing branches on this column today, so it's behaviorally harmless right now — but it means you can never analyze "which of my 3 sources actually finds the winners" from this table as-is, and once #8 is fixed, TV and Schwab rows become indistinguishable again unless this is fixed first.

Fix: Fix this before #8 — tag TV-sourced rows source="tv_alert", reserve source="schwab_screener" for the Schwab daemon's own inserts.

Effort: one-line change, do first since #8 depends on having a real source tag to be useful.

11. Daily NEUTRAL alerts are muted with no backtest backing and no recovery path

Where: main.py's ingest_alert_fast: is_daily_neutral = (strategy == "Daily" and raw_action == "NEUTRAL") returns early, recording the alert to the alerts table but never reaching the setup/queue check above — even if the alert happens to carry a real setup field.

Why this matters for money: Same unvalidated-threshold pattern as items #3/#7 above — no citation for why NEUTRAL should be a hard skip rather than, say, a lower-priority queue entry. If TradingView ever emits a Daily alert that's directionally neutral but still describes a real basing setup, it's dropped with no recovery except an EOD count.

Fix: Same lever as #3/#7 — check whether muted NEUTRAL alerts that had a setup field ever would have been worth queuing (using historical alerts table rows), and either confirm the mute or queue them at lower priority instead of dropping them.

Cross-pipeline dedup — checked clean, no fix needed: job-dispatch locking (active_research_jobs, keyed ticker+date, checked by both continuous_screener_daemon.py:607-619 and auto_triage_daemon.py:73-77) and suggestions_ledger.py's UNIQUE(ticker,date,source) constraint together mean no two sources ever race for the same ticker/day or produce conflicting suggestion rows. First source to claim a ticker on a given day wins the slot; no duplicate research spend, no silently conflicting trade plans.

1. Screener throughput is silently capped at 1 slot, not 3 — highest expected-value item
Where: run_continuous_screener.py:134,153 — hardcodes CONTINUOUS_MAX_CONCURRENT_SLOTS=1 and max_concurrent_slots=1.
Problem: The round-1 fix correctly raised the class default (continuous_screener_daemon.py:192,769) and its own internal __main__ CLI (--max-slots, default 3, line 853/871) from 1 to 3. But run_continuous_screener.py — the actual documented launcher (AGENTS.md:113-128, "python run_continuous_screener.py") — was never touched and still overrides back down to 1, with a stale docstring claiming "dispatches exactly 1 qualified setup at a time."
Why this is the money lever: Every scan cycle that finds more than one qualifying candidate queues the rest behind whichever dispatched first. An intraday setup can decay to nothing while waiting its turn. This isn't a correctness bug — the code runs exactly as written — but it's a real ceiling on how many good opportunities actually reach deep research and a suggestion before their edge is gone. If the daemon you actually run is run_continuous_screener.py (per AGENTS.md), you are running 3x under the throughput the round-1 fix intended.
Fix: Delete the two hardcoded overrides at lines 134/153 (let the class default of 3 take effect), or add its own --max-slots flag (default 3) mirroring continuous_screener_daemon.py's CLI. Also fix the stale "exactly 1" language in the module docstring and the logger.info startup banner.
Effort: trivial, 2-line change.
2. Mixed-arrow size-cut mandate silently skipped for the common case
Where: gems/revanth-0dte.md Table D vs. skills/weinstein_stage_rules.md Rule 1.1. (screener.md #23)
Problem: The skill doc mandates a 50% size cut (or demand Grade A) whenever timeframes give mixed/conflicted signals. The gem's lookup table only applies that cut to one narrow subcase ("15m fighting the tide"); every other mixed reading falls through to "Standard size; tradeable but not a layup" — i.e. full size.
Why this is the money lever: This is a live position-sizing decision, not a doc-hygiene nit. Most real mixed-signal setups almost certainly hit the generic fallthrough, meaning the mandated risk reduction rarely actually fires — full size is going on setups the skill doc says should be half-size. That's real risk-sizing leakage on money every time a mixed-signal setup fires.
Fix: Make the generic mixed-arrow case in Table D apply the same 50%-cut/demand-Grade-A rule the skill doc mandates, not just the narrow "15m fighting the tide" variant.
Effort: doc-only, one table row.
3. Backtest-validate the "PENDING" thresholds instead of trusting guessed numbers
Where: position_monitor.py "THRESHOLD AUDIT TRAIL" comment block (from screener.md #6) — every one of: 1.25x ATR catastrophic stop, 2.5%/30% catastrophic breaker, 65% runner-lock, RVOL 1.8x lunch-bypass, lunch-gate score 88, DAY_PAUSE 3-loss threshold — is marked PENDING — scripts/replay_verify.py, meaning none of them have actually been checked against real trade history.
Why this is the money lever: Everything else on this list is about making the system do what it already claims to do. This is the one item that could change win rate or average R directly — if the 65% runner-lock is actually too tight (scratching runners early) or the RVOL 1.8x lunch-bypass is too loose (letting bad lunch-chop trades through), that's live edge left on the table or given away, and the repo already has the tooling (scripts/replay_verify.py, replay_llm.py, veto_audit.py) to check it.
Fix: Not a code change — run the existing replay tooling against each PENDING threshold, one at a time, and either confirm or recalibrate. Start with the two that gate the most trades: lunch-window score/RVOL bypass and the 65% runner-lock (since #1 above will increase trade volume through the funnel, getting these right matters more once slot-3 throughput is restored).
Effort: analysis, not code — but highest information value of anything on this list.