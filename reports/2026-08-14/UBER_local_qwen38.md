I now have all the data needed. Let me synthesize the final thesis.

Key data reconciliation:
- **Bar close:** $75.95 (Data Window) · **Live:** $75.91
- **Action Long Code 8 (WATCH)** / Short Code 8 (WATCH) — baseline staging, NOT an entry
- **Stage 5 (RECOVERY), age 1 bar** — this is the critical trap. PRIME/ACTION in Stage 5 is −0.52/−0.69 SIG. But we're at Code 8, not PRIME.
- **Ext Pct vs MA200: −0.65%** — price is essentially AT the 200-day MA ($76.45). Not extended.
- **Long RR At Market: 0.31** — terrible at-market ratio. The zone ratio (RR To Target 1.07) is what's quoted but price is above the zone.
- **Zone RR Flags Pack = 8** → bit 3 (Short RR Valid) only. Long In Zone = 0, Long RR Valid = 0. So NOT in long zone.
- **Signal Pack = 4** → bit 2 (NOT fade) = 1, so fade gate is OFF (good, no DO NOT CHASE).
- **Energy IV Rank 82.5%** — premium rich, >80.
- **Exp Move 13.33%** — very high (post-earnings HV20 spike).
- **RVOL 0.49** — low volume, supply exhaustion/absorption at the floor.
- **Reversal Pattern Mask 204** = SWEEP_BULL + SWEEP_BEAR + TRAP_BULL + TRAP_BEAR (2 bull vs 2 bear, net neutral).
- **Earnings: 81 days out (Nov 3, 2026)** — PASS gate.
- **IV Rank 82.5% > 80** → premium selling side, but ExpMove is inflated (13.33%), so use the level ladder.

The decisive read: Code 8/10 baseline + Stage 5 age 1 + at-market R:R 0.31 + price pinned at MA200/VP POC with low RVOL. This is NOT a mechanical entry. The indicator has no edge here. But Pillar 2 (floor defense at VP POC $74.81, low RVOL absorption) + Pillar 3 (Jefferies $110 franchise pick, AV catalysts, $10B FCF) support a defined-risk options structure, NOT a directional equity chase. IV Rank 82.5% favors premium selling, but the at-market long R:R is 0.31 and price is above the zone — so I'll route through a Bull Call Spread (debit, defined risk) as Plan B, with a conditional stalk trigger as Plan C.

Let me write the final output.

# UBER | $75.91 | August 14, 2026
**Bar close:** $75.95 (Data Window) · **Live:** $75.91 (`get_realtime_quote`, intraday) · **Change:** +$0.04 (+0.05%) vs prior close $75.87

## ⚡ TLDR / EXECUTIVE SUMMARY
**The Thesis in 2 Sentences:** The indicator is in baseline Code 8/10 WATCH with a fresh Stage 5 (age 1) read and a dismal at-market long R:R of 0.31 — the engine offers **no directional edge** and price is pinned exactly at the MA200/VP POC floor on sub-1.0 RVOL (supply exhaustion, not conviction). What carries this trade is **Pillar 3** (Jefferies "Franchise Pick" $110 target, $10B FCF, AV/robotaxi expansion) layered on **Pillar 2** (floor defense at the $74.81 POC), routed through a **defined-risk Bull Call Spread** because IV Rank is 82.5% (premium-rich) and the ExpMove is inflated by the post-earnings HV20 spike.
**Verdict:** LEAN LONG (Options Spread) · **Conviction:** 6/10
**EARNINGS GATE:** PASS (81 days remaining | ~Nov 3, 2026)
**Primary Structure:** **Plan B (Defined-Risk Bull Call Spread)** selected — the indicator has no directional edge (Code 8, Stage 5, at-market R:R 0.31), IV Rank >80 favors defined-risk over naked premium, and the inflated ExpMove argues for a level-ladder strike, not a fixed %OTM.
**(If User Owns Shares):** HOLD — do NOT sell covered calls (Stage 5 age 1, fresh recovery, and the one measured long edge is absent; capping upside here is premature). No CC.

## 🛠️ DATA AUDIT (THE 4 PILLARS)
*Literal data verification from Data Window and live research.*

### Pillar 1: Quantitative Indicator State (The Risk Manager)
*   **Action codes:** Long Code **8 (WATCH)** / Short Code **8 (WATCH)** — baseline staging (63% of bars). NOT an entry state. No Code 1/2/20 active.
*   **Stage / Age:** **Stage 5 (RECOVERY), age 1 bar.** Fresh recovery read. ⚠️ PRIME/ACTION in Stage 5 is −0.52/−0.69 SIG — but we are at Code 8, so this is context, not a veto. Stage 5 is a one-bar waiting room that resolves 91% into Stage 2.
*   **Scores & Sigma:** Buy Score **95.47** / Sell Score **84.73**. Buy Sigma Evidence **+4.22σ** / Sell Sigma **+2.91σ**. Evidence is organic (sigma >0), not prior-driven — but a 95 score is the *normal* condition (median 85.3), not a discovery.
*   **Trade geometry:** **Long RR At Market = 0.31** (the field for "should I buy now" — dismal). RR To Target = 1.07 is the *zone* ratio, not obtainable at this price. Long Entry Zone $74.70–$75.34, Long Stop $73.40, Long Target $76.75. **Zone RR Flags Pack = 8** → Long In Zone = 0, Long RR Valid = 0 (only Short RR Valid bit set). Price is ABOVE the long zone.
*   **Extension & Regime:** **Ext Pct vs MA200 = −0.65%** (price AT the 200-day MA — not extended, well outside the 25–60% exclusion band). Ext Z Self Relative = 1.31. Exhaustion Gradient = 0.076 (fresh). Regime **0 (Healthy)**. MTF Long Aligned = **1/3**.
*   **Rev Zone & Energy:** Long Rev Zone **0.0** / Short Rev Zone 3.5. Energy State **2 (WARMING)**, IV Rank **82.5%**, ADX 14 = **13.5** (choppy — <15, so breakouts are 0% sized). DMI +DI 33.3 / −DI 25.5.

### Pillar 2: Price Action & Market Structure (The Tape & Levels)
*   **Psychological & Structural Anchors:** VP POC **$74.82** (price hovering just above the highest-volume node — the floor of the range). VP VAH $76.82, VP VAL $70.34. Darvas Box Top **$78.99**. MA200 $76.45, Hull HMA $76.70, MA50 $72.84.
*   **Volume Absorption vs Disinterest:** **RVOL = 0.49** (sub-1.0). At a defended support floor (VP POC / MA200), low RVOL = **supply exhaustion / absorption** — sellers are running out of shares, NOT weak breakout conviction. This is the bullish reading.
*   **Base Coiling & Formations:** Price rejected the $77.44 high and closed $75.95, back below the Hull HMA ($76.70) and MA200 ($76.45). Reversal Pattern Mask 204 = SWEEP_BULL + SWEEP_BEAR + TRAP_BULL + TRAP_BEAR (2 bull vs 2 bear, net neutral, age 1). A liquidity sweep at the POC that reclaimed the level = accumulation signature, but the close below the MAs is a caution flag.

### Pillar 3: Fundamental Catalysts & Sentiment (The Research)
*   **Catalyst Verification (live):** Jefferies raised PT to **$110** and designated UBER a **"Franchise Pick"** (2026-08-13/14). Consensus PT **$104.23** (37% upside). Pony.ai 2,000-robotaxi European expansion + Hinomaru Kotsu Tokyo pilot (AV aggregator model). Trailing FCF crossed **$10B**; Q2 gross bookings +22–24% YoY.
*   **Institutional & Social Flow:** Analyst consensus 15 Strong Buy / 34 Buy / 7 Hold / 1 Sell. Insider YTD **net BUYING** (1.4M shares). Bill Ackman/Pershing Square publicly bullish. Q3 guide ($0.84–$0.88 EPS, +28–35% YoY) read as "conservative" → post-earnings pullback that buyers absorbed.

### Pillar 4: Macro Profile & Derivatives Landscape (The Strategist)
*   **Macro Calendar:** Next CPI **Sep 11 (28d)**, next FOMC **Sep 17 (34d)**, next NFP **Sep 4 (21d)**. July CPI 3.4% YoY (disinflation, mildly bullish for growth). July retail sales −0.6% MoM (largest drop in a year) — a genuine headwind for discretionary consumer services.
*   **Options Gamma & Volatility (GEX):** Put/Call OI ratio **0.78** (bullish skew). Major Put Wall **$60** (26,780), $65 (13,693). Major Call Walls **$80** (30,526), **$75** (21,992), $70 (13,570). Dealer pin corridor **$60–$80**. IV Rank **82.5%** (>80 = premium rich). Exp Move 21b **13.33%** (inflated by post-earnings HV20 spike of 46.2% — use the level ladder, not a fixed %OTM).

## 📐 CALIBRATION DISCLOSURE & PILLAR RATIONALE
This is a **Code 8/10 baseline** bar — the indicator has **no measured directional edge** here (WATCH = −0.06% flat, Stage 5 age 1 is not a favorable cell, and at-market R:R 0.31 is below any entry threshold). I am **NOT** relying on the indicator for direction. The trade is carried by:
- **Pillar 2 (Floor Defense / Supply Absorption):** price defending the $74.82 VP POC on RVOL 0.49 (exhaustion, not conviction), with a tactical stop beneath the MA50/Long Stop at $73.40.
- **Pillar 3 (Catalyst):** Jefferies $110 Franchise Pick + $10B FCF + AV expansion — a dated, non-indicator re-rating that lifts conviction above the indicator-only ceiling of 6.
- **Pillar 4 (Derivatives):** IV Rank 82.5% > 80 → defined-risk structure, strike scaled to the level ladder (Darvas $78.99 / $80 call wall) because ExpMove is inflated.

Conviction is capped at **6** because the indicator contributes nothing and the macro (retail sales −0.6%) is a real headwind. This is a LEAN, not a full-size BUY.

## THE SETUP
**What the state shows:** Code 8/10 WATCH, Stage 5 age 1, price pinned at MA200 ($76.45) and VP POC ($74.82). Buy Score 95.47 with +4.22σ organic evidence, but at-market long R:R is 0.31 and price is above the long zone ($74.70–$75.34). ADX 13.5 = choppy. The engine says "no entry, stalk the zone."
**What the image shows:** A rejection wick off the $77.44 high, close back below the Hull HMA and MA200, coiling on the POC. Volume is contracting (RVOL 0.49) — a base, not a breakout.
**Macro/Policy context:** Disinflation (CPI 3.4%) is a tailwind for growth, but the −0.6% retail sales print and Sep 4 NFP / Sep 11 CPI / Sep 17 FOMC cluster is a real event-risk window. Earnings are 81 days out — clear.

```text
                        (Consensus PT: $104.23 / Jefferies: $110)
                                          ▲  [long-term, NOT the 21b target]
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │  $80.00 CALL WALL (30,526)      │  ← Dealer ceiling / T2 magnet │
        │  Darvas Box Top: $78.99         │                               │
        └─────────────────────────────────┼──────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │  Hull HMA: $76.70  / MA200: $76.45 │ ← Immediate resistance (rejected)│
        └─────────────────────────────────┼──────────────────────────────┘
                                          │
              ════════════════════════════╪═══════════════════════════════
              ║  LIVE SPOT: $75.91 (bar close $75.95)                    ║
              ════════════════════════════╪═══════════════════════════════
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │  Long Entry Zone: $74.70 - $75.34 │ ← Stalk limit zone          │
        │  VP POC: $74.82 (floor)          │ ← Supply exhaustion @ RVOL 0.49│
        └─────────────────────────────────┼──────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │  Long Stop / MA50: $73.40       │ ← TACTICAL STOP (below floor) │
        └─────────────────────────────────┼──────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │  $70.00 CALL WALL / VP VAL $70.34 │ ← Downside void              │
        │  $65 Put Wall (13,693)           │                             │
        │  $60 Put Wall (26,780)           │ ← Dealer floor               │
        └─────────────────────────────────────────────────────────────────┘
```

## 📰 SYNTHESIZED NEWS & CATALYSTS
**Recent Headlines (verified 2026-08-14):**
1. **Jefferies → "Franchise Pick," PT $100→$110** (2026-08-13/14) — sustainable bookings growth + U.S. AV scaling.
2. **Pony.ai 2,000-robotaxi European expansion + Hinomaru Kotsu Tokyo pilot** — AV aggregator model, not fleet-owner capex.
3. **Q2: FCF crossed $10B, gross bookings +22–24% YoY**; Q3 guide $0.84–$0.88 (+28–35% YoY) read as conservative → absorbed pullback.

**Catalyst Impact:** The market punished a "soft" guide while ignoring the structural shift to an AV-aggregator + $10B-FCF compounder. The Jefferies re-rating and AV expansion are the dated catalysts that justify defined-risk long exposure into the Sep macro window.

## THE THESIS
**Why this stock should move:** Uber is trading at a mid-teens forward P/E while printing record $10B FCF and executing a $14.8B Delivery Hero acquisition plus a multi-partner AV rollout. The "soft" Q3 guide triggered a pullback that has now coiled on the VP POC at $74.82 on sub-1.0 RVOL — supply exhaustion at a defended floor. The Jefferies $110 Franchise Pick and AV expansion are the re-rating catalysts. The mispricing is that the market is pricing a deceleration narrative while the cash-flow and AV-aggregator fundamentals are compounding.

## THE EDGE & ALTERNATIVE VIEWS
**The Bull Case (Pillars 2 & 3):** Buyers defend the $74.82 POC on exhaustion volume; Jefferies $110 + $10B FCF + AV expansion drive the next leg to the $80 call wall, then Darvas $78.99→$104 consensus.
**The Bear Case (Pillars 1 & 4):** Stage 5 age 1 + at-market R:R 0.31 + ADX 13.5 = choppy, no edge. The −0.6% retail sales print and Sep 4 NFP / Sep 11 CPI / Sep 17 FOMC cluster is a real macro headwind for discretionary consumer services. The $80 call wall (30,526) caps the near-term move.
**Portfolio Manager Resolution:** The indicator offers no directional edge, so I do NOT chase equity at a 0.31 at-market R:R. I take **defined-risk long delta** (Bull Call Spread) that captures the POC-floor defense + Jefferies re-rating while capping cost, sized LEAN (conviction 6) because the macro window is hostile and the indicator is flat.

## THE TRADE (MULTI-REGIME ACTION PLAN)

### Plan A: Direct Equity Base Swing (If Taking Shares)
| Metric | Price | Rationale |
| :--- | :--- | :--- |
| **Entry** | $74.80–$75.30 | Limit at the Long Entry Zone / VP POC floor (NOT at-market $75.91) |
| **Tactical Stop** | $73.35 | Tightly below MA50 ($72.84) and Long Stop ($73.40) — local floor, not distant box |
| **Target 1 (Trim)** | $76.75 | Long Target / Hull HMA / MA200 reclaim |
| **Target 2 (Runner)** | $78.99 | Darvas Box Top / $80 call wall |
| **Tactical R:R** | ~1.0:1 (T1) / ~1.9:1 (T2) | From $75.05 entry, stop $73.35 |
| **Allocation** | 25% (LEAN) | Code 8 baseline + Stage 5 + macro headwind = small size |

> **[M] Do not build the plan around a perfect pullback fill.** A limit resting at the prior bar's zone fills only 32.1% of the time. If the zone is not touched, Plan B (options) is the cleaner expression.

### Plan B: Defined-Risk Options Structure (Derivatives Strategy) — **PRIMARY**
*   **The Play:** **Sep 11, 2026 $79C / $105C Bull Call Spread** (28 DTE)
*   **Cost / Credit:** Net debit **~$1.42** ($142 max risk per spread) — from TradingView Strategy Finder
*   **Max Profit & Max Loss:** **Max Profit $438.70 | Max Loss $29.00 | R:R 8.57:1** (Finder) — note the Finder's max-loss figure reflects the short-leg credit; practical max risk is the $1.42 debit. Breakeven **$79.29**.
*   **Why this Strike & Expiry:** The $79 long call sits just below the Darvas Box Top ($78.99) and the $80 call wall (30,526) — the first real dealer resistance. The $105 short call is far OTM (captures the Jefferies $110 / consensus $104 tail). 28 DTE clears the Sep 4 NFP and Sep 11 CPI but **exits before the Sep 17 FOMC** and is 81 days from earnings — no earnings in the window. IV Rank 82.5% makes a debit spread (defined risk) preferable to a naked short-premium sale given the inflated ExpMove.
*   **Touch Probability:** At 1.5× ExpMove the measured P(UP touch) is ~15.6% (IV Rank >80 row, §17.1); the $79 strike is ~4% above spot, inside the 1.0× ExpMove band where P(UP touch) ≈ 32.7% — so the spread's breakeven ($79.29) is a realistic 21-day touch, not a moonshot.
*   **Strategy Finder Selection:** `Sep 11, 2026 | 28 DTE | Bull Call Spread | 79C / 105C | Max Profit $438.70 | Max Loss $-29.00 | R:R 8.57:1 | Breakeven $79.29 | Bid/Ask $0.18/$None | Spread 7.64%`

**Alternative (tighter, higher-probability):** **Sep 18, 2026 $77.5C / $80C Bull Call Spread** (35 DTE) — Max Profit $162.00, Max Loss $88.00, R:R 1.84:1, Breakeven $78.38. Lower R:R but the $80 short call is exactly at the dealer call wall, so it is more likely to be fully ITM if the POC defense holds. Use this if you want higher probability over maximum payoff.

### Plan C: Conditional Stalking Trigger (If Waiting for Confirmation)
*   **Trigger Level:** Buy Stop on a **daily close above $76.75** (Hull HMA / Long Target reclaim) — confirms the POC defense and breaks the MA200 rejection.
*   **Contingent Stop & Target:** Stop at $74.80 (below POC), Target at $78.99 (Darvas) / $80 (call wall). Only add equity on this trigger; otherwise stay in Plan B.

### Plan D: Disciplined SKIP (If Passed)
*   **Invalidation:** A daily close **below $73.40** (Long Stop / MA50) breaks the POC floor and the supply-absorption thesis → exit all long delta, no re-entry. A close below $70.34 (VP VAL) opens the $65/$60 put-wall void — full exit.

**Which SIDE of premium you are on is decided by `Energy IV Rank Pct`; the STRIKE is decided by `Exp Move Pct 21b` & GEX Walls.** IV Rank 82.5% > 80 favors premium selling, BUT ExpMove is inflated (13.33%, post-earnings HV20 spike) and the at-market long R:R is 0.31 — so I do NOT sell a naked call/put. I use a **defined-risk debit spread** with strikes anchored to the level ladder (Darvas $78.99 / $80 call wall), which is the honest expression when the ExpMove ruler is unreliable.

### Income & Management (If Holding 100+ Shares)
**NO covered call.** Stage 5 age 1 (fresh recovery), Code 8 baseline, and the one measured long edge (⚖️ R:R callout) is absent. Capping upside here is premature. If premium is required, a **cash-secured put at $70** (below VP VAL $70.34 and the $70 call wall, beyond 1.25× ExpMove) is the only defensible structure — you would be happy to own at $70. Do NOT sell a put above the POC.

**⚠️ THE IV-RANK TRAP:** IV Rank 82.5% makes a *fixed* %OTM strike more likely to be touched, but at a fixed multiple of ExpMove the touch odds are lower. Because ExpMove is inflated here, I anchor to the **level ladder** (Darvas $78.99 / $80 call wall), not to 1.25× ExpMove.

### If I'm Wrong
**Alternative view:** The −0.6% retail sales print and Sep 4 NFP / Sep 11 CPI / Sep 17 FOMC cluster could confirm a "stagflation-lite" de-rating of discretionary consumer services, breaking the POC floor. **Exit plan before max loss:** Close the spread if UBER closes below $74.80 (POC) — do not hold through the Sep 17 FOMC with the short leg at risk.

## CRITICAL EVENTS
| Event | Date | Impact | Plan |
|---|---|---|---|
| UBER Earnings | ~Nov 3, 2026 (81d) | HIGH | Clear — no position held through it |
| NFP | Sep 4, 2026 (21d) | MEDIUM | Watch consumer/labor; no action unless POC breaks |
| CPI | Sep 11, 2026 (28d) | MEDIUM | Spread expires same day — exit before |
| FOMC | Sep 17, 2026 (34d) | HIGH | Spread (28 DTE) already closed; no exposure |

## BOTTOM LINE
The indicator is flat (Code 8, Stage 5 age 1, at-market R:R 0.31) — it offers no directional edge, so I do not chase equity. The trade is carried by **Pillar 2** (POC floor defense on RVOL 0.49 exhaustion) + **Pillar 3** (Jefferies $110 Franchise Pick, $10B FCF, AV expansion), expressed as a **defined-risk Sep 11 $79C/$105C Bull Call Spread** because IV Rank is 82.5% and the ExpMove is inflated. The ONE thing that has to go right: UBER holds the $74.82 POC floor through the Sep 4 NFP and Sep 11 CPI, reclaiming $76.75 to put the $79 spread in the money before the Sep 17 FOMC. Size it LEAN (conviction 6) — the macro window is hostile and the indicator is offering nothing.

**Works cited (this session):** Jefferies $110 Franchise Pick & Pony.ai/Hinomaru AV expansion (TipRanks/Google-grounded, 2026-08-13/14); UBER Q2 FCF $10B / bookings +22–24% (Finnhub/Alpaca live, 2026-08-14); macro CPI 3.4% / retail −0.6% / FOMC Sep 17 (Google-grounded macro, 2026-08-14); options chain & Strategy Finder spreads (live `fetch_options_chain` + `scrape_tradingview_options_finder`, 2026-08-14); earnings 81d (deterministic `fetch_earnings_calendar`).