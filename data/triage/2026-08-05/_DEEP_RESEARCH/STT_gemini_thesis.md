# STT | $187.05 | August 5, 2026
**Bar close:** $187.05 (Data Window) · **Live:** $188.04 (get_realtime_quote, 22:34 UTC-4 — ⚠️ **SUSPECT DATA: bid/ask $187.11/$197.57 = 5.5% wide, volume 1,348 shares on a $51B-cap bank stock — this is malformed post-close quote, not thin liquidity.** Cross-check: the bar's day range was $186.46–$188.85 and RVOL was 0.60 = ~60% of normal, indicating normal thinness but the 1,348 share live volume is anomalous) · **Change:** +$0.04 (+0.02% vs prev close $187.01; vs 52W high $192.51 still ~2.9% below)

## ⚡ TLDR / EXECUTIVE SUMMARY
**The Thesis in 2 Sentences:** The 94 Buy Score is a lagging-indicator artifact: STT is **29.28% above its 200-day MA** — squarely inside the system's most reliable exclusion band (25–60% above MA200 measures **−0.71% [−1.13, −0.32] SIG**), the long entry state is WATCH (not triggered, with broken at-market R:R of 0.29 and `Long RR Valid = 0`), and Stage 2 just turned over today (age = 1 bar, the worst refinement tested at **−0.57% SIG**). The bullish pillars — LATAM acquisition (Santander CACEIS JV), analyst upgrades to $195–$210, and 68% YTD P/B expansion from 1.44x to 1.89x — are real but **already priced in**; the next clean entry does not exist until price either pulls back to the $185.40–$186.75 zone (closing extension below 25%) or absorbs the 52W high at $192.51 with structural confirmation.
**Verdict:** **SKIP** · **Conviction:** N/A (no trade)
**EARNINGS GATE:** PASS (>7d — next earnings in **69 days, ~2026-10-14**, per yfinance ground truth)
**(If User Owns Shares):** HOLD — no CC. Stage 2 ADVANCING with strong ADX (25.14), positive DMI, +DI 22.55 > −DI 13.93, and no momentum arrows degrading (▬ Buy). Do **not** sell premium against this extension — the **25–60% MA-200 band rules out deep-OTM DEFENSIVE CC** as the only valid response (Rev Zone 0 on the long side means ATM/aggressive CC is also not justified). If you must generate premium, sell **only at or above KEY RES $192.51**, 30–45 DTE, skip earnings.

## 🛠️ DATA AUDIT (LITERAL VALUES)
*Verbatim from the Data Window. NOT from the chart image.*

*   **Action codes:** **Action Long Code = 8 (WATCH — NOT triggered, 63% of bars — the baseline).** **Action Short Code = 10 (WAIT — NOT triggered).** ⚠️ **PRIME/ACTION SELL is structurally impossible** — the Pine demotes every short code 1/2 to WATCH. Short conviction must be read from `Sell Score 9.45`, `Sell Sigma Evidence -2.80`, and `Short Rev Zone 8.0` directly. Both sides sit in the passive lane; no actionable entry exists.
*   **Stage / Age:** **Stage 2 (ADVANCING) · Stage Age = 1 bar.** Just transitioned. Per §16.8, the **PASS + Stage Age ≤ 5 filter measures −0.57% SIG** — the *worst* refinement tested. Mid-life Stage 2 (16–31 bars, +0.32% SIG) is the favorable cell, not age-1.
*   **Scores:** **Buy Score 94.39 ▬ · Sell Score 9.45 ▼ -3** (3-bar delta < −2 triggers red ▼ per §4.6). **Buy Sigma Evidence 3.82σ (real) vs Sell Sigma Evidence −2.80σ** — sigma is positive and meaningful here, NOT a prior-driven hallucination. ⚠️ However, **median Buy Score is 85.3 across all bars** and this score clears 82 on 54% of all bars — a 94 score is normal, not a discovery. Selectivity lives in the action code + zone geometry, not the headline score.
*   **Trade geometry:** `Entry At Market = 1` (long breakout fill) — `RR To Target = 0.29` is the **at-market ratio**, the broken one. `Long In Zone = 0`, `Long RR Valid = 0` (EV gate fails). **Short side:** `Entry At Market = 0` implied, `Short RR Valid = 1` BUT `Action Short Code = 10`, `Short In Zone = 0`, stage = 2 (not 4 — short stalk requires Stage 4). **No valid entry geometry on either side.** Zone fields: long $185.40–$186.75 (populated but price is $1.30 above the top), short $191.93–$193.10 (price is below it, short action = WAIT).
*   **Extension:** **`Ext Pct vs MA200 = 29.28%` — INSIDE the 25–60% exclusion band.** `[M] -0.71% [−1.13, −0.32] SIG`, monotone across the entire range. **This is the single most reliable exclusion in the system, robust in both ≥$20 and unfiltered universes.** Even a textbook PRIME BUY at this extension would be a SKIP. `Ext Z Self Relative = 0.328` (below 1.5 — not relatively stretched, so the system's relative-z BLOW-OFF/EXTENDED codes don't catch this; the absolute MA-200 check is the master). `Exhaustion Gradient = 0.146` (well below 0.4 — not terminal climax yet, but the run-up has legs).
*   **Regime / MTF:** **Regime 6 (SQUEEZE)** — volatility compression. Per §15.6 priority enum, **44.8% of Regime-6 bars are also Stage 4** (this one is NOT — it genuinely is Stage 2 — but never read `Regime ≠ 4` as "not declining"). Regime 6 is not directional; it just means **expansion imminent → favor buying premium** if you have a position. `MTF Long Aligned = 2/3` — the sweet-spot alignment (+0.32% SIG; +0.46% SIG with Buy Sigma > 5). Sigma is 3.82 here so 2/3 is in the favorable cell — but this is consumed by the extension exclusion.
*   **Rev Zone:** **Long Rev Zone = 0.0 (no reversion setup at all).** **Short Rev Zone = 8.0 (Z1 — strong/forming reversion on the bear side).** No dual-zone to force a skip, but Long=0 means **this is NOT a REVERSAL BUY setup** (requires Rev ≥ 7 + Buy < 30 + close < EMA200 + RVOL > 1.5 — fails on Buy and price level).
*   **Energy / DMI:** **Energy State 2 (WARMING)** · Energy IV Rank 45.63% · IV − HV Spread +9.55 (IV > HV → expansion). **ADX 14 = 25.14** (strong trend, ≥25 OK for full size if a trade existed) · **DMI +DI 22.55 / −DI 13.93** (bullish bias confirmed, +DI > −DI).
*   **Volume profile:** **RVOL Vs Avg = 0.603** — running at 60% of average volume despite the BREAKOUT RUN. **This is a weak-volume advance; genuine breakouts require RVOL > 1.** VP POC $152.88 · VP VAH $189.40 · VP VAL $131.90 · VP HVN Below $183.97 · HVN Above blank (expected — VP series are `barstate.islast`-gated, single-population per ticker).
*   **Fresh labels (count by POLARITY, lead with Age):** **Bear Warning Mask = 3 (TOP + RSI_CASCADE), Age 14 bars — both bearish, ~14 sessions ago, not fresh.** **Reversal Pattern Mask = 2112 (TRAP_BULL [BEARISH, age 0] + OOPS_BEAR [BEARISH, age 0]) — 2 BEARISH, fresh today.** Weak Level Mask = 0. **Net scoreboard on fresh signals: 0 bullish vs 2 bearish.**
*   **Next earnings:** **2026-10-14 (in 69 days)** per yfinance ground truth — well outside the <14d gate, no near-term binary event.

## 📐 CALIBRATION DISCLOSURE (MANDATORY)
The state any long entry would have to lean on is **Action Long Code = 8 (WATCH)**, which measures **−0.06% [−0.34, +0.15]** — the interval **straddles zero**, so WATCH is a **DESCRIPTION, not an edge.** Even the textbook PRIME BUY at this extension measures **−0.04% [−0.30, +0.22]** (flat), and the explicit `PASS + Ext 25–60%` filter measures **−0.71% SIG** — the most robust exclusion in the system and the one this trade would have to defeat. The non-indicator pillars (LATAM acquisition signed; Citi $210 / Morgan Stanley $195 / FNB $200 raised targets; Q2 EPS +69% YoY; P/B expanded 1.44x → 1.89x) are real and dated — but they are **already in the price** (stock +68% YTD, sitting ~2.9% below 52W high). The pillar does not lift the verdict above SKIP because the measured exclusion is structural and applies *even on a PRIME*. **No fresh long entry exists here; the trade is to wait.**

## THE SETUP
**What the state shows:** STT is in a structural Stage 2 ADVANCING trend (Weinstein MA 150 = $185.39, the stage pivot is right under price). Price $187.05 sits **29.28% above MA200 ($144.68)**, **3.07% above HMA150 ($185.39)**, **3.61% above MA20 ($181.71)**, and **8.09% above MA50 ($173.05)** — classic late-stage-2 advance, **but in the exclusion band.** 52-week high is at $192.51; price is **2.84% below it**. AVWAP Resistance $183.73 (recent swing-high anchor); AVWAP Support $140.81 (multi-year anchor, far below). ADX 25.14 confirms the trend has legs. MTF 2/3 sweet-spot alignment. RVOL 0.60 — the move is grinding higher without volume conviction. Energy is WARMING with IV − HV = +9.55.

**What the image shows:** Wide structural (zoomed panel) reveals a multi-month Stage 2 ADVANCING from a March 2026 base near $130, with a tight cluster of **bearish warning labels stamped across the recent few weeks** — TOP WARNING ⚠️ (red, top-right cluster), RSI CASCADE 🌊, multiple ANCHOR RES tags, FAILURE SWEEP 💎 (bearish — failed bullish sweep = trapdoor), OOPS labels — exactly the kind of **stale-clustered warning set** that §4.4 calls the signature of distribution being absorbed rather than reversal confirmation. Zoomed recent shows price right at the **orange KEY RES line at $192.51** (52W high) and the **green KEY SUP line at $177.32** with the orange long zone box sitting at **$185.40–$186.75**. Today's bar ($187.05, day range $186.46–$188.85) is a small-bodied advance that **closed ABOVE the zone top ($186.75) but well below the KEY RES ($192.51)** — i.e., price has *just escaped the long zone* by $0.30 but is also **inside the blue-sky synthesis danger zone** (close > all six targets + above 52W high × 0.98 = $188.66, so `isBlueSky` would fire if price crosses $192.51). The bar pattern is a thin-volume drift-up, **not** a breakout bar; structure lean: **late, not early.**

```
                          (52W High: $192.51 — KEY RES)
   ────────────────────────────────────────────────────────────  
                          ▲ RESISTANCE CEILING                  │
                          │                                       │
                  ┌───────┴───────┐                               │
                  │  BLUE SKY ZONE │  ← if price clears $192.51   │
                  │  (target syn-  │     with structural accept,  │
                  │   thesis only) │     blue-sky target =          │
                  └───────┬───────┘     entry + 1.618×FibRange     │
                          │                                       │
                          │                                       │
   ──────────────────● BAND-$187.05 (LIVE $188.04) ●─────────────  ← entry zone top $186.75
                          │   STT BAR CLOSE ▲                    │
                          │                                       │
                  ┌───────┴───────┐                               │
                  │  LONG ZONE    │  $185.40 - $186.75            │
                  │  (pullback    │  ← structural fill only        │
                  │   target)     │     ATM=0; 32% fill rate (§16.5)│
                  └───────┬───────┘                               │
                          │                                       │
                          │                                       │
                  HMA 150: $185.39  ──────── (stage pivot, 0.3% below)│
                          │                                       │
                          │                                       │
                  MA 20: $181.71    ──────── (trend support)        │
                          │                                       │
                          │                                       │
                          │                                       │
                          │                                       │
   AVWAP Support (deep) $140.81 ───────────────────────────────── │ ← far floor, no relevance
                                                              ↓
                                                          STOP IF
                                                       < $181.76
                                                       (Long Stop)
```

**Macro/Policy context:** Rate environment is the dominant macro axis. The Fed held 3.50–3.75% in July 2026 (fifth consecutive hold), but **dovish Warsh framing vs hawkish minutes**: a ~30% market-implied probability of a September hike persists alongside a 9–3 dissent vote favoring further restriction. The energy/oil de-escalation cooled the bond market — Treasuries rallied. **STT is yield-sensitive at the margin (custody + asset management fees) but more leveraged to AUM trajectory than to NIM**; a Fed pivot toward restriction compresses equity multiples more than it impacts fee revenue. LATAM acquisition news (Santander CACEIS JV in Brazil/Mexico/Colombia) is the structural re-rating narrative and is real. Sector context: financials +7.5% over 6 months per Finnhub.

## THE THESIS
**Why the stock should consolidate/pause here (NOT trade thesis):** STT sits **2.84% below its 52-week high** with the system's most reliable exclusion band (ext 25–60% above MA200) **measuring significantly negative.** The bull case is real on the catalyst axis — Q2 2026 results were blockbusters (EPS $3.72, +69% YoY; revenue $4.05B, +18% YoY; P/B expansion from 1.44x to 1.89x; LATAM acquisition adding structural AUM growth), and the street has repriced the stock accordingly (Morgan Stanley $195, Citi $210, FNB $200, ground-level $204.63 from a major analyst on 2026-08-03). But the **measured stock returns at 25–60% ext above MA200 are −0.71% ex21 SIG** — **even for code 20 PRIME setups that read mathematically well.** STT today has *weaker* geometry than a PRIME (it is WATCH, RR Valid 0, at-market entry), which makes it a worse *not better* candidate than the average 25–60% PRIME. The right move is to **wait for either (a) a pullback to the $185.40–$186.75 zone** with **structural fill (ATM = 0)** AND **extension falling back below 25%** (i.e., price < ~$181), which is **both far-fetched on the current trajectory and would require a meaningful drawdown** — not a buyable scenario — OR (b) accept the blue-sky path: a structural acceptance above $192.51 with vol/RVOL confirmation → re-rate target (only A-grade-short zone valid for shorts per the blue-sky filter; for longs the blue-sky target is `entry + 1.618 × FibRange`, but the chase risk is high and the measured extension exclusion still applies until price cools). **Neither path is clean today.** The trade is to wait.

## THE EDGE
**What I know that the market might be missing:** Nothing actionable right now. The market IS pricing the LATAM and analyst-upgrade catalyst (P/B at 1.89x vs 1.44x prior = full re-rating); the LATAM deal is signed but **not closed** (typical CACEIS-integration timeline is 12+ months before accretion shows in earnings) — so the **forward catalyst path is open** but the **forward return is already partially discounted.** The system says: structural setup is broken. **The honest edge here is SKIP + a defined stalking price**, not a chase.

## THE RISK
- **Primary risk (chase risk):** Buying near the 52W high at 29% ext above MA200 puts the trade directly in the −0.71% SIG exclusion band. Probability-weighted expected return on a long entry today is negative.
- **Event risk:** Earnings are 69 days away — outside the 14-day gate, but the **trajectory matters**: Q3 print must validate the LATAM AUM narrative or the re-rating compresses.
- **Macro/regulatory risk:** A hawkish Fed surprise (Warsh could lay groundwork for a September hike on hot CPI) compresses bank multiple. STT is more AUM-sensitive than NIM-sensitive, but multiple compression is multiple compression. Conversely, dovish surprise is a tailwind the bulls will see as confirmation.
- **Technical invalidation:** A daily close **below MA20 ($181.71)** would flip Stage 2 to STAGE 2: PULLBACK and re-open the long-zone discussion at the $185.40 zone. A daily close **below $177.32 (KEY SUP)** would break structural support. **Both are below the entry**, defining the risk.
- **Data risk:** Live quote $188.04 with bid/ask $187.11 / $197.57 and 1,348 shares is **suspect data** — do not size off it. The intraday bar ($187.05 close, $186.46–$188.85 range, RVOL 0.60) is the trustworthy read.

## ⚖️ LOCAL RESEARCHER DEBATE

**Moderator Consensus:** The local debate generated a strong bull narrative and a heavy bear case. After applying the measured calibration:

1. **THE BULL CASE IS REAL BUT UNACTIONABLE.** LATAM acquisition, Q2 blockbuster (EPS +69% YoY), analyst upgrades to $195–$210, and Stage 2 ADVANCING with MTF 2/3 alignment **are validated facts**, not hallucinations. The bull case correctly identifies that we sit just below the 52W high with momentum. **Where it fails:** it conflates **narrative strength with trade constructibility.** The indicator is unambiguous: Action = WATCH, RR Valid = 0, Entry At Market = 1 (chasing the close), Long In Zone = 0, Ext = 29.28% (exclusion band). A clean long requires a *pullback to the zone* OR *structural breakout acceptance above $192.51* — neither is on the tape today.

2. **THE BEAR CASE HAS THE RIGHT ANCHORS BUT OVERREACHES.** Its biggest valid point: **Ext 25–60% is the most reliable exclusion in the system (-0.71% SIG)** — and the rest of the bear signals (TRAP_BULL fresh, Bear warnings, weak volume) compound it. **Where it fails:** it misidentifies Stage (it claims "Stage 3 Top" — actually Stage 2 ADVANCING at age 1) and the bid/ask anomaly. The $10.46 bid/ask spread on the live quote is **suspect post-close data**, not institutional dumping (during RTH STT spreads are sub-penny; the 1,348 share "live volume" is also malformed — flagged but not bearish on its own). The bear case correctly declines to buy but **overshoots into a short sale that the engine does not code**: Action Short = 10 (WAIT), Short In Zone = 0 (price is $4.49 below short zone bot $191.93), and Stage 2 is not a short stage. The premium data shows Short RR Valid = 1 with target $177.79, but the stalk conditions require Stage == 4 — not met.

3. **CROSS-VERIFICATION (Google-grounding is authoritative where it conflicts):** The fresh Finnhub, Alpaca, and web search all **confirm the same facts** — LATAM acquisition, three analyst upgrades, Q2 strength. **No fabrication detected.** The bull-cited "JPMorgan upgrade to $200" was NOT verified in my searches; the verified targets I obtained are Citi $210, Morgan Stanley $195, FNB $200, plus a $204.63 increase on 2026-08-03 from a "major analyst firm" (not specifically named in the dossier). The bear case's "30% probability of September hike" is **confirmed by the macro search** but is also a probability that the market already partially prices.

4. **JUDGE'S RESOLUTION:** Both sides agree this is a *late-stage trend at the 52W-high cusp.* The disagreement is whether to (a) buy into exclusion, (b) short the topping structure, or (c) wait. The measured data **forces answer (c)** with a *specific* wait protocol: **pullback to zone $185.40–$186.75 with structural fill AND ext <25% (price <$181)** OR **structural acceptance above $192.51 with vol/RVOL confirmation.** Without one of those, neither side of the trade is constructible.

## COUNTER-TREND ANALYSIS (only if REV ZONE is active)

| Check | Finding |
|---|---|
| REV ZONE status | **Long Rev Zone = 0.0 (Z-, no setup at all).** Short Rev Zone = 8.0 (Z1 forming). |
| **Is it `Action Long Code = 20`?** | **NO — Action Long Code = 8 (WATCH), not 20.** The REVERSAL BUY lane is closed. |
| MTF alignment | 2/3 (would be the +0.32% SIG cell if Code 20 fired — it didn't) |
| In Zone? | Long In Zone = 0, but this is irrelevant — Code 20 doesn't require In Zone |
| Key triggers | None of the T1-T6 reversion tiers fire. Buy Score 94 (must be <30 for Code 20). Close $187.05 is well ABOVE EMA200 $144.68 (Code 20 requires close < MA200). RVOL 0.60 < 1.5 required. **Triple-fail.** |
| ACTION conflict | No 🛑 state. Action = 8 WATCH is not a danger
state — both long and short reversion lanes are closed for action today. The Short Rev Zone at 8.0 is "forming" (Z1) but Action Short Code is 10 (WAIT), stage is 2 (not 4), Short In Zone is 0, and the closure is 1.27% — any short here is a chase, not a setup. |
| **Reversal Thesis** | **None active.** The 29.28% extension above MA200 actually *blocks* the long reversal gate (must be below MA200) and the bullish bias above MA150 ($185.39) blocks Stage 4 shorting. This is not a counter-trend environment — it is a late trend-following one. The only valid reversion trade is to wait for either side to fail (close < MA200 for long reversion or Stage roll to 4 for short reversion). |

## CONVICTION: N/A — **SKIP**

There is no conviction to assign because no trade is taken. For the record on why conviction is not granted:

- **Code 20 (the only measured long edge) requires Buy < 30, close < MA200, RVOL > 1.5.** STT meets zero of these (Buy 94.39, close $187.05 vs MA200 $144.68, RVOL 0.60). The measured REVERSAL BUY lane (+0.85% [+0.34, +1.36] SIG) is **literally unreachable** here.
- **Codes 1–4 (the structural entry states) require In Zone + RR Valid, both of which fail at 0.** The measured PASS rule is **−0.03% [−0.28, +0.22]** — flat — and adding the 25–60% ext filter drives this to **−0.71% SIG**. Nothing structural is positive.
- **The bullish non-indicator pillars (LATAM acquisition signed, Q2 EPS +69% YoY, P/B 1.44x → 1.89x, three raised targets at $195/$200/$210, MTF 2/3 alignment, Buy Sigma 3.82σ real evidence) are real and verified live — but the measured extension exclusion at 29.28% is the binding constraint.** Even on a *textbook* PRIME BUY at this extension the ex21 is negative. The pillars do not lift the verdict above SKIP because **comfort is priced**.

**Conviction for the stalking plan below: 5/10** that the next clean setup actually triggers inside a meaningful window (15–30 sessions) — given the strong trend + ATH proximity, the pulling-back-into-zone outcome is roughly coin-flip; the breakout-above-$192.51 outcome is the higher-probability path.

## THE TRADE
**No stock or options entry today.** Below are the *two* defined stalking plans that would convert this SKIP into a trade:

### Stalking Plan A — Pullback to Zone (preferred, lower-risk)
| | Price | Rationale |
|---|---|---|
| **Trigger** | Daily close **$185.40–$186.75** (the existing long zone) | Structural fill (ATM = 0); price action: bearish retest of zone top after distribution shake at the highs |
| **Hard pre-condition** | **`Ext Pct vs MA200` must fall back below ~25%** by the time the trigger fires — i.e., price ≤ $181 | The 25–60% band exclusion is binding at 29%; only lifting it via mean reversion re-opens the lane |
| **Entry** | $186.75 (zone top, rest limit) | 32.1% historical fill rate per §11.8 |
| **Stop** | $181.76 (Long Stop Loss from Data Window) | Zone-derived, ≈5.4% below entry |
| **T1** | $188.60 (Target) — but only if entry is at $186.75; if entry is at $185.40 the math changes | First waypoint = nearest target ≥ entry |
| **Target** | $188.60 (Long Target) — **thinned** because stage age and ext at the trigger matter | 1.0:1 R:R math |
| **Size** | **0% until trigger fires**; if triggered, 75% (the Stage-2-▲/▬ tier — but if stage age ≤5 at trigger → 25%) | PASS+age≤5 is the worst refinement |
| **R:R** | **Compute at trigger, not now.** At today's prices, R:R is 0.29 (broken). The 50/50 fill-zone-vs-breakout split means the *honest* expected R:R for this setup is poor. |

⚠️ **Honest expectation for Plan A:** the §11.8 simulation shows "limit resting at prior bar's zone fills 32.1% of the time for −0.00% date-neutral" — i.e., **the pullback-then-buy thesis has historically been flat-to-slightly-negative for STT-equivalent behavior.** Plan A's value is *not* a positive expectancy — it's avoidance of the exclusion-band chase.

### Stalking Plan B — Structural Breakout above $192.51 (higher-risk, blue-sky)
| | Price | Rationale |
|---|---|---|
| **Trigger** | Daily close **> $192.51** on **RVOL Vs Avg > 1.5** (a true breakout bar) | Acceptance above the 52W high changes state; old ceiling becomes new floor |
| **Hard pre-condition** | `Entry At Market = 1` accepted (you are paying the close on a breakout). Action should be **6 ACCELERATION** by the trigger bar | The only code allowed to ignore In Zone |
| **Entry** | At-market on trigger bar close (~% of ATH basis) | $192.51 trigger means entry is wherever the next-day close settles above $192.51 |
| **Stop** | $188.60 (HMA150 / Target re-purposed — this is the rational structural stop on a breakout retest) | ≈2.0% below entry |
| **T1** | Next KEY RES / pivot high — **not pre-computable** because we are in blue-sky territory (no Target above entry at this point) | §5.6 blue-sky synthesis: `entry + 1.618 × FibRange`, capped at entry + 2×ATR |
| **Target** | Compute via blue-sky: from current $187.05 the FibRange is the $115–$192 swing (~$77), so 1.618×$77 = $124.65 → blue-sky ceiling = $187 + $125 = **$312** territory; but 6% cap applies → cap = $187 × 1.06 ≈ **$198**. **Replace with entry + 2×ATR ≈ entry + $13 = ~$206** | This is not a precise target — blue-sky paths are inherently synthetic |
| **Size** | **0% until trigger fires.** If ACCELERATION + RVOL > 1.5 → 50% (Code 6 size); if PRIME never arrives → 25% | Code 6 is a measured-state code at +0.27% (flat) |
| **R:R** | 0.78:1 (entry 192.51 / stop 188.60 / target ~206) — **below the 0.70 EV floor margin** | Will fail `Long RR Valid` again on the EV math; this confirms Plan B is the *worse* path |

> **[M] Do not build the plan around a perfect pullback fill.** A limit resting at the prior bar's zone fills only 32.1% of the time, for −0.00% date-neutral against −0.12% unfilled. **Waiting is not free** — Plan A's stalking plan is just to *avoid the chase*, not to expect positive expectancy on fill.

> ⚠️ **Neither stalk plan is actionable today.** They define the conditions under which the SKIP would convert to a trade — not the trade itself. **There is no current position to size.**

### Options — For Existing Position Holders (Income Module)
**There is no fresh options trade to recommend.** For holders considering a covered-call overlay, see Income & Management below.

### Income & Management (100+ share holders)
STT sits in **STAGE 2: ADVANCING with positive momentum (▬ Buy) and no degradation arrow** — per the doctrine this is the **HOLD — no CC** cell. The structural trend is intact and the system refuses to cap upside. *However*, the 25–60% extension band creates an unusual situation: **you may WANT premium because the trade is end-of-pattern, even though the system says no CC.** Reconcile as follows:

```
                    (STT Spot: $188.04)
                            │
                            ▼
              (52W High: $192.51 — KEY RES)
                            │
                            ▼
              (Long Target: $188.60 — already behind spot)
                            │
                            ▼
              (Long Zone Top: $186.75)
                            │
                            ▼
              (HMA 150: $185.39 — stage pivot)
                            │
                            ▼
              (MA 20: $181.71 — structural stop level)
                            │
                            ▼
              (Long Stop: $181.76 — derived)
                            │
                            ▼
              (KEY SUP: $177.32)
                            │
                            ▼
              (Short Target: $177.79 — where the bear wants to take it)
                            │
                            ▼
              (MA 50: $173.05)
                            │
                            ▼
              (VP POC: $152.88)
                            │
                            ▼
              (MA 200: $144.68)
                            │
                            ▼
            (AVWAP Support: $140.81)
```

| State | Strategy |
|---|---|
| 🚀 ROCKET / healthy Stage 2 (+DI > −DI, ADX ≥ 25) | **HOLD — no CC.** The doctrine is clear. Do not cap upside in a trend with multiple analyst price targets above market. |
| Rev Zone 0/1 short side (Short Rev = 8.0 here) | **NOT TRIGGERED** — Short Action = 10 (WAIT) is not stalk-qualified. **Do NOT sell aggressive CC** — that's a bet on a short setup the engine hasn't confirmed. |
| Rev Zone 2 / chop | N/A — Rev Zone = 0 long side, 8 forming short side. Not chop. |
| 🛑 Breakdown (close < MA20 $181.71) | **EXIT SHARES** — do not sell CC into a collapse. If the breakdown happens, close the long first. |
| 🛑 TOXIC, extended (25–60% band — that's WHERE WE ARE) | **DEFENSIVE CC** — but the doctrine strictly forbids CCs in Stage 2 + 25–60% ext **except** the **CC EXCEPTION clause**: TOXIC RISK blocks directional entries; it does **NOT** block premium selling on shares already held. **Apply this narrowly:** sell calls **at or above KEY RES $192.51** — if assigned you sold at a profit. 30–45 DTE, avoid earnings (Oct 14). |

**Recommended single action for holders:** Sell 1x **$195 call** (just above the 52W high + a couple dollars of cushion) at the closest **30–45 DTE monthly** (skip the October monthly that crosses earnings — likely **Dec 18, 2026 ≈ 135 DTE is the cleanest strike window**, but 30–45 DTE typically means September 18 if no earnings conflict; **EARNINGS = 2026-10-14 per yfinance** so Aug 21 (8 DTE), Sep 18 (36 DTE), Oct 16 (2 DTE past earnings — DO NOT USE), Nov 20 (69 DTE past earnings — clean) are the candidates). Best fit: **Sep 18 $195** at ~30 DTE, premium estimate (IV Rank 45.6%, HV20 25.2%, ATM-call vs $195 OTM ~3.6% above spot) **~$2.80–3.20 per contract** ≈ 1.5% yield on a $188 position. **If challenged** (price > $195) **before Sep 18 expiry**, roll up/out to **Dec 18 $205** to preserve the equity position through earnings. **Do not sell anything ITM** — Stage 2 + 25–60% ext has been measured to punish early assignments via the exclusion band.

### If I'm Wrong
**Alternative bullish view:** If the catalyst pipeline is more powerful than the measured exclusion — e.g., LATAM integration closes faster than 12 months, the Fed pivots dovish at the next meeting, and Q3 beats on AUM growth — STT could carve a **structural path to $210 (Citi target) and beyond** without a meaningful pullback. In that scenario, the trade to monitor is **breakout bar above $192.51 with RVOL > 1.5** → convert SKIP into the **Plan B** stalk (ACCELERATION, code 6, 50% size, target $206, stop $188.60). **Time stop:** if Plan B does NOT trigger within 10 sessions of the bar close, the asset is consolidating under the high and Plan A becomes the higher-probability stalk.

**Alternative bearish view:** If the Feb-Mar consolidation at $200 (Q1 swing highs) acts as a multi-month top, STT could **fail the 52W high retest** and roll over into Stage 3 TOPPING within 5–10 sessions. The structural invalidation of the long thesis is a daily close **below MA20 ($181.71)**, which would also trigger **Plan A stalking convert** in reverse — the long zone becomes a *place to short on failed retest*. The measured short setup would require Stage = 4 (not currently 2) AND Short RR Valid = 1 + Action Short Code ∈ {8, 19} — neither met today; would need Stage 4 confirmation before acting.

**Exit the stalking plan if:**
- Earnings (Oct 14) lands before the trigger — full plan invalidates 7 days before binary event.
- MA20 closes below MA50 (Stage 2 downgrade signal).
- $144.68 (MA200) is broken — re-frame entirely.

## CRITICAL EVENTS
| Event | Date | Impact | Plan |
|---|---|---|---|
| **STT Q3 2026 Earnings** | **2026-10-14** (69 days, per yfinance ground truth) | EXTREME | Do not initiate fresh positions in the **14 days before** this print; for CC holders, ensure strikes are settled or rolled before this date. |
| FOMC Rate Decision | Next meeting after July 29, 2026 hold → likely **mid-September 2026** (around 9/16–9/17 per Fed calendar cadence) | HIGH | If September hike (~30% prob per market pricing), STT multiple compresses — **sizing for all stalking plans gets halved**. If hold, Plan B becomes more attractive. |
| U.S. CPI (next release) | Likely **early-to-mid September 2026** | HIGH | Hot CPI reinforces hike thesis → derails Plan B; cold CPI unlocks structural breakout. |
| LATAM Acquisition Close | **No specific date in source** — assume 6–12 months from signing (so **mid-2027 close plausible**) | LOW (timing-unknown) | Re-rate narrative builds on FIRST regulatory approval, not close — monitor for **CADE (Brazil) / CNBV (Mexico) / SFC (Colombia)** approval news. |
| MS / Citi / FNB Target Updates | Continuous — most recent 8/3/2026 ($204.63) | LOW | Watch for further upgrades toward $210+ as a sentiment confirmation, not a trigger. |

## BOTTOM LINE
**STT is a quality asset in a structurally fine trend, sitting at the worst possible place to add to it.** Two strong catalysts — the LATAM acquisition and four analyst target raises to $195–$210 — have already done their work (P/B expanded from 1.44x to 1.89x, stock +68% YTD, sitting just 2.84% below the 52W high). What is *also* already true: STT is **29.28% above its 200-day MA — inside the system's most reliable exclusion band (−0.71% SIG, robust in both universes), and the engine's structural entry grade is a literal zero (WATCH, RR Valid = 0, at-market R:R 0.29).** The fresh TRAP_BULL pattern (age 0 bars) and the 60%-of-average volume advance are corroborating tells. **The right move is to do nothing today and stalk two specific triggers: (A) a pullback to $185.40–$186.75 with extension falling back below 25% — i.e., price ≤ ~$181 — which historically has only filled 32% of the time for −0.00% date-neutral; or (B) a structural breakout above $192.51 with RVOL > 1.5 → ACCELERATION entry at ~50% size, target $206, stop $188.60.** For existing holders: HOLD the position, consider a Sep 18 $195 covered call at 30 DTE for ~1.5% yield, but only because the **CC EXCEPTION** for shares already held applies — not because the system endorses new premium here. **The one thing that has to go right for any entry to fire is mean-reversion of the extension band** — Plan A by cooling, Plan B by escaping above the old ceiling. Neither is on the tape today. **SKIP.**