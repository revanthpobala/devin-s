# Active Session Post-Mortem Learnings & Empirical Edge

## Session Benchmark: 2026-09-16 Audit
- **Dataset:** 35 Executed Intraday Setups across 16 Watchlist Instruments (SPY, QQQ, AMD, NVDA, COST, AVGO, TSLA, GOOGL, UNH, JPM, MSFT, WMT, etc.)
- **Net System Score:** +$683.00 Capital Saved by AI Guardrails vs. -$1,387.00 False Positive Bleed.

---

## Empirical Quadrant Discoveries

### 1. True Positives (Winning Edge: +$486.00 across 6 Trades)
- **Winning Tickers:** AMD (+0.45 pts, +1.02 pts), QQQ (+0.49 pts), COST (+1.58 pts), AVGO (+0.33 pts), NVDA (+0.99 pts).
- **Core Pattern:**
  - 100% of winners had **perfect alignment across Daily + 15m Stage 2 / Stage 4** continuation.
  - Every single winner executed the deterministic partial-exit rule: **Scale half at Target 1, trail runner stop to break-even+ (BE+)**.
  - **Directive:** Preserve this exact bracket logic. Never override Target 1 scale-out with discretionary hope.

### 2. False Positives (Loss Clusters: -$1,387.00 across 11 Trades)
- **Critical Finding:** 5 out of 11 losses (totaling -$694.00) clustered directly between **11:15 MT and 12:45 MT (the lunch lull)**:
  - AMD 11:50 (-$321.00)
  - QQQ 12:10 (-$88.00)
  - COST 11:35 (-$159.00)
  - AVGO 11:20 (-$96.00)
  - MSFT 11:25 (-$30.00)
- **Root Cause:** Squeeze breakouts in low-volume lunch chop lack institutional follow-through and decay rapidly into theta burn.
- **Directive:** Implemented `Rule 4.1 Midday Lull Gate` in `skills/execution_timing_gates.md`.

### 3. True Negatives (Preserved Capital: +$683.00 across 11 Blocked Trades)
- **Deflected Traps:** UNH, JPM, AAPL, COST, XOM, NVDA counter-trend attempts.
- **Root Cause:** The indicator fired reversal signals, but the AI triage blocked them due to "Counter-Trend Chop", "Fighting Daily Stage 4 Trend", or "Declining Volume".
- **Directive:** Affirm this counter-trend filter. High precision (61.1%) protects capital.

### 4. False Negatives (Alpha Leaks: -$847.00 across 7 Filtered Trades)
- **Major Missed Runs:**
  - `QQQ PUT 14:55 MT`: Missed +3.90 pts (+ $390.00) afternoon breakdown into market close.
  - `TSLA PUT 09:55 MT`: Missed +2.38 pts (+ $238.00) morning continuation.
- **Root Cause:** The LLM was overly conservative on late-session index trades, rejecting the QQQ short simply because it was after 14:30 MT.
- **Directive:** Implemented `Rule 4.2 Power Hour Index Momentum Exception` in `skills/execution_timing_gates.md`.
