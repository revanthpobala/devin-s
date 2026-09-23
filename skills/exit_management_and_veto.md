
# Intraday Exit Management, Veto Protocol & Dynamic Trade Protection

## Overview
This skill governs the quantitative and tactical rules for handling incoming TradingView `EXIT` alerts, live stop/target interactions, and active trade close decisions.
When an alert fires or when evaluating whether to close an open position, the system executes an institutional multi-factor evaluation to determine whether to **CONFIRM_EXIT**, **VETO_HOLD**, or **SCALE_TRIM**.

---

## 1. The 5-Tier Exit Decision Hierarchy

### Tier 1: Target Reached & Scaling Protocol (SCALE_TRIM / PROFIT_HIT)
- **Target 1 Reached ($R \ge 1.5$):**
  - **Action:** **SCALE 50% IMMEDIATELY**. Lock realized profit on half the position.
  - **Runner Protection:** Ratchet the stop on the remaining 50% to **Break-Even + max($0.05, 0.1×ATR) buffer (BE+)**.
  - **Rule 4.1.1 (Golden Lock):** A trade that has hit Target 1 is NEVER permitted to turn into a negative trade.
- **Target 2 Reached ($R \ge 3.0$ or Technical Exhaustion):**
  - **Action:** **TRAIL RUNNER** — Trail stop to protect ≥65% of peak gains (peak ≥ 2.0×ATR). Remaining position rides with trailing stop until trend invalidation.

---

### Tier 2: Catastrophic Risk Circuit Breaker (CONFIRM_EXIT — Mandatory)
- **Breach Condition:**
  - The card's intrabar catastrophe stop: adverse move **$\ge 1.75R$** from entry (`exit_why = catastrophe stop`), or $30\%$ loss on option premium. Earlier cuts (1.25R, 1.0R) measured worse on Sep 4–22 2026 data.
- **Directive:** **MANDATORY CONFIRM_EXIT VIA MARKET ORDER**.
- **Execution Rules:**
  - Zero debate, zero discretionary overrides, zero "waiting for the candle to close".
  - Capital preservation takes absolute priority over any setup thesis.

---

### Tier 3: Technical Invalidation vs Intra-Bar Wick Tap (VETO_HOLD Gate)
Many retail traders get stopped out by market-maker liquidity sweeps (stop hunts) that momentarily probe below support before instantly reversing.

- **Check 1: 5-Minute Candle Close vs Intra-Bar Wick Probe:**
  - If live spot price momentarily wicks through the alert level but **the 5-minute candle closes ABOVE the invalidation line** (for Longs) or **BELOW** (for Shorts), and the catastrophic stop is intact:
    - **Decision:** **VETO_HOLD**.
    - **Rationale:** Setup structure and volume POC/VWAP support remain structurally sound. Do not surrender shares to wick noise.
  - If a 5-minute bar **CLOSES BEYOND the invalidation line**:
    - **Decision:** **CONFIRM_EXIT**.
    - **Rationale:** Structural breakdown confirmed on closing basis. Thesis is dead. Exit immediately.

- **Check 2: Volume & Order Flow Confirmation:**
  - If the breakdown is accompanied by a volume spike ($>2.0\times$ 20-period volume average) and delta is heavily negative $\rightarrow$ **CONFIRM_EXIT immediately** (institutional liquidation underway).
  - If the probe occurs on declining volume (absorption test) $\rightarrow$ **VETO_HOLD with hard line**.

---

### Tier 4: Midday Chop Stagnation Kill (35-Minute Theta Rule)
- **Condition:**
  - Time is between **11:15 MT and 12:45 MT (1:15 PM – 2:45 PM ET)** (the institutional volume lull).
  - Position has been open for **$\ge 35$ minutes** without achieving Target 1.
  - Directional momentum has stalled (NR7 volatility compression, price oscillating around entry).
- **Decision:** **TIME_STOP_KILL**.
- **Rationale:** For 0DTE/intraday options, sideways movement during the midday lull results in aggressive theta decay and IV crush. Cut the position at scratch ($0 \pm 3\%$) to reclaim capital.

---

### Tier 5: End-Of-Day Settlement Flat Rule (EOD_FLATTEN)
- **Time Deadline:** **13:45 MT (3:45 PM ET)** — 15 minutes before the cash market closing bell.
- **Rule:** **ALL 0DTE options and intraday scalp positions MUST be flattened.**
- **Rationale:** Avoid settlement risk, late-day pin risk, spread widening, and overnight gap exposure. No intraday 0DTE trade is ever held into cash close.

---

## 2. Summary Decision Table

| Condition Observed | Live Spot vs Levels | Action Directive | Tactical Instructions |
|---|---|---|---|
| Price reached Target 1 | Spot $\ge T_1$ (Long) | **SCALE_TRIM** | Take 50% profit off table; move runner stop to BE+ max($0.05, 0.1×ATR). |
| Price reached Target 2 | Spot $\ge T_2$ (Long) | **CONFIRM_EXIT** | Close 100% of remaining position. Setup completed. |
| Drawdown $\ge 1.75R$ | Card catastrophe stop | **CONFIRM_EXIT** | Catastrophic stop triggered. Immediate market exit. |
| Intra-bar wick tap | Wick probed stop, 5m body held | **VETO_HOLD** | Invalidation line held on close; retain position with hard stop. |
| Confirmed 5m candle close below stop | 5m Close $<$ Stop | **CONFIRM_EXIT** | Structural invalidation confirmed. Exit trade now. |
| Stagnant $>35$ min in 11:15–12:45 MT chop | Entry $\pm 0.3\%$ | **TIME_STOP_KILL** | Theta bleed risk. Close at scratch/BE before IV decay. |
| Clock reaches 13:45 MT / 15:45 ET | Any P&L | **EOD_FLATTEN** | Cash close settlement rule. Flatten 100% of 0DTE positions. |

---

## 3. Copilot Interaction Syntax
When Rev Chat evaluates an alert or is asked "should I exit?", Copilot MUST formulate its ruling using this quantitative structure:

1. **RULING HEADER:** State clearly in bold: `CONFIRM EXIT`, `VETO & HOLD`, or `SCALE 50% & TRAIL`.
2. **QUANTITATIVE EVIDENCE:**
   - Entry Price, Live Spot, Distance to Stop, 5m ATR, and Unrealized P&L %.
   - Candle State: Has the 5m bar closed below invalidation, or is this an intra-bar wick?
3. **DECISIVE ACTION BUTTONS:**
   - `[🛑 Confirm Exit & Close Now](action:ask?prompt=Close+position+immediately)`
   - `[🛡️ Veto Alert & Hold with Stop at $[Price]](action:ask?prompt=Keep+holding+with+hard+stop+at+$[Price])`
   - `[⚡ Scale 50% & Trail Runner](action:ask?prompt=Scale+half+position+and+trail+stop)`

