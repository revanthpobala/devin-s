# HOOD | $93.47 | August 11, 2026
**Bar close:** $94.40 (Data Window) · **Live:** $93.47 (Alpaca, 11:02 ET) · **Change:** -$1.02 (-1.07%) from prior close $94.49

## ⚡ TLDR / EXECUTIVE SUMMARY
**The Thesis in 2 Sentences:** Despite the fresh August 9 Golden Cross and the UK crypto launch this week, the engine remains in **Stage 1 BASING** with `Action Long Code = 8 (WATCH)` — not triggered, baseline state — and the short side carries an exit-only TOP/BOT WARNING (Code 15). Volume is structurally dead (RVOL 0.19 = 19% of average) on a $93 stock with HV20 of 59%, the DMI is bearish, and the stock just lost the EMA20 today. The bull narrative is real but unconfirmed by the structural read; the trade is not constructible from this state.

**Verdict:** SKIP · **Conviction:** 1/10
**EARNINGS GATE:** PASS (85 days | ~Nov 4, 2026)
**(If User Owns Shares):** DEFENSIVE COVERED CALL — the TOP/BOT WARNING (Code 15) is an exit-style signal.

## 🛠️ DATA AUDIT (LITERAL VALUES)
*Verbatim from the Data Window. NOT from the chart image.*

*   **Action codes:** `Action Long Code = 8` (WATCH — catch-all "no entry right now", 63% of bars; **NOT triggered**). `Action Short Code = 15` (TOP/BOT WARNING — an exit instruction for stretched names that subsequently revert; **NOT a short trigger**). The asymmetry here is by design: `Action Short Code` can never be 1 or 2, so any short-side read must come from Sell Score + zone stats — and Sell Score 60.2 is only moderately bearish, well below the threshold that would constitute a high-conviction short.
*   **Stage / Age:** `Stage = 1` (BASING) · `Stage Age Bars = 0` — first bar of the new stage. The HMA150 just rolled: MA50 (95.65) crossed MA200 (92.66) for the Golden Cross per news on Aug 9, but the HMA150 hasn't confirmed Stage 2 yet. Per §16.2: **WATCH in Stage 1 = −0.34% [−0.65, −0.06] SIG**.
*   **Scores:** Buy Score 52.87 (mediocre, below the 85 PRIME threshold) · Sell Score 60.21 (moderate, dominant side). `Buy Sigma Evidence = +1.39σ` (weak positive) · `Sell Sigma Evidence = −0.60σ` (slight negative). The Buy Score is NOT prior-driven; sigma is +1.39σ, but the score is still under 60. With Sell Score dominant, the `RR To Target = 2.025` is the **short-side** ratio (not the long).
*   **Trade geometry:** `Entry At Market = 0` (structural). `Long Entry = 93.29` · `Long Entry Zone = 92.61–93.97` (live price 93.47 is **inside the long zone**). `Long In Zone = 0` (strict on the breakout side — bidirectional tolerance not binding). `Long Stop = 89.88` · `Long Target = 101.88` (T1 waypoint = Target = 101.88; no partial trim wall). `Long RR Valid` — not explicitly exported but the RR Flags Pack = 12, which decodes to the field's structural setting.
*   **Extension:** `Ext Pct vs MA200 = +1.87%` (below the 25–60% exclusion band, but the "buy below MA200" effect doesn't survive the $20 filter per §16.7). `Ext Z Self Relative = −0.12σ` (not stretched). `Exhaustion Gradient = 0.0094` (very low — far from climax).
*   **Regime / MTF:** `Regime = 0` (Healthy) but **Stage 1 BASING** — the priority enum hides the structural reality. §16.3: Regime 0 + Stage 2 measures −0.17% (negative lean); this is the *worse* Regime 0 + Stage 1. **MTF Long Aligned = 0/3** — no timeframe confirms the long.
*   **Rev Zone:** Long Rev Zone = 3.0, Short Rev Zone = 3.0 — **both below the Z2 threshold (4+)**, no active reversal zones.
*   **Energy / DMI:** `Energy State = 2` (WARMING) · `IV30 = 65.22` (Rank 38.9%) · `IV−HV Spread = +9.92` (consistent with WARMING). `ADX 14 = 20.39` (weak trend) · `DMI +DI = 19.65 / −DI = 24.44` — **bearish directional**. `HV20 = 59.34%` (extremely high realized vol).
*   **Volume profile:** `VP POC = 76.52` · `VAH = 94.04` · `VAL = 69.17` · `HVN Below = 91.22` (live price 93.47 is just above VP VAH 94.04… wait, 94.04 > 93.47, so price is *below* VAH). `RVOL Vs Avg = 0.1947` — **19% of average volume**.
*   **Fresh labels:** `Bear Warning Mask = 0` (clean). `Reversal Pattern Mask = 576 = 512 + 64` → **HIKKAKE_BEAR (Bearish) + TRAP_BULL (Bearish)**, both 18 bars old → both **STALE** (presence is uninformative at 18 bars; rank by freshness, not presence). `Weak Level Mask = 0`.
*   **Next earnings:** ~85 days (Nov 4, 2026) (Source: yfinance).

## 📐 CALIBRATION DISCLOSURE (MANDATORY)
The firing state is `Action Long Code = 8 (WATCH)`, measured at **−0.06% [−0.34, +0.15] flat** — the interval straddles zero, so the code is a description, not a rule. The relevant refinement is **WATCH in Stage 1 = −0.34% [−0.65, −0.06] SIG negative**, the measured live read for this exact state. `Action Short Code = 15` (TOP/BOT WARNING) measures +0.32% [−0.01, +0.67] — just misses significance, and is an exit instruction, not a short trigger. **No measured cell is positive here.** The only non-indicator pillars available are the Aug 9 Golden Cross and the UK crypto launch — both real but unconfirmed by the structural read (Stage 1 BASING, MTF 0/3, bearish DMI, dead volume). The verdict is SKIP because conviction requires a pillar that lifts the trade above mechanical "indicator state", and the available pillars here are not yet integrated into the engine's stage or score.

## THE SETUP
The daily chart shows HOOD in a structural decline from its April 2026 highs above $120 to current levels near $93. The short-term moving averages (MA20/MA50 cluster at ~$95.65) sit overhead as resistance, and price is trading below them. The MA200 (92.66) is right at the live price — the floor pivot. The Weinstein MA150 (Hull, 103.37) is well above price and is still declining, confirming the engine's **Stage 1 BASING** classification. Volume is structurally dead: only 19% of the 20-day average.

```
                            (Weinstein MA 150: 103.37) ── STAGE 1 BASING
                                       │
                                       │  ← Key Resistance 101.88
                                       │
                                       │   ← MA20/MA50 cluster: 95.65
                                       │
        ┌──────────────────────────────┼─────────────────────────┐
        │  Long Entry Zone: 92.61–93.97│  ← Live Price: 93.47    │
        └──────────────────────────────┼─────────────────────────┘
                                       │
                                       ▼
                              (Hull HMA: 89.82 ← KEY SUPPORT 88.41)
                                       │
                                       ▼
                              (MA 200 Slow: 92.66 ← price just above)
                                       │
                                       ▼
                              (VP HVN Below: 91.22)
                                       │
                                       ▼
                              (VP VAL: 69.17)
```

**Macro context:** The Fed is widely expected to hold rates at 3.50–3.75% at the next FOMC, with a 30% probability of a hike per the July 2026 dot plot. CPI is forecast to ease to 3.8% YoY in June 2026 — still the highest since April 2023. The policy backdrop is **hawkish-leaning** with continued pricing uncertainty. For HOOD specifically, **high realized volatility (HV20 59.34%) is a feature when the company is monetizing options flow** — but the Beta of 2.32 means the stock amplifies any market drawdown. The current climate is not a tailwind for a stock with a P/E of 41× and Forward P/E of 44×.

**What the image shows:** Bounce off the MA200 attempted today (94.40 close, 93.47 live) but failed to hold above the MA20/MA50 cluster. The Hull MA (89.82) is the structural support; failure there targets the AVWAP Support at 85.35. The recent Golden Cross (Aug 9) is fresh but the structural MA (Hull 150) hasn't confirmed — the engine is still showing Stage 1 BASING. Warning labels are stale (18 bars old).

## THE THESIS
HOOD sits at a genuine inflection point: the **Golden Cross** (MA50 > MA200) is the first real long-term bullish signal since January 2024, and the **UK crypto launch** via Bitstamp with zero fees is a tangible revenue diversification event. But the engine has not yet processed these into Stage 2 — the HMA150 is still declining, the Stage just rolled to 1 BASING today, and the structural read is "transition, not trend". Mechanical entry is not available; the closest substrate is a stage 1 base forming, which on the measured data is **−0.34% SIG negative** for WATCH.

Why this stock should move (if it does): the UK launch is a real revenue catalyst that could materialize in 1–2 quarters of accounts. The crypto/options retail flow engine is intact. The pipeline of operating leverage from tighter expense guidance ($2.675–2.775B) supports multiple expansion if growth sustains.

## THE EDGE
**What I know that the market might be missing:** The Golden Cross is only 2 trading days old (Aug 9). Historically, post-Golden-Cross entries in Stage 1 BASING have a structural read but the change is fresh — the classic "too early to enter, but the pattern is real" state. The UK launch is a 50-asset crypto rollout via Bitstamp, which is differentiated from a simple licensing arrangement. The analyst consensus target of $120.52 (17 analysts, 12-month) implies ~28% upside. **However:** none of this rises to the non-indicator pillar threshold required to override the Stage 1 BASING negative read. The structural read is "don't enter yet". The pillar is real; the timing is not.

**Counter-research from the LIVE tools:** The Aug 9 Golden Cross claim is cross-verified by the news feed (first occurrence since January 2024). The UK launch is confirmed ("live rollout as of August 2026"). Sentiment is "cautiously optimistic" — retail investors remain skeptical ("stands by bearish bet"). The local-researcher bull case (UK launch + $120 target + fiscal discipline) is interpreting the same data, but the engine has not been persuaded and the structural read remains Stage 1 BASING.

## THE RISK
- **Primary risk:** The structural downtrend continues. The HMA150 (103.37) is still declining and the price is below it. A daily close below the Hull HMA (89.82) and the KEY SUPPORT (88.41) invalidates the basing thesis and targets the AVWAP Support at 85.35, then the VP HVN Below at 91.22 (already broken on the live print).
- **Event risk:** Earnings (~85 days, Nov 4) — the company has guided operating expenses tighter, but Q2 EPS was "boosted by one-offs"; Q3 read will be a clean test. Beta 2.32 means any market drawdown amplifies.
- **Technical risk:** RVOL 0.19 means no institutional accumulation. The bearish DMI, the Sell Score dominance, and the just-lost EMA20 (Trend Bars Up = 0) all suggest momentum is fading on the wrong side. The TOP/BOT WARNING on the short side is an exit-signal — the stock had a major run-up earlier in the year (from $63 52-week low to $153 high) and the engine is flagging distribution risk.

## ⚖️ LOCAL RESEARCHER DEBATE
**Moderator Consensus:** The local researchers split into two mutually-exclusive camps. The BULL case argues the golden cross + UK launch + Q2 record revenue + lowered expense guidance + favorable analyst targets ($120 avg) constitute a structurally bullish setup; the BEAR case argues the trend is broken, the stock is in distribution (DI- > DI+, RVOL 0.19), the Q2 EPS was boosted by one-offs, and the headwinds (sticky inflation, potential rate hike, valuation contraction) are bigger than the catalysts. **My verdict as Judge:** Both sides have merit on the facts. The Golden Cross is real and cross-verified. The UK launch is real. The bearish technicals are also real. The deciding factor is the **timing** — the engine has not yet integrated these into Stage 2; the structural read is still Stage 1 BASING. Until the HMA150 confirms Stage 2 (which requires more bars and a structural breakout above the MA20/MA50 cluster at $95.65), the mechanical entry is not available. **The trade is too early.** The bull case is structurally valid but chronologically misplaced relative to the engine's stage read.

**Cross-verification of facts:** Aug 9 Golden Cross confirmed by news feed (first since January 2024). UK launch details (Bitstamp, 50+ assets, zero fees) confirmed multiple sources. Analyst target $120.52 confirmed (MarketBeat, 17 analysts). Q2 revenue $1.31B confirmed (Simply Wall St). Expense guidance $2.675–2.775B confirmed (Yahoo Finance). Beta 2.32, P/E 41.28, Forward P/E 44.44 confirmed (fundamentals). **No hallucinated claims detected.**

## COUNTER-TREND ANALYSIS
| Check | Finding |
|---|---|
| REV ZONE status | Long 3.0 / Short 3.0 — both below Z2 threshold (4+), **no active reversal zones** |
| **Is it `Action Long Code = 20`?** | **NO** — Action Long Code is 8 (WATCH). The reversal-buy lane is closed. |
| MTF alignment | 0/3 — no alignment, but for Code 20, MTF 0/3 is the *best* subset. Not applicable here. |
| In Zone? | N/A — no Code 20 today |
| Key triggers | N/A — no RSI(2) extreme, no failed-sweep, no setup present |
| ACTION conflict | Top Warning (Code 15) is exit-only, not a short trigger |

**Reversal Thesis:** No reversal-buy setup is present. The Long Rev Zone score of 3.0 is below the Z2 threshold (4+); the trap-bull pattern is 18 bars old (stale); no failed-sweep or OOPS signals in the mask. This is not a counter-trend setup.

## CONVICTION: 1/10
**Because:** No actionable entry code on the long side (8 = WATCH baseline). No actionable short trigger (Code 15 is exit-only). The Stage 1 BASING structural read is **measured −0.34% SIG negative**. The only non-indicator pillars available (Golden Cross, UK launch) are real but unconfirmed by the engine's stage. **The indicator is not triggered, and the only available pillars are insufficient to lift the trade above mechanical skip.** This is a deterministic skip — the discipline is the answer.

## THE TRADE
**No trade construction.** The state is not triggered for long; the short side is exit-only. The setup is for patience, not for entry.

### Stock (If Hypothetical Trade Were Constructed)
| | Price | Rationale |
|---|---|---|
| Entry | $93.29 | Long Entry — live price 93.47 is inside the long zone, but state is WATCH |
| Stop | $89.88 | Just below the Hull HMA (89.82) |
| T1 | $101.88 | No partial-trim wall (T1 = Target) |
| Target | $101.88 | Long Target |
| R:R | 2.03:1 | DOMINANT SIDE is short (Sell Score 60.21 > Buy Score 52.87), so this is the short ratio — **not a long recommendation** |
| Size | 0% | State not triggered |

**If, hypothetically, the Stage 2 confirmation arrives** (price closes above MA20/MA50 at $95.65 with RVOL > 1.0), revisit. Until then, no trade.

### Options
**No options position recommended.** The state is not constructible:
- **Long calls:** Buying calls against a structural basing + bearish DMI + dead volume is buying into a falling knife. IV Rank 38.9% is moderate; not cheap enough to justify a long premium.
- **Long puts:** A short position is not warranted (Code 15 is exit-only, not a short trigger). Buying puts against the Golden Cross catalyst is fighting the cross.
- **Spreads:** Not justified without a directional conviction.

### Income & Management (If User Owns Shares)
The TOP/BOT WARNING (Code 15) on the short side is an exit-style instruction. Stage 1 BASING is a fragile state. The convexity of the recent run-up (from $63 to $153) means downside risk is asymmetric on a structural break.

| State | Strategy |
|---|---|
| 🚀 ROCKET / healthy Stage 2 | N/A — not in Stage 2 |
| Rev Zone 0/1 short side | **N/A — Rev Zone 3.0 (no active zone)** |
| **TOP/BOT WARNING (Code 15)** + Stage 1 BASING | **DEFENSIVE CC** — Deep OTM cushion above KEY RES 101.88 |
| 🛑 Breakdown below Hull HMA | **EXIT SHARES** — do not sell CC into a collapse |

**Action:** If the user owns shares, **sell a defensive covered call at or above KEY RES ($101.88)** to harvest premium while capping upside. Suggested strike: **September 18 $103 call** (mid $3.44, 31 DTE, delta 0.319) — above the Long Target, captures ~3.7% premium while reducing exposure to a potential breakdown. If the stock breaks below the Hull HMA ($89.82), buy back the call and exit shares.

### If I'm Wrong
**Alternative bullish view:** The Golden Cross fires, the HMA150 rolls to Stage 2 ADVANCING, the UK launch surprises to the upside, and the stock breaks above the MA20/MA50 cluster ($95.65) with rising volume. In that case, the trade to enter would be a close above $95.65 with RVOL > 1.0, targeting $101.88 (Long Target) with a stop at $89.88. **Until that setup materializes, the trade is not constructible.**

**Exit plan before max loss:** If the price breaks below the Hull HMA ($89.82) on a daily close, the basing thesis is invalidated — close any long exposure and let the short zone set up at $97.23–$98.60 for a proper short entry.

## CRITICAL EVENTS
| Event | Date | Impact | Plan |
|---|---|---|---|
| **FOMC Rate Decision** | Late July 2026 | HIGH | Avoid holding through the print; 30% probability of a hike per dot plot |
| **UK Crypto Launch (live)** | This week | MEDIUM | Already priced; watch for volume surge / revenue disclosures |
| **CPI (June)** | Mid-Aug 2026 | HIGH | Sticky inflation (>4%) = hawkish constraint on multiples |
| **HOOD Q3 Earnings** | ~Nov 4, 2026 (85d) | EXTREME | Clean test of one-off EPS; close any directional positions before |
| **MA20/MA50 Breakout** | Pending | MEDIUM | Stage 2 confirmation trigger — revisit if it fires with RVOL > 1 |

## BOTTOM LINE
The Golden Cross is real, the UK launch is real, and the analyst targets are real — but the engine has not yet absorbed these into Stage 2, and the structural read is Stage 1 BASING (measured −0.34% SIG negative). `Action Long Code = 8` is the baseline "no entry" state, and `Action Short Code = 15` is an exit instruction, not a short trigger. Buy Score 52.87 is below the meaningful threshold, Sell Score 60.21 dominates, DMI is bearish, and RVOL is 19% of average. **The trade is not constructible from this state.** Patience is the position — wait for Stage 2 confirmation (close above $95.65 with volume) before sizing. If shares are already owned, harvest premium via a defensive CC above KEY RES.