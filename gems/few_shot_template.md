# EXEMPLAR: INSTITUTIONAL PORTFOLIO MANAGER TRADE THESIS

# [TICKER] | $[SPOT_PRICE] | [YYYY-MM-DD]
**Bar close:** $[CLOSE_PRICE] (Data Window) · **Live:** $[LIVE_PRICE] (Alpaca Realtime) · **Change:** [±$X.XX (±X.XX%)]

---

## ⚡ EXECUTIVE PM DECISIONS & ATTRIBUTION
**The Thesis in 2 Sentences:** [Synthesize why the stock moved and what the high-probability path forward is. Explain recent price action in terms of Macro Headwinds (Treasury yields/FOMC) vs Micro Company Catalysts (AWS/AI/earnings backlog).]

| Vehicle | Verdict | Actionable Setup / Structure | Conviction |
| :--- | :--- | :--- | :--- |
| **Equity (Shares)** | **[ENTER (Limit @ Floor) / ENTER (Breakout) / SKIP]** | [Resting Limit @ $[Floor] OR Buy Stop @ $[BreakoutTrigger]] | [X/10] |
| **Options (Derivatives)** | **[ENTER NOW (Credit Spread) / ENTER (LEAPS) / CASH]** | [Specific structure, e.g. Sep 18 $240P/$250P Bull Put @ $1.80 credit] | [X/10] |

**EARNINGS GATE:** PASS ([XX] Days Remaining | Next Earnings Date: ~YYYY-MM-DD)
**PRIMARY VEHICLE:** [If IV Rank ≥ 50% (mandatory >70%), OPTIONS CREDIT SPREAD is Primary; If IV Rank < 30%, EQUITY LIMIT or DEBIT SPREAD is Primary]
**(If User Owns Shares):** [HOLD — no CC (Stage 2 Advancing, IV Rank elevated) / SELL COVERED CALL (Stage 4 / Climax)]

---

## 🛠️ THE 4-PILLAR DATA AUDIT & FORENSICS

### Pillar 1: Quantitative Engine State & Calibration
* **Engine Codes & Stage:** Long Action Code [X], Short Action Code [X], Weinstein Stage [0-5] (0=UNSTAGED, 5=RECOVERY, pine:2231/6009-6024), Stage Age [X] bars.
* **Scores & Evidence:** Buy Score [XX.XX] / Sell Score [XX.XX], Buy Evidence [±X.XX]σ.
* **R:R & Floor Geometry:** 
  * *Zone Baseline:* $[ZoneBot] - $[ZoneTop] (At-Market Zone R:R: [X.XX]:1).
  * *Tactical Structural Floor:* $[Doji/PivotLow] (Confluence with MA20 / 50 SMA / Gap Floor).
  * *Tactical Structural R:R:* **[X.XX]:1** to Target 1 ($[Target1]) against Tactical Stop ($[TacticalStop]).
  * *Calibration Disclosure:* [State whether the indicator code is a descriptive state (e.g. Code 8 WATCH) or a measured trigger (e.g. Code 20 REVERSAL), and identify the non-indicator pillars carrying the thesis.]

### Pillar 2: Auction Market Theory & Tape Structure (VRVP)
* **Volume Profile:** `VP POC` $[POC] ([Relation to spot]), `Value Area` $[VAL]–$[VAH] ([Inside/Above/Below]), `RVOL (20d)` [X.XX]x ([Participation conviction]).
* **Candlestick Formations:** [Identified Pin Bars, Inside Days, Morning Stars, or Gap Retests defining buyer defense].

### Pillar 3: Micro Fundamentals & Company Catalysts
* **Verified Drivers:** [Revenue growth, segment acceleration, backlog, operating margins, analyst revisions, price targets].

### Pillar 4: Volatility & Macro Profile
* **Macro Benchmarks:** 10Y Yields [X.XX]%, DXY [XXX.X], SPY/QQQ Regime, Upcoming CPI ([Days]d) & FOMC ([Days]d).
* **Derivatives Forensics:** `HV20` [XX.X]%, `IV30` [XX.X]%, `IV/HV Spread` [±XX.X]%, `Energy State` [0-3], `IV Rank` [XX.X]%.
* **Options Regime:** [Elevated IV Rank (≥50%, mandatory >70%) or positive IV-HV spread mandates defined-risk credit spreads as Primary; cheap IV (<30%) favors debit/equity; deep ITM LEAPS (Delta 0.70-0.85) preferred over OTM lotto calls].

---

## 🧭 ACTIONABLE MULTI-REGIME EXECUTION PLAN

### Plan A: Equity Execution (Limit or Breakout Trigger)
| Parameter | Level | Exact Execution Trigger |
| :--- | :--- | :--- |
| **Pullback Limit Entry** | $[FloorLevel] | Resting GTC Limit Order at support shelf (e.g. Doji low / MA20) |
| **Breakout Buy Trigger** | $[BreakoutLevel] | Stop-Market Buy ONLY if Daily Close > $[BreakoutLevel] with RVOL > 1.0 |
| **Tactical Stop Loss** | $[StopLevel] | Hard Stop below defense shelf — proven wrong in one sentence |
| **Profit Target 1** | $[Target1] | AVWAP / First liquidity resistance (Trim 50%) |
| **Profit Target 2** | $[Target2] | Darvas Box Top / 52W High (Runner) |
| **Mathematical R:R** | **[X.XX]:1 (T1) / [X.XX]:1 (T2)** | Calculated against $[Entry] and $[Stop] |

### Plan B: Derivatives Execution (Actionable Right Now)
* **Plan B-1: High-IV Credit Spread (Primary when IV Rank > 70%):**
  * **Structure:** [Bull Put Spread] [Short Strike] / [Long Strike] ([DTE] DTE).
  * **Net Credit:** $[X.XX] per contract. Max Profit: $[XXX] | Max Loss: $[XXX] | Break-Even: $[XXX.XX].
  * **Edge:** Sits below structural support shelf ($[SupportShelf]); captures rich IV without needing an immediate stock rally.
* **Plan B-2: Multi-Quarter LEAPS (90–365+ DTE):**
  * **Structure:** [Deep ITM Call Delta 0.75-0.85] [Strike]C ([DTE] DTE).
  * **Assessment:** [Deploy if IV Rank is reasonable; if IV Rank >70%, explicitly defer or structure as debit spread to mitigate extrinsic crush].

### Invalidation & "The ONE Thing"
* **Binary Invalidation Condition:** Daily close below **$[StopPrice]**.
* **Failure Mechanism:** [Explain what structural support breaks if this condition triggers, forcing immediate exit without hesitation].

---

## 🔮 SUPERFORECASTING PREDICTIONS
```json
[
  {
    "horizon_days": 14,
    "event": "[TICKER] enters Pine Script Buy Zone [$EntryBot – $EntryTop]",
    "probability": 0.55,
    "rationale": "Evaluates probability price pulls back to test the Pine Script buyer defense zone."
  },
  {
    "horizon_days": 30,
    "event": "[TICKER] reaches Pine Script Profit Target 1 of $[Target1]",
    "probability": 0.65,
    "rationale": "Evaluates probability price reaches Pine Script Target 1 before hitting stop loss."
  },
  {
    "horizon_days": 45,
    "event": "[TICKER] closes below Pine Script Tactical Stop Loss of $[TacticalStop]",
    "probability": 0.20,
    "rationale": "Evaluates probability of structural invalidation and stop breach."
  }
]
```
