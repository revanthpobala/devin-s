# CSCO | $121.50 | August 5, 2026
**Bar close:** $121.50 (Data Window) · **Live:** $119.94 (Alpaca, ~19:41 UTC) · **Change (intraday):** −$2.50 (−2.02%); live is another −$1.56 below the close, suggesting continuation lower on Aug 6

## ⚡ TLDR / EXECUTIVE SUMMARY
**The Thesis in 2 Sentences:** The engine is explicitly **NOT triggered** — `Action Long Code = 8 (WATCH)` despite a 97.95 Buy Score — because the bar sits in **Stage 1 BASING** with **+29.15% extension above MA200**, both of which are measured-negative cells that the system uses to *suppress*, not to call entries. The bull case is built on ignoring what those numbers actually mean (Buy Score is non-selective; a high score in BASING is the most common combination on this list and historically loses), and the bear case correctly identifies extension, fresh bearish reversal patterns (`SWEEP_BEAR` + `TRAP_BULL` printed *today*), and a 6-day earnings binary — but takes no action either, and rightly so, because short codes 1/2 are structurally impossible.
**Verdict:** **SKIP** · **Conviction:** 2/10 (procedural — this is what the math returns when no setup exists)
**EARNINGS GATE:** **FAIL** (6 days to Aug 12, 2026 ER — binary event, no room for setup to play out)
**(If User Owns Shares):** **DEFENSIVE CC** — but bear in mind the stock is +29% above MA200, in BASING, and earnings are 6 days away. See options table at the end.

## 🛠️ DATA AUDIT (LITERAL VALUES)
*Verbatim from the Data Window. NOT from the chart image.*

*   **Action codes:** Long = **8 (WATCH)** — **NOT triggered**. Short = **10 (WAIT)** — also not triggered. Per the bible, **PRIME/ACTION SELL is structurally impossible** in this codebase (every qualified short is demoted to WATCH); short codes 1/2 will never fire. Short conviction here would only be in Sell Score + Sell Sigma Evidence + Short In Zone, but Sell Score is 26.4 vs Buy 97.96, so the short side is genuinely weak.
*   **Stage / Age:** Stage = **1 (BASING ⏳)**, Stage Age = **5 bars** (very fresh). Per §16.2: **WATCH in Stage 1 = −0.34% [−0.65, −0.06] SIG** — basing is not opportunity. Per §16.8: age ≤5 is also a SIG-negative refinement for the PASS rule (−0.57% SIG).
*   **Scores:** Buy Score **97.96** ▬, Sell Score **26.36** ▬. **Buy Sigma Evidence = 5.34σ** — this is one of the genuinely evidence-driven reads on the panel (high score with high sigma is not just prior). **However**, a high Buy Score in a WATCH bar with Stage 1 is the system's normal condition, not a discovery (Buy Score ≥70 on 65% of all bars).
*   **Trade geometry:** `Entry At Market = 0` (structural). Dominant side is long (97.96 vs 26.36), so `RR To Target = 1.64` is the **zone** ratio (long). `Long RR Valid = 1`, `Short RR Valid = 1`. **Zone does exist**: Long Entry Zone Bot 116.18, Top 117.55. **But price is ABOVE the zone** — `Long In Zone = 0` — so this is a chased/stalked bar (Row 8 reads WATCH on purpose; the zone is the pullback, not the entry today).
*   **Extension:** `Ext Pct vs MA200` = **+29.15%** — this is **IN THE 25–60% EXCLUSION BAND**, which §16.7 measures at **−0.71% [−1.13, −0.32] SIG** and tags as "the most robust exclusion in the system". `Ext Z Self Relative = 0.66σ` (modest, not extreme), `Exhaustion Gradient = 0.146` (sub-0.30 territory; not terminal climax).
*   **Regime / MTF:** Regime = **0 (Healthy)** — but Regime is a priority enum; the Stage read separately is **Stage 1 BASING**, which is what governs. **MTF Long Aligned = 2/3** — this is the system's best measured cell (§16.6: +0.32% SIG, +0.46% SIG with Buy Sigma >5; this bar's sigma is 5.34 so the 2/3 cell qualifies). However MTF alignment *cannot override* the stage, extension and action-code realities.
*   **Rev Zone:** Long = **7.5** (Zone 1, 7–9 band — "Strong"); Short = **6.5** (Zone 1). Not Zone 0, so not extreme; not a Code-20 candidate (which needs Buy Score < 30, and this bar's Buy Score is 97.96).
*   **Energy / DMI:** `Energy State = 0 (DORMANT)`, `IV Rank = 28.97%`, `IV−HV Spread = −39.92` (IV depressed vs realized). `ADX 14 = 16.46` (in the 15–18 half-size chop zone). `DMI +DI = 24.41`, `−DI = 18.46` — +DI leading but not strongly.
*   **Volume profile:** `VP POC = 77.89`, `VAH = 116.17`, `VAL = 72.75` — these are anchored far below price. Per §8.2 VP fields populate on **exactly 1 bar per ticker** (live-bar gated) so this is current; the populated levels are far away and **HVN Above = ∅** (no historical reservoir above to absorb supply). `HVN Below = 119.03` — very close, this is the recent consolidation floor from the chart. `RVOL Vs Avg = 0.88` — *below average*, not the >1 minimum for a recovery bar.
*   **Fresh labels:** Bear Warning Mask = **3** (TOP + RSI_CASCADE — both bearish, Age 18). Reversal Pattern Mask = **72** with **Age 0** — `SWEEP_BEAR (8)` + `TRAP_BULL (64)` both just fired today. **Net: 0 bullish vs 2 bearish reversal patterns, fresh today.** Weak Level Mask = 0.
*   **Next earnings:** **Aug 12, 2026** (6 days remaining). Source: yfinance (deterministic, ground-truth per system rule — overrides the TipRanks snippet that said Aug 19).

## 📐 CALIBRATION DISCLOSURE
The firing state is `Action Long Code = 8 (WATCH)`. WATCH measures **−0.06% ex21** flat (62.7% of all bars — it IS the baseline, not a signal). Disqualifying layers underneath it: WATCH in Stage 1 is **−0.34% [−0.65, −0.06] SIG negative**, and Ext Pct +29.15% (in the 25–60% band) is **−0.71% [−1.13, −0.32] SIG negative**. None of those intervals excludes zero in the direction of a long. **There is no non-indicator pillar here strong enough to lift a verdict above SKIP.** The UBS earnings-beat expectation is a one-line sentiment snippet, not a dated catalyst; until the actual print, you are buying 6 days of binary-event exposure into a Stage-1 basing with a stretch that historically reverts. If you can name a dated catalyst and verified flow that converts this into a directional edge, then it is worth revisiting; absent that, the trade is mediocre and the verdict must remain SKIP.

---

## THE SETUP
**What the state shows.** The bar closed at $121.50, ABOVE the long entry zone of $116.18–$117.55 (Long In Zone = 0). The zone exists and is structurally sound, but price has not returned to it. The action that fired is WATCH — the standard "no entry right now, stalk the zone" read. Critically, the Stage is BASING (Stage 1), not ADVANCING (Stage 2); the trend is unwinding, not trending. Extension is +29% above the 200-day, inside the historical exclusion band. ADX 16.46 is too low to break out cleanly; RVOL 0.88 is below average. Energy is DORMANT. The only positive note is MTF alignment at 2/3 with Buy Sigma 5.34σ — the system's best measured cell. But MTF alignment cannot override the Stage and Extension exclusions.

The Aug 5 print itself was violent: opened $124.00, **dropped to $114.88 (-7.4% intraday)**, recovered to close $121.50 — a long-tailed wick down. That is exactly the kind of bar that prints fresh reversal patterns, and the engine logged `SWEEP_BEAR` + `TRAP_BULL` today (Reversal Pattern Age = 0).

```
                            (Long Target: $122.33)
                                      ▲
                                      │
                 ┌────────────────────┼──────────────── KEY RES 125.96
                 │                    │
   ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ Weinstein MA150: 126.22
                 │                    │
                 │   ▲ +29% above     ├──────────────── EXCLUSION BAND
                 │     MA200          │                  (25-60% measured
                 │                    │                   −0.71% SIG)
                 │              Spot: $121.50            ◄ today
                 │                    │
   ══════════════╪════════════════════╪═══════════════ Hull Baseline: 117.06
                 │ ┌──────────────────┤
                 │ │ Long Entry Zone  │
                 │ │   116.18-117.55  │   ← zone below; price chased above
                 │ └──────────────────┤
                 │                    │
                 │ Long Stop: 114.10  │
   ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ MA 20 Fast: 115.82
                 │                    │
                 ▼                    ▼
          SUPPORT FLOOR            TREND BASE
          (52W low 65.75)          (MA 200: 94.08)
```

**What the image shows.** Massive intraday volatility, multiple warning labels freshly clustered (TOP WARNING, RSI CASCADE, EXTREME EXTENSION, KEY RES), failed BULL TRAP label from prior week, and a long-tailed wick today. The structural read on the chart matches the Data Window: basing with stretch, not advancing. The image is consistent with the WATCH verdict — the chart itself is not arguing for an entry.

**Macro/Policy context.** Fed Chair Warsh held the funds rate at 3.50–3.75% in the July 29 meeting (9–3 vote) with markets assigning a ~30% probability to a September hike. 10-Year yields have eased back as oil prices have pulled lower on Iran de-escalation hopes. The "rate path" read is restrictive-but-not-getting-worse; CSCO is a moderate-duration tech name (Forward PE 24.4, but Trailing PE 40.6). No Section 301 or tariff shock specifically targets CSCO at the moment — the system policy override is OFF. EARNINGS on Aug 12 dominate everything; this is a binary event window.

---

## THE THESIS
**Why this stock should move.** In a vacuum: AI-infrastructure spending, Splunk integration ramp, expected AI-driven networking backlog growth, and a Q4 FY2026 print on Aug 12 that UBS expects to be a beat-and-raise. The narrative is real. But the **timing** is the issue — you are 6 days from binary resolution with the indicator on WATCH in BASING with extension at the historical reversion threshold. The narrative needs another few weeks to mature, OR a fresh basing-resolution event, before it becomes tradeable on this system.

**Why NOW is wrong.** The Aug 5 reversal patterns + extension + Stage 1 BASING is precisely the configuration the system tells you to stay out of. Even if the story is correct, the *entry* is wrong.

## THE EDGE
**There is no edge I can name in this session that survives the indicator's measured exclusions.** I have:
- A UBS earnings-beat expectation (sentiment, not dated),
- AI infrastructure narrative (sentiment),
- No verified flow signal (Schwab auth failed on `fetch_unusual_options_flow`),
- A 30.7% sell target median vs live spot of $119.94 is upside, but only ~10% to a target which doesn't exceed the binary-event uncertainty,
- No policy tailwind or Section 301 / Fed override applicable.

A real edge would be: a fresh basing break above the zone WITH volume (RVOL > 1.5), stage rolling to ADVANCING, and extension pulling back below 25% — *none of which are true today.*

## THE RISK
- **Primary risk:** Earnings miss / "sell the news" on Aug 12 with the stock trading at +29% above MA200 — measured expected 21-day move is +10.14%, which on $121.50 suggests a $109–$134 1-sigma band. A sub-consensus print into that stretch could easily tag the long stop at $114.10 from here.
- **Event risk:** Six days is inside the binary window. The bible rule is "never hold through earnings unhedged" — and 6 days is exactly that window.
- **Technical risk:** Continued basing resolves in one of two directions; a downside break of $114 stops out long entries and reverses the trend. An upside break needs volume; we don't have it.

---

## ⚖️ LOCAL RESEARCHER DEBATE
**Moderator Consensus:** Both researchers overstate their conviction. Neither addresses what the indicator's code actually says — that Action = 8 (WATCH) is a *deliberate* output, not a missing signal. The debate to settle is whether the narrative gap (UBS earnings beat, AI infra) can lift a measured-null/measured-negative setup, and the answer is no, because there is no dated catalyst today (only an expected one in 6 days).

**Cross-verification performed:**
- **Earnings date conflict:** yfinance (deterministic) says **2026-08-12 (6 days)**; the local TipRanks snippet said "Aug 19". Per the system rule "trust this over anything found via search/grounding", I go with Aug 12. Confirming: the local research note says "Aug 19" too — that is the stale cached data. Treating Aug 12 as the operative date.
- **CNN "$121.74 / +5.08%" headline:** Inconsistent with the Data Window OPEN $124.00 / CLOSE $121.50. The CNN data is likely a prior-session figure or a scraper artifact; the Data Window is the bar of record. I treat the Data Window as gospel.
- **"Financhill $0.08 in 4 weeks":** Hallucination/gibberish from a low-quality aggregator. Cisco has a $479B market cap; $0.08 is structurally impossible and not worth refuting further. The Bull Rebuttal correctly tags it as noise.
- **"Forward PE 24.4 is reasonable":** True relative to MegaCap Tech, but the trailing PE of 40.6 reflects the post-Splunk integration ramp and one-time costs; not the metric I'd headline as bullish.
- **Both researchers lean on "Buy Score = 97.95" as primary conviction.** §16 of the bible explicitly flags this as a **trap**: median Buy Score is 85.3, Buy Score ≥70 on 65% of all bars, and a high score in WATCH is the *baseline*, not a buy signal. The bull case's central pillar is therefore exactly what the system tells you to discount.
- **Bear correctly identifies the Stage 1 BASING + extension + fresh bearish reversal-pattern stack.** Bear overstates the case (the bullish wick-recovery on Aug 5 implies some absorption is happening, and Buy Sigma 5.34σ is real evidence), but the structural exclusions are right.

**Resolution:** The narrative is bullish; the indicator is not triggered and measures negative at this exact configuration. The cleanest expression is SKIP — let the earnings event pass, then re-evaluate after the print when the basing resolves one way or the other and extension normalizes.

---

## COUNTER-TREND ANALYSIS
| Check | Finding |
|---|---|
| REV ZONE status | Long = 7.5 (Zone 1, Strong), Short = 6.5 (Zone 1). Not extreme. |
| **Is it `Action Long Code = 20`?** | **NO** — Buy Score is 97.96, not the <30 required for REVERSAL BUY. The mean-reversion tier is active but the capitulation code is not. |
| MTF alignment | 2/3 (best measured cell) — but cannot override stage / extension. |
| In Zone? | No (Long In Zone = 0) — irrelevant for code 20 since the in-zone half is the *worse* half, but the code isn't active anyway. |
| Key triggers | The Aug 5 long wick down to $114.88 (-7.4%) prints as a capitulation-shape bar but the close at $121.50 wasn't in the bottom 25% of the day's range (range $9.33, threshold = $117.83; close was above it) so the §9.1 25%-close-location KILL FILTER probably zeroes most of the score. Hence Long Rev Zone = 7.5, not deeper. |
| ACTION conflict | None — Action Long Code = 8 (WATCH), not a 🛑 danger state, but Action Long Code ≠ 20 either. |

**Reversal Thesis:** None warranted here. The setup isn't a counter-trend capitulation (Buy Score isn't <30), and the bar shows absorption but not the *close in the bottom quarter of range* that the §9.1 kill filter requires for a Z0-grade reversal. If a future bar prints below MA200 with a sub-$115 close and Buy Score <30 on a >1.5σ volume spike, then it would become a Code-20 candidate; today is not that.

---

## CONVICTION: 2/10
**Because:** Conviction is procedurally low because the indicator outputs are non-actionable. The two cells that would lift this — a non-indicator pillar and clean event risk — are both absent. Earnings in 6 days FAIL the event-risk gate, the extension band measured-zero-mean the long side, and BASING is structural-meant-reversion-not-opportunity. 2/10 is not "I'm bearish" — it's "the math returns zero" — and the right action is SKIP, not engineer an opposing bet on a name without a short code that's allowed to fire.

## THE TRADE

### Stock
NO trade. Skip the stock entry.

For completeness, here is the geometry the engine exports — but DO NOT enter on it:

| | Price | Rationale |
|---|---|---|
| Entry (zone fill) | $117.22 | Long Entry — only IF price returns to zone (currently $119.94 live, $121.50 close) |
| Entry (at-market) | $121.50+ | Chased; inadvisable |
| Stop | $114.10 | Structural (Engine export, below MA20/Zone Bot, capped at 5%) |
| T1 / Target | $117.28 / $122.33 | T1 = AVWAP-style first wall; Target = VP VAH-extension zone |
| R:R | 1.64 (zone) | Structural read, but measured-return on a Stage-1 Watch bar is SIG-negative |
| Size | **0%** | WATCH in Stage 1 + Ext in 25-60% exclusion band — both disqualify |

> **[M] A limit at $117.22 fills ~32% of the time and earns 0.00% date-neutral.** Even when the pullback actually shows up, it doesn't pay. Add the Stage 1 BASING and the extension exclusion and the math only gets worse.

### Options

The earnings print 6 days out makes unhedged long calls unattractive. Two ways to engage:

**Option A — DEFINED-RISK LONG, post-earnings survivor only (Aug 21 expiry, 15 DTE post-print)**
- **The Play:** Sell the Aug 14 cash-secured put or buy a debit call spread post-earnings. Until the print, **no long calls** — IV Rank 28.97% is low but the binary event gap dwarfs any time premium you can collect.
- Specifically, if you must play the pre-print window: a **$120/$125 bull call spread (Aug 21)** — long Aug 21 $120 call @ $6.44 mid, short Aug 21 $125 call @ $4.11 mid, net debit ~$2.33. BE $122.33. Max profit $2.67. R:R ≈ 1.15:1 (small). **Half position, 1 contract max.** Even this is hard to recommend.

**Option B — SELL PREMIUM into earnings (existing-shareholders only)**
- Aug 14 $130 call (above the consensus $131.36 target, leaving room for upside) ~$2.64 mid. 30+ DTE-out preferable, but Aug 14 is the available post-earnings contract.
- Or Aug 21 $135 call @ $1.60 mid (well above measured +1σ 21-day expected move ceiling).
- See Income & Management section below.

### Income & Management (For 100+ Share Holders)

CSCO's posture here is unique: it is technically bullish (high Buy Score, MTF 2/3, Sigma 5.34σ) but structurally vulnerable (BASING, +29% extension, earnings 6 days). The right CC stance is **defensive** — collect premium ahead of earnings without capping genuine upside if the print is a beat-and-raise.

**The 6-day binary dominates.** The earnings on Aug 12 will resolve the basing. Selling a CC inside the expected-move band into a binary event is *gambling on the direction of the print*. Selling it above the expected-move band is *correct premium selling*.

```
                          (CSCO Live Spot: $119.94 / Aug 5 Close: $121.50)
                                       │
                  ┌────────────────────┼─────────────────── Bias: +29% MA200, BASING
                  │                    │
                  │                    ▼
                  │           +1σ 21-day expected move: ~$134
                  │                    │
                  │                    │
                  │ ┌──────────────────┴──────────────────┐
                  │ │  SELL  Aug 14 $130 Call (≈ $2.64 mid) │
                  │ ├────────────────────────────────────────┤
                  │ │   Strike sits ~$10 above the spot.     │
                  │ │   Sits above +1σ 21-day expected move. │
                  │ │   Captures premium without capping the │
                  │ │   typical 10% ER-day move.             │
                  │ └────────────────────────────────────────┘
                  │                    │
                  │                    ▼
                  │           $135 is the full 1σ 21-day band
                  │           — Aug 21 $135 @ $1.60 mid is the
                  │           cleaner pure-premium play
                  │           with 30+ DTE and wider safety
                  └─────────────────────────────────────────────┘
```

| State | Strategy |
|---|---|
| 🚀 ROCKET / healthy Stage 2 | **HOLD — no CC.** Cap would be wrong here, but we are NOT in Stage 2 |
| Rev Zone 0/1 short side | **AGGRESSIVE CC** — not applicable, no short reversion here |
| Rev Zone 2 / chop | **STANDARD CC** — OTM delta ~0.30 at resistance (this state, partially) |
| 🛑 Breakdown | **EXIT SHARES** — do not sell CC into a collapse |
| 🛑 TOXIC, extended | **DEFENSIVE CC** — deep OTM cushion (this state — **USE THIS**) |

**Recommended CC:** **Aug 21 $135 Call** at $1.60 mid, OR **Aug 14 $130 Call** at $2.64 mid (tighter, more premium, but closer to the Aug 12 binary).

**Rule:** Do not sell a strike inside the +1σ 21-day band ($132–$134 implied). The 30–45 DTE rule is uncomfortable this close to a print; Aug 14 and Aug 21 are the available dates and both pre-expiry the second is preferred for cleaner theta.

**⚠️ COVERED CALL EXCEPTION** — TOXIC RISK blocks directional stock and option entries. Selling premium on shares already held is permitted. Even though the action code is WATCH (not TOXIC), the same principle applies: stay OUTSIDE the expected-move band, and let earnings resolve first.

---

## If I'm Wrong
**Alternative view (bull):** If you genuinely believe UBS's earnings-beat expectation has a >70% probability of hitting, you could construct a defined-risk pre-print position (Aug 21 $120/$125 call spread, $2.33 debit, 15 DTE, 1.15:1 R:R) as a non-directional vol play. The expected move is 10% — a 5% post-print move is inside the band and this trade pays; a 0% move loses the debit; a 10% move hits max. **Trade size: 1 contract max, treat as a coin-flip with positive variance.** Even so, I would not size this as a conviction long — the indicator is not corroborating.

**Alternative view (bear):** If you believe BASING will break down, the engine doesn't support it — short codes 1/2 are impossible. You'd have to enter the at-market short via raw Sell Score / short zone logic, with Entry At Market 2 (the SIG-positive at-market short lane at +0.29% [+0.11, +0.47]). That's structurally allowed, but the short zone is $121.81–$122.85 (above the current price), and you are entering at $119.94 / $121.50 into a $121.81 stop. I would not trade this without a confirming bar.

**Exit plan before max loss:** If you hold shares and the Aug 5 long-wick pattern repeats below $114.10 (the long stop / measured structural floor), close the long. If you short at the zone with a stop above $124.93, accept that level. **Position size in both cases ≤ 25% of normal.**

## CRITICAL EVENTS

| Event | Date | Impact | Plan |
|---|---|---|---|
| CSCO Q4 FY2026 Earnings | **Aug 12, 2026** (6 days) | EXTREME — binary | NO pre-print long exposure. Re-evaluate Aug 13. |
| US CPI / Inflation Release (July 2026 print) | Watch for the next BLS release (system date is now Aug 6, the next print is mid-August — verify externally) | MEDIUM-HIGH | Tighten stops on existing exposure; do NOT initiate ahead of this either. |
| FOMC Rate Decision | Next meeting likely mid-September (verify externally) | MEDIUM | No acute impact inside the 6-day window. |
| LA28 / Olympics partnership | Ongoing | LOW | Pure narrative; ignore for sizing. |

## BOTTOM LINE
CSCO is the textbook case where the **indicator tells the truth twice and the analyst fights it twice**. The action code is WATCH — not because the score is low, but because the **stage (BASING)** and **extension (+29%)** are disqualifying even at a 97.95 Buy Score. The narrative ("AI infrastructure king", UBS expecting a beat) is real but the **timing is wrong** — we are 6 days from a binary print, on top of a basing structure at the historical reversion threshold. The right action on the long side is **do nothing**; on the share-holder side, the right action is **a defensive covered call above the +1σ 21-day band** that lets earnings resolve the basing without capping a genuine beat. Skip the stock. Re-evaluate August 13.