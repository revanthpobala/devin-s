# Stan Weinstein Stage Analysis & Moving Average Confluence

## Overview
This skill governs the classification of market cycles according to Stan Weinstein's classic 4-Stage framework (plus Stage 5 Recovery) and moving average alignment (20 EMA, 50 SMA, 200 SMA).

---

## 1. The Five Stages

### Stage 1: The Basing Area (Accumulation)
- **Characteristics:** Price moves sideways in a defined Darvas box / consolidation band after a prior decline.
- **Moving Averages:** 200-day SMA flattens out. 20 EMA and 50 SMA oscillate around the 200 SMA. Volume contracts.
- **Action:** Stalking zone. Prepare for breakout. Do not buy until breakout on expanding volume above resistance.

### Stage 2: The Advancing Phase (Markup)
- **Characteristics:** Confirmed breakout above Stage 1 resistance on above-average volume.
- **Moving Averages:** Price > 20 EMA > 50 SMA > 200 SMA. All three moving averages slope upward.
- **Action:** **PRIMARY BUYING ZONE (LONG).** Buy pullbacks to the 20 EMA or breakouts to new swing highs.

### Stage 3: The Top Area (Distribution)
- **Characteristics:** Upward momentum stalls. Churning action with high volume but little net price progress.
- **Moving Averages:** 20 EMA flattens, price crosses below the 20 EMA and tests the 50 SMA. 200 SMA loses upward slope.
- **Action:** **TAKE PROFITS / STAND ASIDE.** Tighten trailing stops. No new aggressive long entries.

### Stage 4: The Declining Phase (Markdown)
- **Characteristics:** Breakdown below Stage 3 support floor on expanding volume.
- **Moving Averages:** Price < 20 EMA < 50 SMA < 200 SMA. All three moving averages slope downward.
- **Action:** **PRIMARY SHORTING ZONE (PUTS).** Short breakdowns or counter-trend bear flag retests into the falling 20 EMA. Never buy long dips in Stage 4.

### Stage 5: The Recovery Phase (Rebound)
- **Characteristics:** Sharp mean-reversion counter-trend bounce from extreme oversold conditions (e.g. Connors RSI < 10) back toward the declining 50/200 SMA.
- **Action:** Scalp only with tight trailing stops. Treat as counter-trend relief rally, not confirmed bull trend.

---

## 2. Multi-Timeframe Alignment Rule
- **Rule 1.1:**
  - When evaluating an Intraday 0DTE alert, check the **ALIGN** row: `W _  D _  15m _`.
  - **Triple Green (↑ ↑ ↑):** Full size long calls permitted.
  - **Triple Red (↓ ↓ ↓):** Full size short puts permitted.
  - **Mixed / Conflicted Arrows:** High risk of whipsaw. Cut position sizing by 50% or demand Grade A conviction ($\ge 85$).
