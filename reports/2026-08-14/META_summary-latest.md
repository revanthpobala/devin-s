The options chain tool is returning only the tactical (21-42 DTE) range regardless of my DTE bounds — the LEAPS (150-400 DTE) and put-side strikes are not being returned by the tool. I have sufficient tactical call data and the TradingView strategy finder results. I'll note the LEAPS data as unavailable from the live tool and construct the plan from what is grounded.

I have all the data I need. Let me synthesize the final thesis.

Key determinations:
- **Action Long Code 8 (WATCH)** — baseline staging, zero measured directional edge (−0.06% flat). Triage = WATCH/no_setup.
- **Stage 4 (Down), Regime 4 (Decline)** — downtrend, NOT a reversal buy (Code 20 requires Buy<30, Rev≥7, RVOL>1.5; all fail: Buy 74.93, Rev 0.0, RVOL 0.30).
- **Long RR At Market 1.57** — below the 2.0 PASS lane. EV −0.36R.
- **IV Rank 7.14%** (Dormant) — premium is CHEAP, so debit structures are favored over credit.
- **Exp Move 12.71%** — large; strikes must be scaled.
- **Earnings 75 days out** — PASS gate (>7d), but a binary CapEx event.
- **Non-indicator pillars:** Ackman increased META (Finnhub), 89% Buy consensus, $750-754 target, 28% revenue growth, 18-20x P/E. These are the conviction carriers.
- **Bear:** CapEx $130-145B, FCF compression, EPS miss, downgrade to Hold ($631), retail sales -0.6%, consumer sentiment 51.0.

The indicator says STALK (no fresh long). IV Rank <20 → buy premium (debit), not sell. The measured edge is absent, so conviction is capped at 6 and carried by Pillar 3 (catalyst/flow).

# META | $589.76 | 2026-08-14
**Bar close:** $591.79 (Data Window) · **Live:** $589.76 (get_realtime_quote, intraday) · **Change:** -$5.09 (-0.86%) vs prior close $594.85

## ⚡ TLDR / EXECUTIVE SUMMARY
**The Thesis in 2 Sentences:** META is in a Stage 4 downtrend (Regime 4 Decline) with the indicator in baseline WATCH (Code 8, zero measured directional edge) and at-market R:R of 1.57 below the 2.0 PASS lane, so no fresh directional long is authorized at market. However, with IV Rank at a Dormant 7.14% (cheap premium), a 28% revenue-growth franchise at a historically low 18-20x forward P/E, and fresh institutional accumulation (Ackman increased META; 89% Buy consensus, $750-754 target), the state supports a defined-risk DEBIT structure on a technical reclaim rather than a spot buy.
**Verdict:** STALK (Conditional Trigger) / LEAN LONG (Options Debit) · **Conviction:** 5/10
**EARNINGS GATE:** PASS (75 Days Remaining | ~2026-10-28)
**Primary Structure:** Plan B (Defined-Risk Bull Call Spread, tactical) + Plan C (conditional equity trigger). Plan A spot is gated by R:R 1.57 < 2.0 and Stage 4. Plan D (skip) if no reclaim.
**(If User Owns Shares):** HOLD — no CC (IV Rank 7.14% < 20 = thin credit, higher touch odds; do not cap the one potential rebound).

## 🛠️ DATA AUDIT (THE 4 PILLARS)
*Literal data verification from Data Window and live research.*

### Pillar 1: Quantitative Indicator State (The Risk Manager)
*   **Action codes:** Long Code 8 (WATCH) / Short Code 8 (WATCH). Both baseline staging — 63% of bars, zero measured edge (−0.06% flat). No Code 1/2/20 active.
*   **Stage / Age:** Stage 4 (DECLINING ❌), Age 15 bars. Downtrend, price below MA50 ($601.59) and MA200 ($624.82). Weinstein MA 150 ($591.91) is flat/just above spot — the stage-lag flip is not yet confirmed.
*   **Scores & Sigma:** Buy Score 74.93 / Sell Score 79.50 (Sell leads — bearish lean). Buy Sigma +3.56σ vs Sell Sigma +1.82σ (raw evidence is net bullish, but the stage prior is dragging the score). Sell Score > Buy Score = two-sided/indecisive tape (churn flag).
*   **Trade geometry:** Long RR At Market **1.57** (below 2.0 PASS lane). RR To Target 2.35 (zone ratio, NOT obtainable at market). Long Entry $592.10, Stop $578.65, Target $612.43. **ZONELESS** — Long Entry Zone Bot/Top are null (no surviving cluster).
*   **Extension & Regime:** Ext Pct vs MA200 **−5.29%** (below MA200, NOT in the 25-60% exclusion band — favorable). Ext Z +0.07 (not stretched). Exhaustion Gradient 0.0 (no overheat). Regime 4 (Decline). MTF Long Aligned **0/3** (no timeframe confirms long).
*   **Rev Zone & Energy:** Long Rev Zone 0.0 (no reversal zone — Code 20 NOT available). Short Rev Zone 3.5. Energy State 0 (DORMANT), IV Rank 7.14%, IV30 22.56%, HV20 44.04%. ADX 11.88 (choppy, <15 = no breakout authority). DMI +DI 26.02 / −DI 26.67 (flat, −DI leads).

### Pillar 2: Price Action & Market Structure (The Tape & Levels)
*   **Psychological & Structural Anchors:** VP POC $610.46 (magnet, aligns with Long Target $612.43 and Darvas Box Top $612.43). VP VAH $646.39 (≈ AVWAP Resistance $646.42). VP VAL $560.84 (deep floor). VP HVN Above $593.35 (immediate resistance — price is just below it). VP HVN Below $586.51.
*   **Volume Absorption vs Disinterest:** RVOL **0.30** — extremely low. At a defended support floor this reads as supply exhaustion/absorption (sellers running out of shares), NOT weak breakout conviction, because price is at the low end of the range, not breaking out.
*   **Base Coiling & Formations:** Chart shows a sharp distribution off the ~$796 52W high down to the current $580-590 shelf. Price is coiling at the VP HVN Below ($586.51) / AVWAP Support ($576.04) confluence. Reversal Pattern Mask (1101, Age 14): KEY_REV_BULL 🟢, SWEEP_BULL 🟢, OOPS_BULL 🟢 (3 bullish) vs SWEEP_BEAR 🔴, TRAP_BULL 🔴 (2 bearish) — net 3 bullish, a bottoming structure forming but unconfirmed.

### Pillar 3: Fundamental Catalysts & Sentiment (The Research)
*   **Catalyst Verification (Finnhub/Alpaca, 2026-08-14):** Bill Ackman (Pershing Square) **increased META** and MSFT, exited GOOGL — verified institutional accumulation into the dip. AI compute demand robust (Nebius $40B backlog, rising merchant compute prices). Counter-narrative: "eventually free cash flow will matter again."
*   **Institutional & Social Flow:** Analyst Consensus (2026-08-01): 21 Strong Buy / 41 Buy / 9 Hold / 0 Sell = 89% Buy. Street target $750-754 (~28% upside). Insider YTD: Net BUYING (3.2M shares). Recent EPS: 2026-06-30 MISS (est 7.36, act 6.18) — broke the beat streak on CapEx. Downgrade to Hold ($631 target) citing "capex rises and ad metrics slow."

### Pillar 4: Macro Profile & Derivatives Landscape (The Strategist)
*   **Macro Calendar:** Next CPI Sep 11 (28d, July actual YoY +3.4% cooling from 3.5%). Next FOMC Sep 17 (34d). Next NFP Sep 04 (21d). July retail sales −0.6% MoM (worst since May 2025), consumer sentiment 51.0 — soft consumer = headwind for ad spend, but Fed "wait-and-watch/hold" is neutral-to-positive for tech.
*   **Options Gamma & Volatility (GEX):** Put/Call OI ratio 0.41. Major Put Walls: $580 (7,405), $550 (8,851), $500 (8,498). Major Call Walls: $700 (23,784), $720 (22,604), $750 (18,977). Dealer pinning corridor $550 (floor) to $700 (ceiling). IV Rank 7.14% (Dormant) → premium cheap, favor DEBIT. Exp Move 21b 12.71% → 1.25× = $685.84, 1.5× = $704.65 (call side); put 1.25× = $497.74, 1.5× = $478.93.

## 📐 CALIBRATION DISCLOSURE & PILLAR RATIONALE
- **Indicator state (Code 8 WATCH) carries ZERO measured directional edge** (bible §16: −0.06% flat, 63% of bars). It is a baseline staging state, not a buy signal. Long RR At Market 1.57 fails the 2.0 PASS lane; EV −0.36R. **No fresh directional long is authorized at market.**
- **This is NOT a Code 20 REVERSAL BUY** — the one measured long edge (+0.85% SIG). All three gates fail: Buy Score 74.93 (needs <30), Long Rev Zone 0.0 (needs ≥7), RVOL 0.30 (needs >1.5). Do not claim the reversal edge.
- **The trade is carried by Pillar 3 (verified institutional flow + valuation re-rating) + Pillar 4 (cheap IV, Dormant energy → debit structures favored)**, with a tactical local stop beneath the AVWAP/VP VAL floor. Conviction is capped at 5 (below the 6 indicator-only ceiling, because the indicator is affirmatively neutral-to-bearish here, not merely flat).
- **IV Rank 7.14% < 20** → per the structure map, buy premium (debit spreads / LEAPs), do NOT sell premium (thin credit, higher touch odds). This is the correct side of the premium.

## THE SETUP
**What the state shows:** Stage 4 Decline, Regime 4, price $589.76 coiling at the VP HVN Below ($586.51) / AVWAP Support ($576.04) confluence, below MA50 ($601.59) and MA200 ($624.82). ADX 11.88 = choppy, no breakout authority. Sell Score (79.50) leads Buy Score (74.93). The engine is ZONELESS with R:R 1.57 — constructible but not a PASS.
**What the image shows:** Distribution off the $796 high into a low-$590 shelf; bullish reversal labels (KEY_REV_BULL, SWEEP_BULL, OOPS_BULL) clustering at the base (Age 14 bars) but unconfirmed; volume contracting (RVOL 0.30) = absorption at the floor.
**Macro/Policy context:** Cooling CPI (3.4%), soft retail/sentiment, Fed on hold. Earnings ~Oct 28 (75d) is the binary CapEx-validation event.

```text
  $704.65  ───────────────────────────────────────  1.5x ExpMove (call ceiling)
  $700.00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  MAJOR CALL WALL (23,784 OI)
  $646.42  ───────────────────────────────────────  AVWAP Resistance / VP VAH
  $624.82  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  MA200 (long-term trend)
  $612.43  ◄──────────────────────────────────────  TARGET / Darvas Box Top / VP POC
  $601.59  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  MA50 (overhead supply)
  $593.35  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  VP HVN Above (immediate resistance)
  $591.79  ●──────────────────────────────────────  BAR CLOSE
  $589.76  ●──────────────────────────────────────  LIVE SPOT
  $586.51  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  VP HVN Below
  $580.00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  PUT WALL (7,405 OI) — dealer floor
  $578.65  ▼──────────────────────────────────────  LONG STOP LOSS (tactical)
  $576.04  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  AVWAP Support
  $560.84  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  VP VAL (deep floor)
  $550.00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  PUT WALL (8,851 OI) — major floor
```

## 📰 SYNTHESIZED NEWS & CATALYSTS
**Recent Headlines (2026-08-14, live):**
1. Bill Ackman (Pershing Square) **increased META** and MSFT, exited GOOGL (Finnhub/Alpaca) — verified institutional accumulation into the dip.
2. Meta released **Muse Glimmer** (30B open-weight agentic model) on Hugging Face; benchmarks beat Google Gemma 4 31B on agentic metrics (Google-grounded).
3. Downgrade to Hold ($631 target) citing "capex rises and ad metrics slow" despite 28% revenue growth (Seeking Alpha).

**Catalyst Impact:** The $130-145B CapEx is compressing FCF (under $1B) and broke the EPS beat streak (2026-06-30 miss). The market is pricing the *cost* of AI, not the *return*. The re-rating catalyst is the ~Oct 28 earnings print demonstrating that CapEx is translating into Advantage+/business-agent revenue. Ackman's accumulation and the 18-20x P/E (for 28% growth) provide a valuation floor argument.

## THE THESIS
**Why this stock should move:** The market has de-rated META 25-27% from its $796 high on CapEx fear, compressing a 28%-growth ad monopoly to a historically low 18-20x forward P/E. The technical base at the $576-586 AVWAP/VP-VAL confluence, with contracting volume (RVOL 0.30 = absorption) and a net-bullish reversal label cluster, plus fresh institutional accumulation (Ackman), sets up a mean-reversion re-rating toward the VP POC ($610.46) and Darvas Box Top ($612.43) — but ONLY on a confirmed reclaim of the VP HVN Above ($593.35) / MA50 ($601.59). The Dormant IV (7.14%) means the market is underpricing the rebound's optionality, favoring a defined-risk debit over spot.

## THE EDGE & ALTERNATIVE VIEWS
**The Bull Case (Pillars 2 & 3):** Ackman accumulation + 89% Buy consensus + $750-754 target + 18-20x P/E for 28% growth. Volume absorption at the $576-586 floor, net-bullish reversal labels, VP POC magnet at $610.46. Cheap IV (7.14%) = underpriced rebound optionality.
**The Bear Case (Pillars 1 & 4):** Stage 4 Decline, Sell Score leads, ADX 11.88 (no trend authority), ZONELESS, R:R 1.57 < 2.0. CapEx $130-145B crushing FCF, EPS miss, downgrade to Hold. Soft retail (−0.6%) and sentiment (51.0) threaten ad spend. MA50 ($601.59) and MA200 ($624.82) are overhead supply.
**Portfolio Manager Resolution:** The indicator is affirmatively neutral-to-bearish (Stage 4, Sell leads), so I do NOT take a spot long at market. The edge is asymmetric and only becomes real on a technical reclaim. I express the long thesis through a defined-risk debit spread (cheap IV, capped downside) triggered on a reclaim of $593.35, with the tactical stop beneath the AVWAP/VP-VAL floor. This respects the indicator's "no fresh long" read while capturing the Pillar 3 catalyst.

## THE TRADE (MULTI-REGIME ACTION PLAN)

### Plan A: Direct Equity Base Swing (If Taking Shares) — GATED
| Metric | Price | Rationale |
| :--- | :--- | :--- |
| **Entry** | $593.50 (on reclaim) | Above VP HVN Above $593.35 — NOT at market $589.76 (R:R 1.57 fails) |
| **Tactical Stop** | $575.50 | Below AVWAP Support $576.04 and Long Stop $578.65 |
| **Target 1 (Trim)** | $612.43 | Darvas Box Top / VP POC / Long Target |
| **Target 2 (Runner)** | $646.42 | AVWAP Resistance / VP VAH |
| **Tactical R:R** | ~1.0:1 to T1 | (612.43−593.50)/(593.50−575.50) = 1.05:1 — thin; this is why Plan B is preferred |
| **Allocation** | 0% (gated) | Spot is gated by R:R < 2.0 and Stage 4; use Plan B/C instead |

### Plan B: Defined-Risk Options Structure (Derivatives Strategy)
*   **The Play (Tactical, 21-45 DTE):** **Sep 18, 2026 $620C / $630C Bull Call Spread** (from TradingView Strategy Finder).
*   **Cost / Credit:** Net debit ~$2.85/contract ($285 max risk).
*   **Max Profit & Max Loss:** Max Profit $720 | Max Loss $285 | R:R 2.13:1.
*   **Breakeven Price:** $622.80 at expiration.
*   **Why this Strike & Expiry:** Short leg $620 is OTM (above spot $589.76, above MA50 $601.59 and VP POC $610.46), within 1.25× ExpMove ($685.84) — geometry-valid. $630 long wing is beyond the nearest call wall cluster and captures the re-rating to AVWAP Resistance. Sep 18 (35 DTE) clears the Sep 4 NFP and Sep 11 CPI, and is 40+ days before the Oct 28 earnings print (no earnings in window). IV Rank 7.14% < 20 → debit is the correct side (cheap premium).
*   **Touch Probability:** At 1.25× ExpMove, P(UP touch) ≈ 22.8% (IV>80 row) but here IV Rank is 7.14% (<20), so touch odds are HIGHER (~31.7% at 1.25× per the IV<20 row) — the spread's breakeven $622.80 sits below the 1.25× barrier, so this is a probability-weighted debit, not a lottery.
*   **Strategy Finder Selection (verbatim):** "Sep 18, 2026 | 35 | Bull Call Spread | 620C / 630C | Max Profit $720.00 | Max Loss $-280.00 | R:R 2.13:1 | Breakeven $622.80 | Bid/Ask $2.45/$3.15 | Spread 2.83%."
*   **Plan B-2 (Multi-Quarter / LEAPS):** The live options chain tool returned only the 21-42 DTE range (LEAPS 150-400 DTE not returned by the tool this session — **not found**). When available, the LEAPS expression is a **Deep-ITM Dec 2027 $500C** (Delta ~0.70-0.85, strike below the $550 put wall) as a Poor Man's Covered Call, capturing the multi-quarter CapEx-monetization re-rating without rapid theta decay and bypassing the Oct 28 binary. Re-pull the chain to price it before execution.

### Plan C: Conditional Stalking Trigger (If Waiting for Confirmation)
*   **Trigger Level:** Buy Stop on daily close **above $593.35** (VP HVN Above) with RVOL > 1.0 — confirms the base defense and flips the absorption read into conviction.
*   **Contingent Stop & Target:** Stop at $575.50 (below AVWAP $576.04), Target $612.43 (T1) / $646.42 (T2).
*   **Invalidation:** Daily close below $576.04 (AVWAP Support) with RVOL > 1.5 = the floor breaks; exit/stand down.

### Plan D: Disciplined SKIP (If Passed)
*   If price does NOT reclaim $593.35 within 21 bars and instead grinds down through $586.51 (VP HVN Below) toward the $550 put wall, the base is failing — SKIP the long entirely and re-evaluate at the $550 dealer floor (a fresh Code 20-style capitulation setup would require Buy<30 + Rev≥7 + RVOL>1.5, none of which are present).

> **[M] Do not build the plan around a perfect pullback fill.** A limit resting at the prior bar's zone fills only 32.1% of the time. The Sep 18 spread's breakeven ($622.80) is the honest level — size for a ~1-in-3 hit rate and assume gap risk through the stop.

**Which SIDE of premium you are on is decided by `Energy IV Rank Pct`; the STRIKE is decided by `Exp Move Pct 21b` & GEX Walls.**
- **Debit (buy premium)** — IV Rank 7.14% < 20 → CORRECT side. Also Regime is not a squeeze, but the cheap-IV read dominates: buy optionality, don't sell it.
- **Strike** — $620 short leg is OTM and within 1.25× ExpMove ($685.84), anchored below the $700 call wall. Geometry-valid.
- ⚠️ **Never sell premium through earnings** — the Oct 28 print is 75 days out, outside the Sep 18 window, so the spread is earnings-clean.

### Income & Management (If Holding 100+ Shares)
**State: IV Rank 7.14% < 20 → NO COVERED CALL.** Thin credit and HIGHER touch odds (IV<20 row: 31.7% touch at 1.25× vs 22.8% at IV>80). Capping the right tail on a Dormant-IV, potential-rebound name costs more than a fair credit pays. Own the shares or buy calls.
**Put-side (cash-secured put):** IV Rank < 20 → NO CSP (thin credit for real downside). Do not write a put into a Stage 4 decline for a small credit.

### If I'm Wrong
**Alternative view:** The CapEx is a value trap — the 18-20x P/E is low *because* FCF is being crushed, and the Oct 28 print confirms no AI-monetization revenue, triggering a deeper de-rating toward the $550 put wall. · **Exit plan before max loss:** On a daily close below $576.04 (AVWAP Support) with RVOL > 1.5, the base has failed — close the spread at market (max loss $285/contract) and stand down.

## CRITICAL EVENTS
| Event | Date | Impact | Plan |
|---|---|---|---|
| NFP | Sep 04 (21d) | MEDIUM | Watch labor data; no position change |
| CPI | Sep 11 (28d) | MEDIUM | Cooling (3.4%) supports hold; no change |
| FOMC | Sep 17 (34d) | MEDIUM | Hold expected; neutral-to-positive for tech |
| Earnings | ~Oct 28 (75d) | EXTREME | Binary CapEx-validation; spread expires Sep 18 (clean) |

## BOTTOM LINE
The indicator says STALK, not buy — Stage 4, Sell leads, R:R 1.57, ZONELESS, and this is NOT a Code 20 reversal (all three gates fail). The one thing that has to go right is a **reclaim of $593.35 (VP HVN Above)** that converts the low-volume absorption into a confirmed base defense, validating the Ackman-driven institutional accumulation and the 18-20x P/E re-rating. Express the long through the cheap-IV Sep 18 $620/$630C Bull Call Spread (breakeven $622.80, R:R 2.13:1, earnings-clean), not spot. If the $576 AVWAP floor breaks on volume, the thesis is dead — stand down.

Works cited
1. Finnhub/Alpaca live news (Ackman META increase, Muse Glimmer, CapEx/FCF), accessed 2026-08-14
2. Data Window & Live Options Chain (Alpaca), accessed 2026-08-14
3. Macro calendar (CPI Sep 11, FOMC Sep 17, NFP Sep 04) & retail/sentiment data, accessed 2026-08-14